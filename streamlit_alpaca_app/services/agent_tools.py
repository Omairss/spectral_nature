from __future__ import annotations

from typing import Any

from data_access.contracts import QueryRequest, coerce_object
from data_access.query_service import QueryService


def tool_schema(
    params: list[str] | tuple[str, ...],
    *,
    required: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            name: {"type": ["string", "number", "boolean", "object", "array", "null"]}
            for name in list(params or [])
        },
        "required": list(required or []),
        "additionalProperties": False,
    }


def _schema_from_capability(spec: dict[str, Any]) -> dict[str, Any]:
    schema = spec.get("param_schema")
    if isinstance(schema, dict):
        return dict(schema)
    return tool_schema(
        list(spec.get("params") or []),
        required=list(spec.get("required_params") or []),
    )


def build_tool_catalog(service: QueryService) -> list[dict[str, Any]]:
    capabilities = service.list_capabilities()
    tools: list[dict[str, Any]] = [
        {
            "name": "system.capabilities",
            "description": "Return dataset and chart capability metadata.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]
    for dataset_name, spec in dict(capabilities.get("datasets") or {}).items():
        tools.append(
            {
                "name": f"dataset.{dataset_name}",
                "description": f"Fetch dataset '{dataset_name}'.",
                "inputSchema": _schema_from_capability(dict(spec or {})),
                "resolution": spec.get("resolution"),
            }
        )
    for chart_name, spec in dict(capabilities.get("charts") or {}).items():
        tools.append(
            {
                "name": f"chart.{chart_name}",
                "description": f"Build chart model '{chart_name}'.",
                "inputSchema": _schema_from_capability(dict(spec or {})),
                "resolution": spec.get("resolution"),
            }
        )
    return tools


def build_query_request_for_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> QueryRequest:
    name = str(tool_name or "").strip()
    args = coerce_object(arguments, field_name="arguments")
    if name == "system.capabilities":
        return QueryRequest(operation="capabilities", name="", params={})
    if name == "query.execute":
        payload = {
            "operation": str(args.get("operation") or "").strip().lower(),
            "name": str(args.get("name") or ""),
            "params": coerce_object(args.get("params"), field_name="params"),
        }
        return QueryRequest.from_dict(payload)
    if name.startswith("dataset."):
        return QueryRequest(operation="dataset", name=name.split(".", 1)[1], params=args)
    if name.startswith("chart."):
        return QueryRequest(operation="chart", name=name.split(".", 1)[1], params=args)
    raise ValueError(f"Unsupported tool '{name}'.")


def invoke_tool(
    *,
    service: QueryService,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = build_query_request_for_tool(tool_name=tool_name, arguments=arguments)
    return service.execute(request).to_dict()


__all__ = [
    "build_tool_catalog",
    "build_query_request_for_tool",
    "invoke_tool",
    "tool_schema",
]
