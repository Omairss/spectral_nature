"""Shared hypothesis verification interface.

Agents and AQL both need hypothesis grading. This module owns the contract so
neither side imports the other's summarizer internals.
"""
from __future__ import annotations

import json
from typing import Any

from .contracts import HYPOTHESIS_VERIFICATION_SCHEMA
from .market_activity import _coerce_text
from ..llm import LLMAPIError, get_prompt, register_narrative_prompt


_HYPOTHESIS_VERIFICATION_SYSTEM_PROMPT = register_narrative_prompt(
    name="Hypothesis Verification (grade + gap queries)",
    file="services/common/hypothesis.py",
    group="Common / Research",
    prompt=(
        "You are a senior market analyst verifying a hypothesis against evidence. "
        "Be rigorous: a hypothesis is only 'supported' when multiple independent claims confirm it. "
        "Mark it 'weak' when evidence is thin but directionally consistent. "
        "Mark it 'conflicting' when claims contradict each other. "
        "Mark it 'unsupported' when the evidence does not back the hypothesis at all. "
        "List specific claims that support or contradict, not paraphrases. "
        "gap_queries must be concrete web search queries that would fill the holes in the evidence. "
        "Write them as you would type into a news search engine. "
        "Only include gap_queries when the verdict is NOT 'supported'. "
        "Keep reasoning to 2-3 sentences."
    ),
)


def _trim_text(value: object, limit: int = 800) -> str:
    text = _coerce_text(value)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "..."


def verify_hypothesis(
    *,
    hypothesis: str,
    claims: list[dict[str, Any]],
    stories: list[dict[str, object]],
    llm_client: Any,
    signal_context: str = "",
) -> dict[str, Any]:
    """Grade a hypothesis against supporting claims and market stories."""
    claim_rows = [
        {
            "claim_text": _coerce_text(item.get("claim_text")),
            "claim_type": _coerce_text(item.get("claim_type")),
            "source": _coerce_text(item.get("source")),
            "freshness_class": _coerce_text(item.get("freshness_class")),
            "confidence_score": float(item.get("confidence_score") or 0.0),
            "relevance_score": float(item.get("relevance_score") or 0.0),
            "causal_score": float(item.get("causal_score") or 0.0),
            "is_same_day": bool(item.get("is_same_day")),
        }
        for item in claims
        if _coerce_text(item.get("claim_text"))
    ]
    if not claim_rows:
        return _unverified_result("No evidence claims were available for LLM verification.")

    story_rows = [
        {
            "kind": _coerce_text(story.get("kind")),
            "sentence": _coerce_text(story.get("sentence")),
            "symbols": [str(s).upper().strip() for s in list(story.get("symbols") or []) if str(s).strip()],
        }
        for story in (stories or [])[:6]
    ]
    user_data: dict[str, Any] = {
        "hypothesis": hypothesis,
        "claims": claim_rows[:12],
        "stories": story_rows,
    }
    if signal_context:
        user_data["signal_context"] = _trim_text(signal_context, limit=800)

    try:
        data = llm_client.generate_json(
            system_prompt=get_prompt(_HYPOTHESIS_VERIFICATION_SYSTEM_PROMPT),
            user_prompt=json.dumps(user_data, ensure_ascii=False, default=str),
            schema_name="hypothesis_verification",
            schema=HYPOTHESIS_VERIFICATION_SCHEMA,
        )
    except (LLMAPIError, Exception):
        return _unverified_result("LLM verification failed; no verdict was generated.")

    verdict = _coerce_text(data.get("verdict")).lower()
    if verdict not in {"supported", "weak", "conflicting", "unsupported"}:
        verdict = "weak"
    confidence = _coerce_text(data.get("confidence")).lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    gap_queries: list[dict[str, str]] = []
    for item in list(data.get("gap_queries") or []):
        if isinstance(item, dict):
            query = _coerce_text(item.get("query"))
            rationale = _coerce_text(item.get("rationale"))
            if query:
                gap_queries.append({"query": query, "rationale": rationale})

    return {
        "verdict": verdict,
        "confidence": confidence,
        "supporting_claims": [_coerce_text(c) for c in list(data.get("supporting_claims") or []) if _coerce_text(c)],
        "contradicting_claims": [_coerce_text(c) for c in list(data.get("contradicting_claims") or []) if _coerce_text(c)],
        "gap_queries": gap_queries,
        "reasoning": _coerce_text(data.get("reasoning")),
    }


def _unverified_result(reason: str) -> dict[str, Any]:
    return {
        "verdict": "unsupported",
        "confidence": "low",
        "supporting_claims": [],
        "contradicting_claims": [],
        "gap_queries": [],
        "reasoning": reason,
    }


__all__ = ["verify_hypothesis"]
