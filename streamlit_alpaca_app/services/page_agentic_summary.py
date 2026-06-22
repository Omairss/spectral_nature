from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Callable

import pandas as pd

from .attention_surface import clean_attention_text
from .json_utils import to_jsonable, to_list
from .llm import (
    LLMAPIError,
    NARRATIVE_STYLE_RULE,
    get_prompt,
    register_narrative_prompt,
)


_PAGE_SUMMARY_SYSTEM_PROMPT = register_narrative_prompt(
    name="Page Agentic Summary",
    file="services/page_agentic_summary.py",
    group="Page Summaries",
    prompt=(
        f"You are the Spectral Nature page summary agent. {NARRATIVE_STYLE_RULE} "
        "Write from the supplied page data only. Do not invent live prices, catalysts, or macro releases. "
        "Focus on updates, what is worth looking into, and the data gaps that matter. "
        "Do not headline unknowns or narrate missing evidence. If evidence is thin, write the observable setup and move the open question "
        "into watch_items as a concrete evidence target. Data gaps must name evidence to go fetch, not complain about "
        "what was unavailable; write gaps as noun phrases or checks, not sentences about evidence being absent. "
        "For company pages, do not publish a price-chart-only or technical-only summary. A useful summary must connect "
        "the setup to the business, customers, product demand, financing, fundamentals, sector context, or source-backed news. "
        "If that context is not present, return no summary and put the business evidence needed in data_gaps. "
        "When using numeric levels, compare them literally. If close is below resistance, say near/below resistance, "
        "not through resistance. Do not turn technical levels into a driver. "
        "Use watch/investigate language, not personalized financial advice. "
        "Return concise markdown that can sit at the top of a dense research page."
    ),
)

_PAGE_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "summary_markdown": {"type": "string"},
        "watch_items": {"type": "array", "items": {"type": "string"}},
        "data_gaps": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["headline", "summary_markdown", "watch_items", "data_gaps", "confidence"],
}

_PAGE_SUMMARY_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "accepted": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "revision_instruction": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["accepted", "issues", "revision_instruction", "confidence"],
}

AgentRunner = Callable[..., dict[str, Any]]


def _clean(text: object) -> str:
    if text is None:
        return ""
    out = " ".join(str(text).split()).strip()
    return "" if out.lower() == "nan" else out


def _clean_page_narrative(text: object) -> str:
    clean = _clean(text)
    if not clean:
        return ""
    cleaned = clean_attention_text(clean)
    if cleaned:
        return cleaned
    return clean


def _compact_json(value: Any, *, limit: int = 12000) -> str:
    payload = to_jsonable(value)
    try:
        text = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        text = str(payload)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _env_positive_int(name: str, default: int, *, maximum: int | None = None) -> int:
    raw = _clean(os.getenv(name))
    try:
        value = int(raw) if raw else int(default)
    except Exception:
        value = int(default)
    value = max(value, 0)
    if maximum is not None:
        value = min(value, int(maximum))
    return value


def _page_summary_quality_revisions() -> int:
    return _env_positive_int("PAGE_AGENTIC_SUMMARY_QUALITY_REVISIONS", 2, maximum=4)


def page_summary_context_signature(context: dict[str, Any]) -> str:
    payload = to_jsonable(context if isinstance(context, dict) else {})
    try:
        text = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        text = str(payload)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _public_page_summary_payload(summary: dict[str, Any]) -> dict[str, Any]:
    payload = summary if isinstance(summary, dict) else {}
    status = _clean(payload.get("status")) or "unavailable"
    headline = _clean_page_narrative(payload.get("headline"))
    summary_markdown = _clean_page_narrative(payload.get("summary_markdown"))
    watch_items = [
        item
        for item in (_clean_page_narrative(value) for value in to_list(payload.get("watch_items")))
        if item
    ][:5]
    data_gaps = [
        item
        for item in (_clean_page_narrative(value) for value in to_list(payload.get("data_gaps")))
        if item
    ][:5]
    error = _public_unavailable_reason(payload.get("error")) if status != "ok" else ""
    public_payload = {
        "status": status,
        "surface": _clean(payload.get("surface")),
        "headline": headline,
        "summary_markdown": summary_markdown,
        "watch_items": watch_items,
        "data_gaps": data_gaps,
        "confidence": _clean(payload.get("confidence")) or "low",
        "error": error,
    }
    if status != "ok" and error and error not in public_payload["data_gaps"]:
        public_payload["data_gaps"] = [error, *public_payload["data_gaps"]][:5]
    return public_payload


