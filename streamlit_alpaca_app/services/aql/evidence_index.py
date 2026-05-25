"""
AQL evidence index helpers.

Cheap, deterministic metadata extraction for source documents and chunks so
materialized research evidence can be filtered and searched without an extra
vector store.
"""
from __future__ import annotations

from functools import lru_cache
import json
import re
from typing import Any

import pandas as pd

from ..llm import LLMAPIError
from ..market import COMMODITY_PROXY_METADATA, default_universe_symbols
from ..saa import build_canonical_document_fields
from ._shared import _coerce_text, _json_dumps, _normalize_symbol


_DATE_LIMIT = 8
_MONTH_PATTERN = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})\b", re.IGNORECASE),
    re.compile(rf"\b{_MONTH_PATTERN}\s+\d{{1,2}}(?:,\s*\d{{4}})?\b", re.IGNORECASE),
)
_UPPER_TOKEN_PATTERN = re.compile(r"\$?([A-Z]{2,5})\b")
_COMMON_NON_TICKERS = {
    "AI",
    "CEO",
    "CFO",
    "COO",
    "CPI",
    "EPS",
    "ETF",
    "ETN",
    "FDA",
    "FOMC",
    "GDP",
    "IPO",
    "IRS",
    "LLM",
    "NYSE",
    "OPEC",
    "PPI",
    "SEC",
    "USA",
    "USD",
}
_KNOWN_EVENT_TAGS: frozenset[str] = frozenset({
    "earnings", "guidance", "m_and_a", "partnership", "contract",
    "approval", "clinical_trial", "sec_filing", "analyst_rating",
    "buyback_dividend", "financing", "litigation", "restructuring",
    "product_launch", "ai_datacenter", "supply_chain", "geopolitics",
    "rates", "inflation", "jobs",
})
_KNOWN_COMMODITY_TAGS: frozenset[str] = frozenset({
    "oil", "natural_gas", "gold", "silver", "platinum", "palladium",
    "copper", "base_metals", "rare_earths", "lithium", "uranium",
    "agriculture", "corn", "wheat", "soybeans", "coffee", "sugar",
    "cocoa", "cotton", "shipping",
})
_TAG_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "event_tags": {"type": "array", "items": {"type": "string"}},
        "commodity_tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["event_tags", "commodity_tags"],
}
_SOURCE_KIND_EVENT_TAGS: dict[str, tuple[str, ...]] = {
    "sec": ("sec_filing",),
    "fred": ("macro_data",),
    "treasury": ("rates",),
    "context": ("summary_context",),
}


def _normalized_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _coerce_text(value).lower()).strip()


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _tag_key(values: list[str]) -> str:
    items = _dedupe_preserve_order(values)
    if not items:
        return ""
    return "|" + "|".join(items) + "|"


def _json_key_fields(values: list[str]) -> dict[str, str]:
    clean = _dedupe_preserve_order(values)
    return {
        "json": _json_dumps(clean),
        "key": _tag_key(clean),
    }


@lru_cache(maxsize=1)
def _known_symbols() -> set[str]:
    symbols = {symbol for symbol in default_universe_symbols() if symbol}
    symbols.update(COMMODITY_PROXY_METADATA.keys())
    return {str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()}


def _published_date_text(value: object) -> str:
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return ""
    return ts.strftime("%Y-%m-%d")


def _parse_date_token(token: str, *, asof_time_utc: pd.Timestamp) -> str:
    clean = _coerce_text(token)
    if not clean:
        return ""
    parsed = pd.to_datetime(clean, utc=True, errors="coerce")
    if pd.isna(parsed) and re.search(rf"^{_MONTH_PATTERN}\s+\d{{1,2}}$", clean, re.IGNORECASE):
        parsed = pd.to_datetime(f"{clean}, {asof_time_utc.year}", utc=True, errors="coerce")
        if pd.notna(parsed) and parsed > (asof_time_utc + pd.Timedelta(days=45)):
            parsed = parsed - pd.DateOffset(years=1)
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d")


