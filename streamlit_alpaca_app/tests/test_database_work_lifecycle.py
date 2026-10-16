"""Execution/transaction contracts; these tests do not assess LLM prose."""
import multiprocessing
import os
import threading
import time
import uuid

import psycopg
import pytest

from services.database import open_database_session, _close_pools
from services.work_scope import WorkScope, WorkCancelled, check_work, register_cancellation


class _DatabaseBinding(str):
    def __repr__(self):
        return "<database binding>"


def test_expired_scope_rejects_new_work():
    scope = WorkScope(time.monotonic() - 1)
    with pytest.raises(WorkCancelled), scope.activate():
        raise AssertionError("Expired scope entered")


def test_scope_unregister_and_nested_cancellation():
    calls = []
    parent = WorkScope(time.monotonic() + 10)
    child = WorkScope(time.monotonic() + 10)
    with parent.activate():
        with child.activate():
            unregister = register_cancellation(lambda: calls.append("cancel"))
            unregister()
    parent.cancel()
    child.cancel()
    assert calls == []
    parent = WorkScope(time.monotonic() + 10)
    with pytest.raises(WorkCancelled), parent.activate():
        unregister = register_cancellation(lambda: calls.append("active"))
        parent.cancel()
        assert calls == ["active"]
        unregister()
        check_work()


@pytest.fixture(scope="module")
def dsn():
    value = os.getenv("SN_DB_TEST_DSN")
    if not value:
        pytest.skip("Set SN_DB_TEST_DSN for actual PostgreSQL boundary checks")
    yield _DatabaseBinding(value)
    _close_pools()


def _observer(dsn):
    return psycopg.connect(dsn, autocommit=True, connect_timeout=10,
                           options="-c statement_timeout=8000 -c default_transaction_read_only=on",
                           application_name="sn_db_repair_observer")


def test_real_deadline_stops_active_sql_and_releases_lease(dsn):
    label = "sn_deadline_" + uuid.uuid4().hex[:12]
    result = {}
    ready = threading.Event()

    def run():
        try:
            with open_database_session(dsn, application_name=label) as conn:
                with conn.pinned_connection():
                    scope = WorkScope(time.monotonic() + 40)
                    result["scope"] = scope
                    ready.set()
                    with scope.activate(), conn.cursor() as cur:
                        cur.execute("SELECT pg_sleep(30)")
        except Exception as exc:
            result["error"] = type(exc).__name__

    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(35), result
    scope = result.pop("scope")
    observed = False
    with _observer(dsn) as observer:
        until = time.monotonic() + 10
        while time.monotonic() < until and thread.is_alive():
            with observer.cursor() as cur:
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name=%s "
                            "AND state='active' AND query LIKE 'SELECT pg_sleep(30)%%'", (label,))
                observed = cur.fetchone()[0] == 1
            if observed:
                break
            time.sleep(.1)
        assert observed, result
        started = time.monotonic()
        scope.cancel()
        thread.join(15)
        try:
            assert not thread.is_alive(), "SQL continued after cancellation"
            assert time.monotonic() - started < 15
            assert result.get("error") in {"QueryCanceled", "WorkCancelled"}
            with observer.cursor() as cur:
                until = time.monotonic() + 3
                while True:
                    cur.execute("SELECT state FROM pg_stat_activity WHERE application_name=%s", (label,))
                    remaining = cur.fetchall()
                    if not remaining or time.monotonic() >= until:
                        break
                    time.sleep(.1)
                assert not remaining, remaining
        finally:
            if thread.is_alive():
                with observer.cursor() as cur:
                    cur.execute("SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE application_name=%s", (label,))
                thread.join(15)


def test_real_savepoint_and_old_cancellation_cannot_affect_later_query(dsn):
    scope = WorkScope(time.monotonic() + 30)
    with open_database_session(dsn, application_name="sn_db_savepoint") as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("CREATE TEMP TABLE sn_db_probe(value int) ON COMMIT DROP")
                cur.execute("INSERT INTO sn_db_probe VALUES (1)")
            with pytest.raises(ValueError), conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO sn_db_probe VALUES (2)")
                raise ValueError("Rollback only the savepoint")
            with scope.activate(), conn.cursor() as cur:
                cur.execute("SELECT sum(value) FROM sn_db_probe")
                assert cur.fetchone()[0] == 1
            timer = threading.Timer(.05, scope.cancel)
            timer.start()
            with conn.cursor() as cur:
                cur.execute("SELECT pg_sleep(.15), 17")
                assert cur.fetchone()[1] == 17
            timer.join()
        assert conn._connection is None


def _background_worker(dsn, active, peak, ready, seconds, release=None):
    with open_database_session(dsn, application_name="sn_bg_admission_probe", workload="background") as conn:
        with conn.cursor() as cur:
            with active.get_lock():
                active.value += 1
                peak.value = max(peak.value, active.value)
            ready.set()
            try:
                if release is not None:
                    cur.execute("SELECT 1")
                    assert release.wait(25)
                else:
                    cur.execute("SELECT pg_sleep(%s)", (seconds,))
            finally:
                with active.get_lock():
                    active.value -= 1


def test_real_server_deadline_and_late_write_rollback(dsn):
    with open_database_session(dsn, application_name="sn_deadline_transaction") as conn:
        conn.autocommit = True
        with conn.pinned_connection():
            with conn.cursor() as cur:
                cur.execute("CREATE TEMP TABLE sn_expired_write(value int)")
            with pytest.raises(WorkCancelled):
                with WorkScope(time.monotonic() + 1).activate(), conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO sn_expired_write VALUES (1)")
                    time.sleep(1.1)
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM sn_expired_write")
                assert cur.fetchone()[0] == 0
            started = time.monotonic()
            with pytest.raises((psycopg.errors.QueryCanceled, WorkCancelled)):
                with WorkScope(time.monotonic() + 1).activate(), conn.cursor() as cur:
                    cur.execute("SELECT pg_sleep(5)")
            assert time.monotonic() - started < 3
        assert conn._connection is None


def test_real_admission_is_shared_between_processes_with_interactive_headroom(dsn):
    ctx = multiprocessing.get_context("spawn")
    active, peak = ctx.Value("i", 0), ctx.Value("i", 0)
    ready, second_ready, release = ctx.Event(), ctx.Event(), ctx.Event()
    first = ctx.Process(target=_background_worker, args=(dsn, active, peak, ready, 0, release))
    second = ctx.Process(target=_background_worker, args=(dsn, active, peak, second_ready, .2))
    first.start()
    assert ready.wait(25)
    second.start()
    try:
        with open_database_session(dsn, application_name="sn_interactive_probe", workload="interactive") as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 23")
                assert cur.fetchone()[0] == 23
        assert active.value == 1
        release.set()
        first.join(25)
        second.join(25)
        assert first.exitcode == second.exitcode == 0
        assert second_ready.is_set() and peak.value == 1
    finally:
        release.set()
        for process in [first, second]:
            if process.is_alive():
                process.terminate()
                process.join()
