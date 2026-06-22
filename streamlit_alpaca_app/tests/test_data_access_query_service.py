from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data_access.contracts import DataProvenance, QueryRequest, ResolvedPayload
from data_access.contracts import frame_to_records
from data_access.layer import DataAccessLayer
from data_access.query_service import QueryService
from presentation.plotly import render_chart_model


def test_frame_to_records_serializes_array_like_cells():
    frame = pd.DataFrame(
        {
            "symbol": ["APLD"],
            "sparkline_3m": [np.array([1.0, 2.0, 3.0])],
            "return_1y_pct": [np.nan],
        }
    )

    records = frame_to_records(frame)

    assert records == [{"symbol": "APLD", "sparkline_3m": [1.0, 2.0, 3.0], "return_1y_pct": None}]


def test_data_access_layer_prefers_materialized_price_history_when_available(monkeypatch):
    import data_access.layer as layer_module

    pipeline = pd.DataFrame(
        {
            "symbol": ["AAPL", "AAPL", "MSFT"],
            "timestamp": pd.to_datetime(["2026-03-10T00:00:00Z", "2026-03-12T00:00:00Z", "2026-03-12T00:00:00Z"], utc=True),
            "close": [100.0, 102.0, 250.0],
        }
    )
    metadata = SimpleNamespace(
        dataset_name="price_history",
        dataset_version_id="price_history__20260312T000000Z__abcd1234",
        blob_path="datasets/price_history/example.parquet",
        asof_time_utc="2026-03-12T00:00:00Z",
        ingested_at_utc="2026-03-12T00:05:00Z",
        row_count=3,
    )

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", lambda dataset_name: (pipeline.copy(), metadata))

    resolved = DataAccessLayer().resolve_price_history("AAPL", days=30)

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.datasets == ("price_history",)
    assert resolved.provenance.details["dataset_version_id"] == metadata.dataset_version_id
    assert resolved.payload["symbol"].unique().tolist() == ["AAPL"]
    assert resolved.payload["close"].tolist() == [100.0, 102.0]


def test_data_access_layer_materialized_only_does_not_fallback_for_price_history(monkeypatch):
    import data_access.layer as layer_module

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", lambda dataset_name: (pd.DataFrame(), None))
    monkeypatch.setattr(layer_module, "load_price_history", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live fetch should not run")))

    resolved = DataAccessLayer(materialized_only=True).resolve_price_history("AAPL", days=30)

    assert resolved.provenance.mode == "materialized"
    assert resolved.payload.empty
    assert resolved.provenance.details["materialized_only"] is True


def test_data_access_layer_prefers_materialized_positions_when_available(monkeypatch):
    positions = pd.DataFrame(
        {
            "symbol": ["AAPL"],
            "qty": [10.0],
            "market_value": [1800.0],
        }
    )

    monkeypatch.setattr(DataAccessLayer, "_try_pipeline_frame", lambda self, dataset_name, force_refresh: (positions.copy(), {"dataset_name": dataset_name}))

    resolved = DataAccessLayer().resolve_positions()

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.datasets == ("positions_snapshot",)
    assert resolved.payload["symbol"].tolist() == ["AAPL"]


def test_data_access_layer_empty_materialized_positions_fall_back_to_live_cache(monkeypatch):
    import data_access.layer as layer_module

    live_positions = pd.DataFrame({"symbol": ["AAPL"], "market_value": [1800.0]})
    cfg = SimpleNamespace(alpaca_api_key="key")

    monkeypatch.setattr(DataAccessLayer, "_try_pipeline_frame", lambda self, dataset_name, force_refresh: (pd.DataFrame(), {"dataset_name": dataset_name}))
    monkeypatch.setattr(layer_module, "cached_frame", lambda *args, **kwargs: live_positions.copy())

    resolved = DataAccessLayer(cfg=cfg).resolve_positions()

    assert resolved.provenance.mode == "on_demand"
    assert resolved.provenance.datasets == ("positions",)
    assert resolved.payload["symbol"].tolist() == ["AAPL"]


def test_data_access_layer_prefers_materialized_portfolio_timeseries_when_available(monkeypatch):
    history = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-15", "2025-11-15", "2025-12-20", "2025-12-31"], utc=True),
            "portfolio": [90.0, 95.0, 100.0, 101.0],
            "SPY": [100.0, 110.0, 111.0, 112.0],
        }
    )

    monkeypatch.setattr(DataAccessLayer, "_try_pipeline_frame", lambda self, dataset_name, force_refresh: (history.copy(), {"dataset_name": dataset_name}))

    resolved = DataAccessLayer().resolve_portfolio_timeseries("1M")

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.datasets == ("portfolio_timeseries_snapshot",)
    assert resolved.payload["timestamp"].dt.strftime("%Y-%m-%d").tolist() == ["2025-12-20", "2025-12-31"]


def test_data_access_layer_empty_materialized_portfolio_timeseries_fall_back_to_live_cache(monkeypatch):
    import data_access.layer as layer_module

    live_history = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-12-20", "2025-12-31"], utc=True),
            "portfolio": [100.0, 101.0],
        }
    )
    cfg = SimpleNamespace(alpaca_api_key="key")

    monkeypatch.setattr(DataAccessLayer, "_try_pipeline_frame", lambda self, dataset_name, force_refresh: (pd.DataFrame(), {"dataset_name": dataset_name}))
    monkeypatch.setattr(layer_module, "cached_frame", lambda *args, **kwargs: live_history.copy())

    resolved = DataAccessLayer(cfg=cfg).resolve_portfolio_timeseries("1M")

    assert resolved.provenance.mode == "on_demand"
    assert resolved.provenance.datasets == ("portfolio_timeseries",)
    assert resolved.payload["portfolio"].tolist() == [100.0, 101.0]


def test_data_access_layer_holding_roc_uses_materialized_momentum_profiles(monkeypatch):
    momentum = pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT"],
            "roc_1d_to_1w": [0.1, 0.2],
            "roc_1w_to_1m": [0.3, 0.4],
            "roc_1m_to_3m": [0.5, 0.6],
            "momentum_1w": [0.01, 0.02],
            "momentum_1m": [0.03, 0.04],
            "momentum_3m": [0.05, 0.06],
        }
    )

    monkeypatch.setattr(DataAccessLayer, "_try_pipeline_frame", lambda self, dataset_name, force_refresh: (momentum.copy(), {"dataset_name": dataset_name}))

    resolved = DataAccessLayer().resolve_holding_roc(["MSFT"])

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.datasets == ("momentum_profiles",)
    assert resolved.payload["symbol"].tolist() == ["MSFT"]


def test_data_access_layer_resolves_materialized_market_opportunity_feed(monkeypatch):
    materialized = pd.DataFrame(
        [
            {
                "business_filter": "All Market",
                "selected_horizon_col": "return_1m_pct",
                "selected_horizon_label": "1 Month",
                "rank": 1,
                "symbol": "AAPL",
                "opportunity_score": 90.0,
                "return_1m_pct": 8.2,
            },
            {
                "business_filter": "All Market",
                "selected_horizon_col": "return_3m_pct",
                "selected_horizon_label": "3 Month",
                "rank": 1,
                "symbol": "MSFT",
                "opportunity_score": 75.0,
                "return_3m_pct": 4.0,
            },
        ]
    )

    monkeypatch.setattr(
        DataAccessLayer,
        "_try_pipeline_frame",
        lambda self, dataset_name, force_refresh: (materialized.copy(), {"dataset_name": dataset_name}),
    )

    resolved = DataAccessLayer().resolve_market_opportunity_feed(
        business_filter="All Market",
        selected_horizon_col="return_1m_pct",
        selected_horizon_label="1 Month",
        symbols=["AAPL", "MSFT"],
    )

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.datasets == ("market_opportunity_feed",)
    assert resolved.payload["symbol"].tolist() == ["AAPL"]


def test_data_access_layer_resolves_latest_materialized_page_summary(monkeypatch):
    materialized = pd.DataFrame(
        [
            {
                "surface": "Broad Economy",
                "ticker": "",
                "context_signature": "old",
                "status": "ok",
                "headline": "Old macro read",
                "confidence": "low",
                "summary_json": json.dumps(
                    {
                        "status": "ok",
                        "surface": "Broad Economy",
                        "headline": "Old macro read",
                        "summary_markdown": "Old macro read",
                        "watch_items": [],
                        "data_gaps": [],
                        "confidence": "low",
                    }
                ),
                "generated_at_utc": "2026-04-26T17:00:00Z",
                "run_id": "old",
            },
            {
                "surface": "Broad Economy",
                "ticker": "",
                "context_signature": "new",
                "status": "ok",
                "headline": "Latest macro read",
                "confidence": "medium",
                "summary_json": json.dumps(
                    {
                        "status": "ok",
                        "surface": "Broad Economy",
                        "headline": "Latest macro read",
                        "summary_markdown": "Latest macro read",
                        "watch_items": [],
                        "data_gaps": [],
                        "confidence": "medium",
                    }
                ),
                "generated_at_utc": "2026-04-27T17:00:00Z",
                "run_id": "new",
            },
        ]
    )

    monkeypatch.setattr(
        DataAccessLayer,
        "_try_pipeline_frame",
        lambda self, dataset_name, force_refresh: (materialized.copy(), {"dataset_name": dataset_name}),
    )
    monkeypatch.setattr(
        DataAccessLayer,
        "_recent_materialized_frames",
        lambda self, dataset_name, limit=8: [(materialized.copy(), {"dataset_name": dataset_name})],
    )

    resolved = DataAccessLayer().resolve_page_agentic_summary(
        surface="Broad Economy",
        context_signature="",
    )

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.datasets == ("page_agentic_summaries",)
    assert resolved.payload["headline"] == "Latest macro read"
    assert resolved.payload["materialized"]["context_match"] == "surface"


def test_data_access_layer_page_summary_searches_recent_versions_for_ok_payload(monkeypatch):
    newer_unavailable = pd.DataFrame(
        [
            {
                "surface": "Broad Economy",
                "ticker": "",
                "context_signature": "new",
                "status": "unavailable",
                "headline": "",
                "confidence": "low",
                "summary_json": json.dumps(
                    {
                        "status": "unavailable",
                        "surface": "Broad Economy",
                        "headline": "",
                        "summary_markdown": "",
                        "watch_items": [],
                        "data_gaps": ["Attention job does not own Broad Economy."],
                        "confidence": "low",
                    }
                ),
                "generated_at_utc": "2026-04-27T18:00:00Z",
                "run_id": "newer",
            }
        ]
    )
    older_ok = pd.DataFrame(
        [
            {
                "surface": "Broad Economy",
                "ticker": "",
                "context_signature": "old",
                "status": "ok",
                "headline": "Macro job read",
                "confidence": "medium",
                "summary_json": json.dumps(
                    {
                        "status": "ok",
                        "surface": "Broad Economy",
                        "headline": "Macro job read",
                        "summary_markdown": "Macro job read",
                        "watch_items": [],
                        "data_gaps": [],
                        "confidence": "medium",
                    }
                ),
                "generated_at_utc": "2026-04-27T17:00:00Z",
                "run_id": "older",
            }
        ]
    )

    monkeypatch.setattr(
        DataAccessLayer,
        "_recent_materialized_frames",
        lambda self, dataset_name, limit=8: [
            (newer_unavailable.copy(), {"dataset_name": dataset_name, "dataset_version_id": "newer"}),
            (older_ok.copy(), {"dataset_name": dataset_name, "dataset_version_id": "older"}),
        ],
    )

    resolved = DataAccessLayer().resolve_page_agentic_summary(
        surface="Broad Economy",
        context_signature="different",
    )

    assert resolved.provenance.mode == "materialized"
    assert resolved.payload["status"] == "ok"
    assert resolved.payload["headline"] == "Macro job read"
    assert resolved.payload["materialized"]["run_id"] == "older"


def test_data_access_layer_materialized_only_does_not_fallback_for_attention_home(monkeypatch):
    import data_access.layer as layer_module

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", lambda dataset_name: (pd.DataFrame(), None))
    monkeypatch.setattr(DataAccessLayer, "_resolve_live_attention_artifacts", lambda self, force_refresh: (_ for _ in ()).throw(AssertionError("live attention build should not run")))

    resolved = DataAccessLayer(materialized_only=True).resolve_attention_home_1d()

    assert resolved.provenance.mode == "materialized"
    assert resolved.payload == {}
    assert resolved.provenance.details["materialized_only"] is True


