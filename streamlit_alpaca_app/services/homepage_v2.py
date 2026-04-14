from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

import pandas as pd


HOMEPAGE_V2_RESEARCH_PANEL = "research"
HOMEPAGE_V2_COMPANY_PANEL = "company"
HOMEPAGE_V2_DETAIL_PANELS = (
    HOMEPAGE_V2_RESEARCH_PANEL,
    HOMEPAGE_V2_COMPANY_PANEL,
)
HOMEPAGE_V2_EDITORIAL_LINKS: tuple[dict[str, str], ...] = (
    {
        "link_id": "torres-capital-substack",
        "placement": "sidebar_brand",
        "label": "Torres Capital Substack",
        "button_label": "Read on Substack",
        "icon_name": "substack",
        "url": "https://substack.com/@torrescap",
    },
)


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


def homepage_v2_editorial_links(*, placement: str = "") -> list[dict[str, str]]:
    normalized_placement = _coerce_text(placement).lower()
    links: list[dict[str, str]] = []
    seen: set[str] = set()

    for raw in HOMEPAGE_V2_EDITORIAL_LINKS:
        link_id = _coerce_text(raw.get("link_id")).lower()
        link_placement = _coerce_text(raw.get("placement")).lower()
        label = _coerce_text(raw.get("label"))
        url = _coerce_text(raw.get("url"))
        if not label or not url:
            continue
        if normalized_placement and link_placement != normalized_placement:
            continue

        dedupe_key = link_id or url.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        links.append(
            {
                "link_id": link_id,
                "placement": link_placement,
                "label": label,
                "button_label": _coerce_text(raw.get("button_label")) or label,
                "icon_name": _coerce_text(raw.get("icon_name")),
                "url": url,
            }
        )

    return links


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


__all__ = [
    "HOMEPAGE_V2_COMPANY_PANEL",
    "HOMEPAGE_V2_DETAIL_PANELS",
    "HOMEPAGE_V2_RESEARCH_PANEL",
    "build_homepage_v2_market_digest",
    "homepage_v2_bundle_symbol_lookup",
    "normalize_homepage_v2_detail_state",
]
