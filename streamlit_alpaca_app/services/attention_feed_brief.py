from __future__ import annotations

import json
import re
from typing import Any

from .llm import AzureOpenAIChatJSONClient, OpenAIChatJSONClient


FEED_BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lead_text": {"type": "string"},
        "cluster_text": {"type": "string"},
        "headline_text": {"type": "string"},
        "company_text": {"type": "string"},
        "explainer_text": {"type": "string"},
        "watchpoint_text": {"type": "string"},
    },
    "required": [
        "lead_text",
        "cluster_text",
        "headline_text",
        "company_text",
        "explainer_text",
        "watchpoint_text",
    ],
}

_NUMERIC_PROSE_PATTERNS = (
    r"\bobserved\b",
    r"\bexpected\b",
    r"\bresidual\b",
    r"\bzscore\b",
    r"\battention score\b",
    r"\d+(?:\.\d+)?%",
)

_TERM_EXPLAINERS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bphase 1\b", re.IGNORECASE),
        "Phase 1 is the first human trial stage, mainly focused on basic safety and dosing.",
    ),
    (
        re.compile(r"\bphase 2\b", re.IGNORECASE),
        "Phase 2 is a mid-stage clinical trial meant to show whether a drug is starting to help patients while safety is still being monitored.",
    ),
    (
        re.compile(r"\bphase 3\b", re.IGNORECASE),
        "Phase 3 is the large late-stage trial that usually tests whether the drug works well enough to support approval.",
    ),
    (
        re.compile(r"\bmaintenance data\b", re.IGNORECASE),
        "Maintenance data shows whether the benefit of a treatment holds up after the initial response period.",
    ),
    (
        re.compile(r"\bil-?13\b", re.IGNORECASE),
        "IL-13 is an immune-system signaling protein linked to inflammation, so therapies that block it are trying to calm that response.",
    ),
    (
        re.compile(r"\bantibody candidate\b", re.IGNORECASE),
        "An antibody candidate is a drug still in development that uses a lab-designed protein to bind a specific biological target.",
    ),
    (
        re.compile(r"\batopic dermatitis\b", re.IGNORECASE),
        "Atopic dermatitis is a chronic inflammatory skin condition, often referred to as eczema.",
    ),
    (
        re.compile(r"\bprivate credit\b", re.IGNORECASE),
        "Private credit refers to loans made outside public bond markets, often directly to companies by asset managers or private funds.",
    ),
]


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _trim(text: object, limit: int = 260) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _sentence_split(text: str) -> list[str]:
    clean = " ".join(str(text or "").split())
    if not clean:
        return []
    parts = re.split(r"(?<=[.!?])\s+", clean)
    return [part.strip() for part in parts if part.strip()]


def _clean_numeric_prose(text: object, *, fallback: str = "") -> str:
    sentences = _sentence_split(_coerce_text(text))
    kept: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(re.search(pattern, lowered) for pattern in _NUMERIC_PROSE_PATTERNS):
            continue
        kept.append(sentence)
    if kept:
        return " ".join(kept[:2])
    return _trim(fallback)


