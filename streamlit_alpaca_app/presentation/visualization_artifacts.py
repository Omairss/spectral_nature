from __future__ import annotations

import hashlib
import json
from typing import Any

from data_access.contracts import (
    ChartModel,
    DataProvenance,
    DatasetArtifact,
    DatasetColumn,
    VisualizationArtifact,
    to_jsonable,
)
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
