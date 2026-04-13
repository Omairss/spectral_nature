from __future__ import annotations

import re
from typing import Any

import pandas as pd

from data_access.layer import DataAccessLayer
from services import attention_market_events as market_events_module
from services.attention_agentic import search_symbol_news_payload
from services.attention_live_research import search_market_event_news_payload
from services.llm import load_llm_client
from services.market import COMMODITY_FOCUS_UNIVERSES, commodity_proxy_profile
from services.omnibar import resolve_omnibar
from .page_browsing import browse_page


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


def query_needs_evidence(query: str) -> bool:
    normalized = _clean(query).lower()
    if not normalized:
        return False
    if _normalize_symbol(normalized):
        return False
    if any(
        phrase in normalized
        for phrase in (
            "pan out",
            "what happens next",
            "what changed",
            "why ",
            "how ",
            "impact ",
            "implications",
            "outlook",
            "now that",
            "after ",
            "amid ",
            "talks",
            "agreement",
            "ceasefire",
            "iran",
            "tariff",
            "fomc",
            "cpi",
            "payrolls",
            "today",
            "latest",
            "recent",
        )
    ):
        return True
    if "?" in normalized and len(re.findall(r"[a-z0-9]+", normalized)) >= 4:
        return True
    return False


def _query_theme(query: str) -> str:
    normalized = _clean(query).lower()
    if any(token in normalized for token in ("oil", "crude", "brent", "wti", "gasoline", "iran", "hormuz", "opec")):
        return "oil"
    if any(token in normalized for token in ("treasury", "yield", "rates", "bond", "fed", "inflation")):
        return "rates"
    if any(token in normalized for token in ("gold", "haven", "defensive", "precious", "volatility")):
        return "defensives"
    if any(token in normalized for token in ("stocks", "equities", "risk", "airlines", "travel")):
        return "risk"
    return "generic"


def _query_direction(query: str, theme: str) -> str:
    normalized = _clean(query).lower()
    relief_tokens = (
        "agreement",
        "deal",
        "ceasefire",
        "truce",
        "de-escalation",
        "talks resume",
        "progress",
        "relief",
    )
    stress_tokens = (
        "no agreement",
        "no deal",
        "stalled",
        "collapse",
        "breakdown",
        "escalation",
        "attack",
        "strike",
        "sanction",
        "risk",
    )
    relief_hit = any(token in normalized for token in relief_tokens)
    stress_hit = any(token in normalized for token in stress_tokens)
    if theme == "oil":
        if relief_hit and not stress_hit:
            return "down"
        if stress_hit:
            return "up"
        return "up"
    if theme == "risk":
        if relief_hit and not stress_hit:
            return "up"
        if stress_hit:
            return "down"
    if theme == "defensives":
        if relief_hit and not stress_hit:
            return "down"
        if stress_hit:
            return "up"
    return "up" if relief_hit else ("down" if stress_hit else "down")


