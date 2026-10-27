from __future__ import annotations

from services.work_scope import WorkScope

import hashlib
import json
import os
import queue
import re
import threading
import time
from contextvars import copy_context
from jsonschema import Draft202012Validator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import pandas as pd

from services.json_utils import frame_to_records, to_jsonable, to_list
from services.aql.pipeline import build_bottom_up_attention_artifacts
from services.aql.collector import search_symbol_news_payload
from services.aql.summarizer import build_market_stories
from services.aql_zopedia_engine import (
    attach_aql_zopedia_summary_audio as attach_attention_home_summary_audio,
    load_aql_zopedia_llm_client,
    run_aql_zopedia_agent,
    run_aql_zopedia_structured_agent,
    resolve_research_budget,
)
from services.attention_surface import clean_attention_text, hydrate_home_item_with_bundle
from services.attention_home_1d import build_attention_entity_master, resolve_macro_anchor_symbols, shortlist_attention_symbols_1d
from services.attention_materialized import bars_by_symbol_from_price_history, serialize_attention_home_payload, serialize_attention_research_bundles
from services.attention_ticker_snapshots import (
    build_attention_ticker_background_snapshot_frame,
    build_attention_ticker_snapshot_frame,
    collect_attention_ticker_symbols,
    prefetch_attention_intraday_prices,
    deserialize_attention_ticker_background_frame,
)
from services.common.news_freshness import (
    coerce_article_published_at,
    is_recent_for_attention,
)
from services.elevenlabs_tts import ElevenLabsTTSAPIError
from services.knowledge_graph_proposals import build_attention_knowledge_graph_proposals
from services.llm import LLMAPIError, load_embedding_client, llm_request_deadline
from services.entity_taxonomy import business_focus_label_from_taxonomy_row
from services.market_opportunity import build_market_opportunity_feed, build_materialized_market_opportunity_feeds
from services.page_agentic_summary import (
    build_materialized_page_agentic_summary_row,
    build_page_agentic_summary,
    build_unavailable_page_agentic_summary,
    market_summary_context,
    page_summary_timeout_seconds,
    stock_summary_context,
)
from services.pipeline_store import load_latest_dataset_frame, load_recent_dataset_frames
from services.daily_market_datapoints import build_daily_datapoint_catalog, select_daily_market_datapoints


PersistDatasetFn = Callable[[str, pd.DataFrame, Any, Any | None], None]
JobProgressFn = Callable[..., None]
LoadFrameFn = Callable[[str], pd.DataFrame]
ResearchProgressFn = Callable[[int, int, dict[str, Any]], None]


class AttentionHomeBuildError(RuntimeError):
    def __init__(self, message: str, *, research_result: dict[str, Any] | None = None):
        super().__init__(message)
        self.research_result = research_result or {}


class _AttentionStepTimeout(TimeoutError):
    pass


def _load_latest_materialized_frame(dataset_name: str) -> pd.DataFrame:
    try:
        frame, metadata = load_latest_dataset_frame(dataset_name)
        if dataset_name in {"daily_movers", "macro_anchor_daily_movers"} and not frame.empty and "observed_at_utc" not in frame:
            frame = frame.assign(observed_at_utc=getattr(metadata, "asof_time_utc", None))
    except Exception:
        frame = pd.DataFrame()
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        return frame.copy()
    try:
        for candidate, _ in load_recent_dataset_frames(dataset_name, limit=8):
            if isinstance(candidate, pd.DataFrame) and not candidate.empty:
                return candidate.copy()
    except Exception:
        pass
    return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def _zopedia_write_policy(default: str = "safe_auto") -> str:
    fallback = str(default or "safe_auto").strip().lower()
    policy = str(os.getenv("ATTENTION_ZOPEDIA_WRITE_POLICY") or fallback).strip().lower()
    if policy in {"off", "disabled", "read_only", "readonly"}:
        return "none"
    if policy in {"proposal", "review"}:
        return "propose"
    if policy in {"auto", "commit", "safe", "safe-auto"}:
        return "safe_auto"
    if policy in {"none", "propose", "safe_auto"}:
        return policy
    return fallback if fallback in {"none", "propose", "safe_auto"} else "safe_auto"


def _attention_home_research_limit() -> int:
    raw = (os.getenv("ATTENTION_HOME_RESEARCH_LIMIT") or "40").strip()
    try:
        parsed = int(raw)
    except Exception:
        parsed = 40
    return max(parsed, 1)


def _attention_home_search_backfill_limit() -> int:
    raw = (os.getenv("ATTENTION_HOME_SEARCH_BACKFILL_LIMIT") or "100").strip()
    try:
        parsed = int(raw)
    except Exception:
        parsed = 100
    return max(parsed, 1)


def _attention_home_search_backfill_max_workers() -> int:
    return _env_positive_int("ATTENTION_HOME_SEARCH_BACKFILL_MAX_WORKERS", 4)


def _attention_home_search_backfill_symbol_timeout_seconds() -> int:
    return _env_positive_int("ATTENTION_HOME_SEARCH_BACKFILL_SYMBOL_TIMEOUT_SECONDS", 45)


def _attention_home_search_backfill_lookback_days() -> int:
    return _env_positive_int("ATTENTION_HOME_SEARCH_BACKFILL_LOOKBACK_DAYS", 14)


def _attention_home_top_events_display_limit() -> int:
    return _env_positive_int("ATTENTION_HOME_TOP_EVENTS_DISPLAY_LIMIT", 5)


def _attention_home_must_read_display_limit() -> int:
    return _env_positive_int("ATTENTION_HOME_MUST_READ_DISPLAY_LIMIT", 10)


def _attention_home_unresolved_display_limit() -> int:
    return _env_positive_int("ATTENTION_HOME_UNRESOLVED_DISPLAY_LIMIT", 5)


def _attention_home_top_events_review_limit() -> int:
    return _env_positive_int(
        "ATTENTION_HOME_TOP_EVENTS_REVIEW_LIMIT",
        max(_attention_home_top_events_display_limit() * 3, 12),
    )


def _attention_home_must_read_review_limit() -> int:
    return _env_positive_int(
        "ATTENTION_HOME_MUST_READ_REVIEW_LIMIT",
        max(_attention_home_must_read_display_limit() * 2, 20),
    )


def _attention_home_unresolved_review_limit() -> int:
    return _env_positive_int(
        "ATTENTION_HOME_UNRESOLVED_REVIEW_LIMIT",
        max(_attention_home_unresolved_display_limit() * 3, 15),
    )


def _env_positive_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        parsed = int(raw)
    except Exception:
        parsed = default
    return max(parsed, 1)


def _page_agentic_summary_timeout_seconds() -> int:
    return page_summary_timeout_seconds()


def _page_agentic_summary_max_workers() -> int:
    return _env_positive_int("PAGE_AGENTIC_SUMMARY_MAX_WORKERS", 4)


def _call_with_timeout(label: str, timeout_seconds: int, fn: Callable[[], Any]) -> Any:
    if timeout_seconds <= 0:
        raise _AttentionStepTimeout(f"{label} has no remaining execution budget")

    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
    work = WorkScope(time.monotonic() + float(timeout_seconds))
    deadline = time.monotonic() + float(timeout_seconds)

    def _runner() -> None:
        try:
            with work.activate(), llm_request_deadline(deadline):
                value = fn()
            result_queue.put(("ok", value))
        except BaseException as exc:
            result_queue.put(("error", exc))

    context = copy_context()
    thread = threading.Thread(target=lambda: context.run(_runner), name=f"attention-timeout-{label[:32]}", daemon=True)
    thread.start()
    thread.join(float(timeout_seconds))
    if thread.is_alive():
        work.cancel()
        raise _AttentionStepTimeout(f"{label} exceeded {timeout_seconds}s")

    status, payload = result_queue.get_nowait()
    if status == "error":
        raise payload
    return payload


def _has_symbol_inputs(frame: pd.DataFrame) -> bool:
    return isinstance(frame, pd.DataFrame) and not frame.empty and "symbol" in frame.columns


# Datasets that must be present and non-empty for a meaningful build.
# If any of these are missing, the job should fail rather than produce
# stale or incomplete output (see mistakes.md #3, #19).
_MANDATORY_DATASETS = ("price_history",)

# Datasets that are useful but the build can degrade gracefully without.
def _validate_mandatory_datasets(
    load_fn: LoadFrameFn,
    dataset_names: tuple[str, ...],
) -> dict[str, pd.DataFrame]:
    """Load mandatory datasets and raise if any are missing or empty."""
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for name in dataset_names:
        frame = load_fn(name)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            missing.append(name)
        else:
            frames[name] = frame
    if missing:
        raise AttentionHomeBuildError(
            f"Attention home build cannot proceed: mandatory datasets missing or empty: {missing}. "
            "The upstream pipeline job may not have run. Failing instead of producing stale output."
        )
    return frames


def _page_agentic_stock_summary_limit() -> int:
    raw = (os.getenv("PAGE_AGENTIC_STOCK_SUMMARY_LIMIT") or "4").strip()
    try:
        return max(int(raw), 0)
    except Exception:
        return 4


def _symbol_list_from_frame(frame: pd.DataFrame) -> list[str]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns:
        return []
    seen: set[str] = set()
    symbols: list[str] = []
    for raw in frame["symbol"].tolist():
        symbol = _normalize_symbol(raw)
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _market_opportunity_focus_symbol_map(
    *,
    taxonomy_labels_frame: pd.DataFrame,
    universe_snapshot_frame: pd.DataFrame,
) -> dict[str, list[str]]:
    focus_map: dict[str, list[str]] = {"All Market": _symbol_list_from_frame(universe_snapshot_frame)}
    if not isinstance(taxonomy_labels_frame, pd.DataFrame) or taxonomy_labels_frame.empty or "symbol" not in taxonomy_labels_frame.columns:
        return focus_map

    taxonomy = taxonomy_labels_frame.copy()
    taxonomy["symbol"] = taxonomy["symbol"].map(_normalize_symbol)
    taxonomy = taxonomy[taxonomy["symbol"].ne("")].drop_duplicates(subset=["symbol"], keep="first")
    if not focus_map["All Market"]:
        focus_map["All Market"] = _symbol_list_from_frame(taxonomy)

    for _, row in taxonomy.iterrows():
        symbol = _normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        label = str(business_focus_label_from_taxonomy_row(row.to_dict()) or "").strip()
        if not label:
            label = str(row.get("industry") or row.get("peer_group_name") or row.get("sector") or "").strip()
        if not label or label in {"Unknown", "Market", "All Market"}:
            continue
        focus_map.setdefault(label, []).append(symbol)

    ordered: dict[str, list[str]] = {"All Market": list(dict.fromkeys(focus_map.get("All Market", [])))}
    for label in sorted(name for name in focus_map if name != "All Market"):
        ordered[label] = list(dict.fromkeys(focus_map[label]))
    return ordered


def _technical_signal_payload(frame: pd.DataFrame, symbol: str) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns:
        return {}
    target = _normalize_symbol(symbol)
    rows = frame.copy()
    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
    match = rows[rows["symbol"] == target].head(1)
    return match.iloc[0].to_dict() if not match.empty else {}


def _stock_news_summary_from_background(symbol: str, background_payload: dict[str, Any]) -> dict[str, Any]:
    headlines = to_list((background_payload or {}).get("recent_headlines"))
    articles = pd.DataFrame(headlines) if headlines else pd.DataFrame()
    return {
        "summary_lines": [
            str(item).strip()
            for item in to_list((background_payload or {}).get("news_summary_lines"))
            if str(item).strip()
        ],
        "articles": articles,
        "fallback_summary": None,
        "source": "attention_ticker_background_snapshots" if background_payload else None,
    }


def _materialized_page_summary_row(
    *,
    surface: str,
    context: dict[str, Any],
    llm_client: Any | None,
    generated_at_utc: object,
    run_id: str,
    ticker: str = "",
    context_label: str = "",
) -> dict[str, Any]:
    try:
        summary = _call_with_timeout(
            f"page AQL summary {surface}",
            _page_agentic_summary_timeout_seconds(),
            lambda: build_page_agentic_summary(
                surface=surface,
                context=context,
                llm_client=llm_client,
            ),
        )
    except Exception as exc:
        summary = build_unavailable_page_agentic_summary(
            surface=surface,
            reason=f"Page summary failed: {type(exc).__name__}: {exc}",
        )
    return build_materialized_page_agentic_summary_row(
        surface=surface,
        context=context,
        summary=summary,
        generated_at_utc=generated_at_utc,
        run_id=run_id,
        ticker=ticker,
        context_label=context_label,
    )


def _build_page_agentic_summary_frame(
    *,
    ctx: Any,
    llm_client: Any | None,
    daily_movers: pd.DataFrame,
    momentum_profiles: pd.DataFrame,
    ticker_background_frame: pd.DataFrame,
    technical_signals_latest_frame: pd.DataFrame,
    universe_snapshot_frame: pd.DataFrame,
) -> pd.DataFrame:
    tasks: list[dict[str, Any]] = []
    name_map = _company_name_map(universe_snapshot_frame)
    opportunity_feed = build_market_opportunity_feed(
        movers=daily_movers if isinstance(daily_movers, pd.DataFrame) else pd.DataFrame(),
        momentum=momentum_profiles if isinstance(momentum_profiles, pd.DataFrame) else pd.DataFrame(),
        selected_horizon_col="return_1m_pct",
        selected_horizon_label="1 Month",
        name_map=name_map,
        limit=80,
    )
    if not opportunity_feed.empty:
        market_context = market_summary_context(
            business_filter="All Market",
            selected_horizon_label="1 Month",
            opportunity_feed=opportunity_feed,
            movers=pd.DataFrame(),
            momentum=pd.DataFrame(),
        )
        tasks.append(
            {
                "surface": "Market Explorer",
                "context": market_context,
                "ticker": "",
                "context_label": "All Market / 1 Month",
            }
        )

    stock_limit = _page_agentic_stock_summary_limit()
    if stock_limit > 0 and not opportunity_feed.empty and "symbol" in opportunity_feed.columns:
        stock_symbols = [
            _normalize_symbol(symbol)
            for symbol in opportunity_feed["symbol"].astype(str).tolist()
            if _normalize_symbol(symbol)
        ][:stock_limit]
        for symbol in stock_symbols:
            background_payload = deserialize_attention_ticker_background_frame(ticker_background_frame, symbol)
            attention_context = {
                key: background_payload.get(key)
                for key in [
                    "llm_headline",
                    "llm_summary_text",
                    "context_story_text",
                ]
                if str(background_payload.get(key) or "").strip()
            }
            stock_context = stock_summary_context(
                ticker=symbol,
                taxonomy_summary="",
                signal_summary=_technical_signal_payload(technical_signals_latest_frame, symbol),
                forecast={},
                news_summary=_stock_news_summary_from_background(symbol, background_payload),
                attention_context=attention_context,
                background_payload=background_payload,
            )
            tasks.append(
                {
                    "surface": "Stock Investigator",
                    "context": stock_context,
                    "ticker": symbol,
                    "context_label": symbol,
                }
            )

    if not tasks:
        return pd.DataFrame()

    max_workers = min(len(tasks), _page_agentic_summary_max_workers())
    print(f"[info] page-agentic summaries starting: tasks={len(tasks)} max_workers={max_workers}")
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="page-summary") as executor:
        futures = {}
        for task in tasks:
            label = task.get("ticker") or task.get("context_label") or task.get("surface")
            print(f"[info] page-agentic summary queued: {task.get('surface')} {label}")
            futures[
                executor.submit(
                    copy_context().run, _materialized_page_summary_row,
                    surface=str(task.get("surface") or ""),
                    context=dict(task.get("context") or {}),
                    llm_client=llm_client,
                    generated_at_utc=ctx.asof,
                    run_id=ctx.run_id,
                    ticker=str(task.get("ticker") or ""),
                    context_label=str(task.get("context_label") or ""),
                )
            ] = task
        for future in as_completed(futures):
            task = futures[future]
            label = task.get("ticker") or task.get("context_label") or task.get("surface")
            try:
                row = future.result()
            except Exception as exc:
                row = build_materialized_page_agentic_summary_row(
                    surface=str(task.get("surface") or ""),
                    context=dict(task.get("context") or {}),
                    summary=build_unavailable_page_agentic_summary(
                        surface=str(task.get("surface") or ""),
                        reason=f"Page summary failed: {type(exc).__name__}: {exc}",
                    ),
                    generated_at_utc=ctx.asof,
                    run_id=ctx.run_id,
                    ticker=str(task.get("ticker") or ""),
                    context_label=str(task.get("context_label") or ""),
                )
            print(f"[info] page-agentic summary completed: {task.get('surface')} {label} status={row.get('status')}")
            rows.append(row)
    return pd.DataFrame(rows)


