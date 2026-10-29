from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import dataclass, asdict, replace
from functools import wraps
from datetime import datetime, timezone
import json
import logging
import hashlib
import os
import re
import threading
import time
import uuid
from typing import Any, Callable
from urllib.parse import urlparse

import pandas as pd

from ..llm_context import serialize_llm_context, render_research_prompt, organize_source_captures
from ..llm_execution import (latest_research_artifact, current_research_access_scope, retain_research_artifact,
    research_artifact_key, execution_ownership, research_work_context, mark_research_work_incomplete,
    check_execution_ownership, ResearchWorkDeferred)

from ..saa import build_zopedia_change_proposal, prepare_zopedia_pages
from ..web_research import WebResearchError, search_client_provider
from ..seeking_alpha_access import is_seeking_alpha_url
from ..page_browsing import browse_page, page_quality_issue
from ._shared import _source_authority_bucket, _trim
from .news_business_resolution import (
    BUSINESS_MEMORY_SLOTS,
    BUSINESS_MEMORY_ARTICLE_SCHEMA,
    _add_slot,
    _asof_iso,
    _coerce_text,
    _company_baseline_row,
    _company_business_memory_pages,
    _company_name,
    _edgar_rows,
    _event_symbols,
    _event_text,
    _fundamental_rows,
    _json_dict,
    _json_dumps,
    _matching_zopedia_pages,
    _merge_slot_facts,
    _normalize_symbol,
    _source_backed_memory_facts,
    _stable_id,
)


ProgressCallback = Callable[[dict[str, Any]], None]
log = logging.getLogger(__name__)
TICKER_BUSINESS_MODEL_STACK_SOURCE_TYPE = "ticker_business_model_stack_business_memory"
_BUSINESS_RESEARCH_REQUEST_LOCK = threading.Lock()
_BUSINESS_RESEARCH_LAST_REQUEST_AT_BY_KEY: dict[str, float] = {}


def _emit_business_progress(progress_callback: ProgressCallback | None, **payload: Any) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(dict(payload))
    except Exception:
        return

TICKER_BUSINESS_MODEL_STACK_COLUMNS: tuple[str, ...] = (
    "stack_id",
    "run_id",
    "asof_time_utc",
    "symbol",
    "company_name",
    "status",
    "confidence",
    "source_page_ids_json",
    "zopedia_page_ids_read_json",
    "fundamental_datasets_used_json",
    "research_plan_id",
    "research_query_plan_json",
    "search_request_ids_json",
    "search_result_ids_json",
    "research_dossier_json",
    "slot_coverage_json",
    "synthesis_warnings_json",
    "slot_facts_json",
    "slot_gaps_json",
    "business_story_markdown",
    "business_memory_page_id",
    "business_memory_body_markdown",
    "proposal_ids_json",
    "proposal_rows_json",
    "proposed_pages_json",
    "evidence_pack_id",
    "created_at_utc",
)

BUSINESS_MODEL_RESEARCH_PLAN_COLUMNS: tuple[str, ...] = (
    "plan_id",
    "run_id",
    "asof_time_utc",
    "symbol",
    "company_name",
    "required_slots_json",
    "query_plan_json",
    "created_at_utc",
)

BUSINESS_MODEL_SEARCH_REQUEST_COLUMNS: tuple[str, ...] = (
    "request_id",
    "plan_id",
    "run_id",
    "asof_time_utc",
    "symbol",
    "company_name",
    "slot",
    "source_intent",
    "requires_page_open",
    "provider",
    "topic",
    "query",
    "priority",
    "created_at_utc",
)

BUSINESS_MODEL_SEARCH_RESULT_COLUMNS: tuple[str, ...] = (
    "result_id",
    "request_id",
    "plan_id",
    "run_id",
    "asof_time_utc",
    "symbol",
    "company_name",
    "slot",
    "provider",
    "source_intent",
    "title",
    "url",
    "snippet",
    "raw_text",
    "source",
    "published_at",
    "authority_bucket",
    "authority_rank",
    "opened_page",
    "browse_mode",
    "browse_warning",
    "page_quality_issue",
    "error_text",
    "created_at_utc",
)

BUSINESS_MODEL_STACK_SLOT_QUESTIONS: dict[str, str] = {
    "business_model": "What does the company sell and how does it make money?",
    "products_and_services": "Which products, services, platforms, or capabilities matter most?",
    "customer_segments": "Who buys from the company?",
    "named_customers": "Which named customers, partners, or end markets are source-backed?",
    "customer_demand": "Is demand improving, weakening, or changing mix?",
    "fundamentals": "What do revenue, margins, cash flow, balance sheet, and debt say?",
    "backlog_or_rpo": "Is backlog, RPO, bookings, or contracted demand visible?",
    "cash_and_runway": "Is there enough cash or financing capacity for the plan?",
    "workforce_and_hiring": "Is headcount or hiring expanding, shrinking, or shifting?",
    "employee_sentiment": "What are employees saying, and is morale a risk or asset?",
    "web_or_developer_attention": "Is website, developer, community, or search attention changing?",
    "policy_or_regulatory_environment": "Is the policy or regulatory backdrop supportive or hostile?",
    "supply_chain_or_capacity_constraints": "Are capacity, inputs, supply chain, or delivery constraints important?",
    "execution_risks": "What can break the business plan?",
    "confirmation_events": "What recent events confirmed the existing business story?",
    "invalidation_events": "What recent events contradicted the existing business story?",
}

BUSINESS_MODEL_RESEARCH_SLOT_ORDER: tuple[str, ...] = (
    "business_model",
    "products_and_services",
    "customer_segments",
    "named_customers",
    "customer_demand",
    "fundamentals",
    "backlog_or_rpo",
    "cash_and_runway",
    "workforce_and_hiring",
    "employee_sentiment",
    "web_or_developer_attention",
    "policy_or_regulatory_environment",
    "supply_chain_or_capacity_constraints",
    "execution_risks",
    "confirmation_events",
    "invalidation_events",
)

BUSINESS_MODEL_READY_REQUIRED_SLOTS: tuple[str, ...] = (
    "business_model",
    "products_and_services",
    "customer_segments",
    "fundamentals",
    "execution_risks",
)

BUSINESS_MODEL_READY_SIGNAL_SLOTS: tuple[str, ...] = (
    "customer_demand",
    "backlog_or_rpo",
    "confirmation_events",
)


def _safe_error_text(value: object, *, limit: int = 260) -> str:
    text = _trim(value, limit)
    text = re.sub(r"sk-[A-Za-z0-9*_\\-]{8,}", "[redacted_api_key]", text)
    text = re.sub(r"(api[_-]?key[\"'=:\\s]+)[A-Za-z0-9*_\\-]{8,}", r"\1[redacted]", text, flags=re.IGNORECASE)
    return text


def _env_positive_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = int(default)
    return max(value, int(minimum))


def _env_nonnegative_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = float(default)
    return max(value, 0.0)


def _business_research_budget_seconds() -> int:
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_RESEARCH_BUDGET_SECONDS", 3600, minimum=60)


