from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .common.news_freshness import coerce_article_published_at
from .llm import LLMAPIError
from .web_research import SerperSearchClient, SerpAPISearchClient, TavilySearchClient, search_client_provider
from .aql_zopedia_engine import load_aql_zopedia_llm_client


_EVENT_SEARCH_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "search_query": {"type": "string"},
    },
    "required": ["search_query"],
}


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _normalize_symbol(symbol: object) -> str:
    return _coerce_text(symbol).upper()


def _to_article_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["headline", "summary", "description", "source", "published_at", "url"])
    frame = pd.DataFrame(rows)
    for column in ["headline", "summary", "description", "source", "url"]:
        if column not in frame.columns:
            frame[column] = ""
    frame["published_at"] = frame.apply(
        lambda row: coerce_article_published_at(row.get("published_at"), url=row.get("url")),
        axis=1,
    )
    frame = frame.dropna(subset=["headline"]).copy()
    if frame.empty:
        return pd.DataFrame(columns=["headline", "summary", "description", "source", "published_at", "url"])
    frame = frame.sort_values("published_at", ascending=False, na_position="last")
    return frame.drop_duplicates(subset=["headline", "url"], keep="first").reset_index(drop=True)




def _event_search_query(event: dict[str, Any]) -> str:
    theme = _coerce_text(event.get("event_type")).lower() or "generic"
    direction = _coerce_text(event.get("anchor_direction")).lower() or "down"
    event_title = _coerce_text(event.get("event_title"))
    anchor_symbol = _normalize_symbol(event.get("anchor_symbol"))
    supporting_symbols = [
        _normalize_symbol(symbol)
        for symbol in list(event.get("supporting_symbols") or [])
        if _normalize_symbol(symbol)
    ]
    llm_client = load_aql_zopedia_llm_client(surface="attention.live_research.event_search_query")
    if llm_client is not None:
        try:
            symbol_context = ", ".join(([anchor_symbol] if anchor_symbol else []) + supporting_symbols[:4])
            from .aql_zopedia_engine import run_aql_zopedia_structured_agent

            engine_operation = run_aql_zopedia_structured_agent(
                query=f"User's original question: {event_title}\nMarket event: {event_title or theme} ({direction})\nTheme: {theme}, Direction: {direction}\nKey symbols: {symbol_context}\nGenerate a targeted search query to find relevant financial news. Preserve the important event terms, but do not require every result to name the listed tickers if a private-company, macro, policy, or sector event plausibly explains the move.",
                instructions="You generate concise financial news search queries. Return one search query that can explain a market move. Use wider-circle market reasoning: exact companies first, then close peers, customers, suppliers, private leaders, IPO rumors, policy, rates, commodities, or sector themes when those are likely to explain the move.",
                schema_name="event_search_query",
                schema=_EVENT_SEARCH_QUERY_SCHEMA,
                task="event_search_query",
                surface="services.attention_live_research",
                llm_client=llm_client,
                max_tool_calls=0,
                persist_findings=False,
                write_policy="none",
                call_type="formatter_over_aql",
                operation_metadata=None,
            )
            if engine_operation.get("status") != "completed":
                raise RuntimeError(
                    engine_operation.get("error") or "Shared AI operation failed"
                )
            result = engine_operation["payload"]
            query = str(result.get("search_query") or "").strip()
            if query:
                return query
        except (LLMAPIError, Exception):
            pass
    parts = [anchor_symbol] if anchor_symbol else []
    parts.extend(supporting_symbols[:3])
    if event_title:
        return f"{event_title} {' '.join(parts[:2])} today".strip()
    return " ".join(part for part in parts if part) + " market move today"


def _minimal_event_search_query(event: dict[str, Any]) -> str:
    event_title = _coerce_text(event.get("event_title"))
    anchor_symbol = _normalize_symbol(event.get("anchor_symbol"))
    supporting_symbols = [
        _normalize_symbol(symbol)
        for symbol in list(event.get("supporting_symbols") or [])
        if _normalize_symbol(symbol)
    ]
    parts = [event_title] if event_title else []
    parts.extend(([anchor_symbol] if anchor_symbol else []) + supporting_symbols[:3])
    return f"{' '.join(part for part in parts if part)} today".strip() or "market news today"


def search_market_event_news_payload(
    event: dict[str, Any],
    *,
    max_results: int = 8,
    serp_client: SerpAPISearchClient | None = None,
    serper_client: SerperSearchClient | None = None,
    tavily_client: TavilySearchClient | None = None,
    search_query: str = "",
    allow_llm_query: bool = True,
) -> dict[str, Any]:
    """Collect news and general-web leads; the shared agent judges relevance."""
    if not isinstance(event, dict) or not event:
        return {"articles": pd.DataFrame(), "fallback_summary": None, "source": None, "messages": []}
    if serper_client is None and serp_client is None:
        try:
            from .aql.config import _load_search_clients
            primary, secondary = _load_search_clients()
            if search_client_provider(primary) == "serper":
                serper_client = primary
            else:
                serp_client = primary
            tavily_client = tavily_client or secondary
        except Exception:
            pass
    query = _coerce_text(search_query) or (_event_search_query(event) if allow_llm_query else _minimal_event_search_query(event))
    rows: list[dict[str, Any]] = []
    providers: list[str] = []
    errors: list[str] = []
    requests_to_make = [(provider, client, news) for provider, client in [("serpapi", serp_client), ("serper", serper_client)] if client is not None for news in (True, False)]
    if tavily_client is not None:
        requests_to_make.extend(("tavily", tavily_client, news) for news in (True, False))
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from contextvars import copy_context
    def collect(provider: str, client: Any, news: bool) -> list[Any]:
        if provider == "tavily":
            return client.search(query, max_results=max(max_results, 3), topic="news" if news else "general")
        return client.search(query, news=news, num=max(max_results, 3))
    with ThreadPoolExecutor(max_workers=min(len(requests_to_make), 4) or 1, thread_name_prefix="event-source-search") as pool:
        futures = {pool.submit(copy_context().run, collect, provider, client, news): (provider, news) for provider, client, news in requests_to_make}
        for future in as_completed(futures):
            provider, news = futures[future]
            try:
                for item in future.result():
                    title = _coerce_text(item.title)
                    if not title:
                        continue
                    rows.append({**item.to_source_record(), "search_kind": "news" if news else "web"})
                providers.append(provider)
            except Exception as exc:
                errors.append(f"{provider} {'news' if news else 'web'} search failed: {type(exc).__name__}")
    # Interleave both source types so undated web leads survive alongside news.
    frame = _to_article_frame(rows)
    if not frame.empty:
        news_rows = frame[frame['search_kind'].eq('news')].to_dict('records')
        web_rows = frame[frame['search_kind'].eq('web')].to_dict('records')
        ordered = [row for index in range(max(len(news_rows), len(web_rows))) for group in (news_rows, web_rows) if index < len(group) for row in [group[index]]]
        frame = pd.DataFrame(ordered).drop_duplicates('url').head(max(int(max_results), 1))
    return {"articles": frame, "fallback_summary": errors[0] if errors and frame.empty else None, "source": "+".join(dict.fromkeys(providers)) or None, "messages": errors, "search_query": query}


__all__ = ["search_market_event_news_payload"]