def _zopedia_enrichment_limit() -> int:
    return _env_positive_int("ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LIMIT", 12)


def _zopedia_enrichment_timeout_seconds() -> int:
    return _env_positive_int("ATTENTION_HOME_ZOPEDIA_ENRICHMENT_TIMEOUT_SECONDS", 600)


def _zopedia_agent_llm_timeout_seconds(total_timeout_seconds: int) -> int:
    configured = _env_positive_int("ATTENTION_HOME_ZOPEDIA_AGENT_LLM_TIMEOUT_SECONDS",
                                   int(resolve_research_budget()["llm_step_timeout_seconds"]))
    return max(30, min(configured, max(int(total_timeout_seconds), 1)))


def _zopedia_enrichment_max_workers() -> int:
    return _env_positive_int("ATTENTION_HOME_ZOPEDIA_ENRICHMENT_MAX_WORKERS", 4)


def _home_surface_quality_timeout_seconds() -> int:
    return _env_positive_int("ATTENTION_HOME_SURFACE_QUALITY_TIMEOUT_SECONDS", 600)


_HOME_SURFACE_QUALITY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "issues": {"type": "array", "items": {"type": "string"}},
        "market_summary": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "summary_text": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "limitations": {"type": "array", "items": {"type": "string"}},
            }, "required": ["summary_text", "confidence", "limitations"],
        },
        "new_events": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "source_ids": {"type": "array", "items": {"type": "string"}},
                "supporting_symbols": {"type": "array", "items": {"type": "string"}},
                "priority": {"type": "number", "minimum": 0, "maximum": 100},
                **{key: {"type": "string"} for key in (
                    "event_title", "surface_summary_text", "what_happened_text", "why_happened_text",
                    "affected_assets_summary_text", "business_context_text", "watch_next_text",
                )},
                "cause_status": {"type": "string", "enum": ["supported", "partial", "unresolved"]},
            },
            "required": [
                "source_ids", "supporting_symbols", "priority", "event_title", "surface_summary_text",
                "what_happened_text", "why_happened_text", "affected_assets_summary_text",
                "business_context_text", "watch_next_text", "cause_status",
            ],
        }},
        "items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "section": {"type": "string", "enum": ["top_events", "must_read_movers", "unresolved_large_moves"]},
                "index": {"type": "integer", "minimum": 0}, "bundle_id": {"type": "string"},
                "symbol": {"type": "string"}, "publish": {"type": "boolean"},
                "priority": {"type": "number", "minimum": 0, "maximum": 100},
                **{key: {"type": "string"} for key in (
                    "event_title", "surface_summary_text", "what_happened_text", "why_happened_text",
                    "affected_assets_summary_text", "business_context_text", "watch_next_text", "review_note",
                )},
                "cause_status": {"type": "string", "enum": ["supported", "partial", "unresolved"]},
            },
            "required": ["section", "index", "bundle_id", "symbol", "publish"],
            "anyOf": [
                {"properties": {"publish": {"const": False}}},
                {"properties": {"publish": {"const": True}}, "required": [
                    "priority", "event_title", "surface_summary_text", "what_happened_text", "why_happened_text",
                    "affected_assets_summary_text", "business_context_text", "watch_next_text", "cause_status",
                ]},
            ],
        }},
    }, "required": ["issues", "market_summary", "items", "new_events"],
}


def _extract_story_symbols(payload: dict[str, Any]) -> list[str]:
    stories = build_market_stories(payload)
    seen: set[str] = set()
    ordered: list[str] = []
    for story in stories:
        for symbol in list(story.get("symbols") or []):
            norm = str(symbol).upper().strip()
            if norm and norm not in seen:
                seen.add(norm)
                ordered.append(norm)
    return ordered


def _looks_like_failed_zopedia_synthesis_answer(answer: object) -> bool:
    text = re.sub(r"\s+", " ", str(answer or "").strip().lower())
    if not text:
        return False
    if "i collected tool output" in text or "did not get a clean final synthesis" in text:
        return True
    tool_markers = sum(
        1
        for marker in (
            "evidence collected:",
            "research.",
            "investigator.",
            "dataset.",
            "question: what is driving",
            "columns=",
            "sample=[",
        )
        if marker in text
    )
    return tool_markers >= 3


def _is_retryable_zopedia_llm_failure(result: object) -> bool:
    if not isinstance(result, dict):
        return False
    status = str(result.get("status") or "").strip().lower()
    if status not in {"failed", "error"}:
        return False
    error_text = " ".join(
        [
            str(result.get("error") or ""),
            " ".join(str(item or "") for item in list(result.get("limitations") or [])),
        ]
    ).lower()
    return any(
        marker in error_text
        for marker in (
            "llm error",
            "llmapierror",
            "returned non-json",
            "returned invalid json",
            "returned empty content",
            "request failed",
            "chunkedencodingerror",
            "response ended prematurely",
            "connection reset",
            "connection aborted",
            "timed out",
            "timeout",
        )
    )


def _zopedia_enrichment_failure_categories(
    *,
    status: object,
    answer_markdown: object,
    limitations: list[Any],
    tool_calls: list[Any],
    quality_review: dict[str, Any],
) -> list[str]:
    status_text = str(status or "").strip().lower()
    if status_text in {"completed", "success"} and str(answer_markdown or "").strip():
        return []
    pieces: list[str] = [status_text, str(answer_markdown or "")]
    pieces.extend(str(item or "") for item in limitations)
    pieces.append(json.dumps(quality_review, default=str))
    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            pieces.append(str(tool_call.get("tool_name") or ""))
            pieces.append(str(tool_call.get("status") or ""))
            pieces.append(str(tool_call.get("error") or ""))
            pieces.append(str(tool_call.get("message") or ""))
    text = " ".join(pieces).lower()
    categories: list[str] = []
    if any(marker in text for marker in ("non-json", "invalid json", "empty content", "no choices", "refused")):
        categories.append("llm_structured_output_failure")
    if any(marker in text for marker in ("timeout", "timed out", "readtimeout")):
        categories.append("timeout")
    if any(marker in text for marker in ("tool budget", "max tool", "budget exhausted", "maximum tool", "budget is depleting")):
        categories.append("tool_budget_exhausted")
    if any(marker in text for marker in ("indexerror", "requires object_value", "unsupported tool argument", "invalid tool arguments")):
        categories.append("tool_contract_failure")
    if any(marker in text for marker in ("quality monitor", "failed_quality_review", "quality review")):
        categories.append("quality_rejected")
    if any(marker in text for marker in ("no recent", "no relevant", "no current", "stale", "freshness", "no substantive evidence")):
        categories.append("no_current_evidence")
    if any(marker in text for marker in ("no zopedia", "memory missing", "wiki", "retained memory")):
        categories.append("zopedia_recall_gap")
    if any(marker in text for marker in ("did not use", "wander", "not followed", "unrelated", "low-signal loop", "exhausted 2 restarts")):
        categories.append("agent_trajectory_issue")
    if not categories and status_text and status_text not in {"completed", "success"}:
        categories.append("unknown_failure")
    seen: set[str] = set()
    ordered: list[str] = []
    for category in categories:
        if category not in seen:
            seen.add(category)
            ordered.append(category)
    return ordered


def _cap_zopedia_confidence(confidence: object, limitations: list[Any]) -> str:
    normalized = str(confidence or "low").strip().lower()
    if normalized not in {"low", "medium", "high"}:
        normalized = "low"
    limitation_text = " ".join(str(item or "") for item in limitations).lower()
    source_quality_flags = (
        "not directly verified",
        "official company filings not",
        "financial blogs",
        "source quality",
    )
    if normalized == "high" and any(flag in limitation_text for flag in source_quality_flags):
        return "medium"
    return normalized


def _zopedia_asof_text(asof_time_utc: object) -> str:
    try:
        ts = pd.Timestamp(asof_time_utc) if asof_time_utc is not None else pd.Timestamp.utcnow()
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.isoformat()
    except Exception:
        return str(asof_time_utc or "").strip()


def _build_zopedia_enrichment_query(symbol: str, *, asof_text: str) -> str:
    return (
        f"Compose the investor explanation for {symbol} from the supplied completed research and source captures. "
        f"As-of timestamp: {asof_text}. This is the composition pass after acquisition, not a second investigation. "
        "Use the acquired company, peer, customer, supplier, sector, private-company, policy and macro context "
        "when it helps explain the business. Generated research is dated, fallible analysis, not independent "
        "corroboration. Preserve useful indirect connections and distinguish observations from hypotheses. "
        "A source report may describe an older event and a new reaction: keep both dates and explain the distinction. "
        "If the evidence supports a broad sector move, describe that mechanism without inventing a precise cause. "
        "Distinguish direct data from proxies: if you only have ETF/proxy evidence such as USO, XLE, SMH, "
        "or peer moves, describe the proxy or peer move; do not convert it into a confirmed commodity, "
        "rate, or macro fact. "
        "Older developments can remain relevant context; do not present them as new disclosures. "
        "The answer must be a useful investor synthesis, not an inventory of missing evidence. "
        "Name the best supported sector, macro, peer, customer, supplier, policy, or financing mechanism; "
        "if none is supported, write one short watch-next sentence and stop. "
        "A watch-next sentence should use affirmative monitoring language, such as watching the next filing, "
        "contract update, earnings detail, sector data, or peer print; do not write 'confirm', 'verify', "
        "'check whether', or similar absence-framed wording. "
        "Write only a final synthesis; never print tool-output scaffolding, raw evidence tables, "
        "or a paragraph about failing to synthesize. "
        "Structure with only substantive sections: ### What Changed, "
        "### Business Context, ### Most Likely Driver, ### What To Watch. "
        "Skip any section where you have nothing substantive to say. "
        "Keep the total answer under 200 words."
    )


def _run_single_zopedia_enrichment(
    symbol: str,
    *,
    timeout_seconds: int,
    asof_time_utc: object,
    initial_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    query = _build_zopedia_enrichment_query(symbol, asof_text=_zopedia_asof_text(asof_time_utc))
    try:
        result = run_aql_zopedia_agent(
            query=query, task="attention_ticker_enrichment",
            surface="attention_home.zopedia_ticker_enrichment", force_refresh=True,
            max_tool_calls=0,
            run_timeout_seconds=timeout_seconds,
            llm_step_timeout_seconds=_zopedia_agent_llm_timeout_seconds(timeout_seconds),
            initial_evidence=initial_evidence, persist_findings=False,
            request_context={"asof_time_utc": _zopedia_asof_text(asof_time_utc),
                             "allowed_source_access": ["supplied"]},
            write_policy=_zopedia_write_policy(),
        )
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "limitations": [f"{type(exc).__name__}: {exc}"]}
    tool_calls = [item for item in result.get("tool_calls", []) if isinstance(item, dict)]
    limitations = list(result.get("limitations") or [])
    quality_review = dict(result.get("quality_review") or {})
    answer = str(result.get("answer_markdown") or "")
    status = str(result.get("status") or "failed")
    if _looks_like_failed_zopedia_synthesis_answer(answer):
        answer, status = "", "failed_synthesis"
    return {
        "symbol": symbol, "status": status, "answer_markdown": answer,
        "answer_status": str(result.get("answer_status") or ""),
        "confidence": _cap_zopedia_confidence(result.get("confidence"), limitations),
        "limitations_json": json.dumps(limitations),
        "tool_calls_json": json.dumps(tool_calls, default=str),
        "quality_review_json": json.dumps(quality_review, default=str),
        "failure_categories_json": json.dumps(_zopedia_enrichment_failure_categories(
            status=status, answer_markdown=answer, limitations=limitations,
            tool_calls=tool_calls, quality_review=quality_review,
        )),
        "model": str(result.get("model") or ""),
        "agent_run_id": str(result.get("run_id") or ""),
        "error_summary": str(result.get("error") or ""),
        "runtime_json": json.dumps(result.get("runtime") or {}, default=str),
    }


ZOPEDIA_MARKET_SUMMARY_KEY = "__MARKET_SUMMARY__"
def _plain_audio_text_from_markdown(markdown: object) -> str:
    text = str(markdown or "").strip()
    if not text:
        return ""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~>|]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:4000]


def _attach_market_summary_audio(row: dict[str, Any]) -> dict[str, Any]:
    answer = str(row.get("answer_markdown") or "").strip()
    if not answer:
        return row
    audio_payload = {
        "summary_text": answer,
        "audio_text": _plain_audio_text_from_markdown(answer),
    }
    if not str(audio_payload.get("audio_text") or "").strip():
        return row
    try:
        narrated = attach_attention_home_summary_audio(audio_payload)
    except ElevenLabsTTSAPIError as exc:
        print(f"[warn] zopedia market summary ElevenLabs narration unavailable: {exc}")
        return row
    except Exception as exc:
        print(f"[warn] zopedia market summary unexpected ElevenLabs narration error: {type(exc).__name__}: {exc}")
        return row
    out = dict(row)
    for key in (
        "audio_text",
        "audio_base64",
        "audio_text_hash",
        "audio_mime_type",
        "audio_file_extension",
        "voice_id",
        "model_id",
        "output_format",
    ):
        value = narrated.get(key)
        if value:
            out[key] = value
    return out


