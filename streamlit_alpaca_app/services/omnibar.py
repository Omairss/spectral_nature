from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import uuid
from typing import Any

from data_access.layer import DataAccessLayer
from services.attention_home_summary import build_attention_home_narrative_beats


OMNIBAR_POLICY_VERSION = "streamlit-agentic-omnibar-v1"
OMNIBAR_MACRO_RELEASES: tuple[dict[str, object], ...] = (
    {
        "release_id": "cpi",
        "label": "CPI Release",
        "subtitle": "Inflation release context and price-level signals in Broad Economy.",
        "aliases": ("cpi", "consumer price index", "inflation release", "inflation print"),
    },
    {
        "release_id": "pce",
        "label": "PCE Release",
        "subtitle": "Fed-focused inflation context and personal consumption expenditures signals in Broad Economy.",
        "aliases": ("pce", "core pce", "personal consumption expenditures"),
    },
    {
        "release_id": "nfp",
        "label": "NFP Release",
        "subtitle": "Labor-market release context for payrolls and unemployment sensitivity in Broad Economy.",
        "aliases": ("nfp", "payrolls", "nonfarm payrolls", "jobs report"),
    },
    {
        "release_id": "fomc",
        "label": "FOMC Decision",
        "subtitle": "Policy path and rates context through Broad Economy.",
        "aliases": ("fomc", "fed", "fed meeting", "rate decision", "powell"),
    },
    {
        "release_id": "retail_sales",
        "label": "Retail Sales",
        "subtitle": "Consumer-demand release context in Broad Economy.",
        "aliases": ("retail sales", "consumer spending"),
    },
    {
        "release_id": "ism",
        "label": "ISM Survey",
        "subtitle": "Manufacturing and services diffusion context in Broad Economy.",
        "aliases": ("ism", "pmi", "manufacturing pmi", "services pmi"),
    },
)


@dataclass(frozen=True)
class OmnibarContext:
    home_payload: dict[str, Any]
    beats: list[dict[str, object]]
    symbol_catalog: dict[str, dict[str, object]]


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _trim_text(text: object, *, limit: int = 140) -> str:
    clean = _normalize_text(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _exact_ticker_candidate(query: str) -> str:
    normalized = _normalize_text(query).upper()
    if not normalized or " " in normalized:
        return ""
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,5}", normalized):
        return normalized
    return ""


def _match_score(query: str, candidates: list[str]) -> float:
    normalized_query = _normalize_text(query).lower()
    if not normalized_query:
        return 0.0
    text = " ".join(_normalize_text(candidate).lower() for candidate in candidates if _normalize_text(candidate))
    if not text:
        return 0.0
    if normalized_query == text:
        return 1.0
    if normalized_query in text:
        coverage = len(normalized_query) / max(len(text), len(normalized_query), 1)
        return min(0.95, 0.74 + coverage * 0.18)
    query_tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_query) if token]
    if not query_tokens:
        return 0.0
    hits = sum(1 for token in query_tokens if token in text)
    if hits <= 0:
        return 0.0
    coverage = hits / max(len(query_tokens), 1)
    return min(0.88, 0.34 + coverage * 0.42 + min(0.12, hits * 0.05))


_confidence_band_cache: dict[str, str] = {}


def _confidence_band(intent: str, top_score: float, *, query: str = "", top_label: str = "") -> str:
    if intent == "navigate" or top_score >= 0.92:
        return "high"
    if top_score < 0.5:
        return "low"
    cache_key = f"{intent}::{round(top_score, 2)}::{top_label[:40]}"
    if cache_key in _confidence_band_cache:
        return _confidence_band_cache[cache_key]
    query_terms = set(re.findall(r"[a-z0-9]+", _normalize_text(query).lower()))
    label_terms = set(re.findall(r"[a-z0-9]+", _normalize_text(top_label).lower()))
    overlap = len(query_terms & label_terms) / max(len(query_terms), 1) if query_terms else 0.0
    if top_score >= 0.82 or overlap >= 0.75:
        band = "high"
    elif top_score >= 0.68 or overlap >= 0.45:
        band = "medium"
    else:
        band = "low"
    _confidence_band_cache[cache_key] = band
    return band

