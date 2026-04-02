from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from compute.analytics import select_signed_ranked
from compute.fred import build_fred_dashboard_from_pipeline
from compute.portfolio import (
    build_portfolio_timeseries,
    filter_portfolio_timeseries_period,
    normalize_timeseries_view,
    select_holding_roc_view,
)


def test_normalize_timeseries_view_scales_each_series_independently():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-03-10", "2026-03-11", "2026-03-12"]),
            "portfolio": [50.0, 55.0, 60.0],
            "SPY": [100.0, 110.0, 120.0],
        }
    )

    out = normalize_timeseries_view(frame)

    assert out["portfolio"].round(6).tolist() == [100.0, 110.0, 120.0]
    assert out["SPY"].round(6).tolist() == [100.0, 110.0, 120.0]


def test_normalize_timeseries_view_anchors_on_first_non_zero_value():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13"]),
            "portfolio": [0.0, 0.0, 50.0, 55.0],
            "SPY": [100.0, 101.0, 102.0, 103.0],
        }
    )

    out = normalize_timeseries_view(frame)

    assert pd.isna(out.loc[0, "portfolio"])
    assert pd.isna(out.loc[1, "portfolio"])
    assert out.loc[2, "portfolio"] == 100.0
    assert out.loc[3, "portfolio"] == pytest.approx(110.0)
    assert out["SPY"].round(6).tolist() == [100.0, 101.0, 102.0, 103.0]


def test_build_portfolio_timeseries_trims_leading_zero_equity_rows():
    timestamps = pd.to_datetime(
        ["2026-03-10T20:00:00Z", "2026-03-11T20:00:00Z", "2026-03-12T20:00:00Z", "2026-03-13T20:00:00Z"],
        utc=True,
    )

    class FakeAPI:
        def get_portfolio_history(self, *, period: str, timeframe: str) -> pd.DataFrame:
            assert period == "1Y"
            assert timeframe == "1D"
            return pd.DataFrame({"timestamp": timestamps, "equity": [0.0, 0.0, 50.0, 55.0]})

        def get_stock_bars(self, symbols, *, start, timeframe: str, feed: str) -> dict[str, pd.DataFrame]:
            assert timeframe == "1Day"
            assert feed == "iex"
            return {
                symbol: pd.DataFrame({"timestamp": timestamps, "close": [100.0, 101.0, 102.0, 103.0]})
                for symbol in symbols
            }

    out = build_portfolio_timeseries(FakeAPI(), "1Y")

    assert out["timestamp"].dt.strftime("%Y-%m-%d").tolist() == ["2026-03-12", "2026-03-13"]
    assert out["portfolio"].tolist() == [50.0, 55.0]


def test_build_portfolio_timeseries_degrades_to_portfolio_only_when_benchmarks_fail():
    timestamps = pd.to_datetime(
        ["2026-03-10T20:00:00Z", "2026-03-11T20:00:00Z", "2026-03-12T20:00:00Z"],
        utc=True,
    )

    class FakeAPI:
        def get_portfolio_history(self, *, period: str, timeframe: str) -> pd.DataFrame:
            return pd.DataFrame({"timestamp": timestamps, "equity": [50.0, 52.0, 55.0]})

        def get_stock_bars(self, symbols, *, start, timeframe: str, feed: str) -> dict[str, pd.DataFrame]:
            raise RuntimeError("benchmark fetch failed")

    out = build_portfolio_timeseries(FakeAPI(), "1Y")

    assert out.columns.tolist() == ["timestamp", "portfolio"]
    assert out["portfolio"].tolist() == [50.0, 52.0, 55.0]


def test_filter_portfolio_timeseries_period_trims_to_requested_window():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-01", "2025-06-01", "2025-12-15", "2025-12-31"], utc=True),
            "portfolio": [90.0, 95.0, 100.0, 101.0],
        }
    )

    out = filter_portfolio_timeseries_period(frame, "1M")

    assert out["timestamp"].dt.strftime("%Y-%m-%d").tolist() == ["2025-12-15", "2025-12-31"]


def test_select_holding_roc_view_shapes_materialized_momentum_profiles():
    frame = pd.DataFrame(
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

    out = select_holding_roc_view(frame, ["msft"])

    assert out["symbol"].tolist() == ["MSFT"]
    assert out["roc_1w_to_1m"].tolist() == [0.4]


def test_build_fred_dashboard_from_pipeline_shapes_materialized_payload():
    summary = pd.DataFrame(
        {
            "series_id": ["CPIAUCSL"],
            "indicator": ["Headline CPI"],
            "units_short": ["Index 1982-1984=100"],
            "frequency_short": ["M"],
            "source_title": ["Consumer Price Index"],
            "last_updated": ["2026-03-01T00:00:00Z"],
        }
    )
    observations = pd.DataFrame(
        {
            "series_id": ["CPIAUCSL", "CPIAUCSL"],
            "date": ["2025-01-01", "2026-02-01"],
            "value": ["310.0", "315.0"],
            "release_id": [10, 10],
        }
    )

    payload = build_fred_dashboard_from_pipeline(summary, observations, years=2)

    assert payload["summary"]["series_id"].tolist() == ["CPIAUCSL"]
    assert payload["metadata"]["CPIAUCSL"]["title"] == "Consumer Price Index"
    assert payload["series_data"]["CPIAUCSL"]["value"].tolist() == [310.0, 315.0]
    assert payload["release_index"]["release_id"].tolist() == [10]
    assert "Inflation" in payload["specs_by_category"]


def test_select_signed_ranked_filters_to_true_gainers_and_losers():
    frame = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB", "CCC", "DDD", "EEE"],
            "return_1d_pct": [4.0, 2.0, -1.5, -3.0, 0.0],
        }
    )

    up = select_signed_ranked(frame, "return_1d_pct", direction="up", limit=20)
    down = select_signed_ranked(frame, "return_1d_pct", direction="down", limit=20)

    assert up["symbol"].tolist() == ["AAA", "BBB"]
    assert down["symbol"].tolist() == ["DDD", "CCC"]
    assert (up["return_1d_pct"] > 0).all()
    assert (down["return_1d_pct"] < 0).all()
