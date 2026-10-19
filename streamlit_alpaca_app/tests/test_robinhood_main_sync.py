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


def test_sync_cli_uses_background_admission_without_a_pipeline_job_name(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    from services.database import _workload, database_workload

    path = Path(__file__).parents[1] / "scripts/sync_robinhood_main.py"
    spec = importlib.util.spec_from_file_location("broker_sync_cli_probe", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.delenv("PIPELINE_JOB_NAME", raising=False)
    monkeypatch.setattr(sys, "argv", [str(path), "--environment", "development", "--schedule-due"])
    observed = []
    def ready():
        observed.append(_workload())
        return True
    def enqueue(**kwargs):
        observed.append(_workload())
        return 0
    def pending(**kwargs):
        observed.append(_workload())
        return []
    monkeypatch.setattr(cli.robinhood_main_store, "schema_ready", ready)
    monkeypatch.setattr(cli.robinhood_main_sync, "enqueue_due", enqueue)
    monkeypatch.setattr(cli.robinhood_main_sync, "run_pending", pending)
    with database_workload("interactive"):
        assert cli.main() == 0
        assert _workload() == "interactive"
    assert observed == ["background", "background", "background"]