def _load_symbol_name_map(
    layer: DataAccessLayer,
    symbols: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for symbol in sorted({str(value).upper().strip() for value in symbols if str(value).strip()}):
        try:
            payload = layer.resolve_asset_metadata(symbol, force_refresh=force_refresh).payload
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        name = _normalize_text(payload.get("name") or payload.get("company_name"))
        if name:
            out[symbol] = name
    return out


def _build_symbol_catalog(
    beats: list[dict[str, object]],
    symbol_name_map: dict[str, str],
) -> dict[str, dict[str, object]]:
    catalog: dict[str, dict[str, object]] = {}
    for beat in beats:
        bundle_id = _normalize_text(beat.get("bundle_id"))
        sentence = _normalize_text(beat.get("sentence"))
        summary = _normalize_text(beat.get("summary"))
        for raw_symbol in list(beat.get("symbols") or []):
            symbol = str(raw_symbol or "").upper().strip()
            if not symbol:
                continue
            entry = catalog.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "company_name": "",
                    "bundle_ids": [],
                    "beat_titles": [],
                    "summaries": [],
                },
            )
            if bundle_id and bundle_id not in entry["bundle_ids"]:
                entry["bundle_ids"].append(bundle_id)
            if sentence and sentence not in entry["beat_titles"]:
                entry["beat_titles"].append(sentence)
            if summary and summary not in entry["summaries"]:
                entry["summaries"].append(summary)
    for symbol, entry in catalog.items():
        entry["company_name"] = _normalize_text(symbol_name_map.get(symbol))
    return catalog


def build_omnibar_context(
    *,
    layer: DataAccessLayer | None = None,
    force_refresh: bool = False,
) -> OmnibarContext:
    resolved_layer = layer or DataAccessLayer.from_environment()
    try:
        home_payload = resolved_layer.resolve_attention_home_1d(force_refresh=force_refresh).payload
    except Exception:
        home_payload = {}
    if not isinstance(home_payload, dict):
        home_payload = {}
    beats = build_attention_home_narrative_beats(home_payload if isinstance(home_payload, dict) else {})
    tracked_symbols = sorted(
        {
            str(symbol).upper().strip()
            for beat in beats
            for symbol in list(beat.get("symbols") or [])
            if str(symbol).strip()
        }
    )
    symbol_name_map = _load_symbol_name_map(
        resolved_layer,
        tracked_symbols,
        force_refresh=force_refresh,
    ) if tracked_symbols else {}
    symbol_catalog = _build_symbol_catalog(beats, symbol_name_map)
    return OmnibarContext(
        home_payload=home_payload,
        beats=beats,
        symbol_catalog=symbol_catalog,
    )


