"""Legacy summary review interfaces on the single shared research engine.

Research planning, tools, deadlines and review state are owned by the engine.
This module only preserves the older typed summary/revision contracts.
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


# The agent_tools and zopedia_agent modules pull in the wider services
# package (zopedia_resolver.py → attention_home_summary.py → services.aql), so importing
# them eagerly at module load time creates a circular import for the AQL
# package. Resolve them lazily inside the functions that actually need them.


# --- Configurable limits ---
_P_CRITIQUE_ENABLED = register_config_param(
    "Critique+Judge enabled",
    group="AQL / Research",
    default=1,
    description="When non-zero, run the critique+judge layer after the homepage summary LLM",
)


# --- Tool subset ---


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

_JUDGE_SYSTEM_PROMPT = register_narrative_prompt(
    name="Homepage Summary Judge (apply critique fixes)",
    file="services/aql/critique.py",
    group="AQL / Research", stage='synthesis',
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
        "6. For type='gap' issues, replace the weak absence-language with the critique evidence "
        "or cut the sentence if the critique only identifies a missing slot. "
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


def _featured_symbols(summary: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for symbol in list(summary.get("featured_symbols") or []):
        text = str(symbol or "").upper().strip()
        if text and text not in out:
            out.append(text)
    return out


def critique_home_summary(
    *,
    summary: dict[str, Any],
    home_payload: dict[str, Any],
    llm_client: LLMClient,
    query_service: "QueryService | None" = None,
    max_tool_calls: int | None = None,
) -> dict[str, Any]:
    """Compatibility operation on the shared agent, with no separate tool loop."""
    from ..aql_zopedia_engine import run_aql_zopedia_structured_agent

    if llm_client is None or int(get_config_param(_P_CRITIQUE_ENABLED) or 0) == 0:
        return {"issues": [], "tool_calls": [], "skipped": True}
    result = run_aql_zopedia_structured_agent(
        query=(
            "Review this market summary for usefulness, relevant business connections, factual errors and "
            "overstatement. Read the supplied completed research first and acquire evidence only where it "
            "could materially change the answer. Return concrete issues; an empty list is valid.\n\n"
            + json.dumps({"summary": summary, "context": home_payload}, default=str)
        ),
        schema_name="home_summary_review",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"issues": {"type": "array", "items": _ISSUE_SCHEMA}},
            "required": ["issues"],
        },
        task="home_summary_review", stage="review",
        surface="attention.home_summary_review",
        llm_client=llm_client,
        service=query_service,
        max_tool_calls=max_tool_calls,
        persist_findings=False,
        write_policy="none",
    )
    agent = result.get("agent_result") or {}
    return {
        "issues": (result.get("payload") or {}).get("issues", []),
        "tool_calls": agent.get("tool_calls") or [],
        "run_id": agent.get("run_id"),
        "status": result.get("status"),
        "error": result.get("error"),
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
        from ..aql_zopedia_engine import run_aql_zopedia_structured_agent

        engine_operation = run_aql_zopedia_structured_agent(
            query=_judge_user_prompt(original=original, issues=issues),
            instructions=get_prompt(_JUDGE_SYSTEM_PROMPT),
            schema_name="aql_judge_revision",
            schema=_JUDGE_SCHEMA,
            task="aql_judge_revision", stage='synthesis',
            surface="services.aql.critique",
            llm_client=llm_client,
            max_tool_calls=0,
            persist_findings=False,
            write_policy="none",
            call_type="formatter_over_aql",
            operation_metadata=None,
        )
        if engine_operation.get("status") != "completed":
            raise RuntimeError(
                engine_operation.get("error") or "Shared AI operation failed"
            )
        data = engine_operation["payload"]
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