def test_data_access_layer_resolves_materialized_attention_home_with_homepage_graph(monkeypatch):
    import data_access.layer as layer_module

    materialized_home = pd.DataFrame(
        [
            {
                "run_id": "materialized-run",
                "generated_at_utc": "2026-04-01T18:00:00Z",
                "coverage_summary_json": json.dumps({"candidate_count": 4}),
                "taxonomy_horizon_trends_json": json.dumps([]),
                "top_events_json": json.dumps([]),
                "must_read_movers_json": json.dumps([]),
                "unresolved_large_moves_json": json.dumps([]),
                "event_candidates_1d_json": json.dumps([]),
                "event_impacts_1d_json": json.dumps([]),
                "entity_master_json": json.dumps([]),
                "homepage_graph_json": json.dumps({"figure": {"data": [], "layout": {"height": 320}}, "summary": {"connected_components": 1}}),
            }
        ]
    )

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(
        DataAccessLayer,
        "_pipeline_frame",
        lambda self, dataset_name: (materialized_home.copy(), {"dataset_name": dataset_name}),
    )

    resolved = DataAccessLayer().resolve_attention_home_1d()

    assert resolved.provenance.mode == "materialized"
    assert resolved.payload["run_id"] == "materialized-run"
    assert resolved.payload["homepage_graph"]["figure"]["layout"]["height"] == 320
    assert resolved.payload["homepage_graph"]["summary"]["connected_components"] == 1


def test_data_access_layer_builds_materialized_fred_dashboard_when_available(monkeypatch):
    import data_access.layer as layer_module

    summary = pd.DataFrame(
        {
            "series_id": ["CPIAUCSL"],
            "indicator": ["Headline CPI"],
            "units_short": ["Index 1982-1984=100"],
            "frequency_short": ["M"],
            "source_title": ["Consumer Price Index"],
            "last_updated": ["2026-03-12T00:00:00Z"],
        }
    )
    observations = pd.DataFrame(
        {
            "series_id": ["CPIAUCSL", "CPIAUCSL"],
            "date": ["2026-02-01", "2026-03-01"],
            "value": ["315.0", "316.2"],
            "release_id": [10, 10],
        }
    )
    series_index = pd.DataFrame(
        {
            "series_id": ["CPIAUCSL"],
            "title": ["Consumer Price Index"],
            "frequency": ["Monthly"],
            "frequency_short": ["M"],
            "units": ["Index 1982-1984=100"],
            "units_short": ["Index 1982-1984=100"],
            "release_id": [10],
            "release_name": ["Consumer Price Index Release"],
            "notes": ["Example notes"],
        }
    )
    release_index = pd.DataFrame({"release_id": [10], "release_name": ["Consumer Price Index Release"]})
    metadata = {
        "fred_summary": SimpleNamespace(
            dataset_name="fred_summary",
            dataset_version_id="fred_summary__20260312T000000Z__abcd1234",
            blob_path="datasets/fred_summary/example.parquet",
            asof_time_utc="2026-03-12T00:00:00Z",
            ingested_at_utc="2026-03-12T00:05:00Z",
            row_count=1,
        ),
        "fred_observations": SimpleNamespace(
            dataset_name="fred_observations",
            dataset_version_id="fred_observations__20260312T000000Z__abcd1234",
            blob_path="datasets/fred_observations/example.parquet",
            asof_time_utc="2026-03-12T00:00:00Z",
            ingested_at_utc="2026-03-12T00:05:00Z",
            row_count=2,
        ),
        "fred_series_index": SimpleNamespace(
            dataset_name="fred_series_index",
            dataset_version_id="fred_series_index__20260312T000000Z__abcd1234",
            blob_path="datasets/fred_series_index/example.parquet",
            asof_time_utc="2026-03-12T00:00:00Z",
            ingested_at_utc="2026-03-12T00:05:00Z",
            row_count=1,
        ),
        "fred_release_index": SimpleNamespace(
            dataset_name="fred_release_index",
            dataset_version_id="fred_release_index__20260312T000000Z__abcd1234",
            blob_path="datasets/fred_release_index/example.parquet",
            asof_time_utc="2026-03-12T00:00:00Z",
            ingested_at_utc="2026-03-12T00:05:00Z",
            row_count=1,
        ),
    }

    def _load(dataset_name: str):
        if dataset_name == "fred_summary":
            return summary.copy(), metadata[dataset_name]
        if dataset_name == "fred_observations":
            return observations.copy(), metadata[dataset_name]
        if dataset_name == "fred_series_index":
            return series_index.copy(), metadata[dataset_name]
        if dataset_name == "fred_release_index":
            return release_index.copy(), metadata[dataset_name]
        raise AssertionError(f"unexpected dataset: {dataset_name}")

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", _load)

    resolved = DataAccessLayer(fred_api_key="").resolve_fred_dashboard(years=3)

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.datasets == ("fred_summary", "fred_observations", "fred_series_index", "fred_release_index")
    assert resolved.provenance.details["summary"]["dataset_version_id"] == metadata["fred_summary"].dataset_version_id
    assert resolved.provenance.details["series_index"]["dataset_version_id"] == metadata["fred_series_index"].dataset_version_id
    assert resolved.payload["series_data"]["CPIAUCSL"]["value"].tolist() == [315.0, 316.2]
    assert resolved.payload["metadata"]["CPIAUCSL"]["release_name"] == "Consumer Price Index Release"


def test_data_access_layer_resolves_attention_datasets_when_available(monkeypatch):
    import data_access.layer as layer_module

    feed = pd.DataFrame(
        {
            "feed_rank": [2, 1],
            "entity_id": ["AAPL", "TSLA"],
            "status": ["active", "cooling"],
            "attention_score": [41.0, 72.5],
            "asof_time_utc": ["2026-03-19T04:00:00Z", "2026-03-19T04:00:00Z"],
        }
    )
    commodity_feed = pd.DataFrame(
        {
            "feed_rank": [1],
            "entity_id": ["GLD"],
            "status": ["active"],
            "attention_score": [68.0],
            "asof_time_utc": ["2026-03-19T04:00:00Z"],
        }
    )
    rollups = pd.DataFrame(
        {
            "rollup_type": ["market", "business_lens"],
            "rollup_name": ["Market", "All Market"],
            "net_attention_score": [100.0, 55.0],
            "top_attention_score": [72.5, 72.5],
            "active_event_count": [2, 1],
            "asof_time_utc": ["2026-03-19T04:00:00Z", "2026-03-19T04:00:00Z"],
        }
    )
    commodity_rollups = pd.DataFrame(
        {
            "rollup_type": ["commodity_market", "commodity_focus"],
            "rollup_name": ["Commodities", "Precious Metals"],
            "net_attention_score": [88.0, 61.0],
            "top_attention_score": [68.0, 68.0],
            "active_event_count": [1, 1],
            "asof_time_utc": ["2026-03-19T04:00:00Z", "2026-03-19T04:00:00Z"],
        }
    )
    metadata = {
        "attention_feed": SimpleNamespace(
            dataset_name="attention_feed",
            dataset_version_id="attention_feed__20260319T040000Z__abcd1234",
            blob_path="datasets/attention_feed/example.parquet",
            asof_time_utc="2026-03-19T04:00:00Z",
            ingested_at_utc="2026-03-19T04:05:00Z",
            row_count=2,
        ),
        "attention_rollups": SimpleNamespace(
            dataset_name="attention_rollups",
            dataset_version_id="attention_rollups__20260319T040000Z__abcd1234",
            blob_path="datasets/attention_rollups/example.parquet",
            asof_time_utc="2026-03-19T04:00:00Z",
            ingested_at_utc="2026-03-19T04:05:00Z",
            row_count=2,
        ),
        "commodity_attention_feed": SimpleNamespace(
            dataset_name="commodity_attention_feed",
            dataset_version_id="commodity_attention_feed__20260319T040000Z__abcd1234",
            blob_path="datasets/commodity_attention_feed/example.parquet",
            asof_time_utc="2026-03-19T04:00:00Z",
            ingested_at_utc="2026-03-19T04:05:00Z",
            row_count=1,
        ),
        "commodity_attention_rollups": SimpleNamespace(
            dataset_name="commodity_attention_rollups",
            dataset_version_id="commodity_attention_rollups__20260319T040000Z__abcd1234",
            blob_path="datasets/commodity_attention_rollups/example.parquet",
            asof_time_utc="2026-03-19T04:00:00Z",
            ingested_at_utc="2026-03-19T04:05:00Z",
            row_count=2,
        ),
    }

    def _load(dataset_name: str):
        if dataset_name == "attention_feed":
            return feed.copy(), metadata[dataset_name]
        if dataset_name == "attention_rollups":
            return rollups.copy(), metadata[dataset_name]
        if dataset_name == "commodity_attention_feed":
            return commodity_feed.copy(), metadata[dataset_name]
        if dataset_name == "commodity_attention_rollups":
            return commodity_rollups.copy(), metadata[dataset_name]
        return pd.DataFrame(), None

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", _load)

    resolved_feed = DataAccessLayer().resolve_attention_feed(limit=1, entity_ids=["tsla"], statuses=["cooling"])
    resolved_rollups = DataAccessLayer().resolve_attention_rollups(rollup_type="business_lens", limit=1)
    resolved_commodity_feed = DataAccessLayer().resolve_attention_feed(dataset_name="commodity_attention_feed", limit=1)
    resolved_commodity_rollups = DataAccessLayer().resolve_attention_rollups(dataset_name="commodity_attention_rollups", rollup_type="commodity_focus", limit=1)

    assert resolved_feed.provenance.mode == "materialized"
    assert resolved_feed.provenance.datasets == ("attention_feed",)
    assert resolved_feed.payload["entity_id"].tolist() == ["TSLA"]
    assert resolved_feed.payload["feed_rank"].tolist() == [1]

    assert resolved_rollups.provenance.mode == "materialized"
    assert resolved_rollups.provenance.datasets == ("attention_rollups",)
    assert resolved_rollups.payload["rollup_type"].tolist() == ["business_lens"]
    assert resolved_rollups.payload["rollup_name"].tolist() == ["All Market"]

    assert resolved_commodity_feed.provenance.datasets == ("commodity_attention_feed",)
    assert resolved_commodity_feed.payload["entity_id"].tolist() == ["GLD"]

    assert resolved_commodity_rollups.provenance.datasets == ("commodity_attention_rollups",)
    assert resolved_commodity_rollups.payload["rollup_name"].tolist() == ["Precious Metals"]


