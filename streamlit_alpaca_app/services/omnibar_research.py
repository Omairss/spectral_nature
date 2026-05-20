from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

from data_access.layer import DataAccessLayer
from services.attention_agentic import search_symbol_news_payload
from services.attention_live_research import search_market_event_news_payload
from services.llm import get_config_param, register_config_param
from services.omnibar import resolve_omnibar
from services.aql_zopedia_engine import load_aql_zopedia_llm_client
from .page_browsing import browse_page
from .saa import (
    apply_zopedia_typed_mutation,
    build_zopedia_change_proposal,
    fetch_youtube_transcript,
    ingest_zopedia_source,
    list_zopedia_change_proposals,
    list_zopedia_maintenance_reports,
    list_zopedia_mutation_audits,
    load_zopedia_page,
    persist_zopedia_change_proposals,
    rollback_zopedia_mutation,
    search_zopedia_pages,
    zopedia_page_neighborhood,
    zopedia_read_source as load_zopedia_read_source,
    zopedia_sources_for_page as load_zopedia_sources_for_page,
    zopedia_trace_to_evidence as load_zopedia_trace_to_evidence,
)

# --- Configurable limits (exposed in Admin > LLM Config > Tuning Parameters) ---
_P_LIVE_EVENT_MAX_RESULTS = register_config_param(
    "Live event evidence max results",
    group="Zopedia",
    default=6,
    description="Default max articles returned by live_event_evidence",
)
_P_LIVE_EVENT_MAX_SYMBOLS = register_config_param(
    "Live event max symbols",
    group="Zopedia",
    default=8,
    description="Max symbols to resolve for live event evidence searches",
)
_P_RETAINED_CONTEXT_MAX_ITEMS = register_config_param(
    "Retained context max items",
    group="Zopedia",
    default=5,
    description="Default max items returned by retained_context",
)
_P_SUMMARY_TRIM_LIMIT = register_config_param(
    "Research summary text trim limit",
    group="Zopedia",
    default=220,
    description="Max chars for summary text in research results",
)


_QUERY_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "event_type": {
            "type": "string",
            "enum": ["oil", "rates", "defensives", "risk", "squeeze", "earnings", "sector", "generic"],
        },
        "direction": {"type": "string", "enum": ["up", "down"]},
        "theme_label": {"type": "string"},
        "evidence_needed": {
            "type": "boolean",
            "description": "True if the query requires fresh external evidence (news, macro data, live prices) to answer. False for simple lookups or ticker queries.",
        },
        "search_keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Key phrases from the query to preserve in search (e.g. 'short squeeze', 'earnings miss'). Extract the specific terms, not categories.",
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
    "required": ["event_type", "direction", "theme_label", "relevant_symbols", "symbol_roles", "evidence_needed", "search_keywords"],
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
                "event_type must be one of: oil, rates, defensives, risk, squeeze, earnings, sector, generic. "
                "Use 'rates' for interest rates, inflation, central banks, CPI, PPI, PCE, NFP, or any economic data release. "
                "Use 'oil' for energy, commodities, or geopolitical supply risk. "
                "Use 'defensives' for gold, safe havens, volatility, or risk-off moves. "
                "Use 'squeeze' for short squeezes, gamma squeezes, or heavily-shorted stock moves. "
                "Use 'earnings' for earnings reports, beats, misses, or guidance. "
                "Use 'sector' for sector rotation, industry-specific trends, or thematic moves. "
                "Use 'risk' for broad equity risk, growth scares, or systemic market moves. "
                "Use 'generic' only when nothing else fits. "
                "direction is 'up' for stress/supply-risk/hawkish reads, 'down' for relief/dovish/risk-on reads. "
                "evidence_needed is true when the query requires fresh external data to answer (news, macro releases, "
                "live prices, recent events). False for simple ticker lookups or questions answerable from static knowledge. "
                "search_keywords: extract the key domain-specific phrases from the query that should be preserved "
                "in any search (e.g. 'short squeeze', 'earnings miss', 'tariff impact'). Do not generalize. "
                "relevant_symbols: the actual US-listed tickers most directly impacted by the event described. "
                "Use the specific stocks/ETFs the query is about. When the user asks for market impact rather than one ticker, "
                "return a compact cross-asset evidence basket: broad benchmarks, directly exposed instruments, and second-order sector or style proxies that would test spillovers beyond the original asset class. "
                "For a rates or bond-market shock, do not return only bond instruments; include enough cross-asset proxies to test whether equities, style, or sectors are moving too. "
                "For interest-rate shocks, the basket must cover the direct rate instrument, a broad equity benchmark, a duration/growth proxy, and a credit/financial or balance-sheet-sensitive proxy when liquid listed instruments exist. "
                "Do not return symbols only because they are popular; each symbol needs a role tied to the query. "
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
        if event_type not in {"oil", "rates", "defensives", "risk", "squeeze", "earnings", "sector", "generic"}:
            event_type = "generic"
        if direction not in {"up", "down"}:
            direction = "down"
        search_keywords = [str(k).strip() for k in list(data.get("search_keywords") or []) if str(k).strip()]
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
            "search_keywords": search_keywords,
            "rows": rows[:max_symbols],
        }
    except Exception:
        return baseline


