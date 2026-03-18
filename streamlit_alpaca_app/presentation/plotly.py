from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go

from data_access.contracts import ChartModel, ChartTraceModel


def _dataset_frame(model: ChartModel, dataset_name: str) -> pd.DataFrame:
    return pd.DataFrame(model.datasets.get(dataset_name, []))


def _apply_filter(frame: pd.DataFrame, trace: ChartTraceModel) -> pd.DataFrame:
    out = frame.copy()
    for key, expected in trace.where.items():
        if key not in out.columns:
            continue
        if isinstance(expected, (list, tuple, set)):
            out = out[out[key].isin(list(expected))].copy()
        else:
            out = out[out[key] == expected].copy()
    return out


def _trace_series(frame: pd.DataFrame, field: str | None) -> pd.Series:
    if field is None or field not in frame.columns:
        return pd.Series(dtype=object)
    return frame[field]


def render_chart_model(model: ChartModel) -> go.Figure:
    fig = go.Figure()

    for trace in model.traces:
        frame = _apply_filter(_dataset_frame(model, trace.dataset), trace)
        style: dict[str, Any] = dict(trace.style)
        options: dict[str, Any] = dict(trace.options)
        trace_type = trace.trace_type.lower()

        if trace_type == "line":
            fig.add_trace(
                go.Scatter(
                    x=_trace_series(frame, trace.x),
                    y=_trace_series(frame, trace.y),
                    name=trace.name,
                    mode=style.pop("mode", "lines"),
                    **style,
                )
            )
            continue

        if trace_type == "bar":
            fig.add_trace(
                go.Bar(
                    x=_trace_series(frame, trace.x),
                    y=_trace_series(frame, trace.y),
                    name=trace.name,
                    **style,
                )
            )
            continue

        if trace_type == "histogram":
            fig.add_trace(
                go.Histogram(
                    x=_trace_series(frame, trace.x),
                    y=_trace_series(frame, trace.y) if trace.y else None,
                    name=trace.name,
                    **style,
                )
            )
            continue

        if trace_type == "candlestick":
            fig.add_trace(
                go.Candlestick(
                    x=_trace_series(frame, trace.x),
                    open=_trace_series(frame, trace.open),
                    high=_trace_series(frame, trace.high),
                    low=_trace_series(frame, trace.low),
                    close=_trace_series(frame, trace.close),
                    name=trace.name,
                    **style,
                )
            )
            continue

        raise ValueError(f"Unsupported trace type '{trace.trace_type}'.")

    layout = dict(model.layout)
    if model.title and "title" not in layout:
        layout["title"] = model.title
    if layout:
        fig.update_layout(**layout)
    if options := model.metadata.get("layout_shapes"):
        fig.update_layout(shapes=options)
    return fig