def _business_agent_tool_timeout_seconds() -> int:
    budget = _business_research_budget_seconds()
    default = min(max(budget // 2, 300), budget)
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_AGENT_TOOL_TIMEOUT_SECONDS", default, minimum=60)


def _business_agent_llm_step_timeout_seconds() -> int:
    budget = _business_research_budget_seconds()
    default = min(max(budget // 6, 180), budget)
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_AGENT_LLM_STEP_TIMEOUT_SECONDS", default, minimum=60)


def _business_agent_prefetch_timeout_seconds() -> int:
    budget = _business_research_budget_seconds()
    default = min(max(budget // 20, 60), budget)
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_AGENT_PREFETCH_TIMEOUT_SECONDS", default, minimum=30)


def _business_research_plan_max_tool_calls() -> int:
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_RESEARCH_PLAN_MAX_TOOL_CALLS", 10, minimum=1)


def _business_stack_max_tool_calls() -> int:
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_STACK_MAX_TOOL_CALLS", 24, minimum=1)


def _business_search_max_workers() -> int:
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_SEARCH_MAX_WORKERS", 64, minimum=1)


def _business_research_open_pages_per_query() -> int:
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_OPEN_PAGES_PER_QUERY", 2, minimum=1)


def _business_research_open_attempts_per_query() -> int:
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_OPEN_ATTEMPTS_PER_QUERY", 4, minimum=1)


def _business_research_open_page_chars() -> int:
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_OPEN_PAGE_CHARS", 8000, minimum=1200)


def _business_research_seeking_alpha_pages_per_query() -> int:
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_SEEKING_ALPHA_PAGES_PER_QUERY", 1, minimum=0)


def _business_research_request_delay_seconds() -> float:
    return _env_nonnegative_float("ZOPEDIA_TICKER_BUSINESS_REQUEST_DELAY_SECONDS", 0.4)


def _business_research_provider_delay_seconds() -> float:
    return _env_nonnegative_float(
        "ZOPEDIA_TICKER_BUSINESS_PROVIDER_DELAY_SECONDS",
        _business_research_request_delay_seconds(),
    )


def _business_research_host_delay_seconds() -> float:
    return _env_nonnegative_float(
        "ZOPEDIA_TICKER_BUSINESS_HOST_DELAY_SECONDS",
        _business_research_request_delay_seconds(),
    )


def _business_research_host_key(url: str) -> str:
    host = _coerce_text(urlparse(_coerce_text(url)).netloc).lower()
    return f"host:{host}" if host else "host:unknown"


def _throttle_business_research_request(*, key: str, delay_seconds: float) -> None:
    clean_key = _coerce_text(key) or "unknown"
    delay = max(float(delay_seconds), 0.0)
    if delay <= 0:
        return
    while True:
        with _BUSINESS_RESEARCH_REQUEST_LOCK:
            now = time.monotonic()
            last_request_at = _BUSINESS_RESEARCH_LAST_REQUEST_AT_BY_KEY.get(clean_key, 0.0)
            wait_for = delay - (now - last_request_at)
            if wait_for <= 0:
                _BUSINESS_RESEARCH_LAST_REQUEST_AT_BY_KEY[clean_key] = now
                return
        time.sleep(wait_for)


def _business_aql_candidate_timeout_seconds() -> int:
    budget = _business_research_budget_seconds()
    default = min(max(budget // 2, 300), budget)
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_AQL_CANDIDATE_TIMEOUT_SECONDS", default, minimum=60)


def _business_aql_event_bundle_timeout_seconds() -> int:
    budget = _business_research_budget_seconds()
    default = min(max(budget // 4, 180), budget)
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_AQL_EVENT_BUNDLE_TIMEOUT_SECONDS", default, minimum=60)


def _business_aql_macro_timeout_seconds() -> int:
    budget = _business_research_budget_seconds()
    default = min(max(budget // 3, 240), budget)
    return _env_positive_int("ZOPEDIA_TICKER_BUSINESS_AQL_MACRO_TIMEOUT_SECONDS", default, minimum=60)


def _business_agent_runtime_kwargs() -> dict[str, int]:
    return {
        "tool_call_timeout_seconds": _business_agent_tool_timeout_seconds(),
        "llm_step_timeout_seconds": _business_agent_llm_step_timeout_seconds(),
        "prefetch_timeout_seconds": _business_agent_prefetch_timeout_seconds(),
    }


@contextmanager
def _business_aql_timeouts():
    from .pipeline import aql_timeout_scope

    defaults = {
        "AQL_CANDIDATE_RESEARCH_TIMEOUT_SECONDS": _business_aql_candidate_timeout_seconds(),
        "AQL_EVENT_BUNDLE_TIMEOUT_SECONDS": _business_aql_event_bundle_timeout_seconds(),
        "AQL_MACRO_VERIFICATION_TIMEOUT_SECONDS": _business_aql_macro_timeout_seconds(),
    }
    with aql_timeout_scope({name: value for name, value in defaults.items() if os.getenv(name) is None}):
        yield


def _clean_company_name_for_research(value: object) -> str:
    text = _coerce_text(value)
    if not text:
        return ""
    text = re.sub(
        r"\s+-\s+.*?\b(common stock|capital stock|ordinary shares|ordinary share|adr|ads|depositary shares?)\b.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(class\s+[a-z]\s+)?(common stock|capital stock|ordinary shares?|depositary shares?)\b.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(inc|corp|corporation|company|ltd|plc)\.\s*$", lambda match: match.group(0).strip(), text, flags=re.IGNORECASE)
    return text.strip(" ,-") or _coerce_text(value)

TICKER_BUSINESS_MODEL_STACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "resolved_company_name": {"type": "string"},
        "business_story_markdown": {"type": "string", "minLength": 1},
        "memory_article": BUSINESS_MEMORY_ARTICLE_SCHEMA,
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "slot_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["resolved_company_name", "business_story_markdown", "memory_article", "confidence", "slot_gaps"],
}

BUSINESS_MODEL_RESEARCH_QUERY_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "resolved_company_name": {"type": "string"},
        "company_aliases": {"type": "array", "items": {"type": "string"}},
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "slot": {"type": "string"},
                    "question": {"type": "string"},
                    "query": {"type": "string"},
                    "topic": {"type": "string"},
                    "source_intent": {"type": "string"},
                    "requires_page_open": {"type": "boolean"},
                    "priority": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "slot",
                    "question",
                    "query",
                    "topic",
                    "source_intent",
                    "requires_page_open",
                    "priority",
                    "reason",
                ],
            },
        },
        "data_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["resolved_company_name", "company_aliases", "queries", "data_gaps"],
}

BUSINESS_MODEL_RESEARCH_DOSSIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "source_inventory": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "evidence_ref": {"type": "string"},
                    "slot": {"type": "string"},
                    "source_class": {"type": "string"},
                    "source_status": {"type": "string"},
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "relevance": {"type": "string"},
                    "evidence_scope": {"type": "string"},
                    "business_use": {"type": "string"},
                    "evidence_limits": {"type": "string"},
                    "confidence": {"type": "string"},
                },
                "required": [
                    "evidence_ref",
                    "title",
                ],
            },
        },
        "slot_findings": {
            "type": "object",
            "additionalProperties": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "finding": {"type": "string"},
                        "status": {"type": "string"},
                        "confidence": {"type": "string"},
                        "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "missing_evidence": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["finding", "confidence", "evidence_refs"],
                },
            },
        },
        "source_gaps": {"type": "array", "items": {"type": "string"}},
        "next_research_actions": {"type": "array", "items": {"type": "string"}},
        "follow_up_questions": {
            "type": "array", "items": {"type": "object", "additionalProperties": True,
            "properties": {"question": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["question"]}},
    },
    "required": ["source_inventory", "slot_findings", "source_gaps", "next_research_actions", "follow_up_questions"],
}

AqlZopediaStructuredRunner = Callable[..., dict[str, Any]]

# Source identity and capture metadata are application-owned. Inference adds
# interpretation without paying to reproduce the acquired document catalog.
BUSINESS_MODEL_DOSSIER_ANALYSIS_SCHEMA = {
    **BUSINESS_MODEL_RESEARCH_DOSSIER_SCHEMA,
    "properties": {
        **{key: value for key, value in BUSINESS_MODEL_RESEARCH_DOSSIER_SCHEMA["properties"].items()
           if key != "source_inventory"},
        "source_annotations": {
            "type": "array", "items": {
                **BUSINESS_MODEL_RESEARCH_DOSSIER_SCHEMA["properties"]["source_inventory"]["items"],
                "required": ["evidence_ref"],
            },
        },
    },
    "required": [key for key in BUSINESS_MODEL_RESEARCH_DOSSIER_SCHEMA["required"] if key != "source_inventory"],
}


@dataclass
class TickerBusinessModelStackResult:
    symbol: str
    company_name: str
    status: str
    confidence: str
    source_page_ids: list[str]
    zopedia_page_ids_read: list[str]
    fundamental_datasets_used: list[str]
    research_plan_id: str
    research_query_plan: list[dict[str, Any]]
    search_request_ids: list[str]
    search_result_ids: list[str]
    research_dossier: dict[str, Any]
    slot_coverage: dict[str, dict[str, Any]]
    synthesis_warnings: list[str]
    slot_facts: dict[str, list[dict[str, Any]]]
    slot_gaps: list[str]
    business_story_markdown: str
    business_memory_page_id: str
    business_memory_body_markdown: str
    proposal_rows: list[dict[str, Any]]
    proposed_pages: list[dict[str, Any]]
    research_plan_rows: list[dict[str, Any]]
    search_request_rows: list[dict[str, Any]]
    search_result_rows: list[dict[str, Any]]
    evidence_pack_id: str
    run_id: str
    asof_time_utc: str
    created_at_utc: str

    @property
    def stack_id(self) -> str:
        return _stable_id("zopedia_ticker_business_model_stack", self.symbol, self.run_id)

    @property
    def proposal_ids(self) -> list[str]:
        return [_coerce_text(row.get("proposal_id")) for row in self.proposal_rows if _coerce_text(row.get("proposal_id"))]


def _news_rows_for_symbol(news_frame: pd.DataFrame | None, symbol: str, *, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(news_frame, pd.DataFrame) or news_frame.empty:
        return []
    rows = news_frame.copy()
    if "published_at" in rows.columns:
        rows["_published_at"] = pd.to_datetime(rows["published_at"], utc=True, errors="coerce")
        rows = rows.sort_values("_published_at", ascending=False, na_position="last")
    out: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        if symbol not in set(_event_symbols(row)):
            continue
        out.append(
            {
                "headline": _coerce_text(row.get("headline")),
                "summary": _coerce_text(row.get("summary")),
                "url": _coerce_text(row.get("url")),
                "published_at": _coerce_text(row.get("published_at")),
                "text": _event_text(row),
            }
        )
        if len(out) >= max(int(limit), 1):
            break
    return out


def _business_research_subject(symbol: str, company_name: str) -> str:
    clean_company = _clean_company_name_for_research(company_name)
    clean_symbol = _normalize_symbol(symbol)
    if clean_company and clean_company.upper() != clean_symbol:
        quoted_company = f'"{clean_company}"' if " " in clean_company else clean_company
        return f"{quoted_company} {clean_symbol}".strip()
    return clean_symbol


def _research_plan_id(*, symbol: str, run_id: str, query_plan: list[dict[str, Any]]) -> str:
    payload = json.dumps(query_plan, ensure_ascii=True, sort_keys=True, default=str)
    return _stable_id("zopedia_business_model_research_plan", symbol, run_id, payload)


def _default_query_for_slot(*, slot: str, symbol: str, company_name: str) -> str:
    subject = _business_research_subject(symbol, company_name)
    question = BUSINESS_MODEL_STACK_SLOT_QUESTIONS.get(slot) or slot.replace("_", " ")
    return f"{subject} {question}".strip()


def build_business_model_research_query_plan(
    *,
    symbol: str,
    company_name: str,
    missing_slots: list[str] | None = None,
    max_queries: int = 12,
) -> list[dict[str, Any]]:
    requested = [_coerce_text(slot) for slot in list(missing_slots or []) if _coerce_text(slot) in BUSINESS_MEMORY_SLOTS]
    priority_slots = requested or list(BUSINESS_MODEL_RESEARCH_SLOT_ORDER)
    essential_slots = [
        "business_model",
        "products_and_services",
        "customer_demand",
        "fundamentals",
        "workforce_and_hiring",
        "employee_sentiment",
        "web_or_developer_attention",
        "policy_or_regulatory_environment",
        "execution_risks",
    ]
    ordered_slots = list(dict.fromkeys([slot for slot in essential_slots if slot in priority_slots] + priority_slots))
    query_plan: list[dict[str, Any]] = []
    for priority, slot in enumerate(ordered_slots[: max(int(max_queries), 1)], start=1):
        topic = "news" if slot in {"confirmation_events", "invalidation_events", "customer_demand"} else "general"
        query_plan.append(
            {
                "slot": slot,
                "question": BUSINESS_MODEL_STACK_SLOT_QUESTIONS.get(slot, slot.replace("_", " ")),
                "query": (
                    f"{company_name or symbol} {symbol} bull bear stock analysis Seeking Alpha business risks fundamentals"
                    if slot == "execution_risks"
                    else _default_query_for_slot(slot=slot, symbol=symbol, company_name=company_name)
                ),
                "topic": topic,
                "source_intent": "investor_debate" if slot == "execution_risks" else "business_slot_seed",
                "requires_page_open": True,
                "priority": priority,
            }
        )
    return query_plan


def _research_request_id(*, plan_id: str, provider: str, slot: str, query: str) -> str:
    return _stable_id("zopedia_business_model_search_request", plan_id, provider, slot, query)


def _research_result_id(*, request_id: str, provider: str, title: str, url: str) -> str:
    return _stable_id("zopedia_business_model_search_result", request_id, provider, url or title)


def _web_result_text(item: Any) -> str:
    return _coerce_text(getattr(item, "raw_text", "")) or _coerce_text(getattr(item, "snippet", ""))


def _open_official_research_pages_enabled() -> bool:
    raw = _coerce_text(os.getenv("ZOPEDIA_TICKER_BUSINESS_OPEN_OFFICIAL_PAGES"))
    if not raw:
        return True
    return raw.lower() not in {"0", "false", "no", "off", "disabled"}


def _open_research_page_payload(url: str, *, max_chars: int = 4000) -> dict[str, Any]:
    clean_url = _coerce_text(url)
    if not clean_url:
        return {}

    page = browse_page(
        clean_url,
        max_text_chars=max(int(max_chars), 800),
        require_main_content=True,
        min_text_chars=800,
    )
    if page_quality_issue(page, min_text_chars=280) and not _coerce_text(page.get("text")):
        return {}
    payload = dict(page)
    payload["text"] = _coerce_text(page.get("text"))[:max_chars]
    payload["quality_issue"] = _coerce_text(payload.get("quality_issue")) or page_quality_issue(page, min_text_chars=280)
    return payload


def _is_seeking_alpha_research_result(*, source: str, url: str, title: str = "") -> bool:
    if is_seeking_alpha_url(url):
        return True
    haystack = f"{_coerce_text(source)} {_coerce_text(title)}".lower()
    return "seeking alpha" in haystack


def _should_open_research_result(
    *,
    query_spec: dict[str, Any],
    url: str,
    authority_bucket: str,
    source: str = "",
    title: str = "",
) -> bool:
    if not _open_official_research_pages_enabled():
        return False
    if not _coerce_text(url).lower().startswith(("http://", "https://")):
        return False
    if _is_seeking_alpha_research_result(source=source, url=url, title=title):
        return True
    clean_bucket = _coerce_text(authority_bucket).lower()
    if clean_bucket in {"official", "regulator"}:
        return True
    if not bool(query_spec.get("requires_page_open")):
        return False
    source_intent = _coerce_text(query_spec.get("source_intent")).lower()
    if source_intent in {"quote", "stock_quote", "stock_price", "technical"}:
        return False
    return clean_bucket in {"wire", "press", "web"}


def _search_results_for_query(
    *,
    plan_id: str,
    run_id: str,
    asof_time_utc: str,
    symbol: str,
    company_name: str,
    query_spec: dict[str, Any],
    serp_client: Any | None,
    tavily_client: Any | None,
    max_results_per_query: int,
    created_at_utc: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    slot = _coerce_text(query_spec.get("slot"))
    query = _coerce_text(query_spec.get("query"))
    topic = _coerce_text(query_spec.get("topic")) or "general"
    source_intent = _coerce_text(query_spec.get("source_intent"))
    requires_page_open = bool(query_spec.get("requires_page_open"))
    priority = int(query_spec.get("priority") or 0)
    requests: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    primary_provider = search_client_provider(serp_client) or "primary_search"
    providers: list[tuple[str, Any]] = [(primary_provider, serp_client), ("tavily", tavily_client)]
    for provider, client in providers:
        if client is None or not query:
            continue
        request_id = _research_request_id(plan_id=plan_id, provider=provider, slot=slot, query=query)
        requests.append(
            {
                "request_id": request_id,
                "plan_id": plan_id,
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "symbol": symbol,
                "company_name": company_name,
                "slot": slot,
                "source_intent": source_intent,
                "requires_page_open": requires_page_open,
                "provider": provider,
                "topic": topic,
                "query": query,
                "priority": priority,
                "created_at_utc": created_at_utc,
            }
        )
        try:
            _throttle_business_research_request(
                key=f"provider:{provider}",
                delay_seconds=_business_research_provider_delay_seconds(),
            )
            if provider != "tavily":
                provider_results = client.search(query, news=(topic == "news"), num=max(int(max_results_per_query), 1))
            else:
                provider_results = client.search(query, max_results=max(int(max_results_per_query), 1), topic=topic)
        except (WebResearchError, Exception) as exc:
            results.append(
                {
                    "result_id": _research_result_id(request_id=request_id, provider=provider, title="error", url=""),
                    "request_id": request_id,
                    "plan_id": plan_id,
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "symbol": symbol,
                    "company_name": company_name,
                    "slot": slot,
                    "provider": provider,
                    "source_intent": source_intent,
                    "title": "",
                    "url": "",
                    "snippet": "",
                    "raw_text": "",
                    "source": provider,
                    "published_at": "",
                    "authority_bucket": "web",
                    "authority_rank": 3,
                    "opened_page": False,
                    "browse_mode": "",
                    "browse_warning": "",
                    "page_quality_issue": "",
                    "error_text": _safe_error_text(exc, limit=220),
                    "created_at_utc": created_at_utc,
                }
            )
            continue
        opened_pages = 0
        opened_seeking_alpha_pages = 0
        opened_page_attempts = 0
        max_opened_pages = _business_research_open_pages_per_query()
        max_seeking_alpha_pages = _business_research_seeking_alpha_pages_per_query()
        max_open_attempts = _business_research_open_attempts_per_query()
        open_page_chars = _business_research_open_page_chars()
        for item in list(provider_results or [])[: max(int(max_results_per_query), 1)]:
            title = _coerce_text(getattr(item, "title", ""))
            url = _coerce_text(getattr(item, "url", ""))
            snippet = _coerce_text(getattr(item, "snippet", ""))
            raw_text = _web_result_text(item)
            source = _coerce_text(getattr(item, "source", "")) or provider
            authority_bucket, authority_rank = _source_authority_bucket(source, url)
            opened_page = False
            browse_mode = ""
            browse_warning = ""
            opened_page_quality_issue = ""
            is_seeking_alpha = _is_seeking_alpha_research_result(source=source, url=url, title=title)
            if not title and not snippet and not raw_text:
                continue
            if (
                (opened_pages < max_opened_pages or (is_seeking_alpha and opened_seeking_alpha_pages < max_seeking_alpha_pages))
                and opened_page_attempts < max_open_attempts
                and _should_open_research_result(
                    query_spec=query_spec,
                    url=url,
                    authority_bucket=authority_bucket,
                    source=source,
                    title=title,
                )
            ):
                try:
                    opened_page_attempts += 1
                    _throttle_business_research_request(
                        key=_business_research_host_key(url),
                        delay_seconds=_business_research_host_delay_seconds(),
                    )
                    opened_payload = _open_research_page_payload(url, max_chars=open_page_chars)
                except Exception:
                    opened_payload = {}
                opened_text = _coerce_text(opened_payload.get("text"))
                opened_page_quality_issue = _coerce_text(opened_payload.get("quality_issue"))
                if opened_text:
                    opened_page = True
                    browse_mode = _coerce_text(opened_payload.get("mode"))
                    browse_warning = _safe_error_text(opened_payload.get("warning"), limit=360)
                    if not opened_page_quality_issue:
                        opened_page_quality_issue = _source_text_quality_issue(opened_text)
                    if not opened_page_quality_issue:
                        raw_text = opened_text
                        opened_pages += 1
                        if is_seeking_alpha:
                            opened_seeking_alpha_pages += 1
            results.append(
                {
                    "result_id": _research_result_id(request_id=request_id, provider=provider, title=title, url=url),
                    "request_id": request_id,
                    "plan_id": plan_id,
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "symbol": symbol,
                    "company_name": company_name,
                    "slot": slot,
                    "provider": provider,
                    "source_intent": source_intent,
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "raw_text": raw_text,
                    "source": source,
                    "published_at": _coerce_text(getattr(item, "published_at", "")),
                    "authority_bucket": authority_bucket,
                    "authority_rank": authority_rank,
                    "opened_page": opened_page,
                    "browse_mode": browse_mode,
                    "browse_warning": browse_warning,
                    "page_quality_issue": opened_page_quality_issue,
                    "error_text": "",
                    "created_at_utc": created_at_utc,
                }
            )
    return requests, results


def _fresh_search_clients_like(
    serp_client: Any | None,
    tavily_client: Any | None,
) -> tuple[Any | None, Any | None]:
    def _fresh(client: Any | None) -> Any | None:
        if client is None:
            return None
        config = getattr(client, "config", None)
        if config is None:
            return client
        try:
            return client.__class__(config)
        except Exception:
            return client

    return _fresh(serp_client), _fresh(tavily_client)


def _research_rows_for_symbol(frame: pd.DataFrame | None, symbol: str) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns:
        return []
    rows = frame.copy()
    rows["symbol"] = rows["symbol"].map(_normalize_symbol)
    rows = rows[rows["symbol"] == symbol].copy()
    if rows.empty:
        return []
    if "error_text" in rows.columns:
        rows = rows[rows["error_text"].astype(str).str.strip() == ""].copy()
    return rows.to_dict("records")


def _research_evidence_by_slot(
    research_rows: list[dict[str, Any]],
    *,
    symbol: str,
    company_name: str,
    limit_per_slot: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in list(research_rows or []):
        slot = _coerce_text(row.get("slot"))
        if slot not in BUSINESS_MEMORY_SLOTS:
            continue
        candidate_item = {
            "title": row.get("title"),
            "text": row.get("raw_text") or row.get("snippet") or row.get("title"),
            "source": row.get("source") or row.get("provider"),
            "url": row.get("url"),
            "authority_bucket": row.get("authority_bucket"),
            "source_intent": row.get("source_intent"),
        }
        if not _research_item_matches_company(candidate_item, symbol=symbol, company_name=company_name):
            continue
        if _low_signal_research_title(row.get("title"), slot=slot):
            continue
        quality_issue = _coerce_text(row.get("page_quality_issue"))
        if quality_issue.startswith(("blocked", "captcha", "navigation_dominated", "source_boilerplate")):
            continue
        if _source_text_quality_issue(candidate_item.get("text")):
            continue
        evidence_text = _coerce_text(row.get("raw_text") or row.get("snippet") or row.get("title"))
        title = _coerce_text(row.get("title"))
        if not evidence_text and not title:
            continue
        grouped.setdefault(slot, [])
        if len(grouped[slot]) >= max(int(limit_per_slot), 1):
            continue
        grouped[slot].append(
            {
                "result_id": _coerce_text(row.get("result_id")),
                "title": title,
                "source": _coerce_text(row.get("source") or row.get("provider")),
                "url": _coerce_text(row.get("url")),
                "published_at": _coerce_text(row.get("published_at")),
                "authority_bucket": _coerce_text(row.get("authority_bucket")),
                "authority_rank": row.get("authority_rank"),
                "source_intent": _coerce_text(row.get("source_intent")),
                "snippet": _coerce_text(row.get("snippet")),
                "text": evidence_text[:1200],
            }
        )
    return grouped


def _low_signal_research_title(value: object, *, slot: str) -> bool:
    title = _coerce_text(value).lower()
    if not title:
        return False
    low_signal_phrases = (
        "analyst ratings",
        "dividend history",
        "earnings estimates",
        "historical data",
        "price target",
        "realtime quote",
        "real-time quote",
        "stock forecast",
        "stock price",
        "stock price, news, quote",
        "stock price, news, quotes",
        "stock quote",
        "quote & history",
    )
    if any(phrase in title for phrase in low_signal_phrases):
        return True
    if slot in {"workforce_and_hiring", "employee_sentiment", "web_or_developer_attention"}:
        return any(phrase in title for phrase in ("stock price", "quote & history", "analyst ratings"))
    return False


_LOW_SIGNAL_BUSINESS_TEXT_MARKERS: tuple[str, ...] = (
    "certification of principal executive officer",
    "date form description pdf xbrl pages",
    "filing type: view all",
    "financial news, news, press releases",
    "i have reviewed this annual report",
    "latest news",
    "pursuant to rule 13a-14",
    "section 302",
    "sign up for email alerts",
    "upcoming events",
    "view all press releases",
    "view all sec filings",
    "year: view all",
)

_LEGAL_COMPANY_IDENTITY_TOKENS: frozenset[str] = frozenset(
    {
        "adr",
        "ads",
        "class",
        "co",
        "common",
        "company",
        "corp",
        "corporation",
        "depositary",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "ltd",
        "ordinary",
        "plc",
        "shares",
        "stock",
    }
)

_GENERIC_COMPANY_IDENTITY_TOKENS: frozenset[str] = frozenset(
    {
        "capital",
        "core",
        "financial",
        "fund",
        "global",
        "group",
        "holding",
        "holdings",
        "main",
        "management",
        "partners",
        "scientific",
        "services",
        "street",
        "systems",
        "technologies",
        "technology",
        "test",
    }
)


def _research_item_blob(item: dict[str, Any]) -> str:
    return " ".join(
        _coerce_text(item.get(key))
        for key in ("title", "text", "source", "url", "authority_bucket")
        if _coerce_text(item.get(key))
    ).lower()


def _company_identity_terms(*, symbol: str, company_name: str) -> list[str]:
    clean_company = re.sub(r"\s+", " ", _clean_company_name_for_research(company_name).lower()).strip()
    if clean_company:
        tokens = [token for token in re.split(r"[^a-z0-9]+", clean_company) if token]
        identity_tokens = [token for token in tokens if token not in _LEGAL_COMPANY_IDENTITY_TOKENS]
        identity_name = " ".join(identity_tokens)
        distinctive_tokens = [
            token
            for token in identity_tokens
            if len(token) >= 4 and token not in _GENERIC_COMPANY_IDENTITY_TOKENS
        ]
        terms = [clean_company, identity_name]
        if len(distinctive_tokens) >= 2:
            terms.append(" ".join(distinctive_tokens[:2]))
        terms.extend(distinctive_tokens[:2])
        return list(dict.fromkeys(term for term in terms if len(term) >= 3))
    clean_symbol = _coerce_text(symbol).lower()
    return [clean_symbol] if clean_symbol else []


def _source_text_quality_issue(value: object) -> str:
    text = _coerce_text(value).lower()
    if not text:
        return "empty"
    if any(marker in text for marker in _LOW_SIGNAL_BUSINESS_TEXT_MARKERS):
        return "source_boilerplate"
    return ""


def _research_item_matches_company(item: dict[str, Any], *, symbol: str, company_name: str) -> bool:
    blob = _research_item_blob(item)
    terms = _company_identity_terms(symbol=symbol, company_name=company_name)
    if terms:
        return any(term in blob for term in terms)
    clean_symbol = _coerce_text(symbol).lower()
    if clean_symbol:
        return clean_symbol in blob
    return False


def _fact_source_has_zopedia_verdict(value: object) -> bool:
    source = _coerce_text(value)
    return source == "aql_zopedia_agent" or source.startswith("aql_zopedia_agent::") or source == "zopedia_business_memory"


def _slot_has_zopedia_verdict(facts: list[dict[str, Any]] | None) -> bool:
    return any(_fact_source_has_zopedia_verdict(item.get("source")) for item in list(facts or []) if isinstance(item, dict))


def _business_stack_ready_gaps(slot_facts: dict[str, list[dict[str, Any]]]) -> list[str]:
    gaps = [
        slot
        for slot in BUSINESS_MODEL_READY_REQUIRED_SLOTS
        if not _slot_has_zopedia_verdict(slot_facts.get(slot))
    ]
    if not any(_slot_has_zopedia_verdict(slot_facts.get(slot)) for slot in BUSINESS_MODEL_READY_SIGNAL_SLOTS):
        gaps.append("customer_demand_or_business_confirmation")
    return gaps


def _prioritize_zopedia_verdict_facts(slot_facts: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    ordered: dict[str, list[dict[str, Any]]] = {}
    for slot, facts in (slot_facts or {}).items():
        verdict_facts = [item for item in list(facts or []) if isinstance(item, dict) and _fact_source_has_zopedia_verdict(item.get("source"))]
        source_facts = [item for item in list(facts or []) if isinstance(item, dict) and not _fact_source_has_zopedia_verdict(item.get("source"))]
        if verdict_facts or source_facts:
            ordered[slot] = verdict_facts + source_facts
    return ordered


def _filter_resolved_slot_warnings(warnings: list[str], slot_facts: dict[str, list[dict[str, Any]]]) -> list[str]:
    resolved_slots = {slot for slot, facts in (slot_facts or {}).items() if _slot_has_zopedia_verdict(facts)}
    out: list[str] = []
    for warning in list(warnings or []):
        clean = _coerce_text(warning)
        if not clean:
            continue
        if clean.startswith("aql_zopedia_slot_unresolved::"):
            parts = clean.split("::", 2)
            slot = parts[1] if len(parts) > 1 else ""
            if slot in resolved_slots:
                continue
        if clean.startswith("aql_zopedia_research_plan_gap::"):
            slot = clean.split("::", 1)[1]
            if slot in resolved_slots:
                continue
        out.append(clean)
    return out


def _slot_coverage(
    *,
    slot_facts: dict[str, list[dict[str, Any]]],
    query_plan: list[dict[str, Any]],
    research_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    planned_slots = {_coerce_text(item.get("slot")) for item in list(query_plan or []) if _coerce_text(item.get("slot"))}
    searched_slots = {_coerce_text(row.get("slot")) for row in list(research_rows or []) if _coerce_text(row.get("slot"))}
    coverage: dict[str, dict[str, Any]] = {}
    for slot in BUSINESS_MEMORY_SLOTS:
        fact_count = len(slot_facts.get(slot) or [])
        verdict_count = sum(
            1
            for item in list(slot_facts.get(slot) or [])
            if isinstance(item, dict) and _fact_source_has_zopedia_verdict(item.get("source"))
        )
        evidence_count = sum(1 for row in research_rows if _coerce_text(row.get("slot")) == slot and not _coerce_text(row.get("error_text")))
        if verdict_count:
            status = "supported"
        elif fact_count:
            status = "source_facts_need_zopedia_verdict"
        elif evidence_count:
            status = "searched_needs_synthesis"
        elif slot in searched_slots:
            status = "searched_no_evidence"
        elif slot in planned_slots:
            status = "planned_not_searched"
        else:
            status = "not_planned"
        coverage[slot] = {
            "status": status,
            "fact_count": fact_count,
            "verdict_fact_count": verdict_count,
            "evidence_count": evidence_count,
            "planned": slot in planned_slots,
        }
    return coverage


def _base_slot_facts(
    *,
    symbol: str,
    company_name: str,
    baseline: dict[str, Any],
    fundamentals: list[dict[str, Any]],
    edgar_rows: list[dict[str, Any]],
    business_memory_pages: list[dict[str, Any]],
    recent_news_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    slot_facts: dict[str, list[dict[str, Any]]] = {slot: [] for slot in BUSINESS_MEMORY_SLOTS}
    baseline_text = _coerce_text(
        baseline.get("company_background_text")
        or baseline.get("description_text")
        or baseline.get("business_description")
    )
    _add_slot(slot_facts, "business_model", baseline_text, source="company_baselines")
    lens = _coerce_text(baseline.get("business_lens"))
    if lens:
        _add_slot(slot_facts, "products_and_services", f"Business lens: {lens}", source="company_baselines", confidence="low")
    for page in business_memory_pages[:3]:
        page_metadata = _json_dict(page.get("metadata") or page.get("metadata_json"))
        if page_metadata.get("source_type") in {
            "news_business_resolution_business_memory",
            TICKER_BUSINESS_MODEL_STACK_SOURCE_TYPE,
        }:
            for slot, facts in _source_backed_memory_facts(page).items():
                for fact in facts:
                    _add_slot(
                        slot_facts,
                        slot,
                        fact.get("text"),
                        source=_coerce_text(fact.get("source")) or "zopedia_business_memory",
                        confidence=_coerce_text(fact.get("confidence")) or "medium",
                    )
            continue
        page_title = _coerce_text(page.get("title"))
        page_summary = _coerce_text(page.get("summary") or page.get("body_markdown"))
        _add_slot(
            slot_facts,
            "business_model",
            f"{page_title}: {page_summary}" if page_title else page_summary,
            source="zopedia_business_memory",
        )
    for item in fundamentals[:8]:
        metric = _coerce_text(item.get("metric"))
        if not metric:
            continue
        value = item.get("value")
        date = _coerce_text(item.get("report_date"))
        text = f"{metric}: {value}" + (f" ({date})" if date else "")
        _add_slot(slot_facts, "fundamentals", text, source="quarterly_fundamentals")
    for row in recent_news_rows[:3]:
        text = _coerce_text(row.get("text") or row.get("headline"))
        if text:
            _add_slot(slot_facts, "confirmation_events", text, source="news_articles", confidence="medium")
    for row in edgar_rows[:3]:
        filing_text = _coerce_text(row.get("text"))
        form = _coerce_text(row.get("form"))
        if filing_text:
            _add_slot(slot_facts, "confirmation_events", f"{form}: {filing_text}" if form else filing_text, source="edgar_evidence")
    return {slot: facts for slot, facts in slot_facts.items() if facts}


def _default_aql_zopedia_structured_runner(**kwargs: Any) -> dict[str, Any]:
    from ..aql_zopedia_engine import run_aql_zopedia_structured_agent

    return run_aql_zopedia_structured_agent(**kwargs)


def _compact_json(value: Any, *, limit: int = 12000) -> str:
    # Preserve all model context; share exact repeated values by reference.
    return serialize_llm_context(value)


def _agent_runner_or_none(
    *,
    zopedia_agent_runner: AqlZopediaStructuredRunner | None,
    llm_client: Any | None,
) -> AqlZopediaStructuredRunner | None:
    if zopedia_agent_runner is not None:
        return zopedia_agent_runner
    if llm_client is None:
        return None
    return _default_aql_zopedia_structured_runner


def _structured_agent_payload(
    *,
    zopedia_agent_runner: AqlZopediaStructuredRunner | None,
    llm_client: Any | None,
    query: str,
    schema_name: str,
    schema: dict[str, Any],
    task: str,
    surface: str,
    max_tool_calls: int,
    research_subject: str = "",
    progress_callback: ProgressCallback | None = None,
    answer_review_owner: str = "agent",
    reuse_identity: str = "",
    artifact_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runner = _agent_runner_or_none(zopedia_agent_runner=zopedia_agent_runner, llm_client=llm_client)
    if runner is None:
        return {"status": "skipped", "payload": None, "error": "aql_zopedia_agent_not_configured"}
    try:
        with _business_aql_timeouts():
            result = runner(
                query=query,
                schema_name=schema_name,
                schema=schema,
                task=task,
                surface=surface,
                max_tool_calls=max_tool_calls,
                llm_client=llm_client,
                progress_callback=progress_callback,
                persist_findings=False,
                reuse_scope=current_research_access_scope(),
                research_subject=research_subject,
                answer_review_owner=answer_review_owner,
                reuse_identity=reuse_identity,
                artifact_context=artifact_context,
                **_business_agent_runtime_kwargs(),
            )
    except Exception as exc:
        mark_research_work_incomplete()
        return {
            "status": "failed",
            "payload": None,
            "error": f"{type(exc).__name__}: {_safe_error_text(exc, limit=260)}",
        }
    if not isinstance(result, dict) or result.get("status") != "completed":
        mark_research_work_incomplete()
    return result if isinstance(result, dict) else {"status": "failed", "payload": None, "error": "AQL/Zopedia agent returned a non-dict result."}


def _normalize_research_query_plan_payload(payload: dict[str, Any], *, max_queries: int) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    resolved_company_name = _coerce_text(payload.get("resolved_company_name"))
    company_aliases = [_coerce_text(item) for item in list(payload.get("company_aliases") or []) if _coerce_text(item)]
    for item in list(payload.get("queries") or []):
        if not isinstance(item, dict):
            continue
        slot = _coerce_text(item.get("slot"))
        query = _coerce_text(item.get("query"))
        if not slot or not query:
            continue
        signature = (slot, re.sub(r"\s+", " ", query.lower()).strip())
        if signature in seen:
            continue
        seen.add(signature)
        topic = _coerce_text(item.get("topic")).lower()
        if topic not in {"general", "news"}:
            topic = "general"
        try:
            priority = int(item.get("priority") or len(out) + 1)
        except Exception:
            priority = len(out) + 1
        out.append(
            {
                "slot": slot,
                "question": _coerce_text(item.get("question")) or BUSINESS_MODEL_STACK_SLOT_QUESTIONS.get(slot, slot.replace("_", " ")),
                "query": query,
                "topic": topic,
                "source_intent": _coerce_text(item.get("source_intent")) or "llm_planned_business_evidence",
                "requires_page_open": bool(item.get("requires_page_open")),
                "priority": priority,
                "reason": _coerce_text(item.get("reason")),
                "resolved_company_name": resolved_company_name,
                "company_aliases": company_aliases,
                "plan_mode": "aql_zopedia_planned",
            }
        )
        if len(out) >= max(int(max_queries), 1):
            break
    return sorted(out, key=lambda row: int(row.get("priority") or 9999))


def _company_name_is_weak(symbol: str, company_name: str) -> bool:
    symbol_text = _normalize_symbol(symbol)
    name = _coerce_text(company_name)
    if not name:
        return True
    if name.upper() == symbol_text:
        return True
    return len(name) <= 6 and " " not in name


def _resolved_company_name_from_query_plan(query_plan: list[dict[str, Any]], *, symbol: str, current_name: str) -> str:
    if not _company_name_is_weak(symbol, current_name):
        return current_name
    for item in list(query_plan or []):
        resolved = _coerce_text(item.get("resolved_company_name"))
        if resolved and not _company_name_is_weak(symbol, resolved):
            return resolved
    return current_name


def _business_research_plan_query(
    *,
    symbol: str,
    company_name: str,
    baseline: dict[str, Any],
    fundamentals: list[dict[str, Any]],
    edgar_rows: list[dict[str, Any]],
    recent_news_rows: list[dict[str, Any]],
    zopedia_pages: list[dict[str, Any]],
    initial_slot_facts: dict[str, list[dict[str, Any]]],
    missing_slots: list[str],
    max_queries: int,
) -> str:
    context = {
        "symbol": symbol,
        "company_name": company_name,
        "prior_company_research": latest_research_artifact(
            access_scope=current_research_access_scope(), subject="company:" + symbol,
            task="ticker_business_model_research_dossier") if current_research_access_scope() else None,
        "business_memory_slots": BUSINESS_MODEL_STACK_SLOT_QUESTIONS,
        "missing_slots": missing_slots,
        "company_identity_task": {
            "instruction": "Resolve the actual company display/legal name for this ticker and include useful aliases.",
            "input_symbol": symbol,
            "input_company_name": company_name,
        },
        "company_baseline": baseline,
        "fundamentals": fundamentals[:8],
        "edgar_evidence": edgar_rows[:4],
        "recent_news": recent_news_rows[:4],
        "related_zopedia_pages": [
            {
                "page_id": _coerce_text(page.get("page_id")),
                "page_type": _coerce_text(page.get("page_type")),
                "title": _coerce_text(page.get("title")),
                "summary": _coerce_text(page.get("summary")),
                "metadata": _json_dict(page.get("metadata") or page.get("metadata_json")),
            }
            for page in zopedia_pages
        ],
        "existing_slot_facts": initial_slot_facts,
        "max_queries": max_queries,
    }
    shared = {key: context[key] for key in (
        "company_baseline", "fundamentals", "edgar_evidence", "recent_news"
    ) if key in context}
    request = {key: value for key, value in context.items() if key not in shared}
    instructions = (
        "Plan the external evidence acquisition for a ticker-specific business model stack. "
        "Return concrete search queries only; do not answer the business questions. "
        "Recover practical company identity and aliases while planning, but do not treat a ticker-only seed as a blocker. "
        "Use the ticker plus any recovered company or platform identity in queries so smaller companies do not get lost in generic theme results. "
        "Use prior_company_research as dated work already completed. Plan evidence deltas, contradictions, confirmation and worthwhile exploration rather than repeating the whole profile. Consider all business questions, but choose queries for their expected additional usefulness. Return an empty queries array when no new acquisition would help this update. Sparse and indirect leads remain valid; uncertainty alone does not suppress exploration. "
        "These sources sharpen what must be investigated next; they are research-direction inputs, not final authority. "
        "For source_intent, describe the intended source class in plain machine-readable words, such as company_filing, investor_presentation, earnings_transcript, credible_news, employee_reviews, hiring_page, web_traffic, policy_regulatory, or customer_demand. "
        "Set requires_page_open=true when snippets are unlikely to be enough and the page body should be opened through the shared crawler path. "
        "Do not use stock-price, chart, or quote-page queries.\n\n"
    )
    return render_research_prompt(evidence=shared, request=request, instructions=instructions)


def _zopedia_research_query_plan(
    *,
    zopedia_agent_runner: AqlZopediaStructuredRunner | None,
    llm_client: Any | None,
    symbol: str,
    company_name: str,
    baseline: dict[str, Any],
    fundamentals: list[dict[str, Any]],
    edgar_rows: list[dict[str, Any]],
    recent_news_rows: list[dict[str, Any]],
    zopedia_pages: list[dict[str, Any]],
    initial_slot_facts: dict[str, list[dict[str, Any]]],
    max_queries: int,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    missing_slots = _slot_gaps(initial_slot_facts)
    plan_query = _business_research_plan_query(
        symbol=symbol,
        company_name=company_name,
        baseline=baseline,
        fundamentals=fundamentals,
        edgar_rows=edgar_rows,
        recent_news_rows=recent_news_rows,
        zopedia_pages=zopedia_pages,
        initial_slot_facts=initial_slot_facts,
        missing_slots=missing_slots,
        max_queries=max_queries,
    )
    runner = _agent_runner_or_none(zopedia_agent_runner=zopedia_agent_runner, llm_client=llm_client)
    if runner is None:
        return [], ["aql_zopedia_research_plan_unavailable::agent_not_configured"]
    plan_max_tool_calls = _business_research_plan_max_tool_calls()
    result = _structured_agent_payload(
        zopedia_agent_runner=runner,
        llm_client=llm_client,
        query=plan_query,
        answer_review_owner="downstream",
        schema_name="ticker_business_model_research_query_plan",
        schema=BUSINESS_MODEL_RESEARCH_QUERY_PLAN_SCHEMA,
        task="ticker_business_model_research_plan",
        surface="pipeline.ticker_business_model_stack.research_plan",
        max_tool_calls=plan_max_tool_calls,
        progress_callback=progress_callback,
        research_subject="company:" + symbol,
    )
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else None
    if payload is None:
        error = _coerce_text(result.get("error")) or _coerce_text(result.get("status")) or "unknown_error"
        retry_query = (
            f"{plan_query}\n\n"
            "The previous AQL/Zopedia planner call failed before producing a usable payload. "
            "Retry once and return the requested structured research query plan. "
            "Do not answer the business questions; plan evidence acquisition only."
        )
        retry_result = _structured_agent_payload(
            zopedia_agent_runner=runner,
            llm_client=llm_client,
            query=retry_query,
            answer_review_owner="downstream",
        schema_name="ticker_business_model_research_query_plan",
            schema=BUSINESS_MODEL_RESEARCH_QUERY_PLAN_SCHEMA,
            task="ticker_business_model_research_plan_retry_after_error",
            surface="pipeline.ticker_business_model_stack.research_plan",
            max_tool_calls=max(plan_max_tool_calls, 5),
            progress_callback=progress_callback,
        )
        retry_payload = retry_result.get("payload") if isinstance(retry_result.get("payload"), dict) else None
        if retry_payload is None:
            retry_error = _coerce_text(retry_result.get("error")) or _coerce_text(retry_result.get("status")) or "unknown_error"
            return [], [
                f"aql_zopedia_research_plan::{_safe_error_text(error, limit=180)}",
                f"aql_zopedia_research_plan_retry::{_safe_error_text(retry_error, limit=180)}",
            ]
        payload = retry_payload
    plan = _normalize_research_query_plan_payload(payload, max_queries=max_queries)
    warnings = [
        f"aql_zopedia_research_plan_gap::{_safe_error_text(item, limit=160)}"
        for item in list(payload.get("data_gaps") or [])
        if _coerce_text(item)
    ]
    return plan, warnings


def _agent_result_evidence_ref(result: dict[str, Any], *, slot: str = "") -> str:
    agent_result = result.get("agent_result") if isinstance(result.get("agent_result"), dict) else {}
    evidence_pack_id = _coerce_text(agent_result.get("aql_evidence_pack_id")) if isinstance(agent_result, dict) else ""
    run_id = _coerce_text(agent_result.get("run_id")) if isinstance(agent_result, dict) else ""
    anchor = evidence_pack_id or run_id
    if slot and anchor:
        return f"aql_zopedia_agent::{slot}::{anchor}"
    if slot:
        return f"aql_zopedia_agent::{slot}"
    return f"aql_zopedia_agent::{anchor}" if anchor else "aql_zopedia_agent"


def _normalized_business_confidence(value: object, *, default: str = "low") -> str:
    confidence = _coerce_text(value).lower().replace("_", "-").strip()
    if confidence in {"high", "medium", "low"}:
        return confidence
    if "low" in confidence:
        return "low"
    if "medium" in confidence:
        return "medium"
    if "high" in confidence:
        return "high"
    return default if default in {"high", "medium", "low"} else "low"


def _add_slot_with_evidence_refs(
    slot_facts: dict[str, list[dict[str, Any]]],
    slot: str,
    text: object,
    *,
    source: str,
    confidence: str = "medium",
    evidence_refs: list[str] | None = None,
) -> None:
    before_count = len(slot_facts.get(slot) or [])
    _add_slot(slot_facts, slot, text, source=source, confidence=confidence)
    after_count = len(slot_facts.get(slot) or [])
    if after_count > before_count and evidence_refs:
        slot_facts[slot][-1]["evidence_refs"] = list(dict.fromkeys(_coerce_text(item) for item in evidence_refs if _coerce_text(item)))


def _compact_research_rows_for_dossier(research_rows: list[dict[str, Any]], *, limit: int = 80) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in list(research_rows or []):
        error_text = _coerce_text(row.get("error_text"))
        quality_issue = _coerce_text(row.get("page_quality_issue"))
        if error_text:
            source_status = "error"
        elif bool(row.get("opened_page")):
            source_status = "opened_page"
        elif quality_issue:
            source_status = f"quality_issue:{quality_issue}"
        else:
            source_status = "snippet_or_candidate"
        evidence_ref = _coerce_text(row.get("result_id") or row.get("request_id"))
        rows.append(
            {
                "evidence_ref": evidence_ref,
                "slot": _coerce_text(row.get("slot")),
                "source_intent": _coerce_text(row.get("source_intent")),
                "source_status": source_status,
                "provider": _coerce_text(row.get("provider")),
                "source": _coerce_text(row.get("source")),
                "authority_bucket": _coerce_text(row.get("authority_bucket")),
                "title": _coerce_text(row.get("title")),
                "url": _coerce_text(row.get("url")),
                "snippet": _coerce_text(row.get("snippet")),
                "text": _coerce_text(row.get("raw_text")),
                "published_at": _coerce_text(row.get("published_at")),
                "error_text": error_text[:240],
            }
        )
    return organize_source_captures(rows)


def _normalize_research_dossier_payload(payload: dict[str, Any], *, source_catalog: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    sources = {item["evidence_ref"]: dict(item) for item in payload.get("source_inventory") or []}
    for annotation in payload.get("source_annotations") or []:
        ref = annotation["evidence_ref"]
        sources[ref] = {**sources.get(ref, {}), **annotation}
    for source in source_catalog or []:
        ref = source["evidence_ref"]
        sources[ref] = {**sources.get(ref, {}), **source}
    source_inventory: list[dict[str, Any]] = []
    for item in sources.values():
        if not isinstance(item, dict):
            continue
        source_inventory.append(
            {
                **item,
                "evidence_ref": _coerce_text(item.get("evidence_ref")),
                "slot": _coerce_text(item.get("slot")),
                "source_class": _coerce_text(item.get("source_class")),
                "source_status": _coerce_text(item.get("source_status")),
                "title": _coerce_text(item.get("title")),
                "url": _coerce_text(item.get("url")),
                "relevance": _coerce_text(item.get("relevance")),
                "evidence_scope": _coerce_text(item.get("evidence_scope")) or "unknown",
                "business_use": _coerce_text(item.get("business_use")),
                "evidence_limits": _coerce_text(item.get("evidence_limits")),
                "confidence": _coerce_text(item.get("confidence")) or "low",
            }
        )
    slot_findings: dict[str, list[dict[str, Any]]] = {}
    raw_findings = payload.get("slot_findings") if isinstance(payload.get("slot_findings"), dict) else {}
    for slot, items in raw_findings.items():
        clean_slot = _coerce_text(slot)
        if not clean_slot:
            continue
        for item in list(items or []):
            if not isinstance(item, dict):
                continue
            finding = _coerce_text(item.get("finding"))
            evidence_refs = [_coerce_text(ref) for ref in list(item.get("evidence_refs") or []) if _coerce_text(ref)]
            missing_evidence = [_coerce_text(ref) for ref in list(item.get("missing_evidence") or []) if _coerce_text(ref)]
            slot_findings.setdefault(clean_slot, []).append(
                {
                    **item,
                    "finding": finding,
                    "status": _coerce_text(item.get("status")).lower(),
                    "confidence": _coerce_text(item.get("confidence")) or "low",
                    "evidence_refs": evidence_refs,
                    "missing_evidence": missing_evidence,
                }
            )
    return {
        **{key: value for key, value in payload.items() if key != "artifact_reference"},
        "source_inventory": source_inventory,
        "slot_findings": slot_findings,
        "source_gaps": [_coerce_text(item) for item in list(payload.get("source_gaps") or []) if _coerce_text(item)],
        "next_research_actions": [_coerce_text(item) for item in list(payload.get("next_research_actions") or []) if _coerce_text(item)],
        "follow_up_questions": [
            {**item, "question": _coerce_text(item.get("question")), "reason": _coerce_text(item.get("reason"))}
            for item in list(payload.get("follow_up_questions") or [])
            if isinstance(item, dict) and _coerce_text(item.get("question"))
        ],
    }


def _slot_facts_from_research_dossier(dossier: dict[str, Any], *, default_source: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    slot_findings = dossier.get("slot_findings") if isinstance(dossier.get("slot_findings"), dict) else {}
    for slot, findings in slot_findings.items():
        clean_slot = _coerce_text(slot)
        if clean_slot not in BUSINESS_MEMORY_SLOTS:
            continue
        for item in list(findings or []):
            if not isinstance(item, dict):
                continue
            finding = _coerce_text(item.get("finding"))
            confidence = _normalized_business_confidence(item.get("confidence"), default="low")
            evidence_refs = [_coerce_text(ref) for ref in list(item.get("evidence_refs") or []) if _coerce_text(ref)]
            if (
                not finding
                or confidence == "low"
                or not evidence_refs
            ):
                continue
            _add_slot_with_evidence_refs(
                out,
                clean_slot,
                finding,
                source=default_source,
                confidence=confidence if confidence in {"high", "medium"} else "medium",
                evidence_refs=evidence_refs,
            )
    return out


def _research_dossier_has_relevant_sources(dossier: dict[str, Any]) -> bool:
    for item in list(dossier.get("source_inventory") or []):
        if not isinstance(item, dict):
            continue
        relevance = _coerce_text(item.get("relevance")).lower()
        confidence = _coerce_text(item.get("confidence")).lower()
        if relevance in {"direct", "high", "moderate", "adjacent", "weak", "partial"} or confidence in {"medium", "high"}:
            return True
    return False


def _business_research_dossier_query(
    *,
    symbol: str,
    company_name: str,
    query_plan: list[dict[str, Any]],
    baseline: dict[str, Any],
    fundamentals: list[dict[str, Any]],
    edgar_rows: list[dict[str, Any]],
    recent_news_rows: list[dict[str, Any]],
    zopedia_pages: list[dict[str, Any]],
    initial_slot_facts: dict[str, list[dict[str, Any]]],
    business_research_evidence: dict[str, list[dict[str, Any]]],
    combined_research_rows: list[dict[str, Any]],
    include_prior: bool = True,
) -> str:
    context = {
        "prior_company_research": latest_research_artifact(
            access_scope=current_research_access_scope(), subject="company:" + symbol,
            task="ticker_business_model_research_dossier") if include_prior and current_research_access_scope() else None,
        "symbol": symbol,
        "company_name": company_name,
        "business_model_slot_questions": BUSINESS_MODEL_STACK_SLOT_QUESTIONS,
        "query_plan": query_plan,
        "company_baseline": baseline,
        "fundamentals": fundamentals[:8],
        "edgar_evidence": edgar_rows[:4],
        "recent_news": recent_news_rows[:5],
        "zopedia_pages_read": [
            {
                "page_id": _coerce_text(page.get("page_id")),
                "page_type": _coerce_text(page.get("page_type")),
                "title": _coerce_text(page.get("title")),
                "summary": _coerce_text(page.get("summary")),
                "body_markdown": _coerce_text(page.get("body_markdown")),
                "metadata": _json_dict(page.get("metadata") or page.get("metadata_json")),
            }
            for page in zopedia_pages
        ],
        "initial_slot_facts": initial_slot_facts,
        "source_acquisition_rows": _compact_research_rows_for_dossier(combined_research_rows, limit=80),
        "allowed_slots": list(BUSINESS_MEMORY_SLOTS),
    }
    shared = {key: context[key] for key in (
        "company_baseline", "fundamentals", "edgar_evidence", "recent_news"
    ) if key in context}
    request = {key: value for key, value in context.items() if key not in shared}
    instructions = (
        "Build the reusable company research dossier, including the resolved findings for all relevant business questions. Do not write the final business story. Reuse dated prior_company_research and update it against current evidence; distinguish older observations from new developments. "
        "Use the supplied source_acquisition_rows, local fundamentals, filings, news, and Zopedia pages as evidence inputs. "
        "Source acquisition groups all captures of a document under one evidence_ref. The application preserves the complete source catalog and capture metadata automatically. Do not reproduce source_inventory, titles, URLs or capture metadata. Return sparse source_annotations only where source-specific interpretation adds information; put substantive analysis in slot_findings. Every supplied capture retains its complete text, dates and query context. Repeated captures are not independent corroboration. "
        "A weak snippet, blocked article, or unopened but clearly relevant source is still evidence of a lead; do not collapse it into 'no evidence'. "
        "For each relevant business question, organize findings, useful hypotheses and indirect connections, with evidence and remaining questions. Keep uncertainty in the finding rather than erasing a useful connection. This dossier owns resolution; there is no later per-question interpretation pass. Each resolved question's complete findings replace its previous materialized findings: reconcile older and newer assertions, retain useful dated history and distinct information, and remove repetition. Do not rely on the application appending an older summary. "
        "If evidence is weak, say what it weakly supports and what must still be opened or verified. "
        "Describe which entities or relationships a useful source concerns in evidence_scope when it helps interpretation. Source annotations are optional; include details that improve understanding rather than repeating a metadata inventory for every document. Preserve useful additional research fields and topics. "
        "Use Seeking Alpha and similar investor-commentary sources to identify the strongest bull/bear questions and verification targets; do not treat opinion as authoritative proof unless the opened text cites direct company evidence. "
        "Use confidence values high, medium, or low for the specific claim. Judge relevance, agreement and limitations rather than requiring exact company attribution or a particular source class. Adjacent evidence and commentary can support useful connections. Confidence is not a calibrated probability or a publication gate. "
        "When extracting numeric metrics, preserve the metric label, segment, period, and source scope. Do not combine company-level revenue, segment revenue, guidance, backlog, AUM, NAV, or yield figures unless the source explicitly connects them. "
        "Do not turn a single earnings beat into a broad customer-demand verdict, a valuation/yield article into a fundamentals verdict, or related-platform liquidity news into a direct company risk unless the source ties it to this company. "
        "Keep event location and spillover separate: if a source says one company, platform, fund, or peer had an issue, do not rewrite it as sector-wide. Say it may affect sector sentiment only when the source says that. "
        "Keep adjacent company, peer, platform, sector, credit-cycle, rate-cycle, and policy evidence organized around the primary company. "
        "Request follow_up_questions only when evidence obtainable during this run could materially improve this investigation. Explain the expected information gain in reason. Use the execution date and source dates: put not-yet-released results and future publication checks in next_research_actions, with their expected timing, rather than sending the acquisition agent to search for them now. Missing data is not itself a reason to repeat searches. Keep longer-term possibilities in next_research_actions. Preserve broad exploration and company/sector/private-entity connections without requiring exact source identity. Do not use stock-price, chart, or technical-analysis commentary.\n\n"
    )
    return render_research_prompt(evidence=shared, request=request, instructions=instructions)


def _zopedia_research_dossier(
    *,
    zopedia_agent_runner: AqlZopediaStructuredRunner | None,
    llm_client: Any | None,
    symbol: str,
    company_name: str,
    query_plan: list[dict[str, Any]],
    baseline: dict[str, Any],
    fundamentals: list[dict[str, Any]],
    edgar_rows: list[dict[str, Any]],
    recent_news_rows: list[dict[str, Any]],
    zopedia_pages: list[dict[str, Any]],
    initial_slot_facts: dict[str, list[dict[str, Any]]],
    business_research_evidence: dict[str, list[dict[str, Any]]],
    combined_research_rows: list[dict[str, Any]],
    progress_callback: ProgressCallback | None = None,
) -> tuple[dict[str, Any], list[str]]:
    from ..llm_execution import best_effort_required
    if best_effort_required():
        # The final writer receives all raw inputs directly. An optional
        # intermediate organization call must not displace current synthesis.
        return {}, []
    runner = _agent_runner_or_none(zopedia_agent_runner=zopedia_agent_runner, llm_client=llm_client)
    if runner is None:
        return {}, []
    source_catalog = [
        {**{key: value for key, value in document.items() if key != "captures"},
         "captures": [{key: value for key, value in capture.items() if key not in {"text", "snippet"}}
                      for capture in document["captures"]]}
        for document in _compact_research_rows_for_dossier(combined_research_rows)
    ]
    result = _structured_agent_payload(
        zopedia_agent_runner=runner,
        llm_client=llm_client,
        query=_business_research_dossier_query(
            symbol=symbol,
            company_name=company_name,
            query_plan=query_plan,
            baseline=baseline,
            fundamentals=fundamentals,
            edgar_rows=edgar_rows,
            recent_news_rows=recent_news_rows,
            zopedia_pages=zopedia_pages,
            initial_slot_facts=initial_slot_facts,
            business_research_evidence=business_research_evidence,
            combined_research_rows=combined_research_rows,
        ),
        reuse_identity=_business_research_dossier_query(
            symbol=symbol, company_name=company_name, query_plan=query_plan, baseline=baseline,
            fundamentals=fundamentals, edgar_rows=edgar_rows, recent_news_rows=recent_news_rows,
            zopedia_pages=zopedia_pages, initial_slot_facts=initial_slot_facts,
            business_research_evidence=business_research_evidence, combined_research_rows=combined_research_rows, include_prior=False),
        schema_name="ticker_business_model_research_dossier",
        schema=BUSINESS_MODEL_DOSSIER_ANALYSIS_SCHEMA,
        task="ticker_business_model_research_dossier",
        surface="pipeline.ticker_business_model_stack.research_dossier",
        max_tool_calls=0,
        progress_callback=progress_callback,
        research_subject="company:" + symbol,
        artifact_context={"source_inventory": source_catalog},
    )
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else None
    if payload is None:
        error = _coerce_text(result.get("error")) or _coerce_text(result.get("status")) or "unknown_error"
        return {}, [f"aql_zopedia_research_dossier::{_safe_error_text(error, limit=180)}"]
    dossier = _normalize_research_dossier_payload(
        payload, source_catalog=(result.get("artifact_context") or {}).get("source_inventory") or source_catalog)
    reference = result.get("research_artifact") or result.get("artifact_reuse")
    if reference:
        dossier["artifact_reference"] = {**reference, "continuation_completed": bool(result.get("continuation_completed"))}
    return dossier, []


def _zopedia_dossier_followup(
    *, zopedia_agent_runner: AqlZopediaStructuredRunner | None, llm_client: Any | None,
    symbol: str, company_name: str, research_dossier: dict[str, Any],
    progress_callback: ProgressCallback | None = None,
) -> tuple[dict[str, Any], list[str]]:
    from ..llm_execution import best_effort_required
    if best_effort_required():
        return research_dossier, []
    questions = list(research_dossier.get("follow_up_questions") or [])
    if not questions or (research_dossier.get("artifact_reference") or {}).get("continuation_completed"):
        return research_dossier, []
    findings_by_id = {}
    for group, findings in (research_dossier.get("slot_findings") or {}).items():
        for finding in findings:
            identity = "finding_" + hashlib.sha256(_compact_json([group, finding]).encode()).hexdigest()
            findings_by_id[identity] = {**finding, "slot": group}
    finding_fields = BUSINESS_MODEL_RESEARCH_DOSSIER_SCHEMA["properties"]["slot_findings"]["additionalProperties"]["items"]
    delta_schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "finding_updates": {"type": "array", "items": {
                "type": "object", "additionalProperties": True,
                "properties": {**finding_fields["properties"], "slot": {"type": "string"},
                    "supersedes": {"type": "array", "items": {"type": "string", **({"enum": list(findings_by_id)} if findings_by_id else {})}}},
                "required": [*finding_fields["required"], "slot", "supersedes"]}},
            "source_updates": BUSINESS_MODEL_RESEARCH_DOSSIER_SCHEMA["properties"]["source_inventory"],
            "source_gaps": BUSINESS_MODEL_RESEARCH_DOSSIER_SCHEMA["properties"]["source_gaps"],
            "next_research_actions": BUSINESS_MODEL_RESEARCH_DOSSIER_SCHEMA["properties"]["next_research_actions"],
            "follow_up_questions": BUSINESS_MODEL_RESEARCH_DOSSIER_SCHEMA["properties"]["follow_up_questions"],
        },
        "required": ["finding_updates", "source_updates", "source_gaps", "next_research_actions", "follow_up_questions"],
    }
    query = render_research_prompt(
        evidence={"symbol": symbol, "company_name": company_name,
                  "source_inventory": research_dossier.get("source_inventory") or [],
                  "existing_findings": findings_by_id},
        request={"follow_up_questions": questions},
        instructions=("Continue this shared investigation. Acquire evidence together for related questions and "
            "reuse retained findings instead of repeating answered research. Return only useful additions and "
            "corrections in finding_updates, not a rewritten dossier. Use supersedes to identify findings that "
            "a new observation revises or contradicts; an empty supersedes array adds a finding. Unchanged "
            "findings are retained automatically. Record newly acquired or corrected source entries in source_updates. "
            "Keep dates and useful indirect connections. Return the remaining open source_gaps, next_research_actions "
            "and follow_up_questions for future work. Do not claim uncompleted questions were answered."),
    )
    result = _structured_agent_payload(
        zopedia_agent_runner=zopedia_agent_runner, llm_client=llm_client, query=query,
        schema_name="research_dossier_delta", schema=delta_schema,
        task="ticker_business_model_research_followup", surface="pipeline.ticker_business_model_stack.research_followup",
        answer_review_owner="downstream",
        max_tool_calls=_business_stack_max_tool_calls(), research_subject="company:" + symbol,
        progress_callback=progress_callback,
    )
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else None
    if payload is None:
        return research_dossier, ["aql_zopedia_followup::" + _safe_error_text(result.get("error") or result.get("status"))]
    retired = {identity for update in payload["finding_updates"] for identity in update["supersedes"]}
    if not retired.issubset(findings_by_id):
        return research_dossier, ["aql_zopedia_followup::invalid_finding_reference"]
    findings = {}
    for identity, finding in findings_by_id.items():
        if identity not in retired:
            group = finding["slot"]
            findings.setdefault(group, []).append({key: value for key, value in finding.items() if key != "slot"})
    for update in payload["finding_updates"]:
        group = update["slot"]
        findings.setdefault(group, []).append({key: value for key, value in update.items() if key not in {"slot", "supersedes"}})
    sources = {source["evidence_ref"]: source for source in research_dossier.get("source_inventory") or []}
    sources.update({source["evidence_ref"]: source for source in payload["source_updates"]})
    updated = _normalize_research_dossier_payload({
        **research_dossier,
        **{key: value for key, value in payload.items() if key not in {"finding_updates", "source_updates"}},
        "slot_findings": findings, "source_inventory": list(sources.values()),
    })
    reference = research_dossier.get("artifact_reference") or {}
    access = current_research_access_scope()
    if access and llm_client is not None:
        namespace, key = research_artifact_key(access_scope=access, subject="company:" + symbol,
            query=query, schema=delta_schema, config=llm_client.config)
        # The completed continuation supersedes the same input checkpoint, so a
        # stage resume reuses the completed investigation rather than repeating it.
        if reference.get("namespace") == namespace and reference.get("input_id"):
            key = reference["input_id"]
        new_reference = retain_research_artifact(namespace, key, subject="company:" + symbol,
            task="ticker_business_model_research_dossier",
            result={"status": "completed", "payload": updated, "agent_result": result.get("agent_result") or {},
                    "parent_artifact_id": reference.get("artifact_id"), "continuation_completed": True})
        if new_reference:
            updated["artifact_reference"] = {**new_reference, "continuation_completed": True}
    return updated, []


def _business_stack_query(
    *,
    symbol: str,
    company_name: str,
    baseline: dict[str, Any],
    fundamentals: list[dict[str, Any]],
    edgar_rows: list[dict[str, Any]],
    recent_news_rows: list[dict[str, Any]],
    zopedia_pages: list[dict[str, Any]],
    research_dossier: dict[str, Any],
    business_research_evidence: dict[str, list[dict[str, Any]]],
    initial_slot_facts: dict[str, list[dict[str, Any]]],
    slot_gaps: list[str],
) -> str:
    context = {
        "symbol": symbol,
        "company_name": company_name,
        "business_model_slot_questions": BUSINESS_MODEL_STACK_SLOT_QUESTIONS,
        "company_baseline": baseline,
        "fundamentals": fundamentals,
        "edgar_evidence": edgar_rows,
        "recent_news": recent_news_rows,
        "zopedia_pages_read": [
            {
                "page_id": _coerce_text(page.get("page_id")),
                "page_type": _coerce_text(page.get("page_type")),
                "title": _coerce_text(page.get("title")),
                "summary": _coerce_text(page.get("summary")),
                "body_markdown": _coerce_text(page.get("body_markdown")),
                "metadata": _json_dict(page.get("metadata") or page.get("metadata_json")),
            }
            for page in zopedia_pages
        ],
        "research_dossier": research_dossier,
        "business_research_evidence": business_research_evidence,
        "initial_slot_facts": initial_slot_facts,
        "slot_gaps": slot_gaps,
        "allowed_slots": list(BUSINESS_MEMORY_SLOTS),
    }
    shared = {key: context[key] for key in (
        "company_baseline", "fundamentals", "edgar_evidence", "recent_news"
    ) if key in context}
    request = {key: value for key, value in context.items() if key not in shared}
    instructions = (
        "Write a useful company analysis from this shared research. Lead with the most consequential business "
        "development or interpretation, then explain what the company sells, how its customers and economics work, "
        "what is changing or being confirmed, and the connected uncertainties that matter. Choose the structure "
        "for the actual company and evidence; do not march through the research schema or publish category status labels. "
        "The research dossier owns organized findings and initial_slot_facts owns retained facts. "
        "Use both without reconstructing the fact inventory in the response. Keep substantive indirect, peer, "
        "customer, private-company, sector and macro connections when useful, including reasonable hypotheses. "
        "Explain why a connection matters and distinguish observations, associations and proposed explanations. "
        "Treat financial narratives as hypotheses, not laws. Do not infer that a quantity cannot exist or cannot be "
        "reported merely because the business model suggests it is unusual. Missing retrieved data is an acquisition "
        "gap, not proof that the data does not exist. Unknown metrics should not become paragraphs of bookkeeping. "
        "Discuss a missing source in the public analysis only when it materially changes the interpretation; "
        "put remaining research questions in slot_gaps. Use clear analytical prose with helpful headings if needed. "
        "Preserve exact numeric metric labels, scope, observation periods and source dates. "
        "Anchor recency to the actual observations; distinguish company figures from estimates and inferred mechanisms. "
        "Use available sources and reasonable inference to recover the company's display name; a bare ticker is "
        "a valid starting point. Return resolved_company_name, business_story_markdown, confidence and slot_gaps. "
        "Also return memory_article, the complete durable company article with its existing canonical page_id (or empty for a new article), summary and full body. Reconcile current research with the complete existing article, retaining useful knowledge from unrelated sections, distinct metrics, dates, conditions, counterevidence and research leads. This is a complete publication, not a reconstruction from only changed finding groups and not the shorter business story. Preserve unresolved disagreement rather than inventing a resolution. "
        "Focus the narrative on consequential facts and connections; keep the exhaustive inventory in the dossier. Aim for a concise analysis of roughly 500 to 800 words, expanding only when useful complexity requires it. Do not add a ritual conclusion or repeat the opening. Do not create numeric forecasts from loosely related summary metrics: projections need supplied computed analysis or an explicit empirical model. Express uncertainty through precise claims and specific open questions, not blanket disclaimers. Confidence is an editorial assessment, not a calibrated probability. "
        "Do not expose internal research fields, status inventories, workflow or review commentary in the narrative."
    )
    return render_research_prompt(evidence=shared, request=request, instructions=instructions)


def _zopedia_stack_synthesis(
    *,
    zopedia_agent_runner: AqlZopediaStructuredRunner | None,
    llm_client: Any | None,
    symbol: str,
    company_name: str,
    baseline: dict[str, Any],
    fundamentals: list[dict[str, Any]],
    edgar_rows: list[dict[str, Any]],
    recent_news_rows: list[dict[str, Any]],
    zopedia_pages: list[dict[str, Any]],
    research_dossier: dict[str, Any],
    business_research_evidence: dict[str, list[dict[str, Any]]],
    initial_slot_facts: dict[str, list[dict[str, Any]]],
    slot_gaps: list[str],
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any] | None:
    runner = _agent_runner_or_none(zopedia_agent_runner=zopedia_agent_runner, llm_client=llm_client)
    if runner is None:
        return None
    result = _structured_agent_payload(
        zopedia_agent_runner=runner,
        llm_client=llm_client,
        query=_business_stack_query(
            symbol=symbol,
            company_name=company_name,
            baseline=baseline,
            fundamentals=fundamentals,
            edgar_rows=edgar_rows,
            recent_news_rows=recent_news_rows,
            zopedia_pages=zopedia_pages,
            research_dossier=research_dossier,
            business_research_evidence=business_research_evidence,
            initial_slot_facts=initial_slot_facts,
            slot_gaps=slot_gaps,
        ),
        schema_name="ticker_business_model_stack",
        schema=TICKER_BUSINESS_MODEL_STACK_SCHEMA,
        task="ticker_business_model_stack",
        surface="pipeline.ticker_business_model_stack",
        max_tool_calls=0,
        progress_callback=progress_callback,
        research_subject="company:" + symbol,
    )
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else None
    if payload is None:
        error = _coerce_text(result.get("error")) or _coerce_text(result.get("status")) or "unknown_error"
        return {"_synthesis_error": f"aql_zopedia_stack::{_safe_error_text(error, limit=220)}"}
    payload = dict(payload)
    payload["_agent_evidence_ref"] = _agent_result_evidence_ref(result)
    return payload


def _slot_gaps(slot_facts: dict[str, list[dict[str, Any]]]) -> list[str]:
    return [slot for slot in BUSINESS_MEMORY_SLOTS if not slot_facts.get(slot)]


def _business_stack_synthesis_unavailable() -> tuple[str, str]:
    return "", "low"


def _build_stack_memory_page(
    *,
    symbol: str,
    company_name: str,
    slot_facts: dict[str, list[dict[str, Any]]],
    source_urls: list[str],
    asof_time_utc: str,
    memory_article: dict[str, Any] | None = None,
    existing_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from .news_business_resolution import _build_company_memory_page

    page = _build_company_memory_page(
        symbol=symbol,
        company_name=company_name,
        slot_facts=slot_facts,
        source_event_ids=[],
        source_urls=source_urls,
        asof_time_utc=asof_time_utc,
        cold_start=True,
        memory_article=memory_article,
        existing_pages=existing_pages,
    )
    if not page:
        return {}
    metadata = _json_dict(page.get("metadata"))
    metadata.update(
        {
            "source_type": TICKER_BUSINESS_MODEL_STACK_SOURCE_TYPE,
            "stack_source": "aql_zopedia_ticker_business_model_stack",
            "last_stack_refresh_at_utc": asof_time_utc,
        }
    )
    page["metadata"] = metadata
    return page


def _proposal_rows(
    *,
    symbol: str,
    company_name: str,
    company_page: dict[str, Any],
    slot_facts: dict[str, list[dict[str, Any]]],
    slot_gaps: list[str],
    write_policy: str,
    asof_time_utc: str,
) -> list[dict[str, Any]]:
    normalized_policy = _coerce_text(write_policy).lower() or "propose"
    if normalized_policy not in {"propose", "safe_auto"}:
        return []
    proposal = build_zopedia_change_proposal(
        proposal_type="ticker_business_model_stack",
        page_id=_coerce_text(company_page.get("page_id")),
        title=f"{company_name or symbol} business model stack",
        rationale=f"Create or refresh the ticker-specific business model stack for {company_name or symbol}.",
        payload={
            "symbol": symbol,
            "company_name": company_name,
            "write_policy": normalized_policy,
            "asof_time_utc": asof_time_utc,
            "proposed_pages": [company_page],
            "slot_facts": slot_facts,
            "slot_gaps": slot_gaps,
        },
    )
    return [proposal]


def _assess_company_refresh(*, previous_inputs, current_inputs, retained_analysis, symbol, original_asof,
                            client, runner=None, progress_callback=None, current_asof=None,
                            freshness_check_due=False):
    return _structured_agent_payload(
        zopedia_agent_runner=runner, llm_client=client,
        query=render_research_prompt(evidence={
            "previous_inputs": previous_inputs, "current_inputs": current_inputs,
            "retained_analysis": retained_analysis,
        }, request={"company": symbol, "original_asof": original_asof,
                    "current_asof": current_asof, "freshness_check_due": freshness_check_due},
        instructions=("Decide whether the changes require refreshing this company investigation. "
            "The current news interpretation runs independently. Reuse the dated company analysis only "
            "when the new inputs add no consequential fact, contradiction, meaningful confirmation, "
            "changed financial observation, source correction or changed uncertainty relevant to its claims. "
            "Duplicate reports, storage timestamps and repetition alone do not require research. "
            "New earnings, filings, guidance, material developments and corrections must be incorporated. "
            "An expired check interval calls for this assessment, not automatic repetition of the full investigation. "
            "Assess whether time-sensitive claims remain usable at the current as-of, using the actual dates and "
            "coverage in current inputs. Reuse does not re-date the original findings or assert that unseen sources "
            "contain no changes. Require refresh when aging or incomplete current coverage makes reuse uncertain. "
            "If the evidence or effect is unclear, require refresh. Never treat absence of a retrieved "
            "source as proof that nothing changed. Explain the decision and identify changed claims.")),
        schema_name="company_refresh_assessment", schema={
            "type": "object", "additionalProperties": False,
            "properties": {"refresh_required": {"type": "boolean"},
                "reason": {"type": "string"},
                "changed_claims": {"type": "array", "items": {"type": "string"}}},
            "required": ["refresh_required", "reason", "changed_claims"]},
        task="company_refresh_assessment", surface="pipeline.ticker_business_model_stack.refresh",
        max_tool_calls=0, research_subject="company:" + symbol,
        answer_review_owner="downstream", progress_callback=progress_callback)


def _checkpoint_company_research(function):
    """Refresh company knowledge on changed evidence or its freshness deadline.

    News interpretation keeps its own current cadence. This checkpoint owns a
    complete company investigation, including searches, provenance and story.
    Its original dates survive reuse and replica/platform-execution recovery.
    """
    @wraps(function)
    def run(**kwargs):
        from .. import llm, pipeline_store
        from ..inference_policy import policy
        from .. import zopedia_agent, zopedia_research  # register the complete prompt contract
        from ..json_utils import to_jsonable
        from ..llm_execution import _CALL, model_call_context, protect_core_research
        access = current_research_access_scope()
        client = kwargs.get("llm_client")
        config = getattr(client, "config", None)
        funding_symbol = _normalize_symbol(kwargs["symbol"])
        funding_evidence = {
            "asof_time_utc": kwargs.get("asof_time_utc"), "symbol": funding_symbol,
            "baseline": _company_baseline_row(kwargs.get("company_baselines_frame"), funding_symbol),
            "fundamentals": _fundamental_rows(kwargs.get("fundamentals_frame"), funding_symbol, limit=10),
            "filings": _edgar_rows(kwargs.get("edgar_evidence_frame"), funding_symbol, limit=4),
            "news": _news_rows_for_symbol(kwargs.get("news_frame"), funding_symbol, limit=4),
        }
        refresh_policy = policy()
        refresh_seconds = int(os.getenv("ZOPEDIA_TICKER_BUSINESS_REFRESH_SECONDS") or
                              refresh_policy["company_refresh_seconds"])
        if not access or client is None or refresh_seconds <= 0:
            deadline = kwargs.get("research_deadline_monotonic")
            # A short window buys a current economical synthesis, rather than
            # postponing this company to a different day's news.
            short = deadline is not None and deadline - time.monotonic() < _business_research_budget_seconds()
            from ..work_scope import WorkScope
            funded = config is None or protect_core_research(config=config,
                work={"company:" + funding_symbol: funding_evidence},
                stages=3, required=not _CALL.get().get("optional_company"))
            with WorkScope(deadline or float("inf")).activate(), model_call_context(completion_only=short or not funded,
                    optional_core_unfunded=not funded, core_work_id="company:" + kwargs["symbol"]):
                result = function(**kwargs)
            if result.business_story_markdown:
                from ..llm_execution import complete_core_research
                complete_core_research("company:" + kwargs["symbol"])
            return result
        symbol = _normalize_symbol(kwargs["symbol"])
        baseline = _company_baseline_row(kwargs.get("company_baselines_frame"), symbol)
        name = _company_name(symbol, baseline, pd.Series(dtype=object))
        pages = _matching_zopedia_pages(kwargs.get("zopedia_pages_frame"), symbol=symbol, company_name=name,
            query=f"{symbol} {name} business model products customers demand fundamentals workforce policy risks", limit=8)
        def page_version(page):
            # Storage timestamps are outside the prompt. Metadata, including
            # actual observation dates and edits, remains part of identity.
            return hashlib.sha256(serialize_llm_context({
                **{key: page.get(key) for key in ("page_id", "page_type", "title", "summary", "body_markdown")},
                "metadata": _json_dict(page.get("metadata") or page.get("metadata_json")),
            }).encode()).hexdigest()
        memory = {str(page["page_id"]): page_version(page) for page in pages}
        llm.load_prompt_overrides()
        inputs = {
            "baseline": baseline, "fundamentals": _fundamental_rows(kwargs.get("fundamentals_frame"), symbol, limit=10),
            "filings": _edgar_rows(kwargs.get("edgar_evidence_frame"), symbol, limit=4),
            "news": _news_rows_for_symbol(kwargs.get("news_frame"), symbol, limit=4),
            "research": _research_rows_for_symbol(kwargs.get("research_results_frame"), symbol),
            "research_options": {key: kwargs.get(key, default) for key, default in (
                ("execute_research", False), ("max_research_queries", 24),
                ("max_search_results_per_query", 4), ("write_policy", "propose"))},
            "prompts": llm.list_narrative_prompts(), "parameters": llm.list_config_params(),
            "environment": {key: value for key, value in os.environ.items() if key.startswith("ZOPEDIA_TICKER_BUSINESS_")
                            and key not in {"ZOPEDIA_TICKER_BUSINESS_MAX_WORKERS", "ZOPEDIA_TICKER_BUSINESS_BATCH_BUDGET_SECONDS"}},
        }
        namespace, contract = research_artifact_key(access_scope=access, subject="company:" + symbol,
            query=serialize_llm_context(inputs), schema={}, config=client.config)
        evidence_fields = {"baseline", "fundamentals", "filings", "news", "research"}
        _, policy_contract = research_artifact_key(access_scope=access, subject="company:" + symbol,
            query=serialize_llm_context({k: v for k, v in inputs.items() if k not in evidence_fields}),
            schema={}, config=client.config)
        evidence = {**{k: v for k, v in inputs.items() if k in evidence_fields}, "memory": pages}
        namespace += ":company_workflow"
        with execution_ownership(namespace + ":leases", symbol,
                                 wait_seconds=_business_research_budget_seconds()) as owned:
            if not owned:
                raise TimeoutError("Company research is still owned by another worker.")
            state = pipeline_store.read_execution_state(namespace, symbol, strict=True) or {}
            now = time.time()
            compatible = (state.get("contract") == contract
                and 0 <= now - state.get("checked_at", state.get("started_at", 0)) < refresh_seconds
                and set(state.get("memory_input_ids", [])) <= memory.keys()
                and all(version in state.get("memory_versions", {}).get(identity, [])
                        for identity, version in memory.items()))
            # An unfinished investigation belongs to its original logical
            # run. A later briefing starts from current inputs and may use
            # dated prior findings, but never inherits its old as-of or clock.
            if not state.get("result") and state.get("run_id") != kwargs.get("run_id", ""):
                compatible = False
            if (not compatible and refresh_policy.get("semantic_reuse") and state.get("result")
                    and state.get("policy_contract") == policy_contract and state.get("evidence")
                    and 0 <= now - state.get("started_at", 0)):
                assessment = _assess_company_refresh(
                    previous_inputs=state["evidence"], current_inputs=evidence, retained_analysis=state["result"],
                    symbol=symbol, original_asof=state["asof"], client=client,
                    current_asof=kwargs.get("asof_time_utc"),
                    freshness_check_due=now - state.get("checked_at", state.get("started_at", 0)) >= refresh_seconds,
                    runner=kwargs.get("zopedia_agent_runner"), progress_callback=kwargs.get("progress_callback"))
                verdict = assessment.get("payload") or {}
                if assessment.get("status") == "completed" and verdict.get("refresh_required") is False:
                    state.update(contract=contract, evidence=evidence, refresh_assessment=verdict, checked_at=now)
                    for identity, version in memory.items():
                        state["memory_versions"].setdefault(identity, []).append(version)
                    if not pipeline_store.write_execution_state(namespace, symbol, state):
                        raise RuntimeError("Company reuse assessment could not be retained.")
                    compatible = True
            if compatible and state.get("result"):
                from ..llm_execution import complete_core_research
                complete_core_research("company:" + symbol)
                _emit_business_progress(kwargs.get("progress_callback"), stage="company_research_reused",
                    symbol=symbol, progress=1.0, message="Reusing the completed company investigation for unchanged evidence.",
                    original_asof_time_utc=state["result"]["asof_time_utc"], work_id=state["work_id"])
                return replace(TickerBusinessModelStackResult(**state["result"]), proposed_pages=[], proposal_rows=[])
            deadline = kwargs.get("research_deadline_monotonic")
            short = deadline is not None and deadline-time.monotonic() < _business_research_budget_seconds()
            if not compatible:
                if state:
                    retained_key = hashlib.sha256(serialize_llm_context(state).encode()).hexdigest()
                    if not pipeline_store.write_execution_state(namespace + ":history", retained_key, state, immutable=True):
                        raise RuntimeError("Previous company investigation could not be retained.")
                subject_task = "company:" + symbol + ":ticker_business_model_research_dossier"
                state = {"contract": contract, "work_id": uuid.uuid4().hex, "started_at": now,
                    "policy_contract": policy_contract, "evidence": evidence,
                    "asof": _asof_iso(kwargs.get("asof_time_utc") or datetime.now(timezone.utc)),
                    "run_id": kwargs.get("run_id", ""), "memory_input_ids": list(memory),
                    "memory_versions": {key: [value] for key, value in memory.items()},
                    "prior": {subject_task: latest_research_artifact(access_scope=access, subject="company:" + symbol,
                                                                  task="ticker_business_model_research_dossier")}}
                if not pipeline_store.write_execution_state(namespace, symbol, state):
                    raise RuntimeError("Company investigation identity could not be retained.")
            from ..work_scope import WorkScope
            work_deadline = min(time.monotonic() + _business_research_budget_seconds(),
                                deadline if deadline is not None else float("inf"))
            # A reusable completed profile has already returned above. Reserve
            # expensive completion only for a real current investigation.
            funded = protect_core_research(config=client.config, work={"company:" + symbol: evidence},
                stages=3, required=not _CALL.get().get("optional_company"))
            with research_work_context(state["work_id"], prior=state["prior"]) as status, \
                    WorkScope(work_deadline).activate(), llm.llm_request_deadline(work_deadline), \
                    model_call_context(completion_only=short or not funded, optional_core_unfunded=not funded,
                                       core_work_id="company:" + symbol):
                result = function(**{**kwargs, "run_id": state["run_id"], "asof_time_utc": state["asof"]})
            check_execution_ownership()
            if result.business_story_markdown and result.status in {"ready", "partial"} and not status["failed"]:
                state["result"] = to_jsonable(asdict(result))
                for page in result.proposed_pages:
                    state["memory_versions"].setdefault(str(page["page_id"]), []).append(page_version(page))
                if not pipeline_store.write_execution_state(namespace, symbol, state):
                    raise RuntimeError("Completed company investigation could not be retained.")
                from ..llm_execution import complete_core_research
                complete_core_research("company:" + symbol)
            return result
    return run


@_checkpoint_company_research
def build_ticker_business_model_stack(
    *,
    symbol: str,
    company_baselines_frame: pd.DataFrame | None = None,
    fundamentals_frame: pd.DataFrame | None = None,
    zopedia_pages_frame: pd.DataFrame | None = None,
    edgar_evidence_frame: pd.DataFrame | None = None,
    news_frame: pd.DataFrame | None = None,
    research_results_frame: pd.DataFrame | None = None,
    serp_client: Any | None = None,
    tavily_client: Any | None = None,
    execute_research: bool = False,
    max_research_queries: int = 24,
    max_search_results_per_query: int = 4,
    llm_client: Any | None = None,
    zopedia_agent_runner: AqlZopediaStructuredRunner | None = None,
    run_id: str = "",
    asof_time_utc: object = "",
    write_policy: str = "propose",
    progress_callback: ProgressCallback | None = None,
    research_deadline_monotonic: float | None = None,
) -> TickerBusinessModelStackResult:
    normalized_symbol = _normalize_symbol(symbol)
    asof_iso = _asof_iso(asof_time_utc)
    created_at = _asof_iso(datetime.now(timezone.utc))
    baseline = _company_baseline_row(company_baselines_frame, normalized_symbol)
    empty_news_row = pd.Series({"company_name": baseline.get("company_name") or baseline.get("name")}, dtype=object)
    company_name = _company_name(normalized_symbol, baseline, empty_news_row)
    _emit_business_progress(
        progress_callback,
        stage="business_profile_start",
        symbol=normalized_symbol,
        company_name=company_name,
        message=f"Starting company business research for {normalized_symbol} / {company_name}.",
        progress=0.02,
    )
    zopedia_pages = _matching_zopedia_pages(
        zopedia_pages_frame,
        symbol=normalized_symbol,
        company_name=company_name,
        query=f"{normalized_symbol} {company_name} business model products customers demand fundamentals workforce policy risks",
        limit=8,
    )
    business_memory_pages = _company_business_memory_pages(zopedia_pages, symbol=normalized_symbol, company_name=company_name, limit=4)
    fundamentals = _fundamental_rows(fundamentals_frame, normalized_symbol, limit=10)
    edgar_rows = _edgar_rows(edgar_evidence_frame, normalized_symbol, limit=4)
    recent_news_rows = _news_rows_for_symbol(news_frame, normalized_symbol, limit=4)
    initial_slot_facts = _base_slot_facts(
        symbol=normalized_symbol,
        company_name=company_name,
        baseline=baseline,
        fundamentals=fundamentals,
        edgar_rows=edgar_rows,
        business_memory_pages=business_memory_pages,
        recent_news_rows=recent_news_rows,
    )
    gaps = _slot_gaps(initial_slot_facts)
    synthesis_warnings: list[str] = []
    runner_configured = _agent_runner_or_none(zopedia_agent_runner=zopedia_agent_runner, llm_client=llm_client) is not None
    from ..llm_execution import best_effort_required
    if runner_configured and not best_effort_required():
        query_plan, plan_warnings = _zopedia_research_query_plan(
            zopedia_agent_runner=zopedia_agent_runner,
            llm_client=llm_client,
            symbol=normalized_symbol,
            company_name=company_name,
            baseline=baseline,
            fundamentals=fundamentals,
            edgar_rows=edgar_rows,
            recent_news_rows=recent_news_rows,
            zopedia_pages=zopedia_pages,
            initial_slot_facts=initial_slot_facts,
            max_queries=max_research_queries,
            progress_callback=progress_callback,
        )
        synthesis_warnings.extend(plan_warnings)
    elif not runner_configured:
        query_plan = []
        synthesis_warnings.append("aql_zopedia_research_plan_unavailable::agent_not_configured")
    else:
        query_plan = []
    _emit_business_progress(
        progress_callback,
        stage="business_research_plan_ready",
        symbol=normalized_symbol,
        company_name=company_name,
        message=(
            f"Research plan ready with {len(query_plan)} question(s); "
            f"{len(gaps)} business question(s) still need evidence."
        ),
        progress=0.18,
        query_count=len(query_plan),
        open_gap_count=len(gaps),
        open_gaps=list(gaps),
    )
    resolved_company_name = _resolved_company_name_from_query_plan(
        query_plan,
        symbol=normalized_symbol,
        current_name=company_name,
    )
    if resolved_company_name != company_name:
        company_name = resolved_company_name
        zopedia_pages = _matching_zopedia_pages(
            zopedia_pages_frame,
            symbol=normalized_symbol,
            company_name=company_name,
            query=f"{normalized_symbol} {company_name} business model products customers demand fundamentals workforce policy risks",
            limit=8,
        )
        business_memory_pages = _company_business_memory_pages(
            zopedia_pages,
            symbol=normalized_symbol,
            company_name=company_name,
            limit=4,
        )
        initial_slot_facts = _base_slot_facts(
            symbol=normalized_symbol,
            company_name=company_name,
            baseline=baseline,
            fundamentals=fundamentals,
            edgar_rows=edgar_rows,
            business_memory_pages=business_memory_pages,
            recent_news_rows=recent_news_rows,
        )
        gaps = _slot_gaps(initial_slot_facts)
    planned_slots = [_coerce_text(item.get("slot")) for item in query_plan if _coerce_text(item.get("slot"))]
    plan_id = _research_plan_id(symbol=normalized_symbol, run_id=_coerce_text(run_id), query_plan=query_plan)
    plan_row = {
        "plan_id": plan_id,
        "run_id": _coerce_text(run_id),
        "asof_time_utc": asof_iso,
        "symbol": normalized_symbol,
        "company_name": company_name,
        "required_slots_json": _json_dumps(planned_slots),
        "query_plan_json": _json_dumps(query_plan),
        "created_at_utc": created_at,
    }
    search_request_rows: list[dict[str, Any]] = []
    search_result_rows: list[dict[str, Any]] = []
    if execute_research and query_plan and not best_effort_required():
        _emit_business_progress(
            progress_callback,
            stage="business_research_search_start",
            symbol=normalized_symbol,
            company_name=company_name,
            message=f"Running {len(query_plan)} connector research question(s).",
            progress=0.24,
            query_count=len(query_plan),
        )
        def _run_search_query(query_spec: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            local_serp_client, local_tavily_client = _fresh_search_clients_like(serp_client, tavily_client)
            return _search_results_for_query(
                plan_id=plan_id,
                run_id=_coerce_text(run_id),
                asof_time_utc=asof_iso,
                symbol=normalized_symbol,
                company_name=company_name,
                query_spec=query_spec,
                serp_client=local_serp_client,
                tavily_client=local_tavily_client,
                max_results_per_query=max_search_results_per_query,
                created_at_utc=created_at,
            )

        search_jobs = list(query_plan)
        search_max_workers = min(_business_search_max_workers(), len(search_jobs))
        if search_max_workers <= 1:
            ordered_search_results = [_run_search_query(query_spec) for query_spec in search_jobs]
        else:
            ordered_search_results: list[tuple[list[dict[str, Any]], list[dict[str, Any]]] | None] = [None] * len(search_jobs)
            with ThreadPoolExecutor(max_workers=search_max_workers) as pool:
                future_map = {
                    pool.submit(copy_context().run, _run_search_query, query_spec): index
                    for index, query_spec in enumerate(search_jobs)
                }
                for future in as_completed(future_map):
                    index = future_map[future]
                    try:
                        ordered_search_results[index] = future.result()
                    except Exception as exc:
                        query_spec = search_jobs[index]
                        request_id = _research_request_id(
                            plan_id=plan_id,
                            provider="parallel_search",
                            slot=_coerce_text(query_spec.get("slot")),
                            query=_coerce_text(query_spec.get("query")),
                        )
                        ordered_search_results[index] = (
                            [
                                {
                                    "request_id": request_id,
                                    "plan_id": plan_id,
                                    "run_id": _coerce_text(run_id),
                                    "asof_time_utc": asof_iso,
                                    "symbol": normalized_symbol,
                                    "company_name": company_name,
                                    "slot": _coerce_text(query_spec.get("slot")),
                                    "source_intent": _coerce_text(query_spec.get("source_intent")),
                                    "requires_page_open": bool(query_spec.get("requires_page_open")),
                                    "provider": "parallel_search",
                                    "topic": _coerce_text(query_spec.get("topic")) or "general",
                                    "query": _coerce_text(query_spec.get("query")),
                                    "priority": int(query_spec.get("priority") or 0),
                                    "created_at_utc": created_at,
                                }
                            ],
                            [
                                {
                                    "result_id": _research_result_id(
                                        request_id=request_id,
                                        provider="parallel_search",
                                        title="error",
                                        url="",
                                    ),
                                    "request_id": request_id,
                                    "plan_id": plan_id,
                                    "run_id": _coerce_text(run_id),
                                    "asof_time_utc": asof_iso,
                                    "symbol": normalized_symbol,
                                    "company_name": company_name,
                                    "slot": _coerce_text(query_spec.get("slot")),
                                    "provider": "parallel_search",
                                    "source_intent": _coerce_text(query_spec.get("source_intent")),
                                    "title": "",
                                    "url": "",
                                    "snippet": "",
                                    "raw_text": "",
                                    "source": "parallel_search",
                                    "published_at": "",
                                    "authority_bucket": "web",
                                    "authority_rank": 3,
                                    "opened_page": False,
                                    "browse_mode": "",
                                    "browse_warning": "",
                                    "page_quality_issue": "",
                                    "error_text": _safe_error_text(exc, limit=220),
                                    "created_at_utc": created_at,
                                }
                            ],
                        )
        for item in ordered_search_results:
            if item is None:
                continue
            requests, results = item
            search_request_rows.extend(requests)
            search_result_rows.extend(results)
        opened_pages = sum(1 for row in search_result_rows if bool(row.get("opened_page")))
        failed_results = sum(1 for row in search_result_rows if _coerce_text(row.get("error_text")))
        _emit_business_progress(
            progress_callback,
            stage="business_research_search_complete",
            symbol=normalized_symbol,
            company_name=company_name,
            message=(
                f"Connector research returned {len(search_result_rows)} result(s), "
                f"opened {opened_pages} page(s), and recorded {failed_results} error result(s)."
            ),
            progress=0.42,
            search_request_count=len(search_request_rows),
            search_result_count=len(search_result_rows),
            opened_page_count=opened_pages,
            failed_result_count=failed_results,
        )
    existing_research_rows = _research_rows_for_symbol(research_results_frame, normalized_symbol)
    successful_search_rows = [row for row in search_result_rows if not _coerce_text(row.get("error_text"))]
    combined_research_rows = successful_search_rows + existing_research_rows
    business_research_evidence = _research_evidence_by_slot(
        combined_research_rows,
        symbol=normalized_symbol,
        company_name=company_name,
    )
    gaps = _slot_gaps(initial_slot_facts)
    _emit_business_progress(
        progress_callback,
        stage="business_research_dossier_start",
        symbol=normalized_symbol,
        company_name=company_name,
        message=f"Asking Zopedia to organize {len(combined_research_rows)} evidence row(s) into a research dossier.",
        progress=0.46,
        evidence_row_count=len(combined_research_rows),
    )
    seed_slot_facts = {group: [dict(fact) for fact in facts] for group, facts in initial_slot_facts.items()}
    research_dossier, dossier_warnings = _zopedia_research_dossier(
        zopedia_agent_runner=zopedia_agent_runner,
        llm_client=llm_client,
        symbol=normalized_symbol,
        company_name=company_name,
        query_plan=query_plan,
        baseline=baseline,
        fundamentals=fundamentals,
        edgar_rows=edgar_rows,
        recent_news_rows=recent_news_rows,
        zopedia_pages=zopedia_pages,
        initial_slot_facts=initial_slot_facts,
        business_research_evidence=business_research_evidence,
        combined_research_rows=combined_research_rows,
        progress_callback=progress_callback,
    )
    synthesis_warnings.extend(dossier_warnings)
    dossier_slot_facts = _slot_facts_from_research_dossier(
        research_dossier,
        default_source=_agent_result_evidence_ref({"agent_result": {"run_id": _coerce_text(run_id)}}, slot="research_dossier"),
    )
    if dossier_slot_facts:
        initial_slot_facts = _merge_slot_facts(initial_slot_facts, dossier_slot_facts)
        initial_slot_facts = _prioritize_zopedia_verdict_facts(initial_slot_facts)
        gaps = _slot_gaps(initial_slot_facts)
    if combined_research_rows and not research_dossier and "research_dossier_unavailable_for_retrieved_evidence" not in synthesis_warnings:
        synthesis_warnings.append("research_dossier_unavailable_for_retrieved_evidence")
    _emit_business_progress(
        progress_callback,
        stage="business_research_dossier_complete",
        symbol=normalized_symbol,
        company_name=company_name,
        message=(
            f"Research dossier organized {len(research_dossier.get('source_inventory') or [])} source lead(s) "
            f"and {sum(len(items) for items in (research_dossier.get('slot_findings') or {}).values()) if isinstance(research_dossier.get('slot_findings'), dict) else 0} finding(s)."
        ),
        progress=0.49,
        source_inventory_count=len(research_dossier.get("source_inventory") or []),
        dossier_finding_count=sum(len(items) for items in (research_dossier.get("slot_findings") or {}).values()) if isinstance(research_dossier.get("slot_findings"), dict) else 0,
        relevant_source_leads=_research_dossier_has_relevant_sources(research_dossier),
    )
    _emit_business_progress(
        progress_callback,
        stage="business_fact_resolution_start",
        symbol=normalized_symbol,
        company_name=company_name,
        message=(
            f"Asking Zopedia to resolve {len(combined_research_rows)} evidence row(s) "
            f"against {len(gaps)} open business question(s)."
        ),
        progress=0.5,
        evidence_row_count=len(combined_research_rows),
        open_gap_count=len(gaps),
    )
    research_dossier, slot_warnings = _zopedia_dossier_followup(
        zopedia_agent_runner=zopedia_agent_runner, llm_client=llm_client,
        symbol=normalized_symbol, company_name=company_name, research_dossier=research_dossier,
        progress_callback=progress_callback,
    )
    slot_overlay = _slot_facts_from_research_dossier(
        research_dossier, default_source="aql_zopedia::company_research::" + normalized_symbol,
    )
    synthesis_warnings.extend(slot_warnings)
    slot_overlay_available = bool(slot_overlay)
    initial_slot_facts = _merge_slot_facts(seed_slot_facts, slot_overlay, replace_resolved=True)
    initial_slot_facts = _prioritize_zopedia_verdict_facts(initial_slot_facts)
    gaps = _slot_gaps(initial_slot_facts)
    _emit_business_progress(
        progress_callback,
        stage="business_fact_resolution_complete",
        symbol=normalized_symbol,
        company_name=company_name,
        message=f"Zopedia resolved {len(slot_overlay)} business question group(s); {len(gaps)} remain open.",
        progress=0.66,
        resolved_group_count=len(slot_overlay),
        open_gap_count=len(gaps),
        open_gaps=list(gaps),
    )
    _emit_business_progress(
        progress_callback,
        stage="business_story_synthesis_start",
        symbol=normalized_symbol,
        company_name=company_name,
        message="Asking Zopedia to write the company business story from resolved evidence.",
        progress=0.72,
    )
    synthesis_payload = _zopedia_stack_synthesis(
        zopedia_agent_runner=zopedia_agent_runner,
        llm_client=llm_client,
        symbol=normalized_symbol,
        company_name=company_name,
        baseline=baseline,
        fundamentals=fundamentals,
        edgar_rows=edgar_rows,
        recent_news_rows=recent_news_rows,
        zopedia_pages=zopedia_pages,
        research_dossier=research_dossier,
        business_research_evidence=business_research_evidence,
        initial_slot_facts=initial_slot_facts,
        slot_gaps=gaps,
        progress_callback=progress_callback,
    )
    if isinstance(synthesis_payload, dict) and _coerce_text(synthesis_payload.get("_synthesis_error")):
        synthesis_warnings.append(_coerce_text(synthesis_payload.get("_synthesis_error")))
        synthesis_payload = None
    slot_facts = initial_slot_facts
    gaps = _slot_gaps(slot_facts)
    # A useful research narrative does not require a new durable-memory fact.
    # Memory eligibility is decided independently below from the retained facts.
    stack_synthesis_available = bool(
        isinstance(synthesis_payload, dict) and _coerce_text(synthesis_payload.get("business_story_markdown"))
    )
    zopedia_verdict_available = bool(slot_overlay_available or stack_synthesis_available)
    if stack_synthesis_available and isinstance(synthesis_payload, dict):
        resolved_from_stack = _coerce_text(synthesis_payload.get("resolved_company_name"))
        if _company_name_is_weak(normalized_symbol, company_name) and not _company_name_is_weak(normalized_symbol, resolved_from_stack):
            company_name = resolved_from_stack
        story = _coerce_text(synthesis_payload.get("business_story_markdown"))
        confidence = _normalized_business_confidence(synthesis_payload.get("confidence"), default="medium")
        for gap in list(synthesis_payload.get("slot_gaps") or []):
            clean_gap = _coerce_text(gap)
            if clean_gap in BUSINESS_MEMORY_SLOTS and clean_gap not in gaps and not slot_facts.get(clean_gap):
                gaps.append(clean_gap)
    else:
        story, confidence = _business_stack_synthesis_unavailable()
        if zopedia_verdict_available:
            confidence = "medium"
        if (slot_facts or business_research_evidence) and not zopedia_verdict_available and "zopedia_verdict_unavailable" not in synthesis_warnings:
            synthesis_warnings.append("zopedia_verdict_unavailable")
        elif zopedia_verdict_available and "business_stack_story_unavailable" not in synthesis_warnings:
            synthesis_warnings.append("business_stack_story_unavailable")
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    if not zopedia_verdict_available:
        confidence = "low"
    if gaps and confidence == "high":
        confidence = "medium"
    synthesis_warnings = _filter_resolved_slot_warnings(synthesis_warnings, slot_facts)
    coverage = _slot_coverage(slot_facts=slot_facts, query_plan=query_plan, research_rows=combined_research_rows)
    ready_gaps = _business_stack_ready_gaps(slot_facts)
    if ready_gaps and any(_slot_has_zopedia_verdict(facts) for facts in slot_facts.values()):
        synthesis_warnings.append(f"business_stack_not_ready_missing_core_slots::{','.join(ready_gaps)}")
    durable_memory_ready = bool(confidence in {"high", "medium"} and not ready_gaps)
    if durable_memory_ready and story:
        status = "ready"
    elif story or any(_slot_has_zopedia_verdict(facts) for facts in slot_facts.values()):
        status = "partial"
    elif slot_facts or business_research_evidence:
        status = "needs_zopedia_verdict"
    else:
        status = "insufficient_evidence"
    if status == "insufficient_evidence":
        confidence = "low"
    _emit_business_progress(
        progress_callback,
        stage="business_profile_complete",
        symbol=normalized_symbol,
        company_name=company_name,
        message=(
            f"Company business profile finished with status={status}, "
            f"confidence={confidence}, open_questions={len(gaps)}."
        ),
        progress=0.98,
        status=status,
        confidence=confidence,
        open_gap_count=len(gaps),
        open_gaps=list(gaps),
    )
    source_urls = list(
        dict.fromkeys(
            _coerce_text(row.get("url"))
            for row in [*recent_news_rows, *combined_research_rows]
            if _coerce_text(row.get("url")) and not _coerce_text(row.get("error_text"))
        )
    )
    memory_article = (synthesis_payload or {}).get("memory_article")
    complete_memory_article = not business_memory_pages or (
        isinstance(memory_article, dict) and bool(_coerce_text(memory_article.get("body_markdown"))) and
        memory_article.get("page_id") in {page.get("page_id") for page in business_memory_pages}
    )
    if slot_facts and durable_memory_ready and complete_memory_article:
        company_page = _build_stack_memory_page(
            symbol=normalized_symbol,
            company_name=company_name,
            slot_facts=slot_facts,
            source_urls=source_urls,
            asof_time_utc=asof_iso,
            memory_article=memory_article,
            existing_pages=business_memory_pages,
        )
        prepared_frame, _ = prepare_zopedia_pages([company_page], now=pd.to_datetime(asof_iso, utc=True, errors="coerce").to_pydatetime())
        proposed_pages = prepared_frame.to_dict("records") if not prepared_frame.empty else []
        business_memory_page_id = _coerce_text(company_page.get("page_id"))
        business_memory_body = _coerce_text(company_page.get("body_markdown"))
        proposal_rows = _proposal_rows(
            symbol=normalized_symbol,
            company_name=company_name,
            company_page=company_page,
            slot_facts=slot_facts,
            slot_gaps=gaps,
            write_policy=write_policy,
            asof_time_utc=asof_iso,
        )
    else:
        proposed_pages = []
        business_memory_page_id = ""
        business_memory_body = ""
        proposal_rows = []
        if zopedia_verdict_available and not durable_memory_ready and "durable_memory_not_written_low_confidence_or_missing_business_model" not in synthesis_warnings:
            synthesis_warnings.append("durable_memory_not_written_low_confidence_or_missing_business_model")
        if durable_memory_ready and not complete_memory_article:
            synthesis_warnings.append("complete_memory_article_unavailable")
    zopedia_page_ids = [_coerce_text(page.get("page_id")) for page in zopedia_pages if _coerce_text(page.get("page_id"))]
    source_page_ids = [_coerce_text(page.get("page_id")) for page in business_memory_pages if _coerce_text(page.get("page_id"))]
    if business_memory_page_id:
        source_page_ids.append(business_memory_page_id)
    return TickerBusinessModelStackResult(
        symbol=normalized_symbol,
        company_name=company_name,
        status=status,
        confidence=confidence,
        source_page_ids=list(dict.fromkeys(source_page_ids)),
        zopedia_page_ids_read=list(dict.fromkeys(zopedia_page_ids)),
        fundamental_datasets_used=["quarterly_fundamentals"] if fundamentals else [],
        research_plan_id=plan_id,
        research_query_plan=query_plan,
        search_request_ids=[
            _coerce_text(row.get("request_id"))
            for row in search_request_rows
            if _coerce_text(row.get("request_id"))
        ],
        search_result_ids=[
            _coerce_text(row.get("result_id"))
            for row in combined_research_rows
            if _coerce_text(row.get("result_id"))
        ],
        research_dossier=research_dossier,
        slot_coverage=coverage,
        synthesis_warnings=synthesis_warnings,
        slot_facts=slot_facts,
        slot_gaps=gaps,
        business_story_markdown=story,
        business_memory_page_id=business_memory_page_id,
        business_memory_body_markdown=business_memory_body,
        proposal_rows=proposal_rows,
        proposed_pages=proposed_pages,
        research_plan_rows=[plan_row],
        search_request_rows=search_request_rows,
        search_result_rows=search_result_rows,
        evidence_pack_id=_stable_id("ticker_business_model_stack_evidence_pack", normalized_symbol, run_id),
        run_id=_coerce_text(run_id),
        asof_time_utc=asof_iso,
        created_at_utc=created_at,
    )


def build_ticker_business_model_stack_results(
    *,
    symbols: list[str],
    company_baselines_frame: pd.DataFrame | None = None,
    fundamentals_frame: pd.DataFrame | None = None,
    zopedia_pages_frame: pd.DataFrame | None = None,
    edgar_evidence_frame: pd.DataFrame | None = None,
    news_frame: pd.DataFrame | None = None,
    research_results_frame: pd.DataFrame | None = None,
    serp_client: Any | None = None,
    tavily_client: Any | None = None,
    execute_research: bool = False,
    max_research_queries: int = 24,
    max_search_results_per_query: int = 4,
    llm_client: Any | None = None,
    zopedia_agent_runner: AqlZopediaStructuredRunner | None = None,
    run_id: str = "",
    asof_time_utc: object = "",
    write_policy: str = "propose",
    limit: int = 25,
    progress_callback: ProgressCallback | None = None,
    research_deadline_monotonic: float | None = None,
    event_work_by_symbol: dict[str, dict[str, Any]] | None = None,
) -> list[TickerBusinessModelStackResult]:
    normalized_symbols = list(dict.fromkeys(_normalize_symbol(symbol) for symbol in list(symbols or []) if _normalize_symbol(symbol)))
    selected = normalized_symbols[: max(int(limit), 1)]
    max_workers = min(_env_positive_int("ZOPEDIA_TICKER_BUSINESS_MAX_WORKERS", 4, minimum=1), len(selected))
    if not selected:
        return []
    kwargs = dict(
        company_baselines_frame=company_baselines_frame,
        fundamentals_frame=fundamentals_frame,
        zopedia_pages_frame=zopedia_pages_frame,
        edgar_evidence_frame=edgar_evidence_frame,
        news_frame=news_frame,
        research_results_frame=research_results_frame,
        serp_client=serp_client,
        tavily_client=tavily_client,
        execute_research=execute_research,
        max_research_queries=max_research_queries,
        max_search_results_per_query=max_search_results_per_query,
        llm_client=llm_client,
        zopedia_agent_runner=zopedia_agent_runner,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
        write_policy=write_policy,
        progress_callback=progress_callback,
        research_deadline_monotonic=research_deadline_monotonic,
    )
    def research(symbol):
        from ..work_scope import WorkCancelled
        from ..llm import LLMDeadlineExceeded
        from ..llm_execution import model_call_context, complete_core_research, retain_paid_result, event_research_context
        core_id = "company:" + symbol
        config = None
        try:
            config = getattr(llm_client, "config", None)
            with event_research_context((event_work_by_symbol or {}).get(symbol)), \
                    model_call_context(core_work_id=core_id, optional_company=True):
                return build_ticker_business_model_stack(symbol=symbol, **kwargs)
        except (ResearchWorkDeferred, WorkCancelled, LLMDeadlineExceeded):
            _emit_business_progress(progress_callback, stage="business_research_incomplete", symbol=symbol,
                message="The current company investigation did not finish within this run.", progress=1.0)
            return None
        except Exception as exc:
            # A failed company remains pending; the independent completed
            # companies must still reach the aggregate publisher. The producer
            # receives a failure event and cannot report a clean run.
            log.exception("Company investigation failed: %s", symbol)
            _emit_business_progress(progress_callback, stage="business_research_failed", symbol=symbol,
                error_type=type(exc).__name__, message="Company investigation failed; completed research is retained.",
                progress=1.0)
            return None
        finally:
            # Returned, failed and cached work all release unused completion
            # funds. In-flight/unknown transport reservations remain held.
            if config is not None:
                try:
                    retain_paid_result(lambda: complete_core_research(core_id))
                except Exception:
                    log.warning("Company completion funding awaits reconciliation: %s", symbol)
    from ..work_scope import WorkScope
    batch = WorkScope(research_deadline_monotonic if research_deadline_monotonic is not None else float("inf"))
    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="company-research")
    futures = []
    try:
        if time.monotonic() >= batch.deadline:
            for symbol in selected:
                _emit_business_progress(progress_callback, stage="business_research_incomplete", symbol=symbol,
                    message="This run reached its company research deadline.", progress=1.0)
            return []
        with batch.activate():
            futures = [pool.submit(copy_context().run, research, symbol) for symbol in selected]
        timeout = max(0, batch.deadline - time.monotonic()) if research_deadline_monotonic is not None else None
        done, pending = wait(futures, timeout=timeout)
        for future, symbol in zip(futures, selected):
            if future in pending:
                _emit_business_progress(progress_callback, stage="business_research_incomplete", symbol=symbol,
                    message="This run reached its company research deadline.", progress=1.0)
        # Caller ordering remains stable; a straggler cannot delay publishing
        # independently completed results beyond the producer's work window.
        return [result for future in futures if future in done and not future.cancelled()
                and (result := future.result()) is not None]
    finally:
        batch.cancel()
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)


def _result_row(result: TickerBusinessModelStackResult) -> dict[str, Any]:
    return {
        "stack_id": result.stack_id,
        "run_id": result.run_id,
        "asof_time_utc": result.asof_time_utc,
        "symbol": result.symbol,
        "company_name": result.company_name,
        "status": result.status,
        "confidence": result.confidence,
        "source_page_ids_json": _json_dumps(result.source_page_ids),
        "zopedia_page_ids_read_json": _json_dumps(result.zopedia_page_ids_read),
        "fundamental_datasets_used_json": _json_dumps(result.fundamental_datasets_used),
        "research_plan_id": result.research_plan_id,
        "research_query_plan_json": _json_dumps(result.research_query_plan),
        "search_request_ids_json": _json_dumps(result.search_request_ids),
        "search_result_ids_json": _json_dumps(result.search_result_ids),
        "research_dossier_json": _json_dumps(result.research_dossier),
        "slot_coverage_json": _json_dumps(result.slot_coverage),
        "synthesis_warnings_json": _json_dumps(result.synthesis_warnings),
        "slot_facts_json": _json_dumps(result.slot_facts),
        "slot_gaps_json": _json_dumps(result.slot_gaps),
        "business_story_markdown": result.business_story_markdown,
        "business_memory_page_id": result.business_memory_page_id,
        "business_memory_body_markdown": result.business_memory_body_markdown,
        "proposal_ids_json": _json_dumps(result.proposal_ids),
        "proposal_rows_json": _json_dumps(result.proposal_rows),
        "proposed_pages_json": _json_dumps(result.proposed_pages),
        "evidence_pack_id": result.evidence_pack_id,
        "created_at_utc": result.created_at_utc,
    }


def serialize_ticker_business_model_stack_results(results: list[TickerBusinessModelStackResult]) -> pd.DataFrame:
    rows = [_result_row(result) for result in list(results or [])]
    return pd.DataFrame(rows, columns=list(TICKER_BUSINESS_MODEL_STACK_COLUMNS))


def business_memory_pages_from_stacks(results: list[TickerBusinessModelStackResult]) -> pd.DataFrame:
    pages: list[dict[str, Any]] = []
    for result in list(results or []):
        for page in list(result.proposed_pages or []):
            metadata = _json_dict(page.get("metadata") or page.get("metadata_json"))
            if metadata.get("source_type") == TICKER_BUSINESS_MODEL_STACK_SOURCE_TYPE:
                pages.append(page)
    if not pages:
        return pd.DataFrame()
    frame = pd.DataFrame(pages)
    if "page_id" in frame.columns:
        frame = frame.drop_duplicates(subset=["page_id"], keep="last")
    return frame.reset_index(drop=True)


def serialize_business_model_research_plan_rows(results: list[TickerBusinessModelStackResult]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for result in list(results or []):
        rows.extend(list(result.research_plan_rows or []))
    if not rows:
        return pd.DataFrame(columns=list(BUSINESS_MODEL_RESEARCH_PLAN_COLUMNS))
    frame = pd.DataFrame(rows, columns=list(BUSINESS_MODEL_RESEARCH_PLAN_COLUMNS))
    if "plan_id" in frame.columns:
        frame = frame.drop_duplicates(subset=["plan_id"], keep="last")
    return frame.reset_index(drop=True)


def serialize_business_model_search_request_rows(results: list[TickerBusinessModelStackResult]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for result in list(results or []):
        rows.extend(list(result.search_request_rows or []))
    if not rows:
        return pd.DataFrame(columns=list(BUSINESS_MODEL_SEARCH_REQUEST_COLUMNS))
    frame = pd.DataFrame(rows, columns=list(BUSINESS_MODEL_SEARCH_REQUEST_COLUMNS))
    if "request_id" in frame.columns:
        frame = frame.drop_duplicates(subset=["request_id"], keep="last")
    return frame.reset_index(drop=True)


def serialize_business_model_search_result_rows(results: list[TickerBusinessModelStackResult]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for result in list(results or []):
        rows.extend(list(result.search_result_rows or []))
    if not rows:
        return pd.DataFrame(columns=list(BUSINESS_MODEL_SEARCH_RESULT_COLUMNS))
    frame = pd.DataFrame(rows, columns=list(BUSINESS_MODEL_SEARCH_RESULT_COLUMNS))
    if "result_id" in frame.columns:
        frame = frame.drop_duplicates(subset=["result_id"], keep="last")
    return frame.reset_index(drop=True)


def build_ticker_business_model_stack_frames(
    *,
    symbols: list[str],
    company_baselines_frame: pd.DataFrame | None = None,
    fundamentals_frame: pd.DataFrame | None = None,
    zopedia_pages_frame: pd.DataFrame | None = None,
    edgar_evidence_frame: pd.DataFrame | None = None,
    news_frame: pd.DataFrame | None = None,
    research_results_frame: pd.DataFrame | None = None,
    existing_stacks_frame: pd.DataFrame | None = None,
    serp_client: Any | None = None,
    tavily_client: Any | None = None,
    execute_research: bool = False,
    max_research_queries: int = 24,
    max_search_results_per_query: int = 4,
    llm_client: Any | None = None,
    zopedia_agent_runner: AqlZopediaStructuredRunner | None = None,
    run_id: str = "",
    asof_time_utc: object = "",
    write_policy: str = "propose",
    limit: int = 25,
    progress_callback: ProgressCallback | None = None,
    research_deadline_monotonic: float | None = None,
    event_work_by_symbol: dict[str, dict[str, Any]] | None = None,
) -> dict[str, pd.DataFrame]:
    eligible = list(dict.fromkeys(_normalize_symbol(s) for s in symbols if _normalize_symbol(s)))[:max(int(limit), 1)]
    if isinstance(existing_stacks_frame, pd.DataFrame) and not existing_stacks_frame.empty:
        previous = existing_stacks_frame.copy()
        previous["symbol"] = previous["symbol"].map(_normalize_symbol)
        previous["_observed"] = pd.to_datetime(previous["asof_time_utc"], utc=True, errors="coerce")
        latest = previous.groupby("symbol")["_observed"].max().to_dict()
        # Preserve the caller's eligible universe; rotate its older/missing
        # investigations ahead of recently completed profiles across batches.
        eligible.sort(key=lambda s: (0 if s in (event_work_by_symbol or {}) else 1,
            latest[s].value if s in latest and pd.notna(latest[s]) else float("-inf")))
    results = build_ticker_business_model_stack_results(
        symbols=eligible,
        company_baselines_frame=company_baselines_frame,
        fundamentals_frame=fundamentals_frame,
        zopedia_pages_frame=zopedia_pages_frame,
        edgar_evidence_frame=edgar_evidence_frame,
        news_frame=news_frame,
        research_results_frame=research_results_frame,
        serp_client=serp_client,
        tavily_client=tavily_client,
        execute_research=execute_research,
        max_research_queries=max_research_queries,
        max_search_results_per_query=max_search_results_per_query,
        llm_client=llm_client,
        zopedia_agent_runner=zopedia_agent_runner,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
        write_policy=write_policy,
        limit=limit,
        progress_callback=progress_callback,
        research_deadline_monotonic=research_deadline_monotonic,
        event_work_by_symbol=event_work_by_symbol,
    )
    stacks = serialize_ticker_business_model_stack_results(results)
    if isinstance(existing_stacks_frame, pd.DataFrame) and not existing_stacks_frame.empty:
        # A scheduled batch researches a subset of companies. Retain complete
        # prior analyses for other companies, and when an attempt has no story.
        # Keep each retained row's original dates and provenance intact.
        stacks = pd.concat([stacks, existing_stacks_frame], ignore_index=True)
        stacks["symbol"] = stacks["symbol"].astype(str).str.upper().str.strip()
        stacks = stacks[stacks["symbol"].ne("")].copy()
        stacks["_has_story"] = stacks["business_story_markdown"].fillna("").astype(str).str.strip().ne("")
        stacks["_as_of"] = pd.to_datetime(stacks["asof_time_utc"], utc=True, errors="coerce")
        stacks = stacks.sort_values(["_has_story", "_as_of"], ascending=False, kind="stable", na_position="last")
        stacks = stacks.drop_duplicates("symbol", keep="first").drop(columns=["_has_story", "_as_of"]).reset_index(drop=True)
    return {
        "zopedia_business_model_research_plans": serialize_business_model_research_plan_rows(results),
        "zopedia_business_model_search_requests": serialize_business_model_search_request_rows(results),
        "zopedia_business_model_search_results": serialize_business_model_search_result_rows(results),
        "zopedia_ticker_business_model_stacks": stacks,
        "zopedia_company_business_memory_pages": business_memory_pages_from_stacks(results),
    }


__all__ = [
    "BUSINESS_MODEL_STACK_SLOT_QUESTIONS",
    "BUSINESS_MODEL_RESEARCH_PLAN_COLUMNS",
    "BUSINESS_MODEL_SEARCH_REQUEST_COLUMNS",
    "BUSINESS_MODEL_SEARCH_RESULT_COLUMNS",
    "build_business_model_research_query_plan",
    "TICKER_BUSINESS_MODEL_STACK_COLUMNS",
    "TICKER_BUSINESS_MODEL_STACK_SCHEMA",
    "TICKER_BUSINESS_MODEL_STACK_SOURCE_TYPE",
    "BUSINESS_MODEL_RESEARCH_DOSSIER_SCHEMA",
    "BUSINESS_MODEL_RESEARCH_QUERY_PLAN_SCHEMA",
    "TickerBusinessModelStackResult",
    "build_ticker_business_model_stack",
    "build_ticker_business_model_stack_frames",
    "build_ticker_business_model_stack_results",
    "business_memory_pages_from_stacks",
    "serialize_business_model_research_plan_rows",
    "serialize_business_model_search_request_rows",
    "serialize_business_model_search_result_rows",
    "serialize_ticker_business_model_stack_results",
]
