"""
AQL collector — web search and document collection functions.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd

from ..web_research import (
    SerpAPISearchClient,
    TavilySearchClient,
    WebResearchError,
    WebSearchResult,
    load_serpapi_config,
    load_tavily_config,
)
from .constants import (
    LLMClient,
    PLANNER_SCHEMA,
    SEARCH_RELEVANCE_SCHEMA,
    SEARCH_ROUTER_SCHEMA,
)
from ._shared import (
    _coerce_float,
    _coerce_text,
    _display_excerpt,
    _evidence_text,
    _freshness_score,
    _is_irrelevant_news_text,
    _is_low_signal,
    _is_provider_error_text,
    _jaccard,
    _json_dumps,
    _latest_yield_facts,
    _normalize_symbol,
    _safe_list,
    _search_mention_score,
    _source_authority_bucket,
    _tag_tokens,
    _trim,
    _yield_context_relevant,
    _yield_fact_summary_text,
)


def _candidate_company_name(candidate: dict[str, Any]) -> str:
    for field in ["company_name", "display_name", "name"]:
        text = _coerce_text(candidate.get(field))
        if text:
            return text
    return ""


def _candidate_subject(candidate: dict[str, Any]) -> str:
    company_name = _candidate_company_name(candidate)
    symbol = _normalize_symbol(candidate.get("symbol"))
    return f"{company_name} ({symbol})".strip() if company_name and symbol else company_name or symbol


def _passes_symbol_search_gate(headline: str, snippet: str, symbol: str, company_name: str) -> bool:
    title_score = _search_mention_score(headline, symbol, company_name)
    body_score = _search_mention_score(snippet, symbol, company_name)
    combined_score = _search_mention_score(f"{headline} {snippet}", symbol, company_name)
    if max(title_score, body_score, combined_score) < 0.45:
        return False
    if title_score < 0.45 and body_score < 0.75:
        return False
    return True


def _search_result_is_relevant(
    headline: str,
    snippet: str,
    *,
    symbol: str,
    company_name: str,
) -> bool:
    if not headline and not snippet:
        return False
    if _is_irrelevant_news_text(headline, snippet):
        return False
    if _is_provider_error_text(headline) or _is_provider_error_text(snippet):
        return False
    if symbol and not _passes_symbol_search_gate(headline, snippet, symbol, company_name):
        return False
    if _is_low_signal(headline, snippet):
        if not symbol:
            return False
        if _search_mention_score(headline, symbol, company_name) < 0.75:
            return False
    return True


def _default_tavily_general_query(query: str, symbol: str, company_name: str) -> str:
    company = _coerce_text(company_name)
    subject = f"{company} ({symbol})" if company and symbol else company or symbol or query
    return f"{subject} latest developments pipeline approvals clinical trial FDA partnership guidance"


def _provider_payload_json(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    return _json_dumps(value)


def _provider_result_text(item: WebSearchResult) -> str:
    raw_text = _coerce_text(getattr(item, "raw_text", ""))
    if raw_text:
        return raw_text
    return _coerce_text(item.snippet)


def _llm_search_relevance_flags(
    *,
    query: str,
    symbol: str,
    company_name: str,
    items: list[dict[str, str]],
    llm_client: LLMClient | None,
) -> list[bool]:
    if not items:
        return []
    fallback = [
        _search_result_is_relevant(
            _coerce_text(item.get("title")),
            _coerce_text(item.get("snippet")),
            symbol=symbol,
            company_name=company_name,
        )
        for item in items
    ]
    if llm_client is None:
        return fallback
    try:
        data = llm_client.generate_json(
            system_prompt=(
                "You are a relevance gate for company news research. "
                "Select only indices that are materially relevant to the target company and likely catalysts, "
                "including trials, approvals, guidance, partnerships, product/commercial updates, management actions, "
                "or major financial/company developments. Exclude noisy insider/form-4/dividend-equivalent chatter, "
                "routine ex-dividend notices, isolated analyst target tweaks, and generic stock-up/stock-down recaps "
                "that do not identify a concrete business catalyst."
            ),
            user_prompt=json.dumps(
                {
                    "query": query,
                    "symbol": symbol,
                    "company_name": company_name,
                    "results": [
                        {
                            "index": index,
                            "title": _coerce_text(item.get("title")),
                            "snippet": _coerce_text(item.get("snippet")),
                            "source": _coerce_text(item.get("source")),
                            "url": _coerce_text(item.get("url")),
                        }
                        for index, item in enumerate(items)
                    ],
                },
                ensure_ascii=False,
                default=str,
            ),
            schema_name="attention_search_relevance",
            schema=SEARCH_RELEVANCE_SCHEMA,
        )
        selected = {
            int(value)
            for value in list(data.get("relevant_indices") or [])
            if isinstance(value, (int, float)) and 0 <= int(value) < len(items)
        }
        return [index in selected for index in range(len(items))]
    except Exception:
        return fallback


def _llm_tavily_route_decision(
    *,
    query: str,
    symbol: str,
    company_name: str,
    serp_preview: list[dict[str, str]],
    heuristic_serp_relevant: bool,
    llm_client: LLMClient | None,
) -> tuple[bool, str, str, str]:
    default_tavily_query = _default_tavily_general_query(query, symbol, company_name)
    if llm_client is None:
        if heuristic_serp_relevant:
            return False, "news", query, "serp_results_relevant"
        return True, "general", default_tavily_query, "serp_results_not_relevant"
    try:
        data = llm_client.generate_json(
            system_prompt=(
                "You are a research-router for market analysis. "
                "Decide if SerpApi results are relevant enough to explain the move. "
                "If not relevant, call Tavily as fallback using topic='general' for broader RAG retrieval. "
                "Prefer Tavily when Serp results are sparse or mostly low-signal (insider/form-4, ex-dividend, "
                "analyst target-only notes, or generic price-action recaps without concrete company catalysts). "
                "When using Tavily, output a high-recall query that includes company identity and likely catalysts."
            ),
            user_prompt=json.dumps(
                {
                    "query": query,
                    "symbol": symbol,
                    "company_name": company_name,
                    "serp_preview": serp_preview[:4],
                    "heuristic_serp_relevant": bool(heuristic_serp_relevant),
                    "policy": "Use Tavily fallback when SerpApi evidence is sparse, generic, or off-topic.",
                    "default_tavily_query": default_tavily_query,
                },
                ensure_ascii=False,
                default=str,
            ),
            schema_name="attention_search_router",
            schema=SEARCH_ROUTER_SCHEMA,
        )
        use_tavily = bool(data.get("use_tavily"))
        topic = _coerce_text(data.get("tavily_topic")).lower() or "general"
        if topic not in {"news", "general"}:
            topic = "general"
        tavily_query = _coerce_text(data.get("tavily_query")) or (default_tavily_query if topic == "general" else query)
        reason = _trim(data.get("reason"), 140)
        if not reason:
            reason = "llm_router_decision"
        return use_tavily, topic, tavily_query, reason
    except Exception:
        if heuristic_serp_relevant:
            return False, "news", query, "serp_results_relevant_fallback"
        return True, "general", default_tavily_query, "serp_results_not_relevant_fallback"


def _to_article_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["headline", "summary", "description", "source", "published_at", "url"])
    frame = pd.DataFrame(rows)
    for column in ["headline", "summary", "description", "source", "url"]:
        if column not in frame.columns:
            frame[column] = ""
    frame["published_at"] = pd.to_datetime(frame.get("published_at"), utc=True, errors="coerce")
    frame = frame.dropna(subset=["headline"]).copy()
    if frame.empty:
        return pd.DataFrame(columns=["headline", "summary", "description", "source", "published_at", "url"])
    frame = frame.sort_values("published_at", ascending=False, na_position="last")
    return frame.drop_duplicates(subset=["headline", "url"], keep="first").reset_index(drop=True)


def search_symbol_news_payload(
    symbol: str,
    *,
    company_name: str = "",
    max_results: int = 8,
    serp_client: SerpAPISearchClient | None = None,
    tavily_client: TavilySearchClient | None = None,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    normalized_symbol = _normalize_symbol(symbol)
    if not normalized_symbol:
        return {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}

    if serp_client is None:
        cfg = load_serpapi_config()
        serp_client = SerpAPISearchClient(cfg) if cfg is not None else None
    if tavily_client is None:
        cfg = load_tavily_config()
        tavily_client = TavilySearchClient(cfg) if cfg is not None else None

    query_base = f"{normalized_symbol} stock today"
    if company_name:
        query_base = f"{company_name} {normalized_symbol} stock today"

    article_rows: list[dict[str, Any]] = []
    sources: list[str] = []
    errors: list[str] = []
    serp_preview: list[dict[str, str]] = []
    serp_relevant_count = 0
    serp_candidates: list[dict[str, str]] = []

    if serp_client is not None:
        try:
            for item in serp_client.search(query_base, news=True, num=max(max_results, 3)):
                title = _coerce_text(item.title)
                snippet = _coerce_text(item.snippet)
                if not title and not snippet:
                    continue
                serp_candidates.append(
                    {
                        "title": title,
                        "snippet": snippet,
                        "provider_text": _provider_result_text(item),
                        "source": _coerce_text(item.source) or "SerpApi",
                        "url": _coerce_text(item.url),
                        "published_at": _coerce_text(item.published_at),
                        "provider_payload_json": _provider_payload_json(item.raw),
                    }
                )
            if hasattr(serp_client, "search_ai_overview"):
                try:
                    ai_overview = serp_client.search_ai_overview(query_base)  # type: ignore[attr-defined]
                except Exception:
                    ai_overview = None
                if isinstance(ai_overview, WebSearchResult):
                    serp_candidates.append(
                        {
                            "title": _coerce_text(ai_overview.title) or f"{normalized_symbol} AI Overview",
                            "snippet": _coerce_text(ai_overview.snippet),
                            "provider_text": _provider_result_text(ai_overview),
                            "source": _coerce_text(ai_overview.source) or "Google AI Overview",
                            "url": _coerce_text(ai_overview.url),
                            "published_at": _coerce_text(ai_overview.published_at),
                            "provider_payload_json": _provider_payload_json(ai_overview.raw),
                        }
                    )
            serp_flags = _llm_search_relevance_flags(
                query=query_base,
                symbol=normalized_symbol,
                company_name=company_name,
                items=serp_candidates,
                llm_client=llm_client,
            )
            for row, keep in zip(serp_candidates, serp_flags):
                serp_preview.append({"title": row.get("title", ""), "snippet": row.get("snippet", ""), "source": row.get("source", "")})
                if not bool(keep):
                    continue
                if _is_irrelevant_news_text(row.get("title"), row.get("snippet")):
                    continue
                serp_relevant_count += 1
                article_rows.append(
                    {
                        "headline": _coerce_text(row.get("title")),
                        "summary": _evidence_text(row.get("provider_text") or row.get("snippet"), row.get("title")),
                        "description": _evidence_text(row.get("provider_text") or row.get("snippet"), row.get("title")),
                        "source": _coerce_text(row.get("source")) or "SerpApi",
                        "provider": "serpapi",
                        "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
                        "url": _coerce_text(row.get("url")),
                        "provider_payload_json": _coerce_text(row.get("provider_payload_json")),
                    }
                )
            sources.append("serpapi")
        except WebResearchError as exc:
            errors.append(str(exc))

    use_tavily = bool(tavily_client is not None and serp_client is None)
    tavily_topic = "news"
    tavily_query = query_base
    if tavily_client is not None and serp_client is not None:
        use_tavily, tavily_topic, tavily_query, _ = _llm_tavily_route_decision(
            query=query_base,
            symbol=normalized_symbol,
            company_name=company_name,
            serp_preview=serp_preview,
            heuristic_serp_relevant=serp_relevant_count > 0,
            llm_client=llm_client,
        )
        if serp_relevant_count <= 0:
            use_tavily = True

    if tavily_client is not None and use_tavily:
        try:
            tavily_candidates: list[dict[str, str]] = []
            for item in tavily_client.search(tavily_query, max_results=max(max_results // 2, 3), topic=tavily_topic):
                title = _coerce_text(item.title)
                snippet = _coerce_text(item.snippet)
                if not title and not snippet:
                    continue
                tavily_candidates.append(
                    {
                        "title": title,
                        "snippet": snippet,
                        "provider_text": _provider_result_text(item),
                        "source": _coerce_text(item.source) or "Tavily",
                        "url": _coerce_text(item.url),
                        "published_at": _coerce_text(item.published_at),
                        "provider_payload_json": _provider_payload_json(item.raw),
                    }
                )
            tavily_flags = _llm_search_relevance_flags(
                query=tavily_query,
                symbol=normalized_symbol,
                company_name=company_name,
                items=tavily_candidates,
                llm_client=llm_client,
            )
            for row, keep in zip(tavily_candidates, tavily_flags):
                if not bool(keep):
                    continue
                if _is_irrelevant_news_text(row.get("title"), row.get("snippet")):
                    continue
                article_rows.append(
                    {
                        "headline": _coerce_text(row.get("title")) or f"{normalized_symbol} web result",
                        "summary": _evidence_text(row.get("provider_text") or row.get("snippet"), row.get("title")),
                        "description": _evidence_text(row.get("provider_text") or row.get("snippet"), row.get("title")),
                        "source": _coerce_text(row.get("source")) or "Tavily",
                        "provider": "tavily",
                        "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
                        "url": _coerce_text(row.get("url")),
                        "provider_payload_json": _coerce_text(row.get("provider_payload_json")),
                    }
                )
            sources.append("tavily")
        except WebResearchError as exc:
            errors.append(str(exc))

    frame = _to_article_frame(article_rows).head(max(int(max_results), 1))
    sanitized_errors = [error for error in errors if not _is_provider_error_text(error)]
    fallback_summary = sanitized_errors[0] if sanitized_errors and frame.empty else None
    return {"articles": frame, "fallback_summary": fallback_summary, "source": "+".join(sources) if sources else None}


def _search_query_results(
    query: str,
    *,
    candidate_id: str,
    symbol: str,
    company_name: str,
    run_id: str,
    asof_time_utc: pd.Timestamp,
    serp_client: SerpAPISearchClient | None,
    tavily_client: TavilySearchClient | None,
    llm_client: LLMClient | None,
    budget: int,
    include_provider_payload: bool = False,
    include_provider_text: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    request_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    query_id = f"query::{hashlib.sha1(f'{candidate_id}|{query}'.encode('utf-8')).hexdigest()[:16]}"
    normalized_symbol = _normalize_symbol(symbol)
    normalized_company = _coerce_text(company_name)
    serp_preview: list[dict[str, str]] = []
    serp_relevant_count = 0

    if serp_client is not None:
        request_rows.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "candidate_id": candidate_id,
                "query_id": query_id,
                "provider": "serpapi",
                "query": query,
                "route_mode": "primary",
            }
        )
        try:
            results = serp_client.search(query, news=True, num=max(min(int(budget), 6), 1))
        except Exception as exc:
            error_text = _trim(str(exc), 180)
            result_rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": candidate_id,
                    "query_id": query_id,
                    "provider": "serpapi",
                    "result_id": f"{query_id}::serpapi::error",
                    "title": "",
                    "url": "",
                    "snippet": "",
                    "error_text": error_text,
                    "result_kind": "error",
                    "source": "serpapi",
                    "published_at": "",
                    "authority_bucket": "web",
                    "authority_rank": 3,
                }
            )
            results = []
        serp_candidates: list[dict[str, str]] = []
        for item in list(results or [])[: max(min(int(budget), 6), 1)]:
            if not isinstance(item, WebSearchResult):
                continue
            serp_candidates.append(
                {
                    "title": _coerce_text(item.title),
                    "snippet": _coerce_text(item.snippet),
                    "provider_text": _provider_result_text(item),
                    "source": _coerce_text(item.source),
                    "url": _coerce_text(item.url),
                    "published_at": _coerce_text(item.published_at),
                    "provider_payload_json": _provider_payload_json(item.raw),
                }
            )
        serp_flags = _llm_search_relevance_flags(
            query=query,
            symbol=normalized_symbol,
            company_name=normalized_company,
            items=serp_candidates,
            llm_client=llm_client,
        )
        for row, keep in zip(serp_candidates, serp_flags):
            title = _coerce_text(row.get("title"))
            snippet = _coerce_text(row.get("snippet"))
            serp_preview.append(
                {
                    "title": title,
                    "snippet": snippet,
                    "source": _coerce_text(row.get("source")),
                }
            )
            if bool(keep) and not _is_irrelevant_news_text(title, snippet):
                serp_relevant_count += 1
            authority_bucket, authority_rank = _source_authority_bucket(row.get("source"), row.get("url"))
            result_rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": candidate_id,
                    "query_id": query_id,
                    "provider": "serpapi",
                    "result_id": f"{query_id}::serpapi::{hashlib.sha1((_coerce_text(row.get('url')) or title).encode('utf-8')).hexdigest()[:12]}",
                    "title": title,
                    "url": _coerce_text(row.get("url")),
                    "snippet": snippet,
                    "provider_text": _coerce_text(row.get("provider_text")) if include_provider_text else "",
                    "error_text": "",
                    "result_kind": "result",
                    "source": _coerce_text(row.get("source")) or "serpapi",
                    "published_at": _coerce_text(row.get("published_at")),
                    "authority_bucket": authority_bucket,
                    "authority_rank": authority_rank,
                    "query_text": query,
                    "provider_payload_json": _coerce_text(row.get("provider_payload_json")) if include_provider_payload else "",
                }
            )

    use_tavily = bool(tavily_client is not None and serp_client is None)
    tavily_topic = "news"
    tavily_query = query
    route_reason = "serp_unavailable" if use_tavily else "serp_results_relevant"
    if tavily_client is not None and serp_client is not None:
        use_tavily, tavily_topic, tavily_query, route_reason = _llm_tavily_route_decision(
            query=query,
            symbol=normalized_symbol,
            company_name=normalized_company,
            serp_preview=serp_preview,
            heuristic_serp_relevant=serp_relevant_count > 0,
            llm_client=llm_client,
        )
        if serp_relevant_count <= 0:
            use_tavily = True

    if tavily_client is not None and use_tavily:
        request_rows.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "candidate_id": candidate_id,
                "query_id": query_id,
                "provider": "tavily",
                "query": tavily_query,
                "route_mode": "rag_fallback" if serp_client is not None else "primary",
                "route_reason": route_reason,
                "topic": tavily_topic,
            }
        )
        try:
            results = tavily_client.search(tavily_query, max_results=max(min(int(budget), 4), 1), topic=tavily_topic)
        except Exception as exc:
            error_text = _trim(str(exc), 180)
            result_rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": candidate_id,
                    "query_id": query_id,
                    "provider": "tavily",
                    "result_id": f"{query_id}::tavily::error",
                    "title": "",
                    "url": "",
                    "snippet": "",
                    "error_text": error_text,
                    "result_kind": "error",
                    "source": "tavily",
                    "published_at": "",
                    "authority_bucket": "web",
                    "authority_rank": 3,
                }
            )
            results = []
        tavily_candidates: list[dict[str, str]] = []
        for item in list(results or [])[: max(min(int(budget), 6), 1)]:
            if not isinstance(item, WebSearchResult):
                continue
            tavily_candidates.append(
                {
                    "title": _coerce_text(item.title),
                    "snippet": _coerce_text(item.snippet),
                    "provider_text": _provider_result_text(item),
                    "source": _coerce_text(item.source),
                    "url": _coerce_text(item.url),
                    "published_at": _coerce_text(item.published_at),
                    "provider_payload_json": _provider_payload_json(item.raw),
                }
            )
        tavily_flags = _llm_search_relevance_flags(
            query=tavily_query,
            symbol=normalized_symbol,
            company_name=normalized_company,
            items=tavily_candidates,
            llm_client=llm_client,
        )
        for row, keep in zip(tavily_candidates, tavily_flags):
            if not bool(keep):
                continue
            title = _coerce_text(row.get("title"))
            snippet = _coerce_text(row.get("snippet"))
            if _is_irrelevant_news_text(title, snippet):
                continue
            authority_bucket, authority_rank = _source_authority_bucket(row.get("source"), row.get("url"))
            result_rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": candidate_id,
                    "query_id": query_id,
                    "provider": "tavily",
                    "result_id": f"{query_id}::tavily::{hashlib.sha1((_coerce_text(row.get('url')) or title).encode('utf-8')).hexdigest()[:12]}",
                    "title": title,
                    "url": _coerce_text(row.get("url")),
                    "snippet": snippet,
                    "provider_text": _coerce_text(row.get("provider_text")) if include_provider_text else "",
                    "error_text": "",
                    "result_kind": "result",
                    "source": _coerce_text(row.get("source")) or "tavily",
                    "published_at": _coerce_text(row.get("published_at")),
                    "authority_bucket": authority_bucket,
                    "authority_rank": authority_rank,
                    "query_text": tavily_query,
                    "provider_payload_json": _coerce_text(row.get("provider_payload_json")) if include_provider_payload else "",
                }
            )
    return request_rows, result_rows


def _candidate_context_documents(
    candidate: dict[str, Any],
    *,
    news_payloads: dict[str, dict[str, Any]] | None,
    context_payloads: dict[str, dict[str, Any]] | None,
    filings_frame: pd.DataFrame | None,
    fred_summary_frame: pd.DataFrame | None,
    yield_curve_facts_frame: pd.DataFrame | None,
    run_id: str,
    asof_time_utc: pd.Timestamp,
    official_routes: list[str],
    priority_entities: list[str],
) -> list[dict[str, Any]]:
    symbol = _normalize_symbol(candidate.get("symbol"))
    documents: list[dict[str, Any]] = []
    payload = dict((news_payloads or {}).get(symbol) or {})
    articles = payload.get("articles")
    if isinstance(articles, pd.DataFrame) and not articles.empty:
        for _, row in articles.iterrows():
            news_headline = _coerce_text(row.get("headline"))
            news_summary = _coerce_text(row.get("summary") or row.get("description"))
            news_text = _evidence_text(news_summary, news_headline)
            if _is_irrelevant_news_text(news_headline, news_text):
                continue
            search_provider = _coerce_text(row.get("provider") or payload.get("source"))
            authority_bucket, authority_rank = _source_authority_bucket(row.get("source"), row.get("url"))
            documents.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": _coerce_text(candidate.get("candidate_id")),
                    "bundle_subject": symbol,
                    "document_id": f"doc::{symbol}::news::{hashlib.sha1((_coerce_text(row.get('url')) or _coerce_text(row.get('headline'))).encode('utf-8')).hexdigest()[:12]}",
                    "source_kind": "news",
                    "source_provider": _coerce_text(row.get("source")),
                    "source_authority_bucket": authority_bucket,
                    "authority_rank": authority_rank,
                    "title": _coerce_text(row.get("headline")),
                    "url": _coerce_text(row.get("url")),
                    "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
                    "raw_text": news_text,
                    "display_excerpt": _display_excerpt(news_text, row.get("headline")),
                    "search_provider": search_provider,
                    "source_trace": _json_dumps({"source": "news_payloads", "provider": search_provider}),
                }
            )
    context = dict((context_payloads or {}).get(symbol) or {})
    context_snippets = [
        ("context_summary", context.get("llm_summary_text")),
        ("context_why_now", context.get("llm_why_now")),
        ("context_headline", context.get("llm_headline")),
    ]
    for label, text in context_snippets:
        clean = _trim(text, 400)
        if not clean:
            continue
        authority_bucket, authority_rank = _source_authority_bucket(context.get("llm_source_line") or "attention_context")
        documents.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "candidate_id": _coerce_text(candidate.get("candidate_id")),
                "bundle_subject": symbol,
                "document_id": f"doc::{symbol}::{label}",
                "source_kind": "context",
                "source_provider": _coerce_text(context.get("llm_source_line") or "Attention Context"),
                "source_authority_bucket": authority_bucket,
                "authority_rank": authority_rank,
                "title": f"{symbol} context",
                "url": "",
                "published_at": pd.NaT,
                "raw_text": clean,
                "display_excerpt": _display_excerpt(clean),
                "source_trace": _json_dumps({"source": "attention_context_bundle"}),
            }
        )
    if "sec" in {route.lower() for route in official_routes} and isinstance(filings_frame, pd.DataFrame) and not filings_frame.empty:
        filings = filings_frame.copy()
        if "symbol" in filings.columns:
            filings["symbol"] = filings["symbol"].astype(str).str.upper().str.strip()
            filings = filings[filings["symbol"] == symbol].copy()
        filings["filing_date"] = pd.to_datetime(filings.get("filing_date"), utc=True, errors="coerce")
        filings = filings.sort_values("filing_date", ascending=False, na_position="last").head(4)
        for _, row in filings.iterrows():
            text = _coerce_text(row.get("filing_excerpt") or row.get("document_text") or row.get("primary_doc_description"))
            if not text:
                continue
            documents.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": _coerce_text(candidate.get("candidate_id")),
                    "bundle_subject": symbol,
                    "document_id": f"doc::{symbol}::sec::{hashlib.sha1((_coerce_text(row.get('filing_url')) or text).encode('utf-8')).hexdigest()[:12]}",
                    "source_kind": "sec",
                    "source_provider": "SEC EDGAR",
                    "source_authority_bucket": "official",
                    "authority_rank": 0,
                    "title": f"{_coerce_text(row.get('form'))} • {_coerce_text(row.get('primary_doc_description') or row.get('items'))}",
                    "url": _coerce_text(row.get("filing_url")),
                    "published_at": pd.to_datetime(row.get("filing_date"), utc=True, errors="coerce"),
                    "raw_text": text,
                    "display_excerpt": _display_excerpt(text, row.get("primary_doc_description")),
                    "source_trace": _json_dumps({"source": "edgar_filings"}),
                }
            )
    if "fred" in {route.lower() for route in official_routes} and isinstance(fred_summary_frame, pd.DataFrame) and not fred_summary_frame.empty:
        fred = fred_summary_frame.copy()
        fred["label"] = fred.get("label", pd.Series(dtype=str)).astype(str)
        tokens = _tag_tokens(priority_entities)
        if tokens:
            fred["_match"] = fred["label"].map(lambda value: len(tokens & _tag_tokens([value])))
            fred = fred[fred["_match"] > 0].sort_values(["_match"], ascending=False).head(3)
        else:
            fred = fred.head(2)
        for _, row in fred.iterrows():
            summary = (
                f"{_coerce_text(row.get('label') or row.get('series_id'))}: latest {_coerce_text(row.get('latest_value'))}"
                f", delta {_coerce_text(row.get('prev_delta'))}, yoy {_coerce_text(row.get('yoy_pct'))}."
            )
            documents.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": _coerce_text(candidate.get("candidate_id")),
                    "bundle_subject": symbol,
                    "document_id": f"doc::{symbol}::fred::{_coerce_text(row.get('series_id'))}",
                    "source_kind": "fred",
                    "source_provider": "FRED",
                    "source_authority_bucket": "official",
                    "authority_rank": 0,
                    "title": _coerce_text(row.get("label") or row.get("series_id")),
                    "url": "",
                    "published_at": pd.NaT,
                    "raw_text": summary,
                    "display_excerpt": _display_excerpt(summary),
                    "source_trace": _json_dumps({"source": "fred_summary"}),
                }
            )
    if "treasury" in {route.lower() for route in official_routes} and _yield_context_relevant(candidate):
        yield_facts = _latest_yield_facts(yield_curve_facts_frame)
        summary = _yield_fact_summary_text(yield_facts)
        if summary:
            documents.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": _coerce_text(candidate.get("candidate_id")),
                    "bundle_subject": symbol,
                    "document_id": f"doc::{symbol}::treasury::yield_curve",
                    "source_kind": "treasury",
                    "source_provider": "U.S. Treasury",
                    "source_authority_bucket": "official",
                    "authority_rank": 0,
                    "title": "Treasury Yield Curve",
                    "url": "https://home.treasury.gov/treasury-daily-interest-rate-xml-feed",
                    "published_at": pd.to_datetime(yield_facts.get("latest_date"), errors="coerce"),
                    "raw_text": summary,
                    "display_excerpt": _display_excerpt(summary),
                    "source_trace": _json_dumps({"source": "yield_curve_facts_1d"}),
                }
            )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in documents:
        key = (_coerce_text(item.get("url")).lower(), _coerce_text(item.get("title")).lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _generic_query_candidates(candidate: dict[str, Any], peer_symbols: list[str]) -> list[dict[str, str]]:
    subject = _candidate_subject(candidate)
    symbol = _normalize_symbol(candidate.get("symbol"))
    sector = _coerce_text(candidate.get("sector"))
    industry = _coerce_text(candidate.get("industry"))
    tags = [tag for tag in _safe_list(candidate.get("macro_exposure_tags")) + _safe_list(candidate.get("business_tags")) if _coerce_text(tag)]
    tag_blob = " ".join(dict.fromkeys(str(tag) for tag in tags[:3]))
    queries: list[dict[str, str]] = []
    base = subject or symbol
    if base:
        queries.append({"query": f"{base} move today", "rationale": "Look for same-day coverage tied to the observed move."})
        queries.append({"query": f"{base} news today", "rationale": "Capture straightforward same-day news about the subject."})
    if industry:
        queries.append({"query": f"{base} {industry} today", "rationale": "Check for industry or peer context linked to the move."})
    elif sector:
        queries.append({"query": f"{base} {sector} today", "rationale": "Check for sector context linked to the move."})
    if tag_blob:
        queries.append({"query": f"{base} {tag_blob} today", "rationale": "Check macro and business-exposure context derived from the subject metadata."})
    if peer_symbols:
        queries.append({"query": f"{base} {' '.join(peer_symbols[:3])} today", "rationale": "Check whether peers or spillover names are moving on the same narrative."})
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in queries:
        query = _trim(item.get("query"), 160)
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        deduped.append({"query": query, "rationale": item.get("rationale", "")})
    return deduped[:4]


def _fallback_research_plan(candidate: dict[str, Any], peer_symbols: list[str]) -> dict[str, Any]:
    routes = ["sec"] if _coerce_text(candidate.get("security_type")).lower() == "common_stock" else []
    if _safe_list(candidate.get("macro_exposure_tags")) or _coerce_text(candidate.get("rates_role")) or _coerce_text(candidate.get("commodity_role")):
        routes.append("fred")
    if _yield_context_relevant(candidate):
        routes.append("treasury")
    routes.append("news")
    routes = list(dict.fromkeys(route for route in routes if route))
    subject = _candidate_subject(candidate)
    tags = [str(tag) for tag in _safe_list(candidate.get("macro_exposure_tags")) + _safe_list(candidate.get("business_tags")) if _coerce_text(tag)]
    hypotheses = []
    if _coerce_text(candidate.get("security_type")).lower() == "common_stock":
        hypotheses.append({"kind": "company_specific", "text": f"Company-specific news may explain why {subject} moved today."})
    if tags:
        hypotheses.append({"kind": "macro", "text": f"Macro or cross-asset context linked to {'/'.join(tags[:2])} may explain the move."})
    hypotheses.append({"kind": "unresolved", "text": f"There may be no clear same-day catalyst for {subject}."})
    priority_entities = [subject, _normalize_symbol(candidate.get("symbol")), _coerce_text(candidate.get("sector")), _coerce_text(candidate.get("industry"))]
    priority_entities.extend(tags[:4])
    priority_entities.extend(peer_symbols[:3])
    return {
        "research_subjects": [{"subject": subject or _normalize_symbol(candidate.get("symbol")), "role": "primary"}]
        + [{"subject": symbol, "role": "peer"} for symbol in peer_symbols[:3]],
        "hypotheses": hypotheses[:3],
        "queries": _generic_query_candidates(candidate, peer_symbols),
        "official_routes": routes,
        "priority_entities": [entity for entity in dict.fromkeys(entity for entity in priority_entities if _coerce_text(entity))],
        "evidence_budget": 8,
    }


def _summary_research_items(home_payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for section_name, section_items in (
        ("top_event", list(home_payload.get("top_events") or [])[:3]),
        ("must_read_mover", list(home_payload.get("must_read_movers") or [])[:4]),
        ("unresolved_move", list(home_payload.get("unresolved_large_moves") or [])[:3]),
    ):
        for item in section_items:
            symbols = [
                str(value).upper().strip()
                for value in list(item.get("supporting_symbols") or [item.get("symbol")])
                if str(value or "").strip()
            ]
            items.append(
                {
                    "kind": section_name,
                    "title": _coerce_text(item.get("event_title") or item.get("headline") or item.get("symbol")),
                    "summary": _coerce_text(
                        item.get("surface_summary_text")
                        or item.get("why_happened_text")
                        or item.get("why_now_text")
                        or item.get("what_happened_text")
                        or item.get("what_changed_text")
                    ),
                    "sector": _coerce_text(item.get("sector") or item.get("source_label")),
                    "industry": _coerce_text(item.get("industry")),
                    "symbols": list(dict.fromkeys(symbols[:6])),
                }
            )
    return items


def _fallback_summary_research_plan(home_payload: dict[str, Any]) -> dict[str, Any]:
    items = _summary_research_items(home_payload)
    symbols: list[str] = []
    sectors: list[str] = []
    titles: list[str] = []
    for item in items:
        for symbol in list(item.get("symbols") or []):
            clean_symbol = _normalize_symbol(symbol)
            if clean_symbol and clean_symbol not in symbols:
                symbols.append(clean_symbol)
        sector = _coerce_text(item.get("sector"))
        if sector and sector not in sectors:
            sectors.append(sector)
        title = _trim(item.get("title"), 120)
        if title and title not in titles:
            titles.append(title)

    priority_entities = symbols[:6] + sectors[:4]
    if not priority_entities:
        priority_entities = ["market", "cross-asset", "sector rotation"]

    queries: list[dict[str, str]] = []
    symbol_blob = " ".join(symbols[:4]).strip()
    sector_blob = " ".join(sectors[:3]).strip()
    title_blob = " ".join(titles[:2]).strip()
    if symbol_blob:
        queries.append(
            {
                "query": f"{symbol_blob} move today macro sector driver",
                "rationale": "Find one cross-market explanation tying the main tape symbols together.",
            }
        )
    if sector_blob:
        queries.append(
            {
                "query": f"{sector_blob} stocks moving today macro driver",
                "rationale": "Check whether sector-level context explains multiple movers at once.",
            }
        )
    if title_blob:
        queries.append(
            {
                "query": f"{title_blob} market narrative today",
                "rationale": "Search the tape's main observed pattern directly.",
            }
        )
    queries.append(
        {
            "query": "stocks Treasury yields oil dollar sectors moving today why",
            "rationale": "Look for the broad cross-market narrative behind the session.",
        }
    )
    queries.append(
        {
            "query": "market sector rotation today macro narrative rates growth defensives",
            "rationale": "Check for rotation and rates-driven explanations that can connect multiple beats.",
        }
    )

    deduped_queries: list[dict[str, str]] = []
    seen_queries: set[str] = set()
    for item in queries:
        query = _trim(item.get("query"), 160)
        if not query or query.lower() in seen_queries:
            continue
        seen_queries.add(query.lower())
        deduped_queries.append({"query": query, "rationale": _coerce_text(item.get("rationale"))})
        if len(deduped_queries) >= 5:
            break

    return {
        "research_subjects": [{"subject": "market tape", "role": "primary"}],
        "hypotheses": [
            {"kind": "cross_market", "text": "A shared macro or sector narrative may be driving several tape items at once."},
            {"kind": "sector_rotation", "text": "Sector rotation or factor positioning may explain the mix of winners and losers."},
            {"kind": "unresolved", "text": "Some moves may still be idiosyncratic or unresolved despite the broader tape pattern."},
        ],
        "queries": deduped_queries,
        "official_routes": ["news"],
        "priority_entities": priority_entities,
        "evidence_budget": 6,
    }


def _plan_summary_research(home_payload: dict[str, Any], *, llm_client: LLMClient | None) -> list[str]:
    import json as _json

    fallback = _fallback_summary_research_plan(home_payload)
    if llm_client is None:
        return [_coerce_text(item.get("query")) for item in fallback["queries"] if _coerce_text(item.get("query"))]

    try:
        data = llm_client.generate_json(
            system_prompt=(
                "You are planning research for a market homepage summary. "
                "Look across the whole tape and propose 3 to 5 search queries that can explain the shared macro, sector, "
                "or cross-asset narrative. Do not emit one query per symbol. Prefer broad but concrete queries that could "
                "tie multiple movers or events together."
            ),
            user_prompt=_json.dumps(
                {
                    "home_payload": {
                        "generated_at_utc": _coerce_text(home_payload.get("generated_at_utc")),
                        "coverage_summary": dict(home_payload.get("coverage_summary") or {}),
                        "items": _summary_research_items(home_payload),
                    },
                    "fallback": fallback,
                    "instructions": {
                        "query_count": "3-5",
                        "no_per_symbol_queries": True,
                        "prefer_cross_market_context": True,
                    },
                },
                ensure_ascii=False,
                default=str,
            ),
            schema_name="attention_research_plan",
            schema=PLANNER_SCHEMA,
        )
    except Exception:
        data = fallback

    query_rows = [
        item
        for item in list((data or {}).get("queries") or [])
        if _coerce_text((item or {}).get("query"))
    ]
    if not query_rows:
        query_rows = list(fallback.get("queries") or [])

    deduped: list[str] = []
    seen: set[str] = set()
    for item in query_rows:
        query = _trim(item.get("query"), 160)
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        deduped.append(query)
        if len(deduped) >= 5:
            break
    return deduped


def _plan_candidate_research(candidate: dict[str, Any], peer_symbols: list[str], llm_client: LLMClient | None) -> dict[str, Any]:
    import json as _json
    if llm_client is None:
        return _fallback_research_plan(candidate, peer_symbols)
    fallback = _fallback_research_plan(candidate, peer_symbols)
    system_prompt = (
        "You are planning bottom-up market-move research. "
        "Use only the supplied facts. Do not use canned oil/rates/risk templates. "
        "Return compact JSON with queries and official routes."
    )
    user_prompt = _json.dumps(
        {
            "candidate": {
                "symbol": _normalize_symbol(candidate.get("symbol")),
                "subject": _candidate_subject(candidate),
                "sector": _coerce_text(candidate.get("sector")),
                "industry": _coerce_text(candidate.get("industry")),
                "change_pct": _coerce_float(candidate.get("change_pct")),
                "expected_move_pct": _coerce_float(candidate.get("expected_move_pct")),
                "surprise_z": _coerce_float(candidate.get("surprise_z")),
                "macro_exposure_tags": _safe_list(candidate.get("macro_exposure_tags")),
                "business_tags": _safe_list(candidate.get("business_tags")),
                "peer_symbols": peer_symbols[:5],
            },
            "fallback": fallback,
        },
        ensure_ascii=False,
        default=str,
    )
    try:
        data = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name="attention_research_plan",
            schema=PLANNER_SCHEMA,
        )
        queries = [item for item in data.get("queries", []) if _coerce_text((item or {}).get("query"))]
        if not queries:
            return fallback
        return {
            "research_subjects": data.get("research_subjects") or fallback["research_subjects"],
            "hypotheses": data.get("hypotheses") or fallback["hypotheses"],
            "queries": queries[:4],
            "official_routes": data.get("official_routes") or fallback["official_routes"],
            "priority_entities": data.get("priority_entities") or fallback["priority_entities"],
            "evidence_budget": int(data.get("evidence_budget") or fallback["evidence_budget"] or 8),
        }
    except Exception:
        return fallback


def _peer_candidates(candidate: dict[str, Any], candidates: pd.DataFrame, *, limit: int = 5) -> list[str]:
    symbol = _normalize_symbol(candidate.get("symbol"))
    industry = _coerce_text(candidate.get("industry"))
    sector = _coerce_text(candidate.get("sector"))
    peers: list[tuple[str, float]] = []
    for _, row in candidates.iterrows():
        peer_symbol = _normalize_symbol(row.get("symbol"))
        if not peer_symbol or peer_symbol == symbol:
            continue
        match = 0.0
        if industry and industry != "Unknown" and _coerce_text(row.get("industry")) == industry:
            match += 1.0
        elif sector and sector != "Unknown" and _coerce_text(row.get("sector")) == sector:
            match += 0.6
        else:
            shared_tags = _jaccard(
                _tag_tokens(_safe_list(candidate.get("macro_exposure_tags")) + _safe_list(candidate.get("business_tags"))),
                _tag_tokens(_safe_list(row.get("macro_exposure_tags")) + _safe_list(row.get("business_tags"))),
            )
            match += shared_tags
        if match <= 0:
            continue
        peers.append((peer_symbol, match + abs(_coerce_float(row.get("change_pct"), 0.0)) / 20.0))
    peers.sort(key=lambda item: (-item[1], item[0]))
    return [symbol for symbol, _ in peers[:limit]]