def test_resolve_attention_feed_includes_macro_provenance_details(monkeypatch):
    import data_access.layer as layer_module
    feed = pd.DataFrame(
        [
            {
                "feed_rank": 1,
                "event_id": "e1",
                "entity_id": "TLT",
                "horizon": "1d",
                "attention_score": 77.0,
                "severity_score": 80.0,
                "impact_score": 70.0,
                "confidence_score": 68.0,
                "observed_value": 2.0,
                "expected_value": 0.5,
                "residual_value": 1.5,
                "residual_zscore": 2.4,
                "card_type": "price_residual",
                "title": "TLT is outperforming expectation",
                "subtitle": "Price Residual over 1d",
                "entity_type": "symbol",
                "direction": "up",
                "peer_group_name": "Rates",
                "regime_label": "",
                "story_text": "",
                "why_now_text": "",
                "expected_vs_observed_text": "",
                "next_best_action": "",
                "drilldown_section": "Market Opportunity",
                "drilldown_params_json": "{}",
                "linked_news_count": 0,
                "status": "active",
                "schema_version": "v1",
            }
        ]
    )
    macro_release = pd.DataFrame(
        [
            {
                "release_event_id": "macro_release::jobs_report::20260324T133000Z",
                "release_time_utc": "2026-03-24T13:30:00Z",
                "surprise_score": 72.0,
            }
        ]
    )
    macro_checks = pd.DataFrame({"consistency_status": ["holding", "mixed", "broken"]})
    hypotheses = pd.DataFrame({"support_status": ["supported", "continuation", "unresolved"]})
    attention_home = pd.DataFrame(
        [
            {
                "run_id": "run-macro",
                "generated_at_utc": "2026-03-24T18:00:00Z",
                "coverage_summary_json": json.dumps(
                    {
                        "macro_release_detected_count": 3,
                        "macro_release_qualifying_count": 2,
                        "macro_release_promoted_count": 1,
                        "macro_release_suppressed_count": 1,
                    }
                ),
                "taxonomy_horizon_trends_json": json.dumps([]),
                "top_events_json": json.dumps([]),
                "must_read_movers_json": json.dumps([]),
                "unresolved_large_moves_json": json.dumps([]),
                "event_candidates_1d_json": json.dumps([]),
                "event_impacts_1d_json": json.dumps([]),
                "entity_master_json": json.dumps([]),
            }
        ]
    )

    metadata = {
        "attention_feed": SimpleNamespace(
            dataset_name="attention_feed",
            dataset_version_id="attention_feed__20260324T180000Z__abcd1234",
            blob_path="datasets/attention_feed/example.parquet",
            asof_time_utc="2026-03-24T18:00:00Z",
            ingested_at_utc="2026-03-24T18:05:00Z",
            row_count=1,
        ),
        "macro_release_events_1d": SimpleNamespace(
            dataset_name="macro_release_events_1d",
            dataset_version_id="macro_release_events_1d__20260324T180000Z__abcd1234",
            blob_path="datasets/macro_release_events_1d/example.parquet",
            asof_time_utc="2026-03-24T18:00:00Z",
            ingested_at_utc="2026-03-24T18:05:00Z",
            row_count=1,
        ),
        "macro_causal_graph_edges_v1": SimpleNamespace(
            dataset_name="macro_causal_graph_edges_v1",
            dataset_version_id="macro_causal_graph_edges_v1__20260324T180000Z__abcd1234",
            blob_path="datasets/macro_causal_graph_edges_v1/example.parquet",
            asof_time_utc="2026-03-24T18:00:00Z",
            ingested_at_utc="2026-03-24T18:05:00Z",
            row_count=1,
        ),
        "macro_relationship_checks_1d": SimpleNamespace(
            dataset_name="macro_relationship_checks_1d",
            dataset_version_id="macro_relationship_checks_1d__20260324T180000Z__abcd1234",
            blob_path="datasets/macro_relationship_checks_1d/example.parquet",
            asof_time_utc="2026-03-24T18:00:00Z",
            ingested_at_utc="2026-03-24T18:05:00Z",
            row_count=3,
        ),
        "attention_hypotheses_1d": SimpleNamespace(
            dataset_name="attention_hypotheses_1d",
            dataset_version_id="attention_hypotheses_1d__20260324T180000Z__abcd1234",
            blob_path="datasets/attention_hypotheses_1d/example.parquet",
            asof_time_utc="2026-03-24T18:00:00Z",
            ingested_at_utc="2026-03-24T18:05:00Z",
            row_count=3,
        ),
        "attention_home_snapshots_1d": SimpleNamespace(
            dataset_name="attention_home_snapshots_1d",
            dataset_version_id="attention_home_snapshots_1d__20260324T180000Z__abcd1234",
            blob_path="datasets/attention_home_snapshots_1d/example.parquet",
            asof_time_utc="2026-03-24T18:00:00Z",
            ingested_at_utc="2026-03-24T18:05:00Z",
            row_count=1,
        ),
    }

    def _load(dataset_name: str):
        if dataset_name == "attention_feed":
            return feed.copy(), metadata[dataset_name]
        if dataset_name == "macro_release_events_1d":
            return macro_release.copy(), metadata[dataset_name]
        if dataset_name == "macro_causal_graph_edges_v1":
            return pd.DataFrame([{"edge_id": "e1"}]), metadata[dataset_name]
        if dataset_name == "macro_relationship_checks_1d":
            return macro_checks.copy(), metadata[dataset_name]
        if dataset_name == "attention_hypotheses_1d":
            return hypotheses.copy(), metadata[dataset_name]
        if dataset_name == "attention_home_snapshots_1d":
            return attention_home.copy(), metadata[dataset_name]
        if dataset_name == "attention_home_1d":
            return pd.DataFrame(), SimpleNamespace(
                dataset_name="attention_home_1d",
                dataset_version_id="",
                blob_path="",
                asof_time_utc="",
                ingested_at_utc="",
                row_count=0,
            )
        raise AssertionError(f"unexpected dataset: {dataset_name}")

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", _load)

    resolved = DataAccessLayer().resolve_attention_feed(limit=1)

    details = resolved.provenance.details
    assert "macro_dataset_version_ids" in details
    assert details["macro_dataset_version_ids"]["macro_release_events_1d"].startswith("macro_release_events_1d__")
    assert details["macro_relationship_summary"]["holding"] == 1
    assert details["macro_hypothesis_summary"]["supported"] == 1
    assert details["macro_release_visibility_summary"]["promoted"] == 1


def test_resolve_attention_home_1d_fails_closed_for_bad_materialized_payload(monkeypatch):
    bad_what = (
        "Energy and crude-linked products advanced with USO +5.95%, BNO +4.35%, and XOM +3.36%. "
        "Travel-related equities fell at the same time, including ABNB -6.28%, LUV -5.45%, UAL -4.61%, and BKNG -3.68%."
    )
    bad_why = (
        "US Treasury yields showed mixed moves with front-end rates falling and long-end yields rising: "
        "the 2Y dropped 8 bps to 3.88% while the 10Y rose 2 bps to 4.44%."
    )
    materialized_home = pd.DataFrame(
        [
            {
                "run_id": "materialized-run",
                "generated_at_utc": "2026-03-30T02:36:02Z",
                "coverage_summary_json": json.dumps({"candidate_count": 1}),
                "taxonomy_horizon_trends_json": json.dumps([]),
                "top_events_json": json.dumps(
                    [
                        {
                            "bundle_id": "event::cluster-02-067808a0a0",
                            "event_title": "Airlines lower, Energy higher",
                            "what_happened_text": bad_what,
                            "why_happened_text": bad_why,
                            "affected_assets_summary_text": bad_what,
                        }
                    ]
                ),
                "must_read_movers_json": json.dumps([]),
                "unresolved_large_moves_json": json.dumps([]),
                "event_candidates_1d_json": json.dumps([]),
                "event_impacts_1d_json": json.dumps([]),
                "entity_master_json": json.dumps([]),
            }
        ]
    )
    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(
        DataAccessLayer,
        "_pipeline_frame",
        lambda self, dataset_name: (materialized_home.copy(), {"dataset_name": dataset_name}),
    )
    monkeypatch.setattr(
        DataAccessLayer,
        "_resolve_live_attention_artifacts",
        lambda self, force_refresh: (_ for _ in ()).throw(AssertionError("Home render must not live-build attention")),
    )

    resolved = DataAccessLayer().resolve_attention_home_1d()

    assert resolved.provenance.mode == "materialized"
    assert resolved.payload == {}
    assert resolved.provenance.details["warning"] == "attention_home_materialized_stat_dump_text"


def test_resolve_attention_research_bundle_fails_closed_for_stat_dump_materialized_payload(monkeypatch):
    bundle_id = "event::cluster-02-067808a0a0"
    bad_bundle_payload = {
        "bundle_id": bundle_id,
        "bundle_type": "event",
        "event_title": "Airlines lower, Energy higher",
        "what_happened_text": (
            "Energy and crude-linked products advanced with USO +5.95%, BNO +4.35%, and XOM +3.36%. "
            "Travel-related equities fell including ABNB -6.28%, LUV -5.45%, UAL -4.61%, and BKNG -3.68%."
        ),
        "why_happened_text": (
            "US Treasury yields showed mixed moves with front-end rates falling and long-end yields rising: "
            "the 2Y dropped 8 bps and the 10Y rose 2 bps."
        ),
        "affected_assets_summary_text": "USO +5.95%, BNO +4.35%, XOM +3.36%, ABNB -6.28%, LUV -5.45%, UAL -4.61%.",
    }
    materialized_bundle = pd.DataFrame(
        [
            {
                "bundle_id": bundle_id,
                "payload_json": json.dumps(bad_bundle_payload),
            }
        ]
    )
    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(
        DataAccessLayer,
        "_pipeline_frame",
        lambda self, dataset_name: (materialized_bundle.copy(), {"dataset_name": dataset_name}),
    )
    monkeypatch.setattr(
        DataAccessLayer,
        "_resolve_live_attention_artifacts",
        lambda self, force_refresh: (_ for _ in ()).throw(AssertionError("bundle render must not live-build attention")),
    )

    resolved = DataAccessLayer().resolve_attention_research_bundle(bundle_id, force_refresh=True)

    assert resolved.provenance.mode == "materialized"
    assert resolved.payload == {}
    assert resolved.provenance.details["warning"] == "attention_research_bundle_materialized_snapshot_unavailable"


def test_resolve_attention_research_bundle_symbol_prefers_on_demand_when_materialized_has_no_web_signal(monkeypatch):
    monkeypatch.setenv("ATTENTION_SYMBOL_BUNDLE_PRECOMPUTED_ONLY", "false")

    bundle_id = "symbol::BMY"
    materialized_bundle_payload = {
        "bundle_id": bundle_id,
        "bundle_type": "symbol",
        "headline": "BMY",
        "what_changed_text": "BMY moved below trend.",
        "why_now_text": "No concrete catalyst in snapshot.",
        "source_summary": "SEC EDGAR",
        "important_news_count": 0,
        "same_day_evidence_count": 0,
        "evidence": [
            {
                "headline": "DEF 14A filing",
                "summary": "Routine filing recap.",
                "source": "SEC EDGAR",
                "source_kind": "sec",
                "is_important": True,
            }
        ],
        "background_context": [],
    }
    materialized_bundle = pd.DataFrame(
        [
            {
                "bundle_id": bundle_id,
                "payload_json": json.dumps(materialized_bundle_payload),
            }
        ]
    )
    direct_payload = {
        "bundle_id": bundle_id,
        "bundle_type": "symbol",
        "headline": "Bristol Myers drawdown tied to trial headline mix",
        "what_changed_text": "BMY sold off after mixed clinical updates.",
        "why_now_text": "Coverage focused on read-through risk to near-term estimates.",
        "source_summary": "Yahoo Finance and Reuters",
        "important_news_count": 1,
        "same_day_evidence_count": 1,
        "evidence": [
            {
                "headline": "Why Bristol-Myers Squibb (BMY) Stock Is Up Today",
                "summary": "Web coverage discussed trial context and positioning.",
                "source": "Yahoo Finance",
                "source_kind": "search",
                "search_provider": "serpapi",
                "is_important": True,
            }
        ],
        "background_context": [],
        "run_id": "live-run",
    }

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(
        DataAccessLayer,
        "_pipeline_frame",
        lambda self, dataset_name: (materialized_bundle.copy(), {"dataset_name": dataset_name}),
    )
    monkeypatch.setattr(
        DataAccessLayer,
        "_resolve_symbol_agentic_bundle",
        lambda self, symbol, force_refresh: direct_payload,
    )

    resolved = DataAccessLayer().resolve_attention_research_bundle(bundle_id, force_refresh=True)

    assert resolved.provenance.mode == "on_demand"
    assert resolved.payload["headline"].startswith("Bristol Myers drawdown")
    assert "attention_search_results" in resolved.provenance.datasets


def test_resolve_attention_research_bundle_symbol_precomputed_only_skips_on_demand(monkeypatch):
    monkeypatch.delenv("ATTENTION_SYMBOL_BUNDLE_PRECOMPUTED_ONLY", raising=False)

    bundle_id = "symbol::BMY"
    materialized_bundle_payload = {
        "bundle_id": bundle_id,
        "bundle_type": "symbol",
        "headline": "BMY retained snapshot",
        "what_changed_text": "BMY moved below trend.",
        "why_now_text": "No concrete catalyst in snapshot.",
        "source_summary": "SEC EDGAR",
        "important_news_count": 0,
        "same_day_evidence_count": 0,
        "evidence": [
            {
                "headline": "DEF 14A filing",
                "summary": "Routine filing recap.",
                "source": "SEC EDGAR",
                "source_kind": "sec",
                "is_important": True,
            }
        ],
        "background_context": [],
    }
    materialized_bundle = pd.DataFrame(
        [
            {
                "bundle_id": bundle_id,
                "payload_json": json.dumps(materialized_bundle_payload),
            }
        ]
    )

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(
        DataAccessLayer,
        "_pipeline_frame",
        lambda self, dataset_name: (materialized_bundle.copy(), {"dataset_name": dataset_name}),
    )
    monkeypatch.setattr(
        DataAccessLayer,
        "_resolve_symbol_agentic_bundle",
        lambda self, symbol, force_refresh: (_ for _ in ()).throw(AssertionError("on-demand symbol bundle should be disabled in precomputed mode")),
    )

    resolved = DataAccessLayer().resolve_attention_research_bundle(bundle_id, force_refresh=True)

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.details.get("precomputed_only") is True
    assert resolved.payload["headline"] == "BMY retained snapshot"


