from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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
    assert capabilities["charts"]["technical_price_channel"]["resolution"] == "computed_from_signal_history"


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
