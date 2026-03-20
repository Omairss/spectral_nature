from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from pipeline.jobs.main import JobContext, _upload_frame
from services.pipeline_store import SOURCE_DATASETS


def test_upload_frame_persists_empty_frames(monkeypatch):
    uploaded: list[tuple[str, int, str]] = []

    def _capture(path: str, payload: bytes, content_type: str) -> None:
        uploaded.append((path, len(payload), content_type))

    monkeypatch.setattr("pipeline.jobs.main._upload_bytes", _capture)

    ctx = JobContext(
        name="equities-intraday-preload",
        run_id="12345678-test-run",
        asof=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
        universe_version="20260320",
    )

    path = _upload_frame("attention_feed", pd.DataFrame(columns=["event_id", "attention_score"]), ctx)

    assert path.endswith("/part-12345678.parquet")
    assert uploaded
    assert uploaded[0][0] == path
    assert uploaded[0][1] > 0
    assert uploaded[0][2] == "application/octet-stream"


def test_pipeline_store_lists_attention_datasets_under_derivatives():
    derivative_datasets = set(SOURCE_DATASETS["derivatives"])

    assert {
        "peer_group_membership",
        "price_expectations",
        "attention_candidates",
        "anomaly_events",
        "attention_rollups",
        "attention_feed",
    }.issubset(derivative_datasets)


def test_pipeline_store_lists_attention_datasets_under_commodities():
    commodity_datasets = set(SOURCE_DATASETS["commodities"])

    assert {
        "commodity_regime_summary",
        "commodity_regime_history",
        "commodity_peer_group_membership",
        "commodity_price_expectations",
        "commodity_attention_candidates",
        "commodity_anomaly_events",
        "commodity_attention_rollups",
        "commodity_attention_feed",
    }.issubset(commodity_datasets)
