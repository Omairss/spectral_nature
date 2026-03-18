from __future__ import annotations

from datetime import timezone

import numpy as np
import pandas as pd

from compute.analytics import BENCHMARKS, normalize_to_100
from services.alpaca_api import AlpacaAPI


def _daily_series(frame: pd.DataFrame, value_col: str) -> pd.DataFrame:
    if frame.empty or "timestamp" not in frame.columns:
        return pd.DataFrame(columns=["timestamp", value_col])

    out = frame.copy()
    timestamps = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out["timestamp"] = timestamps.dt.tz_convert("US/Eastern").dt.floor("D").dt.tz_localize(None)
    out[value_col] = pd.to_numeric(out[value_col], errors="coerce")
    out = out.dropna(subset=["timestamp", value_col])
    return out.groupby("timestamp", as_index=False)[value_col].last().sort_values("timestamp")


def build_portfolio_timeseries(api: AlpacaAPI, period: str) -> pd.DataFrame:
    history = api.get_portfolio_history(period=period, timeframe="1D")
    if history.empty:
        return pd.DataFrame()

    portfolio = _daily_series(history, "equity").rename(columns={"equity": "portfolio"})
    start = history["timestamp"].min().to_pydatetime().replace(tzinfo=timezone.utc)
    benchmark_bars = api.get_stock_bars(BENCHMARKS, start=start, timeframe="1Day", feed="iex")

    merged = portfolio.copy()
    for symbol in BENCHMARKS:
        benchmark_frame = benchmark_bars.get(symbol, pd.DataFrame())
        if benchmark_frame.empty:
            continue
        daily = _daily_series(benchmark_frame, "close").rename(columns={"close": symbol.replace("-", ".")})
        merged = merged.merge(daily, on="timestamp", how="left")

    merged = merged.sort_values("timestamp")
    for column in [column for column in merged.columns if column != "timestamp"]:
        merged[column] = merged[column].ffill()

    merged = merged.dropna(subset=["portfolio"]).reset_index(drop=True)
    funded_rows = np.flatnonzero(pd.to_numeric(merged["portfolio"], errors="coerce").to_numpy(dtype=float) > 0)
    if len(funded_rows):
        merged = merged.iloc[funded_rows[0] :].reset_index(drop=True)
    return merged


def normalize_timeseries_view(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw

    out = raw.copy()
    for column in [column for column in out.columns if column != "timestamp"]:
        out[column] = normalize_to_100(out[column])
    return out


def compute_holding_roc(api: AlpacaAPI, symbols: list[str], days: int = 365) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()

    bars = api.get_stock_bars(symbols, timeframe="1Day", feed="iex")
    rows: list[dict[str, object]] = []
    windows = {"1d": 2, "1w": 5, "1m": 21, "3m": 63}

    for symbol in symbols:
        frame = bars.get(symbol, pd.DataFrame())
        if frame.empty or "close" not in frame.columns:
            continue

        series = pd.to_numeric(frame["close"], errors="coerce").dropna().tail(days)
        if len(series) < max(windows.values()):
            continue

        def _slope(chunk: pd.Series, length: int) -> float:
            values = np.log(chunk.tail(length).to_numpy(dtype=float))
            x_axis = np.arange(len(values), dtype=float)
            slope, _ = np.polyfit(x_axis, values, 1)
            return float(slope)

        slope_1d = _slope(series, windows["1d"])
        slope_1w = _slope(series, windows["1w"])
        slope_1m = _slope(series, windows["1m"])
        slope_3m = _slope(series, windows["3m"])

        rows.append(
            {
                "symbol": symbol,
                "roc_1d_to_1w": (slope_1w / slope_1d - 1.0) if slope_1d else np.nan,
                "roc_1w_to_1m": (slope_1m / slope_1w - 1.0) if slope_1w else np.nan,
                "roc_1m_to_3m": (slope_3m / slope_1m - 1.0) if slope_1m else np.nan,
                "momentum_1w": slope_1w,
                "momentum_1m": slope_1m,
                "momentum_3m": slope_3m,
            }
        )

    return pd.DataFrame(rows)


__all__ = [
    "build_portfolio_timeseries",
    "compute_holding_roc",
    "normalize_timeseries_view",
]
