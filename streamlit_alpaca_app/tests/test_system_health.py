from __future__ import annotations

from datetime import datetime, timezone

from services.pipeline_store import PipelineDataset
from services.system_health import SystemHealthPolicy, evaluate_system_health


def _home_metadata(ingested_at_utc: str) -> PipelineDataset:
    return PipelineDataset(
        dataset_name="attention_home_1d",
        dataset_version_id="home-v1",
        blob_path="datasets/home.parquet",
        asof_time_utc=ingested_at_utc,
        ingested_at_utc=ingested_at_utc,
        row_count=1,
    )


def test_system_health_identifies_failed_surfaces_provider_access_and_stale_home():
    now = datetime(2026, 9, 18, 18, tzinfo=timezone.utc)
    issues = evaluate_system_health(
        now=now,
        policy=SystemHealthPolicy(home_max_age_hours=12, home_due_hour_utc=15, provider_failure_window_hours=6),
        latest_jobs={
            "attention-home-build": {"status": "Failed", "error_summary": "HTTP 402 insufficient funds"},
            "trading-agent-build": {"status": "Failed", "error_summary": "No successful horizon synthesis"},
        },
        home_metadata=_home_metadata("2026-08-14T18:00:00+00:00"),
        trading_successful_horizons=0,
        provider_failures=[{"job_name": "attention-home-build", "error_summary": "HTTP 402 insufficient funds"}],
    )

    assert {issue.key for issue in issues} == {
        "attention-home-build-failed", "attention-home-stale", "trading-agent-build-failed",
        "provider-access-attention-home-build",
    }


def test_system_health_does_not_flag_home_freshness_before_a_weekday_refresh_is_due():
    issues = evaluate_system_health(
        now=datetime(2026, 9, 19, 12, tzinfo=timezone.utc),  # Saturday
        policy=SystemHealthPolicy(home_max_age_hours=12, home_due_hour_utc=15, provider_failure_window_hours=6),
        latest_jobs={},
        home_metadata=_home_metadata("2026-09-18T12:00:00+00:00"),
        trading_successful_horizons=None,
        provider_failures=[],
    )

    assert issues == []
