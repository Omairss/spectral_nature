from __future__ import annotations

import pandas as pd

from services.market_opportunity import (
    build_market_opportunity_feed,
    build_materialized_market_opportunity_feeds,
    select_market_opportunity_feed,
)


def test_market_opportunity_feed_merges_movers_and_momentum_once_per_symbol():
    movers = pd.DataFrame(
        [
            {"symbol": "aapl", "change_pct": 2.1, "volume": 1200000},
            {"symbol": "msft", "change_pct": -3.4, "volume": 900000},
        ]
    )
    momentum = pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "close": 210.0,
                "daily_change_pct": 1.9,
                "return_1m_pct": 8.2,
                "return_1w_pct": 2.5,
                "return_3m_pct": 14.0,
                "momentum_score": 0.88,
                "momentum_roc_score": 0.72,
                "trend_fit_gap": 0.08,
                "sparkline_3m": [95, 100, 112],
                "volume": 100,
            },
            {
                "symbol": "MSFT",
                "close": 405.0,
                "daily_change_pct": -3.1,
                "return_1m_pct": -5.0,
                "return_1w_pct": -1.3,
                "return_3m_pct": 1.0,
                "momentum_score": -0.44,
                "momentum_roc_score": -0.81,
                "trend_fit_gap": 0.31,
                "sparkline_3m": [104, 101, 97],
            },
        ]
    )

    feed = build_market_opportunity_feed(
        movers=movers,
        momentum=momentum,
        selected_horizon_col="return_1m_pct",
        selected_horizon_label="1 Month",
        name_map={"AAPL": "Apple Inc.", "MSFT": "Microsoft Corp."},
    )

    assert feed["symbol"].tolist() == ["AAPL", "MSFT"]
    assert feed.loc[0, "company_name"] == "Apple Inc."
    assert feed.loc[0, "opportunity"] == "Upside momentum"
    assert feed.loc[1, "opportunity"] == "Downside pressure"
    assert "1 Month +8.2%" in feed.loc[0, "details"]
    assert "volume 1,200,000" in feed.loc[0, "details"]


def test_market_opportunity_feed_can_use_movers_without_momentum():
    feed = build_market_opportunity_feed(
        movers=pd.DataFrame([{"symbol": "TSLA", "close": 260.0, "change_pct": 5.4, "volume": 5000000}]),
        momentum=pd.DataFrame(),
        selected_horizon_col="return_1m_pct",
        selected_horizon_label="1 Month",
    )

    assert feed["symbol"].tolist() == ["TSLA"]
    assert feed.loc[0, "close"] == 260.0
    assert feed.loc[0, "daily_change_pct"] == 5.4
    assert feed.loc[0, "opportunity"] == "Daily dislocation"


def test_materialized_market_opportunity_feeds_include_focus_and_horizon_variants():
    movers = pd.DataFrame(
        [
            {"symbol": "AAPL", "change_pct": 2.1, "volume": 1200000},
            {"symbol": "MSFT", "change_pct": -3.4, "volume": 900000},
        ]
    )
    momentum = pd.DataFrame(
        [
            {"symbol": "AAPL", "return_1m_pct": 8.2, "return_3m_pct": 14.0, "momentum_roc_score": 0.72, "trend_fit_gap": 0.08},
            {"symbol": "MSFT", "return_1m_pct": -5.0, "return_3m_pct": 1.0, "momentum_roc_score": -0.81, "trend_fit_gap": 0.31},
        ]
    )

    materialized = build_materialized_market_opportunity_feeds(
        movers=movers,
        momentum=momentum,
        name_map={"AAPL": "Apple Inc.", "MSFT": "Microsoft Corp."},
        focus_symbol_map={"All Market": [], "Software": ["MSFT"]},
        horizon_specs=(
            {"key": "1m", "column": "return_1m_pct", "label": "1 Month"},
            {"key": "3m", "column": "return_3m_pct", "label": "3 Month"},
        ),
        asof_time_utc="2026-04-27T17:00:00Z",
        run_id="run-market",
        limit=10,
    )

    assert set(materialized["business_filter"]) == {"All Market", "Software"}
    assert set(materialized["selected_horizon_col"]) == {"return_1m_pct", "return_3m_pct"}
    assert materialized["run_id"].unique().tolist() == ["run-market"]

    selected = select_market_opportunity_feed(
        materialized,
        business_filter="Software",
        selected_horizon_col="return_1m_pct",
        symbols=["MSFT", "AAPL"],
        limit=5,
    )

    assert selected["symbol"].tolist() == ["MSFT"]
    assert selected.loc[0, "business_filter"] == "Software"
    assert selected.loc[0, "selected_horizon_label"] == "1 Month"
