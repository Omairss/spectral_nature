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

    for trace_index, trace in enumerate(model.traces):
        frame = _apply_filter(_dataset_frame(model, trace.dataset), trace)
        style: dict[str, Any] = dict(trace.style)
        options: dict[str, Any] = dict(trace.options)
        trace_type = trace.trace_type.lower()

        if trace_type in {"line", "scatter"}:
            fig.add_trace(
                go.Scatter(
                    x=_trace_series(frame, trace.x),
                    y=_trace_series(frame, trace.y),
                    name=trace.name,
                    mode=style.pop("mode", "markers" if trace_type == "scatter" else "lines"),
                    **style,
                )
            )
            continue

        if trace_type == "band":
            # Independent closed polygons preserve missing intervals: a gap
            # must not become a filled region joining unrelated observations.
            valid = frame[[trace.lower, trace.upper]].notna().all(axis=1)
            segments = valid.ne(valid.shift()).cumsum()
            colors = model.layout.get("colorway") or ["#52d6c7", "#6cb8ff", "#a991f7", "#f08b72"]
            fillcolor = style.pop("fillcolor", colors[trace_index % len(colors)])
            first = True
            for _, segment in frame[valid].groupby(segments[valid], sort=False):
                x = _trace_series(segment, trace.x).tolist()
                lo = _trace_series(segment, trace.lower).tolist()
                hi = _trace_series(segment, trace.upper).tolist()
                fig.add_trace(go.Scatter(x=x + x[::-1], y=hi + lo[::-1],
                    name=trace.name, mode="lines", fill="toself", opacity=.22,
                    line={"width": 0}, fillcolor=fillcolor, legendgroup=trace.name,
                    showlegend=first, **style))
                first = False
            continue

        if trace_type == "heatmap":
            style.setdefault("zsmooth", False)
            style.setdefault("xgap", 1)
            style.setdefault("ygap", 1)
            xs = list(dict.fromkeys(frame[trace.x]))
            ys = list(dict.fromkeys(frame[trace.y]))
            cells = {(row[trace.x], row[trace.y]): row.get(trace.z) for row in frame.to_dict("records")}
            fig.add_trace(go.Heatmap(x=xs, y=ys, z=[[cells.get((x, y)) for x in xs] for y in ys],
                name=trace.name, **style))
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