def _role_text(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    if normalized in set(getattr(market_events_module, "_ENERGY_EQUITY_SYMBOLS", set())):
        return "energy equity"
    if normalized in set(getattr(market_events_module, "_TRAVEL_SYMBOLS", set())):
        return "travel and airline sensitivity"
    if normalized in set(getattr(market_events_module, "_RATE_SYMBOLS", set())):
        return "rates proxy"
    if normalized in set(getattr(market_events_module, "_DEFENSIVE_SYMBOLS", set())):
        return "defensive proxy"
    if normalized in set(COMMODITY_FOCUS_UNIVERSES.get("Shipping & Logistics", [])):
        return "shipping and freight proxy"
    profile = commodity_proxy_profile(normalized)
    if _clean(profile.get("name")):
        return _clean(profile.get("commodity")) or "commodity proxy"
    return "market proxy"


def _market_impact_rows(theme: str, direction: str, *, max_symbols: int) -> list[dict[str, Any]]:
    oil_symbols = sorted(set(getattr(market_events_module, "_OIL_DRIVER_SYMBOLS", set())))
    energy_symbols = sorted(set(getattr(market_events_module, "_ENERGY_EQUITY_SYMBOLS", set())))
    travel_symbols = sorted(set(getattr(market_events_module, "_TRAVEL_SYMBOLS", set())))
    broad_symbols = sorted(set(getattr(market_events_module, "_BROAD_MARKET_SYMBOLS", set())))
    rate_symbols = sorted(set(getattr(market_events_module, "_RATE_SYMBOLS", set())))
    defensive_symbols = sorted(set(getattr(market_events_module, "_DEFENSIVE_SYMBOLS", set())))
    shipping_symbols = list(COMMODITY_FOCUS_UNIVERSES.get("Shipping & Logistics", []))

    symbol_order: list[tuple[str, str]]
    if theme == "oil" and direction == "up":
        symbol_order = (
            [(symbol, "up") for symbol in ["USO", "BNO", "UGA", "CVX", "XOM", "XLE", "BDRY"] if symbol in (oil_symbols + energy_symbols + shipping_symbols)]
            + [(symbol, "down") for symbol in ["JETS", "UAL", "DAL", "AAL", "SPY"] if symbol in (travel_symbols + broad_symbols)]
            + [(symbol, "up") for symbol in ["GLD", "IEF"] if symbol in (defensive_symbols + rate_symbols)]
        )
    elif theme == "oil" and direction == "down":
        symbol_order = (
            [(symbol, "down") for symbol in ["USO", "BNO", "UGA", "CVX", "XOM", "XLE"] if symbol in (oil_symbols + energy_symbols)]
            + [(symbol, "up") for symbol in ["JETS", "UAL", "DAL", "AAL", "SPY"] if symbol in (travel_symbols + broad_symbols)]
            + [(symbol, "down") for symbol in ["GLD", "IEF"] if symbol in (defensive_symbols + rate_symbols)]
        )
    elif theme == "rates":
        symbol_order = (
            [(symbol, "up" if direction == "up" else "down") for symbol in ["TLT", "IEF", "SHY"] if symbol in rate_symbols]
            + [(symbol, "up" if direction == "up" else "down") for symbol in ["SPY", "QQQ", "IWM"] if symbol in broad_symbols]
        )
    elif theme == "defensives":
        symbol_order = (
            [(symbol, "up" if direction == "up" else "down") for symbol in ["GLD", "SLV", "VIXY"] if symbol in defensive_symbols]
            + [(symbol, "down" if direction == "up" else "up") for symbol in ["SPY", "QQQ"] if symbol in broad_symbols]
        )
    else:
        symbol_order = (
            [(symbol, "up" if direction == "up" else "down") for symbol in ["SPY", "QQQ", "IWM"] if symbol in broad_symbols]
            + [(symbol, "up" if direction == "up" else "down") for symbol in ["JETS", "UAL"] if symbol in travel_symbols]
        )

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for symbol, bias in symbol_order:
        normalized = _normalize_symbol(symbol)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        rows.append(
            {
                "symbol": normalized,
                "role": _role_text(normalized),
                "expected_bias": bias,
                "why": (
                    "Higher supply-risk would likely support oil-linked and energy names."
                    if theme == "oil" and bias == "up"
                    else "Relief in oil and inflation pressure would likely help travel and broad-risk names."
                    if theme == "oil" and bias == "up" and normalized in travel_symbols
                    else "This is a second-order market sensitivity linked to the same theme."
                ),
            }
        )
        if len(rows) >= max_symbols:
            break
    return rows


def market_impact_map(
    *,
    query: str,
    max_symbols: int = 8,
) -> dict[str, Any]:
    normalized_query = _clean(query)
    theme = _query_theme(normalized_query)
    direction = _query_direction(normalized_query, theme)
    rows = _market_impact_rows(theme, direction, max_symbols=max(int(max_symbols), 1))
    focus_symbols = [str(row.get("symbol")) for row in rows if _normalize_symbol(row.get("symbol"))][: max(int(max_symbols), 1)]
    direction_text = {"up": "higher", "down": "lower"}.get(direction, direction)
    llm_lines = [
        f"Theme: {theme}. Expected first read: {direction_text}.",
    ]
    if focus_symbols:
        llm_lines.append("Likely impacted symbols to check next: " + ", ".join(focus_symbols[:6]) + ".")
    for row in rows[:6]:
        llm_lines.append(
            f"{row.get('symbol')}: {row.get('role')} expected bias={row.get('expected_bias')}."
        )
    return {
        "query": normalized_query,
        "theme": theme,
        "expected_direction": direction,
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
    if not selected_symbols:
        selected_symbols = [str(item) for item in list(impact.get("focus_symbols") or [])]
    selected_symbols = list(dict.fromkeys([symbol for symbol in selected_symbols if symbol]))[:4]

    rows: list[dict[str, Any]] = []
    if theme != "generic" or query_needs_evidence(query):
        event_payload = search_market_event_news_payload(
            {
                "event_type": theme,
                "anchor_direction": direction,
                "supporting_symbols": selected_symbols,
            },
            max_results=max(min(safe_limit, 6), 3),
        )
        rows.extend(_news_rows_from_payload(event_payload, scope="market_event"))

    llm_client = load_llm_client()
    for symbol in selected_symbols[:4]:
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
    "query_needs_evidence",
    "retained_context",
]
