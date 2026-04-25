from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

import pandas as pd

from .aql._shared import (
    _coerce_text,
    _has_causal_language,
    _looks_like_stat_dump,
    _text_overlap,
)


def _looks_like_numeric_market_activity_sentence(sentence: object) -> bool:
    return _looks_like_stat_dump(sentence) and not _has_causal_language(sentence)


def clean_attention_text(text: object) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    kept = [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
        and not re.search(
            r"\b(observed|expected|residual|zscore|z-score|attention score|20-day baseline)\b|"
            r"\bz away from expectation\b|"
            r"\bversus an expected\b|"
            r"\bleaving a residual\b",
            sentence.lower(),
        )
        and not _looks_like_numeric_market_activity_sentence(sentence)
    ]
    trimmed = " ".join(kept[:2]).strip()
    return trimmed or clean


def has_causal_language(text: object) -> bool:
    return _has_causal_language(text)


def looks_like_stat_dump_text(text: object) -> bool:
    return _looks_like_stat_dump(text)


def _is_repetitive_text(text: object) -> bool:
    clean = _coerce_text(text)
    if not clean:
        return False
    sentences = [_coerce_text(item) for item in re.split(r"(?<=[.!?])\s+", clean) if _coerce_text(item)]
    normalized = [re.sub(r"[^a-z0-9]+", " ", sentence.lower()).strip() for sentence in sentences]
    return len(normalized) >= 2 and len(set(normalized)) < len(normalized)


def looks_like_low_quality_surface_summary(text: object) -> bool:
    raw_clean = " ".join(str(text or "").split())
    clean = raw_clean.lower()
    if not clean:
        return False
    if "..." in raw_clean or "…" in raw_clean:
        return True
    patterns = [
        r"\bmarket activity reads this as\b",
        r"\bmarket is treating this as\b",
        r"\b20-day baseline\b",
        r"\bz away from expectation\b",
        r"\bleaving a residual\b",
    ]
    if any(re.search(pattern, clean) for pattern in patterns):
        return True
    if _is_repetitive_text(clean):
        return True
    if looks_like_stat_dump_text(clean) and not has_causal_language(clean):
        return True
    return False


def _surface_what_changed_text(text: object) -> str:
    clean = clean_attention_text(text)
    if not clean:
        return ""
    pattern = re.compile(
        r"^(?P<symbol>[A-Z0-9.\-]+)\s+(?P<direction>rose|fell)\s+(?P<move>\d+(?:\.\d+)?)% today"
        r"(?: versus a [+\-]?\d+(?:\.\d+)?% 20-day baseline)?"
        r"(?: \(\d+(?:\.\d+)?z away from expectation\))?\.?$",
        re.IGNORECASE,
    )
    match = pattern.match(clean)
    if not match:
        return clean
    symbol = match.group("symbol").upper()
    direction = match.group("direction").lower()
    move = pd.to_numeric(match.group("move"), errors="coerce")
    if pd.notna(move) and float(move) >= 8.0:
        intensity = "sharply"
    elif pd.notna(move) and float(move) >= 3.0:
        intensity = "meaningfully"
    else:
        intensity = "modestly"
    return f"{symbol} {direction} {intensity} today relative to its recent baseline."


def looks_like_model_math_explanation(text: object) -> bool:
    clean = " ".join(str(text or "").split()).lower()
    if not clean:
        return False
    patterns = [
        r"\bobserved\b",
        r"\bexpected\b",
        r"\bresidual\b",
        r"\bzscore\b",
        r"\bz-score\b",
        r"\b20-day baseline\b",
        r"\bversus an expected\b",
        r"\bleaving a residual\b",
    ]
    return any(re.search(pattern, clean) for pattern in patterns)


