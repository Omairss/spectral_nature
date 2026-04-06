from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from compute.analytics import BENCHMARKS, MetricRow, beta_alpha, max_drawdown, normalize_to_100, performance_table, returns, select_signed_ranked, sharpe_ratio



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
