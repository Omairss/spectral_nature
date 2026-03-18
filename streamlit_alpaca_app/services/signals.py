from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from compute.signals import FEATURE_COLUMNS, build_signal_frame, forecast_next_week, summarize_signal_frame


def build_price_channel_figure(frame: pd.DataFrame, ticker: str) -> go.Figure:
    fig = go.Figure()
    if frame.empty:
        fig.update_layout(template="plotly_dark", title=f"{ticker} Price Channel")
        return fig

    latest = frame.iloc[-1]
    fig.add_trace(
        go.Scatter(
            x=frame["timestamp"],
            y=frame["channel_support"],
            mode="lines",
            line={"color": "rgba(45, 212, 191, 0.45)", "width": 1.5},
            name="Support",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=frame["timestamp"],
            y=frame["channel_resistance"],
            mode="lines",
            fill="tonexty",
            fillcolor="rgba(148, 163, 184, 0.12)",
            line={"color": "rgba(249, 115, 22, 0.45)", "width": 1.5},
            name="Resistance",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=frame["timestamp"],
            y=frame["close"],
            mode="lines",
            line={"color": "#f8fafc", "width": 2.5},
            name="Close",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=frame["timestamp"],
            y=frame["ath"],
            mode="lines",
            line={"color": "#a78bfa", "width": 1, "dash": "dash"},
            name="ATH",
        )
    )
    if np.isfinite(pd.to_numeric(latest.get("channel_support"), errors="coerce")):
        fig.add_hline(y=float(latest["channel_support"]), line_dash="dot", line_color="#2dd4bf")
    if np.isfinite(pd.to_numeric(latest.get("channel_resistance"), errors="coerce")):
        fig.add_hline(y=float(latest["channel_resistance"]), line_dash="dot", line_color="#f97316")

    fig.update_layout(
        template="plotly_dark",
        title=f"{ticker} Price Channel",
        xaxis_title="Date",
        yaxis_title="Price",
        hovermode="x unified",
    )
    return fig


def build_pullback_figure(frame: pd.DataFrame, ticker: str) -> go.Figure:
    fig = go.Figure()
    if frame.empty:
        fig.update_layout(template="plotly_dark", title=f"{ticker} Pullback From ATH")
        return fig

    fig.add_trace(
        go.Scatter(
            x=frame["timestamp"],
            y=frame["pullback_from_ath_pct"],
            mode="lines",
            line={"color": "#38bdf8", "width": 2},
            fill="tozeroy",
            fillcolor="rgba(56, 189, 248, 0.18)",
            name="Pullback %",
        )
    )
    fig.add_hline(y=0, line_dash="dash", line_color="#94a3b8")
    fig.update_layout(
        template="plotly_dark",
        title=f"{ticker} Pullback From ATH",
        xaxis_title="Date",
        yaxis_title="Pullback %",
        hovermode="x unified",
    )
    return fig


def build_forecast_cone_figure(
    history_frame: pd.DataFrame,
    forecast: dict[str, object],
    ticker: str,
    history_points: int = 60,
) -> go.Figure:
    fig = go.Figure()
    if history_frame.empty or not forecast:
        fig.update_layout(template="plotly_dark", title=f"{ticker} Next-Week Probability Cone")
        return fig

    history = history_frame.tail(history_points)
    bands = forecast["percentiles"]
    fig.add_trace(
        go.Scatter(
            x=history["timestamp"],
            y=history["close"],
            mode="lines",
            line={"color": "#f8fafc", "width": 2.5},
            name="Close",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=bands["timestamp"],
            y=bands["p10"],
            mode="lines",
            line={"color": "rgba(248, 250, 252, 0)"},
            showlegend=False,
            hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=bands["timestamp"],
            y=bands["p90"],
            mode="lines",
            fill="tonexty",
            fillcolor="rgba(148, 163, 184, 0.15)",
            line={"color": "rgba(148, 163, 184, 0)"},
            name="10-90%",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=bands["timestamp"],
            y=bands["p25"],
            mode="lines",
            line={"color": "rgba(45, 212, 191, 0)"},
            showlegend=False,
            hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=bands["timestamp"],
            y=bands["p75"],
            mode="lines",
            fill="tonexty",
            fillcolor="rgba(45, 212, 191, 0.2)",
            line={"color": "rgba(45, 212, 191, 0)"},
            name="25-75%",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=bands["timestamp"],
            y=bands["p50"],
            mode="lines+markers",
            line={"color": "#2dd4bf", "width": 2},
            name="Median forecast",
        )
    )
    fig.add_vline(x=history["timestamp"].iloc[-1], line_dash="dash", line_color="#94a3b8")
    fig.update_layout(
        template="plotly_dark",
        title=f"{ticker} Next-Week Probability Cone",
        xaxis_title="Date",
        yaxis_title="Price",
        hovermode="x unified",
    )
    return fig


def build_terminal_distribution_figure(forecast: dict[str, object], ticker: str) -> go.Figure:
    fig = go.Figure()
    if not forecast:
        fig.update_layout(template="plotly_dark", title=f"{ticker} 5-Day Terminal Price Distribution")
        return fig

    terminal_prices = forecast["simulated_prices"][:, -1]
    fig.add_trace(
        go.Histogram(
            x=terminal_prices,
            nbinsx=35,
            marker={"color": "rgba(56, 189, 248, 0.75)"},
            name="Terminal prices",
        )
    )
    fig.add_vline(x=float(forecast["current_price"]), line_dash="dash", line_color="#f8fafc", annotation_text="Current")
    if np.isfinite(forecast["support"]):
        fig.add_vline(x=float(forecast["support"]), line_dash="dot", line_color="#2dd4bf", annotation_text="Support")
    if np.isfinite(forecast["resistance"]):
        fig.add_vline(x=float(forecast["resistance"]), line_dash="dot", line_color="#f97316", annotation_text="Resistance")
    fig.update_layout(
        template="plotly_dark",
        title=f"{ticker} 5-Day Terminal Price Distribution",
        xaxis_title="Price",
        yaxis_title="Frequency",
        bargap=0.05,
    )
    return fig
