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
            "enum": ["numeric", "contradiction", "unsupported", "stale", "other"],
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
        "revisions": {"type": "array", "items": _JUDGE_REVISION_SCHEMA},
    },
    "required": ["overview", "sections", "audio_text", "revisions"],
}


# --- Prompts ---
_CRITIQUE_SYSTEM_PROMPT = register_narrative_prompt(
    name="Homepage Summary Critique (fact-check editor with tools)",
    file="services/aql/critique.py",
    group="AQL / Research",
    prompt=(
        "You are a fact-check editor reviewing a freshly written homepage market summary. "
        "Your only job is to flag flaws — never rephrase, never write replacement text. "
        "\n\nFlag these issue types: "
        "1. numeric — a percent move, price, or count that is invented, off, or unverified. "
        "2. contradiction — the summary says one thing in the overview and the opposite in a section, "
        "or names a catalyst then says no catalyst was confirmed. "
        "3. unsupported — a named catalyst, narrative, or causal claim with no grounding evidence in inputs. "
        "4. stale — a claim that contradicts current data (e.g. wrong direction, outdated price). "
        "5. other — any other clear factual or logical flaw. "
        "\n\nUse the tools to verify specific claims: "
        "- investigator.technical_signals → check actual price / move / regime for a ticker mentioned in the summary. "
        "- investigator.recent_news → check whether a named catalyst actually appears in recent news. "
        "- research.search_evidence / research.retained_context → check whether a narrative is in retained evidence. "
        "- research.live_event_evidence → fresh web check when retained evidence is thin. "
        "\n\nDo NOT call a tool with the same arguments twice. "
        "When you have enough evidence — or when the summary looks clean — return action='final' "
        "with the full issues list (empty list is fine if nothing is wrong). "
        "Keep evidence strings concrete: actual numbers, headlines, source names. "
        "Severity: high = numeric or contradiction visible to a reader; medium = unsupported claim; "
        "low = phrasing nit."
    ),
)

_JUDGE_SYSTEM_PROMPT = register_narrative_prompt(
    name="Homepage Summary Judge (apply critique fixes)",
    file="services/aql/critique.py",
    group="AQL / Research",
    prompt=(
        "You are the editor-in-chief deciding which critique flags to act on. "
        "You receive: (1) the original summary as overview/sections/audio_text, "
        "(2) a numbered list of critique issues with evidence. "
        "\n\nFor each issue, decide: drop (cut the offending text), rephrase (rewrite to match evidence), "
        "or keep (critique was wrong or trivial). Record the decision in `revisions` with the issue_index. "
        "\n\nThen emit the revised summary. Rules: "
        "1. Preserve every specific that was NOT flagged. Do not generalize unflagged content. "
        "2. Only modify text tied to a flagged issue. "
        "3. Never introduce a new claim, ticker, or narrative the critique did not surface. "
        "4. If you drop a sentence, do not pad with filler — shorter is fine. "
        "5. If a numeric claim was flagged but you have no replacement number from the evidence, "
        "remove the number rather than guess. "
        "6. The `audio_text` must reflect the revised content (2–3 sentences, anchor-style). "
        "Return overview/sections/audio_text in the same shape as the original."
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


def _critique_user_prompt(
    *,
    summary_text: str,
    featured_symbols: list[str],
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
    return (
        f"Homepage summary under review:\n{summary_text}\n\n"
        f"Tickers mentioned in the summary: {', '.join(featured_symbols) or '(none captured)'}\n\n"
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

    summary_lines: list[str] = []
    if overview_text:
        summary_lines.append(overview_text)
    for section in sections:
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

    revised = dict(original)
    revised["summary_text"] = revised_summary_text
    revised["audio_text"] = revised_audio_text
    revised["judge_revisions"] = revisions
    return revised


__all__ = [
    "critique_home_summary",
    "judge_revise_summary",
]