def _extract_mentioned_dates(text: object, *, asof_time_utc: pd.Timestamp) -> list[str]:
    clean = _coerce_text(text)
    if not clean:
        return []
    out: list[str] = []
    lowered = clean.lower()
    today = asof_time_utc.strftime("%Y-%m-%d")
    if any(token in lowered for token in (" today ", " today's ", " this morning ", " this afternoon ", " tonight ")):
        out.append(today)
    if "yesterday" in lowered:
        out.append((asof_time_utc - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    if "tomorrow" in lowered:
        out.append((asof_time_utc + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    for pattern in _DATE_PATTERNS:
        for match in pattern.findall(clean):
            parsed = _parse_date_token(match, asof_time_utc=asof_time_utc)
            if parsed:
                out.append(parsed)
            if len(out) >= _DATE_LIMIT:
                return _dedupe_preserve_order(out)[:_DATE_LIMIT]
    return _dedupe_preserve_order(out)[:_DATE_LIMIT]


def _extract_tickers(
    text: object,
    *,
    bundle_subject: object = "",
) -> list[str]:
    clean = _coerce_text(text)
    if not clean:
        return []
    known = _known_symbols()
    anchor = _normalize_symbol(bundle_subject)
    out: list[str] = [anchor] if anchor else []
    for token in _UPPER_TOKEN_PATTERN.findall(clean):
        symbol = str(token or "").upper().strip()
        if not symbol or symbol in _COMMON_NON_TICKERS:
            continue
        if symbol == anchor or symbol in known:
            out.append(symbol)
    return _dedupe_preserve_order(out)


def _commodity_tags_from_tickers(tickers: list[str]) -> list[str]:
    tags: list[str] = []
    for ticker in tickers:
        meta = COMMODITY_PROXY_METADATA.get(str(ticker or "").upper().strip()) or {}
        commodity = _normalized_text(meta.get("commodity"))
        if "brent" in commodity or "wti" in commodity or "oil" in commodity or "gasoline" in commodity:
            tags.append("oil")
        elif "natural gas" in commodity:
            tags.append("natural_gas")
        elif "gold" in commodity:
            tags.append("gold")
        elif "silver" in commodity:
            tags.append("silver")
        elif "platinum" in commodity:
            tags.append("platinum")
        elif "palladium" in commodity:
            tags.append("palladium")
        elif "copper" in commodity:
            tags.append("copper")
        elif "base metals" in commodity:
            tags.append("base_metals")
        elif "rare earth" in commodity:
            tags.append("rare_earths")
        elif "lithium" in commodity:
            tags.append("lithium")
        elif "uranium" in commodity:
            tags.append("uranium")
        elif "agriculture" in commodity:
            tags.append("agriculture")
        elif "corn" in commodity:
            tags.append("corn")
        elif "wheat" in commodity:
            tags.append("wheat")
        elif "soybean" in commodity:
            tags.append("soybeans")
        elif "coffee" in commodity:
            tags.append("coffee")
        elif "sugar" in commodity:
            tags.append("sugar")
        elif "cocoa" in commodity:
            tags.append("cocoa")
        elif "cotton" in commodity:
            tags.append("cotton")
        elif "shipping" in commodity or "dry bulk" in commodity:
            tags.append("shipping")
    return tags


def _llm_extract_tags(
    text: str,
    *,
    tickers: list[str],
    source_kind: str,
    llm_client: Any,
) -> tuple[list[str], list[str]]:
    commodity_tags = _commodity_tags_from_tickers(tickers)
    source_event_tags = list(_SOURCE_KIND_EVENT_TAGS.get(source_kind.lower(), ()))
    if llm_client is None:
        return source_event_tags, commodity_tags
    excerpt = text[:1200]
    try:
        result = llm_client.generate_json(
            system_prompt=(
                "Extract financial event and commodity tags from news text. "
                f"Valid event tags: {', '.join(sorted(_KNOWN_EVENT_TAGS))}. "
                f"Valid commodity tags: {', '.join(sorted(_KNOWN_COMMODITY_TAGS))}. "
                "Return only tags clearly present in the text. Return empty arrays if nothing applies."
            ),
            user_prompt=f"Text: {excerpt}",
            schema_name="tag_extraction",
            schema=_TAG_EXTRACTION_SCHEMA,
        )
    except (LLMAPIError, Exception):
        return source_event_tags, commodity_tags
    raw_event_tags = [str(t).strip() for t in result.get("event_tags") or []]
    raw_commodity_tags = [str(t).strip() for t in result.get("commodity_tags") or []]
    event_tags = [t for t in raw_event_tags if t in _KNOWN_EVENT_TAGS] + source_event_tags
    extra_commodity_tags = [t for t in raw_commodity_tags if t in _KNOWN_COMMODITY_TAGS]
    commodity_tags.extend(extra_commodity_tags)
    return _dedupe_preserve_order(event_tags), _dedupe_preserve_order(commodity_tags)


def build_evidence_metadata(
    *,
    title: object,
    text: object,
    published_at: object,
    bundle_subject: object,
    source_kind: object,
    asof_time_utc: pd.Timestamp,
    llm_client: Any | None = None,
) -> dict[str, str]:
    search_text = " ".join(part for part in [_coerce_text(title), _coerce_text(text)] if part)
    mentioned_tickers = _extract_tickers(search_text, bundle_subject=bundle_subject)
    mentioned_dates = _extract_mentioned_dates(search_text, asof_time_utc=asof_time_utc)
    event_tags, mentioned_commodities = _llm_extract_tags(
        search_text,
        tickers=mentioned_tickers,
        source_kind=_coerce_text(source_kind).lower(),
        llm_client=llm_client,
    )
    published_date = _published_date_text(published_at)
    primary_date = published_date or (mentioned_dates[0] if mentioned_dates else "")
    ticker_fields = _json_key_fields(mentioned_tickers)
    commodity_fields = _json_key_fields(mentioned_commodities)
    event_fields = _json_key_fields(event_tags)
    date_fields = _json_key_fields(mentioned_dates)
    return {
        "published_date": published_date,
        "primary_date": primary_date,
        "mentioned_tickers_json": ticker_fields["json"],
        "mentioned_tickers_key": ticker_fields["key"],
        "mentioned_commodities_json": commodity_fields["json"],
        "mentioned_commodities_key": commodity_fields["key"],
        "event_tags_json": event_fields["json"],
        "event_tags_key": event_fields["key"],
        "mentioned_dates_json": date_fields["json"],
        "mentioned_dates_key": date_fields["key"],
    }


def annotate_source_documents(
    documents: list[dict[str, Any]],
    *,
    asof_time_utc: pd.Timestamp,
    llm_client: Any | None = None,
) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for row in list(documents or []):
        item = dict(row or {})
        item.update(
            build_evidence_metadata(
                title=item.get("title"),
                text=item.get("raw_text"),
                published_at=item.get("published_at"),
                bundle_subject=item.get("bundle_subject"),
                source_kind=item.get("source_kind"),
                asof_time_utc=asof_time_utc,
                llm_client=llm_client,
            )
        )
        item.update(build_canonical_document_fields(item))
        annotated.append(item)
    return annotated


def parse_json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _coerce_text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


__all__ = [
    "annotate_source_documents",
    "build_evidence_metadata",
    "parse_json_list",
]
