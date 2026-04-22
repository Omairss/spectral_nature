from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

from data_access.layer import DataAccessLayer
from services.attention_agentic import search_symbol_news_payload
from services.attention_live_research import search_market_event_news_payload
from services.llm import load_llm_client
from services.omnibar import resolve_omnibar
from .page_browsing import browse_page


_QUERY_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "event_type": {
            "type": "string",
            "enum": ["oil", "rates", "defensives", "risk", "generic"],
        },
        "direction": {"type": "string", "enum": ["up", "down"]},
        "theme_label": {"type": "string"},
        "evidence_needed": {
            "type": "boolean",
            "description": "True if the query requires fresh external evidence (news, macro data, live prices) to answer. False for simple lookups or ticker queries.",
        },
        "relevant_symbols": {"type": "array", "items": {"type": "string"}},
        "symbol_roles": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "symbol": {"type": "string"},
                    "role": {"type": "string"},
                    "expected_bias": {"type": "string", "enum": ["up", "down"]},
                },
                "required": ["symbol", "role", "expected_bias"],
            },
        },
    },
    "required": ["event_type", "direction", "theme_label", "relevant_symbols", "symbol_roles", "evidence_needed"],
}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _trim(text: object, *, limit: int = 220) -> str:
    clean = _clean(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _normalize_symbol(value: object) -> str:
    symbol = _clean(value).upper()
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,6}", symbol):
        return symbol
    return ""


def _symbol_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[\s,|]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item) for item in value]
    else:
        raw_items = [str(value)]
    out: list[str] = []
    for item in raw_items:
        symbol = _normalize_symbol(item)
        if symbol and symbol not in out:
            out.append(symbol)
    return out


def _safe_int(value: object, default: int, *, minimum: int = 1, maximum: int = 20) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return min(max(parsed, minimum), maximum)


def _layer(layer: DataAccessLayer | None = None) -> DataAccessLayer:
    if layer is not None and hasattr(layer, "resolve_attention_home_1d"):
        return layer
    return DataAccessLayer.from_environment()


def _llm_query_intent(query: str, *, llm_client: object, max_symbols: int) -> dict[str, Any]:
    """Extract theme, direction, evidence need, and relevant symbols from a free-form query via LLM.

    Returns a best-effort result. On LLM failure returns a generic/empty baseline.
    """
    baseline: dict[str, Any] = {
        "event_type": "generic",
        "direction": "down",
        "theme_label": "general market",
        "evidence_needed": True,
        "rows": [],
    }
    if llm_client is None or not hasattr(llm_client, "generate_json"):
        return baseline
    try:
        data = llm_client.generate_json(
            system_prompt=(
                "You are a market-theme classifier for a financial research platform. "
                "Given a user query, classify it and infer the directional impact on financial markets. "
                "event_type must be one of: oil, rates, defensives, risk, generic. "
                "Use 'rates' for interest rates, inflation, central banks, CPI, PPI, PCE, NFP, or any economic data release. "
                "Use 'oil' for energy, commodities, or geopolitical supply risk. "
                "Use 'defensives' for gold, safe havens, volatility, or risk-off moves. "
                "Use 'risk' for equities, growth, earnings, or sector-specific moves. "
                "Use 'generic' only when nothing else fits. "
                "direction is 'up' for stress/supply-risk/hawkish reads, 'down' for relief/dovish/risk-on reads. "
                "evidence_needed is true when the query requires fresh external data to answer (news, macro releases, "
                "live prices, recent events). False for simple ticker lookups or questions answerable from static knowledge. "
                "relevant_symbols: US-listed ETFs or large-caps most likely impacted — include proxies "
                "(TLT/IEF for rates, USO/XLE for oil, GLD/VIXY for defensives, SPY/QQQ for broad risk). "
                f"Return at most {max_symbols} symbols."
            ),
            user_prompt=json.dumps({"query": query}, ensure_ascii=False),
            schema_name="query_market_intent",
            schema=_QUERY_INTENT_SCHEMA,
        )
        if not isinstance(data, dict):
            return baseline
        event_type = str(data.get("event_type") or "generic").lower()
        direction = str(data.get("direction") or "down").lower()
        if event_type not in {"oil", "rates", "defensives", "risk", "generic"}:
            event_type = "generic"
        if direction not in {"up", "down"}:
            direction = "down"
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for item in list(data.get("symbol_roles") or []):
            if not isinstance(item, dict):
                continue
            symbol = _normalize_symbol(item.get("symbol"))
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            rows.append({
                "symbol": symbol,
                "role": _clean(item.get("role")) or "market proxy",
                "expected_bias": str(item.get("expected_bias") or direction).lower(),
                "why": "",
            })
        for raw in list(data.get("relevant_symbols") or []):
            symbol = _normalize_symbol(raw)
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            rows.append({"symbol": symbol, "role": "market proxy", "expected_bias": direction, "why": ""})
        return {
            "event_type": event_type,
            "direction": direction,
            "theme_label": _clean(data.get("theme_label")) or event_type,
            "evidence_needed": bool(data.get("evidence_needed", True)),
            "rows": rows[:max_symbols],
        }
    except Exception:
        return baseline


