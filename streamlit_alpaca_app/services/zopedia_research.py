from __future__ import annotations

from services.work_scope import WorkScope
from services.llm import llm_request_deadline

import hashlib
import json
import os
import re
import threading
import time
from contextvars import copy_context
from typing import Any

import pandas as pd

from data_access.layer import DataAccessLayer
from services.attention_agentic import search_symbol_news_payload
from services.attention_live_research import search_market_event_news_payload
from services.llm import get_config_param, get_prompt, register_config_param, register_narrative_prompt, remaining_request_timeout
from services.zopedia_resolver import resolve_zopedia
from services.aql_zopedia_engine import load_aql_zopedia_llm_client
from services.common.news_freshness import is_recent_for_attention
from .page_browsing import browse_page
from .llm_flow import SEARCH_QUERIES, SEARCH_RESULTS, SEARCH_PARALLEL

_P_READ_WINDOW = register_config_param("Source read characters", "Research flow", 5000, "Characters returned from a retained capture in one read.", stage="read", minimum=800, maximum=100000)
_P_CAPTURE_LIMIT = register_config_param("Source capture characters", "Research flow", 1000000, "Maximum text acquired and retained from one page.", stage="read", minimum=12000, maximum=10000000)

_SOURCE_SUMMARY_PROMPT = register_narrative_prompt("Source first-pass summary", "services/zopedia_research.py", "Summarize the supplied source faithfully. Preserve useful facts, exact numbers, dates, qualifications and competing explanations. Do not invent missing context or replace the original with your interpretation. Return a concise summary.", group="Research flow", stage="summary")
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
    stage="search",
    label="Maximum live articles", section="Live web search", minimum=1, maximum=15,
)
_P_LIVE_EVENT_MAX_SYMBOLS = register_config_param(
    "Live event max symbols",
    group="Zopedia",
    default=8,
    description="Max symbols to resolve for live event evidence searches",
    stage="search",
    label="Focus symbols", section="Live web search",
)
_P_LIVE_EVENT_TOTAL_BUDGET_SECONDS = register_config_param(
    "Live event total budget seconds",
    group="Zopedia",
    default=18,
    description="Total internal wall-clock budget for live_event_evidence before returning partial results",
    stage="search",
    label="Search time (seconds)", section="Live web search",
    environment_variable="ZOPEDIA_LIVE_EVENT_TOTAL_BUDGET_SECONDS",
    minimum=6, maximum=900,
)
_P_LIVE_EVENT_LLM_HELPER_TIMEOUT_SECONDS = register_config_param(
    "Live event LLM helper timeout seconds",
    group="Zopedia",
    default=4,
    description="Max seconds for internal live-event helper LLM calls before using direct query fallbacks",
    stage="search",
    retired=True,
    environment_variable="ZOPEDIA_LIVE_EVENT_LLM_HELPER_TIMEOUT_SECONDS",
    minimum=1, maximum=120,
)
_P_LIVE_EVENT_PROVIDER_TIMEOUT_SECONDS = register_config_param(
    "Live event provider timeout seconds",
    group="Zopedia",
    default=6,
    description="Per-provider timeout for interactive live-event news searches",
    stage="search",
    label="Provider timeout (seconds)", section="Live web search",
    environment_variable="ZOPEDIA_LIVE_EVENT_PROVIDER_TIMEOUT_SECONDS",
    minimum=3, maximum=180,
)
_P_RETAINED_CONTEXT_MAX_ITEMS = register_config_param(
    "Retained context max items",
    group="Zopedia",
    default=5,
    description="Default max items returned by retained_context",
    stage="search",
    label="Retained context items", section="Retained search",
)
_P_SUMMARY_TRIM_LIMIT = register_config_param(
    "Research summary text trim limit",
    group="Zopedia",
    default=220,
    description="Max chars for summary text in research results",
    stage="summary",
    label="Search preview characters", section="Search previews",
)

_RETAINED_SEARCH_PLAN_PROMPT = register_narrative_prompt(
    "Retained Evidence Search Plan",
    "services/zopedia_research.py",
    "Plan high-recall retrieval for the actual research request. Return varied short semantic search queries, "
    "including indirect relationships and competing explanations where useful. The caller's query is a search focus; "
    "use the original request and application time context to understand it. Do not require exact tickers or guess a "
    "single cause before discovery. Set reporting_start and reporting_end when dated developments matter to the question. "
    "Interpret relative dates in the supplied timezone, anchored to asof_time_utc, never your training date. "
    "Use ISO dates or timezone-aware timestamps. For a current session, cover that local calendar day through the as-of time. "
    "Caller dates are search hints. Derive the primary reporting window from the original request rather than copying "
    "a caller's broader lookback. Use null for both boundaries when the request is timeless. This window adds broad reporting from that period; "
    "historical and business-context searches remain available across the whole corpus. Return the requested JSON.",
    group="Zopedia",
    stage="search",
)
_RETAINED_SEARCH_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "queries": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
        "reporting_start": {"type": ["string", "null"]},
        "reporting_end": {"type": ["string", "null"]},
    },
    "required": ["queries", "reporting_start", "reporting_end"],
}

