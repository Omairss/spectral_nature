from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.json_utils import to_jsonable


class QueryValidationError(ValueError):
    pass


def coerce_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise QueryValidationError(f"{field_name} must be an object.")
    return dict(value)


@dataclass(frozen=True)
class DataProvenance:
    mode: str
    datasets: tuple[str, ...]
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "datasets": list(self.datasets),
            "details": to_jsonable(self.details),
        }


@dataclass(frozen=True)
class ResolvedPayload:
    payload: Any
    provenance: DataProvenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload": to_jsonable(self.payload),
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class ChartTraceModel:
    trace_type: str
    name: str
    dataset: str = "primary"
    x: str | None = None
    y: str | None = None
    open: str | None = None
    high: str | None = None
    low: str | None = None
    close: str | None = None
    where: dict[str, Any] = field(default_factory=dict)
    style: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    lower: str | None = None
    upper: str | None = None
    z: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True)
class ChartModel:
    chart_id: str
    title: str
    datasets: dict[str, list[dict[str, Any]]]
    traces: list[ChartTraceModel]
    layout: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_id": self.chart_id,
            "title": self.title,
            "datasets": to_jsonable(self.datasets),
            "traces": [trace.to_dict() for trace in self.traces],
            "layout": to_jsonable(self.layout),
            "metadata": to_jsonable(self.metadata),
        }


@dataclass(frozen=True)
class DatasetColumn:
    name: str
    logical_type: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "logical_type": self.logical_type}


@dataclass(frozen=True)
class DatasetArtifact:
    dataset_id: str
    name: str
    delivery: str
    row_count: int
    columns: tuple[DatasetColumn, ...]
    records: list[dict[str, Any]]
    schema_version: str = "dataset.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "name": self.name,
            "delivery": self.delivery,
            "row_count": self.row_count,
            "columns": [column.to_dict() for column in self.columns],
            "records": to_jsonable(self.records),
        }


@dataclass(frozen=True)
class VisualizationArtifact:
    artifact_id: str
    title: str
    renderer: str
    renderer_contract: str
    datasets: dict[str, DatasetArtifact]
    spec: dict[str, Any]
    linked_selection_group: str
    provenance: dict[str, Any]
    performance: dict[str, Any]
    schema_version: str = "visualization.v1"
    artifact_type: str = "visualization"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_type": self.artifact_type,
            "artifact_id": self.artifact_id,
            "title": self.title,
            "renderer": self.renderer,
            "renderer_contract": self.renderer_contract,
            "dataset_refs": [dataset.dataset_id for dataset in self.datasets.values()],
            "datasets": {name: dataset.to_dict() for name, dataset in self.datasets.items()},
            "spec": to_jsonable(self.spec),
            "linked_selection_group": self.linked_selection_group,
            "provenance": to_jsonable(self.provenance),
            "performance": to_jsonable(self.performance),
        }


@dataclass(frozen=True)
class QueryRequest:
    operation: str
    name: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QueryRequest":
        return cls(
            operation=str(payload.get("operation") or "").strip().lower(),
            name=str(payload.get("name") or "").strip(),
            params=coerce_object(payload.get("params"), field_name="params"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "name": self.name,
            "params": to_jsonable(self.params),
        }


@dataclass(frozen=True)
class QueryResponse:
    request: QueryRequest
    result_type: str
    payload: Any
    provenance: DataProvenance | None = None
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        if isinstance(self.payload, (ChartModel, VisualizationArtifact)):
            payload = self.payload.to_dict()
        else:
            payload = to_jsonable(self.payload)
        return {
            "request": self.request.to_dict(),
            "result_type": self.result_type,
            "payload": payload,
            "provenance": self.provenance.to_dict() if self.provenance is not None else None,
            "messages": list(self.messages),
        }