def market_impact_map(*, query: str, max_symbols: int = 8) -> dict[str, Any]:
    normalized_query = _clean(query)
    safe_max = max(int(max_symbols), 1)
    intent = _llm_query_intent(normalized_query, llm_client=load_llm_client(), max_symbols=safe_max)
    theme = intent["event_type"]
    direction = intent["direction"]
    rows = intent["rows"]
    focus_symbols = [str(row["symbol"]) for row in rows if _normalize_symbol(row.get("symbol"))][:safe_max]
    direction_text = {"up": "higher", "down": "lower"}.get(direction, direction)
    llm_lines = [f"Theme: {theme}. Expected first read: {direction_text}."]
    if focus_symbols:
        llm_lines.append("Likely impacted symbols to check next: " + ", ".join(focus_symbols[:6]) + ".")
    for row in rows[:6]:
        llm_lines.append(f"{row['symbol']}: {row['role']} expected bias={row['expected_bias']}.")
    return {
        "query": normalized_query,
        "theme": theme,
        "expected_direction": direction,
        "evidence_needed": intent["evidence_needed"],
        "focus_symbols": focus_symbols,
        "summary": rows,
        "llm_context_text": " ".join(llm_lines),
    }


def _asset_name(layer: DataAccessLayer, symbol: str, *, force_refresh: bool) -> str:
    try:
        payload = layer.resolve_asset_metadata(symbol, force_refresh=force_refresh).payload
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return ""
    return _clean(payload.get("name") or payload.get("company_name"))


def _bundle_summary(payload: dict[str, Any]) -> str:
    for key in ("surface_summary_text", "what_happened_text", "why_happened_text", "headline", "event_title"):
        text = _clean(payload.get(key))
        if text:
            return _trim(text, limit=220)
    return ""


def _background_summary(payload: dict[str, Any]) -> str:
    for key in (
        "llm_summary_text",
        "description_text",
        "what_changed_text",
        "why_now_text",
        "context_story_text",
        "company_background_text",
    ):
        text = _clean(payload.get(key))
        if text:
            return _trim(text, limit=220)
    return ""


