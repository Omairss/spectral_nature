from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pandas as pd

from data_access.contracts import QueryRequest, QueryResponse, QueryValidationError, ResolvedPayload, frame_to_records
from data_access.layer import DataAccessLayer
from data_access.query_registry import CHART_CAPABILITIES, CHART_SPECS, DATASET_CAPABILITIES, DATASET_SPECS, ParamSpec


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


def _coerce_param_value(param: ParamSpec, value: Any, *, target_label: str) -> Any:
    if value is None:
        if param.nullable:
            return None
        raise QueryValidationError(f"Invalid param `{param.name}` for {target_label}: null is not allowed.")
    if param.json_type == "string":
        if not isinstance(value, str):
            raise QueryValidationError(f"Invalid param `{param.name}` for {target_label}: expected string.")
        clean = value.strip()
        if param.required and not clean:
            raise QueryValidationError(f"Invalid param `{param.name}` for {target_label}: value cannot be empty.")
        if param.enum and clean and clean not in set(param.enum):
            raise QueryValidationError(
                f"Invalid param `{param.name}` for {target_label}: expected one of {', '.join(param.enum)}."
            )
        return clean if clean or param.required else None
    if param.json_type == "integer":
        if isinstance(value, bool):
            raise QueryValidationError(f"Invalid param `{param.name}` for {target_label}: expected integer.")
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise QueryValidationError(f"Invalid param `{param.name}` for {target_label}: expected integer.")
    if param.json_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QueryValidationError(f"Invalid param `{param.name}` for {target_label}: expected number.")
        return float(value)
    if param.json_type == "boolean":
        if not isinstance(value, bool):
            raise QueryValidationError(f"Invalid param `{param.name}` for {target_label}: expected boolean.")
        return value
    if param.json_type == "array":
        if not isinstance(value, (list, tuple)):
            raise QueryValidationError(f"Invalid param `{param.name}` for {target_label}: expected array.")
        items = list(value)
        if param.items_type == "string":
            if any(not isinstance(item, str) for item in items):
                raise QueryValidationError(
                    f"Invalid param `{param.name}` for {target_label}: expected array of strings."
                )
            normalized = [item.strip() for item in items if item.strip()]
            return normalized
        return items
    raise QueryValidationError(f"Unsupported param type `{param.json_type}` for {target_label}.")


def _normalize_params(raw_params: dict[str, Any] | None, *, param_specs: tuple[ParamSpec, ...], target_label: str) -> dict[str, Any]:
    params = dict(raw_params or {})
    specs_by_name = {item.name: item for item in param_specs}
    unknown = sorted(key for key in params.keys() if key not in specs_by_name)
    if unknown:
        allowed = ", ".join(sorted(specs_by_name)) or "none"
        raise QueryValidationError(
            f"Unsupported params for {target_label}: {', '.join(unknown)}. Allowed params: {allowed}."
        )
    normalized: dict[str, Any] = {}
    missing = [item.name for item in param_specs if item.required and item.name not in params]
    if missing:
        raise QueryValidationError(f"Missing required param(s) for {target_label}: {', '.join(missing)}.")
    for item in param_specs:
        if item.name not in params:
            continue
        normalized[item.name] = _coerce_param_value(item, params[item.name], target_label=target_label)
    return normalized


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
        normalized = _normalize_params(params, param_specs=spec.params, target_label=f"dataset '{key}'")
        return spec.handler(self.data_access, normalized)

    def build_chart(self, name: str, params: dict[str, Any] | None = None) -> ResolvedPayload:
        key = str(name or "").strip().lower()
        spec = CHART_SPECS.get(key)
        if spec is None:
            raise ValueError(f"Unsupported chart '{name}'.")
        normalized = _normalize_params(params, param_specs=spec.params, target_label=f"chart '{key}'")
        return spec.handler(self.data_access, normalized)

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
