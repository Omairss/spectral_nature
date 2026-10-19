from __future__ import annotations

from .common.market_activity import _coerce_text


def clean_attention_text(text: object) -> str:
    """Normalize a narrative boundary without changing its reviewed content."""
    return "" if text is None else str(text).strip()


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
    stored_business_resolution = _coerce_text(item.get("business_resolution_text"))
    stored_business_context = _coerce_text(item.get("business_context_text") or item.get("surface_business_context_text"))

    if not research_bundle and (stored_summary or stored_what_changed or stored_why or stored_what_else):
        what_changed_text = clean_attention_text(stored_what_changed or item.get("what_changed_text"))
        why_text = clean_attention_text(stored_why or item.get("why_now_text") or item.get("why_happened_text"))
        what_else_moved_text = clean_attention_text(stored_what_else or item.get("what_else_moved_text") or item.get("affected_assets_summary_text"))
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
            "business_resolution_text": stored_business_resolution,
            "business_context_text": stored_business_context,
        }

    bundle_type = _coerce_text(research_bundle.get("bundle_type")).lower()
    is_event = bundle_type == "event" or bool(_coerce_text(item.get("event_title")))
    cause_status = _coerce_text(research_bundle.get("cause_status") or item.get("cause_status")).lower() or "unresolved"

    if is_event:
        what_changed_text = clean_attention_text(research_bundle.get("what_happened_text") or item.get("what_happened_text"))
        why_text = clean_attention_text(research_bundle.get("why_happened_text") or item.get("why_happened_text"))
        what_else_moved_text = clean_attention_text(research_bundle.get("affected_assets_summary_text") or item.get("affected_assets_summary_text"))
    else:
        what_changed_text = clean_attention_text(research_bundle.get("what_changed_text") or item.get("what_changed_text"))
        why_text = clean_attention_text(research_bundle.get("why_now_text") or item.get("why_now_text"))
        what_else_moved_text = clean_attention_text(research_bundle.get("what_else_moved_text") or item.get("what_else_moved_text"))

    return {
        "what_changed_text": what_changed_text,
        "why_text": why_text,
        "what_else_moved_text": what_else_moved_text,
        "cause_status": cause_status,
        "evidence_quality": _coerce_text(research_bundle.get("evidence_quality") or stored_evidence_quality),
        "freshness_quality": _coerce_text(research_bundle.get("freshness_quality") or stored_freshness_quality),
        "source_summary": _coerce_text(research_bundle.get("source_summary") or stored_source_summary),
        "confidence_label": _coerce_text(research_bundle.get("confidence_label") or stored_confidence),
        "surface_summary_text": clean_attention_text(research_bundle.get("surface_summary_text")) or stored_summary,
        "business_resolution_text": _coerce_text(
            research_bundle.get("business_resolution_text") or stored_business_resolution
        ),
        "business_context_text": _coerce_text(
            research_bundle.get("business_context_text")
            or research_bundle.get("surface_business_context_text")
            or stored_business_context
        ),
    }


def attention_home_surface_summary(
    preview: dict[str, str],
    *,
    is_event: bool,
) -> str:
    summary = clean_attention_text(preview.get("surface_summary_text"))
    if summary:
        return summary

    parts: list[str] = []
    what_changed_text = clean_attention_text(preview.get("what_changed_text"))
    why_text = clean_attention_text(preview.get("why_text"))
    what_else_moved_text = clean_attention_text(preview.get("what_else_moved_text"))
    business_resolution_text = clean_attention_text(preview.get("business_resolution_text"))

    if what_changed_text:
        parts.append(what_changed_text)
    if why_text:
        parts.append(why_text)
    elif business_resolution_text:
        parts.append(business_resolution_text)
    if is_event and what_else_moved_text:
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
    hydrated["surface_business_context_text"] = preview.get("business_context_text") or ""
    hydrated["surface_summary_text"] = attention_home_surface_summary(preview, is_event=is_event)
    return hydrated


__all__ = [
    "attention_home_bundle_preview",
    "attention_home_surface_summary",
    "clean_attention_text",
    "hydrate_home_item_with_bundle",
]