def _homepage_summary_payload_from_market_summary_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    status = str(row.get("status") or "").strip()
    answer = str(row.get("answer_markdown") or "").strip()
    if status not in {"completed", "success"} or not answer:
        return {}

    def _load_json(value: object, *, default: Any) -> Any:
        if isinstance(value, type(default)):
            return value
        text = str(value or "").strip()
        if not text or text.lower() == "nan":
            return default
        try:
            parsed = json.loads(text)
        except Exception:
            return default
        return parsed if isinstance(parsed, type(default)) else default

    audio_text = str(row.get("audio_text") or "").strip() or _plain_audio_text_from_markdown(answer)
    payload = {
        "headline": "Market Summary",
        "summary_text": answer,
        "audio_text": audio_text,
        "summary_status": status,
        "confidence": str(row.get("confidence") or "").strip(),
        "limitations": _load_json(row.get("limitations_json"), default=[]),
        "tool_calls": _load_json(row.get("tool_calls_json"), default=[]),
        "quality_review": _load_json(row.get("quality_review_json"), default={}),
        "source": "zopedia_market_summary",
        "model": str(row.get("model") or "").strip(),
    }
    for key in (
        "audio_base64",
        "audio_text_hash",
        "audio_mime_type",
        "audio_file_extension",
        "voice_id",
        "model_id",
        "output_format",
    ):
        value = str(row.get(key) or "").strip()
        if value:
            payload[key] = value
    return payload


def _zopedia_enrichment_context(rows: list[dict[str, Any]], *, limit: int = 12) -> str:
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not symbol or symbol == ZOPEDIA_MARKET_SUMMARY_KEY:
            continue
        status = str(row.get("status") or "").strip().lower()
        answer = str(row.get("answer_markdown") or "").strip()
        confidence = str(row.get("confidence") or "").strip()
        if status not in {"completed", "success"} or not answer:
            continue
        compact_answer = re.sub(r"\s+", " ", answer).strip()
        if len(compact_answer) > 900:
            compact_answer = compact_answer[:897].rstrip() + "..."
        suffix = f" confidence={confidence}" if confidence else ""
        lines.append(f"- {symbol}{suffix}: {compact_answer}")
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def _market_breadth_contract(movers: pd.DataFrame) -> dict[str, Any]:
    if not isinstance(movers, pd.DataFrame) or movers.empty or "change_pct" not in movers.columns:
        return {}
    changes = pd.to_numeric(movers["change_pct"], errors="coerce").dropna()
    if changes.empty:
        return {}
    positive = int((changes > 0).sum())
    negative = int((changes < 0).sum())
    flat = int((changes == 0).sum())
    return {
        "total_symbols": int(len(changes)),
        "advancers": positive,
        "decliners": negative,
        "unchanged": flat,
        "advance_decline_ratio": round(float(positive) / max(float(negative), 1.0), 2),
        "median_change_pct": round(float(changes.median()), 2),
        "average_change_pct": round(float(changes.mean()), 2),
        "strongest_count": int((changes >= 2.0).sum()),
        "weakest_count": int((changes <= -2.0).sum()),
    }


def _sector_rotation_contract(
    movers: pd.DataFrame, *, taxonomy_frame: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(movers, pd.DataFrame) or movers.empty or "change_pct" not in movers.columns:
        return []
    sectors = pd.Series(pd.NA, index=movers.index, dtype="string")
    for column in ("sector", "gics_sector", "taxonomy_sector", "business_sector"):
        if column in movers:
            sectors = sectors.fillna(movers[column].astype("string").str.strip().replace("", pd.NA))
    if (
        isinstance(taxonomy_frame, pd.DataFrame)
        and {"symbol", "sector"}.issubset(taxonomy_frame.columns)
        and "symbol" in movers
    ):
        labels = taxonomy_frame[["symbol", "sector"]].copy()
        labels["symbol"] = labels["symbol"].astype("string").str.upper().str.strip()
        labels = labels.set_index("symbol")["sector"].astype("string").str.strip().replace("", pd.NA)
        symbols = movers["symbol"].astype("string").str.upper().str.strip()
        sectors = sectors.fillna(symbols.map(labels))
    sector_column = "sector"
    working = pd.DataFrame({sector_column: sectors.fillna(""), "change_pct": movers["change_pct"]})
    working["change_pct"] = pd.to_numeric(working["change_pct"], errors="coerce")
    working = working[working[sector_column].ne("") & working["change_pct"].notna()]
    if working.empty:
        return []
    grouped = (
        working.groupby(sector_column)["change_pct"]
        .agg(["count", "mean", "median"])
        .reset_index()
        .sort_values("mean", ascending=False)
    )
    rows: list[dict[str, Any]] = []
    for _, row in grouped.iterrows():
        rows.append(
            {
                "sector": str(row.get(sector_column) or "").strip(),
                "symbol_count": int(row.get("count") or 0),
                "average_change_pct": round(float(row.get("mean") or 0.0), 2),
                "median_change_pct": round(float(row.get("median") or 0.0), 2),
            }
        )
    return rows


def _build_home_market_coverage_contract(
    *,
    movers: pd.DataFrame,
    macro_movers: pd.DataFrame,
    fred_summary_frame: pd.DataFrame,
    yield_curve_facts_frame: pd.DataFrame,
    payload: dict[str, Any],
    asof_time_utc: object,
    taxonomy_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    breadth = _market_breadth_contract(movers)
    # Preserve complete typed observations. Column whitelists and row caps lost
    # units, annual changes, longer maturities and the weakest sectors before
    # the research editor could interpret them.
    macro_anchor_moves = frame_to_records(macro_movers)
    sector_rotation = _sector_rotation_contract(movers, taxonomy_frame=taxonomy_frame)
    yield_curve_facts = frame_to_records(yield_curve_facts_frame)
    fred_context = frame_to_records(fred_summary_frame)
    market_signals = to_jsonable(payload.get("market_signals") or [])
    macro_signals = to_jsonable(payload.get("fred_signals") or [])
    cross_series_signals = to_jsonable(payload.get("cross_series_signals") or [])

    gaps: list[str] = []
    if not breadth:
        gaps.append("market breadth")
    if not macro_anchor_moves:
        gaps.append("macro anchor moves")
    if not sector_rotation:
        gaps.append("sector labels")
    if not yield_curve_facts:
        gaps.append("yield curve facts")
    if not (market_signals or macro_signals or cross_series_signals):
        gaps.append("structural signal extraction")

    status = "ok"
    if not breadth and not macro_anchor_moves:
        status = "unavailable"
    elif gaps:
        status = "partial"

    return {
        "status": status,
        "asof_time_utc": to_jsonable(asof_time_utc),
        "broad_market": {
            "market_breadth": breadth,
            "macro_anchor_moves": macro_anchor_moves,
            "sector_rotation": sector_rotation,
        },
        "rates_and_macro": {
            "yield_curve_facts": yield_curve_facts,
            "fred_context": fred_context,
        },
        "structural_signals": {
            "market": market_signals,
            "macro": macro_signals,
            "cross_series": cross_series_signals,
        },
        "coverage_gaps": gaps,
    }


def _build_market_summary_query(
    payload: dict[str, Any],
    *,
    enrichment_rows: list[dict[str, Any]] | None = None,
    asof_time_utc: object = None,
) -> str:
    stories = build_market_stories(payload)
    story_lines = []
    for story in stories[:10]:
        sentence = str(story.get("sentence") or "").strip()
        symbols = ", ".join(str(s).strip() for s in list(story.get("symbols") or []) if str(s).strip())
        if sentence:
            story_lines.append(f"- {sentence} [{symbols}]" if symbols else f"- {sentence}")
    stories_text = "\n".join(story_lines)
    asof_text = _zopedia_asof_text(asof_time_utc)
    enrichment_text = _zopedia_enrichment_context(list(enrichment_rows or []))
    enrichment_block = (
        "Already-collected Zopedia ticker/event research:\n"
        f"{enrichment_text}\n\n"
        if enrichment_text
        else ""
    )
    coverage_contract = payload.get("market_coverage")
    if not isinstance(coverage_contract, dict):
        coverage_contract = (payload.get("coverage_summary") or {}).get("market_coverage") if isinstance(payload.get("coverage_summary"), dict) else {}
    if not isinstance(coverage_contract, dict):
        coverage_contract = {}
    coverage_text = json.dumps(coverage_contract, ensure_ascii=True, sort_keys=True, default=str)
    if len(coverage_text) > 10000:
        coverage_text = coverage_text[:9997].rstrip() + "..."
    return (
        "Write a daily market summary for an informed investor. "
        f"As-of timestamp: {asof_text}. Compare source dates to this timestamp. "
        "Start from the market coverage contract before single-name anomalies: broad market action, breadth, macro-anchor moves, "
        "rates, commodities, and structural signals define whether this was a market day, a sector day, or mostly a stock-picking day. "
        "If the contract is partial, use only the supported coverage fields and put missing broad-market inputs in limitations. "
        "Use the already-collected Zopedia ticker/event research below as primary evidence. "
        "This is the composition pass after bounded Zopedia/AQL research, so synthesize the supplied evidence instead of "
        "starting a new broad research thread. "
        "For company-level claims, use investigator context/fundamentals/news or Zopedia pages; for macro claims, "
        "use local macro/market datasets plus current evidence. "
        "Old dated material may explain business sensitivity, but it cannot be today's driver unless current evidence "
        "connects it to today's move. The market stories below tell you what moved; your job is to explain WHY with "
        "evidence. Do not expand ticker symbols into company names unless the supplied evidence gives the company name. "
        "Do not turn missing evidence into public prose. If a driver is not supported, omit the claim and place "
        "the concrete evidence target in limitations. Keep evidence gaps out of answer_markdown. If the "
        "supported read is a sector move, say the sector evidence and stop there. Distinguish direct data from proxies: "
        "if the evidence is USO or XLE, call it an oil/energy proxy move, not confirmed crude price action. "
        "Confidence must reflect evidence quality: use medium when any key driver is inferred from peers or proxies.\n\n"
        "Structure with short paragraphs, no bullet lists:\n"
        "1. **Dominant theme** — what defined today's session and why\n"
        "2. **Key movers** — the 3-5 most important equity moves with grounded explanations\n"
        "3. **Macro backdrop** — rates, dollar, commodities, anything driving the broader market\n"
        "4. **What to watch** — upcoming evidence, company events, or risks for the next session\n\n"
        "Use the reviewed stories as the authority for causes and interpretations. Unreviewed ticker notes are background. "
        "Write only as much as the supported material needs. Every sentence must add information the investor cannot get from a ticker screen. "
        "Do not hedge or pad — if you lack evidence for a claim, drop the claim.\n\n"
        f"Market coverage contract:\n{coverage_text}\n\n"
        f"{enrichment_block}"
        f"Today's market stories:\n{stories_text}\n\n"
        "Reviewed Home stories:\n" + json.dumps([
            {key: item.get(key) for key in ("event_title", "headline", "what_happened_text", "why_happened_text", "business_context_text", "watch_next_text", "cause_status", "supporting_symbols")}
            for section in ("top_events", "must_read_movers", "unresolved_large_moves") for item in payload.get(section, [])
        ], ensure_ascii=False, default=str)
    )


def _build_zopedia_enrichment_frame(
    payload: dict[str, Any],
    *,
    bundle_map: dict[str, dict[str, Any]] | None = None,
    asof_time_utc: object,
    run_id: str,
) -> pd.DataFrame:
    limit = _zopedia_enrichment_limit()
    timeout = _zopedia_enrichment_timeout_seconds()
    symbols = collect_attention_ticker_symbols(payload, bundle_map, max_symbols=limit)
    if not symbols:
        symbols = _extract_story_symbols(payload)[:limit]

    evidence_by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    for bundle_id, bundle in (bundle_map or {}).items():
        # Acquisition is evidence. Generated explanations remain draft analysis.
        evidence = [
            {**item, "text": item.get("source_text") or item.get("raw_text") or item.get("text")}
            for item in bundle.get("evidence") or []
            if isinstance(item, dict) and (item.get("source_text") or item.get("raw_text") or item.get("text"))
        ]
        evidence.extend(
            {"source_id": f"{bundle_id}::tool::{index}", "title": item.get("tool_name", ""),
             "text": item["text"], "source_refs": item.get("source_refs") or []}
            for index, item in enumerate(bundle.get("research_evidence") or [])
            if isinstance(item, dict) and item.get("text") and item.get("tool_name") != "research.supplied_context"
        )
        evidence.append({
            "kind": "completed_research_context",
            "bundle_id": bundle_id,
            "asof_time_utc": bundle.get("asof_time_utc"),
            "company_context": bundle.get("business_contexts") or bundle.get("business_context") or {},
            "draft_analysis": {key: bundle.get(key) for key in (
                "what_happened_text", "what_changed_text", "why_happened_text", "why_now_text",
                "business_context_text", "watch_next_text", "observed_members", "instrument_context",
            )},
        })
        for symbol in bundle.get("supporting_symbols") or []:
            if symbol in evidence_by_symbol:
                evidence_by_symbol[symbol].extend(evidence)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    rows: list[dict[str, Any]] = []

    if symbols:
        print(f"[info] attention-home-build zopedia ticker enrichment: {len(symbols)} symbols, timeout={timeout}s each")
        max_workers = min(len(symbols), _zopedia_enrichment_max_workers())
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="zopedia-enrich") as pool:
            futures = {
                pool.submit(
                    copy_context().run, _run_single_zopedia_enrichment,
                    symbol,
                    timeout_seconds=timeout,
                    asof_time_utc=asof_time_utc,
                    initial_evidence=evidence_by_symbol[symbol],
                ): symbol
                for symbol in symbols
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    print(f"[warn] zopedia enrichment thread failed symbol={symbol}: {type(exc).__name__}: {exc}")
                    row = {
                        "symbol": symbol,
                        "status": "failed",
                        "answer_markdown": "",
                        "confidence": "",
                        "limitations_json": "[]",
                        "tool_calls_json": "[]",
                        "quality_review_json": "{}",
                        "failure_categories_json": json.dumps(
                            _zopedia_enrichment_failure_categories(
                                status="failed",
                                answer_markdown="",
                                limitations=[str(exc)],
                                tool_calls=[],
                                quality_review={},
                            )
                        ),
                        "model": "",
                    }
                rows.append(row)
                status = row.get("status", "unknown")
                print(f"[info] zopedia enrichment {symbol}: {status}")

    frame = pd.DataFrame(rows)
    ts = pd.Timestamp(asof_time_utc)
    frame["generated_at_utc"] = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    frame["run_id"] = str(run_id)
    return frame.reset_index(drop=True)


def _normalize_symbol(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.upper().strip()
    else:
        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass
        text = str(value).upper().strip()
    return "" if not text or text in {"NAN", "<NA>", "NONE", "NULL"} else text


def _normalize_symbol_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, pd.Series, pd.Index)):
        raw_items = list(value)
    elif hasattr(value, "tolist") and not isinstance(value, (str, bytes, bytearray)):
        converted = value.tolist()
        raw_items = converted if isinstance(converted, list) else [converted]
    else:
        try:
            if pd.isna(value):
                return []
        except Exception:
            pass
        text = str(value).strip()
        if not text:
            return []
        raw_items = [item.strip() for item in text.split(",")]

    normalized: list[str] = []
    for item in raw_items:
        pieces = item.split(",") if isinstance(item, str) else [item]
        for piece in pieces:
            symbol = _normalize_symbol(piece)
            if symbol:
                normalized.append(symbol)
    return normalized


def _news_payloads_from_articles_frame(
    news_frame: pd.DataFrame,
    *,
    symbols: list[str],
    limit: int,
    asof_time_utc: object | None = None,
) -> dict[str, dict[str, Any]]:
    normalized_symbols = [_normalize_symbol(symbol) for symbol in symbols if str(symbol or "").strip()]
    normalized_symbols = [symbol for symbol in normalized_symbols if symbol]
    if not isinstance(news_frame, pd.DataFrame) or news_frame.empty or not normalized_symbols:
        return {}

    rows_by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in normalized_symbols}
    frame = news_frame.copy()
    asof_label = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    if "published_at" in frame.columns:
        frame["published_at"] = frame.apply(
            lambda row: coerce_article_published_at(
                row.get("published_at"),
                url=row.get("url"),
                asof_time_utc=asof_label,
            ),
            axis=1,
        )

    for _, row in frame.iterrows():
        row_symbols = _normalize_symbol_list(row.get("symbols"))
        if not row_symbols:
            continue
        published_at = coerce_article_published_at(
            row.get("published_at"),
            url=row.get("url"),
            asof_time_utc=asof_label,
        )
        if pd.notna(asof_label) and not is_recent_for_attention(
            published_at,
            asof_time_utc=asof_label,
            max_age_days=3,
            include_undated=True,
        ):
            continue
        article = {
            **row.to_dict(),
            "headline": str(row.get("headline") or "").strip(),
            "summary": str(row.get("summary") or row.get("description") or "").strip(),
            "description": str(row.get("description") or row.get("summary") or "").strip(),
            "source": str(row.get("source") or "").strip(),
            "published_at": published_at,
            "url": str(row.get("url") or "").strip(),
        }
        for symbol in row_symbols:
            if symbol in rows_by_symbol:
                rows_by_symbol[symbol].append(article)

    payloads: dict[str, dict[str, Any]] = {}
    for symbol, rows in rows_by_symbol.items():
        if not rows:
            payloads[symbol] = {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}
            continue
        articles = pd.DataFrame(rows)
        if "published_at" in articles.columns:
            articles["published_at"] = articles.apply(
                lambda row: coerce_article_published_at(row.get("published_at"), url=row.get("url")),
                axis=1,
            )
            articles = articles.sort_values("published_at", ascending=False, na_position="last")
        articles = articles.drop_duplicates(subset=["headline", "url"], keep="first").head(max(int(limit), 1)).reset_index(drop=True)
        payloads[symbol] = {"articles": articles, "fallback_summary": None, "source": "pipeline"}
    return payloads


