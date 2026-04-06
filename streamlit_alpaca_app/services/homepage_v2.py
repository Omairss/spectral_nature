from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

import pandas as pd

from .llm import OpenAIChatJSONClient


HOMEPAGE_V2_RESEARCH_PANEL = "research"
HOMEPAGE_V2_COMPANY_PANEL = "company"
HOMEPAGE_V2_DETAIL_PANELS = (
    HOMEPAGE_V2_RESEARCH_PANEL,
    HOMEPAGE_V2_COMPANY_PANEL,
)

HOMEPAGE_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "dek": {"type": "string"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "beat_id": {"type": "string"},
                    "sentence": {"type": "string"},
                    "summary": {"type": "string"},
                    "event_ids": {"type": "array", "items": {"type": "string"}},
                    "symbols": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["beat_id", "sentence", "summary", "event_ids", "symbols"],
            },
        },
    },
    "required": ["headline", "dek", "beats"],
}


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _coerce_timestamp(value: object) -> str:
    parsed = value
    if not isinstance(parsed, datetime):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _trim(text: object, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _stable_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _prepare_events(event_records: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(event_records or []):
        if not isinstance(raw, dict):
            continue
        event_id = _coerce_text(raw.get("event_id")) or f"event-{index + 1}"
        symbol = _coerce_text(raw.get("symbol") or raw.get("entity_id")).upper()
        title = _coerce_text(raw.get("title"))
        if not symbol or not title:
            continue
        rows.append(
            {
                "event_id": event_id,
                "symbol": symbol,
                "title": title,
                "subtitle": _coerce_text(raw.get("subtitle")),
                "source_label": _coerce_text(raw.get("source_label")),
                "horizon": _coerce_text(raw.get("horizon")),
                "anomaly_type": _coerce_text(raw.get("anomaly_type")),
                "attention_score": _coerce_text(raw.get("attention_score")),
                "story_text": _trim(raw.get("story_text"), 260),
                "why_now_text": _trim(raw.get("why_now_text"), 220),
                "cluster_text": _trim(raw.get("cluster_text"), 240),
                "headline_text": _trim(raw.get("headline_text"), 240),
                "company_text": _trim(raw.get("company_text"), 240),
                "explainer_text": _trim(raw.get("explainer_text"), 260),
                "next_action": _trim(raw.get("next_best_action"), 120),
                "news_summary": _trim(raw.get("news_summary_text"), 260),
                "news_headlines": [
                    _trim(item, 120)
                    for item in list(raw.get("news_headlines") or [])
                    if _coerce_text(item)
                ][:3],
                "context_headline": _trim(raw.get("context_headline"), 180),
                "context_summary": _trim(raw.get("context_summary_text"), 260),
                "context_why_now": _trim(raw.get("context_why_now"), 180),
                "management_signal": _trim(raw.get("management_signal"), 180),
            }
        )
    return rows


def _fallback_summary(event: dict[str, Any]) -> str:
    parts: list[str] = []
    story = _coerce_text(event.get("story_text"))
    cluster_text = _coerce_text(event.get("cluster_text"))
    headline_text = _coerce_text(event.get("headline_text"))
    company_text = _coerce_text(event.get("company_text"))
    explainer_text = _coerce_text(event.get("explainer_text"))
    news_summary = _coerce_text(event.get("news_summary"))
    context_summary = _coerce_text(event.get("context_summary"))
    next_action = _coerce_text(event.get("next_action"))

    if story:
        parts.append(story)
    for candidate in [cluster_text, headline_text, company_text, explainer_text]:
        if candidate and candidate not in parts:
            parts.append(candidate)
    if news_summary:
        parts.append(news_summary)
    elif context_summary:
        parts.append(context_summary)
    if next_action:
        parts.append(f"Next watchpoint: {next_action}.")
    return " ".join(parts[:3]).strip()


def _fallback_digest(events: list[dict[str, Any]], *, generated_at_utc: str) -> dict[str, Any]:
    beats: list[dict[str, Any]] = []
    for index, event in enumerate(events[: min(len(events), 12)]):
        sentence = _coerce_text(event.get("story_text")) or f"{event['symbol']} is demanding attention after a fresh anomaly."
        beats.append(
            {
                "beat_id": f"fallback-{index + 1}",
                "sentence": sentence,
                "summary": _fallback_summary(event),
                "event_ids": [event["event_id"]],
                "symbols": [event["symbol"]],
            }
        )
    return {
        "headline": "Past Day Attention Digest",
        "dek": "Narrative fallback built directly from the latest anomaly cards.",
        "beats": beats,
        "mode": "fallback",
        "generated_at_utc": generated_at_utc,
        "model": "deterministic",
        "input_hash": _stable_hash(events),
    }


def _unique_texts(values: list[object], *, limit: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _coerce_text(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if limit is not None and len(out) >= int(limit):
            break
    return out


def homepage_v2_bundle_symbol_lookup(beats: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    lookup: dict[str, list[str]] = {}
    for beat in beats or []:
        if not isinstance(beat, dict):
            continue
        bundle_id = _coerce_text(beat.get("bundle_id"))
        if not bundle_id:
            continue
        existing = lookup.setdefault(bundle_id, [])
        raw_symbols = [
            _coerce_text(symbol).upper()
            for symbol in list(beat.get("symbols") or [])
            if _coerce_text(symbol)
        ]
        if not raw_symbols:
            continue
        seen = {symbol.lower() for symbol in existing}
        for symbol in raw_symbols:
            if symbol.lower() in seen:
                continue
            existing.append(symbol)
            seen.add(symbol.lower())
    return lookup


def normalize_homepage_v2_detail_state(
    beats: list[dict[str, Any]] | None,
    *,
    selected_bundle_id: str = "",
    selected_ticker: str = "",
    active_panel: str = HOMEPAGE_V2_RESEARCH_PANEL,
) -> dict[str, str]:
    bundle_symbol_lookup = homepage_v2_bundle_symbol_lookup(beats)
    valid_bundle_ids = list(bundle_symbol_lookup.keys())

    normalized_bundle_id = _coerce_text(selected_bundle_id)
    if normalized_bundle_id not in bundle_symbol_lookup:
        normalized_bundle_id = valid_bundle_ids[0] if valid_bundle_ids else ""

    normalized_ticker = _coerce_text(selected_ticker).upper()
    if not normalized_ticker and normalized_bundle_id:
        bundle_symbols = bundle_symbol_lookup.get(normalized_bundle_id, [])
        normalized_ticker = bundle_symbols[0] if bundle_symbols else ""

    normalized_panel = _coerce_text(active_panel).lower()
    if normalized_panel not in HOMEPAGE_V2_DETAIL_PANELS:
        normalized_panel = HOMEPAGE_V2_RESEARCH_PANEL if normalized_bundle_id else HOMEPAGE_V2_COMPANY_PANEL

    if normalized_panel == HOMEPAGE_V2_RESEARCH_PANEL and not normalized_bundle_id:
        normalized_panel = HOMEPAGE_V2_COMPANY_PANEL if normalized_ticker else HOMEPAGE_V2_RESEARCH_PANEL
    if normalized_panel == HOMEPAGE_V2_COMPANY_PANEL and not normalized_ticker:
        normalized_panel = HOMEPAGE_V2_RESEARCH_PANEL

    return {
        "selected_bundle_id": normalized_bundle_id,
        "selected_ticker": normalized_ticker,
        "active_panel": normalized_panel,
    }


def build_homepage_v2_market_digest(
    market_events: pd.DataFrame | list[dict[str, Any]] | None,
    *,
    asof_time_utc: datetime | str | None = None,
) -> dict[str, Any]:
    generated_at_utc = _coerce_timestamp(asof_time_utc or datetime.now(timezone.utc))
    if isinstance(market_events, pd.DataFrame):
        records = market_events.to_dict(orient="records")
    else:
        records = [item for item in list(market_events or []) if isinstance(item, dict)]

    beats: list[dict[str, Any]] = []
    for index, raw in enumerate(records):
        title = _coerce_text(raw.get("event_title") or raw.get("title"))
        if not title:
            continue
        supporting_event_ids = [
            _coerce_text(item)
            for item in list(raw.get("supporting_event_ids") or [])
            if _coerce_text(item)
        ]
        event_ids = supporting_event_ids or ([_coerce_text(raw.get("event_id"))] if _coerce_text(raw.get("event_id")) else [])
        if not event_ids:
            continue
        symbols = _unique_texts(
            list(raw.get("supporting_symbols") or []) + [_coerce_text(raw.get("anchor_symbol")).upper()],
            limit=8,
        )
        summary_parts = _unique_texts(
            [
                raw.get("what_happened_text"),
                raw.get("why_happened_text"),
                raw.get("affected_assets_summary_text"),
                raw.get("headline_text"),
            ],
            limit=4,
        )
        summary = _trim(" ".join(summary_parts), 520)
        beats.append(
            {
                "beat_id": f"market-event-{index + 1}",
                "sentence": title,
                "summary": summary or title,
                "event_ids": event_ids,
                "symbols": symbols,
            }
        )

    if not beats:
        return {
            "headline": "Top Market Events Today",
            "dek": "No clustered market events were available for the latest snapshot.",
            "beats": [],
            "mode": "market_events",
            "generated_at_utc": generated_at_utc,
            "model": "deterministic",
            "input_hash": _stable_hash({"market_events": []}),
        }

    top_title = _coerce_text(records[0].get("event_title") or records[0].get("title"))
    dek_parts = _unique_texts(
        [
            records[0].get("what_happened_text"),
            records[0].get("why_happened_text"),
            records[0].get("affected_assets_summary_text"),
        ],
        limit=3,
    )
    return {
        "headline": top_title or "Top Market Events Today",
        "dek": _trim(
            " ".join(dek_parts) or "Cross-asset thread built from clustered market events instead of isolated symbol anomalies.",
            420,
        ),
        "beats": beats,
        "mode": "market_events",
        "generated_at_utc": generated_at_utc,
        "model": "deterministic",
        "input_hash": _stable_hash(records),
    }


def build_homepage_v2_digest(
    event_records: list[dict[str, Any]] | None,
    llm_client: OpenAIChatJSONClient | None,
    *,
    asof_time_utc: datetime | str | None = None,
    max_sentences: int = 12,
) -> dict[str, Any]:
    events = _prepare_events(event_records)
    generated_at_utc = _coerce_timestamp(asof_time_utc or datetime.now(timezone.utc))
    if not events:
        return {
            "headline": "Past Day Attention Digest",
            "dek": "No anomaly events were available for the latest daily narrative.",
            "beats": [],
            "mode": "empty",
            "generated_at_utc": generated_at_utc,
            "model": "deterministic",
            "input_hash": _stable_hash({"events": []}),
        }
    if llm_client is None:
        return _fallback_digest(events, generated_at_utc=generated_at_utc)

    sentence_target = max(10, min(int(max_sentences or 12), 20))
    prompt_payload = {
        "generated_at_utc": generated_at_utc,
        "sentence_target": sentence_target,
        "events": events[:20],
    }
    input_hash = _stable_hash(prompt_payload)
    event_lookup = {str(event["event_id"]): event for event in events}

    data = llm_client.generate_json(
        system_prompt=(
            "You are writing a financial newsroom homepage lead. "
            "Use only the supplied anomaly events and supporting summaries. "
            "Write crisp, factual, highly readable sentences that explain what happened over the past day. "
            "Each beat must map to one or more supplied event_ids. Do not invent companies, causes, or links. "
            "Never narrate observed-versus-expected percentages, residuals, or z-scores in prose."
        ),
        user_prompt=(
            "Create a Homepage - v2 narrative thread from these anomaly events.\n"
            f"- Target between 10 and 20 beats when there is enough coverage; otherwise use the strongest distinct themes only.\n"
            f"- Each beat sentence should read like a sharp feed post, not a bullet label.\n"
            f"- Each summary should be 2-3 sentences and explain why the linked anomaly events matter now for a reader with no prior context.\n"
            f"- Lead with the market dynamic, the reinforcing headline, what the company does, and any plain-English explainer that matters.\n"
            f"{json.dumps(prompt_payload, ensure_ascii=False, default=str, indent=2)}"
        ),
        schema_name="homepage_v2_digest",
        schema=HOMEPAGE_V2_SCHEMA,
    )

    beats: list[dict[str, Any]] = []
    for index, raw in enumerate(list(data.get("beats") or [])):
        if not isinstance(raw, dict):
            continue
        event_ids = [
            _coerce_text(item)
            for item in list(raw.get("event_ids") or [])
            if _coerce_text(item) in event_lookup
        ]
        if not event_ids:
            continue
        symbols = [
            _coerce_text(item).upper()
            for item in list(raw.get("symbols") or [])
            if _coerce_text(item)
        ]
        if not symbols:
            symbols = sorted({event_lookup[event_id]["symbol"] for event_id in event_ids})
        sentence = _coerce_text(raw.get("sentence"))
        summary = _coerce_text(raw.get("summary"))
        if not sentence or not summary:
            continue
        beats.append(
            {
                "beat_id": _coerce_text(raw.get("beat_id")) or f"beat-{index + 1}",
                "sentence": sentence,
                "summary": summary,
                "event_ids": event_ids,
                "symbols": symbols,
            }
        )

    if not beats:
        return _fallback_digest(events, generated_at_utc=generated_at_utc)

    return {
        "headline": _coerce_text(data.get("headline")) or "Past Day Attention Digest",
        "dek": _coerce_text(data.get("dek")) or "Narrative summary of the latest anomaly layer.",
        "beats": beats,
        "mode": "llm",
        "generated_at_utc": generated_at_utc,
        "model": _coerce_text(getattr(getattr(llm_client, "config", None), "model", "")) or "llm",
        "input_hash": input_hash,
    }

__all__ = [
    "HOMEPAGE_V2_COMPANY_PANEL",
    "HOMEPAGE_V2_DETAIL_PANELS",
    "HOMEPAGE_V2_RESEARCH_PANEL",
    "HOMEPAGE_V2_SCHEMA",
    "build_homepage_v2_digest",
    "build_homepage_v2_market_digest",
    "homepage_v2_bundle_symbol_lookup",
    "normalize_homepage_v2_detail_state",
]
