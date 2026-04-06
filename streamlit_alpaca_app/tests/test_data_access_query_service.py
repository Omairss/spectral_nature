from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data_access.contracts import DataProvenance, QueryRequest, ResolvedPayload
from data_access.layer import DataAccessLayer
from data_access.query_service import QueryService
from presentation.plotly import render_chart_model


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
        raise AssertionError(f"unexpected dataset: {dataset_name}")

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


def test_resolve_attention_home_1d_rejects_stat_dump_materialized_payload(monkeypatch):
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
    live_payload = {
        "run_id": "live-run",
        "generated_at_utc": "2026-03-30T03:15:00Z",
        "top_events": [
            {
                "bundle_id": "event::cluster-02-067808a0a0",
                "event_title": "Airlines lower while energy names hold up",
                "what_happened_text": "Airline and travel names moved lower while energy-linked names were relatively firm.",
                "why_happened_text": "Higher fuel-cost expectations can pressure airline margins and sentiment around discretionary travel demand.",
                "affected_assets_summary_text": "The move spilled into travel-adjacent names while integrated energy equities held up better.",
            }
        ],
        "must_read_movers": [],
        "unresolved_large_moves": [],
        "coverage_summary": {"candidate_count": 1},
        "taxonomy_horizon_trends": [],
        "event_candidates_1d": [],
        "event_impacts_1d": [],
        "entity_master": [],
    }

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(
        DataAccessLayer,
        "_pipeline_frame",
        lambda self, dataset_name: (materialized_home.copy(), {"dataset_name": dataset_name}),
    )
    monkeypatch.setattr(
        DataAccessLayer,
        "_resolve_live_attention_artifacts",
        lambda self, force_refresh: {"home_payload": live_payload, "bundle_map": {}, "run_id": "live-run"},
    )

    resolved = DataAccessLayer().resolve_attention_home_1d()

    assert resolved.provenance.mode == "on_demand"
    assert resolved.payload["run_id"] == "live-run"
    assert resolved.payload["top_events"][0]["event_title"] == "Airlines lower while energy names hold up"


def test_resolve_attention_research_bundle_rejects_stat_dump_materialized_payload(monkeypatch):
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
    live_bundle = {
        "bundle_id": bundle_id,
        "bundle_type": "event",
        "event_title": "Airlines lower while energy names hold up",
        "what_happened_text": "Airline and travel names moved lower while energy-linked names remained comparatively firm.",
        "why_happened_text": "Higher expected fuel costs can compress airline margins and weigh on travel demand expectations.",
        "affected_assets_summary_text": "Spillover stayed concentrated in travel-adjacent names while integrated energy held up better.",
    }

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(
        DataAccessLayer,
        "_pipeline_frame",
        lambda self, dataset_name: (materialized_bundle.copy(), {"dataset_name": dataset_name}),
    )
    monkeypatch.setattr(
        DataAccessLayer,
        "_resolve_live_attention_artifacts",
        lambda self, force_refresh: {"home_payload": {}, "bundle_map": {bundle_id: live_bundle}, "run_id": "live-run"},
    )

    resolved = DataAccessLayer().resolve_attention_research_bundle(bundle_id)

    assert resolved.provenance.mode == "on_demand"
    assert resolved.payload["event_title"] == "Airlines lower while energy names hold up"
    assert "USO +5.95%" not in resolved.payload["what_happened_text"]


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

    resolved = DataAccessLayer().resolve_attention_research_bundle(bundle_id)

    assert resolved.provenance.mode == "on_demand"
    assert resolved.payload["headline"].startswith("Bristol Myers drawdown")
    assert "attention_search_results" in resolved.provenance.datasets


def test_resolve_attention_research_bundle_symbol_precomputed_only_skips_on_demand(monkeypatch):
    monkeypatch.delenv("ATTENTION_SYMBOL_BUNDLE_PRECOMPUTED_ONLY", raising=False)

    bundle_id = "symbol::BMY"
    materialized_bundle_payload = {
        "bundle_id": bundle_id,
        "bundle_type": "symbol",
        "headline": "BMY precomputed snapshot",
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

    resolved = DataAccessLayer().resolve_attention_research_bundle(bundle_id)

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.details.get("precomputed_only") is True
    assert resolved.payload["headline"] == "BMY precomputed snapshot"


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


def test_resolve_attention_ticker_background_reports_no_relevant_agentic_news(monkeypatch):
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

    assert resolved.payload["description_text"].startswith("No relevant catalyst found")
    assert resolved.payload["news_summary_lines"][0].startswith("No relevant catalyst found")
    assert resolved.payload["recent_headlines"] == []
    assert resolved.payload["source_trace"]["relevant_news_count"] == 0


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
    assert not resolved.payload["description_text"].startswith("No relevant catalyst found")
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
            "published_at": [pd.Timestamp("2026-03-30T11:00:00Z"), pd.NaT],
            "url": ["https://example.com/vrdn-search", ""],
            "fallback_summary": ["", "Search backfill summary"],
        }
    )
    metadata = {
        "news_articles": None,
        "attention_web_search_news": SimpleNamespace(
            dataset_name="attention_web_search_news",
            dataset_version_id="attention_web_search_news__20260330T120000Z__abcd1234",
            blob_path="datasets/attention_web_search_news/example.parquet",
            asof_time_utc="2026-03-30T12:00:00Z",
            ingested_at_utc="2026-03-30T12:05:00Z",
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
    assert capabilities["datasets"]["commodity_attention_feed"]["resolution"] == "materialized"
    assert capabilities["charts"]["technical_price_channel"]["resolution"] == "computed_from_signal_history"


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

    service = QueryService(data_access=FakeAccess())
    attention_context = service.fetch_dataset("attention_context", {"ticker": "AAPL", "force_refresh": True})
    attention_feed = service.fetch_dataset("attention_feed", {"limit": 5, "entity_ids": ["TSLA"], "statuses": ["cooling"]})
    attention_rollups = service.fetch_dataset("attention_rollups", {"rollup_type": "business_lens", "limit": 3, "force_refresh": True})
    commodity_attention_feed = service.fetch_dataset("commodity_attention_feed", {"limit": 2, "entity_ids": ["GLD"], "statuses": ["active"]})
    commodity_attention_rollups = service.fetch_dataset("commodity_attention_rollups", {"rollup_type": "commodity_focus", "limit": 2})

    assert attention_context.payload["symbol"] == "AAPL"
    assert attention_feed.payload["entity_id"].tolist() == ["TSLA"]
    assert attention_rollups.payload["rollup_name"].tolist() == ["All Market"]
    assert commodity_attention_feed.payload["entity_id"].tolist() == ["GLD"]
    assert commodity_attention_rollups.payload["rollup_name"].tolist() == ["Precious Metals"]


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
