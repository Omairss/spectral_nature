from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


_MIN_ESTIMATION_DAYS = 15
_MIN_EVENT_DAYS = 3


def _daily_returns(close: pd.Series) -> pd.Series:
    prices = pd.to_numeric(close, errors="coerce")
    returns = np.log(prices / prices.shift(1))
    return returns.dropna()


def _market_model(
    symbol_returns: pd.Series,
    benchmark_returns: pd.Series,
) -> tuple[float, float, pd.Series]:
    """OLS regression of symbol on benchmark returns. Returns (alpha, beta, residuals)."""
    aligned = pd.concat(
        [symbol_returns.rename("sym"), benchmark_returns.rename("bench")], axis=1
    ).dropna()
    if aligned.empty or len(aligned) < 5:
        return 0.0, 1.0, symbol_returns

    x = aligned["bench"].values
    y = aligned["sym"].values
    x_with_const = np.column_stack([np.ones(len(x)), x])
    try:
        coeffs, *_ = np.linalg.lstsq(x_with_const, y, rcond=None)
    except Exception:
        return 0.0, 1.0, symbol_returns

    alpha, beta = float(coeffs[0]), float(coeffs[1])
    residuals = pd.Series(y - (alpha + beta * x), index=aligned.index)
    return alpha, beta, residuals


def compute_event_significance(
    bars_by_symbol: dict[str, pd.DataFrame],
    event_date: str,
    *,
    pre_window_days: int = 60,
    post_window_days: int = 30,
    benchmark: str = "SPY",
) -> pd.DataFrame:
    """
    For each symbol, compute market-adjusted cumulative abnormal return (CAR)
    and its t-statistic over the post-event window.

    Parameters
    ----------
    bars_by_symbol : symbol → DataFrame with 'timestamp' (UTC) and 'close' columns
    event_date     : YYYY-MM-DD string marking the start of the event window
    pre_window_days: trading-day lookback for estimation window (default 60)
    post_window_days: trading-day lookahead for event window (default 30)
    benchmark      : symbol to use as market benchmark (default "SPY")

    Returns
    -------
    DataFrame sorted by abs(t_stat) descending with columns:
        symbol, car_pct, t_stat, p_value, significance, direction,
        estimation_n, event_n, alpha, beta
    """
    event_dt = pd.to_datetime(event_date, utc=True, errors="coerce")
    if pd.isna(event_dt):
        return pd.DataFrame()

    benchmark_sym = str(benchmark).upper().strip()
    bench_frame = bars_by_symbol.get(benchmark_sym)
    bench_returns: pd.Series | None = None
    if bench_frame is not None and not bench_frame.empty:
        bench_frame = bench_frame.copy()
        bench_frame["timestamp"] = pd.to_datetime(bench_frame["timestamp"], utc=True, errors="coerce")
        bench_frame = bench_frame.dropna(subset=["timestamp", "close"]).sort_values("timestamp")
        bench_frame = bench_frame.set_index("timestamp")["close"]
        bench_returns = _daily_returns(bench_frame)

    rows: list[dict] = []
    for symbol, frame in bars_by_symbol.items():
        if symbol == benchmark_sym:
            continue
        if frame is None or frame.empty:
            continue

        frame = frame.copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["timestamp", "close"]).sort_values("timestamp")
        frame = frame.set_index("timestamp")["close"]

        all_returns = _daily_returns(frame)

        pre = all_returns[all_returns.index < event_dt].tail(pre_window_days)
        post = all_returns[all_returns.index >= event_dt].head(post_window_days)

        if len(pre) < _MIN_ESTIMATION_DAYS or len(post) < _MIN_EVENT_DAYS:
            continue

        # Market-adjusted abnormal returns in post window
        if bench_returns is not None:
            alpha, beta, estimation_residuals = _market_model(pre, bench_returns.reindex(pre.index).dropna())
            bench_post = bench_returns.reindex(post.index).fillna(0.0)
            abnormal_post = post - (alpha + beta * bench_post)
            # Residual std from estimation window
            pre_bench = bench_returns.reindex(pre.index).dropna()
            pre_aligned = pre.reindex(pre_bench.index).dropna()
            pre_residuals = pre_aligned - (alpha + beta * pre_bench.reindex(pre_aligned.index))
            sigma = float(pre_residuals.std(ddof=1)) if len(pre_residuals) >= 2 else float(pre.std(ddof=1))
        else:
            alpha, beta = 0.0, float("nan")
            abnormal_post = post - float(pre.mean())
            sigma = float(pre.std(ddof=1))

        car = float(abnormal_post.sum())
        event_n = len(post)

        if sigma <= 0 or not np.isfinite(sigma):
            continue

        # t-stat: CAR / (sigma * sqrt(T))
        t_stat = car / (sigma * np.sqrt(event_n))
        df = max(len(pre) - 2, 1)
        p_value = float(2 * stats.t.sf(abs(t_stat), df=df))

        # Total price return over event window for display
        post_prices = frame[frame.index >= event_dt].head(post_window_days + 1)
        if len(post_prices) >= 2:
            car_pct = (float(post_prices.iloc[-1]) / float(post_prices.iloc[0]) - 1.0) * 100.0
        else:
            car_pct = float(np.expm1(car) * 100.0)

        if p_value < 0.01:
            significance = "high"
        elif p_value < 0.05:
            significance = "moderate"
        elif p_value < 0.10:
            significance = "low"
        else:
            significance = "none"

        rows.append(
            {
                "symbol": symbol,
                "car_pct": round(car_pct, 2),
                "t_stat": round(t_stat, 3),
                "p_value": round(p_value, 4),
                "significance": significance,
                "direction": "up" if car_pct >= 0 else "down",
                "estimation_n": len(pre),
                "event_n": event_n,
                "alpha": round(alpha, 6),
                "beta": round(beta, 3) if np.isfinite(beta) else None,
            }
        )

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)
    result = result.sort_values("t_stat", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)
    return result


__all__ = ["compute_event_significance"]
