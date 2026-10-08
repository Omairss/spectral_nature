from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services import robinhood_main_sync


def test_scheduled_refresh_interval_is_hourly_during_active_market_hours():
    active_market = datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc)

    assert robinhood_main_sync.scheduled_interval(active_market) == timedelta(hours=1)


def test_scheduled_refresh_interval_remains_two_hours_outside_active_market_hours():
    overnight = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)

    assert robinhood_main_sync.scheduled_interval(overnight) == timedelta(hours=2)


def test_active_market_schedule_uses_the_hour_bucket_not_snapshot_age():
    scheduler_started_late = datetime(2026, 10, 7, 14, 0, 39, tzinfo=timezone.utc)

    assert robinhood_main_sync.schedule_bucket_start(scheduler_started_late) == datetime(
        2026, 10, 7, 14, 0, tzinfo=timezone.utc
    )


def test_overnight_schedule_uses_two_hour_buckets():
    scheduler_started_late = datetime(2026, 10, 7, 5, 32, tzinfo=timezone.utc)

    assert robinhood_main_sync.schedule_bucket_start(scheduler_started_late) == datetime(
        2026, 10, 7, 4, 0, tzinfo=timezone.utc
    )


def test_enqueue_due_passes_the_current_bucket_boundary(monkeypatch):
    captured = {}

    def record_enqueue(**kwargs):
        captured.update(kwargs)
        return 1

    monkeypatch.setattr(
        robinhood_main_sync.robinhood_main_store,
        "enqueue_due_refreshes",
        record_enqueue,
    )

    enqueued = robinhood_main_sync.enqueue_due(
        environment="production",
        now=datetime(2026, 10, 7, 14, 0, 39, tzinfo=timezone.utc),
    )

    assert enqueued == 1
    assert captured == {
        "environment": "production",
        "due_before": datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc),
        "reason": "scheduled",
    }
