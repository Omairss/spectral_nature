"""Critique + Judge layer for the AQL homepage summary.

Two personas that run after `_llm_home_summary` to catch and fix
hallucinations in the rendered summary text:

- ``critique_home_summary`` — agentic loop with a focused tool subset.
  Plans tool calls to fact-check numeric claims, named catalysts, and
  internal contradictions. Returns a structured list of issues.
- ``judge_revise_summary`` — single LLM call. Reads the original
  summary plus the critique issues and emits a revised summary in the
  same {overview, sections, audio_text} schema.

Design doc:
  documents/architecture/agents/CRITIQUE_JUDGE_HOMEPAGE_SUMMARY_2026-04-24.md
"""
from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from ..llm import (
    LLMAPIError,
    get_config_param,
    get_prompt,
    register_config_param,
    register_narrative_prompt,
)
from .constants import LLMClient

if TYPE_CHECKING:
    from data_access.query_service import QueryService


# The agent_tools and omnibar_agent modules pull in the wider services
# package (omnibar.py → attention_home_summary.py → services.aql), so importing
# them eagerly at module load time creates a circular import for the AQL
# package. Resolve them lazily inside the functions that actually need them.
def _load_runtime_helpers():
    from ..agent_tools import build_tool_catalog, invoke_tool
    from ..omnibar_agent import (
        _coerce_tool_arguments,
        _summarize_tool_result,
        _tool_entry_by_name,
        _tool_history_prompt,
    )
    return {
        "build_tool_catalog": build_tool_catalog,
        "invoke_tool": invoke_tool,
        "coerce_arguments": _coerce_tool_arguments,
        "summarize_result": _summarize_tool_result,
        "tool_entry_by_name": _tool_entry_by_name,
        "tool_history_prompt": _tool_history_prompt,
    }


# --- Configurable limits ---
_P_CRITIQUE_ENABLED = register_config_param(
    "Critique+Judge enabled",
    group="AQL / Research",
    default=1,
    description="When non-zero, run the critique+judge layer after the homepage summary LLM",
)
_P_CRITIQUE_MAX_TOOL_CALLS = register_config_param(
    "Critique max tool calls",
    group="AQL / Research",
    default=4,
    description="Maximum number of tool calls the critique agent can make per summary",
)


# --- Tool subset ---
_CRITIQUE_TOOL_NAMES = {
    "research.search_evidence",
    "research.retained_context",
    "research.live_event_evidence",
    "investigator.technical_signals",
    "investigator.recent_news",
}


def _filtered_tool_catalog(service: "QueryService", build_tool_catalog) -> list[dict[str, Any]]:
    full_catalog = build_tool_catalog(service)
    return [tool for tool in full_catalog if str(tool.get("name") or "") in _CRITIQUE_TOOL_NAMES]


# --- Schemas ---
_ARGUMENT_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "value_kind": {
            "type": "string",
            "enum": ["string", "number", "boolean", "string_list", "null"],
        },
        "string_value": {"type": ["string", "null"]},
        "number_value": {"type": ["number", "null"]},
        "boolean_value": {"type": ["boolean", "null"]},
        "string_list_value": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
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

_ISSUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "type": {
            "type": "string",
            "enum": ["numeric", "contradiction", "unsupported", "stale", "gap", "orphan_symbol", "other"],
        },
        "location": {"type": "string"},
        "claim": {"type": "string"},
        "severity": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence": {"type": "string"},
    },
    "required": ["type", "location", "claim", "severity", "evidence"],
}

_CRITIQUE_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["tool_call", "final"]},
        "reasoning": {"type": "string"},
        "tool_name": {"type": "string"},
        "tool_arguments": {"type": "array", "items": _ARGUMENT_ENTRY_SCHEMA},
        "issues": {"type": "array", "items": _ISSUE_SCHEMA},
    },
    "required": ["action", "reasoning", "tool_name", "tool_arguments", "issues"],
}

_JUDGE_REVISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "issue_index": {"type": "integer"},
        "decision": {"type": "string", "enum": ["drop", "rephrase", "keep"]},
        "rewritten_text": {"type": "string"},
    },
    "required": ["issue_index", "decision", "rewritten_text"],
}

