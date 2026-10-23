from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np
import pandas as pd

from data_access.contracts import ChartModel, ChartTraceModel, DataProvenance, DatasetArtifact, DatasetColumn, VisualizationArtifact
from services.json_utils import to_jsonable
from presentation.plotly import render_chart_model


def _canonical_json(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _content_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _logical_type(values: list[Any]) -> str:
    types: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            types.add("boolean")
        elif isinstance(value, (int, float)):
            types.add("number")
        elif isinstance(value, str):
            types.add("string")
        elif isinstance(value, list):
            types.add("array")
        elif isinstance(value, dict):
            types.add("object")
        else:
            types.add("unknown")
    if not types:
        return "unknown"
    if len(types) == 1:
        return next(iter(types))
    return "mixed"


def _dataset_artifact(name: str, records: list[dict[str, Any]]) -> DatasetArtifact:
    normalized_records = [dict(record) for record in to_jsonable(records)]
    column_names: list[str] = []
    for record in normalized_records:
        for key in record:
            if key not in column_names:
                column_names.append(key)
    columns = tuple(
        DatasetColumn(
            name=column_name,
            logical_type=_logical_type([record.get(column_name) for record in normalized_records]),
        )
        for column_name in column_names
    )
    identity = {"schema_version": "dataset.v1", "name": name, "records": normalized_records}
    return DatasetArtifact(
        dataset_id=_content_id("dataset", identity),
        name=name,
        delivery="inline",
        row_count=len(normalized_records),
        columns=columns,
        records=normalized_records,
    )


def build_plotly_visualization_artifact(
    model: ChartModel,
    *,
    provenance: DataProvenance,
) -> VisualizationArtifact:
    figure = to_jsonable(render_chart_model(model).to_plotly_json())
    datasets = {
        name: _dataset_artifact(name, records)
        for name, records in model.datasets.items()
    }
    spec = {
        "chart": {"traces": [trace.to_dict() for trace in model.traces],
                  "layout": {axis: figure.get("layout", {}).get(axis, {}) for axis in ("xaxis", "yaxis")},
                  "metadata": to_jsonable(model.metadata)},
        "figure": {
            "data": list(figure.get("data") or []),
            "layout": dict(figure.get("layout") or {}),
        },
        "config": {
            "displaylogo": False,
            "responsive": True,
            "scrollZoom": False,
        },
    }
    provenance_payload = provenance.to_dict()
    identity = {
        "schema_version": "visualization.v1",
        "renderer": "plotly",
        "renderer_contract": "plotly.figure.v1",
        "chart_id": model.chart_id,
        "datasets": {name: dataset.dataset_id for name, dataset in datasets.items()},
        "spec": spec,
        "provenance": provenance_payload,
    }
    linked_selection_group = str(model.metadata.get("ticker") or model.chart_id).strip().lower()
    encoded_payload = {
        **identity,
        "datasets": {name: dataset.to_dict() for name, dataset in datasets.items()},
        "linked_selection_group": linked_selection_group,
    }
    encoded_bytes = len(_canonical_json(encoded_payload).encode("utf-8"))
    return VisualizationArtifact(
        artifact_id=_content_id("viz", identity),
        title=model.title,
        renderer="plotly",
        renderer_contract="plotly.figure.v1",
        datasets=datasets,
        spec=spec,
        linked_selection_group=linked_selection_group,
        provenance=provenance_payload,
        performance={
            "row_count": sum(dataset.row_count for dataset in datasets.values()),
            "encoded_bytes": encoded_bytes,
            "delivery": "inline",
            "level_of_detail": "full",
        },
    )


def analysis_chart_model(name: str, *, kind: str = "line", x: Any = None, y: Any = None,
                         series_name: str = "", series: Any = None, z: Any = None, bins: int = 30,
                         x_label: str = "", y_label: str = "", x_type: str = "auto",
                         x_format: str = "number", y_format: str = "number") -> ChartModel:
    """Validate analytical chart data before either renderer receives it.

    Data is retained in full up to an explicit 20,000 displayed-point budget;
    this does not constrain the data used for computation. No executable or
    remotely loaded specification can enter through this interface.
    """
    def label(value: Any) -> str:
        if not isinstance(value, str) or len(value) > 240:
            raise ValueError("Chart labels must be strings of at most240 characters.")
        return value

    def values(value: Any) -> list:
        result = to_jsonable(value)
        if not isinstance(result, list) or not result or len(result) > 20_000:
            raise ValueError("Chart arrays require1to20000 values; aggregate or select display data explicitly.")
        return result

    def numbers(value: Any, size: int) -> list:
        result = values(value)
        if len(result) != size or any(v is not None and
            (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)) for v in result):
            raise ValueError("Chart numeric arrays must match x, with finite numbers or null gaps.")
        return result

    kind = label(kind)
    if kind not in {"line", "scatter", "bar", "histogram", "heatmap", "band"}:
        raise ValueError("Chart kind must be line, scatter, bar, histogram, heatmap or band.")
    xs = values(x)
    if any(isinstance(v, (bool, dict, list)) or v is None for v in xs):
        raise ValueError("Chart x values must be finite numbers, dates or category strings.")
    if x_format not in {"number", "percent"} or y_format not in {"number", "percent"}:
        raise ValueError("Chart axis formats must be number or percent; percentages use fractions.")
    if x_type == "auto":
        x_type = "number" if all(isinstance(v, (int, float)) for v in xs) else "category"
        if x_type == "category" and all(isinstance(v, str) and len(v) >= 10 and v[4:5] == "-" and v[7:8] == "-" for v in xs):
            x_type = "date"
    if x_type not in {"number", "date", "category"}:
        raise ValueError("x_type must be auto, number, date or category.")
    if x_type == "date":
        dates = pd.to_datetime(xs, utc=True, errors="raise")
        if dates.isna().any():
            raise ValueError("Chart dates must be valid.")
        xs = [v.isoformat() for v in dates]
    elif x_type == "number":
        xs = numbers(xs, len(xs))
    elif any(not isinstance(v, str) or len(v) > 240 for v in xs):
        raise ValueError("Category labels must be strings of at most240 characters.")

    datasets, traces = {}, []
    metadata = {"x_type": x_type, "x_format": x_format, "y_format": y_format}
    if kind == "histogram":
        if x_type != "number" or isinstance(bins, bool) or not isinstance(bins, int) or not 1 <= bins <= 200:
            raise ValueError("Histograms require numeric samples and1to200 bins.")
        counts, edges = np.histogram(xs, bins=bins)
        datasets["primary"] = [{"x": float((a+b)/2), "y": int(n), "left": float(a), "right": float(b)}
                               for n, a, b in zip(counts, edges[:-1], edges[1:])]
        traces.append(ChartTraceModel("bar", label(series_name) or "Count", x="x", y="y",
                                     style={"width": np.diff(edges).tolist()}, options={"left": "left", "right": "right"}))
        metadata.update(histogram=True, sample_count=len(xs))
    elif kind == "heatmap":
        ys, matrix = values(y), values(z)
        xs, ys = [label(v) for v in xs], [label(v) for v in ys]
        if (len(ys) * len(xs) > 20_000 or len(matrix) != len(ys) or
            len(set(xs)) != len(xs) or len(set(ys)) != len(ys)):
            raise ValueError("Heatmap axes must be unique and match a matrix of at most20000 cells.")
        datasets["primary"] = [{"x": xx, "y": yy, "z": value} for yy, row in zip(ys, matrix)
                               for xx, value in zip(xs, numbers(row, len(xs)))]
        style = {"colorbar": {"tickformat": ".1%"}, "hovertemplate": "%{x}, %{y}: %{z:.1%}<extra></extra>"} if y_format == "percent" else {}
        traces.append(ChartTraceModel("heatmap", label(series_name) or label(name), x="x", y="y", z="z", style=style))
    else:
        supplied = to_jsonable(series) if series is not None else [{"name": series_name or name, "y": y}]
        if not isinstance(supplied, list) or not 1 <= len(supplied) <= 16 or len(supplied) * len(xs) > 20_000:
            raise ValueError("Charts require1to16 series and at most20000 displayed points in total.")
        for i, item in enumerate(supplied):
            if not isinstance(item, dict) or set(item) - {"name", "kind", "y", "lower", "upper"}:
                raise ValueError("A series accepts name, kind, y, lower and upper only.")
            trace_kind = item.get("kind", kind)
            if trace_kind not in {"line", "scatter", "bar", "band"}:
                raise ValueError("Series kind must be line, scatter, bar or band.")
            data = [{"x": value} for value in xs]
            if trace_kind == "band":
                lo, hi = numbers(item.get("lower"), len(xs)), numbers(item.get("upper"), len(xs))
                if any((a is None) != (b is None) or (a is not None and a > b) for a, b in zip(lo, hi)):
                    raise ValueError("Band lower/upper values must be ordered and share missing observations.")
                for row, a, b in zip(data, lo, hi):
                    row.update(lower=a, upper=b)
            else:
                for row, value in zip(data, numbers(item.get("y"), len(xs))):
                    row["y"] = value
            key = f"series_{i}"
            datasets[key] = data
            traces.append(ChartTraceModel(trace_kind, label(item.get("name", "")) or f"Series{i+1}",
                dataset=key, x="x", y="y", lower="lower" if trace_kind == "band" else None,
                upper="upper" if trace_kind == "band" else None))
    layout = {"xaxis": {"title": {"text": label(x_label)}, "type": "linear" if x_type == "number" else x_type},
              "yaxis": {"title": {"text": label(y_label)}}, "height": 360,
              "colorway": ["#52d6c7", "#6cb8ff", "#a991f7", "#f08b72"]}
    for axis, fmt in [("xaxis", x_format), ("yaxis", y_format)]:
        if fmt == "percent":
            layout[axis]["tickformat"] = ".1%"
    model = ChartModel(chart_id="analysis_chart", title=label(name), datasets=datasets, traces=traces,
                       layout=layout, metadata=metadata)
    # Verify the shared renderer too: bad chart input fails the calculation
    # step rather than publishing an apparently successful broken artifact.
    render_chart_model(model)
    return model


