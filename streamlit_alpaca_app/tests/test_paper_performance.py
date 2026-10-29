"""Objective daily performance arithmetic; no research prose grading."""
import numpy as np
import pandas as pd
import pytest
from compute.analytics import performance_summary, sharpe_ratio


def test_daily_performance_uses_changes_in_wealth_and_reports_its_convention():
    result = performance_summary([
        {"date": "2026-10-20", "return_pct": 0},
        {"date": "2026-10-21", "return_pct": 10},
        {"date": "2026-10-22", "return_pct": -1},
        {"date": "2026-10-23", "return_pct": 8.9},
    ])
    daily = np.array([.1, -.1, .1])
    assert result["return_pct"] == pytest.approx(8.9)
    assert result["sharpe_ratio"] == pytest.approx((daily.mean() - .02 / 252) / daily.std() * np.sqrt(252))
    assert result["max_drawdown_pct"] == pytest.approx(-10)
    assert result["daily_observations"] == 3
    assert result["risk_free_rate_pct"] == 2
    assert result["start_date"] == "2026-10-20" and result["end_date"] == "2026-10-23"


def test_missing_daily_marks_are_not_filled_or_bridged():
    result = performance_summary([
        {"date": "2026-10-20", "return_pct": 0},
        {"date": "2026-10-21", "return_pct": None},
        {"date": "2026-10-22", "return_pct": 20},
        {"date": "2026-10-23", "return_pct": 32},
    ])
    assert result["daily_observations"] == 1
    assert result["sharpe_ratio"] is None
    assert result["return_pct"] == pytest.approx(32)


@pytest.mark.parametrize("points", [[], [{"date": "2026-10-20", "return_pct": 0}],
    [{"date": day, "return_pct": 0} for day in ("2026-10-20", "2026-10-21", "2026-10-22")]])
def test_empty_single_or_constant_series_never_invents_a_sharpe(points):
    import json
    result = performance_summary(points)
    assert result["sharpe_ratio"] is None
    json.dumps(result, allow_nan=False)


def test_nonpositive_initial_wealth_has_no_fabricated_drawdown_or_daily_return():
    import json
    result = performance_summary([
        {"date": "2026-10-20", "return_pct": -200},
        {"date": "2026-10-21", "return_pct": -250},
    ])
    assert result["max_drawdown_pct"] is None and result["daily_observations"] == 0
    json.dumps(result, allow_nan=False)



@pytest.mark.parametrize("count", [2, 3, 22, 252])
@pytest.mark.parametrize("daily_return", [0.0, 0.001])
def test_sharpe_is_undefined_for_constant_daily_returns(count, daily_return):
    assert np.isnan(sharpe_ratio(pd.Series([daily_return] * count)))


def test_flat_funded_account_has_zero_return_and_no_sharpe():
    from compute.paper_portfolio import period_performance
    points = [{"date": date.isoformat(), "equity": 100000.0,
               "external_cashflow": 0, "cashflow_coverage": "reported"}
              for date in pd.date_range("2026-09-29", periods=23, freq="B", tz="UTC")]
    result = period_performance(points)
    assert result["profit_loss"] == 0
    assert result["return_pct"] == 0
    assert result["performance"]["volatility_pct"] == 0
    assert result["performance"]["sharpe_ratio"] is None