def _build_results(
    *,
    layer: DataAccessLayer,
    query: str,
    beats: list[dict[str, object]],
    symbol_catalog: dict[str, dict[str, object]],
    force_refresh: bool = False,
) -> list[dict[str, object]]:
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return []

    results: list[dict[str, object]] = []
    exact_symbol = _exact_ticker_candidate(normalized_query)
    if exact_symbol:
        symbol_entry = dict(symbol_catalog.get(exact_symbol) or {})
        if not symbol_entry:
            fallback_name_map = _load_symbol_name_map(
                layer,
                [exact_symbol],
                force_refresh=force_refresh,
            )
            symbol_entry = {
                "symbol": exact_symbol,
                "company_name": _normalize_text(fallback_name_map.get(exact_symbol)),
                "bundle_ids": [],
                "beat_titles": [],
                "summaries": [],
            }
        company_name = _normalize_text(symbol_entry.get("company_name"))
        bundle_ids = list(symbol_entry.get("bundle_ids") or [])
        subtitle_parts: list[str] = []
        if company_name:
            subtitle_parts.append(company_name)
        subtitle_parts.append("linked to retained research" if bundle_ids else "open ticker workspace")
        results.append(
            {
                "kind": "symbol",
                "ref": exact_symbol,
                "label": exact_symbol,
                "subtitle": " | ".join(subtitle_parts),
                "score": 1.0,
                "symbol": exact_symbol,
                "company_name": company_name,
                "bundle_ids": bundle_ids,
            }
        )

    normalized_query_lower = normalized_query.lower()
    for release in OMNIBAR_MACRO_RELEASES:
        aliases = [str(item).lower().strip() for item in list(release.get("aliases") or []) if str(item).strip()]
        score = 0.0
        if normalized_query_lower in aliases:
            score = 0.99
        elif any(normalized_query_lower and normalized_query_lower in alias for alias in aliases):
            score = 0.84
        elif any(alias and alias in normalized_query_lower for alias in aliases):
            score = 0.8
        if score <= 0:
            continue
        results.append(
            {
                "kind": "macro_release",
                "ref": _normalize_text(release.get("release_id")),
                "label": _normalize_text(release.get("label")) or "Macro Release",
                "subtitle": _normalize_text(release.get("subtitle")),
                "score": score,
            }
        )

    for beat in beats:
        bundle_id = _normalize_text(beat.get("bundle_id"))
        sentence = _normalize_text(beat.get("sentence"))
        summary = _normalize_text(beat.get("summary"))
        score = 1.0 if bundle_id and normalized_query == bundle_id else _match_score(
            normalized_query,
            [sentence, summary, " ".join(list(beat.get("symbols") or [])), bundle_id],
        )
        if score < 0.46:
            continue
        results.append(
            {
                "kind": "bundle",
                "ref": bundle_id or sentence,
                "label": sentence or "Research bundle",
                "subtitle": _trim_text(summary or "Retained research bundle", limit=180),
                "score": min(score, 0.96 if bundle_id and normalized_query != bundle_id else score),
                "bundle_id": bundle_id,
                "symbols": [
                    str(item).upper().strip()
                    for item in list(beat.get("symbols") or [])
                    if str(item).strip()
                ],
            }
        )

    for symbol, entry in symbol_catalog.items():
        if exact_symbol and symbol == exact_symbol:
            continue
        company_name = _normalize_text(entry.get("company_name"))
        score = _match_score(
            normalized_query,
            [
                symbol,
                company_name,
                " ".join(list(entry.get("beat_titles") or [])),
                " ".join(list(entry.get("summaries") or [])),
            ],
        )
        if score < 0.52:
            continue
        subtitle_parts: list[str] = []
        if company_name:
            subtitle_parts.append(company_name)
        beat_titles = list(entry.get("beat_titles") or [])
        if beat_titles:
            subtitle_parts.append(_trim_text(beat_titles[0], limit=96))
        results.append(
            {
                "kind": "symbol",
                "ref": symbol,
                "label": symbol,
                "subtitle": " | ".join(subtitle_parts) if subtitle_parts else "Open ticker workspace",
                "score": min(score, 0.92),
                "symbol": symbol,
                "company_name": company_name,
                "bundle_ids": list(entry.get("bundle_ids") or []),
            }
        )

    deduped: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    ordered = sorted(
        results,
        key=lambda row: (
            -float(row.get("score") or 0.0),
            str(row.get("kind") or ""),
            str(row.get("label") or ""),
        ),
    )
    for item in ordered:
        dedupe_key = (str(item.get("kind") or ""), str(item.get("ref") or ""))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(
            {
                "result_id": f"sr_{len(deduped) + 1}",
                **item,
            }
        )
        if len(deduped) >= 6:
            break
    return deduped