def collect_research_visualizations(tool_calls: list[dict], *, run_id: str) -> list[dict]:
    """Publish executed artifacts, never chart text invented during synthesis."""
    output, seen = [], set()
    for call in tool_calls:
        if call.get("status") != "completed" or (call.get("origin_run_id") and call["origin_run_id"] != run_id):
            continue
        rendered = (call.get("result_summary") or {}).get("render_payload") or {}
        analysis = rendered.get("analysis") or {}
        candidates = analysis.get("charts", []) if analysis.get("status") == "succeeded" else []
        if rendered.get("kind") == "chart_model":
            raw = rendered["chart_model"]
            model = ChartModel(chart_id=raw["chart_id"], title=raw["title"], datasets=raw["datasets"],
                traces=[ChartTraceModel(**trace) for trace in raw["traces"]], layout=raw.get("layout", {}), metadata=raw.get("metadata", {}))
            candidates = [build_plotly_visualization_artifact(model,
                provenance=DataProvenance(mode="computed", datasets=tuple(raw["datasets"]),
                    details={"research_run_id": run_id, "tool_call_id": call.get("tool_call_id")})).to_dict()]
        for artifact in candidates:
            if (isinstance(artifact, dict) and artifact.get("schema_version") == "visualization.v1" and
                artifact.get("artifact_type") == "visualization" and artifact.get("artifact_id") not in seen):
                seen.add(artifact["artifact_id"])
                output.append(artifact)
    return output