def attention_home_bundle_preview(
    item: dict[str, object],
    bundle: dict[str, object] | None = None,
) -> dict[str, str]:
    item = item if isinstance(item, dict) else {}
    research_bundle = bundle if isinstance(bundle, dict) else {}

    stored_summary = _coerce_text(item.get("surface_summary_text"))
    stored_what_changed = _coerce_text(item.get("surface_what_changed_text"))
    stored_why = _coerce_text(item.get("surface_why_text"))
    stored_what_else = _coerce_text(item.get("surface_what_else_moved_text"))
    stored_cause_status = _coerce_text(item.get("surface_cause_status") or item.get("cause_status")).lower() or "unresolved"
    stored_evidence_quality = _coerce_text(item.get("surface_evidence_quality"))
    stored_freshness_quality = _coerce_text(item.get("surface_freshness_quality"))
    stored_source_summary = _coerce_text(item.get("surface_source_summary") or item.get("top_source"))
    stored_confidence = _coerce_text(item.get("surface_confidence_label") or item.get("confidence_label"))

    if not research_bundle and (stored_summary or stored_what_changed or stored_why or stored_what_else):
        what_changed_text = _surface_what_changed_text(stored_what_changed or item.get("what_changed_text"))
        why_text = clean_attention_text(stored_why or item.get("why_now_text") or item.get("why_happened_text"))
        what_else_moved_text = clean_attention_text(stored_what_else or item.get("what_else_moved_text") or item.get("affected_assets_summary_text"))
        if _text_overlap(what_changed_text, what_else_moved_text) >= 0.62 and looks_like_stat_dump_text(what_else_moved_text):
            what_else_moved_text = ""
        return {
            "what_changed_text": what_changed_text,
            "why_text": why_text,
            "what_else_moved_text": what_else_moved_text,
            "cause_status": stored_cause_status,
            "evidence_quality": stored_evidence_quality,
            "freshness_quality": stored_freshness_quality,
            "source_summary": stored_source_summary,
            "confidence_label": stored_confidence,
            "surface_summary_text": stored_summary,
        }

    bundle_type = _coerce_text(research_bundle.get("bundle_type")).lower()
    is_event = bundle_type == "event" or bool(_coerce_text(item.get("event_title")))
    cause_status = _coerce_text(research_bundle.get("cause_status") or item.get("cause_status")).lower() or "unresolved"

    if is_event:
        what_changed_text = clean_attention_text(research_bundle.get("what_happened_text") or item.get("what_happened_text"))
        why_text = clean_attention_text(research_bundle.get("why_happened_text") or item.get("why_happened_text"))
        if not why_text:
            why_text = "Cause remains unresolved; the move is real but the retained evidence is still thin or conflicting."
        what_else_moved_text = clean_attention_text(research_bundle.get("affected_assets_summary_text") or item.get("affected_assets_summary_text"))
    else:
        what_changed_text = _surface_what_changed_text(research_bundle.get("what_changed_text") or item.get("what_changed_text"))
        bundle_why_text = clean_attention_text(research_bundle.get("why_now_text"))
        item_why_text = clean_attention_text(item.get("why_now_text"))
        why_text = ""
        for candidate in [bundle_why_text, item_why_text]:
            if candidate and not looks_like_model_math_explanation(candidate):
                why_text = candidate
                break
        if not why_text:
            if cause_status == "continuation":
                why_text = "No clear new company-specific catalyst was confirmed today. The move appears to be extending an earlier narrative."
            elif cause_status == "conflicting":
                why_text = "Coverage remains conflicting, and no single cause is clearly dominant yet."
            else:
                why_text = "Cause remains unresolved; the move is large enough to flag, but the retained evidence is not strong enough yet."
        what_else_moved_text = clean_attention_text(research_bundle.get("what_else_moved_text") or item.get("what_else_moved_text"))
    if _text_overlap(what_changed_text, what_else_moved_text) >= 0.62 and looks_like_stat_dump_text(what_else_moved_text):
        what_else_moved_text = ""

    return {
        "what_changed_text": what_changed_text,
        "why_text": why_text,
        "what_else_moved_text": what_else_moved_text,
        "cause_status": cause_status,
        "evidence_quality": _coerce_text(research_bundle.get("evidence_quality") or stored_evidence_quality),
        "freshness_quality": _coerce_text(research_bundle.get("freshness_quality") or stored_freshness_quality),
        "source_summary": _coerce_text(research_bundle.get("source_summary") or stored_source_summary),
        "confidence_label": _coerce_text(research_bundle.get("confidence_label") or stored_confidence),
        "surface_summary_text": stored_summary,
    }


def attention_home_surface_summary(
    preview: dict[str, str],
    *,
    is_event: bool,
) -> str:
    if _coerce_text(preview.get("surface_summary_text")) and not looks_like_low_quality_surface_summary(preview.get("surface_summary_text")):
        return _coerce_text(preview.get("surface_summary_text"))

    parts: list[str] = []
    what_changed_text = clean_attention_text(preview.get("what_changed_text"))
    why_text = clean_attention_text(preview.get("why_text"))
    what_else_moved_text = clean_attention_text(preview.get("what_else_moved_text"))
    cause_status = _coerce_text(preview.get("cause_status")).lower()

    if looks_like_stat_dump_text(why_text) and not has_causal_language(why_text):
        why_text = ""

    if what_changed_text:
        parts.append(what_changed_text)
    if why_text:
        parts.append(why_text)
    if is_event and what_else_moved_text:
        candidate = " ".join(parts + [what_else_moved_text]).strip()
        if len(candidate) <= 360 and _text_overlap(" ".join(parts), what_else_moved_text) < 0.62:
            parts.append(what_else_moved_text)

    summary = " ".join(part for part in parts if part).strip()
    return summary