def _extract_context_items(results: list[dict[str, object]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        kind = _normalize_text(result.get("kind"))
        if kind == "symbol":
            ref = _normalize_text(result.get("symbol") or result.get("ref"))
        elif kind == "bundle":
            ref = _normalize_text(result.get("bundle_id") or result.get("ref"))
        else:
            ref = _normalize_text(result.get("ref"))
        if not kind or not ref:
            continue
        dedupe_key = (kind, ref)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append(
            {
                "kind": kind,
                "ref": ref,
                "label": _normalize_text(result.get("label")) or ref,
            }
        )
        if len(items) >= 4:
            break
    return items


def resolve_omnibar(
    *,
    query: str,
    preferred_mode: str = "auto",
    force_refresh: bool = False,
    layer: DataAccessLayer | None = None,
) -> dict[str, object]:
    normalized_query = _normalize_text(query)
    normalized_mode = _normalize_text(preferred_mode).lower() or "auto"
    if normalized_mode not in {"auto", "search", "agent"}:
        normalized_mode = "auto"

    resolved_layer = layer or DataAccessLayer.from_environment()
    context = build_omnibar_context(layer=resolved_layer, force_refresh=force_refresh)
    search_results = _build_results(
        layer=resolved_layer,
        query=normalized_query,
        beats=context.beats,
        symbol_catalog=context.symbol_catalog,
        force_refresh=force_refresh,
    )

    top_score = float(search_results[0].get("score") or 0.0) if search_results else 0.0
    top_kind = _normalize_text(search_results[0].get("kind")) if search_results else ""
    if normalized_mode == "agent":
        intent = "agent"
    elif normalized_mode == "search":
        intent = "navigate" if top_kind in {"symbol", "macro_release"} and top_score >= 0.96 else "search"
    else:
        if top_kind in {"symbol", "macro_release"} and top_score >= 0.96:
            intent = "navigate"
        elif normalized_query:
            intent = "agent"
        else:
            intent = "ambiguous"

    request_id = f"omni_{uuid.uuid4().hex[:10]}"
    context_items = _extract_context_items(search_results)
    return {
        "request_id": request_id,
        "intent": intent,
        "policy_version": OMNIBAR_POLICY_VERSION,
        "confidence_band": _confidence_band(
            intent,
            top_score,
            query=normalized_query,
            top_label=_normalize_text(search_results[0].get("label")) if search_results else "",
        ),
        "confidence": round(top_score, 4),
        "query_echo": normalized_query,
        "preferred_mode": normalized_mode,
        "search_results": search_results,
        "context_items": context_items,
        "agent_action": {
            "suggested_message_blocks": [{"type": "text", "text": normalized_query}] if normalized_query else [],
            "create_session": intent == "agent",
            "context_items": context_items,
            "tool_transport": "mcp_json_rpc",
            "tool_surface": "query_service",
            "tool_endpoint": "/v1/agent/rpc",
        },
        "trace": {
            "resolver": "shared_backend",
            "match_hash": hashlib.sha1(f"{normalized_mode}::{normalized_query}".encode("utf-8")).hexdigest()[:12],
        },
    }


def list_omnibar_suggestions(
    *,
    limit: int = 8,
    force_refresh: bool = False,
    layer: DataAccessLayer | None = None,
) -> dict[str, object]:
    safe_limit = min(max(int(limit), 1), 20)
    try:
        resolved_layer = layer or DataAccessLayer.from_environment()
        context = build_omnibar_context(layer=resolved_layer, force_refresh=force_refresh)
    except Exception:
        context = OmnibarContext(home_payload={}, beats=[], symbol_catalog={})

    suggestions: list[dict[str, object]] = []
    seen_queries: set[str] = set()

    for beat in context.beats[:safe_limit]:
        query = _normalize_text(beat.get("bundle_id") or beat.get("sentence"))
        if not query or query in seen_queries:
            continue
        seen_queries.add(query)
        suggestions.append(
            {
                "kind": str(beat.get("kind") or "bundle"),
                "query": query,
                "label": _normalize_text(beat.get("sentence")) or query,
                "subtitle": _trim_text(beat.get("summary"), limit=140),
            }
        )
        if len(suggestions) >= safe_limit:
            break

    if len(suggestions) < safe_limit:
        for release in OMNIBAR_MACRO_RELEASES:
            query = _normalize_text(list(release.get("aliases") or [""])[0] if list(release.get("aliases") or []) else "")
            if not query or query in seen_queries:
                continue
            seen_queries.add(query)
            suggestions.append(
                {
                    "kind": "macro_release",
                    "query": query,
                    "label": _normalize_text(release.get("label")) or query,
                    "subtitle": _normalize_text(release.get("subtitle")),
                }
            )
            if len(suggestions) >= safe_limit:
                break

    if len(suggestions) < safe_limit:
        for query in ("semis after CPI", "what changed in payrolls", "compare banks vs software"):
            if query in seen_queries:
                continue
            suggestions.append(
                {
                    "kind": "agent_prompt",
                    "query": query,
                    "label": query,
                    "subtitle": "Analysis-style prompt for the Zopedia agent path.",
                }
            )
            if len(suggestions) >= safe_limit:
                break

    return {
        "policy_version": OMNIBAR_POLICY_VERSION,
        "suggestions": suggestions[:safe_limit],
    }


__all__ = [
    "OMNIBAR_MACRO_RELEASES",
    "OMNIBAR_POLICY_VERSION",
    "OmnibarContext",
    "build_omnibar_context",
    "list_omnibar_suggestions",
    "resolve_omnibar",
]
