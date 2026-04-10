from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from data_access.query_service import QueryService
from .agent_tools import build_tool_catalog, invoke_tool
from .llm import (
    AzureOpenAIChatJSONClient,
    LLMAPIError,
    OpenAIChatJSONClient,
    load_llm_client,
)


LLMClient = OpenAIChatJSONClient | AzureOpenAIChatJSONClient
ProgressCallback = Callable[[dict[str, Any]], None]
DEFAULT_MAX_TOOL_CALLS = 6
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

    return {
        "result_type": result_type,
        "provenance": provenance,
        "preview": preview,
        "preview_text": preview_text,
        "render_payload": render_payload,
    }


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
        lines.append(
            f"- {tool_call['tool_call_id']} | {tool_call['status']} | {tool_call['tool_name']} | "
            f"args={_json_dumps(tool_call.get('arguments') or {}, limit=500)} | "
            f"result={_truncate(tool_call.get('result_summary', {}).get('preview_text') or '', limit=1200)}"
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


def _planner_system_prompt() -> str:
    return (
        "You are the Spectral Nature omnibar agent. "
        "Answer the user's question by calling the available tools when needed. "
        "Use tools for factual retrieval, comparisons, macro releases, company context, portfolio context, "
        "technicals, options, and charts. "
        "Do not invent data. "
        "Prefer the narrowest tool that directly answers the question. "
        "If the answer needs evidence, call a tool before answering. "
        "Once a tool result directly contains the requested fact, stop and answer. "
        "Do not repeat a tool with materially identical arguments. "
        "Avoid broad query tools when a domain-specific dataset already returned the needed value. "
        "When you have enough evidence, return action='final' with a concise markdown answer grounded only in tool results."
    )


def _planner_user_prompt(
    *,
    query: str,
    tool_catalog: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    max_tool_calls: int,
) -> str:
    return (
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
) -> str:
    return (
        f"User question:\n{query}\n\n"
        "Tool evidence:\n"
        f"{_tool_history_prompt(tool_calls)}\n\n"
        "Write the best grounded markdown answer you can from this evidence only. "
        "If the evidence is incomplete, say what is missing."
    )


def run_omnibar_agent(
    *,
    query: str,
    force_refresh: bool = False,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    service: QueryService | None = None,
    llm_client: LLMClient | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    normalized_query = _clean(query)
    run_id = f"agrun_{uuid.uuid4().hex[:10]}"
    if not normalized_query:
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
    resolved_llm = llm_client or load_llm_client()
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
    tool_catalog = build_tool_catalog(resolved_service)
    _emit_progress(
        progress_callback,
        stage="tool_catalog_ready",
        message=f"Loaded {len(tool_catalog)} available modules.",
        progress=0.08,
        tool_count=len(tool_catalog),
    )
    tool_calls: list[dict[str, Any]] = []
    seen_calls: set[str] = set()
    final_answer = ""
    final_confidence = "low"
    limitations: list[str] = []
    consecutive_failed_tools = 0

    try:
        total_steps = max(int(max_tool_calls), 1)
        for step_index in range(total_steps):
            step_progress = 0.1 + ((step_index / total_steps) * 0.62)
            _emit_progress(
                progress_callback,
                stage="planner_start",
                message=f"Planning step {step_index + 1} of {total_steps}.",
                progress=step_progress,
                iteration=step_index + 1,
                tool_call_count=len(tool_calls),
            )
            decision = resolved_llm.generate_json(
                system_prompt=_planner_system_prompt(),
                user_prompt=_planner_user_prompt(
                    query=normalized_query,
                    tool_catalog=tool_catalog,
                    tool_calls=tool_calls,
                    max_tool_calls=max_tool_calls,
                ),
                schema_name="omnibar_agent_step",
                schema=_STEP_SCHEMA,
            )

            action = _clean(decision.get("action")).lower()
            if action == "final":
                _emit_progress(
                    progress_callback,
                    stage="planner_final",
                    message="Final answer is ready to render.",
                    progress=min(step_progress + 0.08, 0.9),
                    iteration=step_index + 1,
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
            )
            try:
                result = invoke_tool(
                    service=resolved_service,
                    tool_name=tool_name,
                    arguments=arguments,
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
            final_payload = resolved_llm.generate_json(
                system_prompt=(
                    "You are the Spectral Nature omnibar agent. "
                    "Write a grounded markdown answer from the collected tool evidence only."
                ),
                user_prompt=_final_user_prompt(query=normalized_query, tool_calls=tool_calls),
                schema_name="omnibar_agent_final",
                schema=_FINAL_SCHEMA,
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
    except LLMAPIError as exc:
        _emit_progress(
            progress_callback,
            stage="failed",
            message="The omnibar agent hit an LLM error.",
            progress=1.0,
            error=str(exc),
        )
        return {
            "run_id": run_id,
            "status": "failed",
            "mode": "sync",
            "model": str(resolved_llm.config.model),
            "tool_calls": tool_calls,
            "answer_markdown": _fallback_answer(normalized_query, tool_calls),
            "confidence": "low",
            "limitations": limitations + [f"LLM error: {exc}"],
            "error": f"{type(exc).__name__}: {exc}",
        }

    answer_markdown = final_answer or _fallback_answer(normalized_query, tool_calls)
    status = "completed" if answer_markdown else "failed"
    _emit_progress(
        progress_callback,
        stage=status,
        message="Agent response ready." if status == "completed" else "Agent run ended without an answer.",
        progress=1.0,
        tool_call_count=len(tool_calls),
        status=status,
    )
    return {
        "run_id": run_id,
        "status": status,
        "mode": "sync",
        "model": str(resolved_llm.config.model),
        "tool_calls": tool_calls,
        "answer_markdown": answer_markdown,
        "confidence": final_confidence,
        "limitations": limitations,
        "error": None if status == "completed" else "Agent did not produce an answer.",
    }


__all__ = [
    "DEFAULT_MAX_TOOL_CALLS",
    "run_omnibar_agent",
]