_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "overview": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "bullets"],
            },
        },
        "audio_text": {"type": "string"},
        "featured_symbols": {"type": "array", "items": {"type": "string"}},
        "revisions": {"type": "array", "items": _JUDGE_REVISION_SCHEMA},
    },
    "required": ["overview", "sections", "audio_text", "featured_symbols", "revisions"],
}


# --- Prompts ---
_CRITIQUE_SYSTEM_PROMPT = register_narrative_prompt(
    name="Homepage Summary Critique (fact-check editor with tools)",
    file="services/aql/critique.py",
    group="AQL / Research",
    prompt=(
        "You are an agentic fact-check editor reviewing a freshly written homepage market summary. "
        "You have web-search tools (Tavily / SerpAPI via research.live_event_evidence and search_evidence) "
        "and per-ticker tools (technical_signals, recent_news). Use them aggressively. "
        "Your only job is to flag flaws — never rephrase, never write replacement text. "
        "\n\nThe user prompt includes a GROUND TRUTH block with the actual numeric moves "
        "(`change_pct`, `surprise_z`), the cause_status, the top retained source, and the retained "
        "narrative (`why`) for every symbol. Treat ground truth as authoritative for *numbers* and "
        "*direction* (e.g. UP vs DOWN). Treat the `cause_status` as a starting point only — "
        "`unresolved` / `continuation` / `developing` mean retention has not pinned the catalyst, "
        "NOT that no catalyst exists. Your job is to FIND IT via tools when the summary says so. "
        "\n\nFlag these issue types: "
        "1. numeric — a percent move, price, or count that disagrees with ground truth or is invented. "
        "2. contradiction — the overview and a section disagree, or one sentence names a catalyst "
        "and the next sentence says no catalyst was confirmed. "
        "3. unsupported — a named catalyst, narrative, or causal claim that contradicts both the "
        "retained `why` AND your tool searches. Do NOT flag a sentence as unsupported just because "
        "cause_status is unresolved/continuation — first check the retained `why` and search the web. "
        "4. stale — wrong direction, wrong sign, or a price that no longer matches current data. "
        "5. gap — the summary uses vague filler like 'no clear catalyst confirmed', 'no single driver "
        "confirmed', 'broad sector de-risking', 'thematic risk bid', 'rallied without confirmed news', "
        "etc. For EVERY such phrase you MUST attempt at least one targeted web search "
        "(research.live_event_evidence) before finalizing. Examples of good gap searches: "
        "'BNO Brent crude rally Strait of Hormuz news today', 'utilities EIX PCG SRE selloff catalyst "
        "today', 'space infrastructure stocks RDW YSS rally short squeeze'. If a credible catalyst "
        "surfaces in the search results, flag as 'gap' and put a one-line summary of what you found "
        "(headline + source) in the `evidence` field — the judge will use that to rewrite. If you "
        "searched and the news genuinely turned up nothing, do not flag — the filler is honest. "
        "6. orphan_symbol — a ticker is in featured_symbols but never appears in the body text, or "
        "vice versa. Severity low. "
        "7. other — any other clear factual or logical flaw. "
        "\n\nUse the tools: "
        "- research.live_event_evidence → fresh web search; THIS is your main weapon for gap detection. "
        "- research.search_evidence / research.retained_context → check the SAA evidence store. "
        "- investigator.recent_news → recent headlines for a single ticker. "
        "- investigator.technical_signals → confirm actual price / direction for a ticker. "
        "\n\nDo NOT call a tool with identical arguments twice. Group queries — one search per cluster "
        "of vague phrases (e.g. one search covering 'utilities EIX PCG SRE selloff catalyst', not one "
        "per ticker). When you have enough evidence — or the summary is clean — return action='final' "
        "with the full issues list. Keep evidence strings concrete: actual headlines, source names, "
        "publish dates. Severity: high = numeric or contradiction; medium = unsupported or a gap "
        "filled with credible new evidence; low = orphan_symbol or phrasing nit."
    ),
)

