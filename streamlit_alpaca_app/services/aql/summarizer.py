"""Read-only projections of materialized Home stories for shared consumers."""
from __future__ import annotations

from ..attention_surface import attention_home_bundle_preview, attention_home_surface_summary


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def attention_mover_card_title(mover: dict[str, object]) -> str:
    headline = _coerce_text((mover or {}).get("headline"))
    if headline:
        return headline
    symbol = _coerce_text((mover or {}).get("symbol")).upper()
    if symbol:
        return symbol
    return "Mover"


def build_attention_home_narrative_beats(home_payload: dict[str, object]) -> list[dict[str, object]]:
    """Project approved stories verbatim; retain compatibility with older snapshots."""
    beats: list[dict[str, object]] = []
    for section, kind in (("top_events", "event"), ("must_read_movers", "mover"), ("unresolved_large_moves", "unresolved")):
        for item in home_payload.get(section) or []:
            if not isinstance(item, dict) or not bool(item.get("publish", True)):
                continue
            reviewed = bool(item.get("public_surface_reviewed"))
            primary_symbol = _coerce_text(item.get("anchor_symbol") if kind == "event" else item.get("symbol")).upper()
            symbols = list(dict.fromkeys(
                ([primary_symbol] if primary_symbol else [])
                + [str(symbol).upper().strip() for symbol in item.get("supporting_symbols") or [] if str(symbol).strip()]
            ))
            if reviewed:
                sentence = _coerce_text(item.get("event_title"))
                summary = _coerce_text(item.get("surface_summary_text"))
                business_context = _coerce_text(item.get("business_context_text"))
                what_changed = _coerce_text(item.get("what_happened_text"))
                why = _coerce_text(item.get("why_happened_text"))
                watch_next = _coerce_text(item.get("watch_next_text"))
            else:
                preview = attention_home_bundle_preview(item, bundle={})
                summary = attention_home_surface_summary(preview, is_event=kind == "event")
                sentence = _coerce_text(item.get("event_title")) if kind == "event" else attention_mover_card_title(item)
                business_context = _coerce_text(item.get("business_resolution_text") or item.get("business_context_text") or item.get("surface_business_context_text"))
                what_changed = why = watch_next = ""
            beat = {
                "bundle_id": _coerce_text(item.get("bundle_id")),
                "sentence": sentence,
                "summary": summary,
                "what_changed": what_changed,
                "why": why,
                "business_context": business_context,
                "watch_next": watch_next,
                "symbols": symbols,
                "primary_symbol": primary_symbol or (symbols[0] if symbols else ""),
                "change_pct": item.get("change_pct"),
                "kind": kind,
            }
            beats.append(beat)
    return beats


build_market_stories = build_attention_home_narrative_beats

__all__ = [
    "attention_mover_card_title",
    "build_attention_home_narrative_beats",
    "build_market_stories",
]