def build_plotly_figure_visualization_artifact(
    figure: dict[str, Any],
    *,
    title: str,
    provenance: DataProvenance,
    linked_selection_group: str,
) -> VisualizationArtifact:
    """Wrap a trusted materialized Plotly figure in the shared artifact contract."""
    normalized = to_jsonable(figure)
    if not isinstance(normalized, dict):
        raise ValueError("Plotly figure must be an object.")
    data = normalized.get("data")
    layout = normalized.get("layout")
    if not isinstance(data, list) or not isinstance(layout, dict):
        raise ValueError("Plotly figure must include data and layout.")
    spec = {
        "figure": {"data": data, "layout": layout},
        "config": {
            "displayModeBar": False,
            "displaylogo": False,
            "responsive": True,
            "scrollZoom": False,
        },
    }
    provenance_payload = provenance.to_dict()
    identity = {
        "schema_version": "visualization.v1",
        "renderer": "plotly",
        "renderer_contract": "plotly.figure.v1",
        "title": str(title).strip(),
        "spec": spec,
        "provenance": provenance_payload,
    }
    point_count = 0
    for trace in data:
        if not isinstance(trace, dict):
            continue
        point_count += max(
            len(trace.get("x") or []) if isinstance(trace.get("x"), list) else 0,
            len(trace.get("y") or []) if isinstance(trace.get("y"), list) else 0,
        )
    return VisualizationArtifact(
        artifact_id=_content_id("viz", identity),
        title=str(title).strip(),
        renderer="plotly",
        renderer_contract="plotly.figure.v1",
        datasets={},
        spec=spec,
        linked_selection_group=str(linked_selection_group).strip(),
        provenance=provenance_payload,
        performance={
            "row_count": point_count,
            "encoded_bytes": len(_canonical_json(identity).encode("utf-8")),
            "delivery": "inline",
            "level_of_detail": "curated",
        },
    )


__all__ = ["build_plotly_figure_visualization_artifact", "build_plotly_visualization_artifact"]
