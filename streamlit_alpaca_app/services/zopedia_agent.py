from __future__ import annotations

from services.work_scope import WorkScope

from contextvars import copy_context
from .llm_execution import LLMContextWindowExceeded, research_run_context

from dataclasses import replace

import hashlib
import json
import re
import threading
import time
import uuid
from jsonschema import Draft202012Validator
from typing import Any, Callable
from urllib.parse import urlparse

from data_access.query_service import QueryService
from .aql.evidence_pack import build_aql_evidence_pack
from .agent_tools import ToolRegistry, build_tool_catalog, build_tool_registry, invoke_tool
from .aql_zopedia_gateway import generate_json_via_aql_zopedia_gateway
from .llm import (
    JSONLLMClient,
    LLMAPIError,
    llm_request_deadline,
    get_config_param,
    get_prompt,
    register_config_param,
    register_narrative_prompt,
)
from .aql_zopedia_engine import (
    load_aql_zopedia_llm_client,
    _P_LLM_STEP_TIMEOUT_SECONDS,
    _P_TOOL_CALL_TIMEOUT_SECONDS,
)
from .agents.research_session import (
    ResearchBudget,
    ResearchStepTimeout,
    research_request_context,
)
from .llm_context import serialize_llm_context


LLMClient = JSONLLMClient
ProgressCallback = Callable[[dict[str, Any]], None]
DEFAULT_MAX_TOOL_CALLS = 8


def _is_public_web_source_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return False
    return not host.endswith((".local", ".localhost", ".test", ".invalid"))

# --- Configurable limits (exposed in Admin > LLM Config > Tuning Parameters) ---
_P_TOOL_RESULT_CONTEXT_LIMIT = register_config_param(
    "Agent tool result context limit",
    group="Zopedia",
    default=4000,
    description="Max chars for the LLM context text extracted from a single tool result",
)
_P_TOOL_RESULT_SUMMARY_LIMIT = register_config_param(
    "Agent tool result summary limit",
    group="Zopedia",
    default=3500,
    description="Max chars for the summary-based LLM context when no explicit context text is available",
)
_P_TOOL_RESULT_SUMMARY_ITEMS = register_config_param(
    "Agent tool result summary items",
    group="Zopedia",
    default=8,
    description="Max summary items to include per tool result",
)
_P_TOOL_HISTORY_PER_TOOL_LIMIT = register_config_param(
    "Agent tool history per-tool limit",
    group="Zopedia",
    default=3000,
    description="Max chars per tool result in the tool call history shown to the planner",
)
_P_BOOTSTRAP_TOOL_CALLS = register_config_param(
    "Agent bootstrap tool calls",
    group="Zopedia",
    default=0,
    description=(
        "Legacy deterministic evidence-call budget. Defaults to 0 so the LLM planner owns tool choice."
    ),
)
_P_USER_PREVIEW_LIMIT = register_config_param(
    "Agent user preview limit",
    group="Zopedia",
    default=200,
    description="Max chars for the user-facing preview of a tool result",
)
_P_ANSWER_JUDGE_ENABLED = register_config_param(
    "Agent answer judge enabled",
    group="Zopedia",
    default=1,
    description="Run bounded editorial refinement of research relevance, explanation and factual accuracy",
)
_P_TRAJECTORY_MONITOR_EFFORT = register_config_param(
    "Trajectory monitor reasoning effort", group="Zopedia Agent", default="low",
    description="Reasoning effort for execution trajectory checks; substantive research keeps its own effort.",
    choices=("none", "low", "high", "max"),
)
_P_TRAJECTORY_MONITOR_ENABLED = register_config_param(
    "Agent trajectory monitor enabled",
    group="Zopedia",
    default=1,
    description="Run a bounded LLM monitor that catches off-contract tool wandering and can restart or kill the thread.",
)
_P_TRAJECTORY_MONITOR_MAX_RESTARTS = register_config_param(
    "Agent trajectory monitor max restarts",
    group="Zopedia",
    default=2,
    description="Maximum corrective restarts one agent run can receive from the trajectory monitor.",
)
_P_TRAJECTORY_MONITOR_TIMEOUT_SECONDS = register_config_param(
    "Agent trajectory monitor timeout seconds",
    group="Zopedia",
    default=45,
    description="Maximum seconds a trajectory monitor check may spend before letting the planner continue.",
)
_P_POST_ANSWER_MEMORY_ENABLED = register_config_param(
    "Agent post-answer memory enabled",
    group="Zopedia",
    default=1,
    description="After a grounded answer, let the agent apply safe Zopedia memory updates or create review proposals",
)
_P_DEFERRED_TOOL_DISCOVERY_ENABLED = register_config_param(
    "Agent deferred tool discovery enabled",
    group="Zopedia",
    default=1,
    description=("Expose shared research primitives and discover other tool definitions on demand."),
)
_P_TOOL_DISCOVERY_MAX_STEPS = register_config_param(
    "Agent tool discovery max steps",
    group="Zopedia",
    default=2,
    description="Extra planner iterations reserved for free tool-registry searches when deferred discovery is enabled.",
)

_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": "Choose a research action. Tool requests require action, tool_name and typed tool_arguments. Final decisions require action, answer_markdown and answer confidence. Progress, research state and policy fields are optional updates; omitted or null updates preserve the current owner state and permissions.",
    "properties": {
        "action": {"type": "string", "enum": ["tool_call", "final"]},
        "reasoning": {"type": ["string", "null"]},
        "tool_name": {"type": ["string", "null"]},
        "tool_arguments": {
            "type": ["object", "null"],
            "additionalProperties": True,
            "description": "Arguments matching the chosen tool input schema, with their original types. Use an empty object for no arguments.",
        },
        "answer_markdown": {"type": ["string", "null"]},
        "confidence": {
            "type": ["string", "null"],
            "description": "Confidence in a delivered answer. Tool requests may omit this or use null/none because no answer is being delivered. Final answers require low, medium or high.",
        },
        "needs_more_tools": {"type": ["boolean", "null"]},
        "public_update": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "summary": {"type": ["string", "null"]},
                "finding": {"type": ["string", "null"]},
                "next_step": {"type": ["string", "null"]},
            },
        },
        "research_state": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "goal": {"type": ["string", "null"]},
                "findings": {"type": ["array", "null"], "items": {"type": "string"}},
                "answer_points": {"type": ["array", "null"], "items": {"type": "string"}},
                "selected_source_ids": {"type": ["array", "null"], "items": {"type": "string"}},
                "alternatives": {"type": ["array", "null"], "items": {"type": "string"}},
                "open_questions": {"type": ["array", "null"], "items": {"type": "string"}},
                "next_actions": {"type": ["array", "null"], "items": {"type": "string"}},
            },
        },
        "source_policy": {"type": ["string", "null"], "enum": [None, "available", "retained_only"]},
        "knowledge_policy": {"type": ["string", "null"], "enum": [None, "inherit", "none", "propose"]},
        "request_review": {"type": ["boolean", "null"]},
    },
    "required": ["action"],
    "allOf": [{
        "if": {"properties": {"action": {"const": "tool_call"}}},
        "then": {
            "required": ["tool_name", "tool_arguments"],
            "properties": {
                "tool_name": {"type": "string", "minLength": 1},
                "tool_arguments": {"type": "object"},
            },
        },
    }, {
        "if": {"properties": {"action": {"const": "final"}}},
        "then": {
            "required": ["answer_markdown", "confidence"],
            "properties": {
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "answer_markdown": {"type": "string", "minLength": 1},
            },
        },
    }],
}

_AGENT_FINAL_SYSTEM_PROMPT = register_narrative_prompt(
    name="Zopedia Agent Final Answer (markdown response to user)",
    file="services/zopedia_agent.py",
    group="Zopedia",
    prompt=(
        "Answer the user's question clearly and directly from the supplied evidence and analysis. Preserve the figures, units, dates and assumptions. Check that the conclusion matches any calculation; correct a draft when the original evidence contradicts it. Do not invent missing facts or claim work that was not performed. Give a useful conditional answer when needed. Cite supporting sources using supplied titles and URLs. Keep internal notes private. Follow the requested format and length."
    ),
)

_FINAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answer_markdown": {"type": "string"},
    },
    "required": [
        "answer_markdown",
    ],
}

_AGENT_JUDGE_SYSTEM_PROMPT = register_narrative_prompt(
    name="Zopedia Agent Answer Judge (evidence sufficiency review)",
    file="services/zopedia_agent.py",
    group="Zopedia",
    prompt=(
        'Check the proposed answer against the original evidence and actual calculation results. Look for invented figures, wrong units or dates, a wrong denominator, contradictions and claims of work that did not happen. Read a needed original if it is not visible. Correct material errors and return the best supported answer and matching answer points. Preserve useful assumptions and alternative interpretations when clearly stated. Accept a sound answer without adding process commentary or generic caveats. Request more research only when it could materially change the answer.'
    ),
)

_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "revise", "insufficient"]},
        "critique_summary": {"type": "string"},
        "answer_points": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "research_requests": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "verdict",
        "critique_summary",
        "answer_points",
        "confidence",
        "research_requests",
    ],
}

_TRAJECTORY_MONITOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["double_down", "restart", "kill"]},
        "reason": {"type": "string"},
        "corrective_instruction": {"type": "string"},
        "off_contract_signals": {"type": "array", "items": {"type": "string"}},
        "evidence_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "decision",
        "reason",
        "corrective_instruction",
        "off_contract_signals",
        "evidence_gaps",
    ],
}

_TRAJECTORY_MONITOR_SYSTEM_PROMPT = register_narrative_prompt(
    name="Zopedia Agent Trajectory Monitor (wandering control)",
    file="services/zopedia_agent.py",
    group="Zopedia",
    prompt=(
        "Check whether the current work helps answer the user's question. Continue useful research. If it has wandered or repeated a failed action, give one clear corrective instruction using available tools. Missing or conflicting evidence calls for a useful next step or a conditional answer. Stop only for clear fabrication, unrecoverable repetition or a permission breach. Judge the actual recorded actions; a planned call has not yet run."
    ),
)

_AGENT_MEMORY_SYSTEM_PROMPT = register_narrative_prompt(
    name="Zopedia Agent Memory Reflection (automatic wiki maintenance)",
    file="services/zopedia_agent.py",
    group="Zopedia",
    prompt=(
        f"You are the Spectral Nature Zopedia memory maintainer. "
        "Review the answered question, the final answer, the judge review, and collected evidence. "
        "Decide whether the underlying Zopedia wiki should be updated. "
        "The active write policy in the user prompt is binding. "
        "Use no_action only when the evidence is too thin, purely conversational, duplicative, or not durable. "
        "When write_policy=safe_auto, prefer apply_mutation for durable source-backed company, source, theme, market-event, or macro pages that can be written as safe reversible page upserts, metadata patches, or page links. "
        "The only safe mutation_type values are upsert_pages, link_pages, and metadata_patch. Use upsert_pages exactly; never use upsert or upsert_page. "
        "For upsert_pages, put complete page objects in pages, not loose article text in payload. "
        "When write_policy=propose, create a proposal for durable memory instead of applying a mutation. "
        "Use propose_change for destructive, ambiguous, low-confidence, merge, delete, rewrite, stale-fact, or maintenance-style changes. "
        "Do not invent facts. Do not write memory from the user's premise unless collected evidence supports it. "
        "Automatic memory updates must keep allow_risky false."
    ),
)

_SAFE_MEMORY_MUTATION_TYPES = {"upsert_pages", "link_pages", "metadata_patch"}
_MEMORY_PAGE_TYPES = ["source", "concept", "entity", "theme", "market_event", "ticker", "macro", "question", "index"]
_MEMORY_PAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "page_id": {"type": "string"},
        "page_type": {"type": "string", "enum": _MEMORY_PAGE_TYPES},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "body_markdown": {"type": "string"},
        "source_urls": {"type": "array", "items": {"type": "string"}},
        "source_document_ids": {"type": "array", "items": {"type": "string"}},
        "entity_refs": {"type": "array", "items": {"type": "string"}},
        "outgoing_links": {"type": "array", "items": {"type": "string"}},
        "metadata": {"type": "object"},
    },
    "required": ["page_type", "title", "summary", "body_markdown", "source_urls", "entity_refs", "metadata"],
}

_MEMORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["no_action", "apply_mutation", "propose_change"]},
        "rationale": {"type": "string"},
        "mutation_type": {"type": "string", "enum": ["", *sorted(_SAFE_MEMORY_MUTATION_TYPES)]},
        "proposal_type": {"type": "string"},
        "page_id": {"type": "string"},
        "target_page_id": {"type": "string"},
        "title": {"type": "string"},
        "pages": {"type": "array", "items": _MEMORY_PAGE_SCHEMA},
        "metadata_patch": {"type": "object"},
        "evidence_refs": {"type": "array", "items": {"type": "object"}},
        "payload": {"type": "object"},
        "allow_risky": {"type": "boolean"},
    },
    "required": [
        "action",
        "rationale",
        "mutation_type",
        "proposal_type",
        "page_id",
        "target_page_id",
        "title",
        "pages",
        "metadata_patch",
        "evidence_refs",
        "payload",
        "allow_risky",
    ],
}


_ZOPEDIA_WRITE_POLICIES = {"none", "propose", "safe_auto"}


def _normalize_zopedia_write_policy(value: object, *, persist_findings: bool = True) -> str:
    policy = _clean(value).lower()
    if not policy:
        return "safe_auto" if persist_findings else "none"
    if policy in {"off", "disabled", "read_only", "readonly"}:
        return "none"
    if policy in {"proposal", "review"}:
        return "propose"
    if policy in {"auto", "commit", "safe", "safe-auto"}:
        return "safe_auto"
    if policy in _ZOPEDIA_WRITE_POLICIES:
        return policy
    return "safe_auto" if persist_findings else "none"


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_model_output(value: object) -> str:
    """Preserve generated prose and Markdown; normalize only transport line endings."""
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


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


def _with_aql_evidence_pack(
    result: dict[str, Any],
    *,
    surface: str = "zopedia_agent",
) -> dict[str, Any]:
    """Attach the shared AQL evidence-pack contract to an agent result."""
    tool_calls = result.get("tool_calls")
    result["aql_evidence_pack"] = build_aql_evidence_pack(
        run_id=_clean(result.get("run_id")),
        query=_clean(result.get("query")),
        original_query=_clean(result.get("original_query")),
        surface=surface,
        status=_clean(result.get("status")),
        model=_clean(result.get("model")),
        tool_calls=tool_calls if isinstance(tool_calls, list) else [],
        limitations=[_clean(item) for item in list(result.get("limitations") or []) if _clean(item)],
        error=_clean(result.get("error")),
    )
    result["aql_evidence_pack_id"] = _clean(result["aql_evidence_pack"].get("evidence_pack_id"))
    return result


def _preview_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        sample = payload[:8]
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
        if payload.get("analysis_run_id"):
            return {
                "kind": "object",
                "keys": [
                    "analysis_run_id",
                    "status",
                    "objective",
                    "metrics",
                    "tables",
                    "charts",
                    "artifacts",
                    "duration_ms",
                ],
                "scalars": {
                    "analysis_run_id": payload.get("analysis_run_id"),
                    "status": payload.get("status"),
                    "objective": payload.get("objective"),
                    "duration_ms": payload.get("duration_ms"),
                },
                "nested_keys": ["metrics", "tables", "charts", "artifacts"],
            }
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
                scalar_items[str(key)] = _scalar_preview_value(str(key), value)
            else:
                nested_keys.append(str(key))
        return {
            "kind": "object",
            "keys": [str(key) for key in list(payload.keys())[:12]],
            "scalars": scalar_items,
            "nested_keys": nested_keys[:8],
        }
    return {"kind": "scalar", "value": payload}


def _is_raw_context_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    if normalized in {
        "raw_text",
        "stdout",
        "stderr",
        "traceback",
        "log_text",
        "logs",
        "code_text",
        "search_text",
    }:
        return True
    return normalized.endswith("_raw_text") or normalized.endswith("_log_text")