def test_resolve_attention_research_bundle_symbol_keeps_materialized_when_web_signal_exists(monkeypatch):
    bundle_id = "symbol::BMY"
    materialized_bundle_payload = {
        "bundle_id": bundle_id,
        "bundle_type": "symbol",
        "headline": "BMY rebounds after catalyst coverage",
        "what_changed_text": "BMY recovered as coverage reframed trial risk.",
        "why_now_text": "Catalyst recap pointed to manageable downside.",
        "source_summary": "SerpApi and Tavily",
        "important_news_count": 1,
        "same_day_evidence_count": 1,
        "evidence": [
            {
                "headline": "BMY catalyst recap",
                "summary": "Coverage highlighted risk-reward reset.",
                "source": "Reuters",
                "source_kind": "search",
                "search_provider": "tavily",
                "is_important": True,
            }
        ],
        "background_context": [],
    }
    materialized_bundle = pd.DataFrame(
        [
            {
                "bundle_id": bundle_id,
                "payload_json": json.dumps(materialized_bundle_payload),
            }
        ]
    )

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(
        DataAccessLayer,
        "_pipeline_frame",
        lambda self, dataset_name: (materialized_bundle.copy(), {"dataset_name": dataset_name}),
    )
    monkeypatch.setattr(
        DataAccessLayer,
        "_resolve_symbol_agentic_bundle",
        lambda self, symbol, force_refresh: (_ for _ in ()).throw(AssertionError("on-demand symbol bundle should not run")),
    )

    resolved = DataAccessLayer().resolve_attention_research_bundle(bundle_id)

    assert resolved.provenance.mode == "materialized"
    assert resolved.payload["headline"].startswith("BMY rebounds")
    assert resolved.provenance.datasets == ("attention_bundle_snapshots",)


def test_resolve_attention_ticker_background_prefers_agentic_symbol_bundle(monkeypatch):
    materialized_background = pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "description_text": "Legacy deterministic summary.",
                "news_summary_lines_json": json.dumps(["Legacy line."]),
                "recent_headlines_json": json.dumps([]),
                "source_trace_json": json.dumps({"source": "attention_ticker_background_snapshots"}),
            }
        ]
    )
    bundle_payload = {
        "bundle_id": "symbol::AAPL",
        "bundle_type": "symbol",
        "headline": "Apple extends a services-led support story",
        "what_changed_text": "Recent coverage focused on services growth and margin durability.",
        "why_now_text": "Investors are repricing recurring revenue durability after fresh channel checks.",
        "background_context_text": "Background coverage points to installed-base monetization and buyback support.",
        "source_summary": "Reuters, CNBC, and company commentary",
        "evidence_count": 3,
        "same_day_evidence_count": 2,
        "run_id": "agentic-run",
        "prompt_version": "attention-bottom-up-v1",
        "model_name": "planner",
        "evidence": [
            {
                "headline": "Apple shares rise as services momentum stays firm",
                "summary": "Coverage highlighted resilient high-margin services demand.",
                "source": "Reuters",
                "published_at": "2026-04-03T13:00:00+00:00",
                "url": "https://example.com/reuters-apple-services",
                "source_kind": "search",
                "search_provider": "tavily",
                "evidence_role": "same_day",
                "is_important": True,
            }
        ],
        "background_context": [
            {
                "headline": "Apple buyback authorization remains a valuation support",
                "summary": "Capital-return policy stayed central in analyst framing.",
                "source": "CNBC",
                "published_at": "2026-04-02T20:00:00+00:00",
                "url": "https://example.com/cnbc-apple-buyback",
                "source_kind": "search",
                "search_provider": "serpapi",
                "evidence_role": "background",
                "is_important": True,
            }
        ],
    }

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(
        DataAccessLayer,
        "_pipeline_frame",
        lambda self, dataset_name: (
            materialized_background.copy(),
            {"dataset_name": dataset_name},
        ),
    )
    monkeypatch.setattr(
        DataAccessLayer,
        "resolve_attention_research_bundle",
        lambda self, bundle_id, force_refresh=False: ResolvedPayload(
            payload=bundle_payload,
            provenance=DataProvenance(
                mode="on_demand",
                datasets=("attention_research_bundles",),
                details={"bundle_id": bundle_id},
            ),
        ),
    )

    resolved = DataAccessLayer().resolve_attention_ticker_background("AAPL")

    assert resolved.provenance.mode == "materialized"
    assert resolved.payload["description_text"].startswith("Apple extends a services-led support story")
    assert "agentic research" in resolved.payload["news_summary_lines"][0].lower()
    assert "tavily" in resolved.payload["news_summary_lines"][0].lower()
    assert resolved.payload["recent_headlines"][0]["headline"].startswith("Apple shares rise")
    assert "(via Tavily)" in resolved.payload["recent_headlines"][0]["source"]
    assert resolved.payload["source_trace"]["bundle_id"] == "symbol::AAPL"


def test_resolve_attention_ticker_background_surfaces_low_importance_web_context(monkeypatch):
    materialized_background = pd.DataFrame(
        [
            {
                "symbol": "IRDM",
                "description_text": "Legacy deterministic summary.",
                "news_summary_lines_json": json.dumps(["Legacy line."]),
                "recent_headlines_json": json.dumps([]),
                "source_trace_json": json.dumps({"source": "attention_ticker_background_snapshots"}),
            }
        ]
    )
    bundle_payload = {
        "bundle_id": "symbol::IRDM",
        "bundle_type": "symbol",
        "headline": "Iridium baseline move",
        "what_changed_text": "Generic move recap.",
        "why_now_text": "No concrete catalyst.",
        "background_context_text": "No useful context.",
        "source_summary": "Attention Context",
        "evidence_count": 1,
        "same_day_evidence_count": 0,
        "run_id": "agentic-run",
        "prompt_version": "attention-bottom-up-v1",
        "model_name": "planner",
        "evidence": [
            {
                "headline": "IRDM context snippet",
                "summary": "Model context only.",
                "source": "Attention Context",
                "source_kind": "context",
                "published_at": "",
                "url": "",
                "evidence_role": "background",
            },
            {
                "headline": "Iridium minor filing update",
                "summary": "Low-impact filing recap.",
                "source": "Stock Titan",
                "source_kind": "search",
                "search_provider": "tavily",
                "published_at": "2026-04-03T09:00:00+00:00",
                "url": "https://example.com/irdm-minor-filing",
                "evidence_role": "same_day",
                "is_important": False,
            },
        ],
        "background_context": [],
    }

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(
        DataAccessLayer,
        "_pipeline_frame",
        lambda self, dataset_name: (
            materialized_background.copy(),
            {"dataset_name": dataset_name},
        ),
    )
    monkeypatch.setattr(
        DataAccessLayer,
        "resolve_attention_research_bundle",
        lambda self, bundle_id, force_refresh=False: ResolvedPayload(
            payload=bundle_payload,
            provenance=DataProvenance(
                mode="on_demand",
                datasets=("attention_research_bundles",),
                details={"bundle_id": bundle_id},
            ),
        ),
    )

    resolved = DataAccessLayer().resolve_attention_ticker_background("IRDM")

    assert resolved.payload["description_text"].startswith("Iridium baseline move")
    assert "same-day web item" in resolved.payload["news_summary_lines"][0]
    assert resolved.payload["recent_headlines"][0]["headline"] == "Iridium minor filing update"
    assert resolved.payload["source_trace"]["relevant_news_count"] == 1


def test_resolve_attention_ticker_background_uses_company_baseline_when_materialized_missing(monkeypatch):
    baseline = pd.DataFrame(
        [
            {
                "symbol": "VRT",
                "company_name": "Vertiv Holdings Co",
                "business_lens": "Power and thermal infrastructure",
                "company_background_text": "Vertiv makes critical digital infrastructure power and thermal products.",
                "description_text": "Vertiv makes critical digital infrastructure power and thermal products.",
                "baseline_source": "company_baseline_prefetch",
                "run_id": "baseline-run",
                "asof_time_utc": "2026-04-29T12:00:00Z",
            }
        ]
    )

    def _frame(self, dataset_name):
        if dataset_name == "company_baselines":
            return baseline.copy(), {"dataset_name": dataset_name}
        return pd.DataFrame(), {"dataset_name": dataset_name}

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(DataAccessLayer, "_pipeline_frame", _frame)
    monkeypatch.setattr(
        DataAccessLayer,
        "resolve_attention_research_bundle",
        lambda self, bundle_id, force_refresh=False: ResolvedPayload(
            payload={},
            provenance=DataProvenance(mode="materialized", datasets=(), details={}),
        ),
    )

    resolved = DataAccessLayer().resolve_attention_ticker_background("VRT")

    assert resolved.provenance.mode == "materialized"
    assert "company_baselines" in resolved.provenance.datasets
    assert resolved.payload["company_name"] == "Vertiv Holdings Co"
    assert resolved.payload["company_background_text"].startswith("Vertiv makes critical")
    assert resolved.payload["news_summary_lines"] == []
    assert resolved.payload["source_trace"]["company_baseline_source"] == "company_baseline_prefetch"


def test_resolve_attention_ticker_background_replaces_low_signal_materialized_context_with_baseline(monkeypatch):
    materialized_background = pd.DataFrame(
        [
            {
                "symbol": "VRT",
                "company_name": "Vertiv Holdings Co",
                "description_text": "6 recent article(s) over roughly the last 4 day(s); tone is mixed.",
                "company_background_text": "6 recent article(s) over roughly the last 4 day(s); tone is mixed.",
                "news_summary_lines_json": json.dumps(["6 recent article(s) over roughly the last 4 day(s); tone is mixed."]),
                "recent_headlines_json": json.dumps([]),
                "source_trace_json": json.dumps({"source": "attention_ticker_background_snapshots"}),
            }
        ]
    )
    baseline = pd.DataFrame(
        [
            {
                "symbol": "VRT",
                "company_name": "Vertiv Holdings Co",
                "company_background_text": "Vertiv makes power, cooling, racks, and services for data centers.",
                "description_text": "Vertiv makes power, cooling, racks, and services for data centers.",
                "baseline_source": "company_baseline_prefetch",
            }
        ]
    )

    def _frame(self, dataset_name):
        if dataset_name == "company_baselines":
            return baseline.copy(), {"dataset_name": dataset_name}
        if dataset_name == "attention_ticker_background_snapshots":
            return materialized_background.copy(), {"dataset_name": dataset_name}
        return pd.DataFrame(), {"dataset_name": dataset_name}

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(DataAccessLayer, "_pipeline_frame", _frame)
    monkeypatch.setattr(
        DataAccessLayer,
        "resolve_attention_research_bundle",
        lambda self, bundle_id, force_refresh=False: ResolvedPayload(
            payload={},
            provenance=DataProvenance(mode="materialized", datasets=(), details={}),
        ),
    )

    resolved = DataAccessLayer().resolve_attention_ticker_background("VRT")

    assert resolved.payload["description_text"].startswith("Vertiv makes power")
    assert resolved.payload["company_background_text"].startswith("Vertiv makes power")
    assert set(resolved.provenance.datasets) == {"attention_ticker_background_snapshots", "company_baselines"}


def test_resolve_attention_ticker_background_keeps_materialized_context_when_bundle_has_no_web_headlines(monkeypatch):
    materialized_background = pd.DataFrame(
        [
            {
                "symbol": "BMY",
                "description_text": "Bristol-Myers Squibb baseline context from materialized ticker background.",
                "news_summary_lines_json": json.dumps(
                    [
                        "6 recent article(s) over roughly the last 1 day(s); tone is mixed.",
                        "Bristol Myers Squibb (BMY) is a Top-Ranked Value Stock: Should You Buy?",
                    ]
                ),
                "recent_headlines_json": json.dumps(
                    [
                        {
                            "headline": "Bristol Myers Squibb (BMY) is a Top-Ranked Value Stock: Should You Buy?",
                            "source": "Yahoo Finance",
                            "published_at": "2026-04-03T10:00:00+00:00",
                            "url": "https://example.com/bmy-value-stock",
                        }
                    ]
                ),
                "source_trace_json": json.dumps(
                    {
                        "source": "attention_ticker_background_snapshots",
                        "evidence_count": 6,
                        "same_day_evidence_count": 1,
                        "important_news_count": 2,
                        "news_provider_mix": ["Yahoo Finance"],
                    }
                ),
            }
        ]
    )
    bundle_payload = {
        "bundle_id": "symbol::BMY",
        "bundle_type": "symbol",
        "headline": "Bristol Myers Squibb (BMY) is a Top-Ranked Value Stock: Should You Buy?",
        "what_changed_text": "BMY fell meaningfully today relative to its recent baseline.",
        "why_now_text": "",
        "source_summary": "",
        "evidence_count": 0,
        "same_day_evidence_count": 0,
        "important_news_count": 0,
        "evidence": [],
        "background_context": [],
    }

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(
        DataAccessLayer,
        "_pipeline_frame",
        lambda self, dataset_name: (
            materialized_background.copy(),
            {"dataset_name": dataset_name},
        ),
    )
    monkeypatch.setattr(
        DataAccessLayer,
        "resolve_attention_research_bundle",
        lambda self, bundle_id, force_refresh=False: ResolvedPayload(
            payload=bundle_payload,
            provenance=DataProvenance(
                mode="materialized",
                datasets=("attention_bundle_snapshots",),
                details={"bundle_id": bundle_id},
            ),
        ),
    )

    resolved = DataAccessLayer().resolve_attention_ticker_background("BMY")

    assert resolved.payload["description_text"].startswith("Bristol-Myers Squibb baseline context")
    assert resolved.payload["news_summary_lines"][0].startswith("6 recent article")
    assert resolved.payload["recent_headlines"][0]["headline"].startswith("Bristol Myers Squibb")
    assert not resolved.payload["description_text"].startswith("No relevant business news found")
    assert resolved.payload["source_trace"]["relevant_news_count"] == 1
    assert resolved.payload["source_trace"]["source"] == "attention_ticker_background_snapshots"


