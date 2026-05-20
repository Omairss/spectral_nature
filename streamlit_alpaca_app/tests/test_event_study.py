from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from compute.event_study import compute_event_significance


def _make_bars(
    *,
    start: str,
    periods: int,
    daily_return: float = 0.0,
    seed_price: float = 100.0,
) -> pd.DataFrame:
    dates = pd.date_range(start, periods=periods, freq="B", tz="UTC")
    prices = [seed_price * ((1 + daily_return) ** i) for i in range(periods)]
    return pd.DataFrame({"timestamp": dates, "close": prices})


def _make_noisy_bars(
    *,
    start: str,
    periods: int,
    mean_return: float = 0.0,
    vol: float = 0.012,
    seed: int = 42,
    seed_price: float = 100.0,
) -> pd.DataFrame:
    """Realistic bars with daily noise — avoids zero-variance pre-window."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=periods, freq="B", tz="UTC")
    daily_returns = rng.normal(mean_return, vol, periods)
    prices = [seed_price]
    for r in daily_returns[1:]:
        prices.append(prices[-1] * (1.0 + r))
    return pd.DataFrame({"timestamp": dates, "close": prices})


def _make_bars_dict(
    event_date: str,
    *,
    pre_days: int = 30,
    post_days: int = 20,
) -> dict[str, pd.DataFrame]:
    total_days = pre_days + post_days + 5
    return {
        "XYZ": _make_noisy_bars(start="2025-01-01", periods=total_days, seed=1),
        "SPY": _make_noisy_bars(start="2025-01-01", periods=total_days, seed=2),
    }


def test_returns_dataframe_with_expected_columns():
    bars = _make_bars_dict("2025-02-10")
    result = compute_event_significance(bars, "2025-02-10", pre_window_days=20, post_window_days=10)
    assert isinstance(result, pd.DataFrame)
    for col in ("symbol", "car_pct", "t_stat", "p_value", "significance", "direction", "estimation_n", "event_n"):
        assert col in result.columns, f"missing column: {col}"


def test_empty_when_bars_empty():
    result = compute_event_significance({}, "2025-02-10")
    assert result.empty


def test_empty_when_not_enough_data():
    bars = {
        "XYZ": _make_bars(start="2025-02-05", periods=5, daily_return=0.01),
        "SPY": _make_bars(start="2025-02-05", periods=5, daily_return=0.0),
    }
    result = compute_event_significance(bars, "2025-02-10", pre_window_days=20, post_window_days=10)
    assert result.empty
    assert result.attrs["diagnostics"]["empty_reason"] == "insufficient_observations"
    assert result.attrs["diagnostics"]["required_event_observations"] == 3
    assert result.attrs["diagnostics"]["max_event_observations"] < 3


def test_large_post_move_yields_high_significance():
    # XYZ noisy pre-window, then jumps ~3% per day in post-window
    # SPY noisy but flat throughout
    pre_xyz = _make_noisy_bars(start="2025-01-01", periods=40, mean_return=0.0, vol=0.01, seed=10)
    post_xyz = _make_bars(start="2025-03-01", periods=20, daily_return=0.03, seed_price=float(pre_xyz["close"].iloc[-1]))
    xyz = pd.concat([pre_xyz, post_xyz], ignore_index=True)

    spy = _make_noisy_bars(start="2025-01-01", periods=60, mean_return=0.0, vol=0.01, seed=20)

    bars = {"XYZ": xyz, "SPY": spy}
    result = compute_event_significance(bars, "2025-03-01", pre_window_days=30, post_window_days=15)
    assert not result.empty
    row = result[result["symbol"] == "XYZ"].iloc[0]
    assert row["significance"] in ("high", "moderate"), f"expected significant, got {row['significance']}"
    assert row["direction"] == "up"
    assert row["t_stat"] > 0


def test_flat_post_move_not_significant():
    # XYZ and SPY both noisy with zero mean — no abnormal return in post window
    xyz = _make_noisy_bars(start="2025-01-01", periods=80, mean_return=0.0, vol=0.01, seed=30)
    spy = _make_noisy_bars(start="2025-01-01", periods=80, mean_return=0.0, vol=0.01, seed=31)
    bars = {"XYZ": xyz, "SPY": spy}
    result = compute_event_significance(bars, "2025-03-15", pre_window_days=40, post_window_days=20)
    if result.empty:
        return
    row = result[result["symbol"] == "XYZ"].iloc[0]
    # With random noise and no systematic post-event move, significance should not be high
    assert row["significance"] in ("none", "low", "moderate")


def test_sorted_by_abs_t_stat():
    # XYZ: big post-event move; ABC: tiny post-event move; both with noisy pre-windows
    pre_xyz = _make_noisy_bars(start="2025-01-01", periods=40, vol=0.01, seed=40)
    post_xyz = _make_bars(start="2025-03-01", periods=20, daily_return=0.04, seed_price=float(pre_xyz["close"].iloc[-1]))
    xyz = pd.concat([pre_xyz, post_xyz], ignore_index=True)

    pre_abc = _make_noisy_bars(start="2025-01-01", periods=40, vol=0.01, seed=41)
    post_abc = _make_bars(start="2025-03-01", periods=20, daily_return=0.0003, seed_price=float(pre_abc["close"].iloc[-1]))
    abc = pd.concat([pre_abc, post_abc], ignore_index=True)

    spy = _make_noisy_bars(start="2025-01-01", periods=80, vol=0.01, seed=42)

    bars = {"XYZ": xyz, "ABC": abc, "SPY": spy}
    result = compute_event_significance(bars, "2025-03-01", pre_window_days=30, post_window_days=15)
    assert not result.empty
    abs_t = result["t_stat"].abs().tolist()
    assert abs_t == sorted(abs_t, reverse=True), "rows should be sorted by abs(t_stat) descending"
    # XYZ should rank higher than ABC
    symbols = result["symbol"].tolist()
    assert symbols.index("XYZ") < symbols.index("ABC")


def test_invalid_event_date_returns_empty():
    bars = _make_bars_dict("2025-02-10")
    result = compute_event_significance(bars, "not-a-date")
    assert result.empty


def test_benchmark_excluded_from_results():
    bars = _make_bars_dict("2025-02-10", pre_days=30, post_days=20)
    result = compute_event_significance(bars, "2025-02-10", pre_window_days=20, post_window_days=10)
    if not result.empty:
        assert "SPY" not in result["symbol"].tolist()