def _scalar_preview_value(key: str, value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if not text:
        return text
    if _is_raw_context_key(key) or len(text) > 800:
        line_count = len(text.splitlines())
        return f"<{len(text)} chars, {line_count} line(s); raw text omitted from default tool context>"
    return value


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if number != number:
        return None
    if number in {float("inf"), float("-inf")}:
        return None
    return number


def _format_number(value: Any, *, digits: int = 2, suffix: str = "") -> str:
    number = _as_float(value)
    if number is None:
        return ""
    return f"{number:.{digits}f}{suffix}"


def _dataset_name_from_result(result: dict[str, Any]) -> str:
    request = result.get("request")
    if isinstance(request, dict):
        return _clean(request.get("name")).lower()
    return ""


def _rows_llm_context(result: dict[str, Any], payload: Any) -> str:
    if not isinstance(payload, list) or not payload or not all(isinstance(row, dict) for row in payload[:4]):
        return ""

    dataset_name = _dataset_name_from_result(result)
    rows = [row for row in payload if isinstance(row, dict)]
    columns = {str(key) for row in rows[:12] for key in row.keys()}

    if dataset_name == "momentum_profiles" or {"return_1m_pct", "return_3m_pct"}.issubset(columns):
        lines = [f"Momentum profiles returned {len(rows)} row(s)."]
        for row in rows[:12]:
            symbol = _clean(row.get("symbol"))
            if not symbol:
                continue
            parts = [symbol]
            close = _format_number(row.get("close"), digits=2)
            if close:
                parts.append(f"close={close}")
            for label, key in [
                ("1W", "return_1w_pct"),
                ("1M", "return_1m_pct"),
                ("3M", "return_3m_pct"),
                ("1Y", "return_1y_pct"),
                ("5Y", "return_5y_pct"),
            ]:
                formatted = _format_number(row.get(key), digits=1, suffix="%")
                if formatted:
                    parts.append(f"{label}={formatted}")
            trend_r2 = _format_number(row.get("trend_r2_3m"), digits=2)
            if trend_r2:
                parts.append(f"3M trend r2={trend_r2}")
            lines.append("; ".join(parts))
        return _truncate("\n".join(lines), limit=int(get_config_param(_P_TOOL_RESULT_CONTEXT_LIMIT)))

    if dataset_name == "price_history" or {"timestamp", "close"}.issubset(columns):
        by_symbol: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            symbol = _clean(row.get("symbol")) or _clean(row.get("ticker")) or "series"
            if _as_float(row.get("close")) is None:
                continue
            by_symbol.setdefault(symbol, []).append(row)
        if not by_symbol:
            return ""
        lines = [f"Price history returned {len(rows)} row(s)."]
        for symbol, symbol_rows in list(by_symbol.items())[:8]:
            ordered = sorted(symbol_rows, key=lambda item: _clean(item.get("timestamp") or item.get("date")))
            first = ordered[0]
            last = ordered[-1]
            first_close = _as_float(first.get("close"))
            last_close = _as_float(last.get("close"))
            first_date = _clean(first.get("timestamp") or first.get("date"))[:10]
            last_date = _clean(last.get("timestamp") or last.get("date"))[:10]
            if first_close is None or last_close is None:
                continue
            return_pct = ((last_close / first_close) - 1.0) * 100.0 if first_close else None
            return_text = _format_number(return_pct, digits=1, suffix="%")
            close_text = f"{first_close:.2f} -> {last_close:.2f}"
            suffix = f"; return={return_text}" if return_text else ""
            lines.append(
                f"{symbol}: {len(ordered)} rows from {first_date or 'unknown'} to {last_date or 'unknown'}; "
                f"close {close_text}{suffix}"
            )
        return _truncate("\n".join(lines), limit=int(get_config_param(_P_TOOL_RESULT_CONTEXT_LIMIT)))

    return ""


def _build_render_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    payload = result.get("payload")
    result_type = _clean(result.get("result_type")).lower()
    if result_type == "analysis_result" and isinstance(payload, dict):
        return {
            "kind": "analysis_result",
            "analysis": {
                "analysis_run_id": payload.get("analysis_run_id"),
                "status": payload.get("status"),
                "objective": payload.get("objective"),
                "metrics": list(payload.get("metrics") or [])[:12],
                "tables": list(payload.get("tables") or [])[:4],
                "charts": list(payload.get("charts") or [])[:4],
                "artifacts": [
                    {
                        "artifact_id": item.get("artifact_id"),
                        "artifact_type": item.get("artifact_type"),
                        "name": item.get("name"),
                        "preview_text": item.get("preview_text"),
                    }
                    for item in list(payload.get("artifacts") or [])[:8]
                    if isinstance(item, dict)
                ],
                "error": payload.get("error"),
                "duration_ms": payload.get("duration_ms"),
            },
        }
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
    from .saa.storage import canonical_source_references

    payload = result.get("payload")
    source_documents = []
    if isinstance(payload, dict):
        source_documents = list(payload.get("candidate_sources") or [])
        if payload.get("page_text") and not source_documents:
            page = (payload.get("summary") or [{}])[0]
            source_documents.append(
                {
                    "url": payload.get("url"),
                    "title": page.get("title"),
                    "chunk_text": payload.get("source_text") or payload["page_text"],
                    "source_kind": "opened_page",
                    "raw_text_origin": "page_capture",
                }
            )
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
    evidence_context_text = ""
    result_messages = [_clean(item) for item in list(result.get("messages") or []) if _clean(item)]
    if isinstance(payload, list):
        evidence_context_text = serialize_llm_context(
            {"rows": payload, "provenance": provenance}
        )
    if isinstance(payload, dict):
        explicit_context = str(
            payload.get("evidence_context_text") or payload.get("llm_context_text") or ""
        ).strip()
        if explicit_context:
            evidence_context_text = explicit_context
            llm_context_text = _truncate(
                explicit_context, limit=int(get_config_param(_P_TOOL_RESULT_CONTEXT_LIMIT))
            )
        elif isinstance(payload.get("top_events"), list):
            llm_context_text = _truncate(
                _attention_home_llm_context(payload),
                limit=int(get_config_param(_P_TOOL_RESULT_CONTEXT_LIMIT)),
            )
        elif isinstance(payload.get("summary"), list):
            evidence_context_text = json.dumps(payload["summary"], ensure_ascii=False, default=str)
            lines: list[str] = []
            for item in list(payload.get("summary") or [])[
                : int(get_config_param(_P_TOOL_RESULT_SUMMARY_ITEMS))
            ]:
                if not isinstance(item, dict):
                    continue
                parts = [
                    _clean(
                        item.get("symbol") or item.get("label") or item.get("title") or item.get("headline")
                    ),
                    _clean(item.get("summary_text") or item.get("summary") or item.get("excerpt")),
                    _clean(item.get("source")),
                    _clean(item.get("url")),
                ]
                text = " | ".join(part for part in parts if part)
                if text:
                    lines.append(text)
            if lines:
                llm_context_text = _truncate(
                    "\n".join(lines), limit=int(get_config_param(_P_TOOL_RESULT_SUMMARY_LIMIT))
                )
    if result_messages:
        message_text = "Tool messages:\n" + "\n".join(f"- {item}" for item in result_messages[:4])
        evidence_context_text = "\n".join(
            part for part in [evidence_context_text, "Tool messages:\n" + "\n".join(result_messages)] if part
        )
        llm_context_text = _truncate(
            "\n".join(part for part in [llm_context_text, message_text] if part),
            limit=int(get_config_param(_P_TOOL_RESULT_CONTEXT_LIMIT)),
        )
    if not llm_context_text:
        llm_context_text = _rows_llm_context(result, payload)

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
    evidence_refs: list[dict[str, str]] = []
    if isinstance(payload, dict):
        # From summary rows (live_event_evidence, retained_context)
        for item in list(payload.get("candidate_sources") or payload.get("summary") or []):
            if not isinstance(item, dict):
                continue
            evidence_ref = {
                "kind": _clean(item.get("kind")),
                "ref": _clean(item.get("ref")),
                "page_id": _clean(item.get("page_id")),
                "proposal_id": _clean(item.get("proposal_id")),
                "mutation_id": _clean(item.get("mutation_id")),
                "chunk_record_id": _clean(item.get("chunk_record_id")),
                "canonical_document_id": _clean(item.get("canonical_document_id")),
                "document_id": _clean(item.get("document_id")),
                "chunk_id": _clean(item.get("chunk_id")),
                "title": _clean(item.get("headline") or item.get("title") or item.get("label")),
                "source": _clean(item.get("source")),
                "published_date": _clean(item.get("published_date") or item.get("published_at")),
                "url": _clean(item.get("url")),
                "evidence_type": _clean(item.get("evidence_type")),
                "source_kind": _clean(item.get("source_kind")),
                "source_refs": (item.get("source_refs") if isinstance(item.get("source_refs"), list) else []),
                **{key: item[key] for key in ("raw_text_origin", "source_trace", "recorded_dates", "date_basis") if key in item},
                **{key: item[key] for key in ("source_references_loaded", "source_reference_resource") if key in item},
            }
            if any(evidence_ref.values()):
                evidence_refs.append(evidence_ref)
            for dependency in item.get("source_refs") or []:
                if isinstance(dependency, dict):
                    evidence_refs.append(dependency)
                    if _is_public_web_source_url(dependency.get("url", "")):
                        source_links.append(
                            {
                                "url": dependency["url"],
                                "label": dependency.get("title") or dependency["url"],
                            }
                        )
            url = _clean(item.get("url"))
            if _is_public_web_source_url(url):
                title = _clean(item.get("headline") or item.get("title") or item.get("label") or url)
                source = _clean(item.get("source") or "")
                label = f"{title} ({source})" if source else title
                source_links.append({"url": url, "label": label})
        # From articles (investigator.recent_news)
        for item in list(payload.get("articles") or []):
            if not isinstance(item, dict):
                continue
            url = _clean(item.get("url"))
            if _is_public_web_source_url(url):
                title = _clean(item.get("headline") or item.get("title") or url)
                source_links.append({"url": url, "label": title})
        # From open_page result
        page_url = _clean(payload.get("url"))
        if _is_public_web_source_url(page_url):
            page_title = _clean(payload.get("title") or page_url)
            source_links.append({"url": page_url, "label": page_title})
        # From rows in the payload directly
        for row in list(payload.get("rows") or []):
            if not isinstance(row, dict):
                continue
            url = _clean(row.get("url"))
            if _is_public_web_source_url(url):
                title = _clean(row.get("headline") or row.get("title") or url)
                source_links.append({"url": url, "label": title})

    return {
        "retrieval_context": {
            "source_reference_format": (
                "source_reference_id identifies complete source metadata in the same source_refs collection. "
                "An entry with only that ID links to the record; repeated links are not independent evidence."
            ),
            **{key: payload.get(key)
               for key in ("queries", "reporting_window", "warnings")
               if isinstance(payload, dict) and key in payload},
        },
        "candidate_sources": (
            payload.get("candidate_sources") or [] if isinstance(payload, dict) else []
        ),
        "source_documents": source_documents,
        "result_type": result_type,
        "provenance": provenance,
        "preview": preview,
        "preview_text": preview_text,
        "user_preview": user_preview,
        "render_payload": render_payload,
        "llm_context_text": llm_context_text,
        "evidence_context_text": evidence_context_text
        or (
            serialize_llm_context({"result": payload, "provenance": provenance})
            if isinstance(payload, (dict, list))
            else llm_context_text or preview_text
        ),
        "source_links": source_links if source_links else None,
        "evidence_refs": canonical_source_references(evidence_refs) if evidence_refs else None,
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
        resolution = _clean(tool.get("resolution"))
        resolution_text = f" [{resolution}]" if resolution else ""
        lines.append(
            f"- {tool.get('name')}: {tool.get('description')}{resolution_text}; "
            f"input_schema: {json.dumps(input_schema, ensure_ascii=False)}"
        )
    return "\n".join(lines)


def _prompt_request_context(context: dict[str, Any]) -> dict[str, Any]:
    """The prompt already carries its query; metadata must not repeat its payload."""
    result = {
        key: value
        for key, value in context.items()
        if key not in {"original_query", "conversation_runs"}
    }
    result["prior_research_attempts"] = [
        {key: value for key, value in run.items() if key != "events"}
        for run in context.get("conversation_runs") or []
    ]
    return result


def _tool_history_prompt(
    tool_calls: list[dict[str, Any]], *, full_evidence: bool = False,
    selected_source_ids: list[str] | None = None,
) -> str:
    """Keep complete evidence in an immutable prefix as the investigation grows."""
    records = []
    for call in tool_calls:
        summary = call.get("result_summary") or {}
        candidates = summary.get("candidate_sources") or []
        if candidates and selected_source_ids is not None:
            selected = set(selected_source_ids)
            candidates = [source for source in candidates if
                (source.get("chunk_record_id") or source.get("canonical_document_id")) in selected]
        records.append(
            {
                "tool_call_id": call.get("tool_call_id"),
                "tool_name": call.get("tool_name"),
                "origin_run_id": call.get("origin_run_id"),
                "origin_turn_id": call.get("origin_turn_id"),
                "carried_forward": call.get("carried_forward", False),
                "status": call.get("status"),
                "arguments": call.get("arguments") or {},
                "evidence": (
                    candidates
                    if summary.get("candidate_sources")
                    else (
                        summary.get("evidence_context_text")
                        or summary.get("llm_context_text")
                        or summary.get("preview_text")
                        or ""
                    )
                ),
                "retrieval_context": summary.get("retrieval_context") or {},
                "evidence_refs": summary.get("evidence_refs") or [],
                "source_links": summary.get("source_links") or [],
                "error": call.get("error"),
            }
        )
    return serialize_llm_context(records, append_only=True)


def _run_evidence_model_step(
    *, llm_client: LLMClient, service: QueryService, tool_calls: list[dict[str, Any]],
    context_state: dict[str, Any], prompt: Callable[[list[dict[str, Any]]], str],
    system_prompt: str, schema_name: str, schema: dict[str, Any],
    surface: str, purpose: str, deadline_monotonic: float,
    metadata: dict[str, Any], progress_callback: ProgressCallback | None,
    capacity_feedback: str = "", call_type: str = "research_grade",
    query: str = "",
    supplied_evidence_allowed: bool = True,
    model_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Keep source reads available through planning, writing and review.

    The trace retains every result. The model owns which results stay in its
    working context and can reopen any original through the existing reader.
    """
    if not isinstance(service, QueryService):
        return generate_json_via_aql_zopedia_gateway(
            llm_client=llm_client, surface=surface, purpose=purpose, call_type=call_type,
            system_prompt=system_prompt, user_prompt=prompt(tool_calls), schema_name=schema_name,
            schema=schema, deadline_monotonic=min(deadline_monotonic, time.monotonic() + model_timeout_seconds)
            if model_timeout_seconds is not None else deadline_monotonic, metadata=metadata,
        )
    step_schema = {
        "type": "object", "additionalProperties": False,
        "description": "Read requires an evidence tool and typed arguments. Answer requires the requested answer object. select_context requires known tool_call_ids. Other fields are optional and do not replace the chosen action.",
        "properties": {
            "action": {"type": "string", "enum": ["answer", "read", "select_context"]},
            "tool_name": {"type": ["string", "null"]},
            "tool_arguments": {"type": ["object", "null"], "additionalProperties": True,
                               "description": "Arguments for the evidence tool, with their original types; an empty object otherwise."},
            "keep_tool_call_ids": {"type": ["array", "null"], "items": {"type": "string"},
                                   "description": "Results to keep in working context; null keeps the current selection. All originals remain readable."},
            "answer": {"type": ["object", "null"]},
        },
        "required": ["action"],
        "allOf": [
            {"if": {"properties": {"action": {"const": "read"}}},
             "then": {"required": ["tool_name", "tool_arguments"], "properties": {
                 "tool_name": {"type": "string", "minLength": 1}, "tool_arguments": {"type": "object"}}}},
            {"if": {"properties": {"action": {"const": "answer"}}},
             "then": {"required": ["answer"], "properties": {"answer": schema}}},
            {"if": {"properties": {"action": {"const": "select_context"}}},
             "then": {"required": ["keep_tool_call_ids"], "properties": {
                 "keep_tool_call_ids": {"type": "array", "items": {"type": "string"}}}}},
        ],
    }
    feedback = capacity_feedback
    catalog_only = False
    input_reference = ""
    query_in_context = bool(query)
    records = {**(service.supplied_evidence if supplied_evidence_allowed else {}),
               **context_state.get("stage_inputs", {})}
    if capacity_feedback:
        input_reference = "stage_input:" + purpose
        records[input_reference] = {"title": "Complete input for " + purpose,
                                    "content": prompt(tool_calls), "metadata": {"purpose": purpose}}
        context_state.setdefault("stage_inputs", {})[input_reference] = records[input_reference]
    while time.monotonic() < deadline_monotonic:
        for call in tool_calls:
            summary = dict(call.get("result_summary") or {})
            for key in ("evidence_context_text", "llm_context_text"):
                value = summary.get(key)
                if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
                    try:
                        summary[key] = json.loads(value)
                    except ValueError:
                        pass
            records["history:" + call["tool_call_id"]] = {
                "title": call.get("tool_name", "Collected evidence"),
                "metadata": {"tool_call_id": call["tool_call_id"], "status": call.get("status"),
                             "origin_run_id": call.get("origin_run_id")},
                "content": {**call, "result_summary": summary},
            }
        reader = service.with_supplied_evidence(records)
        read_tools = [tool for tool in build_tool_registry(reader).catalog() if tool["name"] in {
            "research.list_supplied_evidence", "research.read_supplied_evidence",
        }]
        selected = context_state.get("tool_call_ids")
        visible = [call for call in tool_calls if selected is None or call["tool_call_id"] in selected]
        base_prompt = prompt([] if catalog_only else visible)
        if input_reference:
            base_prompt = (("Original request:\n" + query + "\n\n") if query_in_context else "") + (
                           "The complete stage input, including the original request, is supplied source "
                           + input_reference + ". Read it with the evidence tools.\n\nRead evidence:\n"
                           + _tool_history_prompt([] if catalog_only else visible))
        history_catalog = [{"tool_call_id": call["tool_call_id"], "source_id": "history:" + call["tool_call_id"],
                            "tool_name": call.get("tool_name"), "argument_fields": list(call.get("arguments") or {}),
                            "status": call.get("status")} for call in tool_calls]
        user_prompt = base_prompt + "\n\nEvidence access:\n" + serialize_llm_context({
            "history_catalog": history_catalog, "supplied_sources": reader.supplied_evidence_manifest,
            "tools": read_tools, "context_feedback": feedback,
        })
        try:
            step = generate_json_via_aql_zopedia_gateway(
                llm_client=llm_client, surface=surface, purpose=purpose, call_type=call_type,
                system_prompt=system_prompt + (
                    "\nEvidence remains readable during this step. Return action=answer with the requested result in answer, "
                    "or action=read with an evidence tool and its JSON arguments. Use select_context to choose which "
                    "tool results stay in working context. Read any omitted result through its history source_id."
                ),
                user_prompt=user_prompt, schema_name=schema_name + "_evidence_step", schema=step_schema,
                deadline_monotonic=min(deadline_monotonic, time.monotonic() + model_timeout_seconds)
                if model_timeout_seconds is not None else deadline_monotonic, metadata=metadata,
            )
        except LLMContextWindowExceeded as exc:
            if input_reference:
                if query_in_context:
                    query_in_context = False
                elif catalog_only:
                    raise
            elif catalog_only:
                input_reference = "stage_input:" + purpose
                records[input_reference] = {"title": "Complete input for " + purpose,
                                            "content": base_prompt, "metadata": {"purpose": purpose}}
                context_state.setdefault("stage_inputs", {})[input_reference] = records[input_reference]
            catalog_only = True
            feedback = str(exc) + " Choose working-context tool_call_ids or read specific sections. Originals are retained."
            continue
        public_step = {key: value for key, value in step.items() if not key.startswith("__")}
        # An existing caller may request the artifact directly in its query.
        # Preserve that valid paid answer rather than regenerate it to obtain
        # an unused tool-control envelope.
        if not list(Draft202012Validator(schema).iter_errors(public_step)):
            return step
        errors = list(Draft202012Validator(step_schema).iter_errors(public_step))
        if errors:
            feedback = "Invalid response: " + errors[0].message
            continue
        keep = step.get("keep_tool_call_ids")
        action = step["action"]
        selection_feedback = ""
        if keep is not None:
            known = {call["tool_call_id"] for call in tool_calls}
            if not set(keep) <= known:
                if action == "select_context":
                    feedback = "Unknown tool_call_id in working-context selection. Use the history catalog."
                    continue
                selection_feedback = "Optional context selection was not applied: use known tool_call_ids from the history catalog."
            else:
                context_state["tool_call_ids"] = keep
        if action == "answer" and isinstance(step.get("answer"), dict):
            return {**step["answer"], **{key: value for key, value in step.items() if key.startswith("__")}}
        if action == "select_context":
            catalog_only = False
            feedback = ""
            continue
        arguments, error = _coerce_tool_arguments(step.get("tool_arguments"))
        tool_name = step.get("tool_name")
        entry = next((tool for tool in read_tools if tool["name"] == tool_name), None)
        if not entry or error:
            feedback = error or "Choose an advertised evidence reader or return the answer."
            continue
        errors = list(Draft202012Validator(entry["inputSchema"]).iter_errors(arguments))
        if errors:
            feedback = errors[0].message
            continue
        call_id = "agtc_read_" + uuid.uuid4().hex
        try:
            result = invoke_tool(service=reader, tool_name=tool_name, arguments=arguments)
            call = {"tool_call_id": call_id, "tool_name": tool_name, "arguments": arguments,
                    "status": "completed", "error": None, "result_summary": _summarize_tool_result(result)}
        except Exception as exc:
            call = {"tool_call_id": call_id, "tool_name": tool_name, "arguments": arguments,
                    "status": "failed", "error": str(exc), "result_summary": {}}
        call.update(origin_run_id=metadata.get("run_id"), origin_turn_id=metadata.get("turn_id"))
        tool_calls.append(call)
        if context_state.get("tool_call_ids") is not None:
            context_state["tool_call_ids"] = [*context_state["tool_call_ids"], call_id]
        elif catalog_only:
            context_state["tool_call_ids"] = [call_id]
        catalog_only = False
        feedback = selection_feedback
        _emit_progress(progress_callback, stage="evidence_read_complete", message="Evidence reading complete.",
                       progress=0.9, tool_name=tool_name, tool_call_id=call_id, purpose=purpose)
    raise TimeoutError("Evidence step exceeded its existing deadline: " + purpose)


def _tool_entry_by_name(catalog: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
    normalized_name = _clean(tool_name)
    return next((tool for tool in catalog if _clean(tool.get("name")) == normalized_name), {})


def _activate_discovered_tools(
    *,
    tool_catalog: list[dict[str, Any]],
    tool_registry: ToolRegistry,
    search_result: dict[str, Any],
) -> list[str]:
    payload = search_result.get("payload") if isinstance(search_result, dict) else {}
    matches = payload.get("tools") if isinstance(payload, dict) else []
    active_names = {
        _clean(tool.get("name"))
        for tool in tool_catalog
        if isinstance(tool, dict) and _clean(tool.get("name"))
    }
    activated: list[str] = []
    for match in list(matches or [])[:20]:
        if not isinstance(match, dict):
            continue
        name = _clean(match.get("name"))
        if not name or name in active_names:
            continue
        canonical = tool_registry.get(name)
        if canonical is None:
            continue
        tool_catalog.append(canonical)
        active_names.add(name)
        activated.append(name)
    return activated


def _normalized_arguments(
    tool_entry: dict[str, Any],
    arguments: dict[str, Any],
    *,
    force_refresh: bool,
) -> dict[str, Any]:
    normalized = dict(arguments or {})
    properties = dict((tool_entry.get("inputSchema") or {}).get("properties") or {})
    for name, schema in properties.items():
        if name not in normalized:
            continue
        if not isinstance(schema, dict):
            continue
        normalized[name] = _coerce_argument_to_schema(normalized.get(name), schema)
    if "force_refresh" in properties and "force_refresh" not in normalized:
        normalized["force_refresh"] = force_refresh
    return normalized


def _coerce_argument_to_schema(value: Any, schema: dict[str, Any]) -> Any:
    """Repair common model encoding drift using the advertised tool schema."""
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        types = [str(item) for item in expected_type]
    else:
        types = [str(expected_type)] if expected_type else []
    if "array" in types:
        items = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        if items.get("type") == "string":
            out: list[str] = []
            for item in value:
                if item is None:
                    continue
                if isinstance(item, dict):
                    item = item.get("symbol") or item.get("ticker") or item.get("name") or item.get("value")
                text = _clean(item)
                if text:
                    out.append(text)
            return out
        if items.get("type") == "object":
            return [dict(item) for item in value if isinstance(item, dict)]
        return value
    if "integer" in types:
        try:
            return int(value)
        except Exception:
            return value
    if "number" in types:
        try:
            return float(value)
        except Exception:
            return value
    if "boolean" in types and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    if "object" in types and isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return value
        return parsed if isinstance(parsed, dict) else value
    return value










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
                if not isinstance(values, list):
                    for fallback_key in ("json_value", "object_value"):
                        fallback_value = item.get(fallback_key)
                        if isinstance(fallback_value, list):
                            values = fallback_value
                            break
                if not isinstance(values, list) or any(isinstance(value, dict | list | tuple | set) for value in values):
                    return {}, f"Tool argument `{name}` requires string_list_value."
                out[name] = [str(value) for value in values]
            elif value_kind == "json":
                out[name] = item.get("json_value")
            elif value_kind == "object":
                value = item.get("object_value")
                if value is None:
                    value = item.get("json_value")
                if isinstance(value, dict):
                    out[name] = dict(value)
                elif isinstance(value, list):
                    out[name] = list(value)
                elif value is not None:
                    out[name] = value
                else:
                    return {}, f"Tool argument `{name}` requires object_value."
            elif value_kind == "object_list":
                values = item.get("object_list_value")
                if values is None:
                    values = item.get("json_value")
                if not isinstance(values, list):
                    return {}, f"Tool argument `{name}` requires object_list_value."
                if any(not isinstance(value, dict) for value in values):
                    out[name] = list(values)
                else:
                    out[name] = [dict(value) for value in values]
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
    except Exception as exc:
        from .agents.research_session import ResearchCancellationRequested

        if isinstance(exc, ResearchCancellationRequested):
            raise
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


def _looks_like_recoverable_model_step_error(error: BaseException) -> bool:
    if isinstance(error, TimeoutError) or _looks_like_transient_transport_error(error):
        return True
    if not isinstance(error, LLMAPIError):
        return False
    cleaned = str(error).strip().lower()
    return any(
        marker in cleaned
        for marker in (
            "returned empty content",
            "returned no choices",
        )
    )


def _safe_agent_error_text(error: object) -> str:
    raw = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error or "")
    if isinstance(error, TimeoutError):
        return _clean(raw)
    if _looks_like_transient_transport_error(raw):
        return (
            "A research or model connection dropped before it returned a response. "
            "This is usually transient; rerun the request to retry the source fetch."
        )
    return _clean(raw) or "The research agent failed before it could produce an answer."


_PLANNER_SYSTEM_PROMPT = register_narrative_prompt(
    name="Zopedia Agent Planner (tool-calling reasoning)",
    file="services/zopedia_agent.py",
    group="Zopedia",
    prompt=(
        "Answer the user's question using the available evidence and tools. Read relevant originals before relying on their figures; a catalog entry or earlier generated answer is not the original. Use source identifiers returned by tools, never invent them.\nFor numerical work, check what each figure measures, its date, units and denominator. Use calculation tools when they help, then check the result against the source. Keep assumptions distinct from reported facts. If a source is ambiguous, give a useful conditional answer rather than inventing data or withholding supported findings.\nChoose one useful tool call or finish with the answer. On a tool error, use the returned error and catalog to correct the next call. Do not repeat an unchanged failed call. Keep any research notes brief; optional bookkeeping fields may be omitted. Never claim a read or calculation succeeded unless its result is present. Respect the user's source scope and permissions. Return the requested JSON."
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


def _compact_conversation_history(
    conversation_history: list[dict[str, Any]] | None,
    max_chars: int | None = None,
) -> str:
    """Preserve ordered messages, including corrections beyond an answer preview."""
    if not conversation_history:
        return ""
    return (
        "Prior conversation:\n"
        + serialize_llm_context(
            [
                {
                    "role": message.get("role"),
                    "content": message.get("content") or message.get("answer") or "",
                }
                for message in conversation_history
            ]
        )
        + "\n\n"
    )


def resolve_conversation_followup_query(
    query: str,
    conversation_history: list[dict[str, Any]] | None,
    *,
    max_answer_chars: int = 1800,
) -> tuple[str, bool]:
    """Keep the user's words intact; the shared agent interprets separate history."""
    return str(query or "").strip(), bool(conversation_history)


def _tool_catalog_for_write_policy(
    tool_catalog: list[dict[str, Any]], write_policy: str
) -> list[dict[str, Any]]:
    normalized = _normalize_zopedia_write_policy(write_policy)
    allowed_effects = (
        {"read"}
        if normalized == "none"
        else {"read", "propose"} if normalized == "propose" else {"read", "propose", "write"}
    )
    return [
        tool
        for tool in tool_catalog
        if tool.get("knowledge_effect", "read") in allowed_effects
        and tool.get("name") != "zopedia.rollback_mutation"
    ]


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
    timeout_seconds: int | None = None,
    tool_registry: ToolRegistry | None = None,
    request_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if timeout_seconds is None:
        timeout_seconds = int(get_config_param(_P_TOOL_CALL_TIMEOUT_SECONDS))
    timeout_seconds = max(float(timeout_seconds), 0)
    if timeout_seconds <= 0:
        raise ResearchStepTimeout(
            "source acquisition", "Research phase budget expired before tool dispatch."
        )
    result_box: list[dict[str, Any] | None] = [None]
    error_box: list[BaseException | None] = [None]
    deadline = time.monotonic() + timeout_seconds
    work = WorkScope(deadline)

    def _run_tool() -> None:
        try:
            with work.activate(), llm_request_deadline(deadline):
                if tool_name == "tools.search" and tool_registry is not None:
                    result_box[0] = tool_registry.search_result(
                        arguments.get("query"),
                        namespace=arguments.get("namespace"),
                        limit=int(arguments.get("limit") or 8),
                    )
                else:
                    result_box[0] = invoke_tool(
                        service=service,
                        tool_name=tool_name,
                        arguments=arguments,
                        run_id=run_id,
                        registry=tool_registry,
                        request_context=request_context,
                    )
        except BaseException as exc:
            error_box[0] = exc

    tool_thread = threading.Thread(target=copy_context().run, args=(_run_tool,), name=f"zopedia-tool-{tool_name[:32]}", daemon=True)
    tool_thread.start()
    heartbeat_interval = 5.0
    elapsed = 0.0
    while tool_thread.is_alive():
        wait_seconds = max(min(heartbeat_interval, deadline - time.monotonic()), 0)
        tool_thread.join(timeout=wait_seconds)
        if not tool_thread.is_alive():
            break
        elapsed = timeout_seconds - max(deadline - time.monotonic(), 0)
        if time.monotonic() >= deadline:
            work.cancel()
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
            raise ResearchStepTimeout(
                "source acquisition", f"{tool_name} timed out after {timeout_seconds}s."
            )
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
    timeout_seconds: float,
    progress_callback: ProgressCallback | None,
    progress: float,
    func: Callable[[], Any],
) -> Any:
    if timeout_seconds <= 0:
        raise ResearchStepTimeout(
            label, f"Research phase budget expired before {label}."
        )
    result_box: list[Any] = [None]
    error_box: list[BaseException | None] = [None]
    deadline = time.monotonic() + timeout_seconds
    work = WorkScope(deadline)

    def _runner() -> None:
        try:
            with work.activate(), llm_request_deadline(deadline):
                result_box[0] = func()
        except BaseException as exc:
            error_box[0] = exc

    step_thread = threading.Thread(target=copy_context().run, args=(_runner,), name=f"zopedia-hidden-{label[:32]}", daemon=True)
    step_thread.start()
    heartbeat_interval = 5.0
    elapsed = 0.0
    while step_thread.is_alive():
        wait_seconds = max(min(heartbeat_interval, deadline - time.monotonic()), 0)
        step_thread.join(timeout=wait_seconds)
        if not step_thread.is_alive():
            break
        elapsed = timeout_seconds - max(deadline - time.monotonic(), 0)
        if time.monotonic() >= deadline:
            work.cancel()
            _emit_progress(
                progress_callback,
                stage="hidden_step_timeout",
                message=f"{label} timed out after {timeout_seconds}s.",
                progress=min(progress + 0.02, 0.9),
                elapsed_seconds=int(elapsed),
            )
            raise ResearchStepTimeout(
                label, f"{label} timed out after {timeout_seconds}s."
            )
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


def _budgeted_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    free_tools = {
        "trajectory.monitor",
        "research.prefetched_context",
        "research.supplied_context",
        "tools.search",
    }
    return [
        call
        for call in list(tool_calls or [])
        if _clean(call.get("tool_name")) not in free_tools
        and not call.get("carried_forward")
        and call.get("status") in {"completed", "failed"}
    ]


def _budgeted_tool_count(tool_calls: list[dict[str, Any]]) -> int:
    return len(_budgeted_tool_calls(tool_calls))


def _planner_user_prompt(
    *,
    query: str,
    tool_catalog: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    max_tool_calls: int,
    task: str = "",
    surface: str = "",
    conversation_history: list[dict[str, Any]] | None = None,
    prefetched_context: str = "",
) -> str:
    history_block = _compact_conversation_history(conversation_history)
    evidence_block = ""
    if prefetched_context:
        evidence_block = (
            f"{prefetched_context}\n\n"
            "These are initial candidates, not a complete account. Compare interpretations and pursue useful missing angles.\n\n"
        )
    return (
        f"{history_block}"
        f"{evidence_block}"
        f"Task: {_clean(task) or 'agent_answer'}\n"
        f"Surface: {_clean(surface) or 'zopedia.chat'}\n\n"
        f"User question:\n{query}\n\n"
        "Tool call history:\n"
        f"{_tool_history_prompt(tool_calls)}\n\n"
        f"Tool budget: {max_tool_calls - _budgeted_tool_count(tool_calls)} remaining out of {max_tool_calls}.\n\n"
        "Available tools:\n"
        f"{_tool_catalog_prompt(tool_catalog)}\n\n"
        "Return exactly one next action. "
        "If you still need data, set action='tool_call' and choose one tool. "
        "If you can answer now, set action='final'. "
        "When action='tool_call', tool_arguments is an object with the required fields and typed values "
        "from that tool's input_schema. Use an empty object when the tool takes no arguments or action='final'."
    )


def _final_user_prompt(
    *,
    query: str,
    tool_calls: list[dict[str, Any]],
    task: str = "",
    surface: str = "",
    conversation_history: list[dict[str, Any]] | None = None,
    prefetched_context: str = "",
    response_schema_name: str = "",
    selected_source_ids: list[str] | None = None,
    answer_points: list[str] | None = None,
    draft_answer: str = "",
) -> str:
    history_block = _compact_conversation_history(conversation_history)
    evidence_block = ""
    if prefetched_context:
        evidence_block = f"{prefetched_context}\n\n"
    if not response_schema_name:
        if not answer_points:
            return (
                f"User request:\n{query}\n\n{history_block}{evidence_block}"
                f"Collected sources:\n{_tool_history_prompt(tool_calls, full_evidence=True)}\n\n"
                f"Available draft:\n{draft_answer}\n\n"
                "No research outline is available. Give the best useful answer from this context. "
                "Follow the user's requested source scope, length and format."
            )
        source_fields = (
            "chunk_record_id",
            "page_id",
            "title",
            "url",
            "source_refs",
            "published_at",
            "published_date",
            "recorded_dates",
            "date_basis",
            "source_trace",
            "raw_text_origin",
            "evidence_type",
        )
        candidates = [
            source
            for call in tool_calls
            if call.get("status") == "completed"
            for source in (call.get("result_summary") or {}).get("candidate_sources") or []
        ]
        selected = set(selected_source_ids or [])
        selected.update(
            source["page_id"]
            for source in candidates
            if source.get("chunk_record_id") in selected
            and source.get("page_id")
            and str(source["page_id"]).lower() not in {"nan", "nat"}
        )
        sources = {}
        for call in tool_calls:
            if call.get("status") != "completed":
                continue
            summary = call.get("result_summary") or {}
            references = summary.get("candidate_sources") or summary.get("evidence_refs") or []
            for source in references:
                identities = {source.get("chunk_record_id"), source.get("page_id")}
                if selected and not selected.intersection(identities):
                    continue
                citation = {key: source[key] for key in source_fields if source.get(key)}
                if not citation:
                    continue
                identity = source.get("chunk_record_id") or source.get("page_id") or source.get("url")
                sources[identity or json.dumps(citation, sort_keys=True, default=str)] = citation
            if not references and not selected:
                for link in summary.get("source_links") or []:
                    if link.get("url"):
                        sources[link["url"]] = {"title": link.get("label"), "url": link["url"]}
        return (
            f"User request:\n{query}\n\n{history_block}{evidence_block}"
            "Research outline:\n" + json.dumps(answer_points, ensure_ascii=False) + "\n\n"
            "Source index for citations (the answer points contain the selected analysis):\n"
            + json.dumps(list(sources.values()), ensure_ascii=False, default=str)
            + "\n\nWrite the finished explanation. Select the main ideas needed by this reader. "
            "Use ordinary words and useful source-title links. Follow the requested length and format."
        )
    return (
        f"{history_block}"
        f"{evidence_block}"
        f"Task: {_clean(task) or 'agent_answer'}\n"
        f"Surface: {_clean(surface) or 'zopedia.chat'}\n\n"
        f"User question:\n{query}\n\n"
        "Tool evidence:\n"
        f"{_tool_history_prompt(tool_calls, full_evidence=True, selected_source_ids=selected_source_ids)}\n\n"
        + "Research outline:\n" + json.dumps(answer_points or [], ensure_ascii=False)
        + "\n\nAvailable draft:\n" + draft_answer + "\n\n"
        + (
            f"Return the artifact JSON for {response_schema_name}, using its requested fields and format. "
            if response_schema_name
            else "Write the most useful answer from this evidence and established context, including reasonable inferences. "
        )
        + "When the user question contains typed Context JSON, source_acquisition_rows, supplied source text, or inline datasets, treat that supplied context as evidence too, especially when the tool budget is zero. "
        + (
            "Use concise plain prose in public text fields, without headings or process commentary. "
            if response_schema_name
            else "Match the requested format. Use clear connected prose and short paragraphs with blank lines between them. "
        )
        + "If you use a Markdown table, it must have a header row, separator row, and one row per line; use bullets if a table would be cramped. "
        "Treat the user's premise as unverified unless the evidence supports it; if evidence contradicts the premise, say so plainly. "
        "If you use Zopedia memory, cite the relevant page title. Keep internal IDs out of public prose. "
        "Use source-title links for supporting reports, including retained sources; keep source dependencies distinct from generated interpretation. "
        "Do not claim that live search, recent search, or a source-reading tool failed or returned no results unless the tool history shows that exact attempt. "
        "For current questions, respect the dates of supplied and retained evidence; supplied current reports count even without a live tool call. "
        "Do not turn the answer into a citation list. "
        "Incomplete evidence should guide interpretation and follow-up, not erase a useful connection. Respect the user's evidence scope."
    )


def _judge_user_prompt(
    *,
    query: str,
    tool_calls: list[dict[str, Any]],
    draft_answer: str,
    draft_confidence: str,
    limitations: list[str],
    task: str = "",
    surface: str = "",
    conversation_history: list[dict[str, Any]] | None = None,
    prefetched_context: str = "",
    structured_response: bool = False,
) -> str:
    history_block = _compact_conversation_history(conversation_history)
    evidence_block = ""
    if prefetched_context:
        evidence_block = f"{prefetched_context}\n\n"
    return (
        f"{history_block}"
        f"{evidence_block}"
        f"Task: {_clean(task) or 'agent_answer'}\n"
        f"Surface: {_clean(surface) or 'zopedia.chat'}\n\n"
        f"User question:\n{query}\n\n"
        "Tool evidence:\n"
        f"{_tool_history_prompt(tool_calls, full_evidence=True)}\n\n"
        "Draft answer:\n"
        f"{draft_answer}\n\n"
        "Review the draft for useful discovery, relevance and explanatory depth. "
        "When the original user question contains typed Context JSON, source_acquisition_rows, supplied source text, or inline datasets, treat that supplied context as part of the evidence even if no tool calls were made. "
        "Use verdict='accept' when the draft is useful, coherent and expresses facts and hypotheses appropriately. "
        "Use verdict='revise' when the answer should be corrected or made more precise. "
        "Use verdict='insufficient' only when you cannot form a substantive answer; retain whatever useful analysis is available. "
        "Correct false descriptions of tool activity without discarding the investigation. "
        "Use established business context and reasonable inference to explain indirect relationships. Missing exact source "
        "matches or price data do not exclude useful leads. Keep observations dated and do not invent current facts. "
        + (
            "Return the corrected artifact in answer_payload. Keep its field schema and use plain public prose."
            if structured_response
            else "Return corrected answer_points for the final writer. These points own the substantive analysis; keep review commentary separate."
        )
    )


def _synthesis_llm_client(primary_llm: LLMClient) -> LLMClient:
    """Use the shared LLM provider for synthesis.

    Provider/model selection belongs in the global LLM layer. Keeping a
    Zopedia-only synthesis override made the runtime harder to reason about and
    let one surface drift from AQL/API/job behavior.
    """
    return primary_llm


def _review_allows_memory(review: dict[str, Any]) -> bool:
    return review.get("status") == "completed" and review.get("verdict") in {"accept", "revise"}


def _run_answer_judge(
    *,
    llm: LLMClient,
    run_id: str,
    query: str,
    tool_calls: list[dict[str, Any]],
    draft_answer: str,
    draft_confidence: str,
    limitations: list[str],
    conversation_history: list[dict[str, Any]] | None,
    prefetched_context: str,
    timeout_seconds: int,
    progress_callback: ProgressCallback | None,
    task: str = "",
    surface: str = "",
    response_schema: dict[str, Any] | None = None,
    service: QueryService | None = None,
    context_state: dict[str, Any] | None = None,
    supplied_evidence_allowed: bool = True,
    model_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Review a draft answer against evidence without blocking the whole run."""
    if int(get_config_param(_P_ANSWER_JUDGE_ENABLED)) == 0:
        return {"status": "skipped", "reason": "disabled"}
    if not _clean(draft_answer):
        return {"status": "skipped", "reason": "empty_draft"}

    _emit_progress(
        progress_callback,
        stage="answer_judge_start",
        message="Reviewing the draft answer against collected evidence.",
        progress=0.975,
        tool_call_count=len(tool_calls),
    )
    result_box: list[dict[str, Any] | None] = [None]
    error_box: list[BaseException | None] = [None]
    review_schema = dict(_JUDGE_SCHEMA)
    if response_schema:
        review_schema["properties"] = {
            **_JUDGE_SCHEMA["properties"],
            "answer_payload": response_schema,
        }
        review_schema["required"] = [*_JUDGE_SCHEMA["required"], "answer_payload"]

    def _runner() -> None:
        try:
            result_box[0] = _run_evidence_model_step(
                llm_client=llm,
                service=service or QueryService.from_environment(),
                tool_calls=tool_calls,
                context_state=context_state if context_state is not None else {},
                supplied_evidence_allowed=supplied_evidence_allowed,
                query=query,
                progress_callback=progress_callback,
                surface=_clean(surface) or "zopedia.chat",
                purpose="agent.answer_judge",
                system_prompt=(
                    get_prompt(_AGENT_JUDGE_SYSTEM_PROMPT)
                    + (
                        " The draft is a structured artifact. Return the corrected artifact in answer_payload, preserving its schema. "
                        "Review the facts, sources and business reasoning. Keep process limitations out of public text fields."
                        if response_schema
                        else ""
                    )
                ),
                prompt=lambda visible: _judge_user_prompt(
                    query=query,
                    task=task,
                    surface=surface,
                    tool_calls=visible,
                    draft_answer=draft_answer,
                    draft_confidence=draft_confidence,
                    limitations=limitations,
                    conversation_history=conversation_history,
                    prefetched_context=prefetched_context,
                    structured_response=bool(response_schema),
                ),
                schema_name="zopedia_agent_judge",
                schema=review_schema,
                deadline_monotonic=deadline,
                model_timeout_seconds=model_timeout_seconds,
                metadata={
                    "run_id": run_id,
                    "task": _clean(task) or "agent_answer",
                    "tool_call_count": len(tool_calls),
                },
            )
        except BaseException as exc:
            error_box[0] = exc

    deadline = time.monotonic() + timeout_seconds
    judge_thread = threading.Thread(target=copy_context().run, args=(_runner,), name="zopedia-answer-judge", daemon=True)
    judge_thread.start()
    heartbeat_interval = 5.0
    elapsed = 0.0
    while judge_thread.is_alive():
        judge_thread.join(timeout=max(min(heartbeat_interval, deadline - time.monotonic()), 0))
        if not judge_thread.is_alive():
            break
        elapsed = timeout_seconds - max(deadline - time.monotonic(), 0)
        if time.monotonic() >= deadline:
            _emit_progress(
                progress_callback,
                stage="answer_judge_timeout",
                message="The answer review step timed out; returning the draft answer.",
                progress=0.985,
                elapsed_seconds=int(elapsed),
                tool_call_count=len(tool_calls),
            )
            return {"status": "timeout", "reason": f"timed out after {timeout_seconds}s"}
        _emit_progress(
            progress_callback,
            stage="answer_judge_heartbeat",
            message=f"Still refining the research answer... ({int(elapsed)}s)",
            progress=0.98,
            elapsed_seconds=int(elapsed),
            tool_call_count=len(tool_calls),
        )

    if error_box[0] is not None:
        _emit_progress(
            progress_callback,
            stage="answer_judge_failed",
            message="The answer review step failed; returning the draft answer.",
            progress=0.985,
            error=_safe_agent_error_text(error_box[0]),
            tool_call_count=len(tool_calls),
        )
        return {"status": "failed", "reason": _safe_agent_error_text(error_box[0])}

    payload = result_box[0] or {}
    if response_schema and isinstance(payload.get("answer_payload"), dict):
        answer = json.dumps(payload["answer_payload"], ensure_ascii=False, default=str)
    else:
        answer = (
            "\n\n".join(str(point).strip() for point in payload.get("answer_points") or []) or draft_answer
        )
    errors = list(
        Draft202012Validator(review_schema).iter_errors(
            {key: value for key, value in payload.items() if not key.startswith("__")}
        )
    )
    if errors or not answer:
        _emit_progress(
            progress_callback,
            stage="answer_judge_failed",
            message="The answer review returned an invalid result.",
            progress=0.985,
            tool_call_count=len(tool_calls),
        )
        return {"status": "failed", "reason": "invalid_review", "schema_error_count": len(errors)}
    reasoning_trace = _clean(payload.get("__reasoning_content"))
    if reasoning_trace:
        _emit_progress(
            progress_callback,
            stage="model_reasoning_trace",
            message="Answer judge reasoning trace captured.",
            progress=0.985,
            reasoning_trace=reasoning_trace,
            source="answer_judge",
        )
    review = {
        "status": "completed",
        "verdict": payload["verdict"],
        "critique_summary": _clean(payload.get("critique_summary")),
        "answer_markdown": answer,
        "answer_points": payload.get("answer_points") or [],
        "confidence": _clean(payload.get("confidence")).lower() or draft_confidence or "low",
        "limitations": [_clean(item) for item in list(payload.get("limitations") or []) if _clean(item)],
        "unsupported_claims": [
            _clean(item) for item in list(payload.get("unsupported_claims") or []) if _clean(item)
        ],
        "evidence_gaps": [_clean(item) for item in list(payload.get("evidence_gaps") or []) if _clean(item)],
        "research_requests": [
            str(item).strip() for item in payload.get("research_requests") or [] if str(item).strip()
        ],
    }
    _emit_progress(
        progress_callback,
        stage="answer_judge_complete",
        message="Answer review complete.",
        progress=0.99,
    )
    return review


def _trajectory_monitor_user_prompt(
    *,
    query: str,
    task: str,
    surface: str,
    tool_catalog: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    max_tool_calls: int,
    restart_count: int,
) -> str:
    tool_names = [_clean(tool.get("name")) for tool in tool_catalog if _clean(tool.get("name"))]
    return (
        f"Task: {_clean(task) or 'agent_answer'}\n"
        f"Surface: {_clean(surface) or 'zopedia.chat'}\n"
        f"User question:\n{query}\n\n"
        "Available tool names:\n"
        f"{', '.join(tool_names)}\n\n"
        "Tool call history:\n"
        f"{_tool_history_prompt(tool_calls)}\n\n"
        f"Restart count: {int(restart_count)}\n"
        f"Tool budget remaining: {max(int(max_tool_calls) - _budgeted_tool_count(tool_calls), 0)} of {int(max_tool_calls)}\n\n"
        "Decide whether this thread should double down, restart with one corrective instruction, or kill/fail closed."
    )


def _run_trajectory_monitor(
    *,
    llm: LLMClient,
    run_id: str,
    query: str,
    task: str,
    surface: str,
    tool_catalog: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    max_tool_calls: int,
    restart_count: int,
    timeout_seconds: int,
    progress_callback: ProgressCallback | None,
    inference_effort: str | None = None,
) -> dict[str, Any]:
    if int(get_config_param(_P_TRAJECTORY_MONITOR_ENABLED)) == 0:
        return {"status": "skipped", "decision": "double_down", "reason": "disabled"}
    visible_calls = [
        call for call in tool_calls
        if isinstance(call, dict) and _clean(call.get("tool_name")) != "trajectory.monitor"
    ]
    if not visible_calls:
        return {"status": "skipped", "decision": "double_down", "reason": "no_tool_trace"}

    _emit_progress(
        progress_callback,
        stage="trajectory_monitor_start",
        message="Checking whether the research thread is still on task.",
        progress=0.93,
        tool_call_count=len(tool_calls),
    )
    result_box: list[dict[str, Any] | None] = [None]
    error_box: list[BaseException | None] = [None]

    def _runner() -> None:
        try:
            result_box[0] = generate_json_via_aql_zopedia_gateway(
                llm_client=llm,
                surface=_clean(surface) or "zopedia.chat",
                purpose="agent.trajectory_monitor",
                inference_effort=inference_effort or str(get_config_param(_P_TRAJECTORY_MONITOR_EFFORT)),
                call_type="utility",
                system_prompt=get_prompt(_TRAJECTORY_MONITOR_SYSTEM_PROMPT),
                user_prompt=_trajectory_monitor_user_prompt(
                    query=query,
                    task=task,
                    surface=surface,
                    tool_catalog=tool_catalog,
                    tool_calls=tool_calls,
                    max_tool_calls=max_tool_calls,
                    restart_count=restart_count,
                ),
                schema_name="zopedia_agent_trajectory_monitor",
                schema=_TRAJECTORY_MONITOR_SCHEMA,
                deadline_monotonic=deadline,
                metadata={
                    "run_id": run_id,
                    "task": _clean(task) or "agent_answer",
                    "restart_count": int(restart_count),
                    "tool_call_count": len(tool_calls),
                },
            )
        except BaseException as exc:
            error_box[0] = exc

    deadline = time.monotonic() + timeout_seconds
    monitor_thread = threading.Thread(target=copy_context().run, args=(_runner,), name="zopedia-trajectory-monitor", daemon=True)
    monitor_thread.start()
    heartbeat_interval = 5.0
    elapsed = 0.0
    while monitor_thread.is_alive():
        monitor_thread.join(timeout=max(min(heartbeat_interval, deadline - time.monotonic()), 0))
        if not monitor_thread.is_alive():
            break
        elapsed = timeout_seconds - max(deadline - time.monotonic(), 0)
        if time.monotonic() >= deadline:
            _emit_progress(
                progress_callback,
                stage="trajectory_monitor_timeout",
                message="Trajectory monitor timed out; continuing with the planner.",
                progress=0.935,
                elapsed_seconds=int(elapsed),
                tool_call_count=len(tool_calls),
            )
            return {"status": "timeout", "decision": "double_down", "reason": "monitor_timeout"}
        _emit_progress(
            progress_callback,
            stage="trajectory_monitor_heartbeat",
            message=f"Still checking trajectory... ({int(elapsed)}s)",
            progress=0.932,
            elapsed_seconds=int(elapsed),
            tool_call_count=len(tool_calls),
        )
    if error_box[0] is not None:
        return {
            "status": "failed",
            "decision": "double_down",
            "reason": _safe_agent_error_text(error_box[0]),
        }
    payload = result_box[0] if isinstance(result_box[0], dict) else {}
    decision = _clean(payload.get("decision")).lower()
    if decision not in {"double_down", "restart", "kill"}:
        decision = "double_down"
    payload["decision"] = decision
    payload["status"] = "completed"
    return payload


def _normalized_trajectory_monitor_result(
    monitor_result: dict[str, Any],
    *,
    restart_count: int,
    max_restarts: int,
) -> tuple[dict[str, Any], str]:
    normalized = dict(monitor_result or {})
    decision = _clean(normalized.get("decision")).lower() or "double_down"
    off_contract_signals = [
        _clean(item)
        for item in list(normalized.get("off_contract_signals") or [])
        if _clean(item)
    ]
    if decision == "kill" and not off_contract_signals:
        decision = "double_down"
        normalized["reason"] = (
            _clean(normalized.get("reason"))
            + " Continuing because evidence insufficiency is handled by the critic, not by terminating the research session."
        ).strip()
    elif decision == "restart" and int(restart_count) >= int(max_restarts):
        decision = "double_down"
        normalized["reason"] = (
            (_clean(normalized.get("reason")) + " ")
            + "The correction budget is exhausted, so the researcher will continue to a critic-reviewed answer."
        ).strip()
    normalized["decision"] = decision
    normalized["off_contract_signals"] = off_contract_signals
    return normalized, decision


def _append_trajectory_monitor_call(
    tool_calls: list[dict[str, Any]],
    *,
    monitor_result: dict[str, Any],
    restart_count: int,
) -> None:
    decision = _clean(monitor_result.get("decision")).lower() or "double_down"
    reason = _clean(monitor_result.get("reason"))
    corrective = _clean(monitor_result.get("corrective_instruction"))
    off_contract = [
        _clean(item) for item in list(monitor_result.get("off_contract_signals") or []) if _clean(item)
    ]
    gaps = [_clean(item) for item in list(monitor_result.get("evidence_gaps") or []) if _clean(item)]
    lines = [
        f"Trajectory monitor decision: {decision}.",
        f"Reason: {reason}." if reason else "",
        f"Corrective instruction: {corrective}." if corrective else "",
    ]
    if off_contract:
        lines.append("Off-contract signals: " + "; ".join(off_contract[:5]) + ".")
    if gaps:
        lines.append("Evidence gaps: " + "; ".join(gaps[:5]) + ".")
    context = "\n".join(line for line in lines if line)
    tool_calls.append(
        {
            "tool_call_id": f"agtc_monitor_{restart_count}",
            "tool_name": "trajectory.monitor",
            "arguments": {"decision": decision, "restart_count": restart_count},
            "status": "completed",
            "error": None,
            "result_summary": {
                "preview_text": context,
                "user_preview": _truncate(context, limit=int(get_config_param(_P_USER_PREVIEW_LIMIT))),
                "llm_context_text": context,
                "result_type": "trajectory_monitor",
                "provenance": {"source": "aql_zopedia_trajectory_monitor"},
                "preview": {"kind": "text", "chars": len(context)},
            },
        }
    )


def _has_memory_candidate(
    *,
    tool_calls: list[dict[str, Any]],
    prefetched_context: str,
) -> bool:
    if _clean(prefetched_context):
        return True
    for call in tool_calls:
        if _clean(call.get("status")).lower() != "completed":
            continue
        if _clean(call.get("tool_name")) in _NON_EVIDENCE_TOOL_NAMES:
            continue
        summary = dict(call.get("result_summary") or {})
        if (
            _clean(summary.get("llm_context_text"))
            or _clean(summary.get("preview_text"))
            or list(summary.get("evidence_refs") or [])
            or list(summary.get("source_links") or [])
        ):
            return True
    return False


def _memory_reflection_user_prompt(
    *,
    query: str,
    answer_markdown: str,
    confidence: str,
    limitations: list[str],
    quality_review: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    conversation_history: list[dict[str, Any]] | None,
    prefetched_context: str,
    write_policy: str,
) -> str:
    history_block = _compact_conversation_history(
        conversation_history,
    )
    evidence_refs: list[dict[str, Any]] = []
    for call in tool_calls:
        summary = dict(call.get("result_summary") or {})
        for ref in list(summary.get("evidence_refs") or []):
            if isinstance(ref, dict):
                evidence_refs.append(ref)
        for link in list(summary.get("source_links") or []):
            if isinstance(link, dict):
                evidence_refs.append({"kind": "source_link", **link})
    limitation_block = "\n".join(f"- {item}" for item in limitations if _clean(item)) or "- None"
    return (
        f"{history_block}"
        f"User question:\n{query}\n\n"
        f"Active write_policy: {write_policy}\n\n"
        f"Final answer:\n{answer_markdown}\n\n"
        f"Final confidence: {confidence or 'low'}\n\n"
        "Answer judge review:\n"
        f"{_json_dumps(quality_review, limit=2400)}\n\n"
        "Known limitations:\n"
        f"{limitation_block}\n\n"
        "Prefetched internal evidence:\n"
        f"{_truncate(prefetched_context, limit=2400) if prefetched_context else 'None'}\n\n"
        "Tool evidence:\n"
        f"{_tool_history_prompt(tool_calls, full_evidence=True)}\n\n"
        "Evidence references available for memory writes:\n"
        f"{_json_dumps(evidence_refs[:12], limit=3000)}\n\n"
        "Return the single best memory action. "
        "For write_policy=safe_auto, use apply_mutation for durable source-backed safe page upserts, metadata patches, or page links. "
        "Supported safe mutation_type values are exactly: upsert_pages, link_pages, metadata_patch. "
        "For upsert_pages, pages must be a non-empty array of complete page objects with page_type, title, summary, body_markdown, source_urls, entity_refs, and metadata. "
        "Put article URLs in source_urls and evidence handles in evidence_refs; payload is only auxiliary metadata. "
        "For write_policy=propose, do not use apply_mutation; create a proposal instead. "
        "For apply_mutation, fill mutation_type and the mutation fields. "
        "For propose_change, fill proposal_type, page_id if known, title, rationale, and payload. "
        "For no_action, leave mutation/proposal fields empty and explain why."
    )


def _memory_value_list(value: object) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = [text]
    else:
        parsed = value
    if not isinstance(parsed, (list, tuple, set)):
        parsed = [parsed]
    out: list[str] = []
    for item in parsed:
        clean = _clean(item)
        if clean and clean not in out:
            out.append(clean)
    return out


def _memory_dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _memory_decision_action(decision: dict[str, Any]) -> str:
    action = _clean(decision.get("action")).lower()
    return action if action in {"no_action", "apply_mutation", "propose_change"} else "no_action"


def _memory_apply_contract_issues(decision: dict[str, Any]) -> list[str]:
    if _memory_decision_action(decision) != "apply_mutation":
        return []
    issues: list[str] = []
    mutation_type = _clean(decision.get("mutation_type")).lower()
    pages = _memory_dict_list(decision.get("pages"))
    evidence_refs = _memory_dict_list(decision.get("evidence_refs"))
    if mutation_type not in _SAFE_MEMORY_MUTATION_TYPES:
        issues.append(
            "apply_mutation requires mutation_type to be exactly one of: "
            + ", ".join(sorted(_SAFE_MEMORY_MUTATION_TYPES))
        )
    if mutation_type == "upsert_pages":
        if not pages:
            issues.append("upsert_pages requires a non-empty pages array")
        for index, page in enumerate(pages[:5], start=1):
            page_type = _clean(page.get("page_type") or page.get("type"))
            title = _clean(page.get("title"))
            summary = _clean(page.get("summary") or page.get("summary_text"))
            body = _clean(page.get("body_markdown") or page.get("body") or page.get("text"))
            source_urls = _memory_value_list(page.get("source_urls") or page.get("source_urls_json"))
            source_document_ids = _memory_value_list(
                page.get("source_document_ids") or page.get("source_document_ids_json")
            )
            if page_type not in _MEMORY_PAGE_TYPES:
                issues.append(f"page {index} needs a valid page_type")
            if not title:
                issues.append(f"page {index} needs a title")
            if not summary:
                issues.append(f"page {index} needs a summary")
            if not body:
                issues.append(f"page {index} needs body_markdown")
            if not source_urls and not source_document_ids and not evidence_refs:
                issues.append(f"page {index} needs source_urls, source_document_ids, or evidence_refs")
    elif mutation_type == "link_pages":
        if not _clean(decision.get("page_id")):
            issues.append("link_pages requires page_id")
        if not _clean(decision.get("target_page_id")):
            issues.append("link_pages requires target_page_id")
    elif mutation_type == "metadata_patch":
        if not _clean(decision.get("page_id")):
            issues.append("metadata_patch requires page_id")
        if not isinstance(decision.get("metadata_patch"), dict) or not decision.get("metadata_patch"):
            issues.append("metadata_patch requires a non-empty metadata_patch object")
    return list(dict.fromkeys(issues))[:8]


def _memory_reflection_repair_user_prompt(
    *,
    base_prompt: str,
    invalid_decision: dict[str, Any],
    contract_issues: list[str],
) -> str:
    issue_block = "\n".join(f"- {item}" for item in contract_issues if _clean(item)) or "- Unknown contract issue"
    return (
        f"{base_prompt}\n\n"
        "Your previous memory action did not satisfy the safe Zopedia write contract.\n\n"
        "Contract issues:\n"
        f"{issue_block}\n\n"
        "Previous JSON:\n"
        f"{_json_dumps(invalid_decision, limit=3000)}\n\n"
        "Re-emit the full JSON object. If the evidence supports a safe commit, use action=apply_mutation with "
        "mutation_type=upsert_pages, link_pages, or metadata_patch and complete fields. If you cannot express the "
        "change under that exact contract, use propose_change or no_action."
    )


def _memory_contract_proposal_decision(
    *,
    decision: dict[str, Any],
    issues: list[str],
    rationale: str,
    run_id: str,
) -> dict[str, Any]:
    original_decision = dict(decision)
    issue_text = "; ".join(item for item in issues if _clean(item))
    return {
        "action": "propose_change",
        "rationale": (
            "The memory update looked durable, but it did not satisfy the safe commit contract"
            + (f": {issue_text}." if issue_text else ".")
        ),
        "mutation_type": "",
        "proposal_type": "memory_contract_repair",
        "page_id": _clean(original_decision.get("page_id")),
        "target_page_id": _clean(original_decision.get("target_page_id")),
        "title": _clean(original_decision.get("title")) or "Review Zopedia memory update",
        "pages": [],
        "metadata_patch": {},
        "evidence_refs": _memory_dict_list(original_decision.get("evidence_refs")),
        "payload": {
            "run_id": run_id,
            "original_rationale": rationale,
            "contract_issues": issues,
            "memory_decision": original_decision,
        },
        "allow_risky": False,
    }


def _run_post_answer_memory_agent(
    *,
    llm: LLMClient,
    service: QueryService,
    run_id: str,
    query: str,
    answer_markdown: str,
    confidence: str,
    limitations: list[str],
    quality_review: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    conversation_history: list[dict[str, Any]] | None,
    prefetched_context: str,
    timeout_seconds: int,
    progress_callback: ProgressCallback | None,
    write_policy: str = "safe_auto",
) -> dict[str, Any]:
    """Let Zopedia update or propose memory changes after a grounded answer."""
    normalized_write_policy = _normalize_zopedia_write_policy(write_policy)
    if normalized_write_policy == "none":
        return {"status": "skipped", "reason": "write_policy_none", "write_policy": normalized_write_policy}
    if not _review_allows_memory(quality_review):
        return {"status": "skipped", "reason": "answer_not_approved", "write_policy": normalized_write_policy}
    if int(get_config_param(_P_POST_ANSWER_MEMORY_ENABLED)) == 0:
        return {"status": "skipped", "reason": "disabled", "write_policy": normalized_write_policy}
    if not _clean(answer_markdown):
        return {"status": "skipped", "reason": "empty_answer", "write_policy": normalized_write_policy}
    if not _has_memory_candidate(tool_calls=tool_calls, prefetched_context=prefetched_context):
        return {"status": "skipped", "reason": "no_collected_evidence", "write_policy": normalized_write_policy}

    _emit_progress(
        progress_callback,
        stage="memory_reflection_start",
        message="Checking whether Zopedia memory should change.",
        progress=0.992,
        tool_call_count=len(tool_calls),
    )
    memory_prompt = _memory_reflection_user_prompt(
        query=query,
        answer_markdown=answer_markdown,
        confidence=confidence,
        limitations=limitations,
        quality_review=quality_review,
        tool_calls=tool_calls,
        conversation_history=conversation_history,
        prefetched_context=prefetched_context,
        write_policy=normalized_write_policy,
    )
    deadline = time.monotonic() + timeout_seconds
    try:
        payload = _run_hidden_step_with_timeout(
            label="post-answer memory reflection",
            timeout_seconds=max(float(timeout_seconds), 0),
            progress_callback=progress_callback,
            progress=0.992,
            func=lambda: generate_json_via_aql_zopedia_gateway(
                llm_client=llm,
                surface="zopedia.memory",
                purpose="agent.memory_reflection",
                call_type="research_grade",
                system_prompt=get_prompt(_AGENT_MEMORY_SYSTEM_PROMPT),
                user_prompt=memory_prompt,
                schema_name="zopedia_memory_reflection",
                schema=_MEMORY_SCHEMA,
                deadline_monotonic=deadline,
                metadata={
                    "run_id": run_id,
                    "write_policy": normalized_write_policy,
                    "tool_call_count": len(tool_calls),
                },
            ),
        )
    except TimeoutError as exc:
        _emit_progress(
            progress_callback,
            stage="memory_reflection_timeout",
            message="Zopedia memory reflection timed out; answer is still returned.",
            progress=0.996,
            error=_safe_agent_error_text(exc),
        )
        return {"status": "timeout", "reason": _safe_agent_error_text(exc), "write_policy": normalized_write_policy}
    except Exception as exc:
        _emit_progress(
            progress_callback,
            stage="memory_reflection_failed",
            message="Zopedia memory reflection failed; answer is still returned.",
            progress=0.996,
            error=_safe_agent_error_text(exc),
        )
        return {"status": "failed", "reason": _safe_agent_error_text(exc), "write_policy": normalized_write_policy}

    decision = dict(payload or {})
    reasoning_trace = _clean(decision.get("__reasoning_content"))
    if reasoning_trace:
        _emit_progress(
            progress_callback,
            stage="model_reasoning_trace",
            message="Memory maintainer reasoning trace captured.",
            progress=0.995,
            reasoning_trace=reasoning_trace,
            source="memory_reflection",
        )
    action = _memory_decision_action(decision)
    rationale = _clean(decision.get("rationale"))
    repair_issues = _memory_apply_contract_issues(decision) if normalized_write_policy == "safe_auto" else []
    if action == "apply_mutation" and repair_issues:
        _emit_progress(
            progress_callback,
            stage="memory_reflection_repair_start",
            message="Repairing Zopedia memory action shape.",
            progress=0.995,
            memory_action=action,
            contract_issues=repair_issues,
        )
        try:
            repaired_payload = _run_hidden_step_with_timeout(
                label="post-answer memory reflection repair",
                timeout_seconds=max(deadline - time.monotonic(), 0),
                progress_callback=progress_callback,
                progress=0.995,
                func=lambda: generate_json_via_aql_zopedia_gateway(
                    llm_client=llm,
                    surface="zopedia.memory",
                    purpose="agent.memory_reflection_repair",
                    call_type="schema_repair",
                    system_prompt=get_prompt(_AGENT_MEMORY_SYSTEM_PROMPT),
                    user_prompt=_memory_reflection_repair_user_prompt(
                        base_prompt=memory_prompt,
                        invalid_decision=decision,
                        contract_issues=repair_issues,
                    ),
                    schema_name="zopedia_memory_reflection_repair",
                    schema=_MEMORY_SCHEMA,
                    deadline_monotonic=deadline,
                    metadata={
                        "run_id": run_id,
                        "write_policy": normalized_write_policy,
                        "contract_issue_count": len(repair_issues),
                    },
                ),
            )
            if isinstance(repaired_payload, dict) and repaired_payload:
                decision = dict(repaired_payload)
                action = _memory_decision_action(decision)
                rationale = _clean(decision.get("rationale"))
                repair_issues = _memory_apply_contract_issues(decision)
        except Exception as exc:
            repair_issues = [
                *repair_issues,
                f"memory reflection repair failed: {_safe_agent_error_text(exc)}",
            ][:8]
        if action == "apply_mutation" and repair_issues:
            decision = _memory_contract_proposal_decision(
                decision=decision,
                issues=repair_issues,
                rationale=rationale,
                run_id=run_id,
            )
            action = "propose_change"
            rationale = _clean(decision.get("rationale"))
    if action == "no_action":
        _emit_progress(
            progress_callback,
            stage="memory_reflection_complete",
            message=rationale or "Zopedia memory does not need an update.",
            progress=0.996,
            memory_action="no_action",
        )
        return {
            "status": "completed",
            "action": "no_action",
            "rationale": rationale,
            "decision": decision,
            "write_policy": normalized_write_policy,
        }

    if normalized_write_policy == "propose" and action == "apply_mutation":
        action = "propose_change"
        decision["proposal_type"] = _clean(decision.get("proposal_type")) or _clean(decision.get("mutation_type")) or "update"
        decision["title"] = _clean(decision.get("title")) or "Review Zopedia memory update"
        decision["rationale"] = rationale or "The active write policy requires review before committing this memory update."

    if action == "apply_mutation" and not _clean(decision.get("mutation_type")):
        action = "propose_change"
        decision["proposal_type"] = _clean(decision.get("proposal_type")) or "update"
        decision["title"] = _clean(decision.get("title")) or "Review Zopedia memory update"
        decision["rationale"] = rationale or "The memory reflection requested a mutation without a mutation type."

    if action == "apply_mutation":
        tool_name = "zopedia.apply_mutation"
        arguments = {
            "mutation_type": _clean(decision.get("mutation_type")),
            "page_id": _clean(decision.get("page_id")),
            "target_page_id": _clean(decision.get("target_page_id")),
            "pages": list(decision.get("pages") or []),
            "metadata_patch": dict(decision.get("metadata_patch") or {}),
            "evidence_refs": list(decision.get("evidence_refs") or []),
            "rationale": rationale,
            "payload": dict(decision.get("payload") or {}),
            "allow_risky": False,
        }
    else:
        tool_name = "zopedia.propose_change"
        arguments = {
            "proposal_type": _clean(decision.get("proposal_type")) or "update",
            "page_id": _clean(decision.get("page_id")),
            "title": _clean(decision.get("title")) or "Review Zopedia memory update",
            "rationale": rationale or "Zopedia memory maintainer requested review.",
            "payload": {
                **dict(decision.get("payload") or {}),
                "memory_decision": decision,
                "run_id": run_id,
            },
        }

    _emit_progress(
        progress_callback,
        stage="memory_mutation_start",
        message=f"Applying Zopedia memory action: {tool_name}.",
        progress=0.996,
        memory_action=action,
    )
    try:
        tool_result = _run_hidden_step_with_timeout(
            label=f"{tool_name} memory action",
            timeout_seconds=max(min(deadline - time.monotonic(), float(get_config_param(_P_TOOL_CALL_TIMEOUT_SECONDS))), 0),
            progress_callback=progress_callback,
            progress=0.997,
            func=lambda: invoke_tool(
                service=service,
                tool_name=tool_name,
                arguments=arguments,
                run_id=run_id,
            ),
        )
        result_summary = _summarize_tool_result(tool_result)
        tool_call = {
            "tool_call_id": f"agtc_memory_{len(tool_calls) + 1}",
            "tool_name": tool_name,
            "arguments": arguments,
            "status": "completed",
            "error": None,
            "result_summary": result_summary,
        }
        _emit_progress(
            progress_callback,
            stage="memory_mutation_complete",
            message=_truncate(result_summary.get("user_preview") or result_summary.get("preview_text") or "Zopedia memory action completed.", limit=300),
            progress=0.998,
            memory_action=action,
        )
        return {
            "status": "completed",
            "action": action,
            "rationale": rationale,
            "decision": decision,
            "tool_call": tool_call,
            "tool_result": tool_result,
            "write_policy": normalized_write_policy,
        }
    except Exception as exc:
        safe_error = _safe_agent_error_text(exc)
        _emit_progress(
            progress_callback,
            stage="memory_mutation_failed",
            message="Zopedia memory action failed; answer is still returned.",
            progress=0.998,
            error=safe_error,
            memory_action=action,
        )
        return {
            "status": "failed",
            "action": action,
            "rationale": rationale,
            "decision": decision,
            "tool_name": tool_name,
            "arguments": arguments,
            "reason": safe_error,
            "write_policy": normalized_write_policy,
        }


def _run_zopedia_agent_loop(
    *,
    query: str,
    run_id: str | None = None,
    task: str = "agent_answer",
    surface: str = "zopedia.chat",
    force_refresh: bool = False,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    service: QueryService | None = None,
    llm_client: LLMClient | None = None,
    progress_callback: ProgressCallback | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    persist_findings: bool = True,
    write_policy: str | None = None,
    tool_call_timeout_seconds: int | None = None,
    llm_step_timeout_seconds: int | None = None,
    prefetch_timeout_seconds: int | None = None,
    run_timeout_seconds: float | None = None,
    initial_evidence: list[dict[str, Any]] | None = None,
    response_schema: dict[str, Any] | None = None,
    response_schema_name: str | None = None,
    request_context: dict[str, Any] | None = None,
    resume_state: dict[str, Any] | None = None,
    answer_review_owner: str = "agent",
    checkpoint_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    from .agents.research_session import NullResearchSessionStore, create_research_session

    tool_call_timeout_seconds = int(
        tool_call_timeout_seconds
        if tool_call_timeout_seconds is not None
        else get_config_param(_P_TOOL_CALL_TIMEOUT_SECONDS)
    )
    llm_step_timeout_seconds = int(
        llm_step_timeout_seconds
        if llm_step_timeout_seconds is not None
        else get_config_param(_P_LLM_STEP_TIMEOUT_SECONDS)
    )
    prefetch_timeout_seconds = int(
        prefetch_timeout_seconds
        if prefetch_timeout_seconds is not None
        else min(tool_call_timeout_seconds, 15)
    )
    effective_context = research_request_context(
        query,
        request_context,
        write_policy=_normalize_zopedia_write_policy(write_policy, persist_findings=persist_findings),
    )
    run_budget = ResearchBudget.start(
        run_timeout_seconds,
        llm_step_timeout_seconds or 60,
        research_required=int(max_tool_calls) > 0,
    )
    resolved_run_id = _clean(run_id) or f"agrun_{uuid.uuid4().hex[:10]}"
    if not re.fullmatch(r"agrun_[a-zA-Z0-9_-]{8,64}", resolved_run_id):
        raise ValueError("Invalid research run id.")
    resolved_llm = llm_client or load_aql_zopedia_llm_client(
        surface="zopedia.agent",
    )
    client_config = getattr(resolved_llm, "config", None)
    provider = _clean(getattr(client_config, "provider", ""))
    model = _clean(getattr(client_config, "model", ""))
    session = create_research_session(
        run_id=resolved_run_id,
        surface=surface,
        task=task,
        query=query,
        provider=provider,
        model=model,
        metadata={
            "force_refresh": bool(force_refresh),
            "max_tool_calls": int(max_tool_calls),
            "persist_findings": bool(persist_findings),
            "write_policy": _clean(write_policy),
            "run_timeout_seconds": run_timeout_seconds,
            "request_context": effective_context,
            "effective_budget": {
                "max_tool_calls": int(max_tool_calls),
                "run_timeout_seconds": run_timeout_seconds,
                "tool_call_timeout_seconds": tool_call_timeout_seconds,
                "llm_step_timeout_seconds": llm_step_timeout_seconds,
                "prefetch_timeout_seconds": prefetch_timeout_seconds,
            },
        },
        store=NullResearchSessionStore() if not persist_findings else None,
    )
    session_progress_callback = session.progress_callback(
        progress_callback, checkpoint_callback=checkpoint_callback,
    )
    try:
        with research_run_context(resolved_run_id):
            result = _execute_zopedia_agent_loop(
                run_id=resolved_run_id,
                query=query,
                task=task,
                surface=surface,
                force_refresh=force_refresh,
                max_tool_calls=max_tool_calls,
                service=service,
                llm_client=resolved_llm,
                progress_callback=session_progress_callback,
                conversation_history=conversation_history,
                persist_findings=persist_findings,
                write_policy=write_policy,
                tool_call_timeout_seconds=tool_call_timeout_seconds,
                llm_step_timeout_seconds=llm_step_timeout_seconds,
                prefetch_timeout_seconds=prefetch_timeout_seconds,
                run_budget=run_budget,
                initial_evidence=initial_evidence,
                response_schema=response_schema,
                response_schema_name=response_schema_name,
                request_context=effective_context,
                resume_state=resume_state,
                answer_review_owner=answer_review_owner,
            )
    except BaseException as exc:
        session.finish(error=exc)
        session.close()
        raise
    session.finish(result=result)
    result.setdefault("runtime", {})
    result["runtime"].update(session.runtime_metadata())
    result["runtime"]["budget"] = run_budget.metadata()
    session.close()
    return result


def _execute_zopedia_agent_loop(
    *,
    run_id: str,
    query: str,
    task: str = "agent_answer",
    surface: str = "zopedia.chat",
    force_refresh: bool = False,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    service: QueryService | None = None,
    llm_client: LLMClient | None = None,
    progress_callback: ProgressCallback | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    persist_findings: bool = True,
    write_policy: str | None = None,
    tool_call_timeout_seconds: int | None = None,
    llm_step_timeout_seconds: int | None = None,
    prefetch_timeout_seconds: int | None = None,
    run_budget: ResearchBudget | None = None,
    initial_evidence: list[dict[str, Any]] | None = None,
    response_schema: dict[str, Any] | None = None,
    response_schema_name: str | None = None,
    request_context: dict[str, Any] | None = None,
    resume_state: dict[str, Any] | None = None,
    answer_review_owner: str = "agent",
) -> dict[str, Any]:
    budget = run_budget or ResearchBudget.start(None, llm_step_timeout_seconds or 60)
    normalized_query = str(query or "").strip()
    agent_query = normalized_query
    followup_resolved = bool(conversation_history)
    if tool_call_timeout_seconds is None:
        tool_call_timeout_seconds = int(get_config_param(_P_TOOL_CALL_TIMEOUT_SECONDS))
    tool_call_timeout_seconds = max(int(tool_call_timeout_seconds), 1)
    if llm_step_timeout_seconds is None:
        llm_step_timeout_seconds = int(get_config_param(_P_LLM_STEP_TIMEOUT_SECONDS))
    llm_step_timeout_seconds = max(int(llm_step_timeout_seconds), 1)
    if prefetch_timeout_seconds is None:
        prefetch_timeout_seconds = min(tool_call_timeout_seconds, 15)
    prefetch_timeout_seconds = max(int(prefetch_timeout_seconds), 1)
    resolved_write_policy = _normalize_zopedia_write_policy(write_policy, persist_findings=persist_findings)
    if answer_review_owner not in {"agent", "downstream"}:
        raise ValueError("Unknown research answer review owner")
    if answer_review_owner == "downstream" and (persist_findings or resolved_write_policy != "none"):
        raise ValueError("Deferred research review cannot authorize retained answers or knowledge writes")

    request_context = research_request_context(
        normalized_query, request_context, write_policy=resolved_write_policy
    )
    resumed = dict(resume_state or {})
    if not resumed.get("continued_request") and resumed.get("write_policy") in {"none", "propose"}:
        if resumed["write_policy"] == "none" or resolved_write_policy == "safe_auto":
            resolved_write_policy = resumed["write_policy"]
            request_context["write_policy"] = resolved_write_policy
    research_state = dict(resumed.get("research_state") or {})
    context_state = dict(resumed.get("working_context") or {})
    source_policy = (
        "retained_only"
        if not request_context["allow_web"]
        or (not resumed.get("continued_request") and resumed.get("source_policy") == "retained_only")
        else "available"
    )
    pending_action = resumed.get("pending_action")

    if not agent_query:
        return _with_aql_evidence_pack(
            {
                "run_id": run_id,
                "status": "failed",
                "mode": "sync",
                "model": "",
                "tool_calls": [],
                "answer_markdown": "",
                "confidence": "low",
                "limitations": [],
                "error": "Empty query.",
                "query": agent_query,
                "original_query": normalized_query,
                "followup_resolved": followup_resolved,
            }
        )

    _emit_progress(
        progress_callback,
        stage="preparing",
        message="Preparing research",
        progress=0.02,
    )
    resolved_service = service or QueryService.from_environment()
    if source_policy == "retained_only" and isinstance(resolved_service, QueryService):
        resolved_service = replace(
            resolved_service, data_access=replace(resolved_service.data_access, materialized_only=True)
        )
    resolved_llm = llm_client or load_aql_zopedia_llm_client(
        surface="zopedia.agent",
    )
    if resolved_llm is None:
        return _with_aql_evidence_pack(
            {
                "run_id": run_id,
                "status": "unavailable",
                "mode": "sync",
                "model": "",
                "tool_calls": [],
                "answer_markdown": "",
                "confidence": "low",
                "limitations": [],
                "error": "LLM runtime is unavailable.",
                "query": agent_query,
                "original_query": normalized_query,
                "followup_resolved": followup_resolved,
            }
        )
    _emit_progress(
        progress_callback,
        stage="start",
        message="Preparing Zopedia agent.",
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
    full_tool_registry = build_tool_registry(resolved_service)
    allowed_tool_catalog = _tool_catalog_for_write_policy(full_tool_registry.catalog(), resolved_write_policy)
    allowed_tool_names = request_context.get("allowed_tool_names")
    if allowed_tool_names is not None:
        if not isinstance(allowed_tool_names, list) or not all(
            isinstance(name, str) and name for name in allowed_tool_names
        ):
            raise ValueError("Invalid research tool access scope")
        allowed_tool_catalog = [tool for tool in allowed_tool_catalog
                                if tool.get("name") in allowed_tool_names]
    allowed_source_access = request_context.get("allowed_source_access")
    if allowed_source_access is not None:
        if not isinstance(allowed_source_access, list) or not set(allowed_source_access) <= {"supplied", "retained_or_data", "web"}:
            raise ValueError("Invalid research source access scope")
        allowed_tool_catalog = [tool for tool in allowed_tool_catalog
                                if tool.get("source_access") in allowed_source_access]
    if source_policy == "retained_only":
        allowed_tool_catalog = [tool for tool in allowed_tool_catalog if tool.get("source_access") != "web"]
    tool_registry = ToolRegistry(allowed_tool_catalog)
    deferred_tool_discovery = int(get_config_param(_P_DEFERRED_TOOL_DISCOVERY_ENABLED)) != 0
    if deferred_tool_discovery:
        tool_search_entry = tool_registry.get("tools.search")
        core_names = {
            "tools.search",
            "research.list_supplied_evidence",
            "research.read_supplied_evidence",
            "research.completed_investigations",
            "research.search_evidence",
            "research.read_evidence",
            "zopedia.search_pages",
            "zopedia.read_page",
            "research.live_event_evidence",
            "research.open_page",
        }
        tool_catalog = [tool for tool in tool_registry.catalog() if tool["name"] in core_names]
    else:
        tool_catalog = tool_registry.catalog()
    if request_context.get("conversation_runs") and (
        allowed_tool_names is None or "conversation.research_runs" in allowed_tool_names
    ):
        tool_catalog.append(
            {
                "name": "conversation.research_runs",
                "description": "Read actual execution outcomes and recorded events from this conversation. Use for prior failures, timing, completed work or research progress. Only this authenticated conversation is available.",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "A prior run ID from the supplied context; empty reads all prior outcomes.",
                        }
                    },
                    "required": ["run_id"],
                },
            }
        )
    # Inject conversation.prior_answers tool when chat history is available
    if conversation_history and (
        allowed_tool_names is None or "conversation.prior_answers" in allowed_tool_names
    ):
        tool_catalog.append(
            {
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
            }
        )
    _emit_progress(
        progress_callback,
        stage="tool_catalog_ready",
        message=f"Loaded {len(tool_catalog)} available modules.",
        progress=0.08,
        tool_count=len(tool_catalog),
        registry_tool_count=len(tool_registry),
        deferred_discovery=deferred_tool_discovery,
    )
    prefetched_context = ""
    prefetched_evidence = ""
    # The first model decision determines whether research is needed. Hidden
    # prefetch anchored unrelated requests to old answers before intent was read.
    tool_calls: list[dict[str, Any]] = [
        dict(call) for call in resumed.get("tool_calls") or [] if isinstance(call, dict)
    ]
    if resumed.get("continued_request"):
        tool_calls = [{**call, "carried_forward": True} for call in tool_calls]
    if initial_evidence:
        evidence = [dict(item) for item in initial_evidence if isinstance(item, dict)]
        evidence_text = json.dumps(evidence, ensure_ascii=False, default=str)
        tool_calls.append(
            {
                "tool_call_id": "agtc_supplied_" + uuid.uuid4().hex,
                "tool_name": "research.supplied_context",
                "arguments": {},
                "status": "completed",
                "error": None,
                "origin_run_id": run_id,
                "origin_turn_id": request_context.get("turn_id", ""),
                "result_summary": {
                    "preview_text": _truncate(evidence_text, limit=800),
                    "llm_context_text": _truncate(evidence_text, limit=4000),
                    "evidence_context_text": evidence_text,
                    "result_type": "supplied_context",
                    "evidence_refs": [
                        ref
                        for item in evidence
                        for ref in (
                            [
                                {
                                    key: item[key]
                                    for key in ("source_id", "document_id", "url", "published_at")
                                    if item.get(key)
                                }
                            ]
                            + [dict(ref) for ref in item.get("source_refs") or [] if isinstance(ref, dict)]
                        )
                        if ref
                    ],
                    "source_links": [
                        {"url": item["url"], "title": item.get("title", "")}
                        for item in evidence
                        if item.get("url")
                    ],
                },
            }
        )
    seen_calls: set[str] = set()
    _run_start_time = time.monotonic()
    seen_calls = {
        f"{call.get('tool_name')}::{_json_dumps(call.get('arguments') or {},limit=1200)}"
        for call in tool_calls
    }
    final_answer = ""
    best_draft = str(resumed.get("draft_answer") or "")
    final_confidence = str(resumed.get("confidence") or "low")
    direct_structured_payload_used = False
    limitations = []
    quality_review = {"status": "not_run"} if answer_review_owner == "agent" else {"status": "deferred", "owner": "downstream"}
    memory_update = {"status": "not_run"}
    trajectory_reviews = []
    trajectory_killed = False
    trajectory_restart_count = 0
    reviewed_draft = False
    planning_repairs = 0
    discovery_calls = 0
    iteration = 0

    def checkpoint(stage="research_checkpoint"):
        state = {
            "schema_version": 1,
            "run_id": run_id,
            "turn_id": request_context.get("turn_id"),
            "original_query": normalized_query,
            "request_context": request_context,
            "research_state": research_state,
            "working_context": context_state,
            "source_policy": source_policy,
            "write_policy": resolved_write_policy,
            "tool_calls": tool_calls,
            "pending_action": pending_action,
            "draft_answer": final_answer or best_draft,
            "confidence": final_confidence,
            "quality_review": quality_review,
        }
        _emit_progress(
            progress_callback,
            stage=stage,
            message="Research progress saved.",
            progress=0.4,
            _checkpoint=state,
        )
        return state

    artifact = None
    structured_repair_attempted = False
    try:
        while int(max_tool_calls) > 0 and budget.remaining() > 0:
            if _budgeted_tool_count(tool_calls) >= int(max_tool_calls):
                break
            iteration += 1
            step_progress = min(
                0.15 + 0.65 * _budgeted_tool_count(tool_calls) / max(int(max_tool_calls), 1), 0.88
            )
            checkpoint()
            _emit_progress(
                progress_callback,
                stage="planner_start",
                message="Choosing the next useful research step.",
                progress=step_progress,
                iteration=iteration,
                tool_call_count=_budgeted_tool_count(tool_calls),
            )
            planning_context = json.dumps(
                {
                    "request_context": _prompt_request_context(request_context),
                    "research_state": research_state,
                    "source_policy": source_policy,
                    "seconds_remaining": round(budget.remaining(), 1),
                    "pending_action": pending_action,
                    "answer_review": {
                        key: quality_review[key] for key in (
                            "status", "verdict", "critique_summary", "unsupported_claims",
                            "evidence_gaps", "research_requests", "reviewed_answer_sha256", "answer_sha256",
                        ) if key in quality_review
                    },
                },
                ensure_ascii=False,
            )
            def planning_prompt_for(visible):
                return _planner_user_prompt(
                    query=agent_query, task=task, surface=surface, tool_catalog=tool_catalog,
                    tool_calls=visible, max_tool_calls=max_tool_calls,
                    conversation_history=conversation_history, prefetched_context=prefetched_context,
                ) + "\n\nCurrent planning state:\n" + planning_context
            selected_calls = context_state.get("tool_call_ids")
            planning_prompt = planning_prompt_for([
                call for call in tool_calls if selected_calls is None or call["tool_call_id"] in selected_calls
            ])
            def research_step():
                try:
                    return generate_json_via_aql_zopedia_gateway(
                        llm_client=resolved_llm, surface=surface, purpose="agent.research_step", call_type="research_grade",
                        system_prompt=_planner_system_prompt(), user_prompt=planning_prompt,
                        schema_name="zopedia_agent_step", schema=_STEP_SCHEMA,
                        deadline_monotonic=budget.step_deadline("research", llm_step_timeout_seconds),
                        metadata={"run_id": run_id, "iteration": iteration, "max_tool_calls": max_tool_calls,
                                  "tool_call_count": _budgeted_tool_count(tool_calls)},
                    )
                except LLMContextWindowExceeded as exc:
                    return _run_evidence_model_step(
                        llm_client=resolved_llm, service=resolved_service, tool_calls=tool_calls,
                        context_state=context_state, prompt=planning_prompt_for,
                        system_prompt=_planner_system_prompt(), schema_name="zopedia_agent_step", schema=_STEP_SCHEMA,
                        surface=surface, purpose="agent.research_step",
                        deadline_monotonic=budget.step_deadline("research", budget.remaining()),
                        model_timeout_seconds=llm_step_timeout_seconds,
                        metadata={"run_id": run_id, "iteration": iteration}, progress_callback=progress_callback,
                        capacity_feedback=str(exc),
                        query=agent_query,
                        supplied_evidence_allowed=allowed_source_access is None or "supplied" in allowed_source_access,
                    )
            try:
                decision = _run_hidden_step_with_timeout(
                    label="research planning",
                    timeout_seconds=budget.remaining(),
                    progress_callback=progress_callback,
                    progress=step_progress,
                    func=research_step,
                )
                step_payload = {key: value for key, value in decision.items() if not key.startswith("__")}
                if list(Draft202012Validator(_STEP_SCHEMA).iter_errors(step_payload)):
                    from .aql_zopedia_engine import _repair_structured_payload_from_agent_answer

                    repaired = _repair_structured_payload_from_agent_answer(
                        answer_markdown=json.dumps(step_payload, ensure_ascii=False, default=str),
                        schema_name="zopedia_agent_step", schema=_STEP_SCHEMA, llm_client=resolved_llm,
                        surface=surface, task="agent.research_step",
                        deadline_monotonic=budget.step_deadline("research", llm_step_timeout_seconds),
                        original_request={"system_prompt": _planner_system_prompt(), "user_prompt": planning_prompt},
                    )
                    if repaired is None:
                        limitations.append("Planning stopped: structured response recovery was incomplete.")
                        break
                    decision = repaired
            except Exception as exc:
                from .agents.research_session import ResearchCancellationRequested

                if isinstance(exc, ResearchCancellationRequested):
                    raise
                if _looks_like_recoverable_model_step_error(exc) and (tool_calls or best_draft):
                    limitations.append(f"Planning stopped: {type(exc).__name__}")
                    break
                raise
            if isinstance(decision.get("research_state"), dict):
                research_state = {**research_state, **{
                    key: value for key, value in decision["research_state"].items() if value is not None
                }}
            update = decision.get("public_update") or {}
            if update.get("summary") or update.get("finding"):
                _emit_progress(
                    progress_callback,
                    stage="research_update",
                    message=str(
                        update.get("summary") or update.get("finding") or ""
                    ).strip(),
                    progress=step_progress,
                    public_update=update,
                )
            narrower_write_policy = decision.get("knowledge_policy")
            if narrower_write_policy == "none" or (
                narrower_write_policy == "propose" and resolved_write_policy == "safe_auto"
            ):
                resolved_write_policy = narrower_write_policy
                request_context["write_policy"] = resolved_write_policy
                tool_registry = ToolRegistry(
                    _tool_catalog_for_write_policy(tool_registry.catalog(), resolved_write_policy)
                )
                allowed_names = {tool["name"] for tool in tool_registry.catalog()}
                tool_catalog = [
                    tool
                    for tool in tool_catalog
                    if tool["name"] in allowed_names
                    or tool["name"].startswith("conversation.")
                ]
            if decision.get("source_policy") == "retained_only":
                source_policy = "retained_only"
                if isinstance(resolved_service, QueryService):
                    resolved_service = replace(
                        resolved_service,
                        data_access=replace(resolved_service.data_access, materialized_only=True),
                    )
                tool_catalog = [tool for tool in tool_catalog if tool.get("source_access") != "web"]
                tool_registry = ToolRegistry(
                    [tool for tool in tool_registry.catalog() if tool.get("source_access") != "web"]
                )
            action = decision.get("action")
            candidate = _clean_model_output(decision.get("answer_markdown"))
            if candidate:
                if not response_schema:
                    best_draft = candidate
                else:
                    try:
                        if not list(Draft202012Validator(response_schema).iter_errors(json.loads(candidate))):
                            best_draft = candidate
                    except (TypeError, ValueError):
                        pass
            if action == "final":
                if answer_review_owner == "agent" and best_draft and not reviewed_draft and budget.remaining() > 0:
                    reviewed_draft = True
                    reviewed_answer_sha256 = hashlib.sha256(best_draft.encode("utf-8")).hexdigest()
                    quality_review = _run_answer_judge(
                        llm=_synthesis_llm_client(resolved_llm),
                        run_id=run_id,
                        query=agent_query,
                        task=task,
                        surface=surface,
                        tool_calls=tool_calls,
                        draft_answer=best_draft,
                        draft_confidence=final_confidence,
                        limitations=limitations,
                        conversation_history=conversation_history,
                        prefetched_context=json.dumps(_prompt_request_context(request_context), ensure_ascii=False)
                        + "\n"
                        + prefetched_context,
                        timeout_seconds=budget.remaining(),
                        model_timeout_seconds=llm_step_timeout_seconds,
                        progress_callback=progress_callback,
                        response_schema=response_schema,
                        service=resolved_service,
                        context_state=context_state,
                        supplied_evidence_allowed=allowed_source_access is None or "supplied" in allowed_source_access,
                    )
                    if quality_review.get("status") == "completed":
                        best_draft = quality_review.get("answer_markdown") or best_draft
                        quality_review["reviewed_answer_sha256"] = reviewed_answer_sha256
                        quality_review["answer_sha256"] = hashlib.sha256(
                            _clean_model_output(best_draft).encode("utf-8")
                        ).hexdigest()
                        if quality_review.get("answer_points"):
                            research_state["answer_points"] = quality_review["answer_points"]
                        requests = quality_review.get("research_requests") or []
                        if requests:
                            research_state["next_actions"] = requests
                            pending_action = {"information_requests": requests}
                            checkpoint()
                            if budget.remaining() > 0:
                                continue
                final_answer = best_draft
                final_confidence = decision.get("confidence") or final_confidence
                pending_action = (
                    None if not (quality_review.get("research_requests") or []) else pending_action
                )
                break
            if action != "tool_call":
                planning_repairs += 1
                pending_action = {"error": "Return action tool_call or final using the advertised schema."}
                if planning_repairs >= 3:
                    break
                continue
            tool_name = _clean(decision.get("tool_name"))
            entry = _tool_entry_by_name(tool_catalog, tool_name)
            if not entry:
                # A deferred schema is hidden from the current prompt, not
                # unavailable. Resolve exact names only inside the already
                # authorized registry, then validate the canonical schema.
                entry = tool_registry.get(tool_name) or {}
                if entry:
                    tool_catalog.append(entry)
            arguments, error = _coerce_tool_arguments(decision.get("tool_arguments"))
            if entry and not error:
                arguments = _normalized_arguments(
                    entry,
                    arguments,
                    force_refresh=force_refresh if source_policy != "retained_only" else False,
                )
                errors = list(Draft202012Validator(entry["inputSchema"]).iter_errors(arguments))
                error = errors[0].message if errors else ""
            signature = f"{tool_name}::{_json_dumps(arguments or {},limit=1200)}"
            if not entry or error or signature in seen_calls:
                planning_repairs += 1
                pending_action = {
                    "tool_name": tool_name,
                    "error": error
                    or (
                        "Tool not available"
                        if not entry
                        else "This call already ran; use its result or choose another information request."
                    ),
                }
                _emit_progress(
                    progress_callback,
                    stage="planner_repair",
                    message="Correcting the next research action.",
                    progress=step_progress,
                    error=pending_action["error"],
                    tool_name=tool_name,
                )
                checkpoint()
                if planning_repairs >= 3:
                    break
                continue
            pending_action = {"tool_name": tool_name, "arguments": arguments}
            if decision.get("request_review") and trajectory_restart_count < int(
                get_config_param(_P_TRAJECTORY_MONITOR_MAX_RESTARTS)
            ):
                monitor = _run_trajectory_monitor(
                    llm=_synthesis_llm_client(resolved_llm),
                    run_id=run_id,
                    query=agent_query,
                    task=task,
                    surface=surface,
                    tool_catalog=tool_catalog,
                    tool_calls=tool_calls,
                    max_tool_calls=max_tool_calls,
                    restart_count=trajectory_restart_count,
                    timeout_seconds=budget.remaining(maximum=min(llm_step_timeout_seconds, 20)),
                    progress_callback=progress_callback,
                )
                trajectory_reviews.append(monitor)
                if monitor.get("decision") == "restart":
                    trajectory_restart_count += 1
                    pending_action = {
                        "correction": monitor.get("corrective_instruction"),
                        "proposed_action": pending_action,
                    }
                    checkpoint()
                    continue
            checkpoint()
            if budget.remaining() <= 0:
                break
            if tool_name == "tools.search":
                discovery_calls += 1
                if discovery_calls > max(int(get_config_param(_P_TOOL_DISCOVERY_MAX_STEPS)), 1):
                    break
            call_id = f"agtc_{len(tool_calls)+1}"
            _emit_progress(
                progress_callback,
                stage="tool_start",
                message="Reading the selected source or dataset.",
                progress=step_progress,
                tool_name=tool_name,
                tool_call_id=call_id,
                tool_arguments=arguments,
            )
            try:
                if allowed_tool_names is not None and tool_name not in allowed_tool_names:
                    raise ValueError(f"Tool is outside the requested research scope: {tool_name}")
                if tool_name == "conversation.research_runs":
                    records = [
                        row
                        for row in request_context.get("conversation_runs") or []
                        if not arguments.get("run_id")
                        or row.get("run_id") == arguments["run_id"]
                    ]
                    result = {
                        "summary": records,
                        "llm_context_text": serialize_llm_context(records),
                    }
                elif tool_name == "conversation.prior_answers":
                    result = _search_conversation_history(
                        conversation_history or [], arguments.get("search_text", "")
                    )
                else:
                    result = _invoke_tool_with_heartbeat(
                        service=resolved_service,
                        run_id=run_id,
                        tool_name=tool_name,
                        arguments=arguments,
                        progress_callback=progress_callback,
                        progress=step_progress,
                        tool_call_id=call_id,
                        tool_call_count=_budgeted_tool_count(tool_calls),
                        timeout_seconds=budget.remaining(maximum=tool_call_timeout_seconds),
                        tool_registry=tool_registry,
                        request_context=request_context,
                    )
                summary = _summarize_tool_result(result)
                call = {
                    "tool_call_id": call_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "status": "completed",
                    "error": None,
                    "result_summary": summary,
                }
                if tool_name == "tools.search":
                    _activate_discovered_tools(
                        tool_catalog=tool_catalog, tool_registry=tool_registry, search_result=result
                    )
                planning_repairs = 0
            except Exception as exc:
                from .agents.research_session import ResearchCancellationRequested

                if isinstance(exc, ResearchCancellationRequested):
                    raise
                call = {
                    "tool_call_id": call_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "result_summary": {"result_type": "error", "preview_text": str(exc)},
                }
            call["origin_run_id"] = run_id
            call["origin_turn_id"] = request_context.get("turn_id", "")
            tool_calls.append(call)
            if context_state.get("tool_call_ids") is not None:
                context_state["tool_call_ids"] = [*context_state["tool_call_ids"], call_id]
            seen_calls.add(signature)
            pending_action = None
            checkpoint()
            summary = call["result_summary"]
            _emit_progress(
                progress_callback,
                stage="tool_complete" if call["status"] == "completed" else "tool_failed",
                message=(
                    "Source reading complete."
                    if call["status"] == "completed"
                    else "Source reading failed; choosing another step."
                ),
                progress=step_progress,
                tool_name=tool_name,
                tool_call_id=call_id,
                source_links=summary.get("source_links"),
                result_preview=summary.get("user_preview"),
            )

        if not final_answer and best_draft:
            final_answer = best_draft
        if (
            (not final_answer or not response_schema)
            and not trajectory_killed
            and budget.remaining("synthesis") > 0
        ):
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
            # Reading and writing share the existing phase clock. Individual
            # model requests keep their own configured step limit.
            synthesis_deadline = budget.step_deadline(
                "synthesis", budget.remaining("synthesis") if budget.deadline is not None else llm_step_timeout_seconds
            )
            synthesis_started = time.monotonic()
            def _run_final_synthesis() -> None:
                try:
                    _final_result[0] = _run_evidence_model_step(
                        llm_client=synthesis_llm,
                        service=resolved_service,
                        tool_calls=tool_calls,
                        context_state=context_state,
                        query=agent_query,
                        supplied_evidence_allowed=allowed_source_access is None or "supplied" in allowed_source_access,
                        progress_callback=progress_callback,
                        surface=_clean(surface) or "zopedia.chat",
                        purpose="agent.final_synthesis",
                        system_prompt=(
                            "You are the shared AQL/Zopedia research synthesis engine. Use the collected sources and supplied context. "
                            "Return only the requested artifact JSON matching its schema. Preserve reported facts, business context and "
                            "useful hypotheses at their actual evidence strength. Do not wrap the artifact in answer_markdown, "
                            "confidence or limitations unless those fields belong to the artifact schema."
                            if response_schema
                            else get_prompt(_AGENT_FINAL_SYSTEM_PROMPT)
                        ),
                        prompt=lambda visible: _final_user_prompt(
                            query=agent_query,
                            task=task,
                            surface=surface,
                            tool_calls=visible,
                            conversation_history=conversation_history,
                            prefetched_context=json.dumps(
                                {
                                    "request_context": _prompt_request_context(request_context),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                            + prefetched_context,
                            response_schema_name=response_schema_name if response_schema else "",
                            selected_source_ids=research_state.get("selected_source_ids") or None,
                            answer_points=research_state.get("answer_points") or [],
                            draft_answer=best_draft,
                        ),
                        schema_name=response_schema_name or "zopedia_agent_final",
                        schema=response_schema or _FINAL_SCHEMA,
                        deadline_monotonic=synthesis_deadline,
                        model_timeout_seconds=llm_step_timeout_seconds,
                        metadata={
                            "run_id": run_id,
                            "task": task,
                            "tool_call_count": len(tool_calls),
                        },
                    )
                except BaseException as exc:
                    _final_error[0] = exc

            _final_thread = threading.Thread(target=copy_context().run, args=(_run_final_synthesis,), daemon=True)
            _final_thread.start()
            _heartbeat_interval = 5.0
            _heartbeat_elapsed = 0.0
            while _final_thread.is_alive():
                _final_thread.join(
                    timeout=max(min(_heartbeat_interval, synthesis_deadline - time.monotonic()), 0)
                )
                if _final_thread.is_alive():
                    _heartbeat_elapsed = time.monotonic() - synthesis_started
                    if time.monotonic() >= synthesis_deadline:
                        _final_error[0] = TimeoutError(
                            "Final synthesis exceeded its remaining phase budget."
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
            if _final_error[0] is not None and not final_answer:
                raise _final_error[0]
            if _final_error[0] is not None:
                limitations.append(f"Final writing step failed: {type(_final_error[0]).__name__}")
            final_payload = (
                _final_result[0]
                if _final_error[0] is None
                else (
                    json.loads(final_answer)
                    if response_schema
                    else {"answer_markdown": final_answer, "confidence": final_confidence}
                )
            )
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
            if response_schema:
                artifact = {key: value for key, value in final_payload.items() if not key.startswith("__")}
                # Preserve the paid draft before validating. The shared
                # structured owner can repair it without repeating acquisition.
                final_answer = json.dumps(artifact, ensure_ascii=False, default=str)
            else:
                final_answer = _clean_model_output(final_payload.get("answer_markdown"))
            final_confidence = _clean(final_payload.get("confidence")).lower() or final_confidence
            final_limitations = [
                _clean(item) for item in list(final_payload.get("limitations") or []) if _clean(item)
            ]
            for item in final_limitations:
                if item not in limitations:
                    limitations.append(item)
        if response_schema and final_answer:
            # Recover the complete draft before semantic review, including on
            # continuation. A formatting failure must not skip that review or
            # force the caller to repeat the completed research.
            from .aql_zopedia_engine import (
                parse_aql_zopedia_structured_answer,
                _repair_structured_payload_from_agent_answer,
            )
            if artifact is None:
                artifact = parse_aql_zopedia_structured_answer(final_answer)
            errors = list(Draft202012Validator(response_schema).iter_errors(artifact))
            if errors:
                structured_repair_attempted = True
                repaired = _repair_structured_payload_from_agent_answer(
                    answer_markdown=final_answer, schema_name=response_schema_name or "zopedia_agent_final",
                    schema=response_schema, llm_client=resolved_llm, surface=surface, task=task,
                    deadline_monotonic=budget.step_deadline("synthesis", llm_step_timeout_seconds),
                    original_request={"query": agent_query, "collected_evidence": _tool_history_prompt(tool_calls),
                                      "request_context": _prompt_request_context(request_context)},
                )
                if repaired is None:
                    raise ValueError("Structured synthesis violated its response schema: " + errors[0].message)
                final_answer = json.dumps(repaired, ensure_ascii=False, default=str)
    except Exception as exc:
        if not (response_schema and final_answer and isinstance(exc, ValueError)) and not isinstance(exc, (LLMAPIError, TimeoutError)) and not _looks_like_transient_transport_error(
            exc
        ):
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
            "provider": _clean(getattr(resolved_llm.config, "provider", "")),
            "model": str(resolved_llm.config.model),
            "tool_calls": tool_calls,
            "answer_markdown": final_answer or best_draft,
            "confidence": "low",
            "limitations": limitations + [limitation],
            "trajectory_reviews": trajectory_reviews,
            "error": error_text,
            "failure": {
                "stage": getattr(exc, "stage", None),
                "type": type(exc).__name__,
                "message": safe_error,
            },
            "query": agent_query,
            "original_query": normalized_query,
            "followup_resolved": followup_resolved,
            "request_context": request_context,
            "research_state": research_state,
            "continuation_state": checkpoint("research_interrupted"),
            "structured_repair_attempted": structured_repair_attempted,
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
                retain_knowledge=False,
            )
        return _with_aql_evidence_pack(error_result)

    checkpoint("research_collection_complete")
    structured_draft = final_answer if response_schema else ""
    delivered_answer_sha256 = hashlib.sha256(_clean_model_output(final_answer).encode("utf-8")).hexdigest()
    if quality_review.get("status") == "completed" and quality_review.get("answer_sha256") != delivered_answer_sha256:
        quality_review = {
            **quality_review, "status": "superseded",
            "superseded_by_answer_sha256": delivered_answer_sha256,
        }
    if (
        final_answer
        and answer_review_owner == "agent"
        and quality_review.get("status") != "completed"
        and not direct_structured_payload_used
        and budget.remaining("completion") > 0
    ):
        reviewed_answer_sha256 = delivered_answer_sha256
        quality_review = _run_answer_judge(
            llm=_synthesis_llm_client(resolved_llm),
            run_id=run_id,
            query=agent_query,
            task=task,
            surface=surface,
            tool_calls=tool_calls,
            draft_answer=final_answer,
            draft_confidence=final_confidence,
            limitations=limitations,
            conversation_history=conversation_history,
            prefetched_context=json.dumps(_prompt_request_context(request_context), ensure_ascii=False) + "\n" + prefetched_context,
            timeout_seconds=budget.remaining("completion"),
            model_timeout_seconds=llm_step_timeout_seconds,
            progress_callback=progress_callback,
            response_schema=response_schema,
            service=resolved_service,
            context_state=context_state,
            supplied_evidence_allowed=allowed_source_access is None or "supplied" in allowed_source_access,
        )
        if quality_review.get("status") == "completed":
            verdict = _clean(quality_review.get("verdict")).lower()
            judge_answer = _clean_model_output(quality_review.get("answer_markdown"))
            revised_answer_applied = False
            if judge_answer:
                revised_answer_applied = judge_answer != final_answer
                final_answer = judge_answer
            final_confidence = _clean(quality_review.get("confidence")).lower() or final_confidence
            reviewed_limitations = [
                _clean(item) for item in list(quality_review.get("limitations") or []) if _clean(item)
            ]
            if reviewed_limitations:
                limitations = reviewed_limitations
            quality_review = {
                "status": "completed",
                "verdict": verdict,
                "critique_summary": _clean(quality_review.get("critique_summary")),
                "confidence": final_confidence,
                "limitations": [
                    _clean(item) for item in list(quality_review.get("limitations") or []) if _clean(item)
                ],
                "unsupported_claims": [
                    _clean(item)
                    for item in list(quality_review.get("unsupported_claims") or [])
                    if _clean(item)
                ],
                "evidence_gaps": [
                    _clean(item) for item in list(quality_review.get("evidence_gaps") or []) if _clean(item)
                ],
                "revised_answer_applied": revised_answer_applied,
                "research_requests": quality_review.get("research_requests") or [],
                "reviewed_answer_sha256": reviewed_answer_sha256,
                "answer_sha256": hashlib.sha256(_clean_model_output(final_answer).encode("utf-8")).hexdigest(),
            }

    if quality_review.get("research_requests"):
        research_state["next_actions"] = quality_review["research_requests"]
        pending_action = {"information_requests": quality_review["research_requests"]}

    if response_schema and structured_draft:
        try:
            reviewed_payload = json.loads(final_answer)
            valid = not list(Draft202012Validator(response_schema).iter_errors(reviewed_payload))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            final_answer = structured_draft
            quality_review = {
                "status": "failed_contract",
                "reason": "Review did not preserve the structured response schema.",
            }
    answer_markdown = _clean_model_output(final_answer)
    # Confidence is a semantic judgment over the actual context. Tool counts
    # and whether a page was opened are not reliable confidence estimators.
    if resolved_write_policy != "none" and answer_markdown and budget.remaining("completion") > 0:
        memory_update = _run_post_answer_memory_agent(
            llm=_synthesis_llm_client(resolved_llm),
            service=resolved_service,
            run_id=run_id,
            query=agent_query,
            answer_markdown=answer_markdown,
            confidence=final_confidence,
            limitations=limitations,
            quality_review=quality_review,
            tool_calls=tool_calls,
            conversation_history=conversation_history,
            prefetched_context=prefetched_context,
            timeout_seconds=budget.remaining("completion", llm_step_timeout_seconds),
            progress_callback=progress_callback,
            write_policy=resolved_write_policy,
        )
        memory_tool_call = memory_update.get("tool_call") if isinstance(memory_update, dict) else None
        if isinstance(memory_tool_call, dict):
            tool_calls.append(memory_tool_call)
    status = "completed" if answer_markdown else "failed"
    _emit_progress(
        progress_callback,
        stage=status,
        message=(
            "Agent response ready."
            if status == "completed"
            else (
                "Trajectory monitor killed an off-contract thread."
                if trajectory_killed
                else "Agent run ended without an answer."
            )
        ),
        progress=1.0,
        tool_call_count=len(tool_calls),
        status=status,
    )
    duration = time.monotonic() - _run_start_time
    error_text = (
        None
        if status == "completed"
        else (
            "Trajectory monitor killed an off-contract thread."
            if trajectory_killed
            else "Agent did not produce an answer."
        )
    )
    result = {
        "run_id": run_id,
        "status": status,
        "mode": "sync",
        "provider": _clean(getattr(resolved_llm.config, "provider", "")),
        "model": str(resolved_llm.config.model),
        "synthesis_model": str(
            getattr(
                getattr(_synthesis_llm_client(resolved_llm), "config", object()),
                "model",
                resolved_llm.config.model,
            )
        ),
        "tool_calls": tool_calls,
        "answer_markdown": answer_markdown,
        "confidence": final_confidence,
        "limitations": limitations,
        "quality_review": quality_review,
        "answer_status": (
            "structured"
            if direct_structured_payload_used
            else "reviewed" if (
                quality_review.get("status") == "completed"
                and quality_review.get("answer_sha256") == hashlib.sha256(answer_markdown.encode("utf-8")).hexdigest()
            ) else "draft"
        ),
        "memory_update": memory_update,
        "trajectory_reviews": trajectory_reviews,
        "error": error_text,
        "query": agent_query,
        "original_query": normalized_query,
        "followup_resolved": followup_resolved,
        "write_policy": resolved_write_policy,
        "request_context": request_context,
        "research_state": research_state,
        "continuation_state": checkpoint("research_answer_saved"),
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
            quality_review=quality_review,
            retain_knowledge=resolved_write_policy != "none",
        )
    return _with_aql_evidence_pack(result)


_NON_EVIDENCE_TOOL_NAMES = {
    "analysis.read_raw_output",
    "scratchpad.read",
    "scratchpad.write",
    "system.capabilities",
    "tools.search",
    "trajectory.monitor",
    "zopedia.apply_mutation",
    "zopedia.list_maintenance_reports",
    "zopedia.list_mutations",
    "zopedia.list_proposals",
    "zopedia.propose_change",
    "zopedia.rollback_mutation",
}


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
    quality_review: dict[str, Any] | None = None,
    retain_knowledge: bool = True,
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
            quality_review=quality_review,
        )
    except Exception:
        pass

    if not retain_knowledge:
        return

    # --- Ephemeral scratchpad (for within-session retained_context) ---
    claims: list[str] = []
    symbols: set[str] = set()
    try:
        from .agents import write_entry

        for call in tool_calls:
            if str(call.get("status") or "") != "completed":
                continue
            if _clean(call.get("tool_name")) in _NON_EVIDENCE_TOOL_NAMES:
                continue
            result_summary = dict(call.get("result_summary") or {})
            context_text = _tool_history_prompt([call], full_evidence=True)
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
                "answer": answer if _review_allows_memory(quality_review or {}) else "",
                "quality_review": {key: (quality_review or {}).get(key, "") for key in ("status", "verdict")},
                "confidence": confidence,
                "symbols": sorted(symbols),
                "claim_count": len(claims),
                "tool_call_count": len(tool_calls),
            },
        )
    except Exception:
        pass

    # --- Write-back to retained evidence store (for future retained_context lookups) ---
    if status == "completed" and _review_allows_memory(quality_review or {}) and (claims or answer):
        try:
            _write_back_agent_evidence(
                run_id=run_id,
                query=query,
                answer=answer,
                claims=claims,
                symbols=sorted(symbols),
                source_refs=[
                    ref
                    for call in tool_calls
                    for ref in (call.get("result_summary") or {}).get("evidence_refs") or []
                    if isinstance(ref, dict)
                ],
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
    source_refs: list[dict[str, Any]] | None = None,
) -> None:
    """Write agent findings as evidence chunks to the SAA store for future retained_context lookups."""
    from services.saa import persist_agent_research_evidence

    persist_agent_research_evidence(
        run_id=run_id,
        query=query,
        answer=answer,
        claims=claims,
        symbols=symbols,
        source_refs=source_refs,
    )


__all__ = [
    "DEFAULT_MAX_TOOL_CALLS",
    "resolve_conversation_followup_query",
]
