"""Transaction-owned PostgreSQL leases shared by application and worker stores.

A session retains a physical connection only while a SQL transaction or cursor
needs it. Commit/rollback returns the lease; the bounded null pool closes idle
connections. Research can retain its logical session while waiting on providers.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import logging
import math
import os
import random
import threading
import time
from types import SimpleNamespace
from typing import Any

from psycopg import InterfaceError
from psycopg.pq import TransactionStatus
from psycopg_pool import NullConnectionPool

from services.secrets import postgres_connect_timeout_seconds
from services.work_scope import check_work, remaining_work_seconds, register_cancellation, work_expired


_pools: dict[str, NullConnectionPool] = {}
_cancel_pools: dict[str, NullConnectionPool] = {}
_pool_lock = threading.Lock()
_stats = {"leases": 0, "wait_seconds": 0.0, "acquisition_failures": 0,
          "admission_wait_seconds": 0.0, "admission_waits": 0, "cancellations": 0}
_WORKLOAD: ContextVar[str | None] = ContextVar("database_workload", default=None)
_ADMISSION_KEY = int.from_bytes(hashlib.sha256(b"spectral_database_admission_v1").digest()[:4], "big", signed=True)
log = logging.getLogger(__name__)


@contextmanager
def database_workload(kind: str):
    if kind not in {"background", "interactive"}:
        raise ValueError("Unknown database workload")
    token = _WORKLOAD.set(kind)
    try:
        yield
    finally:
        _WORKLOAD.reset(token)


def _workload() -> str:
    return _WORKLOAD.get() or ("background" if os.getenv("PIPELINE_JOB_NAME") else "interactive")


def _admission_limits() -> tuple[int, int]:
    total = max(2, int(os.getenv("POSTGRES_GLOBAL_ACTIVE_LIMIT", "3")))
    reserved = max(1, math.ceil(total * 0.30))
    background = min(max(1, int(os.getenv("POSTGRES_GLOBAL_BACKGROUND_LIMIT", "1"))), total - reserved)
    return total, background


def _pool(conninfo: str) -> NullConnectionPool:
    # Credentials remain part of the private identity: control and pipeline roles
    # cannot share a connection, even when they address the same server/database.
    identity = hashlib.sha256(conninfo.encode()).hexdigest()
    with _pool_lock:
        if identity not in _pools:
            _pools[identity] = NullConnectionPool(
                conninfo, name="spectral_" + identity[:10], open=True,
                max_size=max(1, int(os.getenv("POSTGRES_MAX_CONCURRENT_CONNECTIONS", "2"))),
                max_waiting=max(1, int(os.getenv("POSTGRES_MAX_WAITING_CONNECTIONS", "32"))),
                timeout=max(1.0, float(os.getenv("POSTGRES_ACQUIRE_TIMEOUT_SECONDS", "30"))),
                kwargs={"connect_timeout": postgres_connect_timeout_seconds(), "autocommit": True,
                        "options": "-c lock_timeout=5000 -c statement_timeout=120000 -c idle_in_transaction_session_timeout=60000"},
            )
        return _pools[identity]


def database_session_stats() -> dict[str, Any]:
    with _pool_lock:
        total, background = _admission_limits()
        return {**_stats, "scope": "current_process", "admission_scope": "physical_database",
                "global_active_limit": total, "global_background_limit": background,
                "pools": [pool.get_stats() for pool in _pools.values()]}


def _close_pools() -> None:
    for pool in [*list(_pools.values()), *list(_cancel_pools.values())]:
        pool.close()


atexit.register(_close_pools)


class DatabaseSession:
    """The SQL session contract used by stores; transactions retain normal semantics.

    No automatic transaction retry: a disconnected write must never be replayed
    without its caller's idempotency/continuation contract.
    """

    def __init__(self, pool: NullConnectionPool, *, application_name: str, statement_timeout_ms: int = 120000,
                 lock_timeout_ms: int = 5000, idle_transaction_timeout_ms: int = 60000,
                 workload: str | None = None):
        self._pool = pool
        self._settings = (application_name, str(statement_timeout_ms), str(lock_timeout_ms), str(idle_transaction_timeout_ms))
        self._connection: Any | None = None
        self._lock = threading.RLock()
        self._autocommit = False
        self._closed = False
        self._cursors = 0
        self._transactions = 0
        self._identity: dict[str, Any] = {}
        self._workload = workload
        self._permit: int | None = None
        self._cancel_lock = threading.RLock()
        self._operation: object | None = None
        self._current_timeout_ms = statement_timeout_ms

    def _return_connection(self, conn: Any) -> None:
        # Deactivation/reuse is synchronized with cancellation. An old callback
        # must never cancel a later borrower's query on this physical session.
        with self._cancel_lock:
            self._operation = None
            permit, self._permit = self._permit, None
            try:
                if not conn.closed and permit is not None:
                    conn.autocommit = True
                    with conn.cursor() as cur:
                        cur.execute("SELECT pg_advisory_unlock(%s, %s)", (_ADMISSION_KEY, permit))
            except BaseException:
                conn.close()
            finally:
                self._pool.putconn(conn)

    def _try_admission(self, conn: Any) -> bool:
        if os.getenv("POSTGRES_ADMISSION_ENABLED", "1") == "0":
            return True
        total, background = _admission_limits()
        kind = self._workload or _workload()
        ranges = [(0, background - 1)] if kind == "background" else [(background, total - 1), (0, background - 1)]
        with conn.cursor() as cur:
            for low, high in ranges:
                cur.execute("SELECT slot FROM generate_series(%s::int,%s::int) AS slot "
                            "WHERE pg_try_advisory_lock(%s::int,slot) LIMIT 1", (low, high, _ADMISSION_KEY))
                row = cur.fetchone()
                if row:
                    self._permit = row[0]
                    return True
        return False

    def _cancel_query(self, conn: Any) -> None:
        # Azure's cancellation transport is not always acknowledged. Use a
        # dedicated bounded control connection so cancellation cannot queue
        # behind the operation it needs to stop. Match backend generation as
        # well as PID: a recycled backend must never receive this cancellation.
        key = hashlib.sha256(self._pool.conninfo.encode()).hexdigest()
        with _pool_lock:
            if key not in _cancel_pools:
                _cancel_pools[key] = NullConnectionPool(
                    self._pool.conninfo, min_size=0, max_size=1, max_waiting=8,
                    timeout=6, open=True, kwargs={"autocommit": True, "connect_timeout": 5,
                    "application_name": "spectral:database-cancel",
                    "options": "-c statement_timeout=2000 -c lock_timeout=1000"})
            pool = _cancel_pools[key]
        try:
            with pool.connection(timeout=6) as control:
                with control.cursor() as cur:
                    cur.execute("""SELECT pg_cancel_backend(pid) FROM pg_stat_activity
                        WHERE pid=%s AND backend_start=%s AND datname=current_database()
                        AND application_name=%s""", (conn.info.backend_pid,
                        self._identity["backend_start"], self._settings[0]))
                    cur.fetchone()
        except Exception as exc:
            log.warning("Server cancellation unavailable: %s (SQLSTATE %s)", type(exc).__name__, getattr(exc, "sqlstate", None))
            conn.cancel_safe(timeout=2)

    def _acquire(self) -> Any:
        check_work()
        if self._closed:
            raise InterfaceError("Database session is closed")
        if self._connection is None:
            start = time.monotonic()
            background = (self._workload or _workload()) == "background"
            queue_setting = "POSTGRES_BACKGROUND_ACQUIRE_TIMEOUT_SECONDS" if background else "POSTGRES_ACQUIRE_TIMEOUT_SECONDS"
            queue_budget = max(1.0, float(os.getenv(queue_setting, "300" if background else "30")))
            deadline = start + remaining_work_seconds(queue_budget)
            queued = False
            try:
                while True:
                    check_work()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Database admission wait exceeded its execution budget")
                    conn = self._pool.getconn(timeout=remaining)
                    try:
                        conn.autocommit = True
                        with conn.cursor() as cur:
                            cur.execute("SELECT set_config('application_name', %s, false), set_config('statement_timeout', %s, false), "
                                        "set_config('lock_timeout', %s, false), set_config('idle_in_transaction_session_timeout', %s, false), "
                                        "(SELECT backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid())", self._settings)
                            backend_start = cur.fetchone()[4]
                        if self._try_admission(conn):
                            self._current_timeout_ms = int(self._settings[1])
                            conn.autocommit = self._autocommit
                            self._identity = {name: getattr(conn.info, name) for name in ("host", "dbname", "user")}
                            self._identity["backend_start"] = backend_start
                            self._connection = conn
                            break
                    except BaseException:
                        self._return_connection(conn)
                        raise
                    self._pool.putconn(conn)
                    queued = True
                    # Queue outside the database; do not hold an idle session.
                    time.sleep(min(random.uniform(0.15, 0.45), max(0, deadline - time.monotonic())))
                with _pool_lock:
                    _stats["leases"] += 1
                    if queued:
                        _stats["admission_waits"] += 1
                        _stats["admission_wait_seconds"] += time.monotonic() - start
            except Exception:
                with _pool_lock:
                    _stats["acquisition_failures"] += 1
                raise
            finally:
                with _pool_lock:
                    _stats["wait_seconds"] += time.monotonic() - start
        return self._connection

    def _release_idle(self) -> None:
        conn = self._connection
        if conn is not None and not self._cursors and not self._transactions and (
            conn.closed or conn.info.transaction_status == TransactionStatus.IDLE
        ):
            self._connection = None
            self._return_connection(conn)

    @property
    def info(self) -> Any:
        with self._lock:
            status = self._connection.info.transaction_status if self._connection is not None else TransactionStatus.IDLE
            return SimpleNamespace(**self._identity, transaction_status=status)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def autocommit(self) -> bool:
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.autocommit = value
            self._autocommit = bool(value)
            self._release_idle()

    @contextmanager
    def cursor(self, *args: Any, **kwargs: Any):
        with self._lock:
            conn = self._acquire()
            self._cursors += 1
            operation = object()
            with self._cancel_lock:
                previous_operation = self._operation
                self._operation = operation

            def cancel() -> None:
                with self._cancel_lock:
                    if self._operation is operation and not conn.closed:
                        with _pool_lock:
                            _stats["cancellations"] += 1
                        self._cancel_query(conn)

            unregister = lambda: None
            try:
                unregister = register_cancellation(cancel)
                with conn.cursor(*args, **kwargs) as cursor:
                    yield _DatabaseCursor(cursor, self)
            finally:
                unregister()
                with self._cancel_lock:
                    if self._operation is operation:
                        self._operation = previous_operation
                self._cursors -= 1
                if work_expired() and not self._cursors and not self._transactions and not conn.closed:
                    conn.rollback()
                self._release_idle()

    @contextmanager
    def transaction(self, *args: Any, **kwargs: Any):
        with self._lock:
            conn = self._acquire()
            self._transactions += 1
            try:
                with conn.transaction(*args, **kwargs) as transaction:
                    yield transaction
                    check_work()
            finally:
                self._transactions -= 1
                self._release_idle()

    @contextmanager
    def pinned_connection(self):
        """Explicit ownership for operations requiring session-level SQL state."""
        with self._lock:
            conn = self._acquire()
            self._transactions += 1
            try:
                yield self
            finally:
                self._transactions -= 1
                self._release_idle()

    def commit(self) -> None:
        with self._lock:
            if work_expired():
                self.rollback()
                check_work()
            if self._connection is not None:
                self._connection.commit()
                self._release_idle()

    def rollback(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.rollback()
                self._release_idle()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                conn, self._connection = self._connection, None
                try:
                    if not conn.closed and conn.info.transaction_status != TransactionStatus.IDLE:
                        conn.rollback()
                finally:
                    self._return_connection(conn)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            self.commit() if exc_type is None else self.rollback()
        finally:
            self.close()


class _DatabaseCursor:
    def __init__(self, cursor: Any, session: DatabaseSession):
        self._cursor = cursor
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._cursor, name, value)

    def __iter__(self):
        return iter(self._cursor)

    def _prepare(self) -> None:
        check_work()
        base = int(self._session._settings[1])
        seconds = remaining_work_seconds(base / 1000 if base else 86400)
        # A cursor may share an existing transaction. Set its current remaining
        # budget without committing or changing the caller's transaction scope.
        milliseconds = max(1, math.floor(seconds * 1000))
        if milliseconds != self._session._current_timeout_ms:
            with self._session._connection.cursor() as control:
                control.execute("SELECT set_config('statement_timeout', %s, false)", (str(milliseconds),))
            self._session._current_timeout_ms = milliseconds
        check_work()

    def execute(self, *args: Any, **kwargs: Any):
        self._prepare()
        self._cursor.execute(*args, **kwargs)
        check_work()
        return self

    def executemany(self, *args: Any, **kwargs: Any):
        self._prepare()
        self._cursor.executemany(*args, **kwargs)
        check_work()
        return self

    def copy(self, *args: Any, **kwargs: Any):
        self._prepare()
        return self._cursor.copy(*args, **kwargs)


def open_database_session(conninfo: str, *, application_name: str = "", connect_timeout: int | None = None,
                          statement_timeout_ms: int = 120000, lock_timeout_ms: int = 5000,
                          idle_transaction_timeout_ms: int = 60000, workload: str | None = None) -> DatabaseSession:
    del connect_timeout  # the shared pool owns its bounded transport timeout
    label = application_name or "spectral:" + (os.getenv("PIPELINE_JOB_NAME") or os.getenv("CONTAINER_APP_NAME") or "app")[:45]
    session = DatabaseSession(_pool(conninfo), application_name=label, statement_timeout_ms=statement_timeout_ms,
                              lock_timeout_ms=lock_timeout_ms, idle_transaction_timeout_ms=idle_transaction_timeout_ms,
                              workload=workload)
    # Preserve existing factory behavior: unavailable stores fail at acquisition,
    # rather than returning a logical session that has never contacted the DB.
    with session._lock:
        session._acquire()
        session._release_idle()
    return session
