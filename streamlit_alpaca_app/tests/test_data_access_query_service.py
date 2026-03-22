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
    }

    def _load(dataset_name: str):
        if dataset_name == "fred_summary":
            return summary.copy(), metadata[dataset_name]
        if dataset_name == "fred_observations":
            return observations.copy(), metadata[dataset_name]
        raise AssertionError(f"unexpected dataset: {dataset_name}")

    monkeypatch.setattr(layer_module, "pipeline_store_configured", lambda: True)
    monkeypatch.setattr(layer_module, "load_latest_dataset_frame", _load)

    resolved = DataAccessLayer(fred_api_key="").resolve_fred_dashboard(years=3)

    assert resolved.provenance.mode == "materialized"
    assert resolved.provenance.datasets == ("fred_summary", "fred_observations")
    assert resolved.provenance.details["summary"]["dataset_version_id"] == metadata["fred_summary"].dataset_version_id
    assert resolved.payload["series_data"]["CPIAUCSL"]["value"].tolist() == [315.0, 316.2]


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
    assert capabilities["datasets"]["portfolio_timeseries"]["resolution"] == "live_cached"
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
