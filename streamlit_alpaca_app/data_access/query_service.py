from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pandas as pd

from data_access.contracts import QueryRequest, QueryResponse, ResolvedPayload, frame_to_records
from data_access.layer import DataAccessLayer
from data_access.query_registry import CHART_CAPABILITIES, CHART_SPECS, DATASET_CAPABILITIES, DATASET_SPECS


def _serialize_payload(payload: Any) -> Any:
    if isinstance(payload, pd.DataFrame):
        return frame_to_records(payload)
    if isinstance(payload, dict):
        return {
            key: frame_to_records(value) if isinstance(value, pd.DataFrame) else value
            for key, value in payload.items()
        }
    if isinstance(payload, tuple):
        return [
            frame_to_records(value) if isinstance(value, pd.DataFrame) else value
            for value in payload
        ]
    return payload


@dataclass(frozen=True)
class QueryService:
    data_access: DataAccessLayer

    @classmethod
    def from_environment(cls) -> "QueryService":
        return cls(data_access=DataAccessLayer.from_environment())

    def list_capabilities(self) -> dict[str, Any]:
        return {
            "datasets": deepcopy(DATASET_CAPABILITIES),
            "charts": deepcopy(CHART_CAPABILITIES),
        }

    def fetch_dataset(self, name: str, params: dict[str, Any] | None = None) -> ResolvedPayload:
        key = str(name or "").strip().lower()
        spec = DATASET_SPECS.get(key)
        if spec is None:
            raise ValueError(f"Unsupported dataset '{name}'.")
        return spec.handler(self.data_access, dict(params or {}))

    def build_chart(self, name: str, params: dict[str, Any] | None = None) -> ResolvedPayload:
        key = str(name or "").strip().lower()
        spec = CHART_SPECS.get(key)
        if spec is None:
            raise ValueError(f"Unsupported chart '{name}'.")
        return spec.handler(self.data_access, dict(params or {}))

    def execute(self, request: QueryRequest | dict[str, Any]) -> QueryResponse:
        query = request if isinstance(request, QueryRequest) else QueryRequest.from_dict(request)

        if query.operation == "capabilities":
            return QueryResponse(request=query, result_type="capabilities", payload=self.list_capabilities())
        if query.operation == "dataset":
            resolved = self.fetch_dataset(query.name, query.params)
            return QueryResponse(
                request=query,
                result_type="dataset",
                payload=_serialize_payload(resolved.payload),
                provenance=resolved.provenance,
            )
        if query.operation == "chart":
            resolved = self.build_chart(query.name, query.params)
            return QueryResponse(
                request=query,
                result_type="chart_model",
                payload=resolved.payload,
                provenance=resolved.provenance,
            )

        raise ValueError(f"Unsupported operation '{query.operation}'.")