def build_materialized_page_agentic_summary_row(
    *,
    surface: str,
    context: dict[str, Any],
    summary: dict[str, Any],
    generated_at_utc: object,
    run_id: str,
    ticker: str = "",
    context_label: str = "",
) -> dict[str, Any]:
    clean_surface = _clean(surface) or _clean((context or {}).get("surface")) or _clean((summary or {}).get("surface"))
    clean_ticker = _clean(ticker or (context or {}).get("ticker")).upper()
    safe_context = to_jsonable(context if isinstance(context, dict) else {})
    safe_summary = to_jsonable(_public_page_summary_payload(summary if isinstance(summary, dict) else {}))
    return {
        "surface": clean_surface,
        "context_label": _clean(context_label),
        "ticker": clean_ticker,
        "context_signature": page_summary_context_signature(context if isinstance(context, dict) else {}),
        "status": _clean(safe_summary.get("status")),
        "headline": _clean(safe_summary.get("headline")),
        "confidence": _clean(safe_summary.get("confidence")),
        "summary_json": json.dumps(safe_summary, ensure_ascii=True, sort_keys=True, default=str),
        "context_json": json.dumps(safe_context, ensure_ascii=True, sort_keys=True, default=str),
        "generated_at_utc": _clean(pd.to_datetime(generated_at_utc, utc=True, errors="coerce").isoformat()),
        "run_id": _clean(run_id),
    }


