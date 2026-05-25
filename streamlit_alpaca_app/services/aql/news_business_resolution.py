from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

import pandas as pd

from ..llm import LLMAPIError
from ..saa import (
    build_zopedia_change_proposal,
    build_zopedia_page_id,
    prepare_zopedia_pages,
    search_prepared_zopedia_pages,
)


BUSINESS_MEMORY_SLOTS: tuple[str, ...] = (
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

RESOLUTION_LABELS: tuple[str, ...] = (
    "confirms_existing_story",
    "extends_existing_story",
    "contradicts_existing_story",
    "updates_magnitude",
    "stale_memory_detected",
    "insufficient_evidence",
)

SOURCE_BACKED_MEMORY_SOURCES: tuple[str, ...] = (
    "company_baselines",
    "quarterly_fundamentals",
    "news_articles",
    "edgar_evidence",
)

NEWS_BUSINESS_RESOLUTION_COLUMNS: tuple[str, ...] = (
    "resolution_id",
    "run_id",
    "asof_time_utc",
    "symbol",
    "company_name",
    "source_event_id",
    "source_event_ids_json",
    "source_headline",
    "source_url",
    "source_published_at",
    "status",
    "confidence",
    "resolution_labels_json",
    "source_page_ids_json",
    "zopedia_page_ids_read_json",
    "fundamental_datasets_used_json",
    "slot_facts_json",
    "resolved_changes_json",
    "coherent_story_markdown",
    "memory_mutation_ids_json",
    "proposal_ids_json",
    "proposal_rows_json",
    "proposed_pages_json",
    "evidence_pack_id",
    "data_gaps_json",
    "cold_start_used",
    "created_at_utc",
)

NEWS_BUSINESS_RESOLUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "slot_facts": {
            "type": "object",
            "additionalProperties": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "text": {"type": "string"},
                        "source": {"type": "string"},
                        "confidence": {"type": "string"},
                    },
                    "required": ["text", "source", "confidence"],
                },
            },
        },
        "resolved_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "text": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["label", "text", "evidence_refs"],
            },
        },
        "coherent_story_markdown": {"type": "string"},
        "confidence": {"type": "string"},
        "data_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["slot_facts", "resolved_changes", "coherent_story_markdown", "confidence", "data_gaps"],
}


@dataclass(frozen=True)
class NewsBusinessResolutionRequest:
    source_event_ids: list[str]
    symbols: list[str]
    query: str = ""
    surface: str = "pipeline.news_business_resolution"
    write_policy: str = "propose"
    evidence_slot_policy: list[str] = field(default_factory=lambda: list(BUSINESS_MEMORY_SLOTS))


@dataclass
class NewsBusinessResolutionResult:
    request: NewsBusinessResolutionRequest
    symbol: str
    company_name: str
    source_event_id: str
    source_event_ids: list[str]
    source_headline: str
    source_url: str
    source_published_at: str
    status: str
    confidence: str
    resolution_labels: list[str]
    source_page_ids: list[str]
    zopedia_page_ids_read: list[str]
    fundamental_datasets_used: list[str]
    slot_facts: dict[str, list[dict[str, Any]]]
    resolved_changes: list[dict[str, Any]]
    coherent_story_markdown: str
    memory_mutation_ids: list[str]
    proposal_rows: list[dict[str, Any]]
    proposed_pages: list[dict[str, Any]]
    evidence_pack_id: str
    data_gaps: list[str]
    cold_start_used: bool
    run_id: str
    asof_time_utc: str
    created_at_utc: str

    @property
    def resolution_id(self) -> str:
        return _stable_id(
            "zopedia_news_business_resolution",
            self.symbol,
            self.source_event_id,
            self.run_id,
        )

    @property
    def proposal_ids(self) -> list[str]:
        return [_coerce_text(row.get("proposal_id")) for row in self.proposal_rows if _coerce_text(row.get("proposal_id"))]