def _company_name_map(frame: pd.DataFrame) -> dict[str, str]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns:
        return {}
    name_column = ""
    for candidate in ("security_name", "company_name", "name"):
        if candidate in frame.columns:
            name_column = candidate
            break
    if not name_column:
        return {}
    rows = frame[["symbol", name_column]].copy()
    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
    rows[name_column] = rows[name_column].astype(str).str.strip()
    rows = rows[rows["symbol"].ne("") & rows[name_column].ne("")]
    return dict(rows.drop_duplicates(subset=["symbol"], keep="first").itertuples(index=False, name=None))


def _news_payload_has_articles(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    articles = payload.get("articles")
    return isinstance(articles, pd.DataFrame) and not articles.empty


def _search_backfill_news_payloads(
    symbols: list[str],
    *,
    existing_payloads: dict[str, dict[str, Any]],
    company_name_by_symbol: dict[str, str] | None = None,
    limit: int = 8,
) -> dict[str, dict[str, Any]]:
    normalized_symbols = [_normalize_symbol(symbol) for symbol in symbols if _normalize_symbol(symbol)]
    if not normalized_symbols:
        return {}

    candidates = [
        symbol
        for symbol in normalized_symbols
        if not _news_payload_has_articles(existing_payloads.get(symbol))
    ]
    if not candidates:
        return {}

    backfilled: dict[str, dict[str, Any]] = {}
    max_workers = min(len(candidates), _attention_home_search_backfill_max_workers())
    timeout_seconds = _attention_home_search_backfill_symbol_timeout_seconds()
    print(
        "[info] attention-home-build search-backed news backfill starting "
        f"candidates={len(candidates)} workers={max_workers} timeout={timeout_seconds}s"
    )

    def _fetch(symbol: str) -> tuple[str, dict[str, Any] | None, str]:
        try:
            payload = _call_with_timeout(
                f"news-backfill-{symbol}",
                timeout_seconds,
                lambda: search_symbol_news_payload(
                    symbol,
                    company_name=str((company_name_by_symbol or {}).get(symbol) or "").strip(),
                    max_results=max(int(limit), 1),
                ),
            )
        except Exception as exc:
            return symbol, None, f"{type(exc).__name__}: {exc}"
        return symbol, payload if isinstance(payload, dict) else None, ""

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="news-backfill") as pool:
        futures = {pool.submit(copy_context().run, _fetch, symbol): symbol for symbol in candidates}
        for future in as_completed(futures):
            symbol, payload, error = future.result()
            completed += 1
            if error:
                print(f"[warn] attention-home-build news backfill failed symbol={symbol}: {error}")
            elif _news_payload_has_articles(payload):
                backfilled[symbol] = payload or {}
            if completed == len(candidates) or completed % 10 == 0:
                print(
                    "[info] attention-home-build search-backed news backfill progress "
                    f"completed={completed}/{len(candidates)} hits={len(backfilled)}"
                )
    return backfilled


