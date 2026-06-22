from __future__ import annotations

from typing import Any


HOMEPAGE_EDITORIAL_LINKS: tuple[dict[str, str], ...] = (
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


def homepage_editorial_links(*, placement: str = "") -> list[dict[str, str]]:
    normalized_placement = _coerce_text(placement).lower()
    links: list[dict[str, str]] = []
    seen: set[str] = set()

    for raw in HOMEPAGE_EDITORIAL_LINKS:
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


def homepage_bundle_symbol_lookup(stories: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    lookup: dict[str, list[str]] = {}
    for story in stories or []:
        if not isinstance(story, dict):
            continue
        bundle_id = _coerce_text(story.get("bundle_id"))
        if not bundle_id:
            continue
        existing = lookup.setdefault(bundle_id, [])
        raw_symbols = [
            _coerce_text(symbol).upper()
            for symbol in list(story.get("symbols") or [])
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


__all__ = [
    "homepage_bundle_symbol_lookup",
    "homepage_editorial_links",
]