def test_data_access_layer_builds_tuned_attention_feed_from_candidate_snapshot(monkeypatch):
    import data_access.layer as layer_module

    candidates = pd.DataFrame(
        {
            "event_id": ["evt_fast", "evt_slow"],
            "asof_time_utc": pd.to_datetime(["2026-03-19T04:00:00Z", "2026-03-19T04:00:00Z"], utc=True),
            "entity_type": ["symbol", "symbol"],
            "entity_id": ["NVDA", "XOM"],
            "parent_entity_type": ["peer_group", "peer_group"],
            "parent_entity_id": ["business_lens:ai", "business_lens:energy"],
            "horizon": ["1yr", "1d"],
            "anomaly_type": ["price_residual", "price_residual"],
            "direction": ["up", "down"],
            "observed_value": [18.0, -1.2],
            "expected_value": [7.0, -0.2],
            "residual_value": [11.0, -1.0],
            "residual_zscore": [2.8, 1.3],
            "severity_score": [70.0, 32.5],
            "impact_score": [75.0, 25.0],
            "relevance_score": [70.0, 70.0],
            "confidence_score": [72.0, 45.0],
            "attention_score": [71.8, 36.0],
            "persistence_score": [95.0, 40.0],
            "novelty_score": [70.0, 32.5],
            "portfolio_exposure_weight": [0.0, 0.0],
            "peer_group_id": ["business_lens:ai", "business_lens:energy"],
            "peer_group_name": ["AI", "Energy"],
            "benchmark": ["SPY", "SPY"],
            "regime_label": ["Trend breakout", ""],
            "why_now_code": ["price_residual", "price_residual"],
            "why_now_text": ["NVDA moved above expectation.", "XOM moved below expectation."],
            "supporting_datasets": ["price_expectations", "price_expectations"],
            "linked_news_count": [1, 0],
            "linked_news_ids": ["n1", ""],
            "drilldown_section": ["Market Opportunity", "Market Opportunity"],
            "drilldown_params_json": ['{"ticker":"NVDA","horizon":"1yr"}', '{"ticker":"XOM","horizon":"1d"}'],
            "status": ["active", "cooling"],
            "schema_version": ["v1", "v1"],
        }
    )
    metadata = {
        "attention_candidates": SimpleNamespace(
            dataset_name="attention_candidates",
            dataset_version_id="attention_candidates__20260319T040000Z__abcd1234",
            blob_path="datasets/attention_candidates/example.parquet",
            asof_time_utc="2026-03-19T04:00:00Z",
            ingested_at_utc="2026-03-19T04:05:00Z",
            row_count=2,
        ),
    }

    def _load(dataset_name: str):
        if dataset_name == "attention_candidates":
            return candidates.copy(), metadata[dataset_name]
        return pd.DataFrame(), None

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", _load)

    resolved_feed = DataAccessLayer().resolve_attention_feed(
        horizons=["1yr"],
        sensitivity="balanced",
        statuses=["active", "cooling"],
        limit=5,
    )
    resolved_rollups = DataAccessLayer().resolve_attention_rollups(
        horizons=["1yr"],
        sensitivity="balanced",
        statuses=["active", "cooling"],
        limit=5,
    )

    assert resolved_feed.provenance.datasets == ("attention_candidates",)
    assert resolved_feed.payload["entity_id"].tolist() == ["NVDA"]
    assert json.loads(resolved_feed.payload.iloc[0]["drilldown_params_json"]) == {
        "horizon": "1yr",
        "market_view": "Markets",
        "ticker": "NVDA",
        "business_filter": "AI",
    }
    assert resolved_rollups.provenance.datasets == ("attention_candidates",)
    assert resolved_rollups.payload["rollup_type"].tolist() == ["market", "business_lens"]


def test_data_access_layer_recent_news_handles_array_symbols(monkeypatch):
    import data_access.layer as layer_module

    articles = pd.DataFrame(
        {
            "headline": ["Oklo signs supply agreement", "Unrelated item"],
            "published_at": pd.to_datetime(["2026-03-20T12:00:00Z", "2026-03-19T09:00:00Z"], utc=True),
            "symbols": [np.array(["OKLO", "SPY"]), np.array(["NVDA"])],
            "source": ["ExampleWire", "ExampleWire"],
        }
    )
    metadata = SimpleNamespace(
        dataset_name="news_articles",
        dataset_version_id="news_articles__20260320T120000Z__abcd1234",
        blob_path="datasets/news_articles/example.parquet",
        asof_time_utc="2026-03-20T12:00:00Z",
        ingested_at_utc="2026-03-20T12:05:00Z",
        row_count=2,
    )

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", lambda dataset_name: (articles.copy(), metadata))

    resolved = DataAccessLayer().resolve_recent_news("OKLO", limit=5)

    assert resolved.provenance.datasets == ("news_articles",)
    assert resolved.payload["articles"]["headline"].tolist() == ["Oklo signs supply agreement"]


def test_data_access_layer_recent_news_falls_back_to_attention_web_search_news(monkeypatch):
    import data_access.layer as layer_module

    search_news = pd.DataFrame(
        {
            "symbol": ["VRDN", "VRDN"],
            "row_type": ["article", "summary"],
            "headline": ["Viridian shares jump on trial coverage", ""],
            "summary": ["Coverage highlighted progress in the thyroid eye disease program.", "Search backfill summary"],
            "source": ["SerpApi", "SerpApi"],
            "payload_source": ["serpapi", "serpapi"],
            "published_at": [pd.Timestamp("2026-05-27T11:00:00Z"), pd.NaT],
            "url": ["https://example.com/vrdn-search", ""],
            "fallback_summary": ["", "Search backfill summary"],
        }
    )
    metadata = {
        "news_articles": None,
        "attention_web_search_news": SimpleNamespace(
            dataset_name="attention_web_search_news",
            dataset_version_id="attention_web_search_news__20260528T120000Z__abcd1234",
            blob_path="datasets/attention_web_search_news/example.parquet",
            asof_time_utc="2026-05-28T12:00:00Z",
            ingested_at_utc="2026-05-28T12:05:00Z",
            row_count=2,
        ),
    }

    def _load(dataset_name: str):
        if dataset_name == "news_articles":
            return pd.DataFrame(), metadata["news_articles"]
        if dataset_name == "attention_web_search_news":
            return search_news.copy(), metadata[dataset_name]
        return pd.DataFrame(), None

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", _load)

    resolved = DataAccessLayer(materialized_only=True).resolve_recent_news("VRDN", limit=5)

    assert resolved.provenance.datasets == ("attention_web_search_news",)
    assert resolved.payload["articles"]["headline"].tolist() == ["Viridian shares jump on trial coverage"]
    assert resolved.payload["fallback_summary"] == "Search backfill summary"
    assert resolved.payload["source"] == "serpapi+SerpApi"


def test_data_access_layer_recent_news_uses_materialized_identity_for_web_refresh(monkeypatch):
    import data_access.layer as layer_module

    company_baselines = pd.DataFrame(
        {
            "symbol": ["APLD"],
            "company_name": ["Applied Digital Corporation"],
        }
    )
    metadata = SimpleNamespace(
        dataset_name="company_baselines",
        dataset_version_id="company_baselines__20260528T120000Z__abcd1234",
        blob_path="datasets/company_baselines/example.parquet",
        asof_time_utc="2026-05-28T12:00:00Z",
        ingested_at_utc="2026-05-28T12:05:00Z",
        row_count=1,
    )
    captured: dict[str, object] = {}

    def _load(dataset_name: str):
        if dataset_name == "company_baselines":
            return company_baselines.copy(), metadata
        return pd.DataFrame(), None

    def _cached_news_payload(_namespace, _scope, factory, **_kwargs):
        return factory()

    def _search_symbol_news_payload(symbol, *, company_name="", **_kwargs):
        captured["symbol"] = symbol
        captured["company_name"] = company_name
        return {
            "articles": pd.DataFrame(
                {
                    "headline": ["Applied Digital secures AI data-center coverage"],
                    "summary": ["Coverage discussed Applied Digital's AI data-center demand."],
                    "description": ["Coverage discussed Applied Digital's AI data-center demand."],
                    "source": ["ExampleWire"],
                    "published_at": [pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1)],
                    "url": ["https://example.com/apld-2026-05-27"],
                }
            ),
            "fallback_summary": None,
            "source": "serpapi",
        }

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", _load)
    monkeypatch.setattr(layer_module, "cached_news_payload", _cached_news_payload)
    monkeypatch.setattr(layer_module, "search_symbol_news_payload", _search_symbol_news_payload)
    monkeypatch.setattr(layer_module, "load_llm_client", lambda: None)

    resolved = DataAccessLayer(cfg=None).resolve_recent_news("APLD", days=7, limit=5, force_refresh=True)

    assert captured == {"symbol": "APLD", "company_name": "Applied Digital Corporation"}
    assert resolved.provenance.datasets == ("web_search_news",)
    assert resolved.payload["articles"]["headline"].tolist() == ["Applied Digital secures AI data-center coverage"]


def test_data_access_layer_recent_news_ignores_summary_only_current_feed_rows(monkeypatch):
    import data_access.layer as layer_module

    search_news = pd.DataFrame(
        {
            "symbol": ["UMC"],
            "row_type": ["summary"],
            "headline": [""],
            "summary": ["Old search backfill summary"],
            "source": ["SerpApi"],
            "payload_source": ["serpapi"],
            "published_at": [pd.NaT],
            "url": [""],
            "fallback_summary": ["Old search backfill summary"],
        }
    )
    metadata = SimpleNamespace(
        dataset_name="attention_web_search_news",
        dataset_version_id="attention_web_search_news__20260528T120000Z__abcd1234",
        blob_path="datasets/attention_web_search_news/example.parquet",
        asof_time_utc="2026-05-28T12:00:00Z",
        ingested_at_utc="2026-05-28T12:05:00Z",
        row_count=1,
    )

    def _load(dataset_name: str):
        if dataset_name == "attention_web_search_news":
            return search_news.copy(), metadata
        return pd.DataFrame(), None

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", _load)

    resolved = DataAccessLayer(materialized_only=True).resolve_recent_news("UMC", limit=5)

    assert resolved.provenance.datasets == ("news_articles",)
    assert resolved.payload["articles"].empty
    assert resolved.payload["fallback_summary"] is None


def test_data_access_layer_resolves_materialized_news_business_resolutions(monkeypatch):
    import data_access.layer as layer_module

    materialized = pd.DataFrame(
        [
            {
                "symbol": "CRWV",
                "company_name": "CoreWeave",
                "coherent_story_markdown": "CoreWeave demand is improving.",
                "asof_time_utc": "2026-05-24T12:00:00Z",
                "source_published_at": "2026-05-21T12:00:00Z",
            },
            {
                "symbol": "NBIS",
                "company_name": "Nebius",
                "coherent_story_markdown": "Nebius demand is improving.",
                "asof_time_utc": "2026-05-24T11:00:00Z",
                "source_published_at": "2026-05-21T11:00:00Z",
            },
        ]
    )
    metadata = SimpleNamespace(
        dataset_name="zopedia_news_business_resolutions",
        dataset_version_id="zopedia_news_business_resolutions__20260524T120000Z__abcd1234",
        blob_path="datasets/zopedia_news_business_resolutions/example.parquet",
        asof_time_utc="2026-05-24T12:00:00Z",
        ingested_at_utc="2026-05-24T12:05:00Z",
        row_count=2,
    )

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", lambda dataset_name: (materialized.copy(), metadata))

    resolved = DataAccessLayer(materialized_only=True).resolve_news_business_resolutions("CRWV", limit=5)

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.datasets == ("zopedia_news_business_resolutions",)
    assert resolved.payload["symbol"].tolist() == ["CRWV"]
    assert resolved.payload.iloc[0]["company_name"] == "CoreWeave"


