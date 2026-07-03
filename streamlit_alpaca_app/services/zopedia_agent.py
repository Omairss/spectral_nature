from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Callable
from urllib.parse import urlparse

from data_access.query_service import QueryService
from .aql.evidence_pack import build_aql_evidence_pack
from .agent_tools import build_tool_catalog, invoke_tool
from .llm import (
    NARRATIVE_STYLE_RULE,
    AzureOpenAIChatJSONClient,
    DeepSeekChatJSONClient,
    LLMAPIError,
    OpenAIChatJSONClient,
    get_config_param,
    get_prompt,
    register_config_param,
    register_narrative_prompt,
)
from .aql_zopedia_engine import load_aql_zopedia_llm_client


LLMClient = OpenAIChatJSONClient | AzureOpenAIChatJSONClient | DeepSeekChatJSONClient
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
_P_PREFETCH_EVIDENCE_LIMIT = register_config_param(
    "Agent prefetch evidence limit",
    group="Zopedia",
    default=8,
    description="Max retained evidence chunks to pre-fetch and inject into the planner prompt",
)
_P_PREFETCH_CHUNK_TEXT_LIMIT = register_config_param(
    "Agent prefetch chunk text limit",
    group="Zopedia",
    default=300,
    description="Max chars per chunk text in the pre-fetched evidence block",
)
_P_MAX_TOOL_CALLS = register_config_param(
    "Agent max tool calls",
    group="Zopedia",
    default=6,
    description="Maximum number of tool calls the agent can make per query",
)
_P_BOOTSTRAP_TOOL_CALLS = register_config_param(
    "Agent bootstrap tool calls",
    group="Zopedia",
    default=0,
    description=(
        "Legacy deterministic evidence-call budget. Defaults to 0 so the LLM planner owns tool choice."
    ),
)
_P_LLM_STEP_TIMEOUT_SECONDS = register_config_param(
    "Agent LLM step timeout seconds",
    group="Zopedia",
    default=45,
    description="Maximum seconds to wait for one planner or synthesis LLM call before degrading gracefully",
)
_P_TOOL_CALL_TIMEOUT_SECONDS = register_config_param(
    "Agent tool call timeout seconds",
    group="Zopedia",
    default=25,
    description="Maximum seconds to wait for one agent tool call before degrading to the next step",
)
_P_CONVERSATION_HISTORY_LIMIT = register_config_param(
    "Agent conversation history limit",
    group="Zopedia",
    default=3000,
    description="Max chars for the compacted conversation history shown to the planner",
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
    description="Run a bounded evidence-sufficiency review before returning final Zopedia answers",
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
_ARGUMENT_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "value_kind": {
            "type": "string",
            "enum": ["string", "number", "boolean", "string_list", "json", "object", "object_list", "null"],
        },
        "string_value": {"type": ["string", "null"]},
        "number_value": {"type": ["number", "null"]},
        "boolean_value": {"type": ["boolean", "null"]},
        "string_list_value": {"type": ["array", "null"], "items": {"type": "string"}},
        "json_value": {},
        "object_value": {"type": ["object", "null"]},
        "object_list_value": {"type": ["array", "null"], "items": {"type": "object"}},
    },
    "required": [
        "name",
        "value_kind",
        "string_value",
        "number_value",
        "boolean_value",
        "string_list_value",
        "json_value",
        "object_value",
        "object_list_value",
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
    name="Zopedia Agent Final Answer (markdown response to user)",
    file="services/zopedia_agent.py",
    group="Zopedia",
    prompt=(
        f"You are the Spectral Nature Zopedia agent. {NARRATIVE_STYLE_RULE} "
        "Write a grounded markdown answer from the collected tool evidence only. "
        "Structure your answer: "
        "(1) Start with a **bold one-sentence verdict or key finding**. "
        "(2) Use ### headings to separate sections when the answer covers multiple themes or tickers. "
        "Every ### heading must be on its own line, followed by a blank line; never put body text on the same line as a heading. "
        "(3) Use **bold** for tickers and key metrics (e.g. **NVDA** +3.2%). "
        "(4) Use bullet points for lists of data points. "
        "(5) Keep paragraphs short, with blank lines between paragraphs; never return one large unbroken paragraph. "
        "(6) If you use a Markdown table, it must have a header row, separator row, and one row per line; if that is awkward, use bullets instead. "
        "(7) End with a brief takeaway or what to watch next. "
        "When Zopedia memory is used, name the supporting Zopedia page title or page_id. "
        "When live external evidence was used, lightly reference one or two supporting sources or URLs. "
        "If the answer discusses market, equity, ETF, sector, or rate-move impact, it must be grounded in observed market data from tools such as daily movers, price history, or analysis artifacts; otherwise state the missing observed-data gap. "
        "For current-driver questions, separate durable business background from current evidence. Older or retained evidence can explain why a company is sensitive to a force, but it cannot be presented as today's driver unless current tool evidence directly connects that force to today's move."
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

_AGENT_JUDGE_SYSTEM_PROMPT = register_narrative_prompt(
    name="Zopedia Agent Answer Judge (evidence sufficiency review)",
    file="services/zopedia_agent.py",
    group="Zopedia",
    prompt=(
        f"You are the Spectral Nature Zopedia answer judge. {NARRATIVE_STYLE_RULE} "
        "Review the draft answer against the collected evidence only. "
        "Look for unsupported claims, stale or missing evidence, false user premises, and overconfident wording. "
        "If the draft is good, accept it. If it needs correction, return a revised markdown answer that fixes the issue. "
        "If the evidence is too thin, return the best cautious answer and name the evidence gap. "
        "If the draft discusses market, equity, ETF, sector, or rate-move impact without observed price/mover/analysis evidence, mark it insufficient or revise it to say that observed market-impact evidence is missing. "
        "For current-driver questions, reject or revise answers that turn older retained evidence into today's cause. "
        "If the evidence only proves a broad peer, sector, or market move, revise the answer to say that rather than naming a precise macro, geopolitical, policy, or company-specific driver. "
        "A current-driver claim needs current evidence tying that driver to the ticker, its close peers, its sector, or the relevant market instrument; durable background alone is not enough. "
        "Do not introduce facts that are not present in the tool evidence or pre-fetched internal evidence. "
        "Fix malformed Markdown tables before accepting or revising the answer; every table needs a header row, separator row, and one row per line."
    ),
)

_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "revise", "insufficient"]},
        "critique_summary": {"type": "string"},
        "answer_markdown": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "evidence_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "verdict",
        "critique_summary",
        "answer_markdown",
        "confidence",
        "limitations",
        "unsupported_claims",
        "evidence_gaps",
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
        f"You are the Spectral Nature Zopedia trajectory monitor. {NARRATIVE_STYLE_RULE} "
        "You do not answer the user. You inspect the active task, surface, user request, and tool trace, then decide whether the research thread is on contract. "
        "Return double_down when the thread is anchored to the requested subject and the next step should continue. "
        "Return restart when the thread has wandered or compressed evidence too early but can recover with one clear corrective instruction. "
        "Return kill when the thread is off-contract, unsafe, fabricating, looping on low-signal tools, or likely to synthesize unsupported output. "
        "When the newest trace item has status planned, judge the proposed tool before it runs. "
        "If the proposed tool is recoverably off-task, return restart so the planner can choose a better next step; reserve kill for unrecoverable drift or exhausted restart budget. "
        "For named company/ticker tasks, judge organization before narrowness. Spillover recall is useful when the thread keeps the requested company as the center of gravity. "
        "Adjacent research is allowed: first-degree and second-degree neighbor tickers, parent/platform, peers, suppliers, customers, sector demand, policy, credit cycle, rates, regulation, and broad market context can all help explain the business. "
        "Return restart only when the thread stops organizing that recall around the requested company, swaps the primary subject, loops without adding evidence, or moves toward unsupported synthesis. "
        "A planned tool call does not need to be ticker-only; it should either deepen the primary company or collect adjacent context that can be clearly labeled later and kept inside the job budget. "
        "Corrective instructions must name only tools that appear in Available tool names. "
        "Do not kill a company-research thread solely because internal memory is empty while source-reading, live evidence, or web/page tools remain available within budget. "
        "For Zopedia/wiki tasks, treat search without page read, page read without source trace for dependent claims, and generated memory without source refs as incomplete. "
        "For current/live questions, retained/internal evidence alone is incomplete unless the task explicitly asks for durable memory. "
        "If a current thread has only prefetched, retained, or wiki search evidence and research.live_event_evidence remains available within budget, return restart with a corrective instruction to gather live/current evidence before final synthesis. "
        "Do not let the thread claim live search failed, returned nothing, or was unavailable unless the tool trace actually shows that live/current tool attempt. "
        "Do not use keyword blacklists; judge semantic fit to the task and evidence coverage."
    ),
)

