from datetime import datetime, timezone

import pandas as pd

from pipeline.jobs import daily_market_datapoints_build as build
from pipeline.jobs.main import JobContext


def test_publication_keeps_the_briefs_observation_time_and_the_current_execution(monkeypatch):
    observed = "2026-01-02T12:00:00+00:00"
    ctx = JobContext(name="daily-market-datapoints-build", run_id="current-execution",
                     asof=datetime(2026, 1, 4, tzinfo=timezone.utc), universe_version="u")
    monkeypatch.setattr(build, "deserialize_attention_home_payload", lambda _: {"generated_at_utc": observed})
    monkeypatch.setattr(build, "build_daily_datapoint_catalog", lambda **_: {})
    monkeypatch.setattr(build, "select_daily_market_datapoints", lambda **_: pd.DataFrame([{"id": "source"}]))
    saved = []
    build.run_daily_market_datapoints_build(ctx, None,
        persist_dataset_fn=lambda name, frame, effective, conn: saved.append(effective),
        job_progress_fn=lambda *a, **kw: None,
        load_materialized_frame_fn=lambda _: pd.DataFrame([{"value": 1}]))
    assert saved[0].asof == datetime.fromisoformat(observed)
    assert saved[0].run_id == ctx.run_id
    assert saved[0].published_datasets is ctx.published_datasets
    assert ctx.asof == datetime(2026, 1, 4, tzinfo=timezone.utc)
