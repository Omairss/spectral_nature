from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def _jsonable_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not np.isfinite(value):
            return None
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _jsonable_scalar(value.item())
    if pd.isna(value):
        return None
    return value


def to_jsonable(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return frame_to_records(value)
    if isinstance(value, pd.Series):
        return [_jsonable_scalar(item) for item in value.tolist()]
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return _jsonable_scalar(value)


def frame_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        records.append({str(key): to_jsonable(value) for key, value in row.items()})
    return records


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
class QueryRequest:
    operation: str
    name: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QueryRequest":
        return cls(
            operation=str(payload.get("operation") or "").strip().lower(),
            name=str(payload.get("name") or "").strip(),
            params=dict(payload.get("params") or {}),
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
        if isinstance(self.payload, ChartModel):
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
