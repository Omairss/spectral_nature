from __future__ import annotations

from dataclasses import replace
import json
import os
import re
import threading
import time
import uuid
from typing import Any, Callable

from data_access.query_service import QueryService
from .agent_tools import build_tool_catalog, invoke_tool
from .llm import (
    NARRATIVE_STYLE_RULE,
    AzureOpenAIChatJSONClient,
    DeepSeekChatJSONClient,
    LLMConfig,
    LLMAPIError,
    OpenAIChatJSONClient,
    get_config_param,
    get_prompt,
    load_llm_client,
    register_config_param,
    register_narrative_prompt,
)


LLMClient = OpenAIChatJSONClient | AzureOpenAIChatJSONClient | DeepSeekChatJSONClient
ProgressCallback = Callable[[dict[str, Any]], None]
DEFAULT_MAX_TOOL_CALLS = 8

# --- Configurable limits (exposed in Admin > LLM Config > Tuning Parameters) ---
_P_TOOL_RESULT_CONTEXT_LIMIT = register_config_param(
    "Agent tool result context limit",
    group="Chat + Search",
    default=4000,
    description="Max chars for the LLM context text extracted from a single tool result",
)
_P_TOOL_RESULT_SUMMARY_LIMIT = register_config_param(
    "Agent tool result summary limit",
    group="Chat + Search",
    default=3500,
    description="Max chars for the summary-based LLM context when no explicit context text is available",
)
_P_TOOL_RESULT_SUMMARY_ITEMS = register_config_param(
    "Agent tool result summary items",
    group="Chat + Search",
    default=8,
    description="Max summary items to include per tool result",
)
_P_TOOL_HISTORY_PER_TOOL_LIMIT = register_config_param(
    "Agent tool history per-tool limit",
    group="Chat + Search",
    default=3000,
    description="Max chars per tool result in the tool call history shown to the planner",
)
_P_PREFETCH_EVIDENCE_LIMIT = register_config_param(
    "Agent prefetch evidence limit",
    group="Chat + Search",
    default=8,
    description="Max retained evidence chunks to pre-fetch and inject into the planner prompt",
)
_P_PREFETCH_CHUNK_TEXT_LIMIT = register_config_param(
    "Agent prefetch chunk text limit",
    group="Chat + Search",
    default=300,
    description="Max chars per chunk text in the pre-fetched evidence block",
)
_P_MAX_TOOL_CALLS = register_config_param(
    "Agent max tool calls",
    group="Chat + Search",
    default=8,
    description="Maximum number of tool calls the agent can make per query",
)
_P_BOOTSTRAP_TOOL_CALLS = register_config_param(
    "Agent bootstrap tool calls",
    group="Chat + Search",
    default=4,
    description=(
        "Maximum deterministic evidence calls to make before the planner. "
        "These calls seed obvious context; the planner can still add more evidence."
    ),
)
_P_LLM_STEP_TIMEOUT_SECONDS = register_config_param(
    "Agent LLM step timeout seconds",
    group="Chat + Search",
    default=75,
    description="Maximum seconds to wait for one planner or synthesis LLM call before degrading gracefully",
)
_P_TOOL_CALL_TIMEOUT_SECONDS = register_config_param(
    "Agent tool call timeout seconds",
    group="Chat + Search",
    default=45,
    description="Maximum seconds to wait for one agent tool call before degrading to the next step",
)
_P_CONVERSATION_HISTORY_LIMIT = register_config_param(
    "Agent conversation history limit",
    group="Chat + Search",
    default=3000,
    description="Max chars for the compacted conversation history shown to the planner",
)
_P_USER_PREVIEW_LIMIT = register_config_param(
    "Agent user preview limit",
    group="Chat + Search",
    default=200,
    description="Max chars for the user-facing preview of a tool result",
)

_AFFIRMATIVE_FOLLOWUP_REPLIES = {
    "yes",
    "y",
    "yeah",
    "yep",
    "sure",
    "ok",
    "okay",
    "please",
    "do it",
    "go ahead",
    "continue",
}
_NON_TICKER_UPPERCASE_WORDS = {
    "A",
    "AI",
    "API",
    "CEO",
    "CFO",
    "CPI",
    "ETF",
    "ETFS",
    "EPS",
    "EU",
    "FED",
    "FOMC",
    "GDP",
    "IPO",
    "LLM",
    "NAV",
    "OPEC",
    "PMI",
    "QOQ",
    "SEC",
    "US",
    "USA",
    "USD",
    "YOY",
}
_ARGUMENT_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "value_kind": {"type": "string", "enum": ["string", "number", "boolean", "string_list", "null"]},
        "string_value": {"type": ["string", "null"]},
        "number_value": {"type": ["number", "null"]},
        "boolean_value": {"type": ["boolean", "null"]},
        "string_list_value": {"type": ["array", "null"], "items": {"type": "string"}},
    },
    "required": [
        "name",
        "value_kind",
        "string_value",
        "number_value",
        "boolean_value",
        "string_list_value",
    ],
}

_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["tool_call", "final"]},
        "reasoning": {"type": "string"},
        "tool_name": {"type": "string"},
        "tool_arguments": {"type": "array", "items": _ARGUMENT_ENTRY_SCHEMA},
        "answer_markdown": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "needs_more_tools": {"type": "boolean"},
    },
    "required": [
        "action",
        "reasoning",
        "tool_name",
        "tool_arguments",
        "answer_markdown",
        "confidence",
        "needs_more_tools",
    ],
}

_AGENT_FINAL_SYSTEM_PROMPT = register_narrative_prompt(
    name="Omnibar Agent Final Answer (markdown response to user)",
    file="services/omnibar_agent.py",
    group="Chat + Search",
    prompt=(
        f"You are the Spectral Nature omnibar agent. {NARRATIVE_STYLE_RULE} "
        "Write a grounded markdown answer from the collected tool evidence only. "
        "Structure your answer: "
        "(1) Start with a **bold one-sentence verdict or key finding**. "
        "(2) Use ### headings to separate sections when the answer covers multiple themes or tickers. "
        "(3) Use **bold** for tickers and key metrics (e.g. **NVDA** +3.2%). "
        "(4) Use bullet points for lists of data points. "
        "(5) End with a brief takeaway or what to watch next. "
        "When live external evidence was used, lightly reference one or two supporting sources or URLs."
    ),
)

_FINAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answer_markdown": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "used_tool_call_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "answer_markdown",
        "confidence",
        "limitations",
        "used_tool_call_ids",
    ],
}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _truncate(text: object, *, limit: int = 1600) -> str:
    clean = _clean(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _json_dumps(value: Any, *, limit: int = 3200) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        raw = str(value)
    return _truncate(raw, limit=limit)


def _preview_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        sample = payload[:3]
        columns: list[str] = []
        for row in sample:
            if isinstance(row, dict):
                for key in row.keys():
                    key_text = str(key)
                    if key_text not in columns:
                        columns.append(key_text)
        return {
            "kind": "rows",
            "row_count": len(payload),
            "columns": columns[:12],
            "sample": sample,
        }
    if isinstance(payload, dict):
        summary_rows = payload.get("summary")
        if isinstance(summary_rows, list):
            sample = [row for row in summary_rows[:6] if isinstance(row, dict)]
            columns: list[str] = []
            for row in sample:
                for key in row.keys():
                    key_text = str(key)
                    if key_text not in columns:
                        columns.append(key_text)
            return {
                "kind": "summary_rows",
                "row_count": len(summary_rows),
                "columns": columns[:16],
                "sample": sample,
            }
        if "chart_id" in payload and "datasets" in payload:
            datasets = payload.get("datasets") or {}
            dataset_sizes = {
                str(name): len(list(rows or []))
                for name, rows in dict(datasets).items()
                if isinstance(rows, list)
            }
            return {
                "kind": "chart",
                "chart_id": str(payload.get("chart_id") or ""),
                "title": str(payload.get("title") or ""),
                "dataset_sizes": dataset_sizes,
                "trace_count": len(list(payload.get("traces") or [])),
            }
        scalar_items: dict[str, Any] = {}
        nested_keys: list[str] = []
        for key, value in list(payload.items())[:10]:
            if isinstance(value, (str, int, float, bool)) or value is None:
                scalar_items[str(key)] = value
            else:
                nested_keys.append(str(key))
        return {
            "kind": "object",
            "keys": [str(key) for key in list(payload.keys())[:12]],
            "scalars": scalar_items,
            "nested_keys": nested_keys[:8],
        }
    return {"kind": "scalar", "value": payload}


def _build_render_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    payload = result.get("payload")
    result_type = _clean(result.get("result_type")).lower()
    if result_type == "chart_model" and isinstance(payload, dict):
        datasets = payload.get("datasets")
        traces = payload.get("traces")
        if isinstance(datasets, dict) and isinstance(traces, list):
            return {
                "kind": "chart_model",
                "chart_model": payload,
            }

    if result_type != "dataset" or not isinstance(payload, dict):
        return None

    summary_rows = payload.get("summary")
    observations = payload.get("observations")
    if not isinstance(summary_rows, list) or not isinstance(observations, list):
        return None

    primary_row = next(
        (
            row
            for row in summary_rows
            if isinstance(row, dict) and _clean(row.get("series_id")) and row.get("latest_value") is not None
        ),
        None,
    )
    if not isinstance(primary_row, dict):
        return None

    series_id = _clean(primary_row.get("series_id"))
    if not series_id:
        return None

    series_rows = [
        row
        for row in observations
        if isinstance(row, dict) and _clean(row.get("series_id")) == series_id and _clean(row.get("date"))
    ]
    if not series_rows:
        return None

    series_rows = sorted(series_rows, key=lambda row: _clean(row.get("date")))[-36:]
    x_values: list[str] = []
    y_values: list[float] = []
    for row in series_rows:
        try:
            value = float(row.get("value"))
        except Exception:
            continue
        date_text = _clean(row.get("date"))
        if not date_text:
            continue
        x_values.append(date_text)
        y_values.append(value)

    if len(x_values) < 2:
        return None

    indicator = _clean(primary_row.get("indicator")) or series_id
    units_short = _clean(primary_row.get("units_short"))
    latest_date = _clean(primary_row.get("latest_date"))
    return {
        "kind": "timeseries",
        "title": f"{indicator} ({series_id})",
        "subtitle": " | ".join(item for item in [latest_date, units_short] if item),
        "series_id": series_id,
        "x": x_values,
        "y": y_values,
    }


def _summarize_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("payload")
    preview = _preview_payload(payload)
    render_payload = _build_render_payload(result)
    result_type = _clean(result.get("result_type")) or "unknown"
    provenance = result.get("provenance")

    if preview.get("kind") == "rows":
        preview_text = (
            f"{result_type} with {int(preview.get('row_count') or 0)} rows; "
            f"columns={', '.join(list(preview.get('columns') or [])) or 'n/a'}; "
            f"sample={_json_dumps(preview.get('sample') or [])}"
        )
    elif preview.get("kind") == "summary_rows":
        preview_text = (
            f"{result_type} summary with {int(preview.get('row_count') or 0)} rows; "
            f"columns={', '.join(list(preview.get('columns') or [])) or 'n/a'}; "
            f"sample={_json_dumps(preview.get('sample') or [])}"
        )
    elif preview.get("kind") == "chart":
        preview_text = (
            f"{result_type} chart {preview.get('chart_id') or ''} "
            f"titled '{preview.get('title') or ''}' with "
            f"{int(preview.get('trace_count') or 0)} traces and "
            f"datasets={_json_dumps(preview.get('dataset_sizes') or {})}"
        )
    elif preview.get("kind") == "object":
        preview_text = (
            f"{result_type} object keys={', '.join(list(preview.get('keys') or [])) or 'n/a'}; "
            f"scalars={_json_dumps(preview.get('scalars') or {})}"
        )
    else:
        preview_text = f"{result_type}: {_json_dumps(preview.get('value'))}"

    llm_context_text = ""
    if isinstance(payload, dict):
        explicit_context = _clean(payload.get("llm_context_text"))
        if explicit_context:
            llm_context_text = _truncate(explicit_context, limit=int(get_config_param(_P_TOOL_RESULT_CONTEXT_LIMIT)))
        elif isinstance(payload.get("homepage_summary"), dict):
            llm_context_text = _truncate(
                _attention_home_llm_context(payload),
                limit=int(get_config_param(_P_TOOL_RESULT_CONTEXT_LIMIT)),
            )
        elif isinstance(payload.get("summary"), list):
            lines: list[str] = []
            for item in list(payload.get("summary") or [])[:int(get_config_param(_P_TOOL_RESULT_SUMMARY_ITEMS))]:
                if not isinstance(item, dict):
                    continue
                parts = [
                    _clean(item.get("symbol") or item.get("label") or item.get("title") or item.get("headline")),
                    _clean(item.get("summary_text") or item.get("summary") or item.get("excerpt")),
                    _clean(item.get("source")),
                    _clean(item.get("url")),
                ]
                text = " | ".join(part for part in parts if part)
                if text:
                    lines.append(text)
            if lines:
                llm_context_text = _truncate("\n".join(lines), limit=int(get_config_param(_P_TOOL_RESULT_SUMMARY_LIMIT)))

    # Build a user-facing preview (clean, concise) separate from the LLM preview
    user_preview = ""
    if llm_context_text:
        user_preview = _truncate(llm_context_text, limit=int(get_config_param(_P_USER_PREVIEW_LIMIT)))
    elif preview.get("kind") == "rows" and int(preview.get("row_count") or 0) > 0:
        row_count = int(preview.get("row_count") or 0)
        columns = list(preview.get("columns") or [])
        user_preview = f"{row_count} results" + (f" ({', '.join(columns[:4])})" if columns else "")
    elif preview.get("kind") == "summary_rows" and int(preview.get("row_count") or 0) > 0:
        row_count = int(preview.get("row_count") or 0)
        columns = list(preview.get("columns") or [])
        user_preview = f"{row_count} items" + (f" ({', '.join(columns[:4])})" if columns else "")
    elif preview.get("kind") == "chart":
        user_preview = f"Chart: {preview.get('title') or preview.get('chart_id') or 'result'}"
    elif preview.get("kind") == "object":
        scalars = dict(preview.get("scalars") or {})
        if scalars:
            parts = [f"{k}={v}" for k, v in list(scalars.items())[:4]]
            user_preview = ", ".join(parts)
    if not user_preview:
        user_preview = "No results returned."

    # Extract source links from research results (URLs from news, search, page browsing)
    source_links: list[dict[str, str]] = []
    if isinstance(payload, dict):
        # From summary rows (live_event_evidence, retained_context)
        for item in list(payload.get("summary") or [])[:8]:
            if not isinstance(item, dict):
                continue
            url = _clean(item.get("url"))
            if url and url.startswith("http"):
                title = _clean(item.get("headline") or item.get("title") or item.get("label") or url)
                source = _clean(item.get("source") or "")
                label = f"{title} ({source})" if source else title
                source_links.append({"url": url, "label": label})
        # From articles (investigator.recent_news)
        for item in list(payload.get("articles") or [])[:8]:
            if not isinstance(item, dict):
                continue
            url = _clean(item.get("url"))
            if url and url.startswith("http"):
                title = _clean(item.get("headline") or item.get("title") or url)
                source_links.append({"url": url, "label": title})
        # From open_page result
        page_url = _clean(payload.get("url"))
        if page_url and page_url.startswith("http"):
            page_title = _clean(payload.get("title") or page_url)
            source_links.append({"url": page_url, "label": page_title})
        # From rows in the payload directly
        for row in list(payload.get("rows") or [])[:8]:
            if not isinstance(row, dict):
                continue
            url = _clean(row.get("url"))
            if url and url.startswith("http"):
                title = _clean(row.get("headline") or row.get("title") or url)
                source_links.append({"url": url, "label": title})

    return {
        "result_type": result_type,
        "provenance": provenance,
        "preview": preview,
        "preview_text": preview_text,
        "user_preview": user_preview,
        "render_payload": render_payload,
        "llm_context_text": llm_context_text,
        "source_links": source_links if source_links else None,
    }


def _attention_home_llm_context(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    generated = _clean(payload.get("generated_at_utc"))
    coverage = payload.get("coverage_summary") if isinstance(payload.get("coverage_summary"), dict) else {}
    if generated:
        lines.append(f"Attention home generated at {generated}.")
    if coverage:
        counts = []
        for key, label in (
            ("candidate_count", "candidates"),
            ("event_count", "events"),
            ("unresolved_count", "unresolved moves"),
            ("macro_anchor_count", "macro anchors"),
            ("research_symbol_count", "research symbols"),
        ):
            value = coverage.get(key)
            if value is not None:
                counts.append(f"{value} {label}")
        if counts:
            lines.append("Coverage: " + ", ".join(counts) + ".")
    homepage_summary = payload.get("homepage_summary")
    if isinstance(homepage_summary, dict):
        headline = _clean(homepage_summary.get("headline"))
        summary_text = _clean(homepage_summary.get("summary_text"))
        if headline:
            lines.append(f"Homepage headline: {headline}.")
        if summary_text:
            lines.append(summary_text)
    top_events = payload.get("top_events")
    if isinstance(top_events, list) and top_events:
        lines.append("Top events:")
        for event in top_events[:4]:
            if not isinstance(event, dict):
                continue
            title = _clean(event.get("event_title") or event.get("surface_summary_text"))
            summary = _clean(event.get("surface_summary_text") or event.get("what_happened_text"))
            symbols = ", ".join(str(sym) for sym in list(event.get("supporting_symbols") or [])[:8])
            parts = [title, summary, f"symbols: {symbols}" if symbols else ""]
            text = " | ".join(part for part in parts if part)
            if text:
                lines.append(f"- {text}")
    unresolved = payload.get("unresolved_large_moves")
    if isinstance(unresolved, list) and unresolved:
        lines.append("Unresolved large moves:")
        for item in unresolved[:4]:
            if not isinstance(item, dict):
                continue
            symbol = _clean(item.get("symbol"))
            change = item.get("change_pct")
            headline = _clean(item.get("headline") or item.get("surface_summary_text"))
            if symbol or headline:
                move_text = f"{float(change):+.2f}%" if isinstance(change, (int, float)) else ""
                lines.append(" - ".join(part for part in [symbol, move_text, headline] if part))
    return "\n".join(line for line in lines if line)


def _tool_catalog_prompt(catalog: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for tool in catalog:
        input_schema = dict(tool.get("inputSchema") or {})
        properties = dict(input_schema.get("properties") or {})
        params = ", ".join(properties.keys()) or "none"
        resolution = _clean(tool.get("resolution"))
        resolution_text = f" [{resolution}]" if resolution else ""
        lines.append(
            f"- {tool.get('name')}: {tool.get('description')}{resolution_text}; params: {params}"
        )
    return "\n".join(lines)


def _tool_history_prompt(tool_calls: list[dict[str, Any]]) -> str:
    if not tool_calls:
        return "No tools have been called yet."
    lines: list[str] = []
    for tool_call in tool_calls:
        result_summary = dict(tool_call.get("result_summary") or {})
        result_text = _truncate(
            result_summary.get("llm_context_text") or result_summary.get("preview_text") or "",
            limit=int(get_config_param(_P_TOOL_HISTORY_PER_TOOL_LIMIT)),
        )
        lines.append(
            f"- {tool_call['tool_call_id']} | {tool_call['status']} | {tool_call['tool_name']} | "
            f"args={_json_dumps(tool_call.get('arguments') or {}, limit=500)} | "
            f"result={result_text}"
        )
    return "\n".join(lines)


def _tool_entry_by_name(catalog: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
    normalized_name = _clean(tool_name)
    return next((tool for tool in catalog if _clean(tool.get("name")) == normalized_name), {})


def _normalized_arguments(
    tool_entry: dict[str, Any],
    arguments: dict[str, Any],
    *,
    force_refresh: bool,
) -> dict[str, Any]:
    normalized = dict(arguments or {})
    properties = dict((tool_entry.get("inputSchema") or {}).get("properties") or {})
    if "force_refresh" in properties and "force_refresh" not in normalized:
        normalized["force_refresh"] = force_refresh
    return normalized


def _coerce_tool_arguments(raw_arguments: Any) -> tuple[dict[str, Any], str]:
    if isinstance(raw_arguments, list):
        out: dict[str, Any] = {}
        for item in raw_arguments:
            if not isinstance(item, dict):
                return {}, "Tool arguments entries must be objects."
            name = _clean(item.get("name"))
            value_kind = _clean(item.get("value_kind")).lower()
            if not name:
                return {}, "Tool argument entry is missing `name`."
            if value_kind == "string":
                out[name] = _clean(item.get("string_value"))
            elif value_kind == "number":
                number_value = item.get("number_value")
                if isinstance(number_value, bool) or number_value is None:
                    return {}, f"Tool argument `{name}` requires number_value."
                out[name] = float(number_value)
            elif value_kind == "boolean":
                boolean_value = item.get("boolean_value")
                if not isinstance(boolean_value, bool):
                    return {}, f"Tool argument `{name}` requires boolean_value."
                out[name] = boolean_value
            elif value_kind == "string_list":
                values = item.get("string_list_value")
                if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                    return {}, f"Tool argument `{name}` requires string_list_value."
                out[name] = [str(value) for value in values]
            elif value_kind in {"null", ""}:
                out[name] = None
            else:
                return {}, f"Unsupported tool argument value_kind `{value_kind}`."
        return out, ""
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments), ""
    if raw_arguments is None:
        return {}, ""
    if isinstance(raw_arguments, str):
        clean = raw_arguments.strip()
        if not clean:
            return {}, ""
        try:
            parsed = json.loads(clean)
        except Exception as exc:
            return {}, f"Invalid tool arguments JSON: {exc}"
        if not isinstance(parsed, dict):
            return {}, "Tool arguments JSON must decode to an object."
        return dict(parsed), ""
    return {}, f"Unsupported tool arguments type: {type(raw_arguments).__name__}"


def _emit_progress(
    progress_callback: ProgressCallback | None,
    *,
    stage: str,
    message: str,
    progress: float,
    **extra: Any,
) -> None:
    if progress_callback is None:
        return
    payload: dict[str, Any] = {
        "stage": _clean(stage),
        "message": _clean(message),
        "progress": max(0.0, min(float(progress or 0.0), 1.0)),
    }
    payload.update(extra)
    try:
        progress_callback(payload)
    except Exception:
        return


def _looks_like_transient_transport_error(text: object) -> bool:
    cleaned = str(text or "").strip().lower()
    if not cleaned:
        return False
    markers = (
        "remotedisconnected",
        "remote end closed connection without response",
        "connection aborted",
        "connection reset",
        "connectionerror",
        "readtimeout",
        "read timed out",
        "timed out",
        "timeout",
        "temporarily unavailable",
    )
    return any(marker in cleaned for marker in markers)


def _safe_agent_error_text(error: object) -> str:
    raw = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error or "")
    if _looks_like_transient_transport_error(raw):
        return (
            "A research or model connection dropped before it returned a response. "
            "This is usually transient; rerun the request to retry the source fetch."
        )
    return _clean(raw) or "The research agent failed before it could produce an answer."


def _fallback_answer(query: str, tool_calls: list[dict[str, Any]]) -> str:
    successful = [call for call in tool_calls if str(call.get("status") or "") == "completed"]
    if not successful:
        return (
            "I could not collect enough data to answer this from the available modules. "
            f"Question: {query}"
        )
    lines = [
        "I collected tool output but did not get a clean final synthesis from the model.",
        f"Question: {query}",
        "",
        "Evidence collected:",
    ]
    for tool_call in successful[-3:]:
        lines.append(
            f"- {tool_call.get('tool_name')}: {tool_call.get('result_summary', {}).get('preview_text')}"
        )
    return "\n".join(lines)


_PLANNER_SYSTEM_PROMPT = register_narrative_prompt(
    name="Omnibar Agent Planner (tool-calling reasoning)",
    file="services/omnibar_agent.py",
    group="Chat + Search",
    prompt=(
        f"You are the Spectral Nature omnibar agent. {NARRATIVE_STYLE_RULE} "
        "Answer the user's question by calling the available tools when needed. Do not invent data. "
        "Do not repeat a tool with materially identical arguments. "
        "\n\nEvidence priority: "
        "1. Check the pre-fetched internal evidence already in this prompt first. "
        "2. If more detail is needed, call research.search_evidence or research.retained_context. "
        "3. For live web news, use research.live_event_evidence. "
        "4. For specific tickers, use investigator.* tools (technical_signals, forecast, company_context, fundamentals, recent_news). "
        "5. For broad spillover or second-order effects, use research.market_impact_map. "
        "6. For deeper reads on a URL, use research.open_page. "
        "7. For event significance analysis, use dataset.event_significance with an inferred event_date. "
        "8. For thesis verification, use hypothesis.verify after gathering evidence. "
        "\n\nOnce you have enough evidence, return action='final' with a structured markdown answer. "
        "Start with a bold verdict sentence, use ### headings for multi-part answers, **bold** tickers and metrics, and end with a takeaway. "
        "When prior conversation is provided, resolve references like 'this', 'that', 'it' from prior turns."
    ),
)


def _planner_system_prompt() -> str:
    return get_prompt(_PLANNER_SYSTEM_PROMPT)


def _search_conversation_history(
    conversation_history: list[dict[str, Any]],
    search_text: str,
) -> dict[str, Any]:
    """Search prior conversation turns for a keyword/phrase.

    Returns matching turns with full answer text so the agent can resolve
    references to specifics from earlier in the session.
    """
    needle = search_text.lower().strip()
    if not needle:
        return {"status": "ok", "matches": [], "llm_context_text": "No search text provided."}

    matches: list[dict[str, str]] = []
    for msg in conversation_history:
        role = str(msg.get("role") or "").strip()
        content = str(msg.get("answer") or msg.get("content") or "").strip()
        if not content:
            continue
        if needle in content.lower():
            # Truncate to a reasonable window around the match
            lower_content = content.lower()
            idx = lower_content.find(needle)
            start = max(0, idx - 200)
            end = min(len(content), idx + len(needle) + 800)
            snippet = content[start:end]
            if start > 0:
                snippet = "..." + snippet
            if end < len(content):
                snippet = snippet + "..."
            matches.append({"role": role, "snippet": snippet})

    if not matches:
        return {
            "status": "ok",
            "matches": [],
            "llm_context_text": f"No prior turns matched '{search_text}'.",
        }

    lines = [f"Found {len(matches)} matching turn(s) for '{search_text}':"]
    for m in matches[:5]:
        lines.append(f"\n[{m['role']}]: {m['snippet']}")
    return {
        "status": "ok",
        "matches": matches[:5],
        "llm_context_text": "\n".join(lines),
    }


def _completed_tool_names(tool_calls: list[dict[str, Any]]) -> set[str]:
    return {
        _clean(call.get("tool_name"))
        for call in tool_calls
        if str(call.get("status") or "") == "completed" and _clean(call.get("tool_name"))
    }


def _needs_live_evidence_recovery(tool_calls: list[dict[str, Any]]) -> bool:
    completed = _completed_tool_names(tool_calls)
    if "research.live_event_evidence" in completed:
        return False
    if "research.search_evidence" in completed:
        low_signal_markers = (
            "0 results",
            "no results",
            "no matching",
            "no retained",
            "no company context",
            "returned empty",
        )
        completed_calls = [
            call for call in tool_calls if str(call.get("status") or "") == "completed"
        ]
        if completed_calls and all(
            any(
                marker in _json_dumps(dict(call.get("result_summary") or {}), limit=1200).lower()
                for marker in low_signal_markers
            )
            for call in completed_calls
        ):
            return True
        return False
    return bool(completed)


def _compact_conversation_history(
    conversation_history: list[dict[str, Any]] | None,
    max_chars: int = 3000,
) -> str:
    """Compact prior conversation turns into a context block for the planner.

    Strategy:
    - The most recent assistant answer is kept in full (up to 1500 chars) so
      the agent can resolve references to specifics in the last answer.
    - Older assistant answers are compacted to ~300 chars each.
    - User messages are always kept in full (they're short).
    - Walks newest-first, then reverses for chronological order.
    """
    if not conversation_history:
        return ""
    turns: list[str] = []
    total = 0
    assistant_count = 0
    reversed_history = list(reversed(conversation_history))
    for msg in reversed_history:
        role = str(msg.get("role") or "").strip()
        if role == "user":
            content = str(msg.get("content") or "").strip()
        elif role == "assistant":
            content = str(msg.get("answer") or msg.get("content") or "").strip()
            # Most recent assistant answer gets generous room;
            # older ones get compacted.
            limit = 1500 if assistant_count == 0 else 300
            assistant_count += 1
            if len(content) > limit:
                content = content[:limit].rsplit(" ", 1)[0] + "..."
        else:
            continue
        if not content:
            continue
        line = f"[{role}]: {content}"
        if total + len(line) > max_chars:
            break
        turns.append(line)
        total += len(line)
    if not turns:
        return ""
    turns.reverse()
    return "Prior conversation:\n" + "\n".join(turns) + "\n\n"


def _message_text(msg: dict[str, Any]) -> str:
    return str(msg.get("answer") or msg.get("content") or msg.get("answer_markdown") or "").strip()


def _latest_user_and_assistant_turn(
    conversation_history: list[dict[str, Any]] | None,
) -> tuple[str, str]:
    if not conversation_history:
        return "", ""
    latest_user = ""
    latest_assistant = ""
    for msg in reversed(conversation_history):
        role = str(msg.get("role") or "").strip().lower()
        content = _message_text(msg)
        if not content:
            continue
        if role == "assistant" and not latest_assistant:
            latest_assistant = content
        elif role == "user" and not latest_user:
            latest_user = content
        if latest_user and latest_assistant:
            break
    return latest_user, latest_assistant


def _is_affirmative_followup(query: str) -> bool:
    normalized = _clean(query).lower().strip(" \t\r\n.!?")
    return normalized in _AFFIRMATIVE_FOLLOWUP_REPLIES


def resolve_conversation_followup_query(
    query: str,
    conversation_history: list[dict[str, Any]] | None,
    *,
    max_answer_chars: int = 1800,
) -> tuple[str, bool]:
    """Resolve bare replies like "yes" against the prior chat turn.

    The chat UI stores the literal user text, but the agent needs an actionable
    query.  Without this handoff, the router can treat "yes" as a standalone
    search and skip the agent path that has conversation context.
    """
    normalized_query = _clean(query)
    if not _is_affirmative_followup(normalized_query):
        return normalized_query, False

    previous_user, previous_assistant = _latest_user_and_assistant_turn(conversation_history)
    if not previous_assistant:
        return normalized_query, False

    answer_excerpt = previous_assistant
    if len(answer_excerpt) > max_answer_chars:
        answer_excerpt = answer_excerpt[:max_answer_chars].rsplit(" ", 1)[0].strip()
        answer_excerpt = f"{answer_excerpt}..."
    if not previous_user:
        previous_user = "the previous market question"

    resolved_query = (
        "Continue the previous Chat + Search thread. The user replied "
        f"'{normalized_query}', so carry out the natural next step implied by the prior assistant answer. "
        "Use the prior answer and prior question as context; verify or expand with evidence instead of "
        "treating the reply as a standalone query.\n\n"
        f"Previous user question:\n{previous_user}\n\n"
        f"Previous assistant answer:\n{answer_excerpt}\n\n"
        f"Current user reply:\n{normalized_query}"
    )
    return resolved_query, True


def _extract_query_tickers(query: str, *, limit: int = 5) -> list[str]:
    """Extract likely ticker symbols without treating common acronyms as tickers."""
    out: list[str] = []
    for match in re.finditer(r"\b[A-Z][A-Z0-9.\-]{0,5}\b", str(query or "")):
        ticker = match.group(0).strip(".-").upper()
        if not ticker or ticker in _NON_TICKER_UPPERCASE_WORDS:
            continue
        if len(ticker) == 1 and not re.search(rf"\b{re.escape(ticker)}\s*[:$]", str(query or "")):
            continue
        if ticker not in out:
            out.append(ticker)
        if len(out) >= limit:
            break
    return out


def _query_looks_current_or_event_driven(query: str) -> bool:
    text = _clean(query).lower()
    markers = (
        "current",
        "latest",
        "today",
        "now",
        "recent",
        "news",
        "catalyst",
        "catalysts",
        "narrative",
        "what happened",
        "what is going on",
        "going on",
        "crisis",
        "war",
        "tariff",
        "strike",
        "hormuz",
        "iran",
        "oil",
        "fed",
        "earnings",
    )
    return any(marker in text for marker in markers)


def _query_looks_live_event_driven(query: str) -> bool:
    text = _clean(query).lower()
    live_markers = (
        "what happened",
        "what is going on",
        "going on",
        "crisis",
        "war",
        "tariff",
        "strike",
        "hormuz",
        "iran",
        "oil shock",
        "talks",
        "ceasefire",
        "election",
        "sanction",
        "sanctions",
    )
    return any(marker in text for marker in live_markers)


def _query_looks_broad_market(query: str, tickers: list[str]) -> bool:
    if tickers:
        return False
    text = _clean(query).lower().strip(" ?!.")
    broad_exact = {
        "what matters today",
        "what matters now",
        "what should i know today",
        "what is important today",
        "what's important today",
        "what happened today",
    }
    if text in broad_exact:
        return True
    return any(
        marker in text
        for marker in (
            "market today",
            "markets today",
            "what matters",
            "top market",
            "market narrative",
            "risk cycle",
        )
    )


def _tool_available(tool_catalog: list[dict[str, Any]], tool_name: str) -> bool:
    return any(_clean(tool.get("name")) == tool_name for tool in tool_catalog)


def _bootstrap_tool_plan(
    *,
    query: str,
    tool_catalog: list[dict[str, Any]],
    force_refresh: bool,
    max_calls: int,
) -> list[tuple[str, dict[str, Any]]]:
    """Choose obvious first evidence calls before spending an LLM planner step."""
    if max_calls <= 0:
        return []
    tickers = _extract_query_tickers(query)
    current_or_event = _query_looks_current_or_event_driven(query)
    broad_market = _query_looks_broad_market(query, tickers)
    plan: list[tuple[str, dict[str, Any]]] = []

    def add(tool_name: str, arguments: dict[str, Any]) -> None:
        if len(plan) >= max_calls:
            return
        if not _tool_available(tool_catalog, tool_name):
            return
        signature = f"{tool_name}::{_json_dumps(arguments, limit=1000)}"
        if any(f"{name}::{_json_dumps(args, limit=1000)}" == signature for name, args in plan):
            return
        plan.append((tool_name, arguments))

    if broad_market:
        add("dataset.attention_home_1d", {})
        if not plan:
            add("research.retained_context", {"query": query, "max_items": 6, "force_refresh": force_refresh})
        return plan

    if tickers:
        if len(tickers) > 1:
            for ticker in tickers[:3]:
                add("investigator.company_context", {"ticker": ticker})
            if current_or_event:
                add(
                    "research.live_event_evidence",
                    {
                        "query": query,
                        "focus_symbols": tickers[:5],
                        "max_results": 8,
                        "force_refresh": force_refresh,
                    },
                )
            add("research.search_evidence", {"query": query, "tickers": tickers[:5], "max_results": 10})
        else:
            ticker = tickers[0]
            add("investigator.company_context", {"ticker": ticker})
            if current_or_event:
                add("investigator.recent_news", {"ticker": ticker, "days": 14, "limit": 8})
                add(
                    "research.live_event_evidence",
                    {
                        "query": query,
                        "focus_symbols": [ticker],
                        "max_results": 6,
                        "force_refresh": force_refresh,
                    },
                )
                add(
                    "research.search_evidence",
                    {"query": query, "tickers": [ticker], "max_results": 8},
                )
        return plan

    if _query_looks_live_event_driven(query):
        add("research.market_impact_map", {"query": query, "max_symbols": 8})
        add(
            "research.live_event_evidence",
            {"query": query, "max_results": 6, "force_refresh": force_refresh},
        )
        if not plan:
            add("research.retained_context", {"query": query, "max_items": 6, "force_refresh": force_refresh})
    return plan


def _execute_seeded_tool_call(
    *,
    service: QueryService,
    run_id: str,
    tool_calls: list[dict[str, Any]],
    progress_callback: ProgressCallback | None,
    tool_name: str,
    arguments: dict[str, Any],
    progress: float,
) -> bool:
    call_id = f"agtc_{len(tool_calls) + 1}"
    _emit_progress(
        progress_callback,
        stage="tool_start",
        message=f"Collecting initial evidence from {tool_name}.",
        progress=progress,
        tool_name=tool_name,
        tool_call_id=call_id,
        tool_call_count=len(tool_calls),
        tool_arguments=arguments,
    )
    try:
        result = _invoke_tool_with_heartbeat(
            service=service,
            tool_name=tool_name,
            arguments=arguments,
            run_id=run_id,
            progress_callback=progress_callback,
            progress=progress,
            tool_call_id=call_id,
            tool_call_count=len(tool_calls),
        )
        result_summary = _summarize_tool_result(result)
        tool_calls.append(
            {
                "tool_call_id": call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "status": "completed",
                "error": None,
                "result_summary": result_summary,
            }
        )
        _emit_progress(
            progress_callback,
            stage="tool_complete",
            message=f"Collected initial evidence from {tool_name}.",
            progress=min(progress + 0.03, 0.9),
            tool_name=tool_name,
            tool_call_id=call_id,
            tool_call_count=len(tool_calls),
            tool_arguments=arguments,
            result_preview=_truncate(result_summary.get("user_preview") or result_summary.get("preview_text") or "", limit=300),
            render_payload=result_summary.get("render_payload"),
            source_links=result_summary.get("source_links"),
        )
        return True
    except Exception as exc:
        tool_calls.append(
            {
                "tool_call_id": call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "result_summary": {
                    "preview_text": f"{type(exc).__name__}: {exc}",
                    "result_type": "error",
                    "provenance": None,
                    "preview": {"kind": "error"},
                },
            }
        )
        _emit_progress(
            progress_callback,
            stage="tool_failed",
            message=f"{tool_name} failed during initial evidence collection.",
            progress=min(progress + 0.03, 0.9),
            tool_name=tool_name,
            tool_call_id=call_id,
            tool_call_count=len(tool_calls),
        )
        return False


def _invoke_tool_with_heartbeat(
    *,
    service: QueryService,
    tool_name: str,
    arguments: dict[str, Any],
    run_id: str,
    progress_callback: ProgressCallback | None,
    progress: float,
    tool_call_id: str,
    tool_call_count: int,
) -> dict[str, Any]:
    timeout_seconds = max(int(get_config_param(_P_TOOL_CALL_TIMEOUT_SECONDS)), 1)
    result_box: list[dict[str, Any] | None] = [None]
    error_box: list[BaseException | None] = [None]

    def _run_tool() -> None:
        try:
            result_box[0] = invoke_tool(
                service=service,
                tool_name=tool_name,
                arguments=arguments,
                run_id=run_id,
            )
        except BaseException as exc:
            error_box[0] = exc

    tool_thread = threading.Thread(target=_run_tool, name=f"omnibar-tool-{tool_name[:32]}", daemon=True)
    tool_thread.start()
    heartbeat_interval = 5.0
    elapsed = 0.0
    while tool_thread.is_alive():
        wait_seconds = min(heartbeat_interval, max(float(timeout_seconds) - elapsed, 0.1))
        tool_thread.join(timeout=wait_seconds)
        if not tool_thread.is_alive():
            break
        elapsed += wait_seconds
        if elapsed >= timeout_seconds:
            _emit_progress(
                progress_callback,
                stage="tool_timeout",
                message=f"{tool_name} timed out after {timeout_seconds}s.",
                progress=min(progress + 0.08, 0.91),
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_call_count=tool_call_count,
                tool_arguments=arguments,
                elapsed_seconds=int(elapsed),
            )
            raise TimeoutError(f"{tool_name} timed out after {timeout_seconds}s.")
        _emit_progress(
            progress_callback,
            stage="tool_heartbeat",
            message=f"Still checking {tool_name}... ({int(elapsed)}s)",
            progress=min(progress + 0.02, 0.9),
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_call_count=tool_call_count,
            tool_arguments=arguments,
            elapsed_seconds=int(elapsed),
        )
    if error_box[0] is not None:
        raise error_box[0]
    result = result_box[0]
    if result is None:
        raise RuntimeError(f"{tool_name} returned no result.")
    return result


def _run_hidden_step_with_timeout(
    *,
    label: str,
    timeout_seconds: int,
    progress_callback: ProgressCallback | None,
    progress: float,
    func: Callable[[], Any],
) -> Any:
    result_box: list[Any] = [None]
    error_box: list[BaseException | None] = [None]

    def _runner() -> None:
        try:
            result_box[0] = func()
        except BaseException as exc:
            error_box[0] = exc

    step_thread = threading.Thread(target=_runner, name=f"omnibar-hidden-{label[:32]}", daemon=True)
    step_thread.start()
    heartbeat_interval = 5.0
    elapsed = 0.0
    while step_thread.is_alive():
        wait_seconds = min(heartbeat_interval, max(float(timeout_seconds) - elapsed, 0.1))
        step_thread.join(timeout=wait_seconds)
        if not step_thread.is_alive():
            break
        elapsed += wait_seconds
        if elapsed >= timeout_seconds:
            _emit_progress(
                progress_callback,
                stage="hidden_step_timeout",
                message=f"{label} timed out after {timeout_seconds}s.",
                progress=min(progress + 0.02, 0.9),
                elapsed_seconds=int(elapsed),
            )
            raise TimeoutError(f"{label} timed out after {timeout_seconds}s.")
        _emit_progress(
            progress_callback,
            stage="hidden_step_heartbeat",
            message=f"Still preparing {label}... ({int(elapsed)}s)",
            progress=min(progress + 0.01, 0.9),
            elapsed_seconds=int(elapsed),
        )
    if error_box[0] is not None:
        raise error_box[0]
    return result_box[0]


def _completed_seeded_evidence_count(tool_calls: list[dict[str, Any]], seeded_names: set[str]) -> int:
    return sum(
        1
        for call in tool_calls
        if str(call.get("status") or "") == "completed"
        and _clean(call.get("tool_name")) in seeded_names
        and not _tool_call_is_low_signal(call)
    )


def _tool_call_is_low_signal(tool_call: dict[str, Any]) -> bool:
    text = _json_dumps(tool_call.get("result_summary") or {}, limit=1800).lower()
    low_signal_markers = (
        "0 results",
        "no results",
        "no matching",
        "no retained",
        "no company context",
        "no recent news",
        "returned empty",
        "no results returned",
    )
    return any(marker in text for marker in low_signal_markers)


def _should_skip_planner_after_bootstrap(query: str, tool_calls: list[dict[str, Any]], seeded_names: set[str]) -> bool:
    tickers = _extract_query_tickers(query)
    if _completed_seeded_evidence_count(tool_calls, seeded_names) <= 0:
        return False
    completed = _completed_tool_names(tool_calls)
    meaningful_completed = {
        _clean(call.get("tool_name"))
        for call in tool_calls
        if str(call.get("status") or "") == "completed"
        and not _tool_call_is_low_signal(call)
    }
    if _query_looks_broad_market(query, tickers) and (
        "dataset.attention_home_1d" in meaningful_completed or "research.retained_context" in meaningful_completed
    ):
        return True
    if tickers and not _query_looks_current_or_event_driven(query) and "investigator.company_context" in meaningful_completed:
        return True
    if tickers and _query_looks_current_or_event_driven(query) and "research.live_event_evidence" in meaningful_completed:
        return True
    if not tickers and _query_looks_live_event_driven(query) and (
        "research.live_event_evidence" in meaningful_completed or "research.market_impact_map" in meaningful_completed
    ):
        return True
    return False


def _planner_user_prompt(
    *,
    query: str,
    tool_catalog: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    max_tool_calls: int,
    conversation_history: list[dict[str, Any]] | None = None,
    prefetched_context: str = "",
) -> str:
    history_block = _compact_conversation_history(conversation_history, max_chars=int(get_config_param(_P_CONVERSATION_HISTORY_LIMIT)))
    evidence_block = ""
    if prefetched_context:
        evidence_block = (
            f"{prefetched_context}\n\n"
            "Use this evidence when answering. Call research tools "
            "only if you need additional details or different angles.\n\n"
        )
    return (
        f"{history_block}"
        f"{evidence_block}"
        f"User question:\n{query}\n\n"
        f"Tool budget: {max_tool_calls - len(tool_calls)} remaining out of {max_tool_calls}.\n\n"
        "Available tools:\n"
        f"{_tool_catalog_prompt(tool_catalog)}\n\n"
        "Tool call history:\n"
        f"{_tool_history_prompt(tool_calls)}\n\n"
        "Return exactly one next action. "
        "If you still need data, set action='tool_call' and choose one tool. "
        "If you can answer now, set action='final'. "
        "When action='tool_call', populate tool_arguments as a list of typed entries. "
        "Examples: "
        '[{"name":"years","value_kind":"number","string_value":null,"number_value":1,"boolean_value":null,"string_list_value":null}] '
        'or [{"name":"ticker","value_kind":"string","string_value":"AAPL","number_value":null,"boolean_value":null,"string_list_value":null}]. '
        "Use an empty list when the tool takes no arguments."
    )


def _final_user_prompt(
    *,
    query: str,
    tool_calls: list[dict[str, Any]],
    conversation_history: list[dict[str, Any]] | None = None,
    prefetched_context: str = "",
) -> str:
    history_block = _compact_conversation_history(conversation_history, max_chars=int(get_config_param(_P_CONVERSATION_HISTORY_LIMIT)))
    evidence_block = ""
    if prefetched_context:
        evidence_block = (
            f"{prefetched_context}\n\n"
        )
    return (
        f"{history_block}"
        f"{evidence_block}"
        f"User question:\n{query}\n\n"
        "Tool evidence:\n"
        f"{_tool_history_prompt(tool_calls)}\n\n"
        "Write the best grounded markdown answer you can from this evidence only. "
        "If the evidence includes fresh web research, lightly mention one or two supporting sources or links. "
        "Do not turn the answer into a citation list. "
        "If the evidence is incomplete, say what is missing."
    )


def _synthesis_llm_client(primary_llm: LLMClient) -> LLMClient:
    """Optionally use a faster model for final answer formatting.

    Planning may benefit from a reasoning model, but when deterministic
    bootstrap has already collected the evidence, the final step is mostly
    compression and writing.  Keep this opt-in through env so provider swaps
    stay scoped to the omnibar agent.
    """
    model_override = _clean(
        os.getenv("OMNIBAR_AGENT_SYNTHESIS_LLM_MODEL")
        or os.getenv("OMNIBAR_AGENT_SYNTHESIS_MODEL")
    )
    if not model_override:
        return primary_llm
    config = getattr(primary_llm, "config", None)
    if not isinstance(config, LLMConfig):
        return primary_llm
    if model_override == config.model:
        return primary_llm
    try:
        next_config = replace(
            config,
            model=model_override,
            deployment=(
                _clean(os.getenv("OMNIBAR_AGENT_SYNTHESIS_LLM_DEPLOYMENT"))
                or model_override
            ),
        )
        return type(primary_llm)(next_config)
    except Exception:
        return primary_llm


def run_omnibar_agent(
    *,
    query: str,
    force_refresh: bool = False,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    service: QueryService | None = None,
    llm_client: LLMClient | None = None,
    progress_callback: ProgressCallback | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    persist_findings: bool = True,
) -> dict[str, Any]:
    normalized_query = _clean(query)
    agent_query, followup_resolved = resolve_conversation_followup_query(
        normalized_query,
        conversation_history,
    )
    # Use config-param override when caller passed the module-level default
    if max_tool_calls == DEFAULT_MAX_TOOL_CALLS:
        max_tool_calls = int(get_config_param(_P_MAX_TOOL_CALLS))
    llm_step_timeout_seconds = max(int(get_config_param(_P_LLM_STEP_TIMEOUT_SECONDS)), 1)
    run_id = f"agrun_{uuid.uuid4().hex[:10]}"
    if not agent_query:
        return {
            "run_id": run_id,
            "status": "failed",
            "mode": "sync",
            "model": "",
            "tool_calls": [],
            "answer_markdown": "",
            "confidence": "low",
            "limitations": ["Empty query."],
            "error": "Empty query.",
        }

    resolved_service = service or QueryService.from_environment()
    resolved_llm = llm_client or load_llm_client(env_prefix="OMNIBAR_AGENT_") or load_llm_client()
    if resolved_llm is None:
        return {
            "run_id": run_id,
            "status": "unavailable",
            "mode": "sync",
            "model": "",
            "tool_calls": [],
            "answer_markdown": "The LLM runtime is not configured, so the omnibar agent cannot run tool-based analysis right now.",
            "confidence": "low",
            "limitations": ["LLM runtime is unavailable."],
            "error": "LLM runtime is unavailable.",
        }
    _emit_progress(
        progress_callback,
        stage="start",
        message="Preparing shared omnibar agent.",
        progress=0.04,
        run_id=run_id,
    )
    if followup_resolved:
        _emit_progress(
            progress_callback,
            stage="conversation_followup_resolved",
            message="Resolved the reply against the prior chat turn.",
            progress=0.06,
            original_query=normalized_query,
            resolved_query=agent_query,
        )
    tool_catalog = build_tool_catalog(resolved_service)
    # Inject conversation.prior_answers tool when chat history is available
    if conversation_history:
        tool_catalog.append({
            "name": "conversation.prior_answers",
            "description": (
                "Search prior conversation turns for specific details. "
                "Use when the user references something from an earlier answer "
                "and the compact conversation summary is not detailed enough. "
                "Returns matching user questions and assistant answers from this session."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "search_text": {
                        "type": "string",
                        "description": "Keyword or phrase to search for in prior answers.",
                    },
                },
                "required": ["search_text"],
                "additionalProperties": False,
            },
        })
    _emit_progress(
        progress_callback,
        stage="tool_catalog_ready",
        message=f"Loaded {len(tool_catalog)} available modules.",
        progress=0.08,
        tool_count=len(tool_catalog),
    )
    # Pre-fetch SAA retained evidence so the planner has it in context
    # even if it doesn't call search_evidence explicitly.
    prefetched_context = ""
    _prefetch_limit = int(get_config_param(_P_PREFETCH_EVIDENCE_LIMIT))
    _prefetch_chunk_limit = int(get_config_param(_P_PREFETCH_CHUNK_TEXT_LIMIT))
    try:
        from .saa import search_retained_evidence_chunks
        prefetch_timeout_seconds = min(max(int(get_config_param(_P_TOOL_CALL_TIMEOUT_SECONDS)), 1), 15)
        saa_frame = _run_hidden_step_with_timeout(
            label="retained-evidence prefetch",
            timeout_seconds=prefetch_timeout_seconds,
            progress_callback=progress_callback,
            progress=0.085,
            func=lambda: search_retained_evidence_chunks(
                query=agent_query, limit=_prefetch_limit, use_semantic=False,
            ),
        )
        # If the full natural-language query returns nothing, use the LLM intent
        # classifier to extract domain-specific search keywords (e.g. "short squeeze")
        # and retry with those.  This replaces brittle hardcoded stop-word lists.
        if saa_frame.empty:
            try:
                from .omnibar_research import market_impact_map
                impact = _run_hidden_step_with_timeout(
                    label="prefetch keyword extraction",
                    timeout_seconds=min(max(int(get_config_param(_P_TOOL_CALL_TIMEOUT_SECONDS)), 1), 15),
                    progress_callback=progress_callback,
                    progress=0.09,
                    func=lambda: market_impact_map(query=agent_query),
                )
                search_kws = impact.get("search_keywords") or []
                reduced = " ".join(str(k).strip() for k in search_kws if str(k).strip())
            except Exception:
                reduced = ""
            if reduced:
                saa_frame = _run_hidden_step_with_timeout(
                    label="retained-evidence keyword prefetch",
                    timeout_seconds=prefetch_timeout_seconds,
                    progress_callback=progress_callback,
                    progress=0.1,
                    func=lambda: search_retained_evidence_chunks(
                        query=reduced, limit=_prefetch_limit, use_semantic=False,
                    ),
                )
        if not saa_frame.empty:
            lines = [f"### Internal evidence ({len(saa_frame)} matches):"]
            for _, row in saa_frame.head(_prefetch_limit).iterrows():
                title = str(row.get("title") or "").strip()
                date = str(row.get("published_date") or "").strip()
                tickers = str(row.get("mentioned_tickers_key") or "").strip()
                text = str(row.get("chunk_text") or "").strip()[:_prefetch_chunk_limit]
                line = f"- [{date}] {title}"
                if tickers:
                    line += f" [{tickers}]"
                if text:
                    line += f": {text}"
                lines.append(line)
            prefetched_context = "\n".join(lines)
    except Exception:
        prefetched_context = ""

    tool_calls: list[dict[str, Any]] = []
    seen_calls: set[str] = set()
    _run_start_time = time.monotonic()
    if prefetched_context:
        prefetch_call_id = "agtc_prefetch_1"
        _emit_progress(
            progress_callback,
            stage="tool_start",
            message="Checking retained internal evidence.",
            progress=0.09,
            tool_name="research.prefetched_context",
            tool_call_id=prefetch_call_id,
            tool_call_count=0,
            tool_arguments={"query": agent_query, "limit": _prefetch_limit},
        )
        tool_calls.append(
            {
                "tool_call_id": prefetch_call_id,
                "tool_name": "research.prefetched_context",
                "arguments": {"query": agent_query, "limit": _prefetch_limit},
                "status": "completed",
                "error": None,
                "result_summary": {
                    "preview_text": _truncate(prefetched_context, limit=800),
                    "user_preview": _truncate(prefetched_context, limit=int(get_config_param(_P_USER_PREVIEW_LIMIT))),
                    "llm_context_text": prefetched_context,
                    "result_type": "prefetched_context",
                    "provenance": {"source": "saa_prefetch"},
                    "preview": {"kind": "text", "chars": len(prefetched_context)},
                },
            }
        )
        _emit_progress(
            progress_callback,
            stage="tool_complete",
            message="Loaded retained internal evidence.",
            progress=0.1,
            tool_name="research.prefetched_context",
            tool_call_id=prefetch_call_id,
            tool_call_count=len(tool_calls),
            tool_arguments={"query": agent_query, "limit": _prefetch_limit},
            result_preview=_truncate(prefetched_context, limit=300),
        )
    seeded_tool_names: set[str] = set()
    bootstrap_budget = min(
        max(int(get_config_param(_P_BOOTSTRAP_TOOL_CALLS)), 0),
        max(int(max_tool_calls) - len(tool_calls), 0),
    )
    if bootstrap_budget > 0:
        bootstrap_plan = _bootstrap_tool_plan(
            query=agent_query,
            tool_catalog=tool_catalog,
            force_refresh=force_refresh,
            max_calls=bootstrap_budget,
        )
        if bootstrap_plan:
            _emit_progress(
                progress_callback,
                stage="bootstrap_start",
                message="Collecting obvious first evidence before planning.",
                progress=0.12,
                tool_call_count=len(tool_calls),
            )
        for idx, (seed_tool_name, seed_arguments) in enumerate(bootstrap_plan):
            seeded_tool_names.add(seed_tool_name)
            _execute_seeded_tool_call(
                service=resolved_service,
                run_id=run_id,
                tool_calls=tool_calls,
                progress_callback=progress_callback,
                tool_name=seed_tool_name,
                arguments=seed_arguments,
                progress=min(0.13 + (idx * 0.04), 0.28),
            )
    for call in tool_calls:
        seen_calls.add(f"{_clean(call.get('tool_name'))}::{_json_dumps(call.get('arguments') or {}, limit=1200)}")
    skip_planner_after_bootstrap = _should_skip_planner_after_bootstrap(
        agent_query,
        tool_calls,
        seeded_tool_names,
    )
    final_answer = ""
    final_confidence = "low"
    limitations: list[str] = []
    consecutive_failed_tools = 0

    try:
        total_steps = max(int(max_tool_calls), 1)
        planner_steps = 0 if skip_planner_after_bootstrap else total_steps
        if skip_planner_after_bootstrap:
            _emit_progress(
                progress_callback,
                stage="planner_skipped",
                message="Initial evidence is sufficient; moving straight to synthesis.",
                progress=0.9,
                tool_call_count=len(tool_calls),
            )
        for step_index in range(planner_steps):
            step_progress = 0.1 + ((step_index / total_steps) * 0.62)
            _emit_progress(
                progress_callback,
                stage="planner_start",
                message=f"Planning step {step_index + 1} of {total_steps}.",
                progress=step_progress,
                iteration=step_index + 1,
                tool_call_count=len(tool_calls),
            )
            # Run the LLM call in a background thread so we can emit
            # heartbeat events every few seconds while it thinks.
            _llm_result: list[dict[str, Any] | None] = [None]
            _llm_error: list[BaseException | None] = [None]

            def _run_planner() -> None:
                try:
                    _llm_result[0] = resolved_llm.generate_json(
                        system_prompt=_planner_system_prompt(),
                        user_prompt=_planner_user_prompt(
                            query=agent_query,
                            tool_catalog=tool_catalog,
                            tool_calls=tool_calls,
                            max_tool_calls=max_tool_calls,
                            conversation_history=conversation_history,
                            prefetched_context=prefetched_context,
                        ),
                        schema_name="omnibar_agent_step",
                        schema=_STEP_SCHEMA,
                    )
                except BaseException as exc:
                    _llm_error[0] = exc

            _planner_thread = threading.Thread(target=_run_planner, daemon=True)
            _planner_thread.start()
            _heartbeat_interval = 5.0
            _heartbeat_elapsed = 0.0
            while _planner_thread.is_alive():
                _planner_thread.join(timeout=_heartbeat_interval)
                if _planner_thread.is_alive():
                    _heartbeat_elapsed += _heartbeat_interval
                    if _heartbeat_elapsed >= llm_step_timeout_seconds:
                        timeout_error = TimeoutError(
                            f"Planner LLM step timed out after {llm_step_timeout_seconds}s."
                        )
                        _llm_error[0] = timeout_error
                        _emit_progress(
                            progress_callback,
                            stage="planner_timeout",
                            message=(
                                "The planning model step timed out; using the evidence already collected."
                            ),
                            progress=step_progress + 0.02,
                            iteration=step_index + 1,
                            elapsed_seconds=int(_heartbeat_elapsed),
                            tool_call_count=len(tool_calls),
                        )
                        break
                    _emit_progress(
                        progress_callback,
                        stage="planner_heartbeat",
                        message=f"Still thinking... ({int(_heartbeat_elapsed)}s)",
                        progress=step_progress + 0.01,
                        iteration=step_index + 1,
                        elapsed_seconds=int(_heartbeat_elapsed),
                        tool_call_count=len(tool_calls),
                    )
            if _llm_error[0] is not None:
                if isinstance(_llm_error[0], TimeoutError) and any(
                    str(call.get("status") or "") == "completed" for call in tool_calls
                ):
                    limitations.append("Stopped planning after a timed-out model step; synthesizing from collected evidence.")
                    if _needs_live_evidence_recovery(tool_calls):
                        recovery_tool_name = "research.live_event_evidence"
                        recovery_call_id = f"agtc_{len(tool_calls) + 1}"
                        recovery_arguments = {
                            "query": agent_query,
                            "max_results": 6,
                            "force_refresh": bool(force_refresh),
                        }
                        _emit_progress(
                            progress_callback,
                            stage="tool_start",
                            message="Recovering with live evidence after the planner timed out.",
                            progress=min(step_progress + 0.05, 0.88),
                            tool_name=recovery_tool_name,
                            tool_call_id=recovery_call_id,
                            tool_call_count=len(tool_calls),
                            tool_arguments=recovery_arguments,
                        )
                        try:
                            recovery_result = _invoke_tool_with_heartbeat(
                                service=resolved_service,
                                tool_name=recovery_tool_name,
                                arguments=recovery_arguments,
                                run_id=run_id,
                                progress_callback=progress_callback,
                                progress=min(step_progress + 0.05, 0.88),
                                tool_call_id=recovery_call_id,
                                tool_call_count=len(tool_calls),
                            )
                            recovery_summary = _summarize_tool_result(recovery_result)
                            tool_calls.append(
                                {
                                    "tool_call_id": recovery_call_id,
                                    "tool_name": recovery_tool_name,
                                    "arguments": recovery_arguments,
                                    "status": "completed",
                                    "error": None,
                                    "result_summary": recovery_summary,
                                }
                            )
                            _emit_progress(
                                progress_callback,
                                stage="tool_complete",
                                message="Collected recovery evidence from live search.",
                                progress=min(step_progress + 0.14, 0.92),
                                tool_name=recovery_tool_name,
                                tool_call_id=recovery_call_id,
                                tool_call_count=len(tool_calls),
                                tool_arguments=recovery_arguments,
                                result_preview=_truncate(recovery_summary.get("user_preview") or recovery_summary.get("preview_text") or "", limit=300),
                                render_payload=recovery_summary.get("render_payload"),
                                source_links=recovery_summary.get("source_links"),
                            )
                        except Exception as exc:
                            tool_calls.append(
                                {
                                    "tool_call_id": recovery_call_id,
                                    "tool_name": recovery_tool_name,
                                    "arguments": recovery_arguments,
                                    "status": "failed",
                                    "error": f"{type(exc).__name__}: {exc}",
                                    "result_summary": {
                                        "preview_text": f"{recovery_tool_name} failed: {exc}",
                                        "result_type": "error",
                                        "provenance": None,
                                        "preview": {"kind": "error"},
                                    },
                                }
                            )
                            limitations.append(f"{recovery_tool_name} recovery failed: {type(exc).__name__}.")
                    break
                raise _llm_error[0]
            decision = _llm_result[0]
            assert decision is not None
            planner_reasoning_trace = _clean(decision.get("__reasoning_content"))
            if planner_reasoning_trace:
                _emit_progress(
                    progress_callback,
                    stage="model_reasoning_trace",
                    message="Model reasoning trace captured.",
                    progress=step_progress + 0.015,
                    iteration=step_index + 1,
                    reasoning_trace=planner_reasoning_trace,
                )

            action = _clean(decision.get("action")).lower()
            reasoning = _clean(decision.get("reasoning"))
            if reasoning:
                _emit_progress(
                    progress_callback,
                    stage="planner_reasoning",
                    message=reasoning,
                    progress=step_progress + 0.02,
                    iteration=step_index + 1,
                    reasoning=reasoning,
                )
            if action == "final":
                _emit_progress(
                    progress_callback,
                    stage="planner_final",
                    message="Final answer is ready to render.",
                    progress=min(step_progress + 0.08, 0.9),
                    iteration=step_index + 1,
                    reasoning=reasoning,
                )
                final_answer = _clean(decision.get("answer_markdown"))
                final_confidence = _clean(decision.get("confidence")).lower() or "low"
                if final_answer:
                    break

            if action != "tool_call":
                limitations.append(f"Unexpected planner action: {action or 'missing'}.")
                break

            tool_name = _clean(decision.get("tool_name"))
            tool_entry = _tool_entry_by_name(tool_catalog, tool_name)
            call_id = f"agtc_{len(tool_calls) + 1}"
            raw_arguments = decision.get("tool_arguments")
            parsed_arguments, argument_error = _coerce_tool_arguments(raw_arguments)
            if argument_error:
                tool_calls.append(
                    {
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "arguments": {},
                        "status": "failed",
                        "error": argument_error,
                        "result_summary": {
                            "preview_text": argument_error,
                            "result_type": "error",
                            "provenance": None,
                            "preview": {"kind": "error"},
                        },
                    }
                )
                limitations.append(argument_error)
                consecutive_failed_tools += 1
                if consecutive_failed_tools >= 2 and any(
                    str(call.get("status") or "") == "completed" for call in tool_calls
                ):
                    limitations.append("Stopping planning after repeated failed tool attempts.")
                    break
                continue
            arguments = _normalized_arguments(
                tool_entry,
                parsed_arguments,
                force_refresh=force_refresh,
            )
            dedupe_signature = f"{tool_name}::{_json_dumps(arguments, limit=1200)}"
            if dedupe_signature in seen_calls:
                tool_calls.append(
                    {
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "status": "failed",
                        "error": "Duplicate tool call requested by planner.",
                        "result_summary": {
                            "preview_text": "Duplicate tool call blocked.",
                            "result_type": "error",
                            "provenance": None,
                            "preview": {"kind": "error"},
                        },
                    }
                )
                limitations.append(f"Planner repeated the same tool call: {tool_name}.")
                consecutive_failed_tools += 1
                if consecutive_failed_tools >= 2 and any(
                    str(call.get("status") or "") == "completed" for call in tool_calls
                ):
                    limitations.append("Stopping planning after repeated failed tool attempts.")
                    break
                continue
            seen_calls.add(dedupe_signature)

            if not tool_entry:
                tool_calls.append(
                    {
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "status": "failed",
                        "error": f"Unsupported tool '{tool_name}'.",
                        "result_summary": {
                            "preview_text": f"Unsupported tool '{tool_name}'.",
                            "result_type": "error",
                            "provenance": None,
                            "preview": {"kind": "error"},
                        },
                    }
                )
                limitations.append(f"Planner selected unsupported tool '{tool_name}'.")
                consecutive_failed_tools += 1
                if consecutive_failed_tools >= 2 and any(
                    str(call.get("status") or "") == "completed" for call in tool_calls
                ):
                    limitations.append("Stopping planning after repeated failed tool attempts.")
                    break
                continue

            _emit_progress(
                progress_callback,
                stage="tool_start",
                message=f"Collecting data from {tool_name}.",
                progress=min(step_progress + 0.06, 0.9),
                tool_name=tool_name,
                tool_call_id=call_id,
                tool_call_count=len(tool_calls),
                tool_arguments=arguments,
            )
            try:
                if tool_name == "conversation.prior_answers":
                    result = _search_conversation_history(
                        conversation_history or [],
                        str((arguments or {}).get("search_text") or ""),
                    )
                else:
                    result = _invoke_tool_with_heartbeat(
                        service=resolved_service,
                        tool_name=tool_name,
                        arguments=arguments,
                        run_id=run_id,
                        progress_callback=progress_callback,
                        progress=min(step_progress + 0.06, 0.9),
                        tool_call_id=call_id,
                        tool_call_count=len(tool_calls),
                    )
                result_summary = _summarize_tool_result(result)
                tool_calls.append(
                    {
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "status": "completed",
                        "error": None,
                        "result_summary": result_summary,
                    }
                )
                consecutive_failed_tools = 0
                _emit_progress(
                    progress_callback,
                    stage="tool_complete",
                    message=f"Collected evidence from {tool_name}.",
                    progress=min(step_progress + 0.14, 0.92),
                    tool_name=tool_name,
                    tool_call_id=call_id,
                    tool_call_count=len(tool_calls),
                    tool_arguments=arguments,
                    result_preview=_truncate(result_summary.get("user_preview") or result_summary.get("preview_text") or "", limit=300),
                    render_payload=result_summary.get("render_payload"),
                    source_links=result_summary.get("source_links"),
                )
            except Exception as exc:
                tool_calls.append(
                    {
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                        "result_summary": {
                            "preview_text": f"{type(exc).__name__}: {exc}",
                            "result_type": "error",
                            "provenance": None,
                            "preview": {"kind": "error"},
                        },
                    }
                )
                limitations.append(f"{tool_name} failed: {type(exc).__name__}.")
                consecutive_failed_tools += 1
                _emit_progress(
                    progress_callback,
                    stage="tool_failed",
                    message=f"{tool_name} failed. Trying the next best step.",
                    progress=min(step_progress + 0.1, 0.9),
                    tool_name=tool_name,
                    tool_call_id=call_id,
                )
                if consecutive_failed_tools >= 2 and any(
                    str(call.get("status") or "") == "completed" for call in tool_calls
                ):
                    limitations.append("Stopping planning after repeated failed tool attempts.")
                    break

        if not final_answer:
            _emit_progress(
                progress_callback,
                stage="final_synthesis_start",
                message="Synthesizing the final answer from collected evidence.",
                progress=0.94,
                tool_call_count=len(tool_calls),
            )
            _final_result: list[dict[str, Any] | None] = [None]
            _final_error: list[BaseException | None] = [None]
            synthesis_llm = _synthesis_llm_client(resolved_llm)

            def _run_final_synthesis() -> None:
                try:
                    _final_result[0] = synthesis_llm.generate_json(
                        system_prompt=get_prompt(_AGENT_FINAL_SYSTEM_PROMPT),
                        user_prompt=_final_user_prompt(query=agent_query, tool_calls=tool_calls, conversation_history=conversation_history, prefetched_context=prefetched_context),
                        schema_name="omnibar_agent_final",
                        schema=_FINAL_SCHEMA,
                    )
                except BaseException as exc:
                    _final_error[0] = exc

            _final_thread = threading.Thread(target=_run_final_synthesis, daemon=True)
            _final_thread.start()
            _heartbeat_interval = 5.0
            _heartbeat_elapsed = 0.0
            while _final_thread.is_alive():
                _final_thread.join(timeout=_heartbeat_interval)
                if _final_thread.is_alive():
                    _heartbeat_elapsed += _heartbeat_interval
                    if _heartbeat_elapsed >= llm_step_timeout_seconds:
                        _final_error[0] = TimeoutError(
                            f"Final synthesis LLM step timed out after {llm_step_timeout_seconds}s."
                        )
                        _emit_progress(
                            progress_callback,
                            stage="final_synthesis_timeout",
                            message="The final synthesis model step timed out; returning the collected evidence state.",
                            progress=0.97,
                            elapsed_seconds=int(_heartbeat_elapsed),
                            tool_call_count=len(tool_calls),
                        )
                        break
                    _emit_progress(
                        progress_callback,
                        stage="final_synthesis_heartbeat",
                        message=f"Still synthesizing... ({int(_heartbeat_elapsed)}s)",
                        progress=0.95,
                        elapsed_seconds=int(_heartbeat_elapsed),
                        tool_call_count=len(tool_calls),
                    )
            if _final_error[0] is not None:
                raise _final_error[0]
            final_payload = _final_result[0]
            assert final_payload is not None
            final_reasoning_trace = _clean(final_payload.get("__reasoning_content"))
            if final_reasoning_trace:
                _emit_progress(
                    progress_callback,
                    stage="model_reasoning_trace",
                    message="Final model reasoning trace captured.",
                    progress=0.965,
                    reasoning_trace=final_reasoning_trace,
                )
            final_answer = _clean(final_payload.get("answer_markdown"))
            final_confidence = _clean(final_payload.get("confidence")).lower() or "low"
            final_limitations = [
                _clean(item)
                for item in list(final_payload.get("limitations") or [])
                if _clean(item)
            ]
            for item in final_limitations:
                if item not in limitations:
                    limitations.append(item)
    except Exception as exc:
        if not isinstance(exc, LLMAPIError) and not _looks_like_transient_transport_error(exc):
            raise
        duration = time.monotonic() - _run_start_time
        safe_error = _safe_agent_error_text(exc)
        _emit_progress(
            progress_callback,
            stage="failed",
            message=safe_error,
            progress=1.0,
            error=safe_error,
        )
        error_text = safe_error
        limitation = f"LLM error: {safe_error}" if isinstance(exc, LLMAPIError) else safe_error
        error_result = {
            "run_id": run_id,
            "status": "failed",
            "mode": "sync",
            "model": str(resolved_llm.config.model),
            "tool_calls": tool_calls,
            "answer_markdown": _fallback_answer(agent_query, tool_calls),
            "confidence": "low",
            "limitations": limitations + [limitation],
            "error": error_text,
            "query": agent_query,
            "original_query": normalized_query,
            "followup_resolved": followup_resolved,
        }
        if persist_findings:
            _persist_agent_findings(
                run_id=run_id,
                query=agent_query,
                status="failed",
                model=str(resolved_llm.config.model),
                answer=error_result["answer_markdown"],
                confidence="low",
                tool_calls=tool_calls,
                limitations=error_result["limitations"],
                error=error_text,
                duration_seconds=duration,
                service=resolved_service,
            )
        return error_result

    answer_markdown = final_answer or _fallback_answer(agent_query, tool_calls)
    status = "completed" if answer_markdown else "failed"
    _emit_progress(
        progress_callback,
        stage=status,
        message="Agent response ready." if status == "completed" else "Agent run ended without an answer.",
        progress=1.0,
        tool_call_count=len(tool_calls),
        status=status,
    )
    duration = time.monotonic() - _run_start_time
    error_text = None if status == "completed" else "Agent did not produce an answer."
    result = {
        "run_id": run_id,
        "status": status,
        "mode": "sync",
        "model": str(resolved_llm.config.model),
        "synthesis_model": str(getattr(getattr(_synthesis_llm_client(resolved_llm), "config", object()), "model", resolved_llm.config.model)),
        "tool_calls": tool_calls,
        "answer_markdown": answer_markdown,
        "confidence": final_confidence,
        "limitations": limitations,
        "error": error_text,
        "query": agent_query,
        "original_query": normalized_query,
        "followup_resolved": followup_resolved,
    }
    if persist_findings:
        _persist_agent_findings(
            run_id=run_id,
            query=agent_query,
            status=status,
            model=str(resolved_llm.config.model),
            answer=answer_markdown,
            confidence=final_confidence,
            tool_calls=tool_calls,
            limitations=limitations,
            error=error_text,
            duration_seconds=duration,
            service=resolved_service,
        )
    return result


def _persist_agent_findings(
    *,
    run_id: str,
    query: str,
    status: str,
    model: str,
    answer: str,
    confidence: str,
    tool_calls: list[dict[str, Any]],
    limitations: list[str] | None = None,
    error: str | None = None,
    duration_seconds: float | None = None,
    service: QueryService,
) -> None:
    """Persist agent session to the durable AQL chat log and the ephemeral scratchpad."""
    # --- Durable chat log (Postgres + blob) ---
    try:
        from .agents import log_chat_session

        log_chat_session(
            run_id=run_id,
            query=query,
            status=status,
            model=model,
            confidence=confidence,
            answer_markdown=answer,
            tool_calls=tool_calls,
            limitations=limitations,
            error=error,
            duration_seconds=duration_seconds,
        )
    except Exception:
        pass

    # --- Ephemeral scratchpad (for within-session retained_context) ---
    claims: list[str] = []
    symbols: set[str] = set()
    try:
        from .agents import write_entry

        for call in tool_calls:
            if str(call.get("status") or "") != "completed":
                continue
            result_summary = dict(call.get("result_summary") or {})
            context_text = _clean(result_summary.get("llm_context_text"))
            if context_text:
                claims.append(context_text)
            args = dict(call.get("arguments") or {})
            for key in ("symbols", "focus_symbols", "ticker"):
                val = args.get(key)
                if isinstance(val, list):
                    symbols.update(str(s).upper().strip() for s in val if str(s).strip())
                elif isinstance(val, str) and val.strip():
                    symbols.add(val.upper().strip())

        write_entry(
            run_id=run_id,
            kind="agent_result",
            content={
                "query": query,
                "answer": answer,
                "confidence": confidence,
                "symbols": sorted(symbols),
                "claim_count": len(claims),
                "tool_call_count": len(tool_calls),
            },
        )
    except Exception:
        pass

    # --- Write-back to retained evidence store (for future retained_context lookups) ---
    if status == "completed" and (claims or answer):
        try:
            _write_back_agent_evidence(
                run_id=run_id,
                query=query,
                answer=answer,
                claims=claims,
                symbols=sorted(symbols),
            )
        except Exception:
            pass


def _write_back_agent_evidence(
    *,
    run_id: str,
    query: str,
    answer: str,
    claims: list[str],
    symbols: list[str],
) -> None:
    """Write agent findings as evidence chunks to the SAA store for future retained_context lookups."""
    from services.saa import persist_agent_research_evidence

    persist_agent_research_evidence(run_id=run_id, query=query, answer=answer, claims=claims, symbols=symbols)


__all__ = [
    "DEFAULT_MAX_TOOL_CALLS",
    "resolve_conversation_followup_query",
    "run_omnibar_agent",
]
