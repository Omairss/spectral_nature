"""Cancellation ownership shared by threaded tools and their database work."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging
import threading
import time
from typing import Callable


log = logging.getLogger(__name__)
_SCOPES: ContextVar[tuple["WorkScope", ...]] = ContextVar("database_work_scopes", default=())


class WorkCancelled(TimeoutError):
    """The owner has expired or cancelled this work; it must not start more IO."""


class WorkScope:
    def __init__(self, deadline: float):
        self.deadline = deadline
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: set[Callable[[], None]] = set()

    @contextmanager
    def activate(self):
        token = _SCOPES.set((*_SCOPES.get(), self))
        try:
            self.check()
            yield self
            self.check()
        finally:
            _SCOPES.reset(token)

    def check(self) -> None:
        if self._cancelled.is_set() or time.monotonic() >= self.deadline:
            self.cancel()
            raise WorkCancelled("Research work has expired.")

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled.is_set():
                return
            self._cancelled.set()
            callbacks = tuple(self._callbacks)
        # Never call into a driver while holding the registry lock. Cleanup may
        # unregister on the SQL thread while cancellation waits for transport.
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:
                log.warning("Database cancellation transport unavailable: %s", type(exc).__name__)

    def register(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._callbacks.add(callback)
            cancelled = self._cancelled.is_set()
        if cancelled:
            callback()
        self.check()

    def unregister(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._callbacks.discard(callback)


def check_work() -> None:
    for scope in _SCOPES.get():
        scope.check()


def remaining_work_seconds(maximum: float) -> float:
    check_work()
    deadlines = [scope.deadline for scope in _SCOPES.get()]
    return max(0.001, min(maximum, min(deadlines) - time.monotonic())) if deadlines else maximum


def work_expired() -> bool:
    return any(scope._cancelled.is_set() or time.monotonic() >= scope.deadline for scope in _SCOPES.get())


@contextmanager
def paid_receipt_retention(seconds: float = 15):
    """Retain metering receipts after their child execution is cancelled.

    Only the provider's finalization boundary uses this scope. It does not
    authorize new inference, acquisition or application publication.
    """
    token = _SCOPES.set(())
    try:
        with WorkScope(time.monotonic() + seconds).activate():
            yield
    finally:
        _SCOPES.reset(token)


def register_cancellation(callback: Callable[[], None]) -> Callable[[], None]:
    scopes = _SCOPES.get()
    registered = []
    try:
        for scope in scopes:
            registered.append(scope)
            scope.register(callback)
    except BaseException:
        for scope in registered:
            scope.unregister(callback)
        raise

    def unregister() -> None:
        for scope in registered:
            scope.unregister(callback)

    return unregister
