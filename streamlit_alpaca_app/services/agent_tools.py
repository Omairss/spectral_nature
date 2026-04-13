from __future__ import annotations

from typing import Any

from data_access.contracts import QueryRequest, coerce_object
from data_access.query_service import QueryService
from . import omnibar_research


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


def _research_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "research.retained_context",
            "description": (
                "Look up retained narrative context already in Spectral Nature for the query. "
                "Best first tool for live analysis prompts."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "focus_symbols": {"type": "array", "items": {"type": "string"}},
                    "max_items": {"type": "integer"},
                    "force_refresh": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.market_impact_map",
            "description": (
                "Expand a live event or macro query into likely impacted assets and spillover symbols."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_symbols": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.live_event_evidence",
            "description": (
                "Fetch fresh web evidence for the query, using event-level search and symbol-level search when relevant."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "focus_symbols": {"type": "array", "items": {"type": "string"}},
                    "max_results": {"type": "integer"},
                    "force_refresh": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.open_page",
            "description": (
                "Open one selected web page with Playwright when available, with a simple HTTP fallback."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    ]


def is_query_service_tool(tool_name: str) -> bool:
    name = str(tool_name or "").strip()
    return name == "system.capabilities" or name.startswith("dataset.") or name.startswith("chart.")


def is_research_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().startswith("research.")


def build_tool_catalog(service: QueryService) -> list[dict[str, Any]]:
    capabilities = service.list_capabilities()
    tools: list[dict[str, Any]] = [
        {
            "name": "system.capabilities",
            "description": "Return dataset and chart capability metadata.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]
    tools.extend(_research_tools())
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


def _resolve_research_layer(service: QueryService) -> Any:
    data_access = getattr(service, "data_access", None)
    if data_access is not None and hasattr(data_access, "resolve_attention_home_1d"):
        return data_access
    return None


def _invoke_research_tool(
    *,
    service: QueryService,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = coerce_object(arguments, field_name="arguments")
    layer = _resolve_research_layer(service)
    if tool_name == "research.retained_context":
        payload = omnibar_research.retained_context(
            query=str(args.get("query") or ""),
            focus_symbols=args.get("focus_symbols"),
            max_items=int(args.get("max_items") or 5),
            force_refresh=bool(args.get("force_refresh")),
            layer=layer,
        )
        datasets = ("attention_home_1d", "attention_research_bundle", "attention_ticker_background")
    elif tool_name == "research.market_impact_map":
        payload = omnibar_research.market_impact_map(
            query=str(args.get("query") or ""),
            max_symbols=int(args.get("max_symbols") or 8),
        )
        datasets = ("attention_market_events", "commodity_proxy_profile")
    elif tool_name == "research.live_event_evidence":
        payload = omnibar_research.live_event_evidence(
            query=str(args.get("query") or ""),
            focus_symbols=args.get("focus_symbols"),
            max_results=int(args.get("max_results") or 6),
            force_refresh=bool(args.get("force_refresh")),
            layer=layer,
        )
        datasets = ("web_research", "attention_search_results")
    elif tool_name == "research.open_page":
        payload = omnibar_research.open_page(
            url=str(args.get("url") or ""),
            max_chars=int(args.get("max_chars") or 5000),
        )
        datasets = ("page_browsing",)
    else:
        raise ValueError(f"Unsupported tool '{tool_name}'.")

    return {
        "request": {"operation": "research", "name": tool_name, "params": args},
        "result_type": "research",
        "payload": payload,
        "provenance": {
            "mode": "computed",
            "datasets": list(datasets),
            "details": {"tool_name": tool_name},
        },
        "messages": [],
    }


def invoke_tool(
    *,
    service: QueryService,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if is_research_tool(tool_name):
        return _invoke_research_tool(service=service, tool_name=tool_name, arguments=arguments)
    request = build_query_request_for_tool(tool_name=tool_name, arguments=arguments)
    return service.execute(request).to_dict()


__all__ = [
    "build_tool_catalog",
    "build_query_request_for_tool",
    "is_query_service_tool",
    "is_research_tool",
    "invoke_tool",
    "tool_schema",
]