def hydrate_home_item_with_bundle(
    item: dict[str, object],
    bundle: dict[str, object] | None = None,
) -> dict[str, object]:
    hydrated = dict(item)
    preview = attention_home_bundle_preview(hydrated, bundle)
    is_event = bool(_coerce_text(hydrated.get("event_title")))
    hydrated["surface_what_changed_text"] = preview.get("what_changed_text") or ""
    hydrated["surface_why_text"] = preview.get("why_text") or ""
    hydrated["surface_what_else_moved_text"] = preview.get("what_else_moved_text") or ""
    hydrated["surface_cause_status"] = preview.get("cause_status") or ""
    hydrated["surface_evidence_quality"] = preview.get("evidence_quality") or ""
    hydrated["surface_freshness_quality"] = preview.get("freshness_quality") or ""
    hydrated["surface_source_summary"] = preview.get("source_summary") or ""
    hydrated["surface_confidence_label"] = preview.get("confidence_label") or ""
    hydrated["surface_summary_text"] = attention_home_surface_summary(preview, is_event=is_event)
    return hydrated


def hydrate_attention_home_payload(
    payload: dict[str, Any],
    bundle_map: dict[str, dict[str, object]],
) -> dict[str, Any]:
    hydrated = deepcopy(payload or {})
    sections = ["top_events", "must_read_movers", "unresolved_large_moves"]
    for section in sections:
        items = []
        for item in list(hydrated.get(section) or []):
            bundle_id = _coerce_text((item or {}).get("bundle_id"))
            bundle = bundle_map.get(bundle_id, {}) if bundle_id else {}
            items.append(hydrate_home_item_with_bundle(dict(item or {}), bundle))
        hydrated[section] = items
    return hydrated


def rebalance_attention_home_payload(payload: dict[str, Any]) -> dict[str, Any]:
    hydrated = deepcopy(payload or {})
    original_must_read = list(hydrated.get("must_read_movers") or [])
    original_unresolved = list(hydrated.get("unresolved_large_moves") or [])

    def _symbol_key(item: dict[str, object]) -> str:
        return _coerce_text(item.get("symbol")).upper()

    must_read: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []

    for item in original_must_read + original_unresolved:
        cause_status = _coerce_text(item.get("surface_cause_status") or item.get("cause_status")).lower()
        evidence_quality = _coerce_text(item.get("surface_evidence_quality"))
        freshness_quality = _coerce_text(item.get("surface_freshness_quality"))
        same_day_evidence_count = int(float(item.get("same_day_evidence_count") or 0))
        qualifies_for_must_read = (
            cause_status in {"supported", "developing"}
            and (
                same_day_evidence_count > 0
                or freshness_quality in {"High", "Medium"}
                or evidence_quality == "High"
            )
        )
        if qualifies_for_must_read:
            must_read.append(item)
        else:
            unresolved.append(item)

    def _dedupe(items: list[dict[str, object]]) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in items:
            key = _symbol_key(item) or _coerce_text(item.get("bundle_id"))
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    def _sort_movers(items: list[dict[str, object]], *, unresolved_bucket: bool) -> list[dict[str, object]]:
        return sorted(
            _dedupe(items),
            key=lambda item: (
                -int(float(item.get("same_day_evidence_count") or 0)) if not unresolved_bucket else 0,
                -float(item.get("candidate_score") or 0.0),
                -abs(float(item.get("change_pct") or 0.0)),
                _symbol_key(item),
            ),
        )

    hydrated["must_read_movers"] = _sort_movers(must_read, unresolved_bucket=False)
    hydrated["unresolved_large_moves"] = _sort_movers(unresolved, unresolved_bucket=True)
    coverage = dict(hydrated.get("coverage_summary") or {})
    coverage["must_read_count"] = len(hydrated["must_read_movers"])
    coverage["unresolved_count"] = len(hydrated["unresolved_large_moves"])
    hydrated["coverage_summary"] = coverage
    return hydrated


__all__ = [
    "attention_home_bundle_preview",
    "attention_home_surface_summary",
    "clean_attention_text",
    "hydrate_attention_home_payload",
    "hydrate_home_item_with_bundle",
    "looks_like_model_math_explanation",
    "rebalance_attention_home_payload",
]