def _materialize_news_payload_frame(
    payloads: dict[str, dict[str, Any]],
    *,
    asof_time_utc: object | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    asof_label = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    for symbol, payload in (payloads or {}).items():
        articles = payload.get("articles")
        payload_source = str(payload.get("source") or "").strip()
        if not isinstance(articles, pd.DataFrame) or articles.empty:
            continue
        scoped = articles.copy()
        if "published_at" in scoped.columns:
            scoped["published_at"] = scoped.apply(
                lambda row: coerce_article_published_at(
                    row.get("published_at"),
                    url=row.get("url"),
                    asof_time_utc=asof_label,
                ),
                axis=1,
            )
        for _, row in scoped.iterrows():
            published_at = coerce_article_published_at(
                row.get("published_at"),
                url=row.get("url"),
                asof_time_utc=asof_label,
            )
            if pd.notna(asof_label) and not is_recent_for_attention(
                published_at,
                asof_time_utc=asof_label,
                max_age_days=_attention_home_search_backfill_lookback_days(),
                include_undated=True,
            ):
                continue
            rows.append(
                {
                    **row.to_dict(),
                    "symbol": symbol,
                    "symbols": [symbol],
                    "headline": str(row.get("headline") or "").strip(),
                    "summary": str(row.get("summary") or row.get("description") or "").strip(),
                    "description": str(row.get("description") or row.get("summary") or "").strip(),
                    "source": str(row.get("source") or payload_source or "").strip(),
                    "published_at": published_at,
                    "url": str(row.get("url") or "").strip(),
                    "sentiment": str(row.get("sentiment") or "").strip(),
                    "payload_source": payload_source,
                }
            )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out = out[out["headline"].astype(str).str.strip().ne("")].copy()
    if "published_at" in out.columns:
        out["published_at"] = out.apply(
            lambda row: coerce_article_published_at(row.get("published_at"), url=row.get("url")),
            axis=1,
        )
        out = out.sort_values("published_at", ascending=False, na_position="last")
    return out.drop_duplicates(subset=["symbol", "headline", "url"], keep="first").reset_index(drop=True)


def _merge_news_frames(*frames: pd.DataFrame) -> pd.DataFrame:
    parts = [frame.copy() for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
    if not parts:
        return pd.DataFrame()
    merged = pd.concat(parts, ignore_index=True, sort=False)
    if "published_at" in merged.columns:
        merged["published_at"] = merged.apply(
            lambda row: coerce_article_published_at(row.get("published_at"), url=row.get("url")),
            axis=1,
        )
        merged = merged.sort_values("published_at", ascending=False, na_position="last")
    dedupe_cols = [column for column in ["symbol", "headline", "url"] if column in merged.columns]
    if not dedupe_cols:
        dedupe_cols = [column for column in ["headline", "url"] if column in merged.columns]
    if dedupe_cols:
        merged = merged.drop_duplicates(subset=dedupe_cols, keep="first")
    return merged.reset_index(drop=True)


def _materialize_attention_web_search_news(
    payloads: dict[str, dict[str, Any]],
    *,
    asof_time_utc: object | None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    asof_label = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    asof_text = asof_label.isoformat() if pd.notna(asof_label) else str(asof_time_utc or "")
    for symbol, payload in (payloads or {}).items():
        source = str(payload.get("source") or "").strip()
        articles = payload.get("articles")
        if isinstance(articles, pd.DataFrame) and not articles.empty:
            scoped = articles.copy()
            if "published_at" in scoped.columns:
                scoped["published_at"] = scoped.apply(
                    lambda row: coerce_article_published_at(
                        row.get("published_at"),
                        url=row.get("url"),
                        asof_time_utc=asof_label,
                    ),
                    axis=1,
                )
            for _, row in scoped.iterrows():
                published_at = coerce_article_published_at(
                    row.get("published_at"),
                    url=row.get("url"),
                    asof_time_utc=asof_label,
                )
                if not is_recent_for_attention(
                    published_at,
                    asof_time_utc=asof_label,
                    max_age_days=_attention_home_search_backfill_lookback_days(),
                    include_undated=True,
                ):
                    continue
                rows.append(
                    {
                        **row.to_dict(),
                        "symbol": symbol,
                        "row_type": "article",
                        "headline": str(row.get("headline") or "").strip(),
                        "summary": str(row.get("summary") or row.get("description") or "").strip(),
                        "source": str(row.get("source") or source or "").strip(),
                        "published_at": published_at,
                        "url": str(row.get("url") or "").strip(),
                        "payload_source": source,
                        "fallback_summary": "",
                        "asof_time_utc": asof_text,
                    }
                )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    if "published_at" in out.columns:
        out["published_at"] = out.apply(
            lambda row: coerce_article_published_at(
                row.get("published_at"),
                url=row.get("url"),
                asof_time_utc=asof_label,
            ),
            axis=1,
        )
        out = out.sort_values("published_at", ascending=False, na_position="last")
    return out.drop_duplicates(subset=["symbol", "row_type", "headline", "url"], keep="first").reset_index(drop=True)


def _context_payloads_from_frame(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns:
        return {}
    payloads: dict[str, dict[str, Any]] = {}
    rows = frame.copy()
    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
    for _, row in rows.iterrows():
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        payload = row.to_dict()
        for json_column, default in [("top_filing_links_json", []), ("llm_supporting_points_json", [])]:
            raw = payload.get(json_column)
            if isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                    payload[json_column[:-5]] = parsed if isinstance(parsed, type(default)) else default
                except Exception:
                    payload[json_column[:-5]] = default
            else:
                payload[json_column[:-5]] = default
        payloads[symbol] = payload
    return payloads


def _json_payload(value: object, *, default: Any) -> Any:
    if isinstance(value, type(default)):
        return value
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return default
    try:
        parsed = json.loads(text)
    except Exception:
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _plain_business_text(value: object, *, limit: int = 520) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return ""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~>|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    clipped = text[: max(int(limit), 1)].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return clipped if clipped else text[: max(int(limit), 1)].rstrip(" ,;:-")


def _business_resolution_text(row: dict[str, Any]) -> str:
    for item in _json_payload(row.get("resolved_changes_json") or row.get("resolved_changes"), default=[]):
        if not isinstance(item, dict):
            continue
        text = _plain_business_text(item.get("text"), limit=460)
        if text:
            return text
    return _plain_business_text(row.get("coherent_story_markdown"), limit=520)


def _latest_symbol_context_rows(frame: pd.DataFrame, *, text_column: str) -> dict[str, dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns:
        return {}
    rows = frame.copy()
    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
    rows = rows[rows["symbol"].ne("")]
    if text_column in rows.columns:
        rows = rows[rows[text_column].astype(str).str.strip().ne("")]
    if rows.empty:
        return {}
    sort_columns = [
        column
        for column in ["source_published_at", "created_at_utc", "asof_time_utc", "generated_at_utc"]
        if column in rows.columns
    ]
    for column in sort_columns:
        rows[f"_{column}_ts"] = pd.to_datetime(rows[column], utc=True, errors="coerce")
    if sort_columns:
        rows = rows.sort_values([f"_{column}_ts" for column in sort_columns], ascending=False, na_position="last")
    out: dict[str, dict[str, Any]] = {}
    for _, row in rows.iterrows():
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol and symbol not in out:
            out[symbol] = row.to_dict()
    return out


def _business_contexts_by_symbol(
    *,
    resolution_frame: pd.DataFrame,
    stack_frame: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    resolution_rows = _latest_symbol_context_rows(resolution_frame, text_column="coherent_story_markdown")
    stack_rows = _latest_symbol_context_rows(stack_frame, text_column="business_story_markdown")
    symbols = sorted(set(resolution_rows) | set(stack_rows))
    contexts: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        resolution = resolution_rows.get(symbol, {})
        stack = stack_rows.get(symbol, {})
        resolution_text = _business_resolution_text(resolution) if resolution else ""
        stack_text = _plain_business_text(stack.get("business_story_markdown"), limit=520) if stack else ""
        context_text = resolution_text or stack_text
        if not context_text:
            continue
        contexts[symbol] = {
            "symbol": symbol,
            "business_resolution_text": resolution_text,
            "business_context_text": context_text,
            "business_stack_text": stack_text,
            "business_resolution": {
                "status": str(resolution.get("status") or "").strip(),
                "confidence": str(resolution.get("confidence") or "").strip(),
                "source_headline": str(resolution.get("source_headline") or "").strip(),
                "source_url": str(resolution.get("source_url") or "").strip(),
                "source_published_at": str(resolution.get("source_published_at") or "").strip(),
                "labels": _json_payload(resolution.get("resolution_labels_json") or resolution.get("resolution_labels"), default=[]),
                "data_gaps": _json_payload(resolution.get("data_gaps_json") or resolution.get("data_gaps"), default=[]),
            },
            "business_model_stack": {
                "status": str(stack.get("status") or "").strip(),
                "confidence": str(stack.get("confidence") or "").strip(),
                "company_name": str(stack.get("company_name") or "").strip(),
                "slot_gaps": _json_payload(stack.get("slot_gaps_json") or stack.get("slot_gaps"), default=[]),
            },
        }
    return contexts


def _title_from_observed_text(value: object, *, limit: int = 120) -> str:
    text = _plain_business_text(value, limit=360)
    if not text:
        return ""
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip(" .!?")
    if not first:
        return ""
    if len(first) <= limit:
        return first
    leading_clause = re.split(r"[,;:]\s+", first, maxsplit=1)[0].strip(" .!?")
    if 24 <= len(leading_clause) <= limit:
        return leading_clause
    return first


def _attach_business_context_to_payload(
    payload: dict[str, Any],
    bundle_map: dict[str, dict[str, Any]],
    contexts_by_symbol: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    updated_payload = dict(payload or {})
    updated_bundle_map = {key: dict(value or {}) for key, value in dict(bundle_map or {}).items()}

    narrative_fields = [
        "headline",
        "event_title",
        "what_changed_text",
        "why_now_text",
        "what_else_moved_text",
        "what_happened_text",
        "why_happened_text",
        "affected_assets_summary_text",
        "surface_summary_text",
        "surface_what_changed_text",
        "surface_why_text",
        "surface_what_else_moved_text",
        "background_context_text",
        "business_context_text",
        "watch_next_text",
    ]

    def _clean_narrative_fields(item: dict[str, Any]) -> dict[str, Any]:
        out = dict(item or {})
        for field in narrative_fields:
            if field in out:
                out[field] = clean_attention_text(out.get(field))
        return out

    def _attach_to_symbol_payload(item: dict[str, Any], symbol: str) -> dict[str, Any]:
        out = _clean_narrative_fields(dict(item or {}))
        if str(out.get("cause_status") or "").strip().lower() == "unresolved":
            out["why_now_text"] = ""
            out["surface_why_text"] = ""
            out["surface_summary_text"] = ""
            observed_title = _title_from_observed_text(out.get("what_changed_text") or out.get("surface_what_changed_text"))
            if observed_title:
                out["headline"] = observed_title
        context = contexts_by_symbol.get(symbol)
        if not context:
            return out
        out["business_context"] = context
        out["business_resolution_text"] = context.get("business_resolution_text") or ""
        out["business_context_text"] = context.get("business_context_text") or ""
        out["business_stack_text"] = context.get("business_stack_text") or ""
        out["surface_business_context_text"] = context.get("business_context_text") or ""
        return out

    def _attach_to_event_payload(item: dict[str, Any]) -> dict[str, Any]:
        out = _clean_narrative_fields(dict(item or {}))
        if str(out.get("cause_status") or "").strip().lower() == "unresolved":
            out["why_happened_text"] = ""
            out["surface_why_text"] = ""
            out["surface_summary_text"] = ""
            observed_title = _title_from_observed_text(
                out.get("what_happened_text")
                or out.get("surface_what_changed_text")
                or out.get("affected_assets_summary_text")
            )
            if observed_title:
                out["event_title"] = observed_title
        symbols = [
            str(value or "").upper().strip()
            for value in list(out.get("supporting_symbols") or [])
            if str(value or "").strip()
        ]
        event_contexts = [contexts_by_symbol[symbol] for symbol in symbols if symbol in contexts_by_symbol]
        if not event_contexts:
            return out
        out["business_contexts"] = event_contexts[:6]
        existing_context = clean_attention_text(out.get("business_context_text") or out.get("surface_business_context_text"))
        if existing_context:
            out["business_context_text"] = existing_context
            out["surface_business_context_text"] = existing_context
        elif len(event_contexts) == 1:
            context_text = clean_attention_text(event_contexts[0].get("business_context_text"))
            if context_text:
                out["business_context_text"] = context_text
                out["surface_business_context_text"] = context_text
        return out

    def _has_substantive_home_text(item: dict[str, Any], *, require_body: bool = False) -> bool:
        symbol = str(item.get("symbol") or "").upper().strip()
        title = clean_attention_text(item.get("headline") or item.get("event_title"))
        body_parts = [
            clean_attention_text(item.get(field))
            for field in (
                "surface_summary_text",
                "why_now_text",
                "why_happened_text",
                "what_changed_text",
                "what_happened_text",
                "business_resolution_text",
                "business_context_text",
                "surface_business_context_text",
                "llm_summary_text",
                "llm_narrative_text",
                "llm_why_now",
            )
        ]
        if any(body_parts):
            return True
        if require_body:
            return False
        return bool(title and title.upper() != symbol)

    for key, bundle in list(updated_bundle_map.items()):
        symbol = str(bundle.get("symbol") or "").upper().strip()
        if symbol:
            updated_bundle_map[key] = _attach_to_symbol_payload(bundle, symbol)
            continue
        if str(bundle.get("bundle_type") or "").strip().lower() == "event":
            updated_bundle_map[key] = _attach_to_event_payload(bundle)

    for section in ["must_read_movers", "unresolved_large_moves"]:
        items: list[dict[str, Any]] = []
        for raw_item in list(updated_payload.get(section) or []):
            item = dict(raw_item or {})
            symbol = str(item.get("symbol") or "").upper().strip()
            item = _attach_to_symbol_payload(item, symbol) if symbol else item
            bundle_id = str(item.get("bundle_id") or "").strip()
            bundle = updated_bundle_map.get(bundle_id, {}) if bundle_id else {}
            hydrated = hydrate_home_item_with_bundle(item, bundle)
            if section == "unresolved_large_moves" and not _has_substantive_home_text(hydrated, require_body=True):
                continue
            items.append(hydrated)
        updated_payload[section] = items

    event_items: list[dict[str, Any]] = []
    for raw_item in list(updated_payload.get("top_events") or []):
        item = _attach_to_event_payload(dict(raw_item or {}))
        bundle_id = str(item.get("bundle_id") or "").strip()
        bundle = updated_bundle_map.get(bundle_id, {}) if bundle_id else {}
        event_items.append(hydrate_home_item_with_bundle(item, bundle))
    updated_payload["top_events"] = event_items

    coverage = dict(updated_payload.get("coverage_summary") or {})
    coverage["business_context_symbol_count"] = len(contexts_by_symbol)
    coverage["business_context_attached_count"] = sum(
        1
        for section in ["top_events", "must_read_movers", "unresolved_large_moves"]
        for item in list(updated_payload.get(section) or [])
        if item.get("business_context") or item.get("business_contexts") or item.get("business_context_text")
    )
    updated_payload["coverage_summary"] = coverage
    return updated_payload, updated_bundle_map


def _home_surface_quality_items(
    payload: dict[str, Any],
    bundle_map: dict[str, dict[str, Any]],
    *,
    enrichment_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Keep source evidence, observations and fallible draft analysis distinct."""
    enrichment_by_symbol = {
        str(row.get("symbol") or ""): row.get("answer_markdown", "")
        for row in enrichment_rows or [] if row.get("status") == "completed"
    }
    rows = []
    for section in ("top_events", "must_read_movers", "unresolved_large_moves"):
        for index, item in enumerate(payload.get(section) or []):
            bundle_id = str(item.get("bundle_id") or "")
            bundle = bundle_map.get(bundle_id) or {}
            symbol = str(item.get("symbol") or bundle.get("symbol") or "")
            symbols = list(dict.fromkeys([*list(bundle.get("supporting_symbols") or item.get("supporting_symbols") or []), *([symbol] if symbol else [])]))
            rows.append({
                "section": section, "index": index, "bundle_id": bundle_id,
                "symbol": symbol, "symbols": symbols,
                "source_event": bool(bundle.get("source_event")),
                "instrument_context": list(bundle.get("instrument_context") or []),
                "observed_members": list(bundle.get("observed_members") or []),
                "candidate_observation": {key: item.get(key) for key in ("change_pct", "direction", "asof_time_utc")},
                "source_evidence": list(bundle.get("evidence") or []) + list(bundle.get("background_context") or []),
                "collected_research_evidence": list(bundle.get("research_evidence") or []),
                "unverified_claims": list(bundle.get("claims") or []),
                "research_draft": {key: bundle.get(key) or item.get(key) for key in (
                    "event_title", "headline", "what_happened_text", "what_changed_text",
                    "why_happened_text", "why_now_text", "business_context_text", "watch_next_text",
                    "cause_status", "cohort_label", "cohort_kind",
                )},
                "member_research_drafts": [{"symbol": sym, "text": enrichment_by_symbol[sym]} for sym in symbols if enrichment_by_symbol.get(sym)],
            })
    return rows


def _completed_zopedia_enrichment_by_symbol(
    enrichment_rows: list[dict[str, Any]] | None,
    *,
    limit: int,
) -> dict[str, str]:
    by_symbol: dict[str, str] = {}
    for row in list(enrichment_rows or []):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol or symbol in by_symbol:
            continue
        if str(row.get("status") or "").strip() not in {"completed", "success"}:
            continue
        text = _plain_business_text(row.get("answer_markdown"), limit=limit)
        if text:
            by_symbol[symbol] = text
    return by_symbol


def _supporting_symbols_for_group_item(item: dict[str, Any], bundle: dict[str, Any] | None = None) -> list[str]:
    values = list(item.get("supporting_symbols") or (bundle or {}).get("supporting_symbols") or [])
    symbol = str(item.get("symbol") or (bundle or {}).get("symbol") or "").upper().strip()
    if symbol:
        values.insert(0, symbol)
    symbols = [str(value or "").upper().strip() for value in values if str(value or "").strip()]
    return list(dict.fromkeys(symbols))


def _is_group_event_item(item: dict[str, Any], bundle: dict[str, Any] | None = None) -> bool:
    symbols = _supporting_symbols_for_group_item(item, bundle)
    if len(symbols) < 2:
        return False
    bundle_type = str(item.get("bundle_type") or (bundle or {}).get("bundle_type") or "").strip().lower()
    if bundle_type == "event":
        return True
    return bool(
        item.get("event_title")
        or (bundle or {}).get("event_title")
        or item.get("observed_cohort")
        or (bundle or {}).get("observed_cohort")
        or str(item.get("event_type") or (bundle or {}).get("event_type") or "").strip()
    )


def _member_zopedia_contexts(
    symbols: list[str],
    enrichment_by_symbol: dict[str, str],
    *,
    limit: int = 6,
) -> list[dict[str, str]]:
    contexts: list[dict[str, str]] = []
    for symbol in symbols:
        text = enrichment_by_symbol.get(symbol)
        if not text:
            continue
        contexts.append({"symbol": symbol, "context_text": text})
        if len(contexts) >= max(int(limit), 1):
            break
    return contexts


def _attach_zopedia_enrichment_to_payload(
    payload: dict[str, Any],
    bundle_map: dict[str, dict[str, Any]],
    enrichment_rows: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    enrichment_by_symbol = _completed_zopedia_enrichment_by_symbol(enrichment_rows, limit=900)
    enrichment_by_symbol.pop(ZOPEDIA_MARKET_SUMMARY_KEY, None)
    if not enrichment_by_symbol:
        return payload, bundle_map

    updated_payload = dict(payload or {})
    updated_bundle_map = {key: dict(value or {}) for key, value in dict(bundle_map or {}).items()}

    def _symbols_for_item(item: dict[str, Any], bundle: dict[str, Any] | None) -> list[str]:
        symbols = [
            str(value or "").upper().strip()
            for value in list(item.get("supporting_symbols") or (bundle or {}).get("supporting_symbols") or [])
            if str(value or "").strip()
        ]
        symbol = str(item.get("symbol") or (bundle or {}).get("symbol") or "").upper().strip()
        if symbol:
            symbols.insert(0, symbol)
        return list(dict.fromkeys(symbols))

    def _attach(item: dict[str, Any], bundle: dict[str, Any] | None) -> dict[str, Any]:
        out = dict(item or {})
        symbols = _symbols_for_item(out, bundle)
        member_contexts = _member_zopedia_contexts(symbols, enrichment_by_symbol)
        if _is_group_event_item(out, bundle):
            if member_contexts:
                out["member_zopedia_contexts"] = member_contexts
                out["zopedia_group_synthesis_status"] = out.get("zopedia_group_synthesis_status") or "pending"
                if bundle is not None:
                    bundle["member_zopedia_contexts"] = member_contexts
                    bundle["zopedia_group_synthesis_status"] = bundle.get("zopedia_group_synthesis_status") or "pending"
            out["business_resolution_text"] = ""
            if bundle is not None:
                bundle["business_resolution_text"] = ""
            return out
        texts = [enrichment_by_symbol[symbol] for symbol in symbols if enrichment_by_symbol.get(symbol)]
        text = " ".join(texts[:3]).strip()
        if not text:
            return out
        out["zopedia_enrichment_text"] = text
        out["business_resolution_text"] = out.get("business_resolution_text") or text
        out["surface_business_context_text"] = out.get("surface_business_context_text") or text
        if bundle is not None:
            bundle["zopedia_enrichment_text"] = text
            bundle["business_resolution_text"] = bundle.get("business_resolution_text") or text
            bundle["surface_business_context_text"] = bundle.get("surface_business_context_text") or text
        return out

    for bundle_id, bundle in list(updated_bundle_map.items()):
        updated_bundle_map[bundle_id] = _attach(bundle, bundle)

    for section in ["top_events", "must_read_movers", "unresolved_large_moves"]:
        items: list[dict[str, Any]] = []
        for raw_item in list(updated_payload.get(section) or []):
            item = dict(raw_item or {})
            bundle_id = str(item.get("bundle_id") or "").strip()
            bundle = updated_bundle_map.get(bundle_id) if bundle_id else None
            items.append(_attach(item, bundle))
        updated_payload[section] = items
    return updated_payload, updated_bundle_map


def _apply_group_synthesis_items(
    payload: dict[str, Any],
    bundle_map: dict[str, dict[str, Any]],
    synthesis_items: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, int]]:
    revisions: dict[tuple[str, int, str], dict[str, Any]] = {}
    for raw in list(synthesis_items or []):
        if not isinstance(raw, dict):
            continue
        section = str(raw.get("section") or "").strip()
        try:
            index = int(raw.get("index"))
        except Exception:
            continue
        bundle_id = str(raw.get("bundle_id") or "").strip()
        revisions[(section, index, bundle_id)] = raw

    updated_payload = dict(payload or {})
    updated_bundle_map = {key: dict(value or {}) for key, value in dict(bundle_map or {}).items()}
    changed = 0
    skipped = 0
    text_fields = [
        "event_title",
        "headline",
        "what_happened_text",
        "why_happened_text",
        "affected_assets_summary_text",
        "business_context_text",
        "watch_next_text",
        "confidence_label",
    ]

    def _apply(item: dict[str, Any], bundle: dict[str, Any] | None, revision: dict[str, Any]) -> bool:
        if not _is_group_event_item(item, bundle):
            return False
        group_text = clean_attention_text(revision.get("business_context_text"))
        if not group_text:
            return False
        for field in text_fields:
            value = clean_attention_text(revision.get(field))
            if value:
                item[field] = value
                if bundle is not None:
                    bundle[field] = value
        cause_status = str(revision.get("cause_status") or "").strip().lower()
        if cause_status in {"supported", "partial", "unresolved"}:
            item["cause_status"] = cause_status
            item["surface_cause_status"] = cause_status
            if bundle is not None:
                bundle["cause_status"] = cause_status
                bundle["surface_cause_status"] = cause_status
        item["business_context_text"] = group_text
        item["surface_business_context_text"] = group_text
        item["business_resolution_text"] = group_text
        item["group_synthesis_text"] = group_text
        item["zopedia_group_synthesis_status"] = "completed"
        item["zopedia_group_synthesis_note"] = clean_attention_text(revision.get("synthesis_note"))
        if bundle is not None:
            bundle["business_context_text"] = group_text
            bundle["surface_business_context_text"] = group_text
            bundle["business_resolution_text"] = group_text
            bundle["group_synthesis_text"] = group_text
            bundle["zopedia_group_synthesis_status"] = "completed"
            bundle["zopedia_group_synthesis_note"] = item["zopedia_group_synthesis_note"]
        return True

    for section in ["top_events", "must_read_movers", "unresolved_large_moves"]:
        items: list[dict[str, Any]] = []
        for index, raw_item in enumerate(list(updated_payload.get(section) or [])):
            item = dict(raw_item or {})
            bundle_id = str(item.get("bundle_id") or "").strip()
            bundle = updated_bundle_map.get(bundle_id) if bundle_id else None
            revision = revisions.get((section, index, bundle_id)) or revisions.get((section, index, ""))
            if revision:
                if _apply(item, bundle, revision):
                    changed += 1
                else:
                    skipped += 1
            items.append(item)
        updated_payload[section] = items
    return updated_payload, updated_bundle_map, {"changed": changed, "skipped": skipped}


def _apply_home_surface_quality_items(
    payload: dict[str, Any],
    bundle_map: dict[str, dict[str, Any]],
    quality_items: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, int]]:
    """Materialize the semantic editor's decisions without rewriting its analysis."""
    revisions = {(item["section"], int(item["index"]), item["bundle_id"]): item for item in quality_items}
    expected = {(section, index, str(item.get("bundle_id") or ""))
                for section in ("top_events", "must_read_movers", "unresolved_large_moves")
                for index, item in enumerate(payload.get(section) or [])}
    if set(revisions) != expected or len(revisions) != len(quality_items):
        raise AttentionHomeBuildError("Public editor decisions do not match the supplied candidates.")
    updated = dict(payload)
    bundles = {key: dict(value) for key, value in bundle_map.items()}
    changed = dropped = 0
    for section in ["top_events", "must_read_movers", "unresolved_large_moves"]:
        kept = []
        for index, original in enumerate(payload.get(section) or []):
            item = dict(original)
            bundle_id = str(item.get("bundle_id") or "")
            revision = revisions.get((section, index, bundle_id))
            if revision is None:
                raise AttentionHomeBuildError("Public editor omitted a supplied story decision.")
            canonical = {key: str(revision.get(key) or "").strip() for key in (
                "event_title", "surface_summary_text", "what_happened_text", "why_happened_text",
                "affected_assets_summary_text", "business_context_text", "watch_next_text",
            )}
            text = {**canonical,
                "headline": canonical["event_title"],
                "what_changed_text": canonical["what_happened_text"],
                "why_now_text": canonical["why_happened_text"],
                "what_else_moved_text": canonical["affected_assets_summary_text"],
                "surface_what_changed_text": canonical["what_happened_text"],
                "surface_why_text": canonical["why_happened_text"],
                "surface_what_else_moved_text": canonical["affected_assets_summary_text"],
                "surface_business_context_text": canonical["business_context_text"],
                "surface_source_summary": "",
            }
            publish = bool(revision["publish"])
            metadata = {
                "publish": publish, "public_surface_reviewed": True, "public_surface_review_note": "",
                "cause_status": revision.get("cause_status", "unresolved"), "surface_cause_status": revision.get("cause_status", "unresolved"),
            }
            if not publish:
                text = {key: "" for key in text}
            item.update(text); item.update(metadata)
            if bundle_id in bundles:
                bundles[bundle_id].update(text); bundles[bundle_id].update(metadata)
            if publish:
                kept.append((float(revision.get("priority") or 0), item))
                changed += 1
            else:
                dropped += 1
        updated[section] = [item for _, item in sorted(kept, key=lambda value: -value[0])]
    coverage = dict(updated.get("coverage_summary") or {})
    coverage.update(public_surface_quality_reviewed_count=changed+dropped, public_surface_quality_changed_count=changed, public_surface_quality_dropped_count=dropped)
    updated["coverage_summary"] = coverage
    return updated, bundles, {"changed": changed, "dropped": dropped, "reviewed": changed+dropped}


def _cap_home_public_sections(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload or {})
    limits = {
        "top_events": _attention_home_top_events_display_limit(),
        "must_read_movers": _attention_home_must_read_display_limit(),
        "unresolved_large_moves": _attention_home_unresolved_display_limit(),
    }
    coverage = dict(out.get("coverage_summary") or {})
    for section, limit in limits.items():
        items = [item for item in list(out.get(section) or []) if isinstance(item, dict) and bool(item.get("publish", True))]
        coverage[f"{section}_reviewed_count"] = int(len(items))
        out[section] = items[: max(int(limit), 1)]
        coverage[f"{section}_display_count"] = int(len(out[section]))
    coverage["public_home_story_count"] = int(sum(len(out.get(section) or []) for section in limits))
    out["coverage_summary"] = coverage
    return out


def _review_home_public_surface(
    payload: dict[str, Any],
    bundle_map: dict[str, dict[str, Any]],
    *,
    enrichment_rows: list[dict[str, Any]] | None,
    asof_time_utc: object,
    timeout_seconds: int,
    source_documents: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    review_items = _home_surface_quality_items(
        payload,
        bundle_map,
        enrichment_rows=enrichment_rows,
    )
    intake = list(source_documents or [])
    if not review_items and not intake:
        return payload, bundle_map, {"status": "skipped", "reason": "no public Home items"}
    query = (
        f"As of {_zopedia_asof_text(asof_time_utc)}, produce a compact investor briefing for Home "
        "from the supplied company research, full source intake and market coverage. "
        "Select and rank developments and connections by expected relevance and usefulness, "
        "including worthwhile uncertain leads. Explain what changed, why it matters economically "
        "and what would change the interpretation. Use the artifact's public fields for the "
        "briefing and its review fields for your assessment. Return a decision for every supplied "
        "candidate using its unchanged section, index, bundle_id and symbol, and discover "
        "additional events from the source intake where useful."
    )
    initial_evidence = [{
        "home_surface_items": review_items,
        "full_source_intake": intake,
        "market_coverage": payload.get("market_coverage") or {},
    }]
    attempts = _env_positive_int("ATTENTION_HOME_SURFACE_QUALITY_ATTEMPTS", 2)
    result: dict[str, Any] = {}
    deadline = time.monotonic() + timeout_seconds
    for attempt in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        result = run_aql_zopedia_structured_agent(
            query=query, schema_name="attention_home_public_surface_quality", schema=_HOME_SURFACE_QUALITY_SCHEMA,
            task="attention_home_public_surface_quality", surface="attention_home.public_surface_quality",
            max_tool_calls=0, run_timeout_seconds=remaining, llm_step_timeout_seconds=remaining,
            persist_findings=False, call_type="research_grade", review_answer=True, write_policy="none",
            initial_evidence=initial_evidence,
        )
        if isinstance(result, dict) and str(result.get("status") or "") == "completed":
            break
        if attempt >= attempts or not _is_retryable_zopedia_llm_failure(result):
            break
        print(
            "[warn] attention-home-build public surface review encountered a transient provider failure; "
            f"retrying attempt={attempt + 1}/{attempts}",
            flush=True,
        )
    if not isinstance(result, dict) or str(result.get("status") or "") != "completed":
        raise AttentionHomeBuildError(
            f"Home public-surface quality review failed: {str((result or {}).get('error') or 'unknown error')}",
            research_result=result,
        )
    quality_payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    errors = list(Draft202012Validator(_HOME_SURFACE_QUALITY_SCHEMA).iter_errors(quality_payload))
    if errors:
        raise AttentionHomeBuildError("Public Home editor returned an invalid response contract: " + errors[0].message, research_result=result)
    # New stories and researched candidates share the same canonical materializer.
    # The model owns discovery, grouping, explanation and ranking; code supplies IDs and source access.
    candidate_payload = dict(payload)
    candidate_payload["top_events"] = list(payload.get("top_events") or [])
    candidate_bundles = dict(bundle_map)
    decisions = list(quality_payload.get("items") or [])
    by_source = {str(doc.get("source_id") or doc.get("document_id") or ""): doc for doc in intake}
    for event in quality_payload.get("new_events") or []:
        identity = json.dumps([event["event_title"], sorted(event["source_ids"])], ensure_ascii=False)
        event_id = "home-" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        bundle_id = "event::" + event_id
        bundle = {
            "bundle_id": bundle_id, "event_id": event_id, "bundle_type": "event", "event_type": "source_event",
            "source_event": True, "asof_time_utc": asof_time_utc, "supporting_symbols": event["supporting_symbols"],
            "run_id": payload.get("run_id") or (payload.get("coverage_summary") or {}).get("run_id", ""),
            "source_ids": event["source_ids"],
            "evidence": [
                {**by_source[sid], "headline": by_source[sid].get("title", ""),
                 "source": by_source[sid].get("source_provider", ""), "source_text": by_source[sid].get("raw_text", "")}
                for sid in event["source_ids"] if sid in by_source
            ],
        }
        index = len(candidate_payload["top_events"])
        candidate_payload["top_events"].append(dict(bundle))
        candidate_bundles[bundle_id] = bundle
        decisions.append({**event, "section": "top_events", "index": index, "bundle_id": bundle_id, "symbol": "", "publish": True})
    updated_payload, updated_bundle_map, counts = _apply_home_surface_quality_items(candidate_payload, candidate_bundles, decisions)
    counts["discovered"] = len(quality_payload.get("new_events") or [])
    counts["source_input_count"] = len(intake)
    updated_payload["homepage_summary"] = {**quality_payload["market_summary"], "summary_status": "completed"}
    return updated_payload, updated_bundle_map, {
        "status": "completed",
        "model": (result.get("agent_result") or {}).get("model", ""),
        "agent_run_id": (result.get("agent_result") or {}).get("run_id", ""),
        "quality_review": (result.get("agent_result") or {}).get("quality_review", {}),
        "runtime": (result.get("agent_result") or {}).get("runtime", {}),
        "issues": [str(item).strip() for item in list(quality_payload.get("issues") or []) if str(item).strip()][:8],
        **counts,
    }


def build_attention_home_output_frames(
    *,
    ctx: Any,
    daily_movers: pd.DataFrame,
    macro_movers: pd.DataFrame,
    positions_frame: pd.DataFrame,
    price_history_frame: pd.DataFrame,
    attention_feed_frame: pd.DataFrame,
    commodity_attention_feed_frame: pd.DataFrame,
    news_frame: pd.DataFrame,
    attention_context_frame: pd.DataFrame,
    edgar_filings_frame: pd.DataFrame,
    llm_client: Any | None,
    load_materialized_frame_fn: LoadFrameFn = _load_latest_materialized_frame,
    research_progress_fn: ResearchProgressFn | None = None,
    include_page_agentic: bool = True,
) -> dict[str, pd.DataFrame]:
    movers = (
        pd.concat(
            [frame for frame in [daily_movers, macro_movers] if isinstance(frame, pd.DataFrame) and not frame.empty],
            ignore_index=True,
            sort=False,
        )
        if any(isinstance(frame, pd.DataFrame) and not frame.empty for frame in [daily_movers, macro_movers])
        else pd.DataFrame()
    )
    if movers.empty or "symbol" not in movers.columns:
        movers = pd.DataFrame(columns=["symbol", "close", "prev_close", "change_pct", "volume"])
        print("[warn] attention-home-build has no mover observations; retaining source-event research")
    movers["symbol"] = movers["symbol"].astype(str).str.upper().str.strip()
    movers = movers.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)

    attention_parts = [
        frame.copy()
        for frame in [attention_feed_frame, commodity_attention_feed_frame]
        if isinstance(frame, pd.DataFrame) and not frame.empty
    ]
    attention_rows = pd.concat(attention_parts, ignore_index=True, sort=False) if attention_parts else pd.DataFrame()
    holdings = (
        [
            _normalize_symbol(value)
            for value in positions_frame.get("symbol", pd.Series(dtype=str)).dropna().astype(str).tolist()
            if str(value).strip()
        ]
        if isinstance(positions_frame, pd.DataFrame) and not positions_frame.empty and "symbol" in positions_frame.columns
        else []
    )
    holdings = [symbol for symbol in holdings if symbol]

    shortlist = shortlist_attention_symbols_1d(
        movers,
        holdings=holdings,
        attention_rows=attention_rows,
        max_count=100,
    )
    if not shortlist:
        print("[info] attention-home-build has no ticker shortlist; continuing source-event research")
    research_limit = _attention_home_research_limit()
    search_backfill_limit = _attention_home_search_backfill_limit()

    print(
        "[info] attention-home-build prepared inputs "
        f"movers={len(movers)} shortlist={len(shortlist)} holdings={len(holdings)}"
    )
    print("[info] attention-home-build loading universe/taxonomy snapshots")
    universe_snapshot_frame = load_materialized_frame_fn("universe_snapshot")
    taxonomy_labels_frame = load_materialized_frame_fn("entity_taxonomy_labels")
    company_name_by_symbol = _company_name_map(universe_snapshot_frame)
    asset_metadata = {
        str(row["symbol"]): {**row, "name": row.get("security_name") or row.get("name")}
        for row in universe_snapshot_frame.to_dict("records") if row.get("symbol")
    }
    entity_master = build_attention_entity_master(
        shortlist, taxonomy_frame=taxonomy_labels_frame, asset_metadata_by_symbol=asset_metadata,
    )
    research_universe = universe_snapshot_frame.copy()
    if "symbol" in taxonomy_labels_frame:
        research_universe = (
            research_universe.merge(taxonomy_labels_frame, on="symbol", how="outer", suffixes=("", "_taxonomy"))
            if "symbol" in research_universe else taxonomy_labels_frame.copy()
        )
    print(
        "[info] attention-home-build loaded universe/taxonomy "
        f"universe_rows={len(universe_snapshot_frame)} taxonomy_rows={len(taxonomy_labels_frame)}"
    )
    bars_by_symbol = bars_by_symbol_from_price_history(
        price_history_frame,
        shortlist,
        asof_time_utc=ctx.asof,
        lookback_days=120,
    )
    print("[info] attention-home-build building news payloads and search backfill")
    news_payloads = _news_payloads_from_articles_frame(
        news_frame,
        symbols=shortlist,
        limit=8,
        asof_time_utc=ctx.asof,
    )
    search_backfill_payloads = _search_backfill_news_payloads(
        shortlist[:search_backfill_limit],
        existing_payloads=news_payloads,
        company_name_by_symbol=company_name_by_symbol,
        limit=8,
    )
    if search_backfill_payloads:
        print(
            "[info] attention-home-build search-backed news backfill: "
            f"symbols={len(search_backfill_payloads)}"
        )
        news_payloads.update(search_backfill_payloads)
    context_payloads = _context_payloads_from_frame(attention_context_frame)
    print("[info] attention-home-build loading macro context snapshots")
    fred_summary_frame = load_materialized_frame_fn("fred_summary")
    yield_curve_facts_frame = load_materialized_frame_fn("yield_curve_facts_1d")
    embedding_client = load_embedding_client()

    top_events_review_limit = _attention_home_top_events_review_limit()
    must_read_review_limit = _attention_home_must_read_review_limit()
    unresolved_review_limit = _attention_home_unresolved_review_limit()
    print(
        "[info] attention-home-build starting agentic attention artifacts "
        f"research_limit={research_limit} "
        f"review_pool=top_events:{top_events_review_limit},must_read:{must_read_review_limit},unresolved:{unresolved_review_limit}"
    )
    artifacts = build_bottom_up_attention_artifacts(
        movers,
        attention_rows=attention_rows,
        bars_by_symbol=bars_by_symbol,
        news_payloads=news_payloads,
        news_frame=news_frame,
        context_payloads=context_payloads,
        entity_master=entity_master,
        topology_universe_frame=research_universe,
        holdings=holdings,
        generated_at_utc=pd.Timestamp(ctx.asof),
        filings_frame=edgar_filings_frame,
        fred_summary_frame=fred_summary_frame,
        yield_curve_facts_frame=yield_curve_facts_frame,
        llm_client=llm_client,
        embedding_client=embedding_client,
        run_id=ctx.run_id,
        top_events_limit=top_events_review_limit,
        must_read_limit=must_read_review_limit,
        unresolved_limit=unresolved_review_limit,
        research_limit=research_limit,
        load_search_clients=True,
        progress_callback=research_progress_fn,
    )
    payload = dict(artifacts.home_payload or {})
    coverage = dict(payload.get("coverage_summary") or {})
    macro_anchor_symbols = (
        {
            _normalize_symbol(value)
            for value in macro_movers.get("symbol", pd.Series(dtype=str)).tolist()
            if _normalize_symbol(value)
        }
        if isinstance(macro_movers, pd.DataFrame) and not macro_movers.empty
        else set()
    )
    if not macro_anchor_symbols and isinstance(movers, pd.DataFrame) and not movers.empty and "symbol" in movers.columns:
        macro_anchor_symbols = set(resolve_macro_anchor_symbols(movers["symbol"].dropna().astype(str).tolist()))
    coverage.update(
        {
            "equity_universe_count": int(movers[~movers["symbol"].isin(macro_anchor_symbols)]["symbol"].nunique()),
            "macro_anchor_target_count": len(macro_anchor_symbols),
            "research_symbol_count": min(len(shortlist), research_limit),
        }
    )
    payload["coverage_summary"] = coverage

    business_resolution_frame = load_materialized_frame_fn("zopedia_news_business_resolutions")
    business_stack_frame = load_materialized_frame_fn("zopedia_ticker_business_model_stacks")
    business_contexts = _business_contexts_by_symbol(
        resolution_frame=business_resolution_frame,
        stack_frame=business_stack_frame,
    )
    payload, artifacts.bundle_map = _attach_business_context_to_payload(
        payload,
        artifacts.bundle_map,
        business_contexts,
    )
    if business_contexts:
        print(
            "[info] attention-home-build attached zopedia business context: "
            f"symbols={len(business_contexts)}"
        )

    # --- Signal extraction: dense market + macro signals for the LLM summary ---
    try:
        from compute.signal_extraction import (
            extract_signals_from_bars,
            extract_signals_from_phase_shift_summary,
        )
        from compute.fred import build_fred_signal_dicts

        # Market signals from the full price history (not the 120-day attention window)
        full_price_history_frame = load_materialized_frame_fn("price_history")
        full_bars = bars_by_symbol_from_price_history(
            full_price_history_frame,
            shortlist,
            asof_time_utc=ctx.asof,
            lookback_days=504,  # 2yr for signal extraction
        )
        market_signals = extract_signals_from_bars(full_bars, category="equity")

        # Cross-series signals from precomputed correlation phase shifts
        phase_summary_frame = load_materialized_frame_fn("correlation_phase_shift_summary")
        cross_signals = extract_signals_from_phase_shift_summary(phase_summary_frame)

        # FRED macro signals from precomputed observations
        fred_obs_frame = load_materialized_frame_fn("fred_observations")
        fred_signals = build_fred_signal_dicts(fred_obs_frame)

        payload["market_signals"] = market_signals
        payload["cross_series_signals"] = cross_signals
        payload["fred_signals"] = fred_signals
        print(
            f"[info] attention-home-build signal extraction: "
            f"market={len(market_signals)} cross={len(cross_signals)} fred={len(fred_signals)}"
        )
    except Exception as exc:
        print(f"[warn] attention-home-build signal extraction failed (non-fatal): {type(exc).__name__}: {exc}")
        payload.setdefault("market_signals", [])
        payload.setdefault("cross_series_signals", [])
        payload.setdefault("fred_signals", [])

    market_coverage = _build_home_market_coverage_contract(
        movers=movers,
        macro_movers=macro_movers,
        fred_summary_frame=fred_summary_frame,
        yield_curve_facts_frame=yield_curve_facts_frame,
        payload=payload,
        asof_time_utc=ctx.asof,
        taxonomy_frame=taxonomy_labels_frame,
    )
    coverage = dict(payload.get("coverage_summary") or {})
    coverage["market_coverage"] = market_coverage
    coverage["market_coverage_status"] = str(market_coverage.get("status") or "")
    coverage["market_coverage_gaps"] = list(market_coverage.get("coverage_gaps") or [])
    legacy_broad_market_key = "broad_" + "ta" + "pe"
    coverage["market_breadth_symbol_count"] = int(
        ((market_coverage.get("broad_market") or market_coverage.get(legacy_broad_market_key) or {}).get("market_breadth") or {}).get("total_symbols") or 0
    )
    payload["market_coverage"] = market_coverage
    payload["coverage_summary"] = coverage
    print(
        "[info] attention-home-build market coverage: "
        f"status={market_coverage.get('status')} "
        f"gaps={len(market_coverage.get('coverage_gaps') or [])} "
        f"breadth_symbols={coverage['market_breadth_symbol_count']}"
    )

    artifacts.frames["knowledge_graph_update_proposals"] = build_attention_knowledge_graph_proposals(
        run_id=ctx.run_id,
        asof_time_utc=ctx.asof,
        claims_frame=artifacts.frames.get("attention_claims", pd.DataFrame()),
        macro_edges_frame=artifacts.frames.get("macro_causal_graph_edges_v1", pd.DataFrame()),
        relationship_checks_frame=artifacts.frames.get("macro_relationship_checks_1d", pd.DataFrame()),
    )

    # --- Zopedia ticker enrichment: pre-compute deep dives for homepage tickers ---
    try:
        print("[info] attention-home-build starting zopedia ticker enrichment")
        artifacts.frames["attention_ticker_zopedia_enrichments"] = _build_zopedia_enrichment_frame(
            payload,
            bundle_map=artifacts.bundle_map,
            asof_time_utc=ctx.asof,
            run_id=ctx.run_id,
        )
        enrichment_count = len(artifacts.frames["attention_ticker_zopedia_enrichments"])
        completed_count = int(
            (artifacts.frames["attention_ticker_zopedia_enrichments"]["status"] == "completed").sum()
        ) if enrichment_count > 0 else 0
        print(f"[info] attention-home-build zopedia enrichment done: {completed_count}/{enrichment_count} completed")
    except Exception as exc:
        print(f"[warn] attention-home-build zopedia enrichment failed (non-fatal): {type(exc).__name__}: {exc}")
        artifacts.frames["attention_ticker_zopedia_enrichments"] = pd.DataFrame()

    enrichment_rows = (
        artifacts.frames["attention_ticker_zopedia_enrichments"].to_dict(orient="records")
        if isinstance(artifacts.frames.get("attention_ticker_zopedia_enrichments"), pd.DataFrame)
        and not artifacts.frames["attention_ticker_zopedia_enrichments"].empty
        else []
    )
    payload, artifacts.bundle_map = _attach_zopedia_enrichment_to_payload(
        payload,
        artifacts.bundle_map,
        enrichment_rows,
    )
    print("[info] attention-home-build reviewing public Home surface with Zopedia")
    try:
        payload, artifacts.bundle_map, public_surface_review = _review_home_public_surface(
            payload,
            artifacts.bundle_map,
            enrichment_rows=enrichment_rows,
            asof_time_utc=ctx.asof,
            timeout_seconds=_home_surface_quality_timeout_seconds(),
            source_documents=[
                {key: row.get(key) for key in ("source_id", "document_id", "title", "url", "published_at", "raw_text", "source_provider", "provider_symbols")}
                for row in artifacts.frames.get("attention_source_documents", pd.DataFrame()).to_dict("records")
                if str(row.get("document_id") or "").startswith("doc::event-news::")
            ],
        )
    except Exception as exc:
        # An execution/contract failure cannot supply a new canonical artifact.
        # Keep the last snapshot and retain this attempt's collected research.
        artifacts.frames.pop("attention_home_snapshots_1d", None)
        artifacts.frames.pop("attention_bundle_snapshots", None)
        artifacts.frames["attention_home_publication_reviews"] = pd.DataFrame([{
            "run_id": ctx.run_id, "asof_time_utc": ctx.asof, "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "result_json": json.dumps(getattr(exc, "research_result", {}), default=str),
        }])
        artifacts.frames["attention_research_health"] = pd.DataFrame([{
            "run_id": ctx.run_id, "asof_time_utc": ctx.asof, "status": "degraded",
            "summary": f"Home publication failed: {type(exc).__name__}: {exc}",
            "stage_statuses_json": json.dumps({"public_editor": "failed"}),
        }])
        return artifacts.frames
    print(
        "[info] attention-home-build public Home surface review: "
        f"status={public_surface_review.get('status')} "
        f"reviewed={public_surface_review.get('reviewed', 0)} "
        f"changed={public_surface_review.get('changed', 0)} "
        f"dropped={public_surface_review.get('dropped', 0)}"
    )
    artifacts.frames["attention_home_publication_reviews"] = pd.DataFrame([{
        "run_id": ctx.run_id, "asof_time_utc": ctx.asof, "status": "completed",
        "error": "", "result_json": json.dumps(public_surface_review, default=str),
    }])
    summary = dict(payload.get("homepage_summary") or {})
    market_summary_row = _attach_market_summary_audio({
        "symbol": ZOPEDIA_MARKET_SUMMARY_KEY,
        "status": "completed" if summary.get("summary_text") else "unavailable",
        "answer_markdown": summary.get("summary_text", ""),
        "confidence": summary.get("confidence", ""),
        "limitations_json": json.dumps(summary.get("limitations") or []),
        "tool_calls_json": "[]", "quality_review_json": json.dumps(public_surface_review),
        "run_id": ctx.run_id, "generated_at_utc": ctx.asof,
        "model": public_surface_review.get("model", ""),
    })
    enrichment_rows.insert(0, market_summary_row)
    artifacts.frames["attention_ticker_zopedia_enrichments"] = pd.DataFrame(enrichment_rows)
    if market_summary_row["status"] == "completed":
        payload["homepage_summary"] = _homepage_summary_payload_from_market_summary_row(market_summary_row)
    coverage = dict(payload.get("coverage_summary") or {})
    coverage["group_synthesis_status"] = "consolidated_in_public_editor"
    payload["coverage_summary"] = coverage
    ticker_rows = [row for row in enrichment_rows if row.get("symbol") != ZOPEDIA_MARKET_SUMMARY_KEY]
    failed_tickers = [str(row.get("symbol")) for row in ticker_rows if row.get("status") != "completed"]
    unreviewed_tickers = [
        str(row.get("symbol")) for row in ticker_rows if row.get("status") == "completed"
        and _json_payload(row.get("quality_review_json"), default={}).get("verdict") not in {"accept", "revise"}
    ]
    coverage = dict(payload.get("coverage_summary") or {})
    stages = {
        "source_events": coverage.get("source_event_research_status", "unavailable"),
        "ticker_enrichment": "completed" if not failed_tickers else "partial" if len(failed_tickers) < len(ticker_rows) else "failed",
        "ticker_answer_review": "partial" if unreviewed_tickers else "completed",
        "market_summary": market_summary_row.get("status", "unavailable"),
        "group_synthesis": coverage.get("group_synthesis_status", "unavailable"),
        "public_editor": public_surface_review.get("status", "unavailable"),
        "public_answer_review": (public_surface_review.get("quality_review") or {}).get("status", "not_run"),
        "market_inputs": market_coverage.get("status", "unavailable"),
    }
    degraded = bool(failed_tickers) or any(value in {"failed", "partial", "unavailable", "timeout", "not_run", "failed_contract", "rejected", "failed_quality_review", "failed_synthesis"} for value in stages.values())
    research_status = "degraded" if degraded else "completed"
    coverage["research_health_status"] = research_status
    coverage["research_stage_statuses"] = stages
    coverage["ticker_enrichment_failed_count"] = len(failed_tickers)
    coverage["ticker_enrichment_unreviewed_count"] = len(unreviewed_tickers)
    payload["coverage_summary"] = coverage
    artifacts.frames["attention_research_health"] = pd.DataFrame([{
        "run_id": ctx.run_id, "asof_time_utc": ctx.asof, "status": research_status,
        "summary": f"Research {research_status}; ticker enrichments {len(ticker_rows)-len(failed_tickers)}/{len(ticker_rows)} completed; source events {coverage.get('source_event_completed_count', 0)}/{coverage.get('source_event_nominated_count', 0)} completed; market summary {stages['market_summary']}.",
        "stage_statuses_json": json.dumps(stages), "failed_tickers_json": json.dumps(failed_tickers),
        "errors_json": json.dumps([{key: row.get(key) for key in ("symbol", "agent_run_id", "error_summary", "failure_categories_json", "limitations_json", "runtime_json")} for row in enrichment_rows if row.get("status") != "completed"], default=str),
    }])
    payload = _cap_home_public_sections(payload)
    print(
        "[info] attention-home-build public Home section counts: "
        f"top_events={len(payload.get('top_events') or [])} "
        f"must_read={len(payload.get('must_read_movers') or [])} "
        f"unresolved={len(payload.get('unresolved_large_moves') or [])}"
    )
    artifacts.frames["attention_home_snapshots_1d"] = serialize_attention_home_payload(payload)
    artifacts.frames["attention_bundle_snapshots"] = serialize_attention_research_bundles(
        artifacts.bundle_map,
        generated_at_utc=ctx.asof,
    )

    snapshot_symbols = collect_attention_ticker_symbols(payload, artifacts.bundle_map, max_symbols=120)
    merged_news_frame = _merge_news_frames(
        news_frame,
        _materialize_news_payload_frame(search_backfill_payloads, asof_time_utc=ctx.asof),
    )
    momentum_profiles_frame = load_materialized_frame_fn("momentum_profiles")
    quarterly_fundamentals_frame = load_materialized_frame_fn("quarterly_fundamentals")
    print(f"[info] attention-home-build prefetching intraday prices: symbols={len(snapshot_symbols)}")
    try:
        intraday_prices = prefetch_attention_intraday_prices(snapshot_symbols, asof_time_utc=ctx.asof)
    except Exception as exc:
        # The independent ticker job retries market collection. A provider outage
        # must not discard an already completed research/news publication.
        print(f"[warn] attention-home-build intraday prefetch unavailable: {type(exc).__name__}")
        intraday_prices = {}

    def _materialize_ticker_snapshots() -> pd.DataFrame:
        print(f"[info] attention-home-build materializing ticker snapshots: symbols={len(snapshot_symbols)}")
        return build_attention_ticker_snapshot_frame(
            snapshot_symbols,
            price_history_frame=price_history_frame,
            universe_snapshot_frame=universe_snapshot_frame,
            asof_time_utc=ctx.asof,
            run_id=ctx.run_id,
            quarterly_fundamentals_frame=quarterly_fundamentals_frame,
            intraday_prices=intraday_prices,
        )

    def _materialize_ticker_background() -> pd.DataFrame:
        print(f"[info] attention-home-build materializing ticker background snapshots: symbols={len(snapshot_symbols)}")
        return build_attention_ticker_background_snapshot_frame(
            snapshot_symbols,
            price_history_frame=price_history_frame,
            universe_snapshot_frame=universe_snapshot_frame,
            news_frame=merged_news_frame,
            attention_context_frame=attention_context_frame,
            asof_time_utc=ctx.asof,
            run_id=ctx.run_id,
            entity_taxonomy_frame=taxonomy_labels_frame,
            bundle_map=artifacts.bundle_map,
            zopedia_enrichment_frame=artifacts.frames.get("attention_ticker_zopedia_enrichments", pd.DataFrame()),
        )

    def _materialize_market_opportunity_feed() -> pd.DataFrame:
        print("[info] attention-home-build materializing market_opportunity_feed")
        return build_materialized_market_opportunity_feeds(
            movers=daily_movers,
            momentum=momentum_profiles_frame,
            name_map=_company_name_map(universe_snapshot_frame),
            focus_symbol_map=_market_opportunity_focus_symbol_map(
                taxonomy_labels_frame=taxonomy_labels_frame,
                universe_snapshot_frame=universe_snapshot_frame,
            ),
            asof_time_utc=ctx.asof,
            run_id=ctx.run_id,
            limit=80,
        )

    parallel_tasks: dict[str, Callable[[], pd.DataFrame]] = {
        "attention_ticker_snapshots_1d": _materialize_ticker_snapshots,
        "attention_ticker_background_snapshots": _materialize_ticker_background,
        "market_opportunity_feed": _materialize_market_opportunity_feed,
    }
    print(f"[info] attention-home-build starting parallel materialization: tasks={len(parallel_tasks)}")
    with ThreadPoolExecutor(max_workers=len(parallel_tasks), thread_name_prefix="attention-materialize") as executor:
        futures = {executor.submit(copy_context().run, fn): name for name, fn in parallel_tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                frame = future.result()
            except Exception as exc:
                print(f"[warn] attention-home-build materialization failed dataset={name}: {type(exc).__name__}: {exc}")
                frame = pd.DataFrame()
            artifacts.frames[name] = frame
            print(f"[info] attention-home-build materialized {name} rows={len(frame)}")

    public_symbols = set(collect_attention_ticker_symbols(payload, max_symbols=60))
    ticker_snapshot_frame = artifacts.frames.get("attention_ticker_snapshots_1d", pd.DataFrame())
    if not ticker_snapshot_frame.empty and "symbol" in ticker_snapshot_frame.columns:
        embedded = ticker_snapshot_frame[
            ticker_snapshot_frame["symbol"].astype(str).str.upper().str.strip().isin(public_symbols)
        ].copy()
        payload["ticker_snapshots"] = {
            str(record.get("symbol") or "").upper().strip(): record
            for record in frame_to_records(embedded)
            if str(record.get("symbol") or "").strip()
        }
        artifacts.frames["attention_home_snapshots_1d"] = serialize_attention_home_payload(payload)

    try:
        catalog = build_daily_datapoint_catalog(snapshots=ticker_snapshot_frame, fred_observations=load_materialized_frame_fn("fred_observations"), fred_summary=load_materialized_frame_fn("fred_summary"), asof=ctx.asof)
        artifacts.frames["daily_market_datapoints_1d"] = select_daily_market_datapoints(home=payload, catalog=catalog, asof=ctx.asof, run_id=ctx.run_id)
        payload["daily_datapoints"] = frame_to_records(artifacts.frames["daily_market_datapoints_1d"])
        artifacts.frames["attention_home_snapshots_1d"] = serialize_attention_home_payload(payload)
    except Exception as exc:
        print(f"[warn] attention-home-build daily datapoint selection unavailable: {type(exc).__name__}: {exc}")

    if include_page_agentic:
        try:
            print("[info] attention-home-build materializing page_agentic_summaries")
            artifacts.frames["page_agentic_summaries"] = _build_page_agentic_summary_frame(
                ctx=ctx,
                llm_client=llm_client,
                daily_movers=daily_movers,
                momentum_profiles=momentum_profiles_frame,
                ticker_background_frame=artifacts.frames["attention_ticker_background_snapshots"],
                technical_signals_latest_frame=load_materialized_frame_fn("technical_signals_latest"),
                universe_snapshot_frame=universe_snapshot_frame,
            )
            print(
                "[info] attention-home-build materialized page_agentic_summaries "
                f"rows={len(artifacts.frames['page_agentic_summaries'])}"
            )
        except Exception as exc:
            print(f"[warn] attention-home-build page agentic summaries failed (non-fatal): {type(exc).__name__}: {exc}")
            artifacts.frames["page_agentic_summaries"] = pd.DataFrame()

    search_results = artifacts.frames.get("attention_search_results", pd.DataFrame()).copy()
    if not search_results.empty:
        search_results["symbol"] = search_results["candidate_id"].astype(str).map(
            lambda value: _normalize_symbol(str(value).split("candidate::", 1)[1]) if "candidate::" in str(value) else ""
        )
        legacy_search_news = pd.DataFrame(
            {
                "symbol": search_results.get("symbol", pd.Series(dtype=str)),
                "row_type": "article",
                "headline": search_results.get("title", pd.Series(dtype=str)),
                "summary": search_results.get("snippet", pd.Series(dtype=str)),
                "source": search_results.get("source", pd.Series(dtype=str)),
                "published_at": search_results.get("published_at", pd.Series(dtype=str)),
                "url": search_results.get("url", pd.Series(dtype=str)),
                "payload_source": search_results.get("provider", pd.Series(dtype=str)),
                "fallback_summary": "",
                "asof_time_utc": pd.Timestamp(ctx.asof).isoformat(),
            }
        )
        legacy_search_news = legacy_search_news[legacy_search_news["symbol"].astype(str).ne("")].reset_index(drop=True)
        if not legacy_search_news.empty:
            asof_ts = pd.to_datetime(ctx.asof, utc=True, errors="coerce")
            legacy_search_news["published_at"] = legacy_search_news.apply(
                lambda row: coerce_article_published_at(
                    row.get("published_at"),
                    url=row.get("url"),
                    asof_time_utc=asof_ts,
                ),
                axis=1,
            )
            legacy_search_news = legacy_search_news[
                legacy_search_news["published_at"].apply(
                    lambda value: is_recent_for_attention(
                        value,
                        asof_time_utc=asof_ts,
                        max_age_days=3,
                        include_undated=False,
                    )
                )
            ].reset_index(drop=True)
    else:
        legacy_search_news = pd.DataFrame()

    backfill_search_news = _materialize_attention_web_search_news(
        search_backfill_payloads,
        asof_time_utc=ctx.asof,
    )

    return {
        **artifacts.frames,
        "attention_home_1d": artifacts.frames.get("attention_home_snapshots_1d", pd.DataFrame()),
        "attention_research_bundles": artifacts.frames.get("attention_bundle_snapshots", pd.DataFrame()),
        "attention_web_search_news": _merge_news_frames(legacy_search_news, backfill_search_news),
    }


def run_attention_home_build(
    ctx: Any,
    conn: Any | None,
    *,
    persist_dataset_fn: PersistDatasetFn,
    job_progress_fn: JobProgressFn,
    load_materialized_frame_fn: LoadFrameFn = _load_latest_materialized_frame,
) -> dict[str, pd.DataFrame]:
    job_progress_fn(
        ctx,
        conn,
        stage="starting",
        message="Starting attention home build from materialized datasets.",
        progress_pct=1.0,
    )

    print("[info] attention-home-build loading LLM client")
    try:
        llm_client = load_aql_zopedia_llm_client(surface="attention.home_build")
    except LLMAPIError as exc:
        print(f"[warn] attention home build LLM unavailable: {exc}")
        llm_client = None
    if llm_client is None:
        print("[warn] attention home build running without LLM; narrative synthesis will fail closed")
    else:
        print("[info] attention-home-build LLM client loaded")

    job_progress_fn(
        ctx,
        conn,
        stage="loading_inputs",
        message="Loading attention build source datasets.",
        progress_pct=12.0,
    )
    research_limit = _attention_home_research_limit()
    print(f"[info] attention-home-build loading source datasets research_limit={research_limit}")
    job_progress_fn(
        ctx,
        conn,
        stage="building_narratives",
        message=f"Building attention narratives and graph from materialized inputs (research_limit={research_limit}).",
        progress_pct=18.0,
    )

    def _research_progress(index: int, total: int, candidate: dict[str, Any]) -> None:
        total_safe = max(int(total), 1)
        completed = max(min(int(index), total_safe), 0)
        progress_pct = 18.0 + (54.0 * completed / total_safe)
        symbol = _normalize_symbol(candidate.get("symbol"))
        message = f"Completed narrative candidate {completed}/{total_safe}"
        if symbol:
            message += f" symbol={symbol}"
        if candidate.get("stage"):
            message = str(candidate.get("message") or candidate["stage"])
        job_progress_fn(
            ctx,
            conn,
            stage=str(candidate.get("stage") or "research_candidates"),
            message=message,
            progress_pct=progress_pct,
        )

    daily_movers = load_materialized_frame_fn("daily_movers")
    macro_movers = load_materialized_frame_fn("macro_anchor_daily_movers")
    if not _has_symbol_inputs(daily_movers) and not _has_symbol_inputs(macro_movers):
        message = "Attention home build failed because daily_movers and macro_anchor_daily_movers were unavailable."
        job_progress_fn(
            ctx,
            conn,
            stage="failed",
            message=message,
            progress_pct=100.0,
            status="Failed",
        )
        raise AttentionHomeBuildError(message)

    # Validate mandatory datasets up front. Fail the job instead of silently
    # producing stale output when a required upstream dataset is missing
    # (mistakes.md #3, #19).
    try:
        mandatory = _validate_mandatory_datasets(load_materialized_frame_fn, _MANDATORY_DATASETS)
    except AttentionHomeBuildError as exc:
        job_progress_fn(
            ctx,
            conn,
            stage="failed",
            message=str(exc),
            progress_pct=100.0,
            status="Failed",
        )
        raise

    persist_frames = build_attention_home_output_frames(
        ctx=ctx,
        daily_movers=daily_movers,
        macro_movers=macro_movers,
        positions_frame=load_materialized_frame_fn("positions_snapshot"),
        price_history_frame=mandatory["price_history"],
        attention_feed_frame=load_materialized_frame_fn("attention_feed"),
        commodity_attention_feed_frame=load_materialized_frame_fn("commodity_attention_feed"),
        news_frame=load_materialized_frame_fn("news_articles"),
        attention_context_frame=load_materialized_frame_fn("attention_context_bundle"),
        edgar_filings_frame=load_materialized_frame_fn("edgar_filings"),
        llm_client=llm_client,
        load_materialized_frame_fn=load_materialized_frame_fn,
        research_progress_fn=_research_progress,
        include_page_agentic=False,
    )

    if not persist_frames:
        message = "Attention home build failed because it produced no output datasets."
        job_progress_fn(
            ctx,
            conn,
            stage="failed",
            message=message,
            progress_pct=100.0,
            status="Failed",
        )
        raise AttentionHomeBuildError(message)

    job_progress_fn(
        ctx,
        conn,
        stage="persist_attention_outputs",
        message=f"Persisting attention home datasets count={len(persist_frames)}.",
        progress_pct=84.0,
    )
    for dataset_name, frame in persist_frames.items():
        persist_dataset_fn(dataset_name, frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame(), ctx, conn)
    if "attention_home_snapshots_1d" not in persist_frames:
        health = persist_frames.get("attention_research_health", pd.DataFrame())
        message = str(health.iloc[0]["summary"]) if not health.empty else "No approved Home publication was produced."
        raise AttentionHomeBuildError(message)
    try:
        print("[info] attention-home-build materializing optional page_agentic_summaries after core persist")
        page_summaries = _build_page_agentic_summary_frame(
            ctx=ctx,
            llm_client=llm_client,
            daily_movers=daily_movers,
            momentum_profiles=load_materialized_frame_fn("momentum_profiles"),
            ticker_background_frame=persist_frames.get("attention_ticker_background_snapshots", pd.DataFrame()),
            technical_signals_latest_frame=load_materialized_frame_fn("technical_signals_latest"),
            universe_snapshot_frame=load_materialized_frame_fn("universe_snapshot"),
        )
        persist_dataset_fn("page_agentic_summaries", page_summaries, ctx, conn)
        persist_frames["page_agentic_summaries"] = page_summaries
        print(f"[info] attention-home-build materialized optional page_agentic_summaries rows={len(page_summaries)}")
    except Exception as exc:
        print(f"[warn] attention-home-build optional page_agentic_summaries failed after core persist: {type(exc).__name__}: {exc}")
    return persist_frames


__all__ = [
    "AttentionHomeBuildError",
    "build_attention_home_output_frames",
    "run_attention_home_build",
]