def materialized_page_agentic_summary(
    frame: pd.DataFrame,
    *,
    surface: str,
    context_signature: str,
    ticker: str = "",
) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    if "surface" not in frame.columns or "summary_json" not in frame.columns:
        return {}
    target_surface = _clean(surface)
    target_signature = _clean(context_signature)
    target_ticker = _clean(ticker).upper()
    rows = frame.copy()
    rows["surface"] = rows["surface"].astype(str).str.strip()
    rows = rows[rows["surface"] == target_surface]
    context_match = "surface"
    if target_signature and "context_signature" in rows.columns:
        exact_rows = rows[rows["context_signature"].astype(str).str.strip() == target_signature]
        if not exact_rows.empty:
            rows = exact_rows
            context_match = "exact"
        elif not target_ticker:
            context_match = "surface_fallback"
    if target_ticker and "ticker" in rows.columns:
        ticker_rows = rows[rows["ticker"].astype(str).str.upper().str.strip() == target_ticker]
        if not ticker_rows.empty:
            rows = ticker_rows
            if context_match != "exact":
                context_match = "ticker"
    if rows.empty:
        return {}
    if "generated_at_utc" in rows.columns:
        rows = rows.assign(
            _generated_at=pd.to_datetime(rows["generated_at_utc"], utc=True, errors="coerce")
        ).sort_values("_generated_at", ascending=False, na_position="last")
    row = rows.iloc[0]
    try:
        payload = json.loads(_clean(row.get("summary_json")) or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return {}
    payload.setdefault("surface", target_surface)
    payload.setdefault("status", _clean(row.get("status")))
    payload.setdefault("headline", _clean(row.get("headline")))
    payload.setdefault("confidence", _clean(row.get("confidence")))
    payload["materialized"] = {
        "generated_at_utc": _clean(row.get("generated_at_utc")),
        "run_id": _clean(row.get("run_id")),
        "context_signature": _clean(row.get("context_signature")),
        "context_match": context_match,
    }
    return payload


def _extract_symbols(context: dict[str, Any]) -> list[str]:
    symbols: list[str] = []

    def add(value: object) -> None:
        raw = _clean(value).upper()
        if raw and raw not in symbols and raw.replace(".", "").replace("-", "").isalnum() and len(raw) <= 8:
            symbols.append(raw)

    add((context or {}).get("ticker"))
    for key in ("opportunities", "market_opportunity_feed", "ticker_evidence"):
        for item in to_list((context or {}).get(key)):
            if isinstance(item, dict):
                add(item.get("symbol") or item.get("ticker"))
    return symbols[:8]


def _aql_agent_query(*, surface: str, context: dict[str, Any]) -> str:
    symbols = _extract_symbols(context)
    symbol_line = f" Focus symbols: {', '.join(symbols)}." if symbols else ""
    return (
        f"Use the shared AQL / Zopedia evidence path to write a grounded top-of-page summary for {surface}."
        f"{symbol_line} Start from the supplied page data, then check retained evidence or live evidence if needed. "
        "Focus on what changed, what is worth investigating next, and meaningful data gaps. "
        "Use watch/investigate language, not personalized financial advice.\n\n"
        "Supplied page data JSON:\n"
        f"{_compact_json(context, limit=9000)}"
    )


def _default_aql_agent_runner(**kwargs: Any) -> dict[str, Any]:
    from .aql_zopedia_engine import run_aql_zopedia_agent

    kwargs.setdefault("task", "page_summary")
    kwargs.setdefault("surface", "page_agentic_summary")
    return run_aql_zopedia_agent(**kwargs)


def _aql_agent_context(result: dict[str, Any]) -> dict[str, Any]:
    tool_calls = []
    for call in to_list((result or {}).get("tool_calls"))[:8]:
        if not isinstance(call, dict):
            continue
        tool_calls.append(
            {
                "tool_name": _clean(call.get("tool_name")),
                "status": _clean(call.get("status")),
                "arguments": call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
                "preview": _clean((call.get("result_summary") or {}).get("preview_text"))[:500]
                if isinstance(call.get("result_summary"), dict)
                else "",
            }
        )
    return {
        "run_id": _clean((result or {}).get("run_id")),
        "evidence_pack_id": _clean((result or {}).get("aql_evidence_pack_id")),
        "evidence_pack": result.get("aql_evidence_pack") if isinstance(result.get("aql_evidence_pack"), dict) else {},
        "status": _clean((result or {}).get("status")),
        "answer_markdown": _clean_page_narrative((result or {}).get("answer_markdown")),
        "confidence": _clean((result or {}).get("confidence")),
        "limitations": [_clean(item) for item in to_list((result or {}).get("limitations")) if _clean(item)][:6],
        "tool_calls": tool_calls,
    }


def _public_unavailable_reason(reason: object) -> str:
    text = _clean(reason)
    lowered = text.lower()
    if not text:
        return "Page summary was not produced for this run."
    if "page summary failed" in lowered and "exceeded" in lowered:
        return "Page summary failed: background job budget exceeded."
    if "exceeded" in lowered and "page aql summary" in lowered:
        return "Page summary was not produced within the background job budget."
    if "tool budget exhausted" in lowered or "trajectory monitor" in lowered:
        return "AQL did not return enough grounded evidence for a page summary."
    if "did not produce a grounded page summary" in lowered:
        return "AQL did not return enough grounded evidence for a page summary."
    if "llm runtime is not configured" in lowered:
        return "LLM runtime is not configured."
    if "formatting failed" in lowered:
        return "Page summary formatting failed."
    if "aql summary failed" in lowered:
        return "AQL page summary failed."
    return text


def _unavailable_page_summary(
    *,
    surface: str,
    reason: str,
    aql_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_surface = _clean(surface) or "Page"
    public_reason = _public_unavailable_reason(reason)
    data_gaps = [public_reason] if public_reason else []
    return {
        "status": "unavailable",
        "surface": clean_surface,
        "headline": "",
        "summary_markdown": "",
        "watch_items": [],
        "data_gaps": data_gaps[:5],
        "confidence": "low",
        "error": public_reason,
    }


def build_unavailable_page_agentic_summary(
    *,
    surface: str,
    reason: str,
    aql_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _unavailable_page_summary(
        surface=surface,
        reason=reason,
    )


def _records(frame: pd.DataFrame, *, limit: int = 12, columns: list[str] | None = None) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    out = frame.copy()
    if columns:
        out = out[[column for column in columns if column in out.columns]]
    return to_jsonable(out.head(max(int(limit), 1)))


def market_summary_context(
    *,
    business_filter: str,
    selected_horizon_label: str,
    opportunity_feed: pd.DataFrame,
    movers: pd.DataFrame,
    momentum: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "surface": "Market Explorer",
        "business_filter": _clean(business_filter) or "All Market",
        "selected_horizon": _clean(selected_horizon_label) or "1 Month",
        "row_counts": {
            "opportunity_feed": int(len(opportunity_feed)) if isinstance(opportunity_feed, pd.DataFrame) else 0,
            "daily_movers": int(len(movers)) if isinstance(movers, pd.DataFrame) else 0,
            "momentum_profiles": int(len(momentum)) if isinstance(momentum, pd.DataFrame) else 0,
        },
        "opportunities": _records(
            opportunity_feed,
            limit=14,
            columns=[
                "symbol",
                "company_name",
                "opportunity",
                "direction",
                "opportunity_score",
                "daily_change_pct",
                "return_1w_pct",
                "return_1m_pct",
                "return_3m_pct",
                "momentum_roc_score",
                "trend_fit_gap",
                "details",
            ],
        ),
    }


def stock_summary_context(
    *,
    ticker: str,
    taxonomy_summary: str,
    signal_summary: dict[str, Any],
    forecast: dict[str, Any],
    news_summary: dict[str, Any],
    attention_context: dict[str, Any],
    background_payload: dict[str, Any],
) -> dict[str, Any]:
    articles = news_summary.get("articles")
    article_records: list[dict[str, Any]] = []
    if isinstance(articles, pd.DataFrame) and not articles.empty:
        article_records = _records(
            articles,
            limit=6,
            columns=["headline", "title", "source", "published_at", "summary", "url"],
        )
    return {
        "surface": "Stock Investigator",
        "ticker": _clean(ticker).upper(),
        "taxonomy": _clean(taxonomy_summary),
        "technical_signals": to_jsonable(signal_summary or {}),
        "forecast": to_jsonable(forecast or {}),
        "news_summary_lines": [
            _clean(item)
            for item in to_list((news_summary or {}).get("summary_lines"))
            if _clean(item)
        ][:6],
        "recent_articles": article_records,
        "attention_context": {
            key: _clean((attention_context or {}).get(key))
            for key in [
                "llm_headline",
                "llm_summary_text",
                "llm_narrative_text",
                "llm_why_now",
                "llm_management_signal",
                "context_story_text",
            ]
            if _clean((attention_context or {}).get(key))
        },
        "background": {
            key: _clean((background_payload or {}).get(key))
            for key in [
                "company_background_text",
                "description_text",
                "llm_summary_text",
                "source_line",
            ]
            if _clean((background_payload or {}).get(key))
        },
    }


def broad_economy_summary_context(
    *,
    overview: pd.DataFrame,
    release_index: pd.DataFrame | None = None,
    lookback_years: int | None = None,
) -> dict[str, Any]:
    summary_frame = overview.copy() if isinstance(overview, pd.DataFrame) else pd.DataFrame()
    if not summary_frame.empty and "latest_date" in summary_frame.columns:
        summary_frame = summary_frame.sort_values("latest_date", ascending=False, na_position="last")
    return {
        "surface": "Broad Economy",
        "lookback_years": lookback_years,
        "macro_indicators": _records(
            summary_frame,
            limit=18,
            columns=[
                "category",
                "indicator",
                "series_id",
                "latest",
                "prev",
                "yoy",
                "latest_date",
                "units_short",
            ],
        ),
        "recent_releases": _records(
            release_index if isinstance(release_index, pd.DataFrame) else pd.DataFrame(),
            limit=10,
            columns=["release_id", "release_name", "release_date", "release_datetime", "name"],
        ),
    }


def broad_economy_overview_from_fred_summary(fred_summary_frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(fred_summary_frame, pd.DataFrame) or fred_summary_frame.empty:
        return pd.DataFrame()
    from .fred import format_fred_delta, format_fred_value

    overview = fred_summary_frame.copy()
    if "latest" not in overview.columns and {"latest_value", "units_short"}.issubset(overview.columns):
        overview["latest"] = [
            format_fred_value(value, units)
            for value, units in zip(overview["latest_value"], overview["units_short"])
        ]
    if "prev" not in overview.columns and {"prev_delta", "units_short"}.issubset(overview.columns):
        overview["prev"] = [
            format_fred_delta(value, units)
            for value, units in zip(overview["prev_delta"], overview["units_short"])
        ]
    if "yoy" not in overview.columns and {"yoy_delta", "units_short"}.issubset(overview.columns):
        overview["yoy"] = [
            format_fred_delta(value, units)
            for value, units in zip(overview["yoy_delta"], overview["units_short"])
        ]
    if "latest_date" in overview.columns:
        overview["latest_date"] = pd.to_datetime(overview["latest_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return overview


def _review_page_summary_payload(
    *,
    surface: str,
    original_prompt: str,
    draft_payload: dict[str, Any],
    llm_client: Any,
) -> dict[str, Any]:
    from .aql_zopedia_engine import run_aql_zopedia_structured_agent

    review_query = (
        "You are the Zopedia/AQL quality monitor for a page summary. "
        "Decide whether the draft can be shown at the top of the page. Use only the supplied page evidence prompt "
        "and draft. Accept only if the headline names a supported move/setup/focus rather than an unknown; the body "
        "does not turn technical levels into a cause; numeric level comparisons are literal; data gaps are concrete "
        "evidence targets; and the draft does not contain raw tool failures, missing-evidence prose, or unsupported "
        "market claims. For Stock Investigator pages, reject technical-only or price-chart-only drafts unless the draft "
        "also ties the setup to a business driver, customer/product demand, fundamentals, financing, sector context, "
        "or source-backed news. If it fails, list concrete issues and one revision instruction.\n\n"
        f"Surface: {surface}\n\n"
        "Page evidence prompt:\n"
        f"{original_prompt}\n\n"
        "Draft JSON:\n"
        f"{json.dumps(draft_payload, ensure_ascii=True, sort_keys=True, default=str)}"
    )
    result = run_aql_zopedia_structured_agent(
        query=review_query,
        schema_name="page_agentic_summary_review",
        schema=_PAGE_SUMMARY_REVIEW_SCHEMA,
        task="page_agentic_summary_review",
        surface=f"{surface}.page_agentic_summary_review",
        max_tool_calls=0,
        llm_client=llm_client,
        persist_findings=False,
    )
    if not isinstance(result, dict) or str(result.get("status") or "") != "completed":
        return {
            "accepted": False,
            "issues": [str((result or {}).get("error") or "Zopedia page summary quality monitor did not complete.")],
            "revision_instruction": "Revise the page summary to comply with the evidence contract.",
            "confidence": "low",
        }
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    return {
        "accepted": bool(payload.get("accepted")),
        "issues": [str(item).strip() for item in to_list(payload.get("issues")) if str(item).strip()][:8],
        "revision_instruction": _clean(payload.get("revision_instruction")),
        "confidence": _clean(payload.get("confidence")) or "low",
    }


def _revise_page_summary_payload(
    *,
    surface: str,
    original_prompt: str,
    draft_payload: dict[str, Any],
    review: dict[str, Any],
    llm_client: Any,
) -> dict[str, Any]:
    from .aql_zopedia_engine import run_aql_zopedia_structured_agent

    revision_query = (
        "Revise this page summary through the Zopedia/AQL quality contract. "
        "Use only the supplied page evidence prompt and draft JSON; do not add new facts. Preserve supported claims, "
        "remove unsupported precision, keep open questions in watch_items/data_gaps, and make the headline name a "
        "supported move/setup/focus rather than an unknown. If the draft cannot be made useful without inventing, "
        "return an empty summary_markdown and put the exact evidence target in data_gaps. For Stock Investigator pages, "
        "a price-chart-only or technical-only draft is not useful unless it is connected to business context.\n\n"
        "Quality monitor review JSON:\n"
        f"{json.dumps(review, ensure_ascii=True, sort_keys=True, default=str)}\n\n"
        f"Surface: {surface}\n\n"
        "Page evidence prompt:\n"
        f"{original_prompt}\n\n"
        "Draft JSON:\n"
        f"{json.dumps(draft_payload, ensure_ascii=True, sort_keys=True, default=str)}"
    )
    result = run_aql_zopedia_structured_agent(
        query=revision_query,
        schema_name="page_agentic_summary_quality",
        schema=_PAGE_SUMMARY_SCHEMA,
        task="page_agentic_summary_quality",
        surface=f"{surface}.page_agentic_summary_quality",
        max_tool_calls=0,
        llm_client=llm_client,
        persist_findings=False,
    )
    if not isinstance(result, dict) or str(result.get("status") or "") != "completed":
        return {
            "headline": "",
            "summary_markdown": "",
            "watch_items": [],
            "data_gaps": [str((result or {}).get("error") or "Zopedia page summary quality pass did not complete.")],
            "confidence": "low",
        }
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    return dict(payload) if isinstance(payload, dict) else {
        "headline": "",
        "summary_markdown": "",
        "watch_items": [],
        "data_gaps": ["Zopedia page summary quality pass returned an invalid payload."],
        "confidence": "low",
    }


def _quality_review_page_summary_payload(
    *,
    surface: str,
    original_prompt: str,
    payload: dict[str, Any],
    llm_client: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_payload = dict(payload if isinstance(payload, dict) else {})
    reviews: list[dict[str, Any]] = []
    current_review = _review_page_summary_payload(
        surface=surface,
        original_prompt=original_prompt,
        draft_payload=current_payload,
        llm_client=llm_client,
    )
    current_review["status"] = "reviewed"
    current_review["revision_attempt"] = 0
    reviews.append(dict(current_review))
    for attempt in range(1, _page_summary_quality_revisions() + 1):
        if bool(current_review.get("accepted")):
            break
        current_payload = _revise_page_summary_payload(
            surface=surface,
            original_prompt=original_prompt,
            draft_payload=current_payload,
            review=current_review,
            llm_client=llm_client,
        )
        current_review = _review_page_summary_payload(
            surface=surface,
            original_prompt=original_prompt,
            draft_payload=current_payload,
            llm_client=llm_client,
        )
        current_review["status"] = "reviewed_after_revision"
        current_review["revision_attempt"] = attempt
        reviews.append(dict(current_review))
    review_state = {
        "status": "accepted" if bool(current_review.get("accepted")) else "failed_quality_review",
        "reviews": reviews,
    }
    if not bool(current_review.get("accepted")):
        current_payload = {
            "headline": "",
            "summary_markdown": "",
            "watch_items": [],
            "data_gaps": [
                "Zopedia page summary failed the quality monitor after revision.",
                *list(current_review.get("issues") or [])[:5],
            ],
            "confidence": "low",
        }
    return current_payload, review_state


def build_page_agentic_summary(
    *,
    surface: str,
    context: dict[str, Any],
    llm_client: Any | None,
    aql_agent_runner: AgentRunner | None = None,
) -> dict[str, Any]:
    clean_surface = _clean(surface) or _clean((context or {}).get("surface")) or "Page"
    if llm_client is None:
        return {
            "status": "unavailable",
            "surface": clean_surface,
            "headline": "",
            "summary_markdown": "",
            "watch_items": [],
            "data_gaps": ["LLM runtime is not configured."],
            "confidence": "low",
            "error": "LLM runtime is not configured.",
        }

    runner = aql_agent_runner or _default_aql_agent_runner
    try:
        aql_result = runner(
            query=_aql_agent_query(surface=clean_surface, context=context or {}),
            force_refresh=False,
            llm_client=llm_client,
            persist_findings=False,
        )
    except Exception as exc:
        return _unavailable_page_summary(
            surface=clean_surface,
            reason=f"AQL summary failed: {type(exc).__name__}: {exc}",
        )

    aql_context = _aql_agent_context(aql_result if isinstance(aql_result, dict) else {})
    if aql_context["status"] != "completed" or not aql_context["answer_markdown"]:
        error_text = _clean((aql_result or {}).get("error")) if isinstance(aql_result, dict) else ""
        reason = "; ".join(
            item
            for item in [
                "AQL agent did not produce a grounded page summary.",
                error_text,
                *to_list(aql_context.get("limitations")),
            ]
            if _clean(item)
        )
        return _unavailable_page_summary(
            surface=clean_surface,
            reason=reason or "AQL agent did not produce a grounded page summary.",
            aql_context=aql_context,
        )

    user_prompt = (
        f"Page: {clean_surface}\n\n"
        "Page data JSON:\n"
        f"{_compact_json(context)}\n\n"
        "AQL agent result JSON:\n"
        f"{_compact_json(aql_context, limit=9000)}\n\n"
        "Write one short markdown summary followed by what is worth checking next. "
        "Use only the supplied page data and AQL agent result. Headlines must name a supported move, setup, "
        "or investigation focus. Do not use headlines that say the cause is unknown. Treat technical levels as context, "
        "not as proof of why the move happened. For Stock Investigator pages, publish only if the summary connects "
        "the setup to business context, source-backed news, fundamentals, sector context, financing, or customer/product demand."
    )
    quality_review: dict[str, Any] = {"status": "not_run"}
    try:
        from .aql_zopedia_engine import run_aql_zopedia_structured_agent

        formatting_prompt = f"{get_prompt(_PAGE_SUMMARY_SYSTEM_PROMPT)}\n\n{user_prompt}"
        structured_result = run_aql_zopedia_structured_agent(
            query=formatting_prompt,
            schema_name="page_agentic_summary",
            schema=_PAGE_SUMMARY_SCHEMA,
            task="page_summary_format",
            surface=f"{clean_surface}.page_agentic_summary",
            max_tool_calls=0,
            llm_client=llm_client,
            persist_findings=False,
        )
        payload = structured_result.get("payload") if isinstance(structured_result, dict) else None
        if not isinstance(payload, dict):
            raise LLMAPIError(_clean((structured_result or {}).get("error")) or "AQL/Zopedia structured summary failed.")
        payload, quality_review = _quality_review_page_summary_payload(
            surface=clean_surface,
            original_prompt=formatting_prompt,
            payload=payload,
            llm_client=llm_client,
        )
    except LLMAPIError as exc:
        return _unavailable_page_summary(
            surface=clean_surface,
            reason=f"LLM summary formatting failed: {exc}",
            aql_context=aql_context,
        )
    except Exception as exc:
        return _unavailable_page_summary(
            surface=clean_surface,
            reason=f"LLM summary formatting failed: {type(exc).__name__}: {exc}",
            aql_context=aql_context,
        )

    if _clean(quality_review.get("status")) == "failed_quality_review" or not _clean(payload.get("summary_markdown")):
        return _unavailable_page_summary(
            surface=clean_surface,
            reason="Zopedia page summary failed the quality monitor.",
            aql_context={
                **aql_context,
                "quality_review": quality_review,
            },
        )

    return {
        "status": "ok",
        "surface": clean_surface,
        "headline": _clean_page_narrative(payload.get("headline")),
        "summary_markdown": _clean_page_narrative(payload.get("summary_markdown")),
        "watch_items": [
            item
            for item in (_clean_page_narrative(value) for value in to_list(payload.get("watch_items")))
            if item
        ][:5],
        "data_gaps": [
            item
            for item in (_clean_page_narrative(value) for value in to_list(payload.get("data_gaps")))
            if item
        ][:5],
        "confidence": _clean(payload.get("confidence")) or "low",
        "error": "",
        "aql_agent": aql_context,
        "quality_review": quality_review,
    }


__all__ = [
    "broad_economy_summary_context",
    "broad_economy_overview_from_fred_summary",
    "build_materialized_page_agentic_summary_row",
    "build_page_agentic_summary",
    "build_unavailable_page_agentic_summary",
    "market_summary_context",
    "materialized_page_agentic_summary",
    "page_summary_context_signature",
    "stock_summary_context",
]
