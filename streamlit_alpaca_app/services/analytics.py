from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


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
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return s
    base = s.iloc[0]
    if base == 0:
        return s * np.nan
    return (s / base) * 100.0



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
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return np.nan
    roll_max = s.cummax()
    drawdown = (s / roll_max) - 1.0
    return float(drawdown.min())



def beta_alpha(portfolio_ret: pd.Series, benchmark_ret: pd.Series, risk_free_rate: float = 0.02) -> tuple[float, float]:
    joined = pd.concat([portfolio_ret, benchmark_ret], axis=1, join="inner").dropna()
    if joined.empty:
        return np.nan, np.nan

    p = joined.iloc[:, 0]
    b = joined.iloc[:, 1]
    var_b = b.var(ddof=0)
    if var_b == 0 or np.isnan(var_b):
        return np.nan, np.nan

    beta = float(np.cov(p, b, ddof=0)[0, 1] / var_b)
    daily_rf = risk_free_rate / 252.0
    alpha = float((p.mean() - daily_rf) - beta * (b.mean() - daily_rf)) * 252.0
    return beta, alpha



def build_portfolio_vs_benchmarks_fig(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for col in df.columns:
        if col == "timestamp":
            continue
        line_width = 3 if col == "portfolio" else 1.5
        opacity = 1.0 if col == "portfolio" else 0.65
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df[col],
                mode="lines",
                name=col,
                line={"width": line_width},
                opacity=opacity,
            )
        )

    fig.update_layout(
        title="Portfolio vs Benchmarks (Normalized to 100)",
        xaxis_title="Date",
        yaxis_title="Normalized Value",
        hovermode="x unified",
        template="plotly_dark",
    )
    return fig



def performance_table(timeseries: pd.DataFrame, risk_free_rate: float = 0.02) -> pd.DataFrame:
    if timeseries.empty:
        return pd.DataFrame()

    frame = timeseries.copy().dropna(subset=["timestamp"]).sort_values("timestamp")
    frame["year"] = frame["timestamp"].dt.year.astype(str)

    rows: list[MetricRow] = []
    spy_col = "SPY" if "SPY" in frame.columns else None

    symbols = [c for c in frame.columns if c not in {"timestamp", "year"}]
    for symbol in symbols:
        for year, chunk in frame.groupby("year"):
            if len(chunk) < 3:
                continue
            r = returns(chunk[symbol])
            ann = float((chunk[symbol].iloc[-1] / chunk[symbol].iloc[0]) - 1.0) if chunk[symbol].iloc[0] else np.nan
            shrp = sharpe_ratio(r, risk_free_rate=risk_free_rate)

            if spy_col and symbol != spy_col:
                beta, alpha = beta_alpha(r, returns(chunk[spy_col]), risk_free_rate=risk_free_rate)
            else:
                beta, alpha = np.nan, np.nan

            mdd = max_drawdown(chunk[symbol])
            rows.append(
                MetricRow(
                    entity=symbol,
                    year=year,
                    annual_return=ann,
                    sharpe_ratio=shrp,
                    beta_vs_spy=beta,
                    alpha_vs_spy=alpha,
                    max_drawdown=mdd,
                )
            )

        # cumulative row
        full = frame[["timestamp", symbol] + ([spy_col] if spy_col else [])].dropna(subset=[symbol])
        if len(full) >= 3:
            r = returns(full[symbol])
            ann = float((full[symbol].iloc[-1] / full[symbol].iloc[0]) - 1.0) if full[symbol].iloc[0] else np.nan
            shrp = sharpe_ratio(r, risk_free_rate=risk_free_rate)
            if spy_col and symbol != spy_col:
                beta, alpha = beta_alpha(r, returns(full[spy_col]), risk_free_rate=risk_free_rate)
            else:
                beta, alpha = np.nan, np.nan
            mdd = max_drawdown(full[symbol])
            rows.append(
                MetricRow(
                    entity=symbol,
                    year="Cumulative",
                    annual_return=ann,
                    sharpe_ratio=shrp,
                    beta_vs_spy=beta,
                    alpha_vs_spy=alpha,
                    max_drawdown=mdd,
                )
            )

    out = pd.DataFrame([r.__dict__ for r in rows])
    return out



def build_metric_bar(df: pd.DataFrame, metric: str) -> go.Figure:
    if df.empty or metric not in df.columns:
        return go.Figure()
    chart = df[df["year"] != "Cumulative"].copy()
    if chart.empty:
        chart = df.copy()
    fig = px.bar(
        chart,
        x="year",
        y=metric,
        color="entity",
        barmode="group",
        template="plotly_dark",
        title=metric.replace("_", " ").title(),
    )
    return fig