def retained_context(
    *,
    query: str,
    focus_symbols: list[str] | None = None,
    max_items: int = 5,
    force_refresh: bool = False,
    layer: DataAccessLayer | None = None,
) -> dict[str, Any]:
    resolved_layer = _layer(layer)
    safe_limit = _safe_int(max_items, 5, minimum=1, maximum=8)
    resolution = resolve_omnibar(
        query=query,
        preferred_mode="search",
        force_refresh=force_refresh,
        layer=resolved_layer,
    )
    impact = market_impact_map(query=query, max_symbols=6)
    rows: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, str]] = set()
    search_results = list(resolution.get("search_results") or [])

    for result in search_results[:safe_limit]:
        kind = _clean(result.get("kind")).lower()
        if kind == "bundle":
            bundle_id = _clean(result.get("bundle_id") or result.get("ref"))
            if not bundle_id or (kind, bundle_id) in seen_refs:
                continue
            seen_refs.add((kind, bundle_id))
            payload = resolved_layer.resolve_attention_research_bundle(bundle_id, force_refresh=force_refresh).payload
            payload = payload if isinstance(payload, dict) else {}
            rows.append(
                {
                    "kind": "bundle",
                    "ref": bundle_id,
                    "label": _clean(result.get("label")) or bundle_id,
                    "summary_text": _bundle_summary(payload) or _trim(result.get("subtitle"), limit=220),
                    "source_line": _clean(payload.get("source_summary") or payload.get("source_line")),
                    "supporting_symbols": ", ".join(_symbol_list(payload.get("related_symbols") if isinstance(payload.get("related_symbols"), list) else result.get("symbols"))[:4]),
                }
            )
        elif kind == "symbol":
            symbol = _normalize_symbol(result.get("symbol") or result.get("ref"))
            if not symbol or (kind, symbol) in seen_refs:
                continue
            seen_refs.add((kind, symbol))
            payload = resolved_layer.resolve_attention_ticker_background(symbol, force_refresh=force_refresh).payload
            payload = payload if isinstance(payload, dict) else {}
            rows.append(
                {
                    "kind": "symbol",
                    "ref": symbol,
                    "label": symbol,
                    "summary_text": _background_summary(payload),
                    "company_name": _clean(payload.get("company_name")) or _asset_name(resolved_layer, symbol, force_refresh=force_refresh),
                    "source_line": _clean(payload.get("llm_source_line") or payload.get("source_line") or payload.get("source_summary")),
                }
            )
        elif kind == "macro_release":
            ref = _clean(result.get("ref"))
            if not ref or (kind, ref) in seen_refs:
                continue
            seen_refs.add((kind, ref))
            rows.append(
                {
                    "kind": "macro_release",
                    "ref": ref,
                    "label": _clean(result.get("label")) or ref,
                    "summary_text": _trim(result.get("subtitle"), limit=220),
                    "company_name": "",
                    "source_line": "Macro release context",
                }
            )

    for symbol in _symbol_list(focus_symbols) or list(impact.get("focus_symbols") or []):
        if len(rows) >= safe_limit:
            break
        if ("symbol", symbol) in seen_refs:
            continue
        payload = resolved_layer.resolve_attention_ticker_background(symbol, force_refresh=force_refresh).payload
        payload = payload if isinstance(payload, dict) else {}
        rows.append(
            {
                "kind": "symbol",
                "ref": symbol,
                "label": symbol,
                "summary_text": _background_summary(payload),
                "company_name": _clean(payload.get("company_name")) or _asset_name(resolved_layer, symbol, force_refresh=force_refresh),
                "source_line": _clean(payload.get("llm_source_line") or payload.get("source_line") or payload.get("source_summary")),
            }
        )
        seen_refs.add(("symbol", symbol))

    llm_lines = [f"Retained context matched {len(rows)} item(s)."]
    if impact.get("focus_symbols"):
        llm_lines.append("Possible follow-up symbols: " + ", ".join(list(impact.get("focus_symbols") or [])[:6]) + ".")
    for row in rows[:safe_limit]:
        label = _clean(row.get("label") or row.get("ref"))
        company_name = _clean(row.get("company_name"))
        summary_text = _clean(row.get("summary_text"))
        source_line = _clean(row.get("source_line"))
        line = f"{label}"
        if company_name:
            line += f" ({company_name})"
        if summary_text:
            line += f": {summary_text}"
        if source_line:
            line += f" Source: {source_line}."
        llm_lines.append(line)

    return {
        "query": _clean(query),
        "intent": _clean(resolution.get("intent")),
        "matched_count": len(rows),
        "focus_symbols": list(dict.fromkeys(_symbol_list(focus_symbols) + list(impact.get("focus_symbols") or [])))[:6],
        "summary": rows[:safe_limit],
        "llm_context_text": " ".join(item for item in llm_lines if item),
    }


def _news_rows_from_payload(
    payload: dict[str, Any],
    *,
    symbol: str = "",
    scope: str,
) -> list[dict[str, Any]]:
    articles = payload.get("articles")
    if not isinstance(articles, pd.DataFrame) or articles.empty:
        return []
    rows: list[dict[str, Any]] = []
    frame = articles.copy()
    frame["published_at"] = pd.to_datetime(frame.get("published_at"), utc=True, errors="coerce")
    frame = frame.sort_values("published_at", ascending=False, na_position="last")
    for _, article in frame.iterrows():
        headline = _clean(article.get("headline"))
        summary = _clean(article.get("summary") or article.get("description"))
        url = _clean(article.get("url"))
        if not headline and not summary:
            continue
        published_at = pd.to_datetime(article.get("published_at"), utc=True, errors="coerce")
        rows.append(
            {
                "scope": scope,
                "symbol": symbol,
                "headline": headline,
                "summary_text": _trim(summary or headline, limit=220),
                "source": _clean(article.get("source")),
                "published_at": published_at.isoformat() if pd.notna(published_at) else "",
                "url": url,
            }
        )
    return rows