def test_data_access_layer_resolves_materialized_ticker_business_model_stack(monkeypatch):
    import data_access.layer as layer_module

    materialized = pd.DataFrame(
        [
            {
                "symbol": "CRWV",
                "company_name": "CoreWeave",
                "status": "ready",
                "business_story_markdown": "CoreWeave sells AI infrastructure.",
                "asof_time_utc": "2026-05-24T12:00:00Z",
            },
            {
                "symbol": "NBIS",
                "company_name": "Nebius",
                "status": "ready",
                "business_story_markdown": "Nebius sells AI infrastructure.",
                "asof_time_utc": "2026-05-24T11:00:00Z",
            },
        ]
    )
    metadata = SimpleNamespace(
        dataset_name="zopedia_ticker_business_model_stacks",
        dataset_version_id="zopedia_ticker_business_model_stacks__20260524T120000Z__abcd1234",
        blob_path="datasets/zopedia_ticker_business_model_stacks/example.parquet",
        asof_time_utc="2026-05-24T12:00:00Z",
        ingested_at_utc="2026-05-24T12:05:00Z",
        row_count=2,
    )

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", lambda dataset_name: (materialized.copy(), metadata))

    resolved = DataAccessLayer(materialized_only=True).resolve_ticker_business_model_stack("CRWV")

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.datasets == ("zopedia_ticker_business_model_stacks",)
    assert resolved.payload["symbol"].tolist() == ["CRWV"]
    assert resolved.payload.iloc[0]["business_story_markdown"] == "CoreWeave sells AI infrastructure."


def test_data_access_layer_asset_metadata_falls_back_to_universe_snapshot(monkeypatch):
    import data_access.layer as layer_module

    universe_snapshot = pd.DataFrame(
        {
            "symbol": ["VRDN"],
            "security_name": ["Viridian Therapeutics"],
        }
    )
    metadata = SimpleNamespace(
        dataset_name="universe_snapshot",
        dataset_version_id="universe_snapshot__20260330T120000Z__abcd1234",
        blob_path="datasets/universe_snapshot/example.parquet",
        asof_time_utc="2026-03-30T12:00:00Z",
        ingested_at_utc="2026-03-30T12:05:00Z",
        row_count=1,
    )

    def _load(dataset_name: str):
        if dataset_name == "universe_snapshot":
            return universe_snapshot.copy(), metadata
        return pd.DataFrame(), None

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", _load)

    resolved = DataAccessLayer(materialized_only=True).resolve_asset_metadata("VRDN")

    assert resolved.provenance.datasets == ("universe_snapshot",)
    assert resolved.payload["name"] == "Viridian Therapeutics"
    assert resolved.payload["symbol"] == "VRDN"


def test_data_access_layer_materialized_only_forecast_uses_materialized_signal_history(monkeypatch):
    import data_access.layer as layer_module
    from services.signals import build_signal_frame

    raw = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-02", periods=420, freq="B", tz="UTC"),
            "open": np.linspace(10.0, 28.0, 420),
            "high": np.linspace(10.3, 28.5, 420),
            "low": np.linspace(9.7, 27.7, 420),
            "close": np.linspace(10.0, 28.0, 420) + np.sin(np.linspace(0.0, 20.0, 420)),
            "volume": np.linspace(100000.0, 240000.0, 420),
        }
    )
    signal_history = build_signal_frame(raw)
    signal_history["symbol"] = "VRDN"
    metadata = SimpleNamespace(
        dataset_name="technical_signal_history",
        dataset_version_id="technical_signal_history__20260330T120000Z__abcd1234",
        blob_path="datasets/technical_signal_history/example.parquet",
        asof_time_utc="2026-03-30T12:00:00Z",
        ingested_at_utc="2026-03-30T12:05:00Z",
        row_count=len(signal_history),
    )

    def _load(dataset_name: str):
        if dataset_name == "technical_signal_history":
            return signal_history.copy(), metadata
        return pd.DataFrame(), None

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", _load)

    resolved = DataAccessLayer(materialized_only=True).resolve_forecast_next_week("VRDN", days=365)

    assert resolved.provenance.datasets == ("technical_signal_history", "technical_forecast")
    assert resolved.provenance.mode == "computed"
    assert resolved.payload["analog_count"] > 0
    assert 0.0 <= resolved.payload["up_probability"] <= 1.0


def test_data_access_layer_attention_context_uses_materialized_bundle(monkeypatch):
    import data_access.layer as layer_module

    bundle = pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT"],
            "context_story_text": ["AAPL filed an 8-K on Mar 20.", "MSFT filed a 10-Q on Mar 19."],
            "primary_source_excerpt": ["Current report; items: 2.02", "Quarterly report"],
            "source_line": ["Primary sources: SEC EDGAR filings", "Primary sources: SEC EDGAR filings"],
            "llm_headline": ["EDGAR points to a services-led support story", ""],
            "llm_summary_text": ["Management looks focused on services and capital returns.", ""],
            "llm_supporting_points_json": ['["Fresh 8-K","Services growth"]', "[]"],
            "top_filing_links_json": [
                '[{"label":"8-K • Mar 20","url":"https://example.com/aapl-8k"}]',
                '[{"label":"10-Q • Mar 19","url":"https://example.com/msft-10q"}]',
            ],
        }
    )
    metadata = SimpleNamespace(
        dataset_name="attention_context_bundle",
        dataset_version_id="attention_context_bundle__20260320T120000Z__abcd1234",
        blob_path="datasets/attention_context_bundle/example.parquet",
        asof_time_utc="2026-03-20T12:00:00Z",
        ingested_at_utc="2026-03-20T12:05:00Z",
        row_count=2,
    )

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", lambda dataset_name: (bundle.copy(), metadata))

    resolved = DataAccessLayer().resolve_attention_context("AAPL")

    assert resolved.provenance.datasets == ("attention_context_bundle",)
    assert resolved.payload["symbol"] == "AAPL"
    assert resolved.payload["top_filing_links"][0]["label"] == "8-K • Mar 20"
    assert resolved.payload["llm_headline"] == "EDGAR points to a services-led support story"
    assert resolved.payload["llm_supporting_points"] == ["Fresh 8-K", "Services growth"]


def test_query_service_forwards_attention_tuning_params():
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeAccess:
        def resolve_attention_feed(self, **kwargs):
            calls.append(("feed", kwargs))
            return ResolvedPayload(
                payload=pd.DataFrame({"entity_id": ["NVDA"]}),
                provenance=DataProvenance(mode="materialized", datasets=("attention_candidates",), details={}),
            )

        def resolve_attention_rollups(self, **kwargs):
            calls.append(("rollups", kwargs))
            return ResolvedPayload(
                payload=pd.DataFrame({"rollup_type": ["market"]}),
                provenance=DataProvenance(mode="materialized", datasets=("attention_candidates",), details={}),
            )

    service = QueryService(data_access=FakeAccess())
    service.fetch_dataset(
        "attention_feed",
        {
            "horizons": ["1yr"],
            "sensitivity": "aggressive",
            "min_attention_score": 30,
            "residual_zscore_threshold": 1.5,
            "statuses": ["active"],
        },
    )
    service.fetch_dataset(
        "attention_rollups",
        {
            "horizons": ["3mo"],
            "sensitivity": "balanced",
            "high_priority_threshold": 70,
            "statuses": ["active", "cooling"],
        },
    )

    assert calls[0][0] == "feed"
    assert calls[0][1]["horizons"] == ["1yr"]
    assert calls[0][1]["sensitivity"] == "aggressive"
    assert calls[0][1]["min_attention_score"] == 30.0
    assert calls[1][0] == "rollups"
    assert calls[1][1]["high_priority_threshold"] == 70.0


def test_query_service_builds_price_channel_chart_model():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-03-10", "2026-03-11", "2026-03-12"]),
            "close": [100.0, 101.5, 103.0],
            "channel_support": [95.0, 95.5, 96.0],
            "channel_resistance": [105.0, 105.5, 106.0],
            "ath": [100.0, 101.5, 103.0],
        }
    )

    class FakeAccess:
        def resolve_technical_signal_history(self, ticker: str, *, days: int, force_refresh: bool = False) -> ResolvedPayload:
            assert ticker == "AAPL"
            assert days == 180
            return ResolvedPayload(
                payload=frame,
                provenance=DataProvenance(mode="materialized", datasets=("technical_signal_history",), details={"dataset_version_id": "tech-v1"}),
            )

    service = QueryService(data_access=FakeAccess())
    resolved = service.build_chart("technical_price_channel", {"ticker": "AAPL", "days": 180})

    assert resolved.provenance.mode == "materialized"
    assert resolved.payload.chart_id == "technical_price_channel"
    assert resolved.payload.title == "AAPL Price Channel"
    assert [trace.name for trace in resolved.payload.traces] == ["Support", "Resistance", "Close", "ATH"]
    assert len(resolved.payload.datasets["primary"]) == 3


def test_query_service_execute_returns_serializable_chart_model():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-03-10", "2026-03-11"]),
            "portfolio": [100.0, 102.0],
            "SPY": [100.0, 101.0],
        }
    )

    class FakeAccess:
        def resolve_portfolio_timeseries(self, period: str, *, force_refresh: bool = False) -> ResolvedPayload:
            assert period == "1Y"
            return ResolvedPayload(
                payload=frame,
                provenance=DataProvenance(mode="on_demand", datasets=("portfolio_timeseries",), details={"period": period}),
            )

    service = QueryService(data_access=FakeAccess())
    response = service.execute(QueryRequest(operation="chart", name="portfolio_vs_benchmarks", params={"period": "1Y"}))
    payload = response.to_dict()

    assert payload["result_type"] == "chart_model"
    assert payload["payload"]["chart_id"] == "portfolio_vs_benchmarks"
    assert payload["payload"]["datasets"]["primary"][0]["portfolio"] == 100.0
    assert payload["provenance"]["mode"] == "on_demand"


def test_query_service_portfolio_chart_uses_first_non_zero_anchor():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13"]),
            "portfolio": [0.0, 0.0, 50.0, 55.0],
            "SPY": [100.0, 101.0, 102.0, 103.0],
        }
    )

    class FakeAccess:
        def resolve_portfolio_timeseries(self, period: str, *, force_refresh: bool = False) -> ResolvedPayload:
            assert period == "1Y"
            return ResolvedPayload(
                payload=frame,
                provenance=DataProvenance(mode="on_demand", datasets=("portfolio_timeseries",), details={"period": period}),
            )

    service = QueryService(data_access=FakeAccess())
    resolved = service.build_chart("portfolio_vs_benchmarks", {"period": "1Y"})
    rows = resolved.payload.datasets["primary"]

    assert rows[0]["portfolio"] is None
    assert rows[1]["portfolio"] is None
    assert rows[2]["portfolio"] == 100.0
    assert rows[3]["portfolio"] == 110.00000000000001
    assert rows[0]["SPY"] == 100.0


def test_query_service_capabilities_include_resolution_hints():
    capabilities = QueryService(data_access=object()).list_capabilities()

    assert capabilities["datasets"]["daily_movers"]["resolution"] == "materialized_first"
    assert capabilities["datasets"]["portfolio_timeseries"]["resolution"] == "materialized_first"
    assert capabilities["datasets"]["attention_feed"]["resolution"] == "materialized"
    assert capabilities["datasets"]["attention_context"]["resolution"] == "materialized"
    assert capabilities["datasets"]["attention_evidence_search"]["resolution"] == "materialized"
    assert capabilities["datasets"]["saa_document_search"]["resolution"] == "service_backed"
    assert capabilities["datasets"]["saa_chunk_search"]["resolution"] == "service_backed"
    assert capabilities["datasets"]["saa_document"]["required_params"] == ["canonical_document_id"]
    assert capabilities["datasets"]["commodity_attention_feed"]["resolution"] == "materialized"
    assert capabilities["charts"]["technical_price_channel"]["resolution"] == "computed_from_signal_history"
    assert capabilities["datasets"]["price_history"]["required_params"] == ["ticker"]
    assert capabilities["datasets"]["price_history"]["param_schema"]["additionalProperties"] is False
    assert capabilities["datasets"]["fred_dashboard"]["param_schema"]["properties"]["years"]["type"] == "integer"