_RELEVANCE_PROMPT = register_narrative_prompt(
    "Source relevance", "services/zopedia_research.py",
    "Organize the candidate sources around the research question. Rank useful direct evidence, indirect connections, "
    "competing explanations and background by their expected research value. Distinguish original reports from generated analysis and retained knowledge. "
    "Use actual chunk_record_id values in ordered_source_ids. Preserve broad recall; this ranking does not discard sources. "
    "Briefly identify useful connections in connections. Do not decide the final answer from previews alone.",
    group="Research flow", stage="relevance",
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


def _dedupe_text_items(items: list[str], *, limit: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        text = _clean(raw)
        if not text:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if limit is not None and len(out) >= max(int(limit), 1):
            break
    return out


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


def _symbol_news_max_age_days_for_live_event() -> int:
    raw = _clean(os.getenv("ATTENTION_SYMBOL_NEWS_MAX_AGE_DAYS"))
    if raw:
        try:
            return max(int(raw), 1)
        except Exception:
            pass
    return 7


def _run_with_timeout(label: str, timeout_seconds: int, func: Any, fallback: Any) -> tuple[Any, str]:
    """Run a helper in a daemon thread and return a fallback if it exceeds its budget."""
    try:
        timeout = remaining_request_timeout(max(float(timeout_seconds), 0))
    except TimeoutError:
        return fallback, f"{label} skipped: caller budget expired"
    if timeout <= 0:
        return fallback, f"{label} skipped: caller budget expired"
    work = WorkScope(time.monotonic() + timeout)
    context = copy_context()
    result_box: list[Any] = [fallback]
    error_box: list[BaseException | None] = [None]

    def _runner() -> None:
        try:
            with work.activate(), llm_request_deadline(work.deadline):
                result_box[0] = func()
        except BaseException as exc:
            error_box[0] = exc

    thread = threading.Thread(target=lambda: context.run(_runner), name=f"zopedia-live-{label[:32]}", daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        work.cancel()
        return fallback, f"{label} timed out after {timeout}s"
    if error_box[0] is not None:
        exc = error_box[0]
        return fallback, f"{label} failed: {type(exc).__name__}"
    return result_box[0], ""


def _remaining_budget(deadline: float) -> int:
    return max(int(deadline - time.monotonic()), 0)


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
        from .aql_zopedia_engine import run_aql_zopedia_structured_agent

        engine_operation = run_aql_zopedia_structured_agent(
            query=json.dumps({"query": query}, ensure_ascii=False),
            instructions=f"You are a market-theme classifier for a financial research platform. Given a user query, classify it and infer the directional impact on financial markets. event_type must be one of: oil, rates, defensives, risk, squeeze, earnings, sector, generic. Use 'rates' for interest rates, inflation, central banks, CPI, PPI, PCE, NFP, or any economic data release. Use 'oil' for energy, commodities, or geopolitical supply risk. Use 'defensives' for gold, safe havens, volatility, or risk-off moves. Use 'squeeze' for short squeezes, gamma squeezes, or heavily-shorted stock moves. Use 'earnings' for earnings reports, beats, misses, or guidance. Use 'sector' for sector rotation, industry-specific trends, or thematic moves. Use 'risk' for broad equity risk, growth scares, or systemic market moves. Use 'generic' only when nothing else fits. direction is 'up' for stress/supply-risk/hawkish reads, 'down' for relief/dovish/risk-on reads. evidence_needed is true when the query requires fresh external data to answer (news, macro releases, live prices, recent events). False for simple ticker lookups or questions answerable from static knowledge. search_keywords: extract the key domain-specific phrases from the query that should be preserved in any search (e.g. 'short squeeze', 'earnings miss', 'tariff impact'). Do not generalize. relevant_symbols: the actual US-listed tickers most directly impacted by the event described. Use the specific stocks/ETFs the query is about. When the user asks for market impact rather than one ticker, return a compact cross-asset evidence basket: broad benchmarks, directly exposed instruments, and second-order sector or style proxies that would test spillovers beyond the original asset class. For a rates or bond-market shock, do not return only bond instruments; include enough cross-asset proxies to test whether equities, style, or sectors are moving too. For interest-rate shocks, the basket must cover the direct rate instrument, a broad equity benchmark, a duration/growth proxy, and a credit/financial or balance-sheet-sensitive proxy when liquid listed instruments exist. Do not return symbols only because they are popular; each symbol needs a role tied to the query. Return at most {max_symbols} symbols.",
            schema_name="query_market_intent",
            schema=_QUERY_INTENT_SCHEMA,
            task="query_market_intent", stage='search',
            surface="services.zopedia_research",
            llm_client=llm_client,
            max_tool_calls=0,
            persist_findings=False,
            write_policy="none",
            call_type="formatter_over_aql",
            operation_metadata=None,
            run_timeout_seconds=None,
        )
        if engine_operation.get("status") != "completed":
            raise RuntimeError(
                engine_operation.get("error") or "Shared AI operation failed"
            )
        data = engine_operation["payload"]
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
    safe_limit = _safe_int(
        max_items if max_items != 5 else default_items, default_items, minimum=1, maximum=12
    )
    scoped_symbols = _symbol_list(focus_symbols)
    if scoped_symbols:
        resolution = {
            "intent": "scoped_symbol_context",
            "search_results": [
                {
                    "kind": "symbol",
                    "ref": symbol,
                    "label": symbol,
                    "score": 1.0,
                    "symbol": symbol,
                }
                for symbol in scoped_symbols
            ],
        }
    else:
        resolution = resolve_zopedia(
            query=query, preferred_mode="search", force_refresh=force_refresh, layer=resolved_layer
        )
    impact = {"focus_symbols": scoped_symbols, "search_keywords": []}
    rows: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, str]] = set()
    search_results = list(resolution.get("search_results") or [])

    for result in search_results[:safe_limit]:
        kind = _clean(result.get("kind")).lower()
        if kind == "bundle":
            bundle_id = _clean(result.get("bundle_id") or result.get("ref"))
            if not bundle_id or (kind, bundle_id) in seen_refs:
                continue
            result_symbols = set(_symbol_list(result.get("symbols")))
            ref_symbol = (
                _normalize_symbol(bundle_id.split("::", 1)[1])
                if bundle_id.lower().startswith("symbol::")
                else ""
            )
            if scoped_symbols and not (
                result_symbols.intersection(scoped_symbols) or ref_symbol in scoped_symbols
            ):
                continue
            seen_refs.add((kind, bundle_id))
            payload = resolved_layer.resolve_attention_research_bundle(
                bundle_id, force_refresh=force_refresh
            ).payload
            payload = payload if isinstance(payload, dict) else {}
            rows.append(
                {
                    "kind": "bundle",
                    "ref": bundle_id,
                    "label": _clean(result.get("label")) or bundle_id,
                    "summary_text": _bundle_summary(payload)
                    or _trim(result.get("subtitle"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
                    "source_line": _clean(payload.get("source_summary") or payload.get("source_line")),
                    "supporting_symbols": ", ".join(
                        _symbol_list(
                            payload.get("related_symbols")
                            if isinstance(payload.get("related_symbols"), list)
                            else result.get("symbols")
                        )[:4]
                    ),
                }
            )
        elif kind == "symbol":
            symbol = _normalize_symbol(result.get("symbol") or result.get("ref"))
            if not symbol or (kind, symbol) in seen_refs:
                continue
            if scoped_symbols and symbol not in scoped_symbols:
                continue
            seen_refs.add((kind, symbol))
            payload = resolved_layer.resolve_attention_ticker_background(
                symbol, force_refresh=force_refresh
            ).payload
            payload = payload if isinstance(payload, dict) else {}
            rows.append(
                {
                    "kind": "symbol",
                    "ref": symbol,
                    "label": symbol,
                    "summary_text": _background_summary(payload),
                    "company_name": _clean(payload.get("company_name"))
                    or _asset_name(resolved_layer, symbol, force_refresh=force_refresh),
                    "source_line": _clean(
                        payload.get("llm_source_line")
                        or payload.get("source_line")
                        or payload.get("source_summary")
                    ),
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
                    "summary_text": _trim(
                        result.get("subtitle"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))
                    ),
                    "company_name": "",
                    "source_line": "Macro release context",
                }
            )

    adjacent_symbols = [
        symbol
        for symbol in list(impact.get("focus_symbols") or [])
        if _normalize_symbol(symbol) and _normalize_symbol(symbol) not in scoped_symbols
    ][:4]

    for symbol in list(dict.fromkeys(scoped_symbols + adjacent_symbols)):
        if len(rows) >= safe_limit:
            break
        if ("symbol", symbol) in seen_refs:
            continue
        payload = resolved_layer.resolve_attention_ticker_background(
            symbol, force_refresh=force_refresh
        ).payload
        payload = payload if isinstance(payload, dict) else {}
        rows.append(
            {
                "kind": "symbol" if symbol in scoped_symbols else "adjacent_symbol",
                "ref": symbol,
                "label": symbol,
                "relationship_to_query": ("primary" if symbol in scoped_symbols else "adjacent_context"),
                "summary_text": _background_summary(payload),
                "company_name": _clean(payload.get("company_name"))
                or _asset_name(resolved_layer, symbol, force_refresh=force_refresh),
                "source_line": _clean(
                    payload.get("llm_source_line")
                    or payload.get("source_line")
                    or payload.get("source_summary")
                ),
            }
        )
        seen_refs.add(("symbol", symbol))

    llm_lines = [f"Retained context matched {len(rows)} item(s)."]
    if scoped_symbols:
        llm_lines.append("Primary company symbols: " + ", ".join(scoped_symbols) + ".")
    if adjacent_symbols:
        llm_lines.append(
            "Adjacent recall to organize around the primary company: " + ", ".join(adjacent_symbols) + "."
        )
    elif impact.get("focus_symbols"):
        llm_lines.append(
            "Possible follow-up symbols: " + ", ".join(list(impact.get("focus_symbols") or [])[:6]) + "."
        )
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
        "focus_symbols": list(dict.fromkeys(scoped_symbols + list(impact.get("focus_symbols") or [])))[:6],
        "primary_symbols": scoped_symbols,
        "adjacent_context": {
            "symbols": adjacent_symbols,
            "theme": _clean(impact.get("theme")),
            "expected_direction": _clean(impact.get("expected_direction")),
            "search_keywords": list(impact.get("search_keywords") or [])[:8],
            "note": "Adjacent context is useful recall; organize it around the primary company before using it in synthesis.",
        },
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
                "summary_text": summary or headline,
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
    normalized_query = _clean(query)
    asof_ts = pd.Timestamp.utcnow()
    default_results = int(get_config_param(_P_LIVE_EVENT_MAX_RESULTS))
    safe_limit = _safe_int(
        max_results if max_results != 6 else default_results, default_results, minimum=1, maximum=15
    )
    max_sym = int(get_config_param(_P_LIVE_EVENT_MAX_SYMBOLS))
    total_budget_seconds = _safe_int(
        get_config_param(_P_LIVE_EVENT_TOTAL_BUDGET_SECONDS),
        18,
        minimum=6,
        maximum=900,
    )
    helper_timeout_seconds = _safe_int(
        get_config_param(_P_LIVE_EVENT_LLM_HELPER_TIMEOUT_SECONDS),
        4,
        minimum=1,
        maximum=120,
    )
    provider_timeout_seconds = _safe_int(
        get_config_param(_P_LIVE_EVENT_PROVIDER_TIMEOUT_SECONDS),
        6,
        minimum=3,
        maximum=180,
    )
    deadline = time.monotonic() + remaining_request_timeout(total_budget_seconds)
    messages: list[str] = []

    selected_symbols = _symbol_list(focus_symbols)
    direct_query_intent = {
        "query": normalized_query,
        "theme": "",
        "expected_direction": "",
        "evidence_needed": True,
        "search_keywords": [normalized_query] if normalized_query else [],
        "focus_symbols": selected_symbols,
        "summary": [],
        "llm_context_text": "",
    }
    # Acquisition executes the planner's query. It does not run hidden market
    # classifiers or reinterpret model expectations as observed price changes.
    impact = direct_query_intent
    theme = ""
    direction = ""
    explicitly_resolved = bool(selected_symbols)
    selected_symbols = selected_symbols[:max_sym]

    rows: list[dict[str, Any]] = []
    if impact.get("evidence_needed", True):
        event_payload, event_warning = _run_with_timeout(
            "live_event_news_search",
            max(min(_remaining_budget(deadline), provider_timeout_seconds * 2 + 3), 1),
            lambda: search_market_event_news_payload(
                {
                    "event_type": theme,
                    "anchor_direction": direction,
                    "supporting_symbols": selected_symbols,
                    # Pass the original query as event_title so search preserves
                    # the user's actual phrasing instead of category labels.
                    "event_title": normalized_query,
                },
                max_results=max(min(safe_limit, 6), 3),
                serp_client=None,
                serper_client=None,
                tavily_client=None,
                search_query=normalized_query,
                allow_llm_query=False,
            ),
            {
                "articles": pd.DataFrame(),
                "messages": ["Live event news search did not finish within budget."],
            },
        )
        if event_warning:
            messages.append(event_warning)
        if isinstance(event_payload, dict):
            messages.extend(
                [_clean(item) for item in list(event_payload.get("messages") or []) if _clean(item)]
            )
        rows.extend(_news_rows_from_payload(event_payload, scope="market_event"))

    event_rows_are_sufficient = len(rows) >= min(max(safe_limit // 2, 3), safe_limit)
    if len(rows) >= safe_limit or event_rows_are_sufficient:
        symbol_search_limit = 0
    else:
        symbol_search_limit = 4 if explicitly_resolved else 2
    # Only run per-symbol news search for symbols that came from the user or resolved
    # Search per-symbol news for explicitly resolved symbols (up to 4) or
    # intent-classified symbols (up to 2) since the classifier now returns
    # actual affected tickers rather than generic proxies.
    for symbol in selected_symbols[:symbol_search_limit]:
        remaining = _remaining_budget(deadline)
        if remaining <= max(provider_timeout_seconds, 3):
            messages.append(
                "Skipped remaining symbol news searches because the live-event budget was exhausted."
            )
            break
        company_name, asset_warning = _run_with_timeout(
            f"asset_name_{symbol}",
            min(helper_timeout_seconds, max(_remaining_budget(deadline), 1)),
            lambda symbol=symbol: _asset_name(resolved_layer, symbol, force_refresh=force_refresh),
            "",
        )
        if asset_warning:
            messages.append(asset_warning)
        payload, symbol_warning = _run_with_timeout(
            f"symbol_news_{symbol}",
            max(min(remaining, provider_timeout_seconds + 3), 1),
            lambda symbol=symbol, company_name=company_name: search_symbol_news_payload(
                symbol,
                company_name=company_name,
                max_results=max(min(safe_limit, 6), 3),
                serp_client=None,
                tavily_client=None,
                llm_client=None,
            ),
            {
                "articles": pd.DataFrame(),
                "messages": [f"Symbol news search for {symbol} did not finish within budget."],
            },
        )
        if symbol_warning:
            messages.append(symbol_warning)
        if isinstance(payload, dict):
            messages.extend([_clean(item) for item in list(payload.get("messages") or []) if _clean(item)])
        rows.extend(_news_rows_from_payload(payload, symbol=symbol, scope="symbol"))

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if not is_recent_for_attention(
            row.get("published_at"),
            asof_time_utc=asof_ts,
            max_age_days=_symbol_news_max_age_days_for_live_event(),
            include_undated=True,
        ):
            continue
        key = (
            _clean(row.get("url")).lower(),
            _clean(row.get("headline")).lower(),
            _clean(row.get("symbol")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    ordered = deduped[:safe_limit]
    llm_lines = [
        "Live evidence rows are provider headlines/snippets with source URLs, not opened article bodies.",
        "These are source reports. They do not establish a causal ranking.",
    ]
    if selected_symbols:
        llm_lines.append("Focused symbols: " + ", ".join(selected_symbols) + ".")
    if messages:
        llm_lines.append("Live evidence warnings: " + "; ".join(_dedupe_text_items(messages, limit=4)) + ".")
    for row in ordered[:6]:
        label = _clean(row.get("symbol")) or _clean(row.get("scope"))
        source = _clean(row.get("source"))
        published_at = _clean(row.get("published_at"))
        headline = _clean(row.get("headline"))
        summary_text = _clean(row.get("summary_text"))
        url = _clean(row.get("url"))
        line = f"{label}: {headline}"
        if summary_text:
            line += f" | snippet={summary_text}"
        if source:
            line += f" | source={source}"
        if published_at:
            line += f" | published_at={published_at}"
        if url:
            line += f" | url={url}"
        llm_lines.append(line)

    return {
        "query": normalized_query,
        "theme": theme,
        "expected_direction": direction,
        "focus_symbols": selected_symbols,
        "summary": ordered,
        "candidate_sources": [
            {
                **row,
                "chunk_record_id": "news_"
                + hashlib.sha256(
                    (row["url"] + "\n" + row["headline"]).encode()
                ).hexdigest()[:20],
                "canonical_document_id": "url_"
                + hashlib.sha256(row["url"].encode()).hexdigest()[:20],
                "title": row["headline"],
                "chunk_text": "\n\n".join(
                    dict.fromkeys([row["headline"], row["summary_text"]])
                ),
                "source_kind": "provider_snippet",
            }
            for row in ordered
        ],
        "llm_context_text": " ".join(item for item in llm_lines if item),
        "messages": _dedupe_text_items(messages, limit=8),
    }


def search_evidence(
    *,
    query: str,
    tickers: list[str] | None = None,
    max_results: int = 10,
    queries: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    asof_time_utc: str | None = None,
    timezone: str = "UTC",
    strict_tickers: bool = False,
    original_query: str = "",
    resolved_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Interpret retrieval intent, then acquire broad semantic and dated candidates."""
    remaining_request_timeout(0)
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    from datetime import datetime, time as day_time, timedelta, timezone as utc_timezone
    from zoneinfo import ZoneInfo
    from .saa import search_retained_evidence_chunks

    normalized_query = str(query or "").strip()
    safe_limit = _safe_int(max_results, 10, minimum=1, maximum=30)
    query_limit = int(get_config_param(SEARCH_QUERIES))
    result_limit = int(get_config_param(SEARCH_RESULTS))
    parallel_queries = int(get_config_param(SEARCH_PARALLEL))
    supplied_searches = list(
        dict.fromkeys([normalized_query, *[str(item).strip() for item in queries or [] if str(item).strip()]])
    )[:query_limit]
    plan = {}
    warnings = []
    if resolved_plan is not None:
        from jsonschema import Draft202012Validator
        schema = {**_RETAINED_SEARCH_PLAN_SCHEMA, "properties": {
            **_RETAINED_SEARCH_PLAN_SCHEMA["properties"],
            "queries": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1, "maxItems": query_limit},
        }}
        errors = list(Draft202012Validator(schema).iter_errors(resolved_plan))
        if errors or any(not text.strip() for text in resolved_plan.get("queries", [])):
            raise ValueError("Invalid resolved search plan: " + (errors[0].message if errors else "Empty query"))
        plan = resolved_plan
    elif normalized_query or queries:
        try:
            from .aql_zopedia_engine import run_aql_zopedia_structured_agent
            llm = load_aql_zopedia_llm_client(surface="research.search_evidence")

            engine_operation = run_aql_zopedia_structured_agent(
                query=json.dumps(
                    {
                        "original_query": original_query or normalized_query,
                        "search_focus": normalized_query,
                        "query_hints": supplied_searches,
                        "asof_time_utc": asof_time_utc,
                        "timezone": timezone,
                        "explicit_start": start_date,
                        "explicit_end": end_date,
                    },
                    ensure_ascii=False,
                ),
                instructions=get_prompt(_RETAINED_SEARCH_PLAN_PROMPT),
                schema_name="retained_evidence_search_plan",
                schema={**_RETAINED_SEARCH_PLAN_SCHEMA, "properties": {**_RETAINED_SEARCH_PLAN_SCHEMA["properties"], "queries": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": query_limit}}},
                task="retrieval.plan",
                stage="search",
                surface="research.search_evidence",
                llm_client=llm,
                max_tool_calls=0,
                persist_findings=False,
                write_policy="none",
                call_type="research_grade",
                operation_metadata=None,
            )
            if engine_operation.get("status") != "completed":
                raise RuntimeError(
                    engine_operation.get("error") or "Shared AI operation failed"
                )
            plan = engine_operation["payload"]
        except Exception as exc:
            remaining_request_timeout(0)
            warnings.append(
                f"Search interpretation unavailable: {type(exc).__name__}; using supplied queries."
            )
    searches = list(dict.fromkeys(plan["queries"] if resolved_plan is not None else [normalized_query, *plan.get("queries", []), *supplied_searches]))[:query_limit] or [
        ""
    ]
    if resolved_plan is not None:
        start_date, end_date = plan["reporting_start"], plan["reporting_end"]
    else:
        start_date = plan.get("reporting_start") or start_date
        end_date = plan.get("reporting_end") or end_date

    def date_boundary(value, *, end=False):
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if len(str(value)) == 10:
            parsed = datetime.combine(parsed.date(), day_time(), tzinfo=ZoneInfo(timezone))
            if end:
                parsed += timedelta(days=1, microseconds=-1)
        elif parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
        return parsed.astimezone(utc_timezone.utc).isoformat()

    start_date, end_date = date_boundary(start_date), date_boundary(end_date, end=True)
    symbols = _symbol_list(tickers)
    requests = [
        (text, symbols if strict_tickers else None, dated)
        for text in searches
        for dated in ([True, False] if start_date or end_date else [False])
    ]
    if symbols and not strict_tickers:
        requests.append((normalized_query, symbols, False))

    def search(request):
        remaining_request_timeout(0)
        text, scoped_symbols, publication_only = request
        found = search_retained_evidence_chunks(
            query=text,
            tickers=scoped_symbols,
            start_date=start_date if publication_only else None,
            end_date=end_date if publication_only else None,
            limit=result_limit,
            use_semantic=True,
            include_source_references=False,
            publication_dates_only=publication_only,
        )
        found["_candidate_rank"] = range(len(found))
        return found

    with ThreadPoolExecutor(max_workers=min(len(requests), parallel_queries) or 1) as pool:
        futures = [pool.submit(copy_context().run, search, request) for request in requests]
        frames = []
        for request, future in zip(requests, futures):
            try:
                frames.append(future.result())
            except Exception as exc:
                remaining_request_timeout(0)
                warnings.append(f"Search failed for {request[0]!r}: {type(exc).__name__}")
    if not frames and requests:
        raise RuntimeError("Retained retrieval failed: " + "; ".join(warnings))
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if start_date or end_date:
        # A dated request also gets a broad view of reporting in that period.
        # Discovery must not depend on already guessing the event's vocabulary.
        recent = search_retained_evidence_chunks(
            query="",
            source_kinds=["news", "search", "sec", "treasury"],
            start_date=start_date,
            end_date=end_date or asof_time_utc,
            publication_dates_only=True,
            limit=100,
            use_semantic=False,
            include_source_references=False,
        )
    else:
        recent = pd.DataFrame()
    rows = []
    if not frame.empty or not recent.empty:
        # Interleave actual ranks before deduplication: result lists have
        # different lengths, and broad searches must survive scoped hints.
        if not frame.empty:
            frame = frame.sort_values("_candidate_rank", kind="stable")
            frame = frame.drop_duplicates("chunk_identity_sha256")
        frame = pd.concat([frame, recent], ignore_index=True).drop_duplicates("chunk_identity_sha256")
        for _, row in frame.iterrows():
            refs = row.get("source_refs") if isinstance(row.get("source_refs"), list) else []
            rows.append(
                {
                    "chunk_record_id": _clean(row.get("chunk_record_id")),
                    "canonical_document_id": _clean(row.get("canonical_document_id")),
                    "document_id": _clean(row.get("document_id")),
                    "chunk_id": _clean(row.get("chunk_id")),
                    "page_id": _clean(row.get("page_id")),
                    "title": _clean(row.get("title")),
                    "chunk_text": str(row.get("chunk_text") or "").strip()[:400],
                    "text_coverage": {"kind": "preview", "start": 0, "end": min(400, len(str(row.get("chunk_text") or ""))), "total_characters": len(str(row.get("chunk_text") or "")), "read_tool": "research.read_evidence"},
                    "source": _clean(row.get("source_provider")),
                    "source_kind": _clean(row.get("source_kind")),
                    "evidence_type": _clean(row.get("evidence_type")),
                    "source_refs": refs,
                    "source_references_loaded": bool(row.get("source_references_loaded", True)),
                    "source_reference_resource": {"tool": "research.read_evidence", "arguments": {
                        "chunk_record_id": _clean(row.get("chunk_record_id")), "include_source_references": True,
                    }},
                    "published_date": _clean(row.get("published_date")),
                    "published_at": "" if pd.isna(row.get("published_at")) else str(row.get("published_at")),
                    "retained_at": str(row.get("asof_time_utc") or ""),
                    "url": _clean(row.get("canonical_url")),
                }
            )
    return {
        "query": normalized_query,
        "queries": searches,
        "relevance": {"status": "not_run"},
        "retrieval_modes": [frame.attrs.get("retrieval", {}) for frame in frames],
        "workload": {"query_limit": query_limit, "results_per_query": result_limit, "parallel_queries": parallel_queries},
        "asof_time_utc": asof_time_utc,
        "timezone": timezone,
        "reporting_window": {"start": start_date, "end": end_date},
        "warnings": warnings,
        "result_count": min(len(rows), safe_limit),
        "summary": rows[:safe_limit],
        "candidate_sources": rows,
        "llm_context_text": json.dumps(rows, ensure_ascii=False),
        "evidence_context_text": json.dumps(rows, ensure_ascii=False),
    }


def read_evidence(*, chunk_record_id: str, include_source_references: bool = False) -> dict[str, Any]:
    from .saa.storage import load_retained_evidence_chunk, load_retained_document_metadata

    row = load_retained_evidence_chunk(chunk_record_id, include_source_references=include_source_references)
    if not row:
        return {"summary": [], "llm_context_text": "The retained source is unavailable."}
    doc = load_retained_document_metadata(row.get("canonical_document_id") or "", include_source_references=False) or {}
    source = {
        "chunk_record_id": chunk_record_id,
        "canonical_document_id": row.get("canonical_document_id"),
        "title": row.get("title"),
        "chunk_text": row.get("chunk_text"),
        "source_kind": row.get("source_kind"),
        "evidence_type": row.get("evidence_type"),
        "source_refs": row.get("source_refs") or [],
        "source_references_loaded": include_source_references,
        "source_reference_resource": {"tool": "research.read_evidence", "arguments": {
            "chunk_record_id": chunk_record_id, "include_source_references": True,
        }},
        "page_id": row.get("page_id"),
        "url": doc.get("canonical_url") or row.get("canonical_url"),
        "published_date": row.get("published_date"),
        "retained_at": str(row.get("asof_time_utc") or ""),
        "text_coverage": {"kind": "complete_chunk", "start": 0, "end": len(str(row.get("chunk_text") or "")), "total_characters": len(str(row.get("chunk_text") or ""))},
    }
    return {
        "summary": [source],
        "status": "completed",
        "candidate_sources": [source],
        "llm_context_text": json.dumps(source, ensure_ascii=False, default=str),
    }


def open_page(
    *,
    url: str,
    max_chars: int | None = None,
    require_main_content: bool = False,
    min_text_chars: int = 500,
    capture_id: str = "",
    offset: int = 0,
) -> dict[str, Any]:
    from .saa.storage import load_web_capture, retain_web_capture
    safe_max_chars = _safe_int(max_chars or get_config_param(_P_READ_WINDOW), 5000, minimum=800, maximum=100000)
    safe_min_text_chars = _safe_int(min_text_chars, 500, minimum=1, maximum=5000)
    offset = max(0, int(offset))
    capture_limit = int(get_config_param(_P_CAPTURE_LIMIT))
    if capture_id:
        retained = load_web_capture(capture_id)
        capture_metadata = retained["capture_metadata"]
        page = {**retained, "mode": "retained_capture", "warning": capture_metadata.get("warning"), "quality_issue": capture_metadata.get("quality_issue")}
        capture = {"status": "retained", "capture_id": capture_id, "chunk_record_id": capture_id, "canonical_document_id": retained.get("canonical_document_id"), "captured_at": retained.get("captured_at")}
    else:
        page = browse_page(url, max_text_chars=capture_limit, require_main_content=bool(require_main_content), min_text_chars=safe_min_text_chars)
        capture_metadata = {"capture_limit_reached": len(str(page.get("text") or "")) >= capture_limit, "mode": page.get("mode"), "warning": page.get("warning"), "quality_issue": page.get("quality_issue")}
        try:
            capture = retain_web_capture(url=page.get("final_url") or url, title=page.get("title") or "", text=str(page.get("text") or ""), metadata=capture_metadata)
        except Exception as exc:
            remaining_request_timeout(0)
            capture = {"status": "unavailable", "reason": type(exc).__name__}
    summary_row = {
        "url": _clean(page.get("final_url") or page.get("url")),
        "title": _clean(page.get("title")),
        "mode": _clean(page.get("mode")),
        "excerpt": _trim(page.get("excerpt"), limit=260),
        "warning": _clean(page.get("warning")),
        "quality_issue": _clean(page.get("quality_issue")),
    }
    llm_lines = [
        f"Opened page via {summary_row['mode'] or 'unknown'}.",
        f"Title: {summary_row['title'] or summary_row['url']}.",
    ]
    if summary_row["warning"]:
        llm_lines.append(f"Warning: {summary_row['warning']}.")
    if summary_row["quality_issue"]:
        llm_lines.append(f"Quality issue: {summary_row['quality_issue']}.")
    if summary_row["excerpt"]:
        llm_lines.append(f"Excerpt: {summary_row['excerpt']}.")
    original = str(page.get("text") or "").strip()
    text = original[offset:offset + safe_max_chars]
    coverage = {"start": offset, "end": offset + len(text), "total_characters": len(original), "has_more": offset + len(text) < len(original), "capture_limit_reached": capture_metadata.get("capture_limit_reached", False)}
    summary_row.update(capture=capture, text_coverage=coverage)
    first_pass = {"status": "not_run"}
    if text:
        llm_lines.append(f"Visible text: {text}")
    return {
        "url": summary_row["url"],
        "summary": [summary_row],
        "page_text": text,
        "capture": capture,
        "text_coverage": coverage,
        "first_pass_summary": first_pass,
        "candidate_sources": [{**summary_row, **capture, "chunk_text": text, "first_pass_summary": first_pass, "source_kind": "web", "evidence_type": "source_capture"}],
        "unretained_capture_text": original if capture["status"] != "retained" else "",
        "llm_context_text": " ".join(item for item in llm_lines if item),
    }


def rank_evidence_candidates(payload: dict[str, Any], *, question: str, llm_client: Any) -> dict[str, Any]:
    """Add a relevance view after the executor has saved the acquired candidates."""
    from .aql_zopedia_engine import run_aql_zopedia_structured_agent

    rows = list(payload.get("candidate_sources") or [])
    if not rows:
        return payload
    operation = run_aql_zopedia_structured_agent(
        query=json.dumps({"question": question, "sources": rows}, ensure_ascii=False),
        instructions=get_prompt(_RELEVANCE_PROMPT), stage="relevance",
        schema_name="source_relevance",
        schema={"type": "object", "properties": {"ordered_source_ids": {"type": "array", "items": {"type": "string"}}, "connections": {"type": "array", "items": {"type": "string"}}}, "required": ["ordered_source_ids", "connections"], "additionalProperties": False},
        task="retrieval.relevance", surface="research.search_evidence", max_tool_calls=0,
        llm_client=llm_client, persist_findings=False, write_policy="none", call_type="research_grade",
    )
    relevance = {"status": operation["status"], **(operation.get("payload") or {})}
    if operation.get("status") == "completed":
        order = {key: i for i, key in enumerate(relevance["ordered_source_ids"])}
        rows = sorted(rows, key=lambda source: order.get(source["chunk_record_id"], len(order)))
    else:
        relevance["reason"] = operation.get("error")
    context = json.dumps({"sources": rows, "relevance": relevance}, ensure_ascii=False)
    return {**payload, "relevance": relevance, "candidate_sources": rows,
            "summary": rows[:payload["result_count"]], "llm_context_text": context,
            "evidence_context_text": context}


def summarize_source_capture(payload: dict[str, Any], *, llm_client: Any) -> dict[str, Any]:
    """Add a first-pass view without changing the captured original or its coverage."""
    from .aql_zopedia_engine import run_aql_zopedia_structured_agent

    text = payload.get("page_text") or ""
    if not text:
        return payload
    operation = run_aql_zopedia_structured_agent(
        query=json.dumps({"source": (payload.get("summary") or [{}])[0], "original_text": text}, ensure_ascii=False),
        instructions=get_prompt(_SOURCE_SUMMARY_PROMPT), stage="summary", schema_name="source_first_pass",
        schema={"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": False},
        task="source.summary", surface="research.open_page", max_tool_calls=0,
        llm_client=llm_client, persist_findings=False, write_policy="none", call_type="utility",
    )
    first_pass = {"status": operation["status"], **(operation.get("payload") or {})}
    if operation.get("status") != "completed":
        first_pass["reason"] = operation.get("error")
    return {**payload, "first_pass_summary": first_pass,
            "candidate_sources": [{**source, "first_pass_summary": first_pass}
                                  for source in payload.get("candidate_sources") or []]}


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
                "evidence_type": "retained_knowledge",
                "source_kind": "zopedia_memory",
                "source_refs": [{"url": url} for url in source_urls],
                "source_version": row.get("version"),
                "retained_at": str(row.get("updated_at_utc") or ""),
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
        page_id = _clean(row.get("page_id"))
        page_type = _clean(row.get("page_type"))
        title = _clean(row.get("title"))
        summary = _clean(row.get("summary_text"))
        line = f"{title}"
        if page_id:
            line += f" (page_id={page_id})"
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


def zopedia_read_page(*, page_id: str, offset: int = 0, max_chars: int = 20000) -> dict[str, Any]:
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
        "evidence_type": "retained_knowledge",
        "source_kind": "zopedia_memory",
        "source_refs": [{"url": url} for url in source_urls]
        + [{"canonical_document_id": doc} for doc in page.get("source_document_ids") or []],
        "source_version": page.get("version"),
        "retained_at": str(page.get("updated_at_utc") or ""),
        "title": _clean(page.get("title")),
        "summary_text": _trim(page.get("summary"), limit=int(get_config_param(_P_SUMMARY_TRIM_LIMIT))),
        "source_urls": source_urls,
        "url": source_urls[0] if source_urls else "",
        "entity_refs": list(page.get("entity_refs") or [])[:12],
        "outgoing_links": list(page.get("outgoing_links") or [])[:12],
    }
    full_body = str(page.get("body_markdown") or "").strip()
    offset = max(int(offset), 0)
    max_chars = max(1000, min(int(max_chars), 100000))
    body = full_body[offset : offset + max_chars]
    row.update(
        {
            "body_chars": len(full_body),
            "offset": offset,
            "next_offset": offset + len(body) if offset + len(body) < len(full_body) else None,
        }
    )
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
        "llm_context_text": "\n\n".join(item for item in lines if item),
        "evidence_context_text": json.dumps({**row, "body_markdown": body}, ensure_ascii=False, default=str),
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