def live_event_evidence(
    *,
    query: str,
    focus_symbols: list[str] | None = None,
    max_results: int = 6,
    force_refresh: bool = False,
    layer: DataAccessLayer | None = None,
) -> dict[str, Any]:
    resolved_layer = _layer(layer)
    safe_limit = _safe_int(max_results, 6, minimum=1, maximum=10)
    impact = market_impact_map(query=query, max_symbols=8)
    theme = _clean(impact.get("theme")) or "generic"
    direction = _clean(impact.get("expected_direction")) or "down"

    selected_symbols = _symbol_list(focus_symbols)
    explicitly_resolved = bool(selected_symbols)
    if not selected_symbols:
        resolution = resolve_omnibar(
            query=query,
            preferred_mode="search",
            force_refresh=force_refresh,
            layer=resolved_layer,
        )
        selected_symbols = [
            _normalize_symbol(item.get("symbol") or item.get("ref"))
            for item in list(resolution.get("search_results") or [])
            if _clean(item.get("kind")).lower() == "symbol"
        ]
        explicitly_resolved = bool(selected_symbols)
    if not selected_symbols:
        selected_symbols = [str(item) for item in list(impact.get("focus_symbols") or [])]
    selected_symbols = list(dict.fromkeys([symbol for symbol in selected_symbols if symbol]))[:4]

    rows: list[dict[str, Any]] = []
    if impact.get("evidence_needed", True):
        event_payload = search_market_event_news_payload(
            {
                "event_type": theme,
                "anchor_direction": direction,
                "supporting_symbols": selected_symbols,
            },
            max_results=max(min(safe_limit, 6), 3),
        )
        rows.extend(_news_rows_from_payload(event_payload, scope="market_event"))

    # Only run per-symbol news search for symbols that came from the user or resolved
    # directly from the query. Fallback market-proxy symbols (SPY, TLT, etc.) are passed
    # to the event search above for relevance scoring but are not worth individual searches
    # for queries that didn't resolve to specific securities.
    llm_client = load_llm_client()
    for symbol in (selected_symbols if explicitly_resolved else [])[:4]:
        company_name = _asset_name(resolved_layer, symbol, force_refresh=force_refresh)
        payload = search_symbol_news_payload(
            symbol,
            company_name=company_name,
            max_results=max(min(safe_limit, 6), 3),
            llm_client=llm_client,
        )
        rows.extend(_news_rows_from_payload(payload, symbol=symbol, scope="symbol"))

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (
            _clean(row.get("url")).lower(),
            _clean(row.get("headline")).lower(),
            _clean(row.get("symbol")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    def _sort_key(item: dict[str, Any]) -> tuple[float, str]:
        published_at = pd.to_datetime(item.get("published_at"), utc=True, errors="coerce")
        timestamp = published_at.timestamp() if pd.notna(published_at) else 0.0
        return (-timestamp, _clean(item.get("headline")).lower())

    ordered = sorted(deduped, key=_sort_key)[:safe_limit]
    llm_lines = [
        f"Live evidence theme={theme} direction={direction}.",
    ]
    if selected_symbols:
        llm_lines.append("Focused symbols: " + ", ".join(selected_symbols) + ".")
    for row in ordered[:6]:
        label = _clean(row.get("symbol")) or _clean(row.get("scope"))
        source = _clean(row.get("source"))
        published_at = _clean(row.get("published_at"))
        headline = _clean(row.get("headline"))
        url = _clean(row.get("url"))
        line = f"{label}: {headline}"
        if source:
            line += f" | source={source}"
        if published_at:
            line += f" | published_at={published_at}"
        if url:
            line += f" | url={url}"
        llm_lines.append(line)

    return {
        "query": _clean(query),
        "theme": theme,
        "expected_direction": direction,
        "focus_symbols": selected_symbols,
        "summary": ordered,
        "llm_context_text": " ".join(item for item in llm_lines if item),
    }


def open_page(
    *,
    url: str,
    max_chars: int = 5000,
) -> dict[str, Any]:
    safe_max_chars = _safe_int(max_chars, 5000, minimum=800, maximum=12000)
    page = browse_page(url, max_text_chars=safe_max_chars)
    summary_row = {
        "url": _clean(page.get("final_url") or page.get("url")),
        "title": _clean(page.get("title")),
        "mode": _clean(page.get("mode")),
        "excerpt": _trim(page.get("excerpt"), limit=260),
        "warning": _clean(page.get("warning")),
    }
    llm_lines = [
        f"Opened page via {summary_row['mode'] or 'unknown'}.",
        f"Title: {summary_row['title'] or summary_row['url']}.",
    ]
    if summary_row["warning"]:
        llm_lines.append(f"Warning: {summary_row['warning']}.")
    if summary_row["excerpt"]:
        llm_lines.append(f"Excerpt: {summary_row['excerpt']}.")
    text = _trim(page.get("text"), limit=safe_max_chars)
    if text:
        llm_lines.append(f"Visible text: {text}")
    return {
        "url": summary_row["url"],
        "summary": [summary_row],
        "page_text": text,
        "llm_context_text": " ".join(item for item in llm_lines if item),
    }


__all__ = [
    "live_event_evidence",
    "market_impact_map",
    "open_page",
    "retained_context",
]