def market_impact_map(*, query: str, max_symbols: int = 8) -> dict[str, Any]:
    normalized_query = _clean(query)
    safe_max = max(int(max_symbols), 1)
    intent = _llm_query_intent(
        normalized_query,
        llm_client=load_aql_zopedia_llm_client(surface="zopedia.market_impact_map"),
        max_symbols=safe_max,
    )
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
        "search_keywords": intent.get("search_keywords") or [],
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
    trim_limit = int(get_config_param(_P_SUMMARY_TRIM_LIMIT))
    for key in ("surface_summary_text", "what_happened_text", "why_happened_text", "headline", "event_title"):
        text = _clean(payload.get(key))
        if text:
            return _trim(text, limit=trim_limit)
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
            return _trim(text, limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT)))
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
    default_items = int(get_config_param(_P_RETAINED_CONTEXT_MAX_ITEMS))
    safe_limit = _safe_int(max_items if max_items != 5 else default_items, default_items, minimum=1, maximum=12)
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
                    "summary_text": _bundle_summary(payload) or _trim(result.get("subtitle"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
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
                    "summary_text": _trim(result.get("subtitle"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
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
                "summary_text": _trim(summary or headline, limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
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
    default_results = int(get_config_param(_P_LIVE_EVENT_MAX_RESULTS))
    safe_limit = _safe_int(max_results if max_results != 6 else default_results, default_results, minimum=1, maximum=15)
    max_sym = int(get_config_param(_P_LIVE_EVENT_MAX_SYMBOLS))
    impact = market_impact_map(query=query, max_symbols=max_sym)
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
                # Pass the original query as event_title so the search query
                # preserves the user's actual phrasing (e.g. "short squeeze")
                # instead of only using the classified theme/proxy symbols.
                "event_title": _clean(query),
            },
            max_results=max(min(safe_limit, 6), 3),
        )
        rows.extend(_news_rows_from_payload(event_payload, scope="market_event"))

    # Only run per-symbol news search for symbols that came from the user or resolved
    # Search per-symbol news for explicitly resolved symbols (up to 4) or
    # intent-classified symbols (up to 2) since the classifier now returns
    # actual affected tickers rather than generic proxies.
    llm_client = load_aql_zopedia_llm_client(surface="zopedia.live_event_symbol_news")
    symbol_search_limit = 4 if explicitly_resolved else 2
    for symbol in selected_symbols[:symbol_search_limit]:
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


def search_evidence(
    *,
    query: str,
    tickers: list[str] | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    """Search the retained SAA evidence store for matching documents and chunks."""
    from .saa import search_retained_evidence_chunks

    normalized_query = _clean(query)
    safe_limit = _safe_int(max_results, 10, minimum=1, maximum=20)
    normalized_tickers = _symbol_list(tickers)

    frame = search_retained_evidence_chunks(
        query=normalized_query,
        tickers=normalized_tickers or None,
        limit=safe_limit,
        use_semantic=False,
    )

    rows: list[dict[str, Any]] = []
    if not frame.empty:
        for _, row in frame.iterrows():
            title = _clean(row.get("title"))
            chunk_text = _clean(row.get("chunk_text"))
            source_provider = _clean(row.get("source_provider"))
            published_date = _clean(row.get("published_date"))
            mentioned_tickers = _clean(row.get("mentioned_tickers_key"))
            url = _clean(row.get("canonical_url") if "canonical_url" in frame.columns else "")
            rows.append({
                "chunk_record_id": _clean(row.get("chunk_record_id")),
                "canonical_document_id": _clean(row.get("canonical_document_id")),
                "document_id": _clean(row.get("document_id")),
                "chunk_id": _clean(row.get("chunk_id")),
                "title": title,
                "chunk_text": _trim(chunk_text, limit=300),
                "source": source_provider,
                "published_date": published_date,
                "tickers": mentioned_tickers,
                "url": url,
            })

    llm_lines = [f"SAA evidence search for '{normalized_query}': {len(rows)} result(s)."]
    if normalized_tickers:
        llm_lines.append("Ticker filter: " + ", ".join(normalized_tickers) + ".")
    for row in rows[:safe_limit]:
        title = row.get("title") or ""
        source = row.get("source") or ""
        date = row.get("published_date") or ""
        tickers_text = row.get("tickers") or ""
        chunk = row.get("chunk_text") or ""
        line = f"{title}"
        if source:
            line += f" ({source})"
        if date:
            line += f" [{date}]"
        if tickers_text:
            line += f" tickers={tickers_text}"
        if chunk:
            line += f": {chunk}"
        llm_lines.append(line)

    return {
        "query": normalized_query,
        "tickers": normalized_tickers,
        "result_count": len(rows),
        "summary": rows,
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


def _zopedia_rows_from_frame(frame: pd.DataFrame, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in frame.head(max(int(limit), 1)).iterrows():
        source_urls = list(row.get("source_urls") or [])
        rows.append(
            {
                "kind": "zopedia_page",
                "ref": _clean(row.get("page_id")),
                "page_id": _clean(row.get("page_id")),
                "page_type": _clean(row.get("page_type")),
                "title": _clean(row.get("title")),
                "summary_text": _trim(row.get("summary"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
                "source_urls": source_urls,
                "url": source_urls[0] if source_urls else "",
                "entity_refs": list(row.get("entity_refs") or [])[:8],
                "outgoing_links": list(row.get("outgoing_links") or [])[:8],
            }
        )
    return rows


def zopedia_search_pages(
    *,
    query: str,
    max_results: int = 8,
    page_types: list[str] | None = None,
) -> dict[str, Any]:
    safe_limit = _safe_int(max_results, 8, minimum=1, maximum=20)
    normalized_query = _clean(query)
    frame = search_zopedia_pages(query=normalized_query, page_types=page_types, limit=safe_limit)
    rows = _zopedia_rows_from_frame(frame, limit=safe_limit)
    lines = [f"Zopedia page search for '{normalized_query}': {len(rows)} result(s)."]
    for row in rows[:safe_limit]:
        page_type = _clean(row.get("page_type"))
        title = _clean(row.get("title"))
        summary = _clean(row.get("summary_text"))
        line = f"{title}"
        if page_type:
            line += f" [{page_type}]"
        if summary:
            line += f": {summary}"
        lines.append(line)
    return {
        "query": normalized_query,
        "result_count": len(rows),
        "summary": rows,
        "llm_context_text": " ".join(item for item in lines if item),
    }


def zopedia_read_page(*, page_id: str) -> dict[str, Any]:
    page = load_zopedia_page(page_id=page_id)
    if not page:
        return {
            "page_id": _clean(page_id),
            "status": "not_found",
            "summary": [],
            "llm_context_text": f"Zopedia page not found: {_clean(page_id)}.",
        }
    source_urls = list(page.get("source_urls") or [])
    row = {
        "kind": "zopedia_page",
        "ref": _clean(page.get("page_id")),
        "page_id": _clean(page.get("page_id")),
        "page_type": _clean(page.get("page_type")),
        "title": _clean(page.get("title")),
        "summary_text": _trim(page.get("summary"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
        "source_urls": source_urls,
        "url": source_urls[0] if source_urls else "",
        "entity_refs": list(page.get("entity_refs") or [])[:12],
        "outgoing_links": list(page.get("outgoing_links") or [])[:12],
    }
    body = _trim(page.get("body_markdown"), limit=5000)
    lines = [
        f"Zopedia page: {row['title']} [{row['page_type']}].",
        f"Summary: {row['summary_text']}.",
    ]
    if row["entity_refs"]:
        lines.append("Entities: " + ", ".join(row["entity_refs"]) + ".")
    if row["outgoing_links"]:
        lines.append("Links: " + ", ".join(row["outgoing_links"]) + ".")
    if body:
        lines.append("Body: " + body)
    return {
        "page_id": row["page_id"],
        "status": "found",
        "summary": [row],
        "page": page,
        "llm_context_text": " ".join(item for item in lines if item),
    }


def zopedia_neighborhood(*, page_id: str, depth: int = 1) -> dict[str, Any]:
    graph = zopedia_page_neighborhood(page_id=page_id, depth=depth)
    node_rows = [
        {
            "kind": "zopedia_page",
            "ref": _clean(node.get("page_id")),
            "page_id": _clean(node.get("page_id")),
            "page_type": _clean(node.get("page_type")),
            "title": _clean(node.get("title")),
            "summary_text": _trim(node.get("summary"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
        }
        for node in list(graph.get("nodes") or [])
        if isinstance(node, dict)
    ]
    lines = [
        f"Zopedia neighborhood for {_clean(page_id)}: {len(node_rows)} page(s), {len(list(graph.get('edges') or []))} link(s)."
    ]
    for row in node_rows[:8]:
        lines.append(f"{row['title']} [{row['page_type']}]: {row['summary_text']}")
    return {
        "page_id": _clean(page_id),
        "node_count": len(node_rows),
        "edge_count": len(list(graph.get("edges") or [])),
        "summary": node_rows,
        "graph": graph,
        "llm_context_text": " ".join(item for item in lines if item),
    }


def _zopedia_source_rows(refs: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ref in list(refs or [])[: max(int(limit), 1)]:
        if not isinstance(ref, dict):
            continue
        title = _clean(ref.get("title") or ref.get("source_title") or ref.get("url") or ref.get("ref"))
        rows.append(
            {
                "kind": _clean(ref.get("kind")),
                "ref": _clean(ref.get("ref")),
                "page_id": _clean(ref.get("page_id")),
                "chunk_record_id": _clean(ref.get("chunk_record_id")),
                "canonical_document_id": _clean(ref.get("canonical_document_id")),
                "title": title,
                "summary_text": _trim(ref.get("summary_text") or ref.get("body_excerpt"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
                "source": _clean(ref.get("source_type")),
                "support_type": _clean(ref.get("support_type")),
                "url": _clean(ref.get("url") or ref.get("source_url")),
                "inferred": bool(ref.get("inferred")),
                "missing": bool(ref.get("missing")),
                "body_excerpt": _trim(ref.get("body_excerpt"), limit=1200),
            }
        )
    return rows


def zopedia_sources_for_page(*, page_id: str) -> dict[str, Any]:
    result = load_zopedia_sources_for_page(page_id=page_id)
    refs = [ref for ref in list(result.get("sources") or []) if isinstance(ref, dict)]
    rows = _zopedia_source_rows(refs, limit=12)
    page = result.get("page") if isinstance(result.get("page"), dict) else {}
    page_title = _clean(page.get("title")) or _clean(page_id)
    lines = [f"Zopedia sources for {page_title}: {len(rows)} source reference(s)."]
    for row in rows[:8]:
        title = _clean(row.get("title") or row.get("ref"))
        kind = _clean(row.get("kind"))
        url = _clean(row.get("url"))
        excerpt = _clean(row.get("body_excerpt") or row.get("summary_text"))
        line = f"{title}"
        if kind:
            line += f" [{kind}]"
        if url:
            line += f" | url={url}"
        if excerpt:
            line += f": {excerpt}"
        lines.append(line)
    return {
        "page_id": _clean(result.get("page_id") or page_id),
        "status": _clean(result.get("status")),
        "source_count": len(rows),
        "summary": rows,
        "sources": refs,
        "page": page,
        "llm_context_text": " ".join(item for item in lines if item),
    }


def zopedia_read_source(
    *,
    page_id: str = "",
    ref: str = "",
    kind: str = "",
    chunk_record_id: str = "",
    canonical_document_id: str = "",
    url: str = "",
) -> dict[str, Any]:
    result = load_zopedia_read_source(
        page_id=page_id,
        ref=ref,
        kind=kind,
        chunk_record_id=chunk_record_id,
        canonical_document_id=canonical_document_id,
        url=url,
    )
    title = _clean(result.get("title") or result.get("source_title") or result.get("url") or result.get("ref"))
    source_kind = _clean(result.get("source_kind"))
    source_url = _clean(result.get("url") or result.get("source_url"))
    text = _trim(result.get("text"), limit=2200)
    line = f"Zopedia source read: {title or _clean(ref) or _clean(page_id)}"
    if source_kind:
        line += f" [{source_kind}]"
    if source_url:
        line += f" | url={source_url}"
    if text:
        line += f": {text}"
    result["summary"] = [
        {
            "kind": source_kind,
            "ref": _clean(ref or page_id or chunk_record_id or canonical_document_id or url),
            "title": title,
            "url": source_url,
            "summary_text": text,
            "status": _clean(result.get("status")),
        }
    ]
    result["llm_context_text"] = line
    return result


def zopedia_trace_to_evidence(*, page_id: str, depth: int = 1) -> dict[str, Any]:
    graph = load_zopedia_trace_to_evidence(page_id=page_id, depth=depth)
    page_rows = [
        {
            "kind": "zopedia_page",
            "ref": _clean(node.get("page_id")),
            "page_id": _clean(node.get("page_id")),
            "page_type": _clean(node.get("page_type")),
            "title": _clean(node.get("title")),
            "summary_text": _trim(node.get("summary"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
        }
        for node in list(graph.get("nodes") or [])
        if isinstance(node, dict)
    ]
    source_rows = _zopedia_source_rows(
        [node for node in list(graph.get("source_nodes") or []) if isinstance(node, dict)],
        limit=20,
    )
    lines = [
        (
            f"Zopedia evidence trace for {_clean(page_id)}: "
            f"{len(page_rows)} page(s), {len(source_rows)} source node(s), "
            f"{len(list(graph.get('edges') or []))} edge(s)."
        )
    ]
    for row in page_rows[:6]:
        lines.append(f"Page: {row['title']} [{row['page_type']}]. {row['summary_text']}")
    for row in source_rows[:8]:
        title = _clean(row.get("title") or row.get("ref"))
        url = _clean(row.get("url"))
        excerpt = _clean(row.get("body_excerpt") or row.get("summary_text"))
        line = f"Source: {title}"
        if url:
            line += f" | url={url}"
        if excerpt:
            line += f": {excerpt}"
        lines.append(line)
    return {
        "page_id": _clean(graph.get("page_id") or page_id),
        "status": _clean(graph.get("status")),
        "summary": page_rows[:12] + source_rows[:20],
        "page_summary": page_rows,
        "source_summary": source_rows,
        "graph": graph,
        "llm_context_text": " ".join(item for item in lines if item),
    }


def zopedia_ingest_source(
    *,
    title: str,
    source_text: str,
    url: str = "",
    source_type: str = "source",
) -> dict[str, Any]:
    result = ingest_zopedia_source(
        title=title,
        source_text=source_text,
        url=url,
        source_type=source_type,
        llm_client=load_aql_zopedia_llm_client(surface="zopedia.ingest_source"),
    )
    rows = _zopedia_rows_from_frame(pd.DataFrame(result.get("pages") or []), limit=12)
    mutation_audit = result.get("mutation_audit") if isinstance(result.get("mutation_audit"), dict) else {}
    result["summary"] = rows
    result["llm_context_text"] = (
        f"Stored {len(rows)} Zopedia page(s) from {result.get('source_title') or title}. "
        f"Enrichment status: {result.get('enrichment_status') or 'unknown'}. "
        f"Mutation audit: {_clean(mutation_audit.get('status')) or 'not_recorded'}."
    )
    return result


def zopedia_ingest_youtube(*, url: str, title: str = "") -> dict[str, Any]:
    transcript = fetch_youtube_transcript(url)
    text = _clean(transcript.get("transcript"))
    if not text:
        return {
            "status": transcript.get("status") or "transcript_unavailable",
            "video_id": transcript.get("video_id") or "",
            "summary": [],
            "llm_context_text": f"YouTube transcript unavailable for {_clean(url)}.",
        }
    return zopedia_ingest_source(
        title=title or f"YouTube {transcript.get('video_id')}",
        source_text=text,
        url=url,
        source_type="youtube_transcript",
    )


def zopedia_propose_change(
    *,
    proposal_type: str,
    page_id: str = "",
    title: str = "",
    rationale: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    proposal = build_zopedia_change_proposal(
        proposal_type=proposal_type,
        page_id=page_id,
        title=title,
        rationale=rationale,
        payload=payload,
    )
    frame = persist_zopedia_change_proposals([proposal])
    rows = frame.to_dict("records") if isinstance(frame, pd.DataFrame) and not frame.empty else [proposal]
    return {
        "status": "proposed",
        "summary": [
            {
                "kind": "zopedia_proposal",
                "ref": _clean(item.get("proposal_id")),
                "title": _clean(item.get("title")),
                "summary_text": _clean(item.get("rationale")),
            }
            for item in rows
        ],
        "proposal": proposal,
        "llm_context_text": f"Created Zopedia proposal: {proposal['title']}. Rationale: {proposal['rationale']}",
    }


def zopedia_list_proposals(*, status: str = "open", max_results: int = 12) -> dict[str, Any]:
    safe_limit = _safe_int(max_results, 12, minimum=1, maximum=30)
    frame = list_zopedia_change_proposals(status=status, limit=safe_limit)
    rows: list[dict[str, Any]] = []
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        for _, row in frame.head(safe_limit).iterrows():
            rows.append(
                {
                    "kind": "zopedia_proposal",
                    "ref": _clean(row.get("proposal_id")),
                    "proposal_type": _clean(row.get("proposal_type")),
                    "page_id": _clean(row.get("page_id")),
                    "title": _clean(row.get("title")),
                    "summary_text": _trim(row.get("rationale"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
                    "status": _clean(row.get("status")),
                }
            )
    lines = [f"Zopedia proposals status={_clean(status) or 'all'}: {len(rows)} result(s)."]
    for row in rows:
        lines.append(f"{row['title']} [{row['proposal_type']}]: {row['summary_text']}")
    return {
        "status": _clean(status),
        "result_count": len(rows),
        "summary": rows,
        "llm_context_text": " ".join(item for item in lines if item),
    }


def zopedia_list_mutations(
    *,
    status: str = "",
    mutation_type: str = "",
    max_results: int = 12,
) -> dict[str, Any]:
    safe_limit = _safe_int(max_results, 12, minimum=1, maximum=30)
    frame = list_zopedia_mutation_audits(status=status, mutation_type=mutation_type, limit=safe_limit)
    rows: list[dict[str, Any]] = []
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        for _, row in frame.head(safe_limit).iterrows():
            page_ids = []
            try:
                parsed_page_ids = json.loads(_clean(row.get("page_ids_json")) or "[]")
                page_ids = list(parsed_page_ids) if isinstance(parsed_page_ids, list) else []
            except Exception:
                page_ids = []
            rows.append(
                {
                    "kind": "zopedia_mutation",
                    "ref": _clean(row.get("mutation_id")),
                    "mutation_id": _clean(row.get("mutation_id")),
                    "mutation_type": _clean(row.get("mutation_type")),
                    "risk_level": _clean(row.get("risk_level")),
                    "status": _clean(row.get("status")),
                    "source": _clean(row.get("source")),
                    "page_ids": page_ids,
                    "summary_text": (
                        f"{_clean(row.get('mutation_type')) or 'mutation'} "
                        f"{_clean(row.get('status')) or 'status_unknown'} "
                        f"for {len(page_ids)} page(s)."
                    ),
                    "created_at_utc": _clean(row.get("created_at_utc")),
                }
            )
    lines = [
        (
            f"Zopedia mutation audit status={_clean(status) or 'all'} "
            f"type={_clean(mutation_type) or 'all'}: {len(rows)} result(s)."
        )
    ]
    for row in rows[:8]:
        lines.append(
            f"{row['mutation_type']} [{row['status']}, {row['risk_level']}]: "
            f"{len(row['page_ids'])} page(s), source={row['source']}."
        )
    return {
        "status": _clean(status),
        "mutation_type": _clean(mutation_type),
        "result_count": len(rows),
        "summary": rows,
        "llm_context_text": " ".join(item for item in lines if item),
    }


def zopedia_list_maintenance_reports(
    *,
    status: str = "",
    max_results: int = 6,
) -> dict[str, Any]:
    safe_limit = _safe_int(max_results, 6, minimum=1, maximum=20)
    frame = list_zopedia_maintenance_reports(status=status, limit=safe_limit)
    rows: list[dict[str, Any]] = []
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        for _, row in frame.head(safe_limit).iterrows():
            try:
                summary = json.loads(_clean(row.get("summary_json")) or "{}")
            except Exception:
                summary = {}
            try:
                issue_rows = json.loads(_clean(row.get("issue_rows_json")) or "[]")
            except Exception:
                issue_rows = []
            issue_counts = dict(summary.get("issue_counts") or {}) if isinstance(summary, dict) else {}
            top_communities = list(summary.get("top_communities") or []) if isinstance(summary, dict) else []
            rows.append(
                {
                    "kind": "zopedia_maintenance_report",
                    "ref": _clean(row.get("run_id")),
                    "run_id": _clean(row.get("run_id")),
                    "status": _clean(row.get("status")),
                    "page_count": int(row.get("page_count") or 0),
                    "edge_count": int(row.get("edge_count") or 0),
                    "issue_count": int(row.get("issue_count") or 0),
                    "mutation_count": int(row.get("mutation_count") or 0),
                    "proposal_count": int(row.get("proposal_count") or 0),
                    "issue_counts": issue_counts,
                    "top_communities": top_communities[:5],
                    "issues": list(issue_rows or [])[:8],
                    "created_at_utc": _clean(row.get("created_at_utc")),
                    "summary_text": (
                        f"{int(row.get('page_count') or 0)} page(s), "
                        f"{int(row.get('edge_count') or 0)} edge(s), "
                        f"{int(row.get('issue_count') or 0)} issue(s)."
                    ),
                }
            )
    lines = [f"Zopedia maintenance reports status={_clean(status) or 'all'}: {len(rows)} result(s)."]
    for row in rows[:6]:
        issue_counts = row.get("issue_counts") if isinstance(row.get("issue_counts"), dict) else {}
        issue_text = ", ".join(f"{key}={value}" for key, value in list(issue_counts.items())[:5])
        line = f"{row['run_id']} [{row['status']}]: {row['summary_text']}"
        if issue_text:
            line += f" Issues: {issue_text}."
        lines.append(line)
    return {
        "status": _clean(status),
        "result_count": len(rows),
        "summary": rows,
        "llm_context_text": " ".join(item for item in lines if item),
    }


def zopedia_apply_mutation(
    *,
    mutation_type: str,
    page_id: str = "",
    target_page_id: str = "",
    pages: list[dict[str, Any]] | None = None,
    metadata_patch: dict[str, Any] | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    rationale: str = "",
    payload: dict[str, Any] | None = None,
    allow_risky: bool = False,
) -> dict[str, Any]:
    result = apply_zopedia_typed_mutation(
        mutation_type=mutation_type,
        page_id=page_id,
        target_page_id=target_page_id,
        pages=pages or [],
        metadata_patch=metadata_patch or {},
        evidence_refs=evidence_refs or [],
        rationale=rationale,
        payload=payload or {},
        actor="zopedia-agent",
        source="zopedia.agent.apply_mutation",
        allow_risky=allow_risky,
    )
    audit = result.get("mutation_audit") if isinstance(result.get("mutation_audit"), dict) else {}
    proposal = result.get("proposal") if isinstance(result.get("proposal"), dict) else {}
    rows = _zopedia_rows_from_frame(pd.DataFrame(result.get("pages") or []), limit=12)
    summary: list[dict[str, Any]] = []
    if audit:
        summary.append(
            {
                "kind": "zopedia_mutation",
                "ref": _clean(audit.get("mutation_id")),
                "mutation_id": _clean(audit.get("mutation_id")),
                "mutation_type": _clean(audit.get("mutation_type")),
                "risk_level": _clean(audit.get("risk_level")),
                "status": _clean(result.get("status")),
                "summary_text": f"{_clean(audit.get('mutation_type'))} committed for {len(rows)} page(s).",
            }
        )
    if proposal:
        summary.append(
            {
                "kind": "zopedia_proposal",
                "ref": _clean(proposal.get("proposal_id")),
                "proposal_id": _clean(proposal.get("proposal_id")),
                "proposal_type": _clean(proposal.get("proposal_type")),
                "status": _clean(result.get("status")),
                "title": _clean(proposal.get("title")),
                "summary_text": _trim(proposal.get("rationale"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
            }
        )
    result["summary"] = summary + rows
    result["llm_context_text"] = (
        f"Zopedia apply mutation type={_clean(mutation_type)} status={_clean(result.get('status'))}. "
        f"Committed pages={len(rows)} proposal={_clean(proposal.get('proposal_id')) or 'none'} "
        f"mutation={_clean(audit.get('mutation_id')) or 'none'}."
    )
    return result


def zopedia_rollback_mutation(*, mutation_id: str) -> dict[str, Any]:
    result = rollback_zopedia_mutation(mutation_id=mutation_id)
    audit = result.get("mutation_audit") if isinstance(result.get("mutation_audit"), dict) else {}
    rows = _zopedia_rows_from_frame(pd.DataFrame(result.get("pages") or []), limit=12)
    summary = [
        {
            "kind": "zopedia_mutation",
            "ref": _clean(audit.get("mutation_id")) or _clean(result.get("mutation_id")),
            "mutation_id": _clean(audit.get("mutation_id")) or _clean(result.get("mutation_id")),
            "mutation_type": _clean(audit.get("mutation_type")) or "rollback",
            "risk_level": _clean(audit.get("risk_level")),
            "status": _clean(result.get("status")),
            "source": _clean(audit.get("source")),
            "summary_text": f"Rollback {result.get('status') or 'status_unknown'} for {_clean(result.get('mutation_id'))}.",
        }
    ]
    result["summary"] = summary + rows
    result["llm_context_text"] = (
        f"Zopedia rollback for {_clean(result.get('mutation_id') or mutation_id)}: "
        f"{_clean(result.get('status')) or 'unknown'}; {len(rows)} page change(s)."
    )
    return result


__all__ = [
    "live_event_evidence",
    "market_impact_map",
    "open_page",
    "retained_context",
    "search_evidence",
    "zopedia_apply_mutation",
    "zopedia_ingest_source",
    "zopedia_ingest_youtube",
    "zopedia_list_maintenance_reports",
    "zopedia_list_mutations",
    "zopedia_list_proposals",
    "zopedia_neighborhood",
    "zopedia_propose_change",
    "zopedia_rollback_mutation",
    "zopedia_read_source",
    "zopedia_read_page",
    "zopedia_search_pages",
    "zopedia_sources_for_page",
    "zopedia_trace_to_evidence",
]