_JUDGE_SYSTEM_PROMPT = register_narrative_prompt(
    name="Homepage Summary Judge (apply critique fixes)",
    file="services/aql/critique.py",
    group="AQL / Research",
    prompt=(
        "You are the editor-in-chief deciding which critique flags to act on. "
        "You receive: (1) the original summary as overview/sections/audio_text and the original "
        "featured_symbols list, (2) a numbered list of critique issues with evidence. "
        "\n\nFor each issue, decide: drop (cut the offending text), rephrase (rewrite to match evidence), "
        "or keep (critique was wrong or trivial). Record the decision in `revisions` with the issue_index. "
        "\n\nThen emit the revised summary. Rules: "
        "1. Preserve every specific that was NOT flagged. Do not generalize unflagged content. "
        "2. Only modify text tied to a flagged issue. "
        "3. Never introduce a new claim, ticker, or narrative the critique did not surface. "
        "4. If you drop a sentence, do not pad with filler — shorter is fine. "
        "5. If a numeric claim was flagged but you have no replacement number from the evidence, "
        "remove the number rather than guess. "
        "6. For type='gap' issues, the critique already supplied a credible catalyst in the evidence "
        "field. Replace the vague 'no clear catalyst' phrasing with that catalyst, kept tight. "
        "7. For type='orphan_symbol' issues, edit the featured_symbols list — drop tickers that don't "
        "appear in the body, or add tickers that the body discusses but the list omits. "
        "8. The `audio_text` must reflect the revised content and remain a real spoken briefing "
        "(8-12 concise sentences, roughly 150-230 words, anchor-style). "
        "9. Always emit a featured_symbols list in the response, even if you didn't change it — repeat "
        "the original list in that case. "
        "10. Return EXACTLY ONE revised summary. Do not include the original alongside the revised. "
        "Each section title must appear only once in the `sections` array. "
        "Return overview/sections/audio_text/featured_symbols in the same shape as the original."
    ),
)


# --- Helpers ---
def _summary_render_text(summary: dict[str, Any]) -> str:
    """Render the LLM summary dict as the user-visible text the critique should review."""
    parts: list[str] = []
    overview = str(summary.get("summary_text") or "").strip()
    if overview:
        parts.append(overview)
    audio = str(summary.get("audio_text") or "").strip()
    if audio and audio not in overview:
        parts.append(f"[audio_text]\n{audio}")
    return "\n\n".join(parts)


