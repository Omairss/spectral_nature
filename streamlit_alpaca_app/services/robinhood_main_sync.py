from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any
from zoneinfo import ZoneInfo

from . import robinhood_main, robinhood_main_store


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def scheduled_interval(now: datetime | None = None) -> timedelta:
    current = (now or _now_utc()).astimezone(ZoneInfo("America/New_York"))
    extended_session = current.weekday() < 5 and 8 <= current.hour < 20
    return timedelta(hours=1 if extended_session else 2)


def schedule_bucket_start(now: datetime | None = None) -> datetime:
    """Return the current cadence boundary in UTC.

    Snapshot timestamps are written after a refresh finishes. Comparing them to
    ``now - cadence`` therefore skips the next scheduled run by the duration of
    the previous refresh. A cadence bucket represents the scheduler invocation
    instead, so normal execution latency cannot turn an hourly schedule into an
    every-other-hour schedule.
    """
    current = now or _now_utc()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    interval_seconds = int(scheduled_interval(current).total_seconds())
    bucket_epoch = int(current.timestamp()) // interval_seconds * interval_seconds
    return datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)


def enqueue_due(*, environment: str | None = None, now: datetime | None = None) -> int:
    current = now or _now_utc()
    target_environment = environment or robinhood_main.environment_name()
    return robinhood_main_store.enqueue_due_refreshes(
        environment=target_environment,
        due_before=schedule_bucket_start(current),
        reason="scheduled",
    )


def run_next(*, environment: str | None = None) -> dict[str, Any] | None:
    target_environment = environment or robinhood_main.environment_name()
    request = robinhood_main_store.claim_next_refresh(
        environment=target_environment,
        lease_seconds=int(os.getenv("ROBINHOOD_MAIN_SYNC_LEASE_SECONDS") or "1800"),
    )
    if not request:
        return None
    request_id = str(request.get("id") or "")
    try:
        result = robinhood_main.sync_portfolio(
            user_id=str(request.get("user_id") or ""),
            portfolio_id=str(request.get("portfolio_id") or ""),
        )
    except Exception as exc:
        robinhood_main_store.finish_refresh(
            request_id=request_id,
            status="failed",
            error=str(exc),
        )
        raise
    robinhood_main_store.finish_refresh(
        request_id=request_id,
        status="succeeded",
        result_summary=result,
    )
    return {"request_id": request_id, **result}


def run_pending(*, environment: str | None = None, limit: int = 1) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for _ in range(max(1, min(int(limit), 20))):
        result = run_next(environment=environment)
        if result is None:
            break
        results.append(result)
    return results


__all__ = ["enqueue_due", "run_next", "run_pending", "schedule_bucket_start", "scheduled_interval"]
