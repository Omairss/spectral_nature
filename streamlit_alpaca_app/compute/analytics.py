from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


BENCHMARKS = ["SPY", "DIA", "QQQ", "VOO", "BRK.B", "ARKK"]


@dataclass
class MetricRow:
    entity: str
    year: str
    annual_return: float
    sharpe_ratio: float
    beta_vs_spy: float
    alpha_vs_spy: float
    max_drawdown: float


def normalize_to_100(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    values = numeric.dropna()
    if values.empty:
        return numeric.astype(float)

    non_zero = values[values != 0]
    if non_zero.empty:
        return pd.Series(np.nan, index=numeric.index, dtype=float)

    base_label = non_zero.index[0]
    base = float(non_zero.iloc[0])
    normalized = (numeric / base) * 100.0

    anchor_positions = np.flatnonzero(numeric.index == base_label)
    if len(anchor_positions):
        normalized.iloc[: anchor_positions[0]] = np.nan
    return normalized


def returns(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").pct_change().dropna()


def sharpe_ratio(ret: pd.Series, risk_free_rate: float = 0.02) -> float:
    if ret.empty:
        return np.nan
    daily_rf = risk_free_rate / 252.0
    excess = ret - daily_rf
    std = excess.std(ddof=0)
    if std == 0 or np.isnan(std):
        return np.nan
    return float((excess.mean() / std) * np.sqrt(252.0))


def max_drawdown(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return np.nan
    roll_max = values.cummax()
    drawdown = (values / roll_max) - 1.0
    return float(drawdown.min())


def beta_alpha(portfolio_ret: pd.Series, benchmark_ret: pd.Series, risk_free_rate: float = 0.02) -> tuple[float, float]:
    joined = pd.concat([portfolio_ret, benchmark_ret], axis=1, join="inner").dropna()
    if joined.empty:
        return np.nan, np.nan

    portfolio_values = joined.iloc[:, 0]
    benchmark_values = joined.iloc[:, 1]
    benchmark_variance = benchmark_values.var(ddof=0)
    if benchmark_variance == 0 or np.isnan(benchmark_variance):
        return np.nan, np.nan

    beta = float(np.cov(portfolio_values, benchmark_values, ddof=0)[0, 1] / benchmark_variance)
    daily_rf = risk_free_rate / 252.0
    alpha = float((portfolio_values.mean() - daily_rf) - beta * (benchmark_values.mean() - daily_rf)) * 252.0
    return beta, alpha


def performance_table(timeseries: pd.DataFrame, risk_free_rate: float = 0.02) -> pd.DataFrame:
    if timeseries.empty:
        return pd.DataFrame()

    frame = timeseries.copy().dropna(subset=["timestamp"]).sort_values("timestamp")
    frame["year"] = frame["timestamp"].dt.year.astype(str)

    rows: list[MetricRow] = []
    spy_col = "SPY" if "SPY" in frame.columns else None

    symbols = [column for column in frame.columns if column not in {"timestamp", "year"}]
    for symbol in symbols:
        for year, chunk in frame.groupby("year"):
            if len(chunk) < 3:
                continue
            symbol_returns = returns(chunk[symbol])
            annual_return = float((chunk[symbol].iloc[-1] / chunk[symbol].iloc[0]) - 1.0) if chunk[symbol].iloc[0] else np.nan
            sharpe = sharpe_ratio(symbol_returns, risk_free_rate=risk_free_rate)

            if spy_col and symbol != spy_col:
                beta, alpha = beta_alpha(symbol_returns, returns(chunk[spy_col]), risk_free_rate=risk_free_rate)
            else:
                beta, alpha = np.nan, np.nan

            rows.append(
                MetricRow(
                    entity=symbol,
                    year=year,
                    annual_return=annual_return,
                    sharpe_ratio=sharpe,
                    beta_vs_spy=beta,
                    alpha_vs_spy=alpha,
                    max_drawdown=max_drawdown(chunk[symbol]),
                )
            )

        cumulative_columns = ["timestamp", symbol]
        if spy_col and symbol != spy_col:
            cumulative_columns.append(spy_col)
        cumulative = frame[cumulative_columns].dropna(subset=[symbol])
        if len(cumulative) < 3:
            continue

        cumulative_returns = returns(cumulative[symbol])
        annual_return = float((cumulative[symbol].iloc[-1] / cumulative[symbol].iloc[0]) - 1.0) if cumulative[symbol].iloc[0] else np.nan
        sharpe = sharpe_ratio(cumulative_returns, risk_free_rate=risk_free_rate)
        if spy_col and symbol != spy_col:
            beta, alpha = beta_alpha(cumulative_returns, returns(cumulative[spy_col]), risk_free_rate=risk_free_rate)
        else:
            beta, alpha = np.nan, np.nan

        rows.append(
            MetricRow(
                entity=symbol,
                year="Cumulative",
                annual_return=annual_return,
                sharpe_ratio=sharpe,
                beta_vs_spy=beta,
                alpha_vs_spy=alpha,
                max_drawdown=max_drawdown(cumulative[symbol]),
            )
        )

    return pd.DataFrame([row.__dict__ for row in rows])


def select_signed_ranked(frame: pd.DataFrame, column: str, *, direction: str, limit: int = 20) -> pd.DataFrame:
    if frame.empty or column not in frame.columns:
        return frame.iloc[0:0].copy()

    out = frame.copy()
    out["_rank_score"] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna(subset=["_rank_score"])

    if direction == "up":
        out = out[out["_rank_score"] > 0].nlargest(limit, "_rank_score")
    elif direction == "down":
        out = out[out["_rank_score"] < 0].nsmallest(limit, "_rank_score")
    else:
        raise ValueError(f"Unsupported direction '{direction}'.")

    return out.drop(columns="_rank_score")


__all__ = [
    "BENCHMARKS",
    "MetricRow",
    "beta_alpha",
    "max_drawdown",
    "normalize_to_100",
    "performance_table",
    "returns",
    "select_signed_ranked",
    "sharpe_ratio",
]