def _top_headline_lines(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows = payload.get("headline_items") or []
    out: list[dict[str, str]] = []
    if not isinstance(rows, list):
        return out
    for raw in rows[:3]:
        if not isinstance(raw, dict):
            continue
        headline = _coerce_text(raw.get("headline"))
        if not headline:
            continue
        out.append(
            {
                "headline": headline,
                "summary": _coerce_text(raw.get("summary")),
                "source": _coerce_text(raw.get("source")),
            }
        )
    return out


def _fallback_explainer(payload: dict[str, Any]) -> str:
    text_blob = " ".join(
        [
            _coerce_text(payload.get("lead_text")),
            _coerce_text(payload.get("cluster_text")),
            _coerce_text(payload.get("headline_text")),
            _coerce_text(payload.get("company_description")),
            " ".join(
                " ".join([item.get("headline", ""), item.get("summary", "")])
                for item in _top_headline_lines(payload)
            ),
            _coerce_text(payload.get("context_summary")),
            _coerce_text(payload.get("context_narrative")),
        ]
    )
    matches: list[str] = []
    for pattern, explanation in _TERM_EXPLAINERS:
        if pattern.search(text_blob):
            matches.append(explanation)
    if not matches:
        return ""
    return " ".join(dict.fromkeys(matches))


def _fallback_brief(payload: dict[str, Any]) -> dict[str, str]:
    cluster_text = _clean_numeric_prose(
        payload.get("news_narrative"),
        fallback=_coerce_text(payload.get("story_text")) or _coerce_text(payload.get("context_summary")),
    )
    headline_items = _top_headline_lines(payload)
    headline_text = ""
    if headline_items:
        top = headline_items[0]
        source = top["source"] or "A fresh headline"
        summary = top["summary"]
        if summary:
            headline_text = f"{source} is reinforcing the move with '{top['headline']}'. In context, {summary[0].lower() + summary[1:] if len(summary) > 1 else summary.lower()}."
        else:
            headline_text = f"{source} is reinforcing the move with '{top['headline']}'."

    company_text = _clean_numeric_prose(payload.get("company_description"))
    explainer_text = _fallback_explainer(payload)
    context_summary = _clean_numeric_prose(payload.get("context_summary"))
    lead_candidates = [
        _clean_numeric_prose(payload.get("story_text")),
        context_summary,
        headline_text,
        cluster_text,
    ]
    lead_text = next((text for text in lead_candidates if text), "")

    return {
        "lead_text": lead_text,
        "cluster_text": cluster_text,
        "headline_text": _trim(headline_text, 280),
        "company_text": _trim(company_text, 280),
        "explainer_text": _trim(explainer_text, 520),
        "watchpoint_text": _trim(
            _coerce_text(payload.get("context_why_now")) or _coerce_text(payload.get("watchpoint_text")),
            220,
        ),
    }


def build_attention_feed_brief(
    payload: dict[str, Any] | None,
    llm_client: OpenAIChatJSONClient | AzureOpenAIChatJSONClient | None,
) -> dict[str, str]:
    normalized = dict(payload or {})
    fallback = _fallback_brief(normalized)
    if llm_client is None:
        return fallback

    prompt_payload = {
        "symbol": _coerce_text(normalized.get("symbol")),
        "company_name": _coerce_text(normalized.get("company_name")),
        "title": _coerce_text(normalized.get("title")),
        "subtitle": _coerce_text(normalized.get("subtitle")),
        "story_text": _clean_numeric_prose(normalized.get("story_text")),
        "why_now_text": _clean_numeric_prose(normalized.get("why_now_text")),
        "peer_group_name": _coerce_text(normalized.get("peer_group_name")),
        "regime_label": _coerce_text(normalized.get("regime_label")),
        "linked_news_count": normalized.get("linked_news_count"),
        "news_narrative": _coerce_text(normalized.get("news_narrative")),
        "headline_items": _top_headline_lines(normalized),
        "company_description": _coerce_text(normalized.get("company_description")),
        "context_headline": _coerce_text(normalized.get("context_headline")),
        "context_summary": _coerce_text(normalized.get("context_summary")),
        "context_narrative": _coerce_text(normalized.get("context_narrative")),
        "context_why_now": _coerce_text(normalized.get("context_why_now")),
        "watchpoint_text": _coerce_text(normalized.get("watchpoint_text")),
        "primary_source_excerpt": _coerce_text(normalized.get("primary_source_excerpt")),
        "fallback": fallback,
    }

    data = llm_client.generate_json(
        system_prompt=(
            "You write market-attention feed cards for readers who may know nothing about the company. "
            "Boil down the essentials of the market dynamic in plain English. "
            "Never restate residuals, z-scores, observed-vs-expected percentages, or other model metrics in prose. "
            "Those belong in charts. Focus on what the market thinks is happening, what headline is reinforcing it, "
            "what the company does, and what any specialist term means."
        ),
        user_prompt=(
            "Create a concise feed brief from the supplied context.\n"
            "- `lead_text`: 1-2 sentences that explain the market dynamic for a cold reader.\n"
            "- `cluster_text`: what coverage is clustering around and why that theme matters.\n"
            "- `headline_text`: name the freshest reinforcing headline and give its context.\n"
            "- `company_text`: explain what the company does in plain English.\n"
            "- `explainer_text`: explain any specialist term or product language if relevant; otherwise return an empty string.\n"
            "- `watchpoint_text`: the cleanest near-term watchpoint, without metric jargon.\n"
            "Use only the supplied material and leave a field empty instead of inventing missing detail.\n"
            f"{json.dumps(prompt_payload, ensure_ascii=False, default=str, indent=2)}"
        ),
        schema_name="attention_feed_brief",
        schema=FEED_BRIEF_SCHEMA,
    )
    return {key: _trim(data.get(key), 520 if key == "explainer_text" else 280) for key in FEED_BRIEF_SCHEMA["required"]}


__all__ = ["FEED_BRIEF_SCHEMA", "build_attention_feed_brief"]