def test_daily_movers_falls_back_when_materialized_symbol_filter_is_empty(monkeypatch):
    import data_access.layer as layer_module

    materialized = pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT"],
            "change_pct": [1.2, -0.7],
        }
    )
    on_demand = pd.DataFrame(
        {
            "symbol": ["SPY", "QQQ"],
            "change_pct": [-0.6, -0.5],
        }
    )
    metadata = SimpleNamespace(
        dataset_name="daily_movers",
        dataset_version_id="daily_movers__20260519T000000Z__abcd1234",
        blob_path="datasets/daily_movers/example.parquet",
        asof_time_utc="2026-05-19T00:00:00Z",
        ingested_at_utc="2026-05-19T00:05:00Z",
        row_count=2,
    )
    calls: dict[str, int] = {"cached_frame": 0}

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", lambda dataset_name: (materialized.copy(), metadata))

    def _cached_frame(*args, **kwargs):
        calls["cached_frame"] += 1
        return on_demand.copy()

    monkeypatch.setattr(layer_module, "cached_frame", _cached_frame)

    resolved = DataAccessLayer().resolve_daily_movers(symbols=["SPY", "QQQ"], force_refresh=False)

    assert calls["cached_frame"] == 1
    assert resolved.provenance.mode == "on_demand"
    assert resolved.payload["symbol"].tolist() == ["SPY", "QQQ"]
    assert resolved.provenance.details["fallback_attempted"] is True
    assert resolved.provenance.details["materialized_filter_empty_reason"] == "materialized_symbol_filter_no_matches"


def test_daily_movers_falls_back_when_materialized_symbol_filter_is_partial(monkeypatch):
    import data_access.layer as layer_module

    materialized = pd.DataFrame(
        {
            "symbol": ["SPY", "QQQ"],
            "change_pct": [-0.6, -0.5],
        }
    )
    on_demand = pd.DataFrame(
        {
            "symbol": ["SPY", "QQQ", "XLF", "TLT"],
            "change_pct": [-0.6, -0.5, -1.2, -0.63],
        }
    )
    metadata = SimpleNamespace(
        dataset_name="daily_movers",
        dataset_version_id="daily_movers__20260519T000000Z__abcd1234",
        blob_path="datasets/daily_movers/example.parquet",
        asof_time_utc="2026-05-19T00:00:00Z",
        ingested_at_utc="2026-05-19T00:05:00Z",
        row_count=2,
    )
    calls: dict[str, int] = {"cached_frame": 0}

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", lambda dataset_name: (materialized.copy(), metadata))

    def _cached_frame(*args, **kwargs):
        calls["cached_frame"] += 1
        return on_demand.copy()

    monkeypatch.setattr(layer_module, "cached_frame", _cached_frame)

    resolved = DataAccessLayer().resolve_daily_movers(symbols=["SPY", "QQQ", "XLF", "TLT"], force_refresh=False)

    assert calls["cached_frame"] == 1
    assert resolved.provenance.mode == "on_demand"
    assert resolved.payload["symbol"].tolist() == ["SPY", "QQQ", "XLF", "TLT"]
    assert resolved.provenance.details["filtered_symbols_present"] == ["QQQ", "SPY"]
    assert resolved.provenance.details["filtered_symbols_missing"] == ["TLT", "XLF"]
    assert resolved.provenance.details["materialized_filter_empty_reason"] == "materialized_symbol_filter_partial_matches"


def test_macro_relationship_empty_result_carries_precomputed_artifact_hint(monkeypatch):
    import data_access.layer as layer_module

    metadata = SimpleNamespace(
        dataset_name="macro_relationship_checks_1d",
        dataset_version_id="macro_relationship_checks_1d__20260519T000000Z__abcd1234",
        blob_path="datasets/macro_relationship_checks_1d/example.parquet",
        asof_time_utc="2026-05-19T00:00:00Z",
        ingested_at_utc="2026-05-19T00:05:00Z",
        row_count=0,
    )

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", lambda dataset_name: (pd.DataFrame(), metadata))

    resolved = DataAccessLayer().resolve_materialized_dataset("macro_relationship_checks_1d")

    assert resolved.payload.empty
    assert resolved.provenance.details["empty_reason"] == "no_precomputed_relationship_rows"
    assert resolved.provenance.details["artifact_role"] == "precomputed_relationship_artifact"
    assert "price_history" in resolved.provenance.details["next_tool_hint"]


def test_query_service_messages_include_empty_result_reason():
    class FakeAccess:
        def resolve_daily_movers(self, *, symbols: list[str] | None = None, force_refresh: bool = False) -> ResolvedPayload:
            return ResolvedPayload(
                payload=pd.DataFrame(),
                provenance=DataProvenance(
                    mode="on_demand",
                    datasets=("daily_movers",),
                    details={
                        "empty_reason": "on_demand_no_mover_rows",
                        "user_safe_explanation": "No daily mover rows were returned for the requested symbols.",
                        "next_tool_hint": "Use dataset.price_history for explicit symbols.",
                    },
                ),
            )

    payload = QueryService(data_access=FakeAccess()).execute(
        {"operation": "dataset", "name": "daily_movers", "params": {"symbols": ["SPY"]}}
    ).to_dict()

    assert payload["payload"] == []
    assert payload["messages"] == [
        "No daily mover rows were returned for the requested symbols.",
        "Next tool hint: Use dataset.price_history for explicit symbols.",
    ]


def test_query_request_rejects_non_object_params():
    with pytest.raises(ValueError, match="params must be an object"):
        QueryRequest.from_dict(
            {
                "operation": "dataset",
                "name": "fred_dashboard",
                "params": "{\"years\": 1}",
            }
        )


def test_query_service_rejects_unknown_dataset_params():
    class FakeAccess:
        def resolve_fred_dashboard(self, *, years: int, force_refresh: bool = False) -> ResolvedPayload:
            raise AssertionError("handler should not run for invalid params")

    service = QueryService(data_access=FakeAccess())

    with pytest.raises(ValueError, match="Unsupported params for dataset 'fred_dashboard': filter_indicator_contains"):
        service.fetch_dataset("fred_dashboard", {"filter_indicator_contains": "M2"})


def test_query_service_normalizes_integral_float_params_for_integer_fields():
    class FakeAccess:
        def resolve_fred_dashboard(self, *, years: int, force_refresh: bool = False) -> ResolvedPayload:
            assert years == 1
            assert isinstance(years, int)
            assert force_refresh is False
            return ResolvedPayload(
                payload={"summary": []},
                provenance=DataProvenance(mode="materialized", datasets=("fred_summary",), details={"years": years}),
            )

    service = QueryService(data_access=FakeAccess())
    resolved = service.fetch_dataset("fred_dashboard", {"years": 1.0})

    assert resolved.provenance.details["years"] == 1


def test_query_service_fetches_attention_datasets():
    class FakeAccess:
        def resolve_attention_context(
            self,
            ticker: str,
            *,
            force_refresh: bool = False,
        ) -> ResolvedPayload:
            assert ticker == "AAPL"
            assert force_refresh is True
            return ResolvedPayload(
                payload={"symbol": "AAPL", "context_story_text": "AAPL filed an 8-K."},
                provenance=DataProvenance(mode="materialized", datasets=("attention_context_bundle",), details={"dataset_version_id": "context-v1"}),
            )

        def resolve_attention_feed(
            self,
            *,
            dataset_name: str = "attention_feed",
            limit: int = 10,
            entity_ids: list[str] | None = None,
            horizons: list[str] | None = None,
            statuses: list[str] | None = None,
            sensitivity: str | None = None,
            min_attention_score: float | None = None,
            residual_zscore_threshold: float | None = None,
            force_refresh: bool = False,
        ) -> ResolvedPayload:
            assert horizons is None
            assert sensitivity is None
            assert min_attention_score is None
            assert residual_zscore_threshold is None
            if dataset_name == "commodity_attention_feed":
                assert limit == 2
                assert entity_ids == ["GLD"]
                assert statuses == ["active"]
                assert force_refresh is False
                return ResolvedPayload(
                    payload=pd.DataFrame({"entity_id": ["GLD"], "attention_score": [68.0]}),
                    provenance=DataProvenance(mode="materialized", datasets=("commodity_attention_feed",), details={"dataset_version_id": "commodity-attention-v1"}),
                )

            assert dataset_name == "attention_feed"
            assert limit == 5
            assert entity_ids == ["TSLA"]
            assert statuses == ["cooling"]
            assert force_refresh is False
            return ResolvedPayload(
                payload=pd.DataFrame({"entity_id": ["TSLA"], "attention_score": [72.5]}),
                provenance=DataProvenance(mode="materialized", datasets=("attention_feed",), details={"dataset_version_id": "attention-v1"}),
            )

        def resolve_attention_rollups(
            self,
            *,
            dataset_name: str = "attention_rollups",
            rollup_type: str | None = None,
            horizons: list[str] | None = None,
            statuses: list[str] | None = None,
            sensitivity: str | None = None,
            min_attention_score: float | None = None,
            residual_zscore_threshold: float | None = None,
            high_priority_threshold: float | None = None,
            limit: int = 10,
            force_refresh: bool = False,
        ) -> ResolvedPayload:
            assert horizons is None
            assert statuses is None
            assert sensitivity is None
            assert min_attention_score is None
            assert residual_zscore_threshold is None
            assert high_priority_threshold is None
            if dataset_name == "commodity_attention_rollups":
                assert rollup_type == "commodity_focus"
                assert limit == 2
                assert force_refresh is False
                return ResolvedPayload(
                    payload=pd.DataFrame({"rollup_type": ["commodity_focus"], "rollup_name": ["Precious Metals"]}),
                    provenance=DataProvenance(mode="materialized", datasets=("commodity_attention_rollups",), details={"dataset_version_id": "commodity-rollups-v1"}),
                )

            assert dataset_name == "attention_rollups"
            assert rollup_type == "business_lens"
            assert limit == 3
            assert force_refresh is True
            return ResolvedPayload(
                payload=pd.DataFrame({"rollup_type": ["business_lens"], "rollup_name": ["All Market"]}),
                provenance=DataProvenance(mode="materialized", datasets=("attention_rollups",), details={"dataset_version_id": "rollups-v1"}),
            )

        def resolve_attention_evidence_search(
            self,
            *,
            query: str = "",
            tickers: list[str] | None = None,
            commodities: list[str] | None = None,
            event_tags: list[str] | None = None,
            dates: list[str] | None = None,
            start_date: str | None = None,
            end_date: str | None = None,
            source_kinds: list[str] | None = None,
            providers: list[str] | None = None,
            research_scopes: list[str] | None = None,
            run_id: str | None = None,
            limit: int = 20,
            force_refresh: bool = False,
        ) -> ResolvedPayload:
            assert query == "ceasefire oil"
            assert tickers == ["USO"]
            assert commodities == ["oil"]
            assert event_tags == ["geopolitics"]
            assert dates == ["2026-03-24"]
            assert providers == ["Reuters"]
            assert research_scopes == ["home_summary"]
            assert run_id == "run-1"
            assert limit == 4
            assert start_date is None and end_date is None and source_kinds is None
            assert force_refresh is False
            return ResolvedPayload(
                payload=pd.DataFrame({"chunk_id": ["chunk::1"], "search_score": [12.0]}),
                provenance=DataProvenance(mode="materialized", datasets=("attention_evidence_chunks",), details={"dataset_version_id": "chunks-v1"}),
            )

    service = QueryService(data_access=FakeAccess())
    attention_context = service.fetch_dataset("attention_context", {"ticker": "AAPL", "force_refresh": True})
    attention_feed = service.fetch_dataset("attention_feed", {"limit": 5, "entity_ids": ["TSLA"], "statuses": ["cooling"]})
    attention_rollups = service.fetch_dataset("attention_rollups", {"rollup_type": "business_lens", "limit": 3, "force_refresh": True})
    attention_evidence = service.fetch_dataset(
        "attention_evidence_search",
        {
            "query": "ceasefire oil",
            "tickers": ["USO"],
            "commodities": ["oil"],
            "event_tags": ["geopolitics"],
            "dates": ["2026-03-24"],
            "providers": ["Reuters"],
            "research_scopes": ["home_summary"],
            "run_id": "run-1",
            "limit": 4,
        },
    )
    commodity_attention_feed = service.fetch_dataset("commodity_attention_feed", {"limit": 2, "entity_ids": ["GLD"], "statuses": ["active"]})
    commodity_attention_rollups = service.fetch_dataset("commodity_attention_rollups", {"rollup_type": "commodity_focus", "limit": 2})

    assert attention_context.payload["symbol"] == "AAPL"
    assert attention_feed.payload["entity_id"].tolist() == ["TSLA"]
    assert attention_rollups.payload["rollup_name"].tolist() == ["All Market"]
    assert attention_evidence.payload["chunk_id"].tolist() == ["chunk::1"]
    assert commodity_attention_feed.payload["entity_id"].tolist() == ["GLD"]
    assert commodity_attention_rollups.payload["rollup_name"].tolist() == ["Precious Metals"]