_AGENT_MEMORY_SYSTEM_PROMPT = register_narrative_prompt(
    name="Zopedia Agent Memory Reflection (automatic wiki maintenance)",
    file="services/zopedia_agent.py",
    group="Zopedia",
    prompt=(
        f"You are the Spectral Nature Zopedia memory maintainer. {NARRATIVE_STYLE_RULE} "
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
    text = _clean(value)
    if not text:
        return ""
    for token in (
        "<|im_end|>",
        "<|endoftext|>",
        "<|end|>",
        "<|assistant|>",
        "<|user|>",
        "<|system|>",
    ):
        text = text.replace(token, " ")
    return _clean(text)


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
    result_messages = [_clean(item) for item in list(result.get("messages") or []) if _clean(item)]
    if isinstance(payload, dict):
        explicit_context = _clean(payload.get("llm_context_text"))
        if explicit_context:
            llm_context_text = _truncate(explicit_context, limit=int(get_config_param(_P_TOOL_RESULT_CONTEXT_LIMIT)))
        elif isinstance(payload.get("top_events"), list):
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
    if result_messages:
        message_text = "Tool messages:\n" + "\n".join(f"- {item}" for item in result_messages[:4])
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
        for item in list(payload.get("summary") or [])[:8]:
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
            }
            if any(evidence_ref.values()):
                evidence_refs.append(evidence_ref)
            url = _clean(item.get("url"))
            if _is_public_web_source_url(url):
                title = _clean(item.get("headline") or item.get("title") or item.get("label") or url)
                source = _clean(item.get("source") or "")
                label = f"{title} ({source})" if source else title
                source_links.append({"url": url, "label": label})
        # From articles (investigator.recent_news)
        for item in list(payload.get("articles") or [])[:8]:
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
        for row in list(payload.get("rows") or [])[:8]:
            if not isinstance(row, dict):
                continue
            url = _clean(row.get("url"))
            if _is_public_web_source_url(url):
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
        "evidence_refs": evidence_refs if evidence_refs else None,
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


def _normalize_scope_symbol(value: object) -> str:
    symbol = _clean(value).upper()
    if not symbol or any(ch.isspace() for ch in symbol):
        return ""
    allowed = "".join(ch for ch in symbol if ch.isalnum() or ch in {".", "-"})
    if allowed != symbol or not any(ch.isalpha() for ch in symbol):
        return ""
    return symbol[:12]


def _query_context_json(query: str) -> dict[str, Any]:
    marker = "Context JSON:"
    tail = str(query or "")
    index = tail.find(marker)
    if index >= 0:
        tail = tail[index + len(marker) :]
    start = tail.find("{")
    if start < 0:
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(tail[start:])
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _typed_context_focus_symbols(query: str) -> list[str]:
    context = _query_context_json(query)
    if not context:
        return []
    symbols: list[str] = []

    def _add(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                _add(item)
            return
        symbol = _normalize_scope_symbol(value)
        if symbol and symbol not in symbols:
            symbols.append(symbol)

    for key in ("symbol", "ticker", "target_symbol", "requested_symbol"):
        _add(context.get(key))
    for container_key in (
        "company_baseline",
        "baseline",
        "asset",
        "company",
        "query_spec",
    ):
        nested = context.get(container_key)
        if not isinstance(nested, dict):
            continue
        for key in ("symbol", "ticker", "target_symbol", "requested_symbol"):
            _add(nested.get(key))
    return symbols[:4]


def _arguments_with_task_scope(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    query: str,
    task: str,
) -> dict[str, Any]:
    normalized_task = _clean(task)
    if not normalized_task.startswith("ticker_business_model"):
        return arguments
    symbols = _typed_context_focus_symbols(query)
    if not symbols:
        return arguments
    scoped = dict(arguments or {})
    if tool_name in {"research.retained_context", "research.live_event_evidence"}:
        existing = scoped.get("focus_symbols")
        if not isinstance(existing, list) or not [_normalize_scope_symbol(item) for item in existing if _normalize_scope_symbol(item)]:
            scoped["focus_symbols"] = symbols
    return scoped


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


_PLANNER_SYSTEM_PROMPT = register_narrative_prompt(
    name="Zopedia Agent Planner (tool-calling reasoning)",
    file="services/zopedia_agent.py",
    group="Zopedia",
    prompt=(
        f"You are the Spectral Nature Zopedia agent. {NARRATIVE_STYLE_RULE} "
        "Answer the user's question by calling the available tools when needed. Do not invent data. "
        "Do not repeat a tool with materially identical arguments. "
        "\n\nEvidence contract: "
        "Do not final-answer from language-model memory alone. "
        "For public company or ticker questions, collect company context, fundamentals, recent/current evidence when relevant, "
        "and retained internal history before synthesis. "
        "For macroeconomic questions, use Spectral Nature's local macro datasets first; use live external evidence only for missing or current gaps. "
        "For market-impact questions, especially when macro, bond, or rates moves are connected to equities, ETFs, sectors, or other assets, collect both driver evidence and observed market data. "
        "Prefetched articles are background, not enough to claim current asset impact. Use dataset.daily_movers and/or dataset.price_history for representative instruments before saying equity or ETF data is unavailable. "
        "If a summary or precomputed artifact is empty, use the primitive time-series tools before final-answering. "
        "If analysis.run_python fails with a code, input, or runtime failure category and tool budget remains, repair the code/input once before final-answering. "
        "For questions that ask Zopedia to adapt or correct memory, read the relevant Zopedia page/source first. "
        "Use zopedia.apply_mutation for safe source-backed updates such as metadata patches, page upserts, or page links. "
        "Use zopedia.propose_change for destructive, ambiguous, low-evidence, merge, delete, or rewrite changes. "
        "Treat user-supplied claims as hypotheses until evidence supports them. "
        "If the user's premise conflicts with tool evidence, state the conflict plainly and do not repeat the premise as fact. "
        "\n\nTask discipline: "
        "The task/surface metadata and any Context JSON in the user prompt are part of the contract. "
        "Use supplied typed context as evidence; tools are for reading the wiki/source trail, opening source URLs, filling missing slots, and checking freshness. "
        "For a named company, ticker, person, source, page, or macro subject, keep every search anchored to that subject unless the task explicitly asks for discovery. "
        "Do not use broad candidate-discovery, market-screening, or unrelated-symbol tools for a named-entity question. "
        "If a query includes planned evidence questions, answer those questions directly; do not replace them with a generic market scan. "
        "For ticker_business_model_* tasks, prefer the supplied Context JSON, zopedia.search_pages/read_page/source tracing, research.open_page on URLs already in context, and investigator company/fundamental/news tools. "
        "Use wide recall when it helps: first-degree and second-degree neighbor tickers, peers, parent/platform, customers, suppliers, industry demand, regulation, credit cycle, rates, policy, and market spillovers can all be relevant. "
        "Keep the output organized around the requested ticker/company instead of suppressing adjacent evidence. "
        "When zopedia.search_pages returns a relevant page_id, read the page before treating the wiki as evidence. "
        "When a Zopedia page has source refs and a claim depends on them, use sources_for_page, trace_to_evidence, read_source, or open_page as needed. "
        "Treat live search rows as leads unless they include opened source text. When snippets or SERP previews are too shallow, open the page through research.open_page before making the verdict. "
        "If tool budget or page access prevents source-body verification, answer only to the strength of the headline/snippet evidence and do not mark confidence high. "
        "Evidence labels are not analysis: never answer that evidence exists; say what the evidence means or name the exact missing slot. "
        "\n\nEvidence priority: "
        "1. Check the pre-fetched internal evidence already in this prompt first. "
        "2. Search Zopedia memory with zopedia.search_pages; read relevant pages with zopedia.read_page. "
        "3. Use research.search_evidence and research.retained_context for already-retained Spectral Nature evidence; these are not live web search. "
        "4. For current or stale-memory questions, use research.live_event_evidence before failing closed or final-answering. "
        "5. For specific tickers, use investigator.* tools (technical_signals, forecast, company_context, fundamentals, recent_news). "
        "6. For broad spillover or second-order effects, use research.market_impact_map. "
        "7. For deeper reads on a URL, use research.open_page. "
        "8. For event significance analysis, use dataset.event_significance with an inferred event_date. "
        "9. For EDA, regressions, clustering, classification, feature checks, or other quantitative questions, use analysis.run_python over approved local datasets or user-supplied inline tables. "
        "10. For thesis verification, use hypothesis.verify after gathering evidence. "
        "11. When checking wiki health, use zopedia.list_maintenance_reports. "
        "12. When evidence shows Zopedia memory is wrong, stale, or missing a durable page, use zopedia.apply_mutation only for safe reversible changes; otherwise create a zopedia.propose_change entry. "
        "\n\nOnce you have enough evidence, return action='final' with a structured markdown answer. "
        "Start with a bold verdict sentence, use ### headings for multi-part answers, keep each heading on its own line with a blank line after it, keep paragraphs short with blank lines, **bold** tickers and metrics, and end with a takeaway. "
        "If you use a Markdown table, it must have a header row, separator row, and one row per line; use bullets if a table would be cramped. "
        "When prior conversation is provided, resolve references like 'this', 'that', 'it' from prior turns."
        "\n\nTool argument encoding: use object_list for arrays of objects such as analysis.run_python dataset_refs "
        "or inline_datasets, object for single objects, json for arbitrary JSON values, string_list only for arrays "
        "of plain strings."
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


def _attempted_tool_names(tool_calls: list[dict[str, Any]]) -> set[str]:
    return {_clean(call.get("tool_name")) for call in tool_calls if isinstance(call, dict) and _clean(call.get("tool_name"))}


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


def _is_contextual_followup_query(query: str) -> bool:
    normalized = _clean(query).lower().strip()
    stripped = normalized.strip(" \t\r\n.!?")
    if not stripped or len(stripped) > 180:
        return False
    starters = (
        "what about ",
        "how about ",
        "what of ",
        "and ",
        "also ",
        "but ",
        "then ",
        "so ",
        "does that ",
        "is that ",
        "can you expand",
        "go deeper",
        "dig deeper",
    )
    if stripped.startswith(starters):
        return True
    reference_terms = {"this", "that", "it", "they", "them", "those", "there"}
    tokens = {token for token in stripped.replace("?", " ").split() if token}
    return bool(tokens & reference_terms) and len(tokens) <= 14


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
    is_affirmative = _is_affirmative_followup(normalized_query)
    is_contextual = _is_contextual_followup_query(normalized_query)
    if not is_affirmative and not is_contextual:
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

    if is_affirmative:
        instruction = (
            "The user replied affirmatively, so carry out the natural next step implied by the prior assistant answer."
        )
    else:
        instruction = (
            "The user asked a contextual follow-up. Resolve the follow-up against the prior question and answer, "
            "then answer that combined question with fresh evidence where needed."
        )

    resolved_query = (
        "Continue the previous Zopedia thread. "
        f"{instruction} "
        "Use the prior answer and prior question as context; verify or expand with evidence instead of "
        "treating the reply as a standalone query.\n\n"
        f"Previous user question:\n{previous_user}\n\n"
        f"Previous assistant answer:\n{answer_excerpt}\n\n"
        f"Current user reply:\n{normalized_query}"
    )
    return resolved_query, True


def _tool_available(tool_catalog: list[dict[str, Any]], tool_name: str) -> bool:
    return any(_clean(tool.get("name")) == tool_name for tool in tool_catalog)


def _tool_catalog_for_write_policy(tool_catalog: list[dict[str, Any]], write_policy: str) -> list[dict[str, Any]]:
    normalized = _normalize_zopedia_write_policy(write_policy)
    blocked = {"zopedia.rollback_mutation"}
    if normalized == "none":
        blocked.update({"zopedia.apply_mutation", "zopedia.propose_change"})
    elif normalized == "propose":
        blocked.add("zopedia.apply_mutation")
    return [tool for tool in tool_catalog if _clean(tool.get("name")) not in blocked]


def _cap_confidence(value: str, maximum: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    normalized_value = _clean(value).lower() or "low"
    normalized_max = _clean(maximum).lower() or "low"
    if normalized_value not in order:
        normalized_value = "low"
    if normalized_max not in order:
        normalized_max = "low"
    if order[normalized_value] <= order[normalized_max]:
        return normalized_value
    for label, rank in order.items():
        if rank == order[normalized_max]:
            return label
    return normalized_max


def _successful_evidence_tool_count(tool_calls: list[dict[str, Any]]) -> int:
    excluded_exact = {
        "scratchpad.write",
        "scratchpad.read",
        "system.capabilities",
        "zopedia.apply_mutation",
        "zopedia.propose_change",
        "zopedia.list_proposals",
        "zopedia.list_mutations",
        "zopedia.list_maintenance_reports",
        "zopedia.rollback_mutation",
        "analysis.read_raw_output",
        "trajectory.monitor",
    }
    count = 0
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        tool_name = _clean(call.get("tool_name"))
        status = _clean(call.get("status")).lower()
        if status not in {"completed", "success", "ok"}:
            continue
        if tool_name in excluded_exact:
            continue
        count += 1
    return count


_SOURCE_BODY_TOOLS = {
    "research.open_page",
    "zopedia.read_source",
}


def _live_search_without_source_body(tool_calls: list[dict[str, Any]]) -> bool:
    completed = _completed_tool_names(tool_calls)
    if "research.live_event_evidence" not in completed:
        return False
    return not any(tool_name in completed for tool_name in _SOURCE_BODY_TOOLS)


_OBSERVED_MARKET_DATA_TOOLS = {
    "dataset.daily_movers",
    "dataset.price_history",
    "dataset.technical_signal_history",
    "analysis.run_python",
}


def _completed_tool_names(tool_calls: list[dict[str, Any]]) -> set[str]:
    return {
        _clean(call.get("tool_name"))
        for call in tool_calls
        if isinstance(call, dict) and _clean(call.get("status")).lower() in {"completed", "success", "ok"}
    }


def _has_observed_market_data(tool_calls: list[dict[str, Any]]) -> bool:
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        if _clean(call.get("status")).lower() not in {"completed", "success", "ok"}:
            continue
        if _clean(call.get("tool_name")) not in _OBSERVED_MARKET_DATA_TOOLS:
            continue
        if not _tool_call_is_low_signal(call):
            return True
    return False


def _mentions_market_impact_claim(query: str, answer: str) -> bool:
    text = f"{query}\n{answer}".lower()
    market_terms = (
        "market",
        "equity",
        "equities",
        "stock",
        "stocks",
        "etf",
        "sector",
        "growth",
        "duration",
        "bond",
        "yield",
        "rate",
        "rates",
    )
    impact_terms = (
        "impact",
        "affect",
        "pressure",
        "weigh",
        "benefit",
        "move",
        "moved",
        "fall",
        "rise",
        "drop",
        "headwind",
        "tailwind",
    )
    return any(term in text for term in market_terms) and any(term in text for term in impact_terms)


def _market_impact_focus_symbols(tool_calls: list[dict[str, Any]]) -> list[str]:
    symbols: list[str] = []
    for call in reversed(tool_calls):
        if _clean(call.get("tool_name")) != "research.market_impact_map":
            continue
        summary = dict(call.get("result_summary") or {})
        preview = dict(summary.get("preview") or {})
        for row in list(preview.get("sample") or []):
            if not isinstance(row, dict):
                continue
            symbol = _clean(row.get("symbol")).upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        if symbols:
            continue
        text = _clean(summary.get("llm_context_text") or summary.get("preview_text"))
        for token in text.replace(",", " ").replace(".", " ").replace(":", " ").split():
            symbol = "".join(ch for ch in token.strip().upper() if ch.isalnum())
            if 1 <= len(symbol) <= 7 and symbol.isalnum() and symbol not in symbols:
                if symbol not in {"THEME", "EXPECTED", "LIKELY", "IMPACTED", "SYMBOLS", "CHECK", "NEXT"}:
                    symbols.append(symbol)
    return symbols[:8]


def _market_impact_recovery_tool(
    *,
    query: str,
    answer: str,
    tool_calls: list[dict[str, Any]],
    tool_catalog: list[dict[str, Any]],
) -> tuple[str, dict[str, Any], str] | None:
    if _has_observed_market_data(tool_calls):
        return None
    if not _mentions_market_impact_claim(query, answer):
        return None
    attempted = _attempted_tool_names(tool_calls)
    if "research.market_impact_map" not in attempted and _tool_available(tool_catalog, "research.market_impact_map"):
        return (
            "research.market_impact_map",
            {"query": query, "max_symbols": 8},
            "Market-impact answer needs a representative instrument basket before final synthesis.",
        )
    symbols = _market_impact_focus_symbols(tool_calls)
    if "dataset.daily_movers" not in attempted and _tool_available(tool_catalog, "dataset.daily_movers"):
        args: dict[str, Any] = {"force_refresh": False}
        if symbols:
            args["symbols"] = symbols
        return (
            "dataset.daily_movers",
            args,
            "Market-impact answer needs observed daily market moves before final synthesis.",
        )
    return None


def _bootstrap_tool_plan(
    *,
    query: str,
    tool_catalog: list[dict[str, Any]],
    force_refresh: bool,
    max_calls: int,
) -> list[tuple[str, dict[str, Any]]]:
    """Legacy hook kept for config compatibility; LLM planner owns tool choice."""
    del query, tool_catalog, force_refresh, max_calls
    return []


def _execute_seeded_tool_call(
    *,
    service: QueryService,
    run_id: str,
    tool_calls: list[dict[str, Any]],
    progress_callback: ProgressCallback | None,
    tool_name: str,
    arguments: dict[str, Any],
    progress: float,
    tool_call_timeout_seconds: int | None = None,
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
            timeout_seconds=tool_call_timeout_seconds,
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
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    if timeout_seconds is None:
        timeout_seconds = int(get_config_param(_P_TOOL_CALL_TIMEOUT_SECONDS))
    timeout_seconds = max(int(timeout_seconds), 1)
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

    tool_thread = threading.Thread(target=_run_tool, name=f"zopedia-tool-{tool_name[:32]}", daemon=True)
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

    step_thread = threading.Thread(target=_runner, name=f"zopedia-hidden-{label[:32]}", daemon=True)
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


def _analysis_failure_needs_repair(tool_calls: list[dict[str, Any]]) -> bool:
    analysis_calls = [
        call
        for call in tool_calls
        if isinstance(call, dict) and _clean(call.get("tool_name")) == "analysis.run_python"
    ]
    if len(analysis_calls) != 1:
        return False
    summary = dict(analysis_calls[0].get("result_summary") or {})
    text = _json_dumps(summary, limit=2400).lower()
    failure_markers = (
        "failure category: analysis_code_error",
        "failure category: analysis_input_missing",
        "failure category: analysis_runtime_error",
        "failure_category",
        "analysis_code_error",
        "analysis_input_missing",
        "analysis_runtime_error",
    )
    return any(marker in text for marker in failure_markers)


def _should_skip_planner_after_bootstrap(query: str, tool_calls: list[dict[str, Any]], seeded_names: set[str]) -> bool:
    del query, tool_calls, seeded_names
    return False


def _first_zopedia_page_id_from_search(tool_calls: list[dict[str, Any]]) -> str:
    for call in reversed(tool_calls):
        if _clean(call.get("tool_name")) != "zopedia.search_pages":
            continue
        if _clean(call.get("status")) != "completed" or _tool_call_is_low_signal(call):
            continue
        summary = dict(call.get("result_summary") or {})
        for ref in list(summary.get("evidence_refs") or []):
            if not isinstance(ref, dict):
                continue
            page_id = _clean(ref.get("page_id") or ref.get("ref"))
            if page_id:
                return page_id
    return ""


def _zopedia_page_already_read(tool_calls: list[dict[str, Any]], page_id: str) -> bool:
    normalized = _clean(page_id)
    if not normalized:
        return False
    for call in tool_calls:
        if _clean(call.get("tool_name")) != "zopedia.read_page":
            continue
        if _clean(call.get("status")) != "completed":
            continue
        args = dict(call.get("arguments") or {})
        if _clean(args.get("page_id")) == normalized:
            return True
    return False


def _direct_structured_payload_answer(query: str, decision: dict[str, Any]) -> str:
    if "Return only a valid JSON object matching schema" not in str(query or ""):
        return ""
    step_keys = set(_STEP_SCHEMA.get("properties") or {})
    payload = {
        key: value
        for key, value in decision.items()
        if key not in step_keys and not str(key).startswith("__")
    }
    if not payload:
        return ""
    if any(key in payload for key in ("queries", "slot_facts", "verdict_markdown", "business_story_markdown")):
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    return ""


def _budgeted_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    free_tools = {
        "trajectory.monitor",
        "research.prefetched_context",
    }
    return [
        call
        for call in list(tool_calls or [])
        if _clean(call.get("tool_name")) not in free_tools
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
        f"Task: {_clean(task) or 'agent_answer'}\n"
        f"Surface: {_clean(surface) or 'zopedia.chat'}\n\n"
        f"User question:\n{query}\n\n"
        f"Tool budget: {max_tool_calls - _budgeted_tool_count(tool_calls)} remaining out of {max_tool_calls}.\n\n"
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
    task: str = "",
    surface: str = "",
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
        f"Task: {_clean(task) or 'agent_answer'}\n"
        f"Surface: {_clean(surface) or 'zopedia.chat'}\n\n"
        f"User question:\n{query}\n\n"
        "Tool evidence:\n"
        f"{_tool_history_prompt(tool_calls)}\n\n"
        "Write the best grounded markdown answer you can from this evidence only. "
        "When the user question contains typed Context JSON, source_acquisition_rows, supplied source text, or inline datasets, treat that supplied context as evidence too, especially when the tool budget is zero. "
        "Make it readable: start with a bold verdict, use ### headings for distinct themes, keep each heading on its own line with a blank line after it, keep paragraphs short, and put blank lines between paragraphs. "
        "If you use a Markdown table, it must have a header row, separator row, and one row per line; use bullets if a table would be cramped. "
        "Treat the user's premise as unverified unless the evidence supports it; if evidence contradicts the premise, say so plainly. "
        "If you use Zopedia memory, cite the relevant Zopedia page title or page_id in the answer. "
        "If the evidence includes fresh web research, lightly mention one or two supporting sources or links. "
        "Do not claim that live search, recent search, or a source-reading tool failed or returned no results unless the tool history shows that exact attempt. "
        "For current questions, if the tool history contains only retained/internal evidence and no completed live/current source tool, say the run has not collected enough current evidence instead of presenting a market update. "
        "Do not turn the answer into a citation list. "
        "If the evidence is incomplete, say what is missing."
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
) -> str:
    history_block = _compact_conversation_history(conversation_history, max_chars=int(get_config_param(_P_CONVERSATION_HISTORY_LIMIT)))
    evidence_block = ""
    if prefetched_context:
        evidence_block = f"{prefetched_context}\n\n"
    limitation_block = "\n".join(f"- {item}" for item in limitations if _clean(item)) or "- None"
    return (
        f"{history_block}"
        f"{evidence_block}"
        f"Task: {_clean(task) or 'agent_answer'}\n"
        f"Surface: {_clean(surface) or 'zopedia.chat'}\n\n"
        f"User question:\n{query}\n\n"
        "Tool evidence:\n"
        f"{_tool_history_prompt(tool_calls)}\n\n"
        "Draft answer:\n"
        f"{draft_answer}\n\n"
        f"Draft confidence: {draft_confidence or 'low'}\n\n"
        "Known limitations:\n"
        f"{limitation_block}\n\n"
        "Review the draft answer for evidence sufficiency. "
        "When the original user question contains typed Context JSON, source_acquisition_rows, supplied source text, or inline datasets, treat that supplied context as part of the evidence even if no tool calls were made. "
        "Use verdict='accept' only when the draft is supported by the evidence and the confidence is appropriate. "
        "Use verdict='revise' when the answer should be corrected or made more precise. "
        "Use verdict='insufficient' when the evidence does not support a substantive answer. "
        "Mark the draft insufficient if it claims live search, recent search, or source-reading failed or found nothing but the tool history does not show that attempt. "
        "For current questions, retained/internal evidence alone should not become a confident market update unless the answer clearly says current evidence was not collected. "
        "Return the answer_markdown that should be shown to the user. "
        "Repair malformed Markdown tables before returning the final answer."
    )


def _synthesis_llm_client(primary_llm: LLMClient) -> LLMClient:
    """Use the shared LLM provider for synthesis.

    Provider/model selection belongs in the global LLM layer. Keeping a
    Zopedia-only synthesis override made the runtime harder to reason about and
    let one surface drift from AQL/API/job behavior.
    """
    return primary_llm


def _run_answer_judge(
    *,
    llm: LLMClient,
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

    def _runner() -> None:
        try:
            result_box[0] = llm.generate_json(
                system_prompt=get_prompt(_AGENT_JUDGE_SYSTEM_PROMPT),
                user_prompt=_judge_user_prompt(
                    query=query,
                    task=task,
                    surface=surface,
                    tool_calls=tool_calls,
                    draft_answer=draft_answer,
                    draft_confidence=draft_confidence,
                    limitations=limitations,
                    conversation_history=conversation_history,
                    prefetched_context=prefetched_context,
                ),
                schema_name="zopedia_agent_judge",
                schema=_JUDGE_SCHEMA,
            )
        except BaseException as exc:
            error_box[0] = exc

    judge_thread = threading.Thread(target=_runner, name="zopedia-answer-judge", daemon=True)
    judge_thread.start()
    heartbeat_interval = 5.0
    elapsed = 0.0
    while judge_thread.is_alive():
        judge_thread.join(timeout=heartbeat_interval)
        if not judge_thread.is_alive():
            break
        elapsed += heartbeat_interval
        if elapsed >= timeout_seconds:
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
            message=f"Still reviewing evidence sufficiency... ({int(elapsed)}s)",
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
        "verdict": _clean(payload.get("verdict")).lower() or "accept",
        "critique_summary": _clean(payload.get("critique_summary")),
        "answer_markdown": _clean_model_output(payload.get("answer_markdown")),
        "confidence": _clean(payload.get("confidence")).lower() or draft_confidence or "low",
        "limitations": [_clean(item) for item in list(payload.get("limitations") or []) if _clean(item)],
        "unsupported_claims": [_clean(item) for item in list(payload.get("unsupported_claims") or []) if _clean(item)],
        "evidence_gaps": [_clean(item) for item in list(payload.get("evidence_gaps") or []) if _clean(item)],
    }
    _emit_progress(
        progress_callback,
        stage="answer_judge_complete",
        message=review["critique_summary"] or "Answer review complete.",
        progress=0.99,
        verdict=review["verdict"],
        confidence=review["confidence"],
        unsupported_claim_count=len(review["unsupported_claims"]),
        evidence_gap_count=len(review["evidence_gaps"]),
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
        f"Restart count: {int(restart_count)}\n"
        f"Tool budget remaining: {max(int(max_tool_calls) - _budgeted_tool_count(tool_calls), 0)} of {int(max_tool_calls)}\n\n"
        f"User question:\n{query}\n\n"
        "Available tool names:\n"
        f"{', '.join(tool_names)}\n\n"
        "Tool call history:\n"
        f"{_tool_history_prompt(tool_calls)}\n\n"
        "Decide whether this thread should double down, restart with one corrective instruction, or kill/fail closed."
    )


def _run_trajectory_monitor(
    *,
    llm: LLMClient,
    query: str,
    task: str,
    surface: str,
    tool_catalog: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    max_tool_calls: int,
    restart_count: int,
    timeout_seconds: int,
    progress_callback: ProgressCallback | None,
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
            result_box[0] = llm.generate_json(
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
            )
        except BaseException as exc:
            error_box[0] = exc

    monitor_thread = threading.Thread(target=_runner, name="zopedia-trajectory-monitor", daemon=True)
    monitor_thread.start()
    heartbeat_interval = 5.0
    elapsed = 0.0
    while monitor_thread.is_alive():
        monitor_thread.join(timeout=heartbeat_interval)
        if not monitor_thread.is_alive():
            break
        elapsed += heartbeat_interval
        if elapsed >= timeout_seconds:
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
        max_chars=int(get_config_param(_P_CONVERSATION_HISTORY_LIMIT)),
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
        f"{_tool_history_prompt(tool_calls)}\n\n"
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
    try:
        payload = _run_hidden_step_with_timeout(
            label="post-answer memory reflection",
            timeout_seconds=max(int(timeout_seconds), 1),
            progress_callback=progress_callback,
            progress=0.992,
            func=lambda: llm.generate_json(
                system_prompt=get_prompt(_AGENT_MEMORY_SYSTEM_PROMPT),
                user_prompt=memory_prompt,
                schema_name="zopedia_memory_reflection",
                schema=_MEMORY_SCHEMA,
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
                timeout_seconds=max(int(timeout_seconds), 1),
                progress_callback=progress_callback,
                progress=0.995,
                func=lambda: llm.generate_json(
                    system_prompt=get_prompt(_AGENT_MEMORY_SYSTEM_PROMPT),
                    user_prompt=_memory_reflection_repair_user_prompt(
                        base_prompt=memory_prompt,
                        invalid_decision=decision,
                        contract_issues=repair_issues,
                    ),
                    schema_name="zopedia_memory_reflection_repair",
                    schema=_MEMORY_SCHEMA,
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
            timeout_seconds=max(min(int(timeout_seconds), int(get_config_param(_P_TOOL_CALL_TIMEOUT_SECONDS))), 1),
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
) -> dict[str, Any]:
    normalized_query = _clean(query)
    agent_query, followup_resolved = resolve_conversation_followup_query(
        normalized_query,
        conversation_history,
    )
    # Use config-param override when caller passed the module-level default
    if max_tool_calls == DEFAULT_MAX_TOOL_CALLS:
        max_tool_calls = int(get_config_param(_P_MAX_TOOL_CALLS))
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
    run_id = f"agrun_{uuid.uuid4().hex[:10]}"
    if not agent_query:
        return _with_aql_evidence_pack({
            "run_id": run_id,
            "status": "failed",
            "mode": "sync",
            "model": "",
            "tool_calls": [],
            "answer_markdown": "",
            "confidence": "low",
            "limitations": ["Empty query."],
            "error": "Empty query.",
            "query": agent_query,
            "original_query": normalized_query,
            "followup_resolved": followup_resolved,
        })

    resolved_service = service or QueryService.from_environment()
    resolved_llm = llm_client or load_aql_zopedia_llm_client(
        surface="zopedia.agent",
    )
    if resolved_llm is None:
        return _with_aql_evidence_pack({
            "run_id": run_id,
            "status": "unavailable",
            "mode": "sync",
            "model": "",
            "tool_calls": [],
            "answer_markdown": "The LLM runtime is not configured, so the Zopedia agent cannot run tool-based analysis right now.",
            "confidence": "low",
            "limitations": ["LLM runtime is unavailable."],
            "error": "LLM runtime is unavailable.",
            "query": agent_query,
            "original_query": normalized_query,
            "followup_resolved": followup_resolved,
        })
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
    tool_catalog = _tool_catalog_for_write_policy(build_tool_catalog(resolved_service), resolved_write_policy)
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
    if int(max_tool_calls) > 0:
        try:
            from .saa import search_retained_evidence_chunks
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
                    from .zopedia_research import market_impact_map
                    impact = _run_hidden_step_with_timeout(
                        label="prefetch keyword extraction",
                        timeout_seconds=prefetch_timeout_seconds,
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
        max(int(max_tool_calls) - _budgeted_tool_count(tool_calls), 0),
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
                tool_call_timeout_seconds=tool_call_timeout_seconds,
            )
        zopedia_page_id = _first_zopedia_page_id_from_search(tool_calls)
        if (
            zopedia_page_id
            and _budgeted_tool_count(tool_calls) < int(max_tool_calls)
            and _tool_available(tool_catalog, "zopedia.read_page")
        ):
            seeded_tool_names.add("zopedia.read_page")
            _execute_seeded_tool_call(
                service=resolved_service,
                run_id=run_id,
                tool_calls=tool_calls,
                progress_callback=progress_callback,
                tool_name="zopedia.read_page",
                arguments={"page_id": zopedia_page_id},
                progress=min(0.13 + (len(tool_calls) * 0.04), 0.34),
                tool_call_timeout_seconds=tool_call_timeout_seconds,
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
    direct_structured_payload_used = False
    limitations: list[str] = []
    quality_review: dict[str, Any] = {"status": "not_run"}
    memory_update: dict[str, Any] = {"status": "not_run"}
    consecutive_failed_tools = 0
    trajectory_reviews: list[dict[str, Any]] = []
    trajectory_restart_count = 0
    trajectory_killed = False
    trajectory_max_restarts = max(int(get_config_param(_P_TRAJECTORY_MONITOR_MAX_RESTARTS)), 0)

    def _check_trajectory(pending_call: dict[str, Any] | None = None) -> str:
        nonlocal trajectory_restart_count, trajectory_killed
        monitor_tool_calls = list(tool_calls)
        if pending_call is not None:
            monitor_tool_calls.append(pending_call)
        monitor_result = _run_trajectory_monitor(
            llm=_synthesis_llm_client(resolved_llm),
            query=agent_query,
            task=task,
            surface=surface,
            tool_catalog=tool_catalog,
            tool_calls=monitor_tool_calls,
            max_tool_calls=int(max_tool_calls),
            restart_count=trajectory_restart_count,
            timeout_seconds=min(
                llm_step_timeout_seconds,
                max(int(get_config_param(_P_TRAJECTORY_MONITOR_TIMEOUT_SECONDS)), 1),
            ),
            progress_callback=progress_callback,
        )
        if monitor_result.get("status") == "completed":
            trajectory_reviews.append(dict(monitor_result))
        decision = _clean(monitor_result.get("decision")).lower() or "double_down"
        if (
            decision == "kill"
            and _clean(task).startswith("ticker_business_model")
            and _budgeted_tool_count(monitor_tool_calls) < int(max_tool_calls)
            and not [
                _clean(item)
                for item in list(monitor_result.get("off_contract_signals") or [])
                if _clean(item)
            ]
        ):
            monitor_result = dict(monitor_result)
            monitor_result["decision"] = "double_down"
            monitor_result["reason"] = (
                _clean(monitor_result.get("reason"))
                + " Continuing because empty internal evidence is a coverage gap, not off-contract drift, and the job still has research budget."
            ).strip()
            decision = "double_down"
            _emit_progress(
                progress_callback,
                stage="trajectory_monitor_continue",
                message=_clean(monitor_result.get("reason")) or "Continuing on-contract research.",
                progress=0.94,
                tool_call_count=len(tool_calls),
            )
        if decision == "restart":
            if trajectory_restart_count >= trajectory_max_restarts:
                decision = "kill"
                monitor_result = dict(monitor_result)
                monitor_result["decision"] = "kill"
                monitor_result["reason"] = (
                    _clean(monitor_result.get("reason"))
                    or "Trajectory monitor requested another restart after the restart budget was exhausted."
                )
            else:
                trajectory_restart_count += 1
                _append_trajectory_monitor_call(
                    tool_calls,
                    monitor_result=monitor_result,
                    restart_count=trajectory_restart_count,
                )
                _emit_progress(
                    progress_callback,
                    stage="trajectory_monitor_restart",
                    message=_clean(monitor_result.get("reason")) or "Restarting the planner with corrected task focus.",
                    progress=0.94,
                    restart_count=trajectory_restart_count,
                )
                return "restart"
        if (
            decision == "kill"
            and _clean(task).startswith("ticker_business_model")
            and _budgeted_tool_count(monitor_tool_calls) < int(max_tool_calls)
            and not [
                _clean(item)
                for item in list(monitor_result.get("off_contract_signals") or [])
                if _clean(item)
            ]
        ):
            monitor_result = dict(monitor_result)
            monitor_result["decision"] = "double_down"
            monitor_result["reason"] = (
                _clean(monitor_result.get("reason"))
                + " Continuing because empty internal evidence is a coverage gap, not off-contract drift, and the job still has research budget."
            ).strip()
            decision = "double_down"
            _emit_progress(
                progress_callback,
                stage="trajectory_monitor_continue",
                message=_clean(monitor_result.get("reason")) or "Continuing on-contract research.",
                progress=0.94,
                tool_call_count=len(tool_calls),
            )
        if decision == "kill":
            trajectory_killed = True
            _append_trajectory_monitor_call(
                tool_calls,
                monitor_result=monitor_result,
                restart_count=trajectory_restart_count,
            )
            reason = _clean(monitor_result.get("reason")) or "Trajectory monitor killed an off-contract research thread."
            if reason not in limitations:
                limitations.append(reason)
            _emit_progress(
                progress_callback,
                stage="trajectory_monitor_kill",
                message=reason,
                progress=0.96,
                tool_call_count=len(tool_calls),
            )
            return "kill"
        return "double_down"

    try:
        total_steps = max(int(max_tool_calls), 1)
        planner_steps = 0 if skip_planner_after_bootstrap or int(max_tool_calls) <= 0 else total_steps
        if skip_planner_after_bootstrap:
            _emit_progress(
                progress_callback,
                stage="planner_skipped",
                message="Initial evidence is sufficient; moving straight to synthesis.",
                progress=0.9,
                tool_call_count=len(tool_calls),
            )
        elif int(max_tool_calls) <= 0:
            _emit_progress(
                progress_callback,
                stage="planner_skipped",
                message="Tool budget is zero; synthesizing from supplied context only.",
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
                            task=task,
                            surface=surface,
                            tool_catalog=tool_catalog,
                            tool_calls=tool_calls,
                            max_tool_calls=max_tool_calls,
                            conversation_history=conversation_history,
                            prefetched_context=prefetched_context,
                        ),
                        schema_name="zopedia_agent_step",
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
            if action not in {"tool_call", "final"}:
                direct_answer = _direct_structured_payload_answer(agent_query, decision)
                if direct_answer:
                    final_answer = direct_answer
                    final_confidence = "medium"
                    direct_structured_payload_used = True
                    limitations.append("Planner returned the requested structured payload directly.")
                    _emit_progress(
                        progress_callback,
                        stage="planner_direct_structured_payload",
                        message="Planner returned the requested structured payload directly.",
                        progress=min(step_progress + 0.08, 0.9),
                        iteration=step_index + 1,
                    )
                    break
            if action == "final":
                candidate_answer = _clean_model_output(decision.get("answer_markdown"))
                zopedia_page_id = _first_zopedia_page_id_from_search(tool_calls)
                if (
                    zopedia_page_id
            and _budgeted_tool_count(tool_calls) < int(max_tool_calls)
                    and _tool_available(tool_catalog, "zopedia.read_page")
                    and not _zopedia_page_already_read(tool_calls, zopedia_page_id)
                ):
                    _emit_progress(
                        progress_callback,
                        stage="zopedia_page_read_required",
                        message="Reading the relevant Zopedia page before final synthesis.",
                        progress=min(step_progress + 0.03, 0.9),
                        tool_name="zopedia.read_page",
                        tool_arguments={"page_id": zopedia_page_id},
                    )
                    _execute_seeded_tool_call(
                        service=resolved_service,
                        run_id=run_id,
                        tool_calls=tool_calls,
                        progress_callback=progress_callback,
                        tool_name="zopedia.read_page",
                        arguments={"page_id": zopedia_page_id},
                        progress=min(step_progress + 0.05, 0.9),
                        tool_call_timeout_seconds=tool_call_timeout_seconds,
                    )
                    for call in tool_calls:
                        seen_calls.add(f"{_clean(call.get('tool_name'))}::{_json_dumps(call.get('arguments') or {}, limit=1200)}")
                    continue
                if _analysis_failure_needs_repair(tool_calls) and _budgeted_tool_count(tool_calls) < int(max_tool_calls):
                    _emit_progress(
                        progress_callback,
                        stage="analysis_repair_required",
                        message="Analysis failed with a repairable code/input/runtime category; giving the planner one repair pass.",
                        progress=min(step_progress + 0.03, 0.9),
                        tool_call_count=len(tool_calls),
                    )
                    continue
                recovery = _market_impact_recovery_tool(
                    query=agent_query,
                    answer=candidate_answer,
                    tool_calls=tool_calls,
                    tool_catalog=tool_catalog,
                )
                if recovery and _budgeted_tool_count(tool_calls) < int(max_tool_calls):
                    recovery_tool_name, recovery_arguments, recovery_reason = recovery
                    _emit_progress(
                        progress_callback,
                        stage="evidence_gap_recovery",
                        message=recovery_reason,
                        progress=min(step_progress + 0.04, 0.9),
                        tool_name=recovery_tool_name,
                        tool_arguments=recovery_arguments,
                    )
                    _execute_seeded_tool_call(
                        service=resolved_service,
                        run_id=run_id,
                        tool_calls=tool_calls,
                        progress_callback=progress_callback,
                        tool_name=recovery_tool_name,
                        arguments=recovery_arguments,
                        progress=min(step_progress + 0.06, 0.9),
                        tool_call_timeout_seconds=tool_call_timeout_seconds,
                    )
                    for call in tool_calls:
                        seen_calls.add(f"{_clean(call.get('tool_name'))}::{_json_dumps(call.get('arguments') or {}, limit=1200)}")
                    continue
                _emit_progress(
                    progress_callback,
                    stage="planner_final",
                    message="Final answer is ready to render.",
                    progress=min(step_progress + 0.08, 0.9),
                    iteration=step_index + 1,
                    reasoning=reasoning,
                )
                final_answer = candidate_answer
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
            arguments = _arguments_with_task_scope(
                tool_name=tool_name,
                arguments=arguments,
                query=agent_query,
                task=task,
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
            if not tool_entry:
                if trajectory_restart_count < trajectory_max_restarts:
                    trajectory_restart_count += 1
                    available_names = [
                        _clean(tool.get("name"))
                        for tool in tool_catalog
                        if _clean(tool.get("name"))
                    ]
                    _append_trajectory_monitor_call(
                        tool_calls,
                        monitor_result={
                            "decision": "restart",
                            "reason": (
                                "Planner selected a missing or unsupported tool name; retrying without spending "
                                "research budget."
                            ),
                            "corrective_instruction": (
                                "Choose exactly one tool from Available tool names. Do not leave tool_name blank. "
                                f"Available examples: {', '.join(available_names[:12])}."
                            ),
                            "off_contract_signals": [f"unsupported tool: {tool_name or '(blank)'}"],
                            "evidence_gaps": [],
                        },
                        restart_count=trajectory_restart_count,
                    )
                    _emit_progress(
                        progress_callback,
                        stage="planner_tool_repair",
                        message="Planner selected an unavailable tool; restarting with the available tool list.",
                        progress=min(step_progress + 0.05, 0.9),
                        iteration=step_index + 1,
                        restart_count=trajectory_restart_count,
                    )
                    continue
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

            pending_call = {
                "tool_call_id": call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "status": "planned",
                "error": None,
                "result_summary": {
                    "preview_text": "Planner proposed this tool call; trajectory monitor is reviewing it before execution.",
                    "result_type": "planned_tool_call",
                    "provenance": None,
                    "preview": {"kind": "planned_tool_call"},
                },
            }
            trajectory_decision = _check_trajectory(pending_call)
            if trajectory_decision == "kill":
                break
            if trajectory_decision == "restart":
                for call in tool_calls:
                    seen_calls.add(f"{_clean(call.get('tool_name'))}::{_json_dumps(call.get('arguments') or {}, limit=1200)}")
                continue

            seen_calls.add(dedupe_signature)
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
                        timeout_seconds=tool_call_timeout_seconds,
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
                trajectory_decision = _check_trajectory()
                if trajectory_decision == "kill":
                    break
                if trajectory_decision == "restart":
                    for call in tool_calls:
                        seen_calls.add(f"{_clean(call.get('tool_name'))}::{_json_dumps(call.get('arguments') or {}, limit=1200)}")
                    continue
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
                trajectory_decision = _check_trajectory()
                if trajectory_decision == "kill":
                    break
                if trajectory_decision == "restart":
                    for call in tool_calls:
                        seen_calls.add(f"{_clean(call.get('tool_name'))}::{_json_dumps(call.get('arguments') or {}, limit=1200)}")
                    continue

        if not final_answer and not trajectory_killed:
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
                        user_prompt=_final_user_prompt(
                            query=agent_query,
                            task=task,
                            surface=surface,
                            tool_calls=tool_calls,
                            conversation_history=conversation_history,
                            prefetched_context=prefetched_context,
                        ),
                        schema_name="zopedia_agent_final",
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
            final_answer = _clean_model_output(final_payload.get("answer_markdown"))
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
            "answer_markdown": "",
            "confidence": "low",
            "limitations": limitations + [limitation],
            "trajectory_reviews": trajectory_reviews,
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
        return _with_aql_evidence_pack(error_result)

    if final_answer and not direct_structured_payload_used:
        quality_review = _run_answer_judge(
            llm=_synthesis_llm_client(resolved_llm),
            query=agent_query,
            task=task,
            surface=surface,
            tool_calls=tool_calls,
            draft_answer=final_answer,
            draft_confidence=final_confidence,
            limitations=limitations,
            conversation_history=conversation_history,
            prefetched_context=prefetched_context,
            timeout_seconds=llm_step_timeout_seconds,
            progress_callback=progress_callback,
        )
        if quality_review.get("status") == "completed":
            verdict = _clean(quality_review.get("verdict")).lower()
            judge_answer = _clean_model_output(quality_review.get("answer_markdown"))
            revised_answer_applied = False
            if verdict in {"revise", "insufficient"} and judge_answer:
                final_answer = judge_answer
                revised_answer_applied = True
            final_confidence = _clean(quality_review.get("confidence")).lower() or final_confidence
            for item in [
                *list(quality_review.get("limitations") or []),
                *list(quality_review.get("unsupported_claims") or []),
                *list(quality_review.get("evidence_gaps") or []),
            ]:
                clean_item = _clean(item)
                if clean_item and clean_item not in limitations:
                    limitations.append(clean_item)
            quality_review = {
                "status": "completed",
                "verdict": verdict,
                "critique_summary": _clean(quality_review.get("critique_summary")),
                "confidence": final_confidence,
                "limitations": [_clean(item) for item in list(quality_review.get("limitations") or []) if _clean(item)],
                "unsupported_claims": [_clean(item) for item in list(quality_review.get("unsupported_claims") or []) if _clean(item)],
                "evidence_gaps": [_clean(item) for item in list(quality_review.get("evidence_gaps") or []) if _clean(item)],
                "revised_answer_applied": revised_answer_applied,
            }

    answer_markdown = _clean_model_output(final_answer)
    evidence_tool_count = _successful_evidence_tool_count(tool_calls)
    if evidence_tool_count == 0:
        final_confidence = "low"
        gap = "Confidence capped because no evidence tool returned usable data."
        if gap not in limitations:
            limitations.append(gap)
    elif evidence_tool_count == 1:
        capped_confidence = _cap_confidence(final_confidence, "medium")
        if capped_confidence != final_confidence:
            final_confidence = capped_confidence
            gap = "Confidence capped because only one evidence source returned usable data."
            if gap not in limitations:
                limitations.append(gap)
    if _live_search_without_source_body(tool_calls):
        capped_confidence = _cap_confidence(final_confidence, "medium")
        if capped_confidence != final_confidence:
            final_confidence = capped_confidence
            gap = "Confidence capped because live web evidence was not opened into source text."
            if gap not in limitations:
                limitations.append(gap)
    if resolved_write_policy != "none" and answer_markdown:
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
            timeout_seconds=llm_step_timeout_seconds,
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
            else "Trajectory monitor killed an off-contract thread."
            if trajectory_killed
            else "Agent run ended without an answer."
        ),
        progress=1.0,
        tool_call_count=len(tool_calls),
        status=status,
    )
    duration = time.monotonic() - _run_start_time
    error_text = None if status == "completed" else (
        "Trajectory monitor killed an off-contract thread."
        if trajectory_killed
        else "Agent did not produce an answer."
    )
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
        "quality_review": quality_review,
        "memory_update": memory_update,
        "trajectory_reviews": trajectory_reviews,
        "error": error_text,
        "query": agent_query,
        "original_query": normalized_query,
        "followup_resolved": followup_resolved,
        "write_policy": resolved_write_policy,
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
    return _with_aql_evidence_pack(result)


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
]
