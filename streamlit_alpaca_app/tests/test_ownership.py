from __future__ import annotations

import pandas as pd

from compute.ownership import project_account_view, project_portfolio_timeseries, project_positions_view


def test_project_account_view_scales_dollar_fields_only():
    account = {
        "equity": "1000",
        "cash": "250",
        "portfolio_value": "1200",
        "status": "ACTIVE",
        "buying_power": "5000",
    }

    projected = project_account_view(account, 0.25)

    assert projected["equity"] == 250.0
    assert projected["cash"] == 62.5
    assert projected["portfolio_value"] == 300.0
    assert projected["status"] == "ACTIVE"
    assert projected["buying_power"] == "5000"
    assert projected["viewer_share_fraction"] == 0.25


def test_project_positions_view_scales_effective_exposure_and_renames_qty():
    positions = pd.DataFrame(
        {
            "symbol": ["AAPL"],
            "qty": [10.0],
            "avg_entry_price": [100.0],
            "market_value": [1250.0],
            "unrealized_pl": [250.0],
            "unrealized_plpc": [0.25],
        }
    )

    projected = project_positions_view(positions, 0.4)

    assert "effective_qty" in projected.columns
    assert projected.loc[0, "effective_qty"] == 4.0
    assert projected.loc[0, "market_value"] == 500.0
    assert projected.loc[0, "unrealized_pl"] == 100.0
    assert projected.loc[0, "unrealized_plpc"] == 0.25


def test_project_portfolio_timeseries_scales_portfolio_not_benchmarks():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-03-20", "2026-03-21"]),
            "portfolio": [1000.0, 1100.0],
            "SPY": [100.0, 101.0],
        }
    )

    projected = project_portfolio_timeseries(frame, 0.5)

    assert projected["portfolio"].tolist() == [500.0, 550.0]
    assert projected["SPY"].tolist() == [100.0, 101.0]