def test_data_access_layer_resolve_attention_evidence_search_filters_materialized_chunks(monkeypatch):
    evidence_chunks = pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "bundle_subject": "USO",
                "chunk_id": "chunk::1",
                "document_id": "doc::1",
                "title": "USO falls as oil pulls back on ceasefire hopes",
                "display_excerpt": "USO and airlines moved as supply-risk eased.",
                "chunk_text": "USO and BNO fell on March 24, 2026 as oil eased and supply-risk faded after ceasefire headlines.",
                "source_kind": "search",
                "source_provider": "Reuters",
                "search_provider": "tavily",
                "research_scope": "home_summary",
                "published_at": pd.Timestamp("2026-03-24T17:30:00Z"),
                "published_date": "2026-03-24",
                "primary_date": "2026-03-24",
                "mentioned_tickers_json": json.dumps(["USO", "BNO"]),
                "mentioned_tickers_key": "|USO|BNO|",
                "mentioned_commodities_json": json.dumps(["oil"]),
                "mentioned_commodities_key": "|oil|",
                "event_tags_json": json.dumps(["geopolitics", "supply_chain"]),
                "event_tags_key": "|geopolitics|supply_chain|",
                "mentioned_dates_json": json.dumps(["2026-03-24"]),
                "mentioned_dates_key": "|2026-03-24|",
                "authority_rank": 1,
                "url": "https://example.com/uso",
            },
            {
                "run_id": "run-1",
                "bundle_subject": "AAPL",
                "chunk_id": "chunk::2",
                "document_id": "doc::2",
                "title": "AAPL gains after product event",
                "display_excerpt": "AAPL rose on launch commentary.",
                "chunk_text": "AAPL rose after a product launch update.",
                "source_kind": "search",
                "source_provider": "Reuters",
                "search_provider": "serpapi",
                "research_scope": "symbol",
                "published_at": pd.Timestamp("2026-03-24T17:30:00Z"),
                "published_date": "2026-03-24",
                "primary_date": "2026-03-24",
                "mentioned_tickers_json": json.dumps(["AAPL"]),
                "mentioned_tickers_key": "|AAPL|",
                "mentioned_commodities_json": json.dumps([]),
                "mentioned_commodities_key": "",
                "event_tags_json": json.dumps(["product_launch"]),
                "event_tags_key": "|product_launch|",
                "mentioned_dates_json": json.dumps(["2026-03-24"]),
                "mentioned_dates_key": "|2026-03-24|",
                "authority_rank": 1,
                "url": "https://example.com/aapl",
            },
        ]
    )

    monkeypatch.setattr(
        DataAccessLayer,
        "_try_pipeline_frame",
        lambda self, dataset_name, force_refresh: (evidence_chunks.copy(), {"dataset_name": dataset_name, "dataset_version_id": "chunks-v1"}),
    )

    resolved = DataAccessLayer().resolve_attention_evidence_search(
        query="ceasefire oil",
        tickers=["USO"],
        commodities=["oil"],
        event_tags=["geopolitics"],
        research_scopes=["home_summary"],
        dates=["2026-03-24"],
        limit=5,
    )

    assert resolved.provenance.datasets == ("attention_evidence_chunks",)
    assert resolved.payload["chunk_id"].tolist() == ["chunk::1"]
    assert resolved.payload.iloc[0]["mentioned_tickers"] == ["USO", "BNO"]
    assert resolved.payload.iloc[0]["event_tags"] == ["geopolitics", "supply_chain"]


def test_query_service_fetch_dataset_supports_saa_document_and_chunk_search():
    class FakeAccess:
        def resolve_saa_document_search(
            self,
            *,
            query: str = "",
            tickers: list[str] | None = None,
            commodities: list[str] | None = None,
            event_tags: list[str] | None = None,
            dates: list[str] | None = None,
            start_date: str | None = None,
            end_date: str | None = None,
            source_kinds: list[str] | None = None,
            providers: list[str] | None = None,
            run_id: str | None = None,
            limit: int = 20,
        ) -> ResolvedPayload:
            assert query == "ceasefire oil"
            assert tickers == ["USO"]
            assert commodities == ["oil"]
            assert event_tags == ["geopolitics"]
            assert dates == ["2026-03-24"]
            assert providers == ["Reuters"]
            assert run_id == "run-1"
            assert limit == 4
            return ResolvedPayload(
                payload=pd.DataFrame({"canonical_document_id": ["saa_doc::uso"], "search_score": [13.0]}),
                provenance=DataProvenance(mode="service_backed", datasets=("saa_documents",), details={}),
            )

        def resolve_saa_chunk_search(
            self,
            *,
            query: str = "",
            tickers: list[str] | None = None,
            commodities: list[str] | None = None,
            event_tags: list[str] | None = None,
            dates: list[str] | None = None,
            start_date: str | None = None,
            end_date: str | None = None,
            source_kinds: list[str] | None = None,
            providers: list[str] | None = None,
            research_scopes: list[str] | None = None,
            run_id: str | None = None,
            canonical_document_id: str | None = None,
            limit: int = 20,
            use_semantic: bool = True,
        ) -> ResolvedPayload:
            assert query == "ceasefire oil"
            assert tickers == ["USO"]
            assert commodities == ["oil"]
            assert event_tags == ["geopolitics"]
            assert dates == ["2026-03-24"]
            assert providers == ["Reuters"]
            assert research_scopes == ["home_summary"]
            assert run_id == "run-1"
            assert canonical_document_id == "saa_doc::uso"
            assert limit == 4
            assert use_semantic is False
            return ResolvedPayload(
                payload=pd.DataFrame({"chunk_record_id": ["saa_chunk::uso"], "search_score": [14.0]}),
                provenance=DataProvenance(mode="service_backed", datasets=("saa_evidence_chunks",), details={}),
            )

        def resolve_saa_document(self, canonical_document_id: str, *, include_raw_text: bool = True) -> ResolvedPayload:
            assert canonical_document_id == "saa_doc::uso"
            assert include_raw_text is False
            return ResolvedPayload(
                payload={"canonical_document_id": canonical_document_id, "title": "USO falls", "raw_text_blob_path": "saa/raw_documents/uso.json"},
                provenance=DataProvenance(mode="service_backed", datasets=("saa_documents",), details={}),
            )

    service = QueryService(data_access=FakeAccess())
    search_resolved = service.fetch_dataset(
        "saa_document_search",
        {
            "query": "ceasefire oil",
            "tickers": ["USO"],
            "commodities": ["oil"],
            "event_tags": ["geopolitics"],
            "dates": ["2026-03-24"],
            "providers": ["Reuters"],
            "run_id": "run-1",
            "limit": 4,
        },
    )
    chunk_search_resolved = service.fetch_dataset(
        "saa_chunk_search",
        {
            "query": "ceasefire oil",
            "tickers": ["USO"],
            "commodities": ["oil"],
            "event_tags": ["geopolitics"],
            "dates": ["2026-03-24"],
            "providers": ["Reuters"],
            "research_scopes": ["home_summary"],
            "run_id": "run-1",
            "canonical_document_id": "saa_doc::uso",
            "limit": 4,
            "use_semantic": False,
        },
    )
    doc_resolved = service.fetch_dataset(
        "saa_document",
        {"canonical_document_id": "saa_doc::uso", "include_raw_text": False},
    )

    assert search_resolved.payload["canonical_document_id"].tolist() == ["saa_doc::uso"]
    assert chunk_search_resolved.payload["chunk_record_id"].tolist() == ["saa_chunk::uso"]
    assert doc_resolved.payload["canonical_document_id"] == "saa_doc::uso"


def test_data_access_layer_resolve_saa_document_and_chunk_search(monkeypatch):
    import data_access.layer as layer_module

    monkeypatch.setattr(
        layer_module,
        "search_retained_documents",
        lambda **kwargs: pd.DataFrame(
            [
                {
                    "canonical_document_id": "saa_doc::uso",
                    "bundle_subject": "USO",
                    "title": "USO falls as oil pulls back on ceasefire hopes",
                    "display_excerpt": "USO and airlines moved as supply-risk eased.",
                    "search_score": 13.0,
                    "mentioned_tickers": ["USO", "BNO"],
                    "event_tags": ["geopolitics", "supply_chain"],
                }
            ]
        ),
    )
    monkeypatch.setattr(
        layer_module,
        "search_retained_evidence_chunks",
        lambda **kwargs: pd.DataFrame(
            [
                {
                    "chunk_record_id": "saa_chunk::uso",
                    "canonical_document_id": "saa_doc::uso",
                    "chunk_text": "USO and BNO fell on March 24, 2026 as oil eased after ceasefire headlines.",
                    "display_excerpt": "USO and airlines moved as supply-risk eased.",
                    "research_scope": "home_summary",
                    "search_score": 14.0,
                    "mentioned_tickers": ["USO", "BNO"],
                    "event_tags": ["geopolitics", "supply_chain"],
                }
            ]
        ),
    )
    monkeypatch.setattr(
        layer_module,
        "load_retained_document_metadata",
        lambda canonical_document_id: {
            "canonical_document_id": canonical_document_id,
            "title": "USO falls as oil pulls back on ceasefire hopes",
            "raw_text_blob_path": "saa/raw_documents/uso.json",
            "search_text": "USO and BNO fell after ceasefire headlines.",
        },
    )
    monkeypatch.setattr(
        layer_module,
        "load_retained_document",
        lambda canonical_document_id: {
            "canonical_document_id": canonical_document_id,
            "raw_text": "USO and BNO fell on March 24, 2026 as oil eased after ceasefire headlines.",
        },
    )

    search_resolved = DataAccessLayer().resolve_saa_document_search(
        query="ceasefire oil",
        tickers=["USO"],
        commodities=["oil"],
        event_tags=["geopolitics"],
        dates=["2026-03-24"],
        providers=["Reuters"],
        run_id="run-1",
        limit=5,
    )
    chunk_search_resolved = DataAccessLayer().resolve_saa_chunk_search(
        query="ceasefire oil",
        tickers=["USO"],
        commodities=["oil"],
        event_tags=["geopolitics"],
        dates=["2026-03-24"],
        providers=["Reuters"],
        research_scopes=["home_summary"],
        run_id="run-1",
        canonical_document_id="saa_doc::uso",
        limit=5,
        use_semantic=False,
    )
    doc_resolved = DataAccessLayer().resolve_saa_document("saa_doc::uso", include_raw_text=True)

    assert search_resolved.provenance.datasets == ("saa_documents",)
    assert search_resolved.payload["canonical_document_id"].tolist() == ["saa_doc::uso"]
    assert chunk_search_resolved.provenance.datasets == ("saa_evidence_chunks",)
    assert chunk_search_resolved.payload["chunk_record_id"].tolist() == ["saa_chunk::uso"]
    assert doc_resolved.provenance.mode == "service_backed"
    assert doc_resolved.payload["raw_text"].startswith("USO and BNO fell")


def test_plotly_renderer_handles_filtered_metric_traces():
    class FakeAccess:
        def resolve_quarterly_fundamentals(self, ticker: str, *, force_refresh: bool = False) -> ResolvedPayload:
            frame = pd.DataFrame(
                {
                    "report_date": pd.to_datetime(["2025-12-31", "2026-03-31", "2025-12-31", "2026-03-31"]),
                    "metric": ["Revenue", "Revenue", "Net Income", "Net Income"],
                    "value": [10.0, 12.0, 2.0, 2.5],
                    "year_quarter": ["2025Q4", "2026Q1", "2025Q4", "2026Q1"],
                }
            )
            return ResolvedPayload(
                payload={"income": frame, "balance": pd.DataFrame(), "cashflow": pd.DataFrame()},
                provenance=DataProvenance(mode="on_demand", datasets=("quarterly_fundamentals",), details={"ticker": ticker}),
            )

    service = QueryService(data_access=FakeAccess())
    chart = service.build_chart("fundamental_statement", {"ticker": "AAPL", "statement": "income"}).payload
    figure = render_chart_model(chart)

    assert len(figure.data) == 2
    assert figure.data[0].name == "Net Income" or figure.data[0].name == "Revenue"
    assert figure.layout.title.text == "AAPL - Income Statement (Quarterly)"
