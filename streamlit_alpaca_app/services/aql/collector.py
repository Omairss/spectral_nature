"""
AQL collector — web search and document collection functions.
"""
from __future__ import annotations

from ..llm_context import render_research_prompt
from ..llm_execution import latest_research_artifact

import hashlib
import json
import os
from typing import Any, Callable

import pandas as pd

from ..common.news_freshness import coerce_article_published_at, is_recent_for_attention
from ..common.company_identity import clean_company_display_name
from ..web_research import (
    SerperSearchClient,
    SerpAPISearchClient,
    TavilySearchClient,
    WebResearchError,
    WebSearchResult,
    load_serper_config,
    search_client_provider,
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
    original_query = _coerce_text(query)
    return _trim(f"{subject} {original_query}".strip(), 200)


def _symbol_news_general_query(symbol: str, company_name: str) -> str:
    company = _coerce_text(company_name)
    subject = f"{company} {symbol}".strip() if company else symbol
    return _trim(
        f"{subject} stock today latest news catalyst business update earnings guidance partnership sector",
        220,
    )


def _provider_payload_json(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    return _json_dumps(value)


def _provider_result_text(item: WebSearchResult) -> str:
    raw_text = _coerce_text(getattr(item, "raw_text", ""))
    if raw_text:
        return raw_text
    return _coerce_text(item.snippet)


def _symbol_news_max_age_days() -> int:
    raw = _coerce_text(os.getenv("ATTENTION_SYMBOL_NEWS_MAX_AGE_DAYS"))
    if raw:
        try:
            return max(int(raw), 1)
        except Exception:
            pass
    return 7


def _symbol_news_include_undated() -> bool:
    raw = _coerce_text(os.getenv("ATTENTION_SYMBOL_NEWS_INCLUDE_UNDATED")).lower()
    return raw in {"1", "true", "yes", "on"}


def _current_symbol_news_timestamp(value: object, *, url: object, asof_time_utc: object) -> pd.Timestamp:
    published_at = coerce_article_published_at(value, url=url, asof_time_utc=asof_time_utc)
    if not is_recent_for_attention(
        published_at,
        asof_time_utc=asof_time_utc,
        max_age_days=_symbol_news_max_age_days(),
        include_undated=_symbol_news_include_undated(),
    ):
        return pd.NaT
    return published_at


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
    baseline_flags = [
        _search_result_is_relevant(
            _coerce_text(item.get("title")),
            _coerce_text(item.get("snippet")),
            symbol=symbol,
            company_name=company_name,
        )
        for item in items
    ]
    if llm_client is None:
        return baseline_flags
    try:
        from ..aql_zopedia_engine import run_aql_zopedia_structured_agent

        engine_operation = run_aql_zopedia_structured_agent(
            query=json.dumps(
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
            instructions="You are a relevance gate for company news research. Select only indices that are materially relevant to the target company and likely business triggers, including trials, approvals, guidance, partnerships, product/commercial updates, management actions, or major financial/company developments. Exclude noisy insider/form-4/dividend-equivalent chatter, routine ex-dividend notices, isolated analyst target tweaks, and generic stock-up/stock-down recaps that do not identify a concrete business development.",
            schema_name="attention_search_relevance",
            schema=SEARCH_RELEVANCE_SCHEMA,
            task="attention_search_relevance", stage='relevance',
            surface="aql.search_relevance",
            llm_client=llm_client,
            max_tool_calls=0,
            persist_findings=False,
            write_policy="none",
            call_type="utility",
            operation_metadata={
                "symbol": symbol,
                "company_name": company_name,
                "item_count": len(items),
            },
        )
        if engine_operation.get("status") != "completed":
            raise RuntimeError(
                engine_operation.get("error") or "Shared AI operation failed"
            )
        data = engine_operation["payload"]
        selected = {
            int(value)
            for value in list(data.get("relevant_indices") or [])
            if isinstance(value, (int, float)) and 0 <= int(value) < len(items)
        }
        return [bool(baseline_flags[index]) or index in selected for index in range(len(items))]
    except Exception:
        return baseline_flags


def _llm_tavily_route_decision(
    *,
    query: str,
    symbol: str,
    company_name: str,
    serp_preview: list[dict[str, str]],
    serp_has_identity_match: bool,
    llm_client: LLMClient | None,
) -> tuple[bool, str, str, str]:
    default_tavily_query = _default_tavily_general_query(query, symbol, company_name)
    if llm_client is None:
        if serp_has_identity_match:
            return False, "news", query, "primary_search_results_relevant"
        return True, "general", default_tavily_query, "primary_search_results_not_relevant"
    try:
        from ..aql_zopedia_engine import run_aql_zopedia_structured_agent

        engine_operation = run_aql_zopedia_structured_agent(
            query=json.dumps(
                {
                    "query": query,
                    "symbol": symbol,
                    "company_name": company_name,
                    "serp_preview": serp_preview[:4],
                    "serp_has_identity_match": bool(serp_has_identity_match),
                    "policy": "Use Tavily when primary search evidence is sparse, generic, or off-topic.",
                    "default_tavily_query": default_tavily_query,
                },
                ensure_ascii=False,
                default=str,
            ),
            instructions="You are a research-router for market analysis. Decide if the primary news-search results are relevant enough to explain the move. If not relevant, call Tavily using topic='general' for broader RAG retrieval. Prefer Tavily when primary search results are sparse or mostly low-signal (insider/form-4, ex-dividend, analyst target-only notes, or generic price-action recaps without concrete company developments). When using Tavily, output a high-recall query that includes company identity and likely business triggers.",
            schema_name="attention_search_router",
            schema=SEARCH_ROUTER_SCHEMA,
            task="attention_search_router", stage='search',
            surface="aql.search_router",
            llm_client=llm_client,
            max_tool_calls=0,
            persist_findings=False,
            write_policy="none",
            call_type="utility",
            operation_metadata={
                "symbol": symbol,
                "company_name": company_name,
                "preview_count": len(serp_preview),
                "primary_has_identity_match": bool(serp_has_identity_match),
            },
        )
        if engine_operation.get("status") != "completed":
            raise RuntimeError(
                engine_operation.get("error") or "Shared AI operation failed"
            )
        data = engine_operation["payload"]
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
        if serp_has_identity_match:
            return False, "news", query, "primary_search_results_relevant"
        return True, "general", default_tavily_query, "primary_search_results_not_relevant"


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


def search_symbol_news_payload(
    symbol: str,
    *,
    company_name: str = "",
    max_results: int = 8,
    serp_client: SerperSearchClient | SerpAPISearchClient | None = None,
    tavily_client: TavilySearchClient | None = None,
    llm_client: LLMClient | None = None,
    asof_time_utc: object | None = None,
) -> dict[str, Any]:
    normalized_symbol = _normalize_symbol(symbol)
    if not normalized_symbol:
        return {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}
    company_name = clean_company_display_name(company_name) or _coerce_text(company_name)
    asof_ts = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    if pd.isna(asof_ts):
        asof_ts = pd.Timestamp.utcnow()

    if serp_client is None:
        serper_cfg = load_serper_config()
        if serper_cfg is not None:
            serp_client = SerperSearchClient(serper_cfg)

    query_base = f"{normalized_symbol} stock today"
    if company_name:
        query_base = f"{company_name} {normalized_symbol} stock today"

    article_rows: list[dict[str, Any]] = []
    sources: list[str] = []
    errors: list[str] = []
    serp_preview: list[dict[str, str]] = []
    serp_relevant_count = 0
    serp_candidates: list[dict[str, str]] = []
    serp_failed = False
    primary_provider = search_client_provider(serp_client) or "primary_search"

    def _append_current_candidates(*, provider: str, query: str, candidates: list[dict[str, str]]) -> int:
        nonlocal serp_relevant_count
        if not candidates:
            return 0
        kept = 0
        flags = _llm_search_relevance_flags(
            query=query,
            symbol=normalized_symbol,
            company_name=company_name,
            items=candidates,
            llm_client=llm_client,
        )
        for row, keep in zip(candidates, flags):
            serp_preview.append({"title": row.get("title", ""), "snippet": row.get("snippet", ""), "source": row.get("source", "")})
            if not bool(keep):
                continue
            if pd.isna(row.get("published_at")):
                continue
            if _is_irrelevant_news_text(row.get("title"), row.get("snippet")):
                continue
            kept += 1
            if provider == primary_provider:
                serp_relevant_count += 1
            article_rows.append(
                {
                    "headline": _coerce_text(row.get("title")),
                    "summary": _evidence_text(row.get("provider_text") or row.get("snippet"), row.get("title")),
                    "description": _evidence_text(row.get("provider_text") or row.get("snippet"), row.get("title")),
                    "source": _coerce_text(row.get("source")) or provider,
                    "provider": provider,
                    "published_at": row.get("published_at"),
                    "url": _coerce_text(row.get("url")),
                    "provider_payload_json": _coerce_text(row.get("provider_payload_json")),
                }
            )
        return kept

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
                        "source": _coerce_text(item.source) or primary_provider,
                        "url": _coerce_text(item.url),
                        "published_at": _current_symbol_news_timestamp(
                            item.published_at,
                            url=item.url,
                            asof_time_utc=asof_ts,
                        ),
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
                            "published_at": _current_symbol_news_timestamp(
                                ai_overview.published_at,
                                url=ai_overview.url,
                                asof_time_utc=asof_ts,
                            ),
                            "provider_payload_json": _provider_payload_json(ai_overview.raw),
                        }
                    )
            _append_current_candidates(provider=primary_provider, query=query_base, candidates=serp_candidates)
            if serp_relevant_count <= 0:
                general_query = _symbol_news_general_query(normalized_symbol, company_name)
                general_candidates: list[dict[str, str]] = []
                for item in serp_client.search(general_query, news=False, num=max(max_results, 3)):
                    title = _coerce_text(item.title)
                    snippet = _coerce_text(item.snippet)
                    if not title and not snippet:
                        continue
                    general_candidates.append(
                        {
                            "title": title,
                            "snippet": snippet,
                            "provider_text": _provider_result_text(item),
                            "source": _coerce_text(item.source) or primary_provider,
                            "url": _coerce_text(item.url),
                            "published_at": _current_symbol_news_timestamp(
                                item.published_at,
                                url=item.url,
                                asof_time_utc=asof_ts,
                            ),
                            "provider_payload_json": _provider_payload_json(item.raw),
                        }
                    )
                _append_current_candidates(provider=primary_provider, query=general_query, candidates=general_candidates)
            sources.append(primary_provider)
        except WebResearchError as exc:
            errors.append(str(exc))
            serp_failed = True

    use_tavily = bool(tavily_client is not None and (serp_client is None or serp_failed))
    tavily_topic = "news"
    tavily_query = query_base
    if tavily_client is not None and serp_client is not None and not serp_failed:
        use_tavily, tavily_topic, tavily_query, _ = _llm_tavily_route_decision(
            query=query_base,
            symbol=normalized_symbol,
            company_name=company_name,
            serp_preview=serp_preview,
            serp_has_identity_match=serp_relevant_count > 0,
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
                        "published_at": _current_symbol_news_timestamp(
                            item.published_at,
                            url=item.url,
                            asof_time_utc=asof_ts,
                        ),
                        "provider_payload_json": _provider_payload_json(item.raw),
                    }
                )
            _append_current_candidates(provider="tavily", query=tavily_query, candidates=tavily_candidates)
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
    serp_client: SerperSearchClient | SerpAPISearchClient | None,
    tavily_client: TavilySearchClient | None,
    llm_client: LLMClient | None,
    budget: int,
    include_provider_payload: bool = False,
    include_provider_text: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Legacy data-shape adapter; research itself belongs to the shared engine."""
    from ..aql_zopedia_engine import run_aql_zopedia_agent
    from .evidence_pack import materialize_research_artifacts

    if llm_client is None:
        return [], []
    result = run_aql_zopedia_agent(
        query=query,
        task="source_research",
        surface="aql.source_research",
        max_tool_calls=max(int(budget), 1),
        llm_client=llm_client,
        persist_findings=False,
        write_policy="none",
        request_context={"asof_time_utc": str(asof_time_utc)},
    )
    trace = materialize_research_artifacts(
        result,
        candidate_id=candidate_id,
        subject=symbol,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
    )
    return trace["request_rows"], trace["result_rows"]


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
                    "published_at": coerce_article_published_at(
                        row.get("published_at"),
                        url=row.get("url"),
                        asof_time_utc=asof_time_utc,
                    ),
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
        clean = _coerce_text(text)
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
    if isinstance(filings_frame, pd.DataFrame) and not filings_frame.empty:
        filings = filings_frame.copy()
        if "symbol" in filings.columns:
            filings["symbol"] = filings["symbol"].astype(str).str.upper().str.strip()
            filings = filings[filings["symbol"] == symbol].copy()
        filings["filing_date"] = pd.to_datetime(filings.get("filing_date"), utc=True, errors="coerce")
        filings = filings.sort_values(
            "filing_date", ascending=False, na_position="last"
        )
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
    if isinstance(fred_summary_frame, pd.DataFrame) and not fred_summary_frame.empty:
        fred = fred_summary_frame.copy()
        fred["label"] = fred.get("label", pd.Series(dtype=str)).astype(str)
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
    if (
        isinstance(yield_curve_facts_frame, pd.DataFrame)
        and not yield_curve_facts_frame.empty
    ):
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


def _peer_candidates(
    candidate: dict[str, Any], candidates: pd.DataFrame, *, limit: int = 5
) -> list[str]:
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


def research_attention_candidate(
    candidate: dict[str, Any],
    *,
    peer_symbols: list[str],
    documents: list[dict[str, Any]],
    llm_client: LLMClient,
    timeout_seconds: float | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Define a feed task; the shared engine owns its entire research lifecycle."""
    from ..aql_zopedia_engine import run_aql_zopedia_structured_agent
    from .constants import CLAIM_SCHEMA, SYMBOL_WRITER_SCHEMA

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **SYMBOL_WRITER_SCHEMA["properties"],
            **CLAIM_SCHEMA["properties"],
        },
        "required": [*SYMBOL_WRITER_SCHEMA["required"], *CLAIM_SCHEMA["required"]],
    }
    prior = latest_research_artifact(
        access_scope="public_company_research",
        subject="company:" + _normalize_symbol(candidate.get("symbol")),
        task="ticker_business_model_research_dossier",
    )
    task_query = (
        "Investigate this company and its observed market development. Explain what changed and the "
        "useful business, customer, supplier, financing, sector or macro connections. Compare alternatives "
        "and pursue original reporting when it improves the explanation. Existing investigations are dated "
        "context that can be extended or corrected. Return the narrative and useful claims for the feed. "
        "Claims may include plausible indirect connections; describe their strength without requiring proof "
        "of causality. Reference the source IDs or tool_call_ids supplied by the shared engine."
    )
    task_query = render_research_prompt(
        evidence={"completed_research": prior},
        request={"candidate": candidate, "peer_symbols": peer_symbols},
        instructions=task_query,
    )
    result = run_aql_zopedia_structured_agent(
        query=task_query,
        schema_name="attention_candidate_research",
        schema=schema,
        task="attention_candidate_research", stage='synthesis',
        surface="attention.candidate",
        initial_evidence=documents,
        llm_client=llm_client,
        run_timeout_seconds=timeout_seconds,
        request_context=(
            {"asof_time_utc": str(candidate["asof_time_utc"])}
            if candidate.get("asof_time_utc") is not None
            else None
        ),
        persist_findings=False,
        write_policy="none",
        progress_callback=progress_callback,
    )
    return result