def _coerce_text(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _normalize_symbol(value: object) -> str:
    text = _coerce_text(value).upper()
    return "".join(ch for ch in text if ch.isalnum() or ch in {".", "-"})


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            if hasattr(item, "tolist"):
                out.extend(_json_list(item))
            else:
                out.append(item)
        return out
    if isinstance(value, tuple):
        return _json_list(list(value))
    if hasattr(value, "tolist"):
        try:
            return _json_list(value.tolist())
        except Exception:
            return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                inner = stripped.strip("[]")
                return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return []


def _json_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("|".join(_coerce_text(part) for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}::{digest}"


def _asof_iso(value: object = "") -> str:
    timestamp = pd.to_datetime(value or datetime.now(timezone.utc), utc=True, errors="coerce")
    if pd.isna(timestamp):
        timestamp = pd.Timestamp.now(tz="UTC")
    return timestamp.isoformat()


def _event_symbols(row: pd.Series) -> list[str]:
    raw_symbols = _json_list(row.get("symbols"))
    if not raw_symbols:
        for key in ("symbol", "ticker", "entity_id"):
            value = _normalize_symbol(row.get(key))
            if value:
                raw_symbols = [value]
                break
    return list(dict.fromkeys(_normalize_symbol(symbol) for symbol in raw_symbols if _normalize_symbol(symbol)))


def _event_text(row: pd.Series) -> str:
    return " ".join(
        part
        for part in (
            _coerce_text(row.get("headline")),
            _coerce_text(row.get("summary")),
            _coerce_text(row.get("content")),
            _coerce_text(row.get("text")),
        )
        if part
    )


def _source_event_id(row: pd.Series, symbol: str) -> str:
    existing = _coerce_text(row.get("source_event_id") or row.get("event_id") or row.get("id"))
    if existing:
        return existing
    return _stable_id(
        "news_event",
        symbol,
        row.get("headline"),
        row.get("url"),
        row.get("published_at"),
    )


def _company_baseline_row(frame: pd.DataFrame | None, symbol: str) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns:
        return {}
    rows = frame.copy()
    rows["symbol"] = rows["symbol"].map(_normalize_symbol)
    match = rows[rows["symbol"] == symbol].head(1)
    return match.iloc[0].to_dict() if not match.empty else {}


def _company_name(symbol: str, baseline: dict[str, Any], news_row: pd.Series) -> str:
    for value in (
        baseline.get("company_name"),
        baseline.get("name"),
        baseline.get("security_name"),
        news_row.get("company_name"),
    ):
        text = _coerce_text(value)
        if text and text.upper() != symbol:
            return text
    return symbol


def _fundamental_rows(frame: pd.DataFrame | None, symbol: str, *, limit: int = 8) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    ticker_col = "ticker" if "ticker" in frame.columns else "symbol" if "symbol" in frame.columns else ""
    if not ticker_col:
        return []
    rows = frame.copy()
    rows[ticker_col] = rows[ticker_col].map(_normalize_symbol)
    rows = rows[rows[ticker_col] == symbol].copy()
    if rows.empty:
        return []
    if "report_date" in rows.columns:
        rows["_report_date"] = pd.to_datetime(rows["report_date"], utc=True, errors="coerce")
        rows = rows.sort_values("_report_date", ascending=False, na_position="last")
    preferred = (
        "Total Revenue",
        "Revenue",
        "Net Income",
        "Cash and Cash Equivalents",
        "Cash",
        "Free Cash Flow",
        "Operating Cash Flow",
        "Total Debt",
    )
    if "metric" in rows.columns:
        metric_text = rows["metric"].astype(str)
        preferred_rows = rows[metric_text.isin(preferred)]
        if not preferred_rows.empty:
            rows = pd.concat([preferred_rows, rows], ignore_index=True, sort=False).drop_duplicates()
    out: list[dict[str, Any]] = []
    for _, row in rows.head(max(int(limit), 1)).iterrows():
        value = row.get("value")
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            value = ""
        out.append(
            {
                "metric": _coerce_text(row.get("metric")),
                "value": value,
                "statement": _coerce_text(row.get("statement")),
                "report_date": _coerce_text(row.get("report_date")),
            }
        )
    return out


def _edgar_rows(frame: pd.DataFrame | None, symbol: str, *, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns:
        return []
    rows = frame.copy()
    rows["symbol"] = rows["symbol"].map(_normalize_symbol)
    rows = rows[rows["symbol"] == symbol].copy()
    if rows.empty:
        return []
    if "filing_date" in rows.columns:
        rows["_filing_date"] = pd.to_datetime(rows["filing_date"], utc=True, errors="coerce")
        rows = rows.sort_values("_filing_date", ascending=False, na_position="last")
    out: list[dict[str, Any]] = []
    for _, row in rows.head(max(int(limit), 1)).iterrows():
        out.append(
            {
                "form": _coerce_text(row.get("form")),
                "filing_date": _coerce_text(row.get("filing_date")),
                "filing_url": _coerce_text(row.get("filing_url")),
                "text": _coerce_text(row.get("filing_excerpt") or row.get("summary") or row.get("document_text"))[:1600],
            }
        )
    return out


def _page_metadata(page: dict[str, Any]) -> dict[str, Any]:
    return _json_dict(page.get("metadata") or page.get("metadata_json"))


def _page_entity_refs(page: dict[str, Any]) -> list[str]:
    refs = _json_list(page.get("entity_refs") or page.get("entity_refs_json"))
    return [_normalize_symbol(ref) for ref in refs if _normalize_symbol(ref)]


def _is_company_business_memory_page(page: dict[str, Any], *, symbol: str, company_name: str) -> bool:
    metadata = _page_metadata(page)
    metadata_symbol = _normalize_symbol(metadata.get("symbol"))
    title = _coerce_text(page.get("title")).lower()
    slug = _coerce_text(page.get("slug")).lower()
    page_type = _coerce_text(page.get("page_type")).lower()
    entity_refs = set(_page_entity_refs(page))
    company_token = _coerce_text(company_name).lower()
    names_match = (
        metadata_symbol == symbol
        or symbol in entity_refs
        or (company_token and company_token in title)
    )
    if not names_match:
        return False
    if metadata.get("source_type") == "news_business_resolution_business_memory":
        return True
    return page_type == "ticker" and ("business-memory" in slug or "business memory" in title)


def _is_generated_business_memory_page(page: dict[str, Any]) -> bool:
    return _page_metadata(page).get("source_type") == "news_business_resolution_business_memory"


def _company_business_memory_pages(
    pages: list[dict[str, Any]],
    *,
    symbol: str,
    company_name: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in pages:
        if _is_company_business_memory_page(page, symbol=symbol, company_name=company_name):
            out.append(page)
        if len(out) >= max(int(limit), 1):
            break
    return out


def _slot_from_memory_heading(value: object) -> str:
    normalized = _coerce_text(value).strip("# ").lower().replace(" ", "_")
    return normalized if normalized in BUSINESS_MEMORY_SLOTS else ""


def _split_memory_fact_source(value: object) -> tuple[str, str]:
    text = _coerce_text(value)
    if not text.endswith(")") or " (" not in text:
        return text, ""
    body, source = text.rsplit(" (", 1)
    return body.strip(), source[:-1].strip()


def _source_backed_memory_facts(page: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    body = _coerce_text(page.get("body_markdown"))
    if not body:
        return {}
    allowed_sources = set(SOURCE_BACKED_MEMORY_SOURCES)
    out: dict[str, list[dict[str, Any]]] = {}
    current_slot = ""
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current_slot = _slot_from_memory_heading(line)
            continue
        if not current_slot or not line.startswith("- "):
            continue
        fact_text, source = _split_memory_fact_source(line[2:].strip())
        if source not in allowed_sources:
            continue
        out.setdefault(current_slot, [])
        out[current_slot].append({"text": fact_text, "source": source, "confidence": "medium"})
    return out


def _matching_zopedia_pages(frame: pd.DataFrame | None, *, symbol: str, company_name: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    queries = [
        " ".join(part for part in (symbol, company_name, "business memory") if part),
        " ".join(part for part in (symbol, company_name, query, "business customers revenue products risks") if part),
        " ".join(part for part in (company_name, "business model customer demand") if part),
    ]
    page_frames: list[pd.DataFrame] = []
    for item in queries:
        try:
            found = search_prepared_zopedia_pages(
                frame,
                query=item,
                page_types=["ticker", "entity", "theme", "concept", "market_event"],
                limit=limit,
            )
        except Exception:
            found = pd.DataFrame()
        if isinstance(found, pd.DataFrame) and not found.empty:
            page_frames.append(found)
    if not page_frames:
        return []
    merged = pd.concat(page_frames, ignore_index=True, sort=False)
    if "page_id" in merged.columns:
        merged = merged.drop_duplicates(subset=["page_id"], keep="first")
    return [dict(row) for _, row in merged.head(max(int(limit), 1)).iterrows()]


def _add_slot(slot_facts: dict[str, list[dict[str, Any]]], slot: str, text: object, *, source: str, confidence: str = "medium") -> None:
    clean = _coerce_text(text)
    if not clean:
        return
    if slot not in BUSINESS_MEMORY_SLOTS:
        return
    slot_facts.setdefault(slot, [])
    if any(_coerce_text(item.get("text")) == clean for item in slot_facts[slot]):
        return
    slot_facts[slot].append({"text": clean[:1600], "source": source, "confidence": confidence})


def _initial_slot_facts(
    *,
    symbol: str,
    company_name: str,
    news_row: pd.Series,
    baseline: dict[str, Any],
    fundamentals: list[dict[str, Any]],
    edgar_rows: list[dict[str, Any]],
    business_memory_pages: list[dict[str, Any]],
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
        if _is_generated_business_memory_page(page):
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
            confidence="medium",
        )
    for item in fundamentals[:6]:
        metric = _coerce_text(item.get("metric"))
        if not metric:
            continue
        value = item.get("value")
        date = _coerce_text(item.get("report_date"))
        text = f"{metric}: {value}" + (f" ({date})" if date else "")
        _add_slot(slot_facts, "fundamentals", text, source="quarterly_fundamentals")
    headline = _coerce_text(news_row.get("headline"))
    event_text = _event_text(news_row)
    _add_slot(
        slot_facts,
        "confirmation_events",
        event_text or headline,
        source="news_articles",
        confidence="medium" if event_text else "low",
    )
    for row in edgar_rows[:3]:
        filing_text = _coerce_text(row.get("text"))
        form = _coerce_text(row.get("form"))
        if filing_text:
            _add_slot(slot_facts, "confirmation_events", f"{form}: {filing_text}" if form else filing_text, source="edgar_evidence")
    return {slot: facts for slot, facts in slot_facts.items() if facts}


def _llm_page_context(page: dict[str, Any], *, symbol: str, company_name: str) -> dict[str, Any]:
    is_memory_page = _is_company_business_memory_page(page, symbol=symbol, company_name=company_name)
    context: dict[str, Any] = {
        "page_id": _coerce_text(page.get("page_id")),
        "page_type": _coerce_text(page.get("page_type")),
        "title": _coerce_text(page.get("title")),
        "context_role": "existing_business_memory" if is_memory_page else "related_zopedia_context",
    }
    if is_memory_page and _is_generated_business_memory_page(page):
        context["source_backed_slot_facts"] = _source_backed_memory_facts(page)
        return context
    context["summary"] = _coerce_text(page.get("summary"))
    context["body_markdown"] = _coerce_text(page.get("body_markdown"))[:1800]
    return context


def _llm_resolution(
    *,
    llm_client: Any | None,
    symbol: str,
    company_name: str,
    news_row: pd.Series,
    baseline: dict[str, Any],
    fundamentals: list[dict[str, Any]],
    edgar_rows: list[dict[str, Any]],
    zopedia_pages: list[dict[str, Any]],
    business_memory_pages: list[dict[str, Any]],
    initial_slot_facts: dict[str, list[dict[str, Any]]],
    data_gaps: list[str],
) -> dict[str, Any] | None:
    if llm_client is None:
        return None
    context = {
        "symbol": symbol,
        "company_name": company_name,
        "incoming_news": {
            "headline": _coerce_text(news_row.get("headline")),
            "summary": _coerce_text(news_row.get("summary")),
            "url": _coerce_text(news_row.get("url")),
            "published_at": _coerce_text(news_row.get("published_at")),
        },
        "company_baseline": baseline,
        "fundamentals": fundamentals,
        "edgar_evidence": edgar_rows,
        "zopedia_pages_read": [
            _llm_page_context(page, symbol=symbol, company_name=company_name)
            for page in zopedia_pages[:5]
        ],
        "existing_business_memory_pages": [
            _llm_page_context(page, symbol=symbol, company_name=company_name)
            for page in business_memory_pages[:3]
        ],
        "initial_slot_facts": initial_slot_facts,
        "data_gaps": data_gaps,
        "allowed_slots": list(BUSINESS_MEMORY_SLOTS),
        "allowed_resolution_labels": list(RESOLUTION_LABELS),
    }
    try:
        return llm_client.generate_json(
            system_prompt=(
                "Resolve incoming company news against durable business memory. "
                "Use the evidence provided. Fill only source-backed business slots. "
                "Treat related theme, concept, or market-event pages as context only; do not promote them into company memory slots unless a company-specific source supports the fact. "
                "Explain what the news confirms, extends, contradicts, or leaves unresolved. "
                "Do not write a stock-price or technical-analysis summary."
            ),
            user_prompt=json.dumps(context, ensure_ascii=False, default=str),
            schema_name="news_business_resolution",
            schema=NEWS_BUSINESS_RESOLUTION_SCHEMA,
        )
    except (LLMAPIError, Exception):
        return None


def _merge_slot_facts(base: dict[str, list[dict[str, Any]]], overlay: object) -> dict[str, list[dict[str, Any]]]:
    merged = {slot: list(facts or []) for slot, facts in base.items() if facts}
    if not isinstance(overlay, dict):
        return merged
    for slot, facts in overlay.items():
        clean_slot = _coerce_text(slot)
        if clean_slot not in BUSINESS_MEMORY_SLOTS or not isinstance(facts, list):
            continue
        for item in facts:
            if not isinstance(item, dict):
                continue
            _add_slot(
                merged,
                clean_slot,
                item.get("text"),
                source=_coerce_text(item.get("source")) or "llm_resolution",
                confidence=_coerce_text(item.get("confidence")) or "medium",
            )
    return merged


def _fallback_story(
    *,
    symbol: str,
    company_name: str,
    headline: str,
    slot_facts: dict[str, list[dict[str, Any]]],
    data_gaps: list[str],
    has_existing_memory: bool,
) -> tuple[list[dict[str, Any]], str, str]:
    labels = ["extends_existing_story"] if has_existing_memory else ["insufficient_evidence"]
    changes = [
        {
            "label": labels[0],
            "text": (
                "The incoming news is mapped to the company business profile, but durable company memory is still thin."
                if not has_existing_memory
                else "The incoming news extends the existing company business memory."
            ),
            "evidence_refs": ["news_articles"],
        }
    ]
    business = (slot_facts.get("business_model") or [{}])[0].get("text", "")
    fundamentals = "; ".join(item.get("text", "") for item in (slot_facts.get("fundamentals") or [])[:3] if item.get("text"))
    story_parts = [f"### {company_name or symbol}", f"Incoming news: {headline or 'No headline provided.'}"]
    if business:
        story_parts.append(f"Business context: {business}")
    if fundamentals:
        story_parts.append(f"Fundamentals context: {fundamentals}")
    if data_gaps:
        story_parts.append(f"Open gaps: {', '.join(data_gaps[:4])}.")
    return changes, "\n\n".join(story_parts).strip(), "medium" if business or fundamentals else "low"


def _build_source_page(news_row: pd.Series, *, symbol: str, company_name: str, source_event_id: str, asof_time_utc: str) -> dict[str, Any]:
    headline = _coerce_text(news_row.get("headline")) or f"{symbol} news event"
    body = _event_text(news_row) or headline
    url = _coerce_text(news_row.get("url"))
    published_at = _coerce_text(news_row.get("published_at"))
    return {
        "page_id": build_zopedia_page_id(page_type="source", title=headline),
        "page_type": "source",
        "title": headline,
        "summary": _coerce_text(news_row.get("summary")) or headline,
        "body_markdown": body,
        "source_urls": [url] if url else [],
        "entity_refs": [item for item in [symbol, company_name] if item],
        "outgoing_links": [company_name] if company_name else [],
        "metadata": {
            "source_type": "news_business_resolution_source",
            "source_event_id": source_event_id,
            "symbol": symbol,
            "company_name": company_name,
            "published_at": published_at,
            "last_resolved_at_utc": asof_time_utc,
        },
    }


def _build_company_memory_page(
    *,
    symbol: str,
    company_name: str,
    slot_facts: dict[str, list[dict[str, Any]]],
    source_event_ids: list[str],
    source_urls: list[str],
    asof_time_utc: str,
    cold_start: bool,
) -> dict[str, Any]:
    title = f"{company_name or symbol} Business Memory"
    lines = [f"# {title}", "", f"Symbol: {symbol}", ""]
    for slot in BUSINESS_MEMORY_SLOTS:
        facts = slot_facts.get(slot) or []
        if not facts:
            continue
        lines.append(f"## {slot.replace('_', ' ').title()}")
        for fact in facts[:6]:
            source = _coerce_text(fact.get("source"))
            suffix = f" ({source})" if source else ""
            lines.append(f"- {_coerce_text(fact.get('text'))}{suffix}")
        lines.append("")
    summary = ""
    for preferred_slot in ("business_model", "customer_demand", "fundamentals", "confirmation_events"):
        facts = slot_facts.get(preferred_slot) or []
        if facts:
            summary = _coerce_text(facts[0].get("text"))
            break
    return {
        "page_id": build_zopedia_page_id(page_type="ticker", title=title, slug=f"{symbol.lower()}-business-memory"),
        "page_type": "ticker",
        "title": title,
        "slug": f"{symbol.lower()}-business-memory",
        "summary": summary or f"Cold-start business memory for {company_name or symbol}.",
        "body_markdown": "\n".join(lines).strip(),
        "source_urls": source_urls,
        "entity_refs": [item for item in [symbol, company_name] if item],
        "outgoing_links": [],
        "metadata": {
            "symbol": symbol,
            "company_name": company_name,
            "slot_tags": [slot for slot in BUSINESS_MEMORY_SLOTS if slot_facts.get(slot)],
            "source_event_ids": source_event_ids,
            "source_type": "news_business_resolution_business_memory",
            "freshness_class": "current",
            "cold_start": bool(cold_start),
            "last_resolved_at_utc": asof_time_utc,
        },
    }


def _proposal_rows(
    *,
    symbol: str,
    company_name: str,
    company_page: dict[str, Any],
    source_page: dict[str, Any],
    slot_facts: dict[str, list[dict[str, Any]]],
    resolved_changes: list[dict[str, Any]],
    data_gaps: list[str],
    write_policy: str,
    asof_time_utc: str,
) -> list[dict[str, Any]]:
    normalized_policy = _coerce_text(write_policy).lower() or "propose"
    if normalized_policy not in {"propose", "safe_auto"}:
        return []
    rationale = (
        f"Create or update business memory for {company_name or symbol} from a source-backed news resolution."
    )
    proposal = build_zopedia_change_proposal(
        proposal_type="company_business_memory_cold_start",
        page_id=_coerce_text(company_page.get("page_id")),
        title=f"{company_name or symbol} business memory cold start",
        rationale=rationale,
        payload={
            "symbol": symbol,
            "company_name": company_name,
            "write_policy": normalized_policy,
            "asof_time_utc": asof_time_utc,
            "proposed_pages": [source_page, company_page],
            "slot_facts": slot_facts,
            "resolved_changes": resolved_changes,
            "data_gaps": data_gaps,
        },
    )
    return [proposal]


def resolve_news_business_event(
    news_row: pd.Series,
    *,
    symbol: str,
    company_baselines_frame: pd.DataFrame | None = None,
    fundamentals_frame: pd.DataFrame | None = None,
    zopedia_pages_frame: pd.DataFrame | None = None,
    edgar_evidence_frame: pd.DataFrame | None = None,
    llm_client: Any | None = None,
    run_id: str = "",
    asof_time_utc: object = "",
    surface: str = "pipeline.news_business_resolution",
    write_policy: str = "propose",
) -> NewsBusinessResolutionResult:
    normalized_symbol = _normalize_symbol(symbol)
    asof_iso = _asof_iso(asof_time_utc)
    created_at = _asof_iso(datetime.now(timezone.utc))
    source_event_id = _source_event_id(news_row, normalized_symbol)
    baseline = _company_baseline_row(company_baselines_frame, normalized_symbol)
    company_name = _company_name(normalized_symbol, baseline, news_row)
    query = " ".join(part for part in (normalized_symbol, company_name, _coerce_text(news_row.get("headline"))) if part)
    zopedia_pages = _matching_zopedia_pages(
        zopedia_pages_frame,
        symbol=normalized_symbol,
        company_name=company_name,
        query=query,
    )
    business_memory_pages = _company_business_memory_pages(
        zopedia_pages,
        symbol=normalized_symbol,
        company_name=company_name,
    )
    has_existing_memory = bool(business_memory_pages)
    fundamentals = _fundamental_rows(fundamentals_frame, normalized_symbol)
    edgar_rows = _edgar_rows(edgar_evidence_frame, normalized_symbol)
    data_gaps: list[str] = []
    if not has_existing_memory:
        data_gaps.append("no_existing_company_memory_page")
    if not baseline:
        data_gaps.append("no_company_baseline")
    if not fundamentals:
        data_gaps.append("no_quarterly_fundamentals")
    headline = _coerce_text(news_row.get("headline"))
    if not headline and not _event_text(news_row):
        data_gaps.append("no_source_event_text")

    initial_slot_facts = _initial_slot_facts(
        symbol=normalized_symbol,
        company_name=company_name,
        news_row=news_row,
        baseline=baseline,
        fundamentals=fundamentals,
        edgar_rows=edgar_rows,
        business_memory_pages=business_memory_pages,
    )
    llm_payload = _llm_resolution(
        llm_client=llm_client,
        symbol=normalized_symbol,
        company_name=company_name,
        news_row=news_row,
        baseline=baseline,
        fundamentals=fundamentals,
        edgar_rows=edgar_rows,
        zopedia_pages=zopedia_pages,
        business_memory_pages=business_memory_pages,
        initial_slot_facts=initial_slot_facts,
        data_gaps=data_gaps,
    )
    slot_facts = _merge_slot_facts(initial_slot_facts, (llm_payload or {}).get("slot_facts") if isinstance(llm_payload, dict) else None)
    if isinstance(llm_payload, dict):
        resolved_changes = [item for item in list(llm_payload.get("resolved_changes") or []) if isinstance(item, dict)]
        story = _coerce_text(llm_payload.get("coherent_story_markdown"))
        confidence = _coerce_text(llm_payload.get("confidence")).lower() or "medium"
        for gap in list(llm_payload.get("data_gaps") or []):
            clean_gap = _coerce_text(gap)
            if clean_gap and clean_gap not in data_gaps:
                data_gaps.append(clean_gap)
    else:
        resolved_changes, story, confidence = _fallback_story(
            symbol=normalized_symbol,
            company_name=company_name,
            headline=headline,
            slot_facts=slot_facts,
            data_gaps=data_gaps,
            has_existing_memory=has_existing_memory,
        )
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    if not has_existing_memory and confidence == "high":
        confidence = "medium"
    if data_gaps and confidence == "high":
        confidence = "medium"

    source_page = _build_source_page(
        news_row,
        symbol=normalized_symbol,
        company_name=company_name,
        source_event_id=source_event_id,
        asof_time_utc=asof_iso,
    )
    company_page = _build_company_memory_page(
        symbol=normalized_symbol,
        company_name=company_name,
        slot_facts=slot_facts,
        source_event_ids=[source_event_id],
        source_urls=[_coerce_text(news_row.get("url"))] if _coerce_text(news_row.get("url")) else [],
        asof_time_utc=asof_iso,
        cold_start=not has_existing_memory,
    )
    proposed_frame, _ = prepare_zopedia_pages([source_page, company_page], now=pd.to_datetime(asof_iso, utc=True, errors="coerce").to_pydatetime())
    proposed_pages = proposed_frame.to_dict("records") if not proposed_frame.empty else []
    normalized_source_page_id = _coerce_text(source_page.get("page_id"))
    proposal_rows = _proposal_rows(
        symbol=normalized_symbol,
        company_name=company_name,
        company_page=company_page,
        source_page=source_page,
        slot_facts=slot_facts,
        resolved_changes=resolved_changes,
        data_gaps=data_gaps,
        write_policy=write_policy,
        asof_time_utc=asof_iso,
    )
    labels = []
    for item in resolved_changes:
        label = _coerce_text(item.get("label"))
        if label in RESOLUTION_LABELS and label not in labels:
            labels.append(label)
    if not labels:
        labels = ["insufficient_evidence"] if not has_existing_memory else ["extends_existing_story"]
    status = "cold_start_prepared" if not has_existing_memory else "resolved"
    if "no_source_event_text" in data_gaps:
        status = "insufficient_evidence"
    request = NewsBusinessResolutionRequest(
        source_event_ids=[source_event_id],
        symbols=[normalized_symbol],
        query=query,
        surface=surface,
        write_policy=write_policy,
        evidence_slot_policy=list(BUSINESS_MEMORY_SLOTS),
    )
    return NewsBusinessResolutionResult(
        request=request,
        symbol=normalized_symbol,
        company_name=company_name,
        source_event_id=source_event_id,
        source_event_ids=[source_event_id],
        source_headline=headline,
        source_url=_coerce_text(news_row.get("url")),
        source_published_at=_coerce_text(news_row.get("published_at")),
        status=status,
        confidence=confidence,
        resolution_labels=labels,
        source_page_ids=[normalized_source_page_id] if normalized_source_page_id else [],
        zopedia_page_ids_read=[_coerce_text(page.get("page_id")) for page in zopedia_pages if _coerce_text(page.get("page_id"))],
        fundamental_datasets_used=["quarterly_fundamentals"] if fundamentals else [],
        slot_facts=slot_facts,
        resolved_changes=resolved_changes,
        coherent_story_markdown=story,
        memory_mutation_ids=[],
        proposal_rows=proposal_rows,
        proposed_pages=proposed_pages,
        evidence_pack_id=_stable_id("news_business_evidence_pack", normalized_symbol, source_event_id, run_id),
        data_gaps=data_gaps,
        cold_start_used=not has_existing_memory,
        run_id=_coerce_text(run_id),
        asof_time_utc=asof_iso,
        created_at_utc=created_at,
    )


def _result_row(result: NewsBusinessResolutionResult) -> dict[str, Any]:
    return {
        "resolution_id": result.resolution_id,
        "run_id": result.run_id,
        "asof_time_utc": result.asof_time_utc,
        "symbol": result.symbol,
        "company_name": result.company_name,
        "source_event_id": result.source_event_id,
        "source_event_ids_json": _json_dumps(result.source_event_ids),
        "source_headline": result.source_headline,
        "source_url": result.source_url,
        "source_published_at": result.source_published_at,
        "status": result.status,
        "confidence": result.confidence,
        "resolution_labels_json": _json_dumps(result.resolution_labels),
        "source_page_ids_json": _json_dumps(result.source_page_ids),
        "zopedia_page_ids_read_json": _json_dumps(result.zopedia_page_ids_read),
        "fundamental_datasets_used_json": _json_dumps(result.fundamental_datasets_used),
        "slot_facts_json": _json_dumps(result.slot_facts),
        "resolved_changes_json": _json_dumps(result.resolved_changes),
        "coherent_story_markdown": result.coherent_story_markdown,
        "memory_mutation_ids_json": _json_dumps(result.memory_mutation_ids),
        "proposal_ids_json": _json_dumps(result.proposal_ids),
        "proposal_rows_json": _json_dumps(result.proposal_rows),
        "proposed_pages_json": _json_dumps(result.proposed_pages),
        "evidence_pack_id": result.evidence_pack_id,
        "data_gaps_json": _json_dumps(result.data_gaps),
        "cold_start_used": bool(result.cold_start_used),
        "created_at_utc": result.created_at_utc,
    }


def _news_symbol_rows(news_frame: pd.DataFrame, *, symbols: list[str] | None, limit: int) -> list[tuple[pd.Series, str]]:
    if not isinstance(news_frame, pd.DataFrame) or news_frame.empty:
        return []
    rows = news_frame.copy()
    if "published_at" in rows.columns:
        rows["_published_at"] = pd.to_datetime(rows["published_at"], utc=True, errors="coerce")
        rows = rows.sort_values("_published_at", ascending=False, na_position="last")
    requested = {_normalize_symbol(symbol) for symbol in list(symbols or []) if _normalize_symbol(symbol)}
    out: list[tuple[pd.Series, str]] = []
    seen: set[tuple[str, str]] = set()
    for _, row in rows.iterrows():
        row_symbols = _event_symbols(row)
        if requested:
            row_symbols = [symbol for symbol in row_symbols if symbol in requested]
        for symbol in row_symbols:
            source_event_id = _source_event_id(row, symbol)
            key = (source_event_id, symbol)
            if key in seen:
                continue
            seen.add(key)
            out.append((row, symbol))
            if len(out) >= max(int(limit), 1):
                return out
    return out


def build_news_business_resolution_results(
    *,
    news_frame: pd.DataFrame,
    company_baselines_frame: pd.DataFrame | None = None,
    fundamentals_frame: pd.DataFrame | None = None,
    zopedia_pages_frame: pd.DataFrame | None = None,
    edgar_evidence_frame: pd.DataFrame | None = None,
    symbols: list[str] | None = None,
    llm_client: Any | None = None,
    run_id: str = "",
    asof_time_utc: object = "",
    surface: str = "pipeline.news_business_resolution",
    write_policy: str = "propose",
    limit: int = 12,
) -> list[NewsBusinessResolutionResult]:
    results: list[NewsBusinessResolutionResult] = []
    for row, symbol in _news_symbol_rows(news_frame, symbols=symbols, limit=limit):
        results.append(
            resolve_news_business_event(
                row,
                symbol=symbol,
                company_baselines_frame=company_baselines_frame,
                fundamentals_frame=fundamentals_frame,
                zopedia_pages_frame=zopedia_pages_frame,
                edgar_evidence_frame=edgar_evidence_frame,
                llm_client=llm_client,
                run_id=run_id,
                asof_time_utc=asof_time_utc,
                surface=surface,
                write_policy=write_policy,
            )
        )
    return results


def serialize_news_business_resolution_results(results: list[NewsBusinessResolutionResult]) -> pd.DataFrame:
    rows = [_result_row(result) for result in list(results or [])]
    return pd.DataFrame(rows, columns=list(NEWS_BUSINESS_RESOLUTION_COLUMNS))


def business_memory_pages_from_results(results: list[NewsBusinessResolutionResult]) -> pd.DataFrame:
    pages: list[dict[str, Any]] = []
    for result in list(results or []):
        for page in list(result.proposed_pages or []):
            metadata = _json_dict(page.get("metadata") or page.get("metadata_json"))
            if metadata.get("source_type") == "news_business_resolution_business_memory":
                pages.append(page)
    if not pages:
        return pd.DataFrame()
    frame = pd.DataFrame(pages)
    if "page_id" in frame.columns:
        frame = frame.drop_duplicates(subset=["page_id"], keep="last")
    return frame.reset_index(drop=True)


def build_news_business_resolution_frames(
    *,
    news_frame: pd.DataFrame,
    company_baselines_frame: pd.DataFrame | None = None,
    fundamentals_frame: pd.DataFrame | None = None,
    zopedia_pages_frame: pd.DataFrame | None = None,
    edgar_evidence_frame: pd.DataFrame | None = None,
    symbols: list[str] | None = None,
    llm_client: Any | None = None,
    run_id: str = "",
    asof_time_utc: object = "",
    surface: str = "pipeline.news_business_resolution",
    write_policy: str = "propose",
    limit: int = 12,
) -> dict[str, pd.DataFrame]:
    results = build_news_business_resolution_results(
        news_frame=news_frame,
        company_baselines_frame=company_baselines_frame,
        fundamentals_frame=fundamentals_frame,
        zopedia_pages_frame=zopedia_pages_frame,
        edgar_evidence_frame=edgar_evidence_frame,
        symbols=symbols,
        llm_client=llm_client,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
        surface=surface,
        write_policy=write_policy,
        limit=limit,
    )
    return {
        "zopedia_news_business_resolutions": serialize_news_business_resolution_results(results),
        "zopedia_company_business_memory_pages": business_memory_pages_from_results(results),
    }


__all__ = [
    "BUSINESS_MEMORY_SLOTS",
    "NEWS_BUSINESS_RESOLUTION_COLUMNS",
    "NEWS_BUSINESS_RESOLUTION_SCHEMA",
    "NewsBusinessResolutionRequest",
    "NewsBusinessResolutionResult",
    "RESOLUTION_LABELS",
    "build_news_business_resolution_frames",
    "build_news_business_resolution_results",
    "business_memory_pages_from_results",
    "resolve_news_business_event",
    "serialize_news_business_resolution_results",
]