def _featured_symbols(summary: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for symbol in list(summary.get("featured_symbols") or []):
        text = str(symbol or "").upper().strip()
        if text and text not in out:
            out.append(text)
    return out


def _ground_truth_text(home_payload: dict[str, Any]) -> str:
    """Build a compact, structured GROUND TRUTH block from the home_payload.

    Lists per-symbol numeric moves and cause_status from movers/unresolved, and
    the cause_status of top events. The critique uses this to spot numeric and
    catalyst inconsistencies without needing to look at upstream payloads.
    """

    def _fmt_pct(value: object) -> str:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return "n/a"
        return f"{num:+.2f}%"

    def _fmt_z(value: object) -> str:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return "n/a"
        return f"z={num:+.2f}"

    def _trim(text: object, limit: int = 240) -> str:
        clean = " ".join(str(text or "").split()).strip()
        return clean[: max(limit, 40)] + ("…" if len(clean) > limit else "")

    lines: list[str] = []

    movers = list(home_payload.get("must_read_movers") or [])
    if movers:
        lines.append("Must-read movers (retained narrative is supported):")
        for item in movers[:6]:
            symbol = str(item.get("symbol") or "?").upper()
            change = _fmt_pct(item.get("change_pct"))
            surprise = _fmt_z(item.get("surprise_z"))
            cause = str(item.get("cause_status") or "?")
            top_source = str(item.get("top_source") or "")
            confidence = str(item.get("confidence_label") or "")
            why = _trim(item.get("why_now_text") or item.get("why_happened_text"), limit=260)
            tail = f"src={top_source}" if top_source else ""
            tail = (tail + f" conf={confidence}").strip()
            lines.append(f"- {symbol}: {change} ({surprise}, cause={cause}) {tail}".rstrip())
            if why:
                lines.append(f"    why_retained: {why}")

    unresolved = list(home_payload.get("unresolved_large_moves") or [])
    if unresolved:
        lines.append("")
        lines.append(
            "Unresolved large moves (cause is OPEN in retained evidence — search the web "
            "to look for a catalyst before accepting filler phrasing):"
        )
        for item in unresolved[:8]:
            symbol = str(item.get("symbol") or "?").upper()
            change = _fmt_pct(item.get("change_pct"))
            surprise = _fmt_z(item.get("surprise_z"))
            cause = str(item.get("cause_status") or "?")
            top_source = str(item.get("top_source") or "")
            why = _trim(item.get("why_now_text") or item.get("why_happened_text"), limit=260)
            tail = f"src={top_source}" if top_source else ""
            lines.append(f"- {symbol}: {change} ({surprise}, cause={cause}) {tail}".rstrip())
            if why:
                lines.append(f"    why_retained: {why}")

    top_events = list(home_payload.get("top_events") or [])
    if top_events:
        lines.append("")
        lines.append("Top events (clusters):")
        for event in top_events[:5]:
            title = str(event.get("event_title") or "?")
            cause = str(event.get("cause_status") or "?")
            event_type = str(event.get("event_type") or "")
            symbols = ", ".join(
                str(s).upper() for s in list(event.get("supporting_symbols") or [])[:6]
            )
            why = _trim(event.get("why_happened_text"), limit=260)
            lines.append(f"- [{event_type}] {title} (cause={cause}; symbols: {symbols})")
            if why:
                lines.append(f"    why_retained: {why}")

    if not lines:
        return ""
    return (
        "GROUND TRUTH (from home_payload — numbers and direction are authoritative; "
        "retained `why` is a starting point but may be incomplete — search the web when needed):\n"
        + "\n".join(lines)
    )


def _critique_user_prompt(
    *,
    summary_text: str,
    featured_symbols: list[str],
    ground_truth_text: str,
    tool_calls: list[dict[str, Any]],
    tool_catalog: list[dict[str, Any]],
    max_tool_calls: int,
    tool_history_prompt,
) -> str:
    catalog_lines = []
    for tool in tool_catalog:
        params = ", ".join(
            list((dict(tool.get("inputSchema") or {}).get("properties") or {}).keys())
        ) or "none"
        catalog_lines.append(f"- {tool.get('name')}: {tool.get('description')}; params: {params}")
    ground_truth_block = f"{ground_truth_text}\n\n" if ground_truth_text else ""
    return (
        f"{ground_truth_block}"
        f"Homepage summary under review:\n{summary_text}\n\n"
        f"Tickers mentioned in the summary (featured_symbols list): {', '.join(featured_symbols) or '(none captured)'}\n\n"
        f"Tool budget: {max_tool_calls - len(tool_calls)} remaining out of {max_tool_calls}.\n\n"
        "Available tools:\n"
        f"{chr(10).join(catalog_lines)}\n\n"
        "Tool call history:\n"
        f"{tool_history_prompt(tool_calls)}\n\n"
        "Return exactly one next action. "
        "If you still want to verify something, set action='tool_call', pick one tool, and supply "
        "tool_arguments as a list of typed entries. "
        "If you have seen enough — or the summary is clean — set action='final' and put your final "
        "issues list in the `issues` field. An empty issues list is valid."
    )


def critique_home_summary(
    *,
    summary: dict[str, Any],
    home_payload: dict[str, Any],
    llm_client: LLMClient,
    query_service: "QueryService | None" = None,
    max_tool_calls: int | None = None,
) -> dict[str, Any]:
    """Run the critique loop over a freshly built homepage summary.

    Returns: ``{"issues": [...], "tool_calls": [...], "run_id": str}``.
    Returns an empty issues list (and possibly empty tool_calls) on any failure
    so the caller can safely fall back to the original summary.
    """
    run_id = f"critique_{uuid.uuid4().hex[:10]}"
    if int(get_config_param(_P_CRITIQUE_ENABLED) or 0) == 0:
        return {"issues": [], "tool_calls": [], "run_id": run_id, "skipped": True}
    if llm_client is None:
        return {"issues": [], "tool_calls": [], "run_id": run_id, "skipped": True}

    helpers = _load_runtime_helpers()
    build_tool_catalog = helpers["build_tool_catalog"]
    invoke_tool = helpers["invoke_tool"]
    coerce_arguments = helpers["coerce_arguments"]
    summarize_result = helpers["summarize_result"]
    tool_entry_by_name = helpers["tool_entry_by_name"]
    tool_history_prompt = helpers["tool_history_prompt"]

    resolved_max = max_tool_calls if max_tool_calls is not None else int(get_config_param(_P_CRITIQUE_MAX_TOOL_CALLS))
    resolved_max = max(int(resolved_max), 1)

    if query_service is None:
        from data_access.query_service import QueryService as _QueryService
        resolved_service = _QueryService.from_environment()
    else:
        resolved_service = query_service

    tool_catalog = _filtered_tool_catalog(resolved_service, build_tool_catalog)
    summary_text = _summary_render_text(summary)
    featured_symbols = _featured_symbols(summary)
    ground_truth_text = _ground_truth_text(home_payload or {})

    if not summary_text.strip():
        return {"issues": [], "tool_calls": [], "run_id": run_id, "skipped": True}

    tool_calls: list[dict[str, Any]] = []
    seen_call_keys: set[str] = set()
    final_issues: list[dict[str, Any]] = []

    for step_index in range(resolved_max + 1):
        try:
            step = llm_client.generate_json(
                system_prompt=get_prompt(_CRITIQUE_SYSTEM_PROMPT),
                user_prompt=_critique_user_prompt(
                    summary_text=summary_text,
                    featured_symbols=featured_symbols,
                    ground_truth_text=ground_truth_text,
                    tool_calls=tool_calls,
                    tool_catalog=tool_catalog,
                    max_tool_calls=resolved_max,
                    tool_history_prompt=tool_history_prompt,
                ),
                schema_name="aql_critique_step",
                schema=_CRITIQUE_STEP_SCHEMA,
            )
        except (LLMAPIError, Exception):
            break

        action = str(step.get("action") or "").lower()
        if action == "final":
            final_issues = list(step.get("issues") or [])
            break

        if len(tool_calls) >= resolved_max:
            # Out of tool budget but model didn't finalize — treat any flagged
            # issues so far as the final list.
            final_issues = list(step.get("issues") or [])
            break

        tool_name = str(step.get("tool_name") or "").strip()
        tool_entry = tool_entry_by_name(tool_catalog, tool_name)
        if not tool_entry:
            tool_calls.append({
                "tool_call_id": f"tc_{len(tool_calls)+1:02d}",
                "tool_name": tool_name,
                "arguments": {},
                "status": "failed",
                "result_summary": {
                    "preview_text": f"Tool '{tool_name}' is not in the critique catalog.",
                    "llm_context_text": "",
                },
            })
            continue

        arguments, arg_error = coerce_arguments(step.get("tool_arguments") or [])
        if arg_error:
            tool_calls.append({
                "tool_call_id": f"tc_{len(tool_calls)+1:02d}",
                "tool_name": tool_name,
                "arguments": {},
                "status": "failed",
                "result_summary": {"preview_text": arg_error, "llm_context_text": ""},
            })
            continue

        call_key = f"{tool_name}::{json.dumps(arguments, sort_keys=True, default=str)}"
        if call_key in seen_call_keys:
            tool_calls.append({
                "tool_call_id": f"tc_{len(tool_calls)+1:02d}",
                "tool_name": tool_name,
                "arguments": arguments,
                "status": "skipped",
                "result_summary": {
                    "preview_text": "Duplicate tool call skipped.",
                    "llm_context_text": "",
                },
            })
            continue
        seen_call_keys.add(call_key)

        try:
            result = invoke_tool(
                service=resolved_service,
                tool_name=tool_name,
                arguments=arguments,
                run_id=run_id,
            )
            summary_block = summarize_result(result)
            tool_calls.append({
                "tool_call_id": f"tc_{len(tool_calls)+1:02d}",
                "tool_name": tool_name,
                "arguments": arguments,
                "status": "completed",
                "result_summary": summary_block,
            })
        except Exception as exc:
            tool_calls.append({
                "tool_call_id": f"tc_{len(tool_calls)+1:02d}",
                "tool_name": tool_name,
                "arguments": arguments,
                "status": "failed",
                "result_summary": {
                    "preview_text": f"Tool error: {exc}",
                    "llm_context_text": "",
                },
            })

    # Normalize issues
    cleaned_issues: list[dict[str, Any]] = []
    for issue in final_issues:
        if not isinstance(issue, dict):
            continue
        claim = str(issue.get("claim") or "").strip()
        if not claim:
            continue
        cleaned_issues.append({
            "type": str(issue.get("type") or "other").lower(),
            "location": str(issue.get("location") or "").strip(),
            "claim": claim,
            "severity": str(issue.get("severity") or "medium").lower(),
            "evidence": str(issue.get("evidence") or "").strip(),
        })

    return {
        "issues": cleaned_issues,
        "tool_calls": tool_calls,
        "run_id": run_id,
        "skipped": False,
    }


def _judge_user_prompt(
    *,
    original: dict[str, Any],
    issues: list[dict[str, Any]],
) -> str:
    original_view = {
        "summary_text": str(original.get("summary_text") or ""),
        "audio_text": str(original.get("audio_text") or ""),
        "featured_symbols": _featured_symbols(original),
    }
    # Note for the judge: the schema requires featured_symbols, so it must
    # always be returned (echoed verbatim if no orphan_symbol issues exist).
    numbered_issues = [
        {
            "issue_index": index,
            "type": issue.get("type"),
            "location": issue.get("location"),
            "claim": issue.get("claim"),
            "severity": issue.get("severity"),
            "evidence": issue.get("evidence"),
        }
        for index, issue in enumerate(issues)
    ]
    payload = {
        "original_summary": original_view,
        "critique_issues": numbered_issues,
    }
    return (
        "Apply the critique to the original summary.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str, indent=2)}\n\n"
        "Return overview, sections (each with title + bullets), audio_text, "
        "and revisions describing your decision per issue_index."
    )


def judge_revise_summary(
    *,
    original: dict[str, Any],
    critique: dict[str, Any],
    llm_client: LLMClient,
) -> dict[str, Any] | None:
    """Apply critique findings to produce a revised summary.

    Returns ``None`` when the judge cannot run or produces nothing usable, so
    the caller can fall back to ``original``.
    """
    issues = list((critique or {}).get("issues") or [])
    if not issues or llm_client is None:
        return None

    try:
        data = llm_client.generate_json(
            system_prompt=get_prompt(_JUDGE_SYSTEM_PROMPT),
            user_prompt=_judge_user_prompt(original=original, issues=issues),
            schema_name="aql_judge_revision",
            schema=_JUDGE_SCHEMA,
        )
    except (LLMAPIError, Exception):
        return None

    overview_text = str(data.get("overview") or "").strip()
    sections = list(data.get("sections") or [])

    # Defensive: the judge has occasionally returned each section twice (once
    # representing the original, once revised) despite prompt rules. Dedupe by
    # title so the rendered summary never repeats.
    seen_titles: set[str] = set()
    unique_sections: list[dict[str, Any]] = []
    for section in sections:
        title = str(section.get("title") or "").strip()
        if not title:
            continue
        key = title.lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        unique_sections.append(section)

    summary_lines: list[str] = []
    if overview_text:
        summary_lines.append(overview_text)
    for section in unique_sections:
        title = str(section.get("title") or "").strip()
        bullets = [str(b or "").strip() for b in list(section.get("bullets") or []) if str(b or "").strip()]
        if not title or not bullets:
            continue
        if summary_lines:
            summary_lines.append("")
        summary_lines.append(f"**{title}**")
        summary_lines.extend(f"- {b}" for b in bullets)

    revised_summary_text = "\n".join(line for line in summary_lines if line is not None).strip()
    if not revised_summary_text:
        return None

    revised_audio_text = str(data.get("audio_text") or "").strip() or overview_text or revised_summary_text

    revisions = []
    for entry in list(data.get("revisions") or []):
        if not isinstance(entry, dict):
            continue
        revisions.append({
            "issue_index": int(entry.get("issue_index") or 0),
            "decision": str(entry.get("decision") or "").lower(),
            "rewritten_text": str(entry.get("rewritten_text") or ""),
        })

    revised_featured_symbols: list[str] = []
    raw_symbols = data.get("featured_symbols")
    if isinstance(raw_symbols, list):
        for symbol in raw_symbols:
            text = str(symbol or "").upper().strip()
            if text and text not in revised_featured_symbols:
                revised_featured_symbols.append(text)
    if not revised_featured_symbols:
        revised_featured_symbols = _featured_symbols(original)

    revised = dict(original)
    revised["summary_text"] = revised_summary_text
    revised["audio_text"] = revised_audio_text
    revised["featured_symbols"] = revised_featured_symbols
    revised["judge_revisions"] = revisions
    return revised


__all__ = [
    "critique_home_summary",
    "judge_revise_summary",
]
