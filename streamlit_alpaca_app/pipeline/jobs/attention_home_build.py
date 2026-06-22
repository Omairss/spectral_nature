from __future__ import annotations

import json
import os
import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import pandas as pd

from services.attention_agentic import build_bottom_up_attention_artifacts, search_symbol_news_payload
from services.aql.summarizer import build_market_stories
from services.aql_zopedia_engine import (
    attach_aql_zopedia_summary_audio as attach_attention_home_summary_audio,
    load_aql_zopedia_llm_client,
    run_aql_zopedia_agent,
    run_aql_zopedia_structured_agent,
)
from services.attention_surface import clean_attention_text, hydrate_home_item_with_bundle
from services.attention_home_1d import build_attention_entity_master, resolve_macro_anchor_symbols, shortlist_attention_symbols_1d
from services.attention_materialized import bars_by_symbol_from_price_history, serialize_attention_home_payload, serialize_attention_research_bundles
from services.attention_ticker_snapshots import (
    build_attention_ticker_background_snapshot_frame,
    build_attention_ticker_snapshot_frame,
    collect_attention_ticker_symbols,
    deserialize_attention_ticker_background_frame,
)
from services.common.news_freshness import (
    coerce_article_published_at,
    is_recent_for_attention,
)
from services.elevenlabs_tts import ElevenLabsTTSAPIError
from services.knowledge_graph_proposals import build_attention_knowledge_graph_proposals
from services.json_utils import to_list
from services.llm import LLMAPIError, load_embedding_client
from services.entity_taxonomy import business_focus_label_from_taxonomy_row
from services.market_opportunity import build_market_opportunity_feed, build_materialized_market_opportunity_feeds
from services.page_agentic_summary import (
    build_materialized_page_agentic_summary_row,
    build_page_agentic_summary,
    build_unavailable_page_agentic_summary,
    market_summary_context,
    stock_summary_context,
)
from services.pipeline_store import load_latest_dataset_frame, load_recent_dataset_frames


PersistDatasetFn = Callable[[str, pd.DataFrame, Any, Any | None], None]
JobProgressFn = Callable[..., None]
LoadFrameFn = Callable[[str], pd.DataFrame]
ResearchProgressFn = Callable[[int, int, dict[str, Any]], None]


class AttentionHomeBuildError(RuntimeError):
    pass


class _AttentionStepTimeout(TimeoutError):
    pass


def _load_latest_materialized_frame(dataset_name: str) -> pd.DataFrame:
    try:
        frame, _ = load_latest_dataset_frame(dataset_name)
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
    return _env_positive_int("PAGE_AGENTIC_SUMMARY_TIMEOUT_SECONDS", 300)


def _page_agentic_summary_max_workers() -> int:
    return _env_positive_int("PAGE_AGENTIC_SUMMARY_MAX_WORKERS", 4)


def _call_with_timeout(label: str, timeout_seconds: int, fn: Callable[[], Any]) -> Any:
    if timeout_seconds <= 0:
        return fn()

    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def _runner() -> None:
        try:
            result_queue.put(("ok", fn()))
        except BaseException as exc:
            result_queue.put(("error", exc))

    thread = threading.Thread(target=_runner, name=f"attention-timeout-{label[:32]}", daemon=True)
    thread.start()
    thread.join(float(timeout_seconds))
    if thread.is_alive():
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
_OPTIONAL_DATASETS = (
    "positions_snapshot",
    "attention_feed",
    "commodity_attention_feed",
    "news_articles",
    "attention_context_bundle",
    "edgar_filings",
    "fred_summary",
    "yield_curve_facts_1d",
    "universe_snapshot",
    "entity_taxonomy_labels",
)


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
                    _materialized_page_summary_row,
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
    return _env_positive_int("ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LIMIT", 80)


def _zopedia_enrichment_timeout_seconds() -> int:
    return _env_positive_int("ATTENTION_HOME_ZOPEDIA_ENRICHMENT_TIMEOUT_SECONDS", 600)


def _zopedia_agent_tool_timeout_seconds(total_timeout_seconds: int) -> int:
    configured = _env_positive_int("ATTENTION_HOME_ZOPEDIA_AGENT_TOOL_TIMEOUT_SECONDS", 120)
    return max(30, min(configured, max(int(total_timeout_seconds), 1)))


def _zopedia_agent_llm_timeout_seconds(total_timeout_seconds: int) -> int:
    configured = _env_positive_int("ATTENTION_HOME_ZOPEDIA_AGENT_LLM_TIMEOUT_SECONDS", 120)
    return max(30, min(configured, max(int(total_timeout_seconds), 1)))


def _zopedia_enrichment_max_workers() -> int:
    return _env_positive_int("ATTENTION_HOME_ZOPEDIA_ENRICHMENT_MAX_WORKERS", 16)


def _zopedia_market_summary_quality_revisions() -> int:
    return min(_env_positive_int("ATTENTION_HOME_ZOPEDIA_MARKET_SUMMARY_QUALITY_REVISIONS", 2), 4)


def _zopedia_enrichment_quality_revisions() -> int:
    return min(_env_positive_int("ATTENTION_HOME_ZOPEDIA_ENRICHMENT_QUALITY_REVISIONS", 2), 4)


def _zopedia_enrichment_llm_retries() -> int:
    return min(_env_positive_int("ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LLM_RETRIES", 2), 5)


def _zopedia_agent_max_tool_calls() -> int:
    return min(_env_positive_int("ATTENTION_HOME_ZOPEDIA_AGENT_MAX_TOOL_CALLS", 18), 40)


def _home_surface_quality_timeout_seconds() -> int:
    return _env_positive_int("ATTENTION_HOME_SURFACE_QUALITY_TIMEOUT_SECONDS", 240)


_HOME_SURFACE_QUALITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "accepted": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "section": {
                        "type": "string",
                        "enum": ["top_events", "must_read_movers", "unresolved_large_moves"],
                    },
                    "index": {"type": "integer"},
                    "bundle_id": {"type": "string"},
                    "symbol": {"type": "string"},
                    "publish": {"type": "boolean"},
                    "event_title": {"type": "string"},
                    "headline": {"type": "string"},
                    "what_happened_text": {"type": "string"},
                    "what_changed_text": {"type": "string"},
                    "why_happened_text": {"type": "string"},
                    "why_now_text": {"type": "string"},
                    "what_else_moved_text": {"type": "string"},
                    "affected_assets_summary_text": {"type": "string"},
                    "business_context_text": {"type": "string"},
                    "surface_summary_text": {"type": "string"},
                    "cause_status": {
                        "type": "string",
                        "enum": ["supported", "partial", "unresolved"],
                    },
                    "watch_next_text": {"type": "string"},
                    "review_note": {"type": "string"},
                },
                "required": [
                    "section",
                    "index",
                    "bundle_id",
                    "symbol",
                    "publish",
                    "event_title",
                    "headline",
                    "what_happened_text",
                    "what_changed_text",
                    "why_happened_text",
                    "why_now_text",
                    "what_else_moved_text",
                    "affected_assets_summary_text",
                    "business_context_text",
                    "surface_summary_text",
                    "cause_status",
                    "watch_next_text",
                    "review_note",
                ],
            },
        },
    },
    "required": ["accepted", "issues", "items"],
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


def _compact_zopedia_tool_evidence(tool_calls: list[Any], *, limit: int = 8) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tool_call in list(tool_calls or []):
        if not isinstance(tool_call, dict):
            continue
        summary = tool_call.get("result_summary") if isinstance(tool_call.get("result_summary"), dict) else {}
        text = str(summary.get("llm_context_text") or summary.get("preview_text") or "").strip()
        links = summary.get("source_links") if isinstance(summary.get("source_links"), list) else []
        rows.append(
            {
                "tool_name": str(tool_call.get("tool_name") or ""),
                "status": str(tool_call.get("status") or ""),
                "arguments": tool_call.get("arguments") if isinstance(tool_call.get("arguments"), dict) else {},
                "evidence_text": text[:4000],
                "source_links": links[:5],
                "provenance": summary.get("provenance") if isinstance(summary.get("provenance"), dict) else {},
            }
        )
        if len(rows) >= max(int(limit), 1):
            break
    return rows


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


_ZOPEDIA_ENRICHMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answer_markdown": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer_markdown", "confidence", "limitations"],
}


_ZOPEDIA_ENRICHMENT_REVIEW_SCHEMA: dict[str, Any] = {
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


def _build_zopedia_enrichment_query(symbol: str, *, asof_text: str) -> str:
    return (
        f"Resolve what is driving {symbol} right now through Zopedia/AQL. "
        f"As-of timestamp: {asof_text}. Compare every source date to this timestamp. "
        "Use widening circles of recall: exact ticker/company, direct peers, customers, suppliers, "
        "sector themes, private-company leaders, IPO speculation, policy, rates, liquidity, commodities, "
        "and other macro forces when they plausibly explain the move. "
        "Do not stop at ticker-specific news if the best explanation is sector or macro. "
        "Treat retained memory and older or undated sources as business background only. A current-driver "
        "claim must come from recent evidence that directly ties that force to today's ticker, peer, sector, "
        "or macro move. If the evidence only supports a broad rally, say broad rally rather than inventing "
        "a precise macro or geopolitical cause. "
        "If investigator.recent_news returns headline-only rows with URLs, call research.open_page on the most "
        "important current source before using the article as verified evidence. "
        "Distinguish direct data from proxies: if you only have ETF/proxy evidence such as USO, XLE, SMH, "
        "or peer moves, describe the proxy or peer move; do not convert it into a confirmed commodity, "
        "rate, or macro fact. "
        "If a source is older than the current move, use it only to explain business sensitivity, not as the "
        "driver of today's move. If source dates conflict, say the date evidence is unreliable instead of "
        "using that source as today's driver. "
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


def _review_zopedia_enrichment_payload(
    *,
    symbol: str,
    original_query: str,
    draft_payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    review_query = (
        "You are a Zopedia/AQL quality monitor. Review every sentence in answer_markdown one by one. "
        "A sentence fails if its main point is that company-specific news, a catalyst, direct evidence, source evidence, "
        "or a driver is missing, not found, absent, unclear, unavailable, or unconfirmed. A sentence also fails if it "
        "contrasts the supported driver against missing company-specific evidence; for example, a sentence that says a "
        "move points to sector momentum rather than company-specific news is still a missing-evidence sentence. "
        "A short What To Watch sentence is allowed when it names the next concrete evidence to monitor in affirmative "
        "language, without saying that evidence is missing, absent, unavailable, unclear, unconfirmed, or needs confirmation. "
        "Limitations can contain evidence gaps; "
        "answer_markdown cannot. Accept only if every answer sentence is an affirmative supported business, sector, peer, "
        "policy, financing, commodity, or macro claim. Numerical claims, dates, analyst targets, financial figures, "
        "and large percentage moves require clear support in the draft context; reject any precise figure that looks "
        "implausible, scale-confused, or unexplained by the supplied evidence. Use draft evidence_trace as source support; "
        "do not require inline citation syntax in answer_markdown when evidence_trace supports the claim. Also reject raw tool-output scaffolding, "
        "unsupported precise causes, and drafts where technical price action is the core thesis. If it fails, list the failing sentences and one "
        "revision instruction.\n\n"
        f"Ticker: {symbol}\n\n"
        "Original task:\n"
        f"{original_query}\n\n"
        "Draft JSON:\n"
        f"{json.dumps(draft_payload, ensure_ascii=True, sort_keys=True, default=str)}"
    )
    result = _call_with_timeout(
        f"zopedia-enrich-review-{symbol}",
        timeout_seconds,
        lambda: run_aql_zopedia_structured_agent(
            query=review_query,
            schema_name="attention_ticker_enrichment_review",
            schema=_ZOPEDIA_ENRICHMENT_REVIEW_SCHEMA,
            task="attention_ticker_enrichment_review",
            surface="attention_home.zopedia_ticker_enrichment_review",
            max_tool_calls=0,
            persist_findings=False,
        ),
    )
    if not isinstance(result, dict) or str(result.get("status") or "") != "completed":
        return {
            "accepted": False,
            "issues": [str((result or {}).get("error") or "Zopedia quality monitor did not complete.")],
            "revision_instruction": "Revise the ticker enrichment to comply with the evidence contract.",
            "confidence": "low",
        }
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    return {
        "accepted": bool(payload.get("accepted")),
        "issues": [str(item).strip() for item in list(payload.get("issues") or []) if str(item).strip()][:8],
        "revision_instruction": str(payload.get("revision_instruction") or "").strip(),
        "confidence": str(payload.get("confidence") or "low").strip() or "low",
    }


def _revise_zopedia_enrichment_payload(
    *,
    symbol: str,
    original_query: str,
    draft_payload: dict[str, Any],
    review: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    revision_query = (
        "Revise this Attention ticker enrichment through the Zopedia/AQL quality contract. "
        "Use only the supplied original task and draft JSON; do not add new facts or fabricate missing sources. "
        "Preserve grounded business, sector, peer, customer, supplier, policy, financing, commodity, or macro mechanisms. "
        "Remove unsupported precision, raw tool scaffolding, and body text about evidence that was not found. If the "
        "supported read is broad sector/peer momentum, write that directly and keep ticker-specific source limitations "
        "inside limitations. If the draft lacks enough substantive support for an investor-facing explanation, return "
        "one concise What To Watch sentence naming the next concrete evidence target in affirmative monitoring language. "
        "Do not use 'confirm', 'verify', 'check whether', 'unclear', 'unconfirmed', 'missing', or 'not found' in "
        "answer_markdown; keep the exact evidence gap in limitations.\n\n"
        f"Ticker: {symbol}\n\n"
        "Quality monitor review JSON:\n"
        f"{json.dumps(review, ensure_ascii=True, sort_keys=True, default=str)}\n\n"
        "Original task:\n"
        f"{original_query}\n\n"
        "Draft JSON:\n"
        f"{json.dumps(draft_payload, ensure_ascii=True, sort_keys=True, default=str)}"
    )
    result = _call_with_timeout(
        f"zopedia-enrich-quality-{symbol}",
        timeout_seconds,
        lambda: run_aql_zopedia_structured_agent(
            query=revision_query,
            schema_name="attention_ticker_enrichment_quality",
            schema=_ZOPEDIA_ENRICHMENT_SCHEMA,
            task="attention_ticker_enrichment_quality",
            surface="attention_home.zopedia_ticker_enrichment_quality",
            max_tool_calls=0,
            persist_findings=False,
        ),
    )
    if not isinstance(result, dict) or str(result.get("status") or "") != "completed":
        error = str((result or {}).get("error") or "Zopedia quality pass did not complete.")
        return {
            "answer_markdown": "",
            "confidence": "low",
            "limitations": [error],
            "_status": "failed_quality_review",
        }
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    if not str(payload.get("answer_markdown") or "").strip():
        return {
            "answer_markdown": "",
            "confidence": str(payload.get("confidence") or "low").strip() or "low",
            "limitations": list(payload.get("limitations") or ["Zopedia quality pass returned an empty enrichment."]),
            "_status": "failed_quality_review",
        }
    return dict(payload)


def _run_single_zopedia_enrichment(
    symbol: str,
    *,
    timeout_seconds: int,
    asof_time_utc: object,
) -> dict[str, Any]:
    asof_text = _zopedia_asof_text(asof_time_utc)
    query = _build_zopedia_enrichment_query(symbol, asof_text=asof_text)
    try:
        attempts = _zopedia_enrichment_llm_retries() + 1
        result: dict[str, Any] = {}
        retry_notes: list[str] = []
        for attempt in range(1, attempts + 1):
            result = _call_with_timeout(
                f"zopedia-enrich-{symbol}-attempt-{attempt}",
                timeout_seconds,
                lambda: run_aql_zopedia_agent(
                    query=query,
                    task="attention_ticker_enrichment",
                    surface="attention_home.zopedia_ticker_enrichment",
                    force_refresh=True,
                    max_tool_calls=_zopedia_agent_max_tool_calls(),
                    tool_call_timeout_seconds=_zopedia_agent_tool_timeout_seconds(timeout_seconds),
                    llm_step_timeout_seconds=_zopedia_agent_llm_timeout_seconds(timeout_seconds),
                    prefetch_timeout_seconds=min(_zopedia_agent_tool_timeout_seconds(timeout_seconds), 60),
                    persist_findings=False,
                ),
            )
            if not _is_retryable_zopedia_llm_failure(result):
                break
            if attempt >= attempts:
                retry_notes.append(f"Zopedia LLM failed after {attempts} attempts; enrichment discarded.")
                break
            retry_notes.append(f"Zopedia LLM attempt {attempt} failed; retried within the same AQL contract.")
        tool_calls = [tc for tc in list(result.get("tool_calls") or []) if isinstance(tc, dict)]
        evidence_trace = _compact_zopedia_tool_evidence(tool_calls)
        answer_markdown = str(result.get("answer_markdown") or "")
        status = str(result.get("status") or "unknown")
        limitations = list(result.get("limitations") or [])
        for note in retry_notes:
            if note not in limitations:
                limitations.append(note)
        quality_review: dict[str, Any] = dict(result.get("quality_review") or {})
        payload_result = {
            "answer_markdown": answer_markdown,
            "confidence": str(result.get("confidence") or "low").strip() or "low",
            "limitations": limitations,
            "evidence_trace": evidence_trace,
        }
        if status in {"completed", "success"} and answer_markdown:
            reviews: list[dict[str, Any]] = []
            current_review = _review_zopedia_enrichment_payload(
                symbol=symbol,
                original_query=query,
                draft_payload=payload_result,
                timeout_seconds=timeout_seconds,
            )
            current_review["status"] = "reviewed"
            current_review["revision_attempt"] = 0
            reviews.append(dict(current_review))
            for attempt in range(1, _zopedia_enrichment_quality_revisions() + 1):
                if bool(current_review.get("accepted")):
                    break
                payload_result = _revise_zopedia_enrichment_payload(
                    symbol=symbol,
                    original_query=query,
                    draft_payload=payload_result,
                    review=current_review,
                    timeout_seconds=timeout_seconds,
                )
                payload_result["evidence_trace"] = evidence_trace
                current_review = _review_zopedia_enrichment_payload(
                    symbol=symbol,
                    original_query=query,
                    draft_payload=payload_result,
                    timeout_seconds=timeout_seconds,
                )
                current_review["status"] = "reviewed_after_revision"
                current_review["revision_attempt"] = attempt
                reviews.append(dict(current_review))
            quality_review = {
                "status": "accepted" if bool(current_review.get("accepted")) else "failed_quality_review",
                "reviews": reviews,
            }
            if not bool(current_review.get("accepted")):
                payload_result = {
                    "answer_markdown": "",
                    "confidence": "low",
                    "limitations": [
                        "Zopedia ticker enrichment failed the quality monitor after revision.",
                        *list(current_review.get("issues") or [])[:5],
                    ],
                    "_status": "failed_quality_review",
                }
            answer_markdown = str(payload_result.get("answer_markdown") or "")
            status = str(payload_result.get("_status") or status)
            limitations = list(payload_result.get("limitations") or [])
        if _looks_like_failed_zopedia_synthesis_answer(answer_markdown):
            answer_markdown = ""
            status = "failed_synthesis"
            limitations.append("Zopedia returned tool output instead of a final synthesis; hidden from display.")
        failure_categories = _zopedia_enrichment_failure_categories(
            status=status,
            answer_markdown=answer_markdown,
            limitations=limitations,
            tool_calls=tool_calls,
            quality_review=quality_review,
        )
        return {
            "symbol": symbol,
            "status": status,
            "answer_markdown": answer_markdown,
            "confidence": _cap_zopedia_confidence(payload_result.get("confidence") or result.get("confidence"), limitations),
            "limitations_json": json.dumps(limitations),
            "tool_calls_json": json.dumps([
                {
                    "tool_name": str(tc.get("tool_name") or ""),
                    "status": str(tc.get("status") or ""),
                }
                for tc in tool_calls
            ]),
            "quality_review_json": json.dumps(quality_review),
            "failure_categories_json": json.dumps(failure_categories),
            "model": str(result.get("model") or ""),
        }
    except Exception as exc:
        failure_categories = _zopedia_enrichment_failure_categories(
            status="failed",
            answer_markdown="",
            limitations=[f"{type(exc).__name__}: {exc}"],
            tool_calls=[],
            quality_review={},
        )
        return {
            "symbol": symbol,
            "status": "failed",
            "answer_markdown": "",
            "confidence": "",
            "limitations_json": json.dumps([f"{type(exc).__name__}: {exc}"]),
            "tool_calls_json": "[]",
            "quality_review_json": "{}",
            "failure_categories_json": json.dumps(failure_categories),
            "model": "",
        }


ZOPEDIA_MARKET_SUMMARY_KEY = "__MARKET_SUMMARY__"
_ZOPEDIA_MARKET_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answer_markdown": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer_markdown", "confidence", "limitations"],
}
_ZOPEDIA_MARKET_SUMMARY_REVIEW_SCHEMA: dict[str, Any] = {
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
    stories_text = "\n".join(story_lines) if story_lines else "No market stories available."
    asof_text = _zopedia_asof_text(asof_time_utc)
    enrichment_text = _zopedia_enrichment_context(list(enrichment_rows or []))
    enrichment_block = (
        "Already-collected Zopedia ticker/event research:\n"
        f"{enrichment_text}\n\n"
        if enrichment_text
        else ""
    )
    return (
        "Write a daily market summary for an informed investor. "
        f"As-of timestamp: {asof_text}. Compare source dates to this timestamp. "
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
        "3. **Macro backdrop** — rates, dollar, commodities, anything driving the broader tape\n"
        "4. **What to watch** — upcoming evidence, company events, or risks for the next session\n\n"
        "Write 300-500 words. Every sentence must add information the investor cannot get from a ticker screen. "
        "Do not hedge or pad — if you lack evidence for a claim, drop the claim.\n\n"
        f"{enrichment_block}"
        f"Today's market stories:\n{stories_text}"
    )


def _review_zopedia_market_summary_payload(
    *,
    original_query: str,
    draft_payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    review_query = (
        "You are the Zopedia/AQL quality monitor for an Attention market summary. "
        "Decide whether the draft can be shown to an investor. Use only the supplied original evidence prompt and draft. "
        "Accept only if answer_markdown: explains supported drivers from the evidence, keeps uncertainty and evidence gaps "
        "inside limitations rather than body prose, distinguishes proxy evidence from direct data, avoids unsupported "
        "company-name expansion, and does not overstate stale or inferred drivers. If it fails, list concrete issues and "
        "write one revision instruction that would make it publishable.\n\n"
        "Original evidence prompt:\n"
        f"{original_query}\n\n"
        "Draft JSON:\n"
        f"{json.dumps(draft_payload, ensure_ascii=True, sort_keys=True, default=str)}"
    )
    result = _call_with_timeout(
        "zopedia-market-summary-review",
        timeout_seconds,
        lambda: run_aql_zopedia_structured_agent(
            query=review_query,
            schema_name="attention_market_summary_review",
            schema=_ZOPEDIA_MARKET_SUMMARY_REVIEW_SCHEMA,
            task="attention_market_summary_review",
            surface="attention_home.zopedia_market_summary_review",
            max_tool_calls=0,
            persist_findings=False,
        ),
    )
    if not isinstance(result, dict) or str(result.get("status") or "") != "completed":
        return {
            "accepted": False,
            "issues": [str((result or {}).get("error") or "Zopedia quality monitor did not complete.")],
            "revision_instruction": "Revise the summary to comply with the evidence contract.",
            "confidence": "low",
        }
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    return {
        "accepted": bool(payload.get("accepted")),
        "issues": [str(item).strip() for item in list(payload.get("issues") or []) if str(item).strip()][:8],
        "revision_instruction": str(payload.get("revision_instruction") or "").strip(),
        "confidence": str(payload.get("confidence") or "low").strip() or "low",
    }


def _revise_zopedia_market_summary_payload(
    *,
    original_query: str,
    draft_payload: dict[str, Any],
    review: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    revision_query = (
        "Revise this Attention market summary through the Zopedia/AQL quality contract. "
        "Use only the supplied original evidence prompt and draft JSON. Preserve grounded facts, remove unsupported "
        "claims, and tighten wording. A valid final answer names supported drivers, treats ETF/proxy evidence as "
        "proxy evidence, does not expand tickers into company names unless the evidence supplied that name, and never "
        "uses body prose about missing drivers, catalysts, company news, macro data, or evidence. Answer markdown should "
        "contain positive supported claims only; evidence gaps belong in limitations. If a claim cannot be written "
        "positively from evidence, omit the claim rather than explaining the absence.\n\n"
        "Quality monitor review JSON:\n"
        f"{json.dumps(review, ensure_ascii=True, sort_keys=True, default=str)}\n\n"
        "Original evidence prompt:\n"
        f"{original_query}\n\n"
        "Draft JSON:\n"
        f"{json.dumps(draft_payload, ensure_ascii=True, sort_keys=True, default=str)}"
    )
    result = _call_with_timeout(
        "zopedia-market-summary-quality",
        timeout_seconds,
        lambda: run_aql_zopedia_structured_agent(
            query=revision_query,
            schema_name="attention_market_summary_quality",
            schema=_ZOPEDIA_MARKET_SUMMARY_SCHEMA,
            task="attention_market_summary_quality",
            surface="attention_home.zopedia_market_summary_quality",
            max_tool_calls=0,
            persist_findings=False,
        ),
    )
    if not isinstance(result, dict) or str(result.get("status") or "") != "completed":
        error = str((result or {}).get("error") or "Zopedia quality pass did not complete.")
        return {
            "answer_markdown": "",
            "confidence": "low",
            "limitations": [error],
            "_status": "failed_quality_review",
        }
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    if not str(payload.get("answer_markdown") or "").strip():
        return {
            "answer_markdown": "",
            "confidence": "low",
            "limitations": ["Zopedia quality pass returned an empty summary."],
            "_status": "failed_quality_review",
        }
    return dict(payload)


def _run_zopedia_market_summary(
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
    enrichment_rows: list[dict[str, Any]] | None = None,
    asof_time_utc: object = None,
) -> dict[str, Any]:
    query = _build_market_summary_query(payload, enrichment_rows=enrichment_rows, asof_time_utc=asof_time_utc)
    try:
        result = _call_with_timeout(
            "zopedia-market-summary",
            timeout_seconds,
            lambda: run_aql_zopedia_structured_agent(
                query=query,
                schema_name="attention_market_summary",
                schema=_ZOPEDIA_MARKET_SUMMARY_SCHEMA,
                task="attention_market_summary",
                surface="attention_home.zopedia_market_summary",
                force_refresh=True,
                max_tool_calls=0,
                tool_call_timeout_seconds=_zopedia_agent_tool_timeout_seconds(timeout_seconds),
                llm_step_timeout_seconds=_zopedia_agent_llm_timeout_seconds(timeout_seconds),
                prefetch_timeout_seconds=min(_zopedia_agent_tool_timeout_seconds(timeout_seconds), 60),
                persist_findings=False,
            ),
        )
        payload_result = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        agent_result = result.get("agent_result") if isinstance(result.get("agent_result"), dict) else {}
        quality_review: dict[str, Any] = {"status": "not_run"}
        if str(result.get("status") or agent_result.get("status") or "") == "completed" and str(payload_result.get("answer_markdown") or "").strip():
            reviews: list[dict[str, Any]] = []
            current_review = _review_zopedia_market_summary_payload(
                original_query=query,
                draft_payload=payload_result,
                timeout_seconds=timeout_seconds,
            )
            current_review["status"] = "reviewed"
            current_review["revision_attempt"] = 0
            reviews.append(dict(current_review))
            for attempt in range(1, _zopedia_market_summary_quality_revisions() + 1):
                if bool(current_review.get("accepted")):
                    break
                payload_result = _revise_zopedia_market_summary_payload(
                    original_query=query,
                    draft_payload=payload_result,
                    review=current_review,
                    timeout_seconds=timeout_seconds,
                )
                current_review = _review_zopedia_market_summary_payload(
                    original_query=query,
                    draft_payload=payload_result,
                    timeout_seconds=timeout_seconds,
                )
                current_review["status"] = "reviewed_after_revision"
                current_review["revision_attempt"] = attempt
                reviews.append(dict(current_review))
            quality_review = {
                "status": "accepted" if bool(current_review.get("accepted")) else "failed_quality_review",
                "reviews": reviews,
            }
            if not bool(current_review.get("accepted")):
                payload_result = {
                    "answer_markdown": "",
                    "confidence": "low",
                    "limitations": [
                        "Zopedia market summary failed the quality monitor after revision.",
                        *list(current_review.get("issues") or [])[:5],
                    ],
                    "_status": "failed_quality_review",
                }
        answer_markdown = str(payload_result.get("answer_markdown") or "")
        status = str(payload_result.get("_status") or result.get("status") or agent_result.get("status") or "unknown")
        limitations = list(payload_result.get("limitations") or [])
        if result.get("error"):
            limitations.append(str(result.get("error")))
        if _looks_like_failed_zopedia_synthesis_answer(answer_markdown):
            answer_markdown = ""
            status = "failed_synthesis"
            limitations.append("Zopedia returned tool output instead of a final synthesis; hidden from display.")
        row = {
            "symbol": ZOPEDIA_MARKET_SUMMARY_KEY,
            "status": status,
            "answer_markdown": answer_markdown,
            "confidence": str(payload_result.get("confidence") or agent_result.get("confidence") or ""),
            "limitations_json": json.dumps(limitations),
            "tool_calls_json": json.dumps([
                {
                    "tool_name": str(tc.get("tool_name") or ""),
                    "status": str(tc.get("status") or ""),
                }
                for tc in list(agent_result.get("tool_calls") or [])
                if isinstance(tc, dict)
            ]),
            "quality_review_json": json.dumps(quality_review),
            "model": str(agent_result.get("model") or ""),
        }
        return _attach_market_summary_audio(row)
    except Exception as exc:
        return {
            "symbol": ZOPEDIA_MARKET_SUMMARY_KEY,
            "status": "failed",
            "answer_markdown": "",
            "confidence": "",
            "limitations_json": json.dumps([f"{type(exc).__name__}: {exc}"]),
            "tool_calls_json": "[]",
            "quality_review_json": "{}",
            "model": "",
        }


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

    from concurrent.futures import ThreadPoolExecutor, as_completed
    rows: list[dict[str, Any]] = []

    if symbols:
        print(f"[info] attention-home-build zopedia ticker enrichment: {len(symbols)} symbols, timeout={timeout}s each")
        max_workers = min(len(symbols), _zopedia_enrichment_max_workers())
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="zopedia-enrich") as pool:
            futures = {
                pool.submit(
                    _run_single_zopedia_enrichment,
                    symbol,
                    timeout_seconds=timeout,
                    asof_time_utc=asof_time_utc,
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
                        "limitations_json": json.dumps([str(exc)]),
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

    print("[info] attention-home-build zopedia market summary starting")
    summary_row = _run_zopedia_market_summary(
        payload,
        timeout_seconds=timeout,
        enrichment_rows=rows,
        asof_time_utc=asof_time_utc,
    )
    rows.insert(0, summary_row)
    print(f"[info] zopedia market summary: {summary_row.get('status', 'unknown')}")

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
            include_undated=False,
        ):
            continue
        article = {
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
        futures = {pool.submit(_fetch, symbol): symbol for symbol in candidates}
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
                include_undated=False,
            ):
                continue
            rows.append(
                {
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
                    include_undated=False,
                ):
                    continue
                rows.append(
                    {
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
        out["business_context_text"] = " ".join(
            str(context.get("business_context_text") or "").strip()
            for context in event_contexts[:3]
            if str(context.get("business_context_text") or "").strip()
        ).strip()
        out["surface_business_context_text"] = out.get("business_context_text") or ""
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
    enrichment_by_symbol = {
        str(row.get("symbol") or "").upper().strip(): _plain_business_text(row.get("answer_markdown"), limit=700)
        for row in list(enrichment_rows or [])
        if isinstance(row, dict)
        and str(row.get("symbol") or "").upper().strip()
        and str(row.get("status") or "").strip() in {"completed", "success"}
    }
    enrichment_by_symbol.pop(ZOPEDIA_MARKET_SUMMARY_KEY, None)

    def _evidence_trace(bundle: dict[str, Any]) -> list[dict[str, Any]]:
        trace: list[dict[str, Any]] = []
        for claim in list(bundle.get("claims") or [])[:8]:
            if not isinstance(claim, dict):
                continue
            trace.append(
                {
                    "kind": "claim",
                    "source": _plain_business_text(claim.get("source"), limit=120),
                    "freshness_class": _plain_business_text(claim.get("freshness_class"), limit=80),
                    "is_same_day": bool(claim.get("is_same_day")),
                    "claim_text": _plain_business_text(claim.get("claim_text"), limit=260),
                }
            )
        for doc in list(bundle.get("evidence") or [])[:6] + list(bundle.get("background_context") or [])[:8]:
            if not isinstance(doc, dict):
                continue
            trace.append(
                {
                    "kind": _plain_business_text(doc.get("evidence_role") or "source", limit=80),
                    "source": _plain_business_text(doc.get("source"), limit=120),
                    "published_at": _plain_business_text(doc.get("published_at"), limit=80),
                    "headline": _plain_business_text(doc.get("headline"), limit=260),
                    "importance_label": _plain_business_text(doc.get("importance_label"), limit=80),
                }
            )
        return trace[:14]

    rows: list[dict[str, Any]] = []
    for section in ["top_events", "must_read_movers", "unresolved_large_moves"]:
        for index, raw_item in enumerate(list((payload or {}).get(section) or [])):
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            bundle_id = str(item.get("bundle_id") or "").strip()
            bundle = dict(bundle_map.get(bundle_id) or {}) if bundle_id else {}
            symbols = [
                str(value or "").upper().strip()
                for value in list(item.get("supporting_symbols") or bundle.get("supporting_symbols") or [])
                if str(value or "").strip()
            ]
            symbol = str(item.get("symbol") or bundle.get("symbol") or "").upper().strip()
            if symbol and symbol not in symbols:
                symbols.insert(0, symbol)
            zopedia_context = " ".join(
                enrichment_by_symbol[sym]
                for sym in symbols[:5]
                if enrichment_by_symbol.get(sym)
            ).strip()
            rows.append(
                {
                    "section": section,
                    "index": index,
                    "bundle_id": bundle_id,
                    "symbol": symbol,
                    "symbols": symbols[:8],
                    "cause_status": str(item.get("cause_status") or bundle.get("cause_status") or "").strip(),
                    "evidence_quality": str(item.get("evidence_quality") or bundle.get("evidence_quality") or "").strip(),
                    "freshness_quality": str(item.get("freshness_quality") or bundle.get("freshness_quality") or "").strip(),
                    "event_title": _plain_business_text(item.get("event_title") or bundle.get("event_title"), limit=220),
                    "headline": _plain_business_text(item.get("headline") or bundle.get("headline"), limit=220),
                    "what_happened_text": _plain_business_text(
                        item.get("what_happened_text") or bundle.get("what_happened_text"),
                        limit=700,
                    ),
                    "what_changed_text": _plain_business_text(
                        item.get("what_changed_text") or bundle.get("what_changed_text"),
                        limit=700,
                    ),
                    "why_happened_text": _plain_business_text(
                        item.get("why_happened_text") or bundle.get("why_happened_text"),
                        limit=700,
                    ),
                    "why_now_text": _plain_business_text(
                        item.get("why_now_text") or bundle.get("why_now_text"),
                        limit=700,
                    ),
                    "what_else_moved_text": _plain_business_text(
                        item.get("what_else_moved_text") or bundle.get("what_else_moved_text"),
                        limit=700,
                    ),
                    "business_context_text": _plain_business_text(
                        item.get("business_context_text")
                        or item.get("surface_business_context_text")
                        or bundle.get("business_context_text")
                        or bundle.get("surface_business_context_text"),
                        limit=900,
                    ),
                    "zopedia_enrichment_text": zopedia_context[:1200],
                    "source_summary": _plain_business_text(item.get("source_summary") or bundle.get("source_summary"), limit=700),
                    "evidence_count": int(float(item.get("evidence_count") or bundle.get("evidence_count") or 0)),
                    "same_day_evidence_count": int(
                        float(item.get("same_day_evidence_count") or bundle.get("same_day_evidence_count") or 0)
                    ),
                    "source_count": int(float(item.get("source_count") or bundle.get("source_count") or 0)),
                    "top_source": _plain_business_text(item.get("top_source") or bundle.get("top_source"), limit=220),
                    "evidence_trace": _evidence_trace(bundle),
                    "affected_assets_summary_text": _plain_business_text(
                        item.get("affected_assets_summary_text") or bundle.get("affected_assets_summary_text"),
                        limit=500,
                    ),
                }
            )
    return rows


def _attach_zopedia_enrichment_to_payload(
    payload: dict[str, Any],
    bundle_map: dict[str, dict[str, Any]],
    enrichment_rows: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    enrichment_by_symbol = {
        str(row.get("symbol") or "").upper().strip(): _plain_business_text(row.get("answer_markdown"), limit=900)
        for row in list(enrichment_rows or [])
        if isinstance(row, dict)
        and str(row.get("symbol") or "").upper().strip()
        and str(row.get("status") or "").strip() in {"completed", "success"}
        and _plain_business_text(row.get("answer_markdown"), limit=900)
    }
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
        texts = [enrichment_by_symbol[symbol] for symbol in _symbols_for_item(out, bundle) if enrichment_by_symbol.get(symbol)]
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


def _apply_home_surface_quality_items(
    payload: dict[str, Any],
    bundle_map: dict[str, dict[str, Any]],
    quality_items: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, int]]:
    revisions: dict[tuple[str, int, str], dict[str, Any]] = {}
    for raw in list(quality_items or []):
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
    dropped = 0
    text_fields = [
        "event_title",
        "headline",
        "what_happened_text",
        "what_changed_text",
        "why_happened_text",
        "why_now_text",
        "what_else_moved_text",
        "affected_assets_summary_text",
        "business_context_text",
        "watch_next_text",
    ]
    stale_surface_fields = [
        "surface_summary_text",
        "surface_what_changed_text",
        "surface_why_text",
        "surface_what_else_moved_text",
        "surface_business_context_text",
        "surface_source_summary",
        "public_surface_review_note",
    ]

    def _drop_public_top_event(section: str, item: dict[str, Any], bundle: dict[str, Any] | None) -> bool:
        if section != "top_events":
            return False
        cause_status = str(item.get("surface_cause_status") or item.get("cause_status") or "").strip().lower()
        public_why = clean_attention_text(
            item.get("surface_why_text") or item.get("why_happened_text") or item.get("why_now_text")
        )
        business_context = clean_attention_text(
            item.get("business_context_text")
            or item.get("surface_business_context_text")
            or (bundle or {}).get("business_context_text")
            or (bundle or {}).get("surface_business_context_text")
        )
        watch_next = clean_attention_text(item.get("watch_next_text") or (bundle or {}).get("watch_next_text"))
        return cause_status == "unresolved" or (not public_why and not business_context and not watch_next)

    def _has_current_public_driver(item: dict[str, Any], bundle: dict[str, Any] | None) -> bool:
        freshness = str(
            item.get("freshness_quality")
            or item.get("surface_freshness_quality")
            or (bundle or {}).get("freshness_quality")
            or (bundle or {}).get("surface_freshness_quality")
            or ""
        ).strip().lower()
        same_day = int(float(item.get("same_day_evidence_count") or (bundle or {}).get("same_day_evidence_count") or 0))
        if same_day > 0:
            return True
        for claim in list((bundle or {}).get("claims") or []):
            if not isinstance(claim, dict):
                continue
            freshness_class = str(claim.get("freshness_class") or "").strip().lower()
            if bool(claim.get("is_same_day")) or freshness_class in {"same_day", "current"}:
                return True
        asof = pd.to_datetime(
            item.get("generated_at_utc")
            or item.get("asof_time_utc")
            or (bundle or {}).get("generated_at_utc")
            or (bundle or {}).get("asof_time_utc"),
            utc=True,
            errors="coerce",
        )
        if pd.isna(asof):
            asof = pd.Timestamp.now(tz="UTC")
        if pd.notna(asof):
            for doc in list((bundle or {}).get("evidence") or []):
                if not isinstance(doc, dict):
                    continue
                published = pd.to_datetime(doc.get("published_at"), utc=True, errors="coerce")
                if pd.notna(published) and pd.Timedelta(0) <= asof - published <= pd.Timedelta(hours=72):
                    return True
        if freshness in {"same_day", "current"}:
            return True
        return False

    def _has_zopedia_business_context(item: dict[str, Any], bundle: dict[str, Any] | None) -> bool:
        if clean_attention_text(item.get("business_resolution_text") or (bundle or {}).get("business_resolution_text")):
            return True
        if clean_attention_text(item.get("business_stack_text") or (bundle or {}).get("business_stack_text")):
            return True
        if isinstance(item.get("business_context"), dict) or isinstance((bundle or {}).get("business_context"), dict):
            return True
        if list(item.get("business_contexts") or []) or list((bundle or {}).get("business_contexts") or []):
            return True
        return False

    def _suppress_stale_driver(item: dict[str, Any], bundle: dict[str, Any] | None) -> bool:
        if _has_current_public_driver(item, bundle):
            return False
        cause_status = str(item.get("surface_cause_status") or item.get("cause_status") or "").strip().lower()
        public_why = clean_attention_text(
            item.get("surface_why_text") or item.get("why_happened_text") or item.get("why_now_text")
        )
        if cause_status == "supported" or public_why:
            return True
        return False

    def _neutral_observed_title(item: dict[str, Any], bundle: dict[str, Any] | None) -> str:
        symbol = str(item.get("symbol") or (bundle or {}).get("symbol") or "").upper().strip()
        symbols = [
            str(value or "").upper().strip()
            for value in list(item.get("supporting_symbols") or (bundle or {}).get("supporting_symbols") or [])
            if str(value or "").strip()
        ]
        label = symbol or (", ".join(symbols[:3]) if symbols else "Observed move")
        change = pd.to_numeric(item.get("change_pct") or (bundle or {}).get("change_pct"), errors="coerce")
        direction = str(item.get("direction") or (bundle or {}).get("direction") or "").strip().lower()
        if pd.notna(change):
            verb = "rises" if float(change) > 0 else ("falls" if float(change) < 0 else "moves")
        elif any(token in direction for token in ["up", "gain", "rise", "positive"]):
            verb = "rises"
        elif any(token in direction for token in ["down", "loss", "fall", "negative"]):
            verb = "falls"
        else:
            verb = "moves"
        return f"{label} {verb}".strip()

    def _has_bundle_evidence(bundle: dict[str, Any] | None) -> bool:
        if not isinstance(bundle, dict) or not bundle:
            return False
        if list(bundle.get("claims") or []) or list(bundle.get("evidence") or []) or list(bundle.get("background_context") or []):
            return True
        if int(float(bundle.get("source_count") or 0)) > 0:
            return True
        return bool(clean_attention_text(bundle.get("source_summary")))

    def _neutralize_stale_unresolved_title(item: dict[str, Any], bundle: dict[str, Any] | None) -> None:
        cause_status = str(item.get("surface_cause_status") or item.get("cause_status") or "").strip().lower()
        if cause_status != "unresolved" or _has_current_public_driver(item, bundle):
            return
        title = _neutral_observed_title(item, bundle)
        if title:
            item["event_title"] = title
            item["headline"] = title
            item["surface_summary_text"] = title
            item["what_happened_text"] = title
            item["what_changed_text"] = title
            if bundle is not None:
                bundle["event_title"] = title
                bundle["headline"] = title
                bundle["surface_summary_text"] = title
                bundle["what_happened_text"] = title
                bundle["what_changed_text"] = title
        if not _has_bundle_evidence(bundle):
            for field in ["business_context_text", "surface_business_context_text"]:
                item[field] = ""
                if bundle is not None:
                    bundle[field] = ""

    def _as_unresolved_item(item: dict[str, Any], bundle: dict[str, Any] | None) -> dict[str, Any]:
        out = dict(item or {})
        out["cause_status"] = "unresolved"
        out["surface_cause_status"] = "unresolved"
        out["why_happened_text"] = ""
        out["why_now_text"] = ""
        out["surface_why_text"] = ""
        out["section"] = "unresolved_large_moves"
        symbols = [
            str(value or "").upper().strip()
            for value in list(out.get("supporting_symbols") or (bundle or {}).get("supporting_symbols") or [])
            if str(value or "").strip()
        ]
        if symbols and not out.get("symbol"):
            out["symbol"] = symbols[0]
        _neutralize_stale_unresolved_title(out, bundle)
        return out

    def _mark_unpublished(item: dict[str, Any], bundle: dict[str, Any] | None) -> None:
        item["publish"] = False
        if bundle is not None:
            bundle["publish"] = False
            for field in text_fields:
                bundle[field] = ""
            for field in stale_surface_fields:
                bundle[field] = ""

    def _has_observed_move(item: dict[str, Any], bundle: dict[str, Any] | None) -> bool:
        symbol = str(item.get("symbol") or (bundle or {}).get("symbol") or "").upper().strip()
        symbols = [
            str(value or "").upper().strip()
            for value in list(item.get("supporting_symbols") or (bundle or {}).get("supporting_symbols") or [])
            if str(value or "").strip()
        ]
        change = pd.to_numeric(item.get("change_pct") or (bundle or {}).get("change_pct"), errors="coerce")
        direction = str(item.get("direction") or (bundle or {}).get("direction") or "").strip()
        return bool(symbol or symbols) and (pd.notna(change) or bool(direction))

    def _has_public_value(item: dict[str, Any], bundle: dict[str, Any] | None) -> bool:
        if _has_bundle_evidence(bundle):
            return True
        for field in [
            "surface_summary_text",
            "what_happened_text",
            "what_changed_text",
            "affected_assets_summary_text",
            "business_context_text",
            "surface_business_context_text",
            "watch_next_text",
            "source_summary",
        ]:
            if clean_attention_text(item.get(field) or (bundle or {}).get(field)):
                return True
        return _has_observed_move(item, bundle)

    def _route_as_unresolved_or_drop(
        item: dict[str, Any],
        bundle: dict[str, Any] | None,
        *,
        current_section: str,
        current_items: list[dict[str, Any]],
        routed_unresolved_items: list[dict[str, Any]],
    ) -> bool:
        if not _has_current_public_driver(item, bundle) and not _has_zopedia_business_context(item, bundle):
            _mark_unpublished(item, bundle)
            return False
        if not _has_public_value(item, bundle):
            _mark_unpublished(item, bundle)
            return False
        routed = _as_unresolved_item(item, bundle)
        routed["publish"] = True
        routed["public_surface_reviewed"] = True
        routed["public_surface_review_note"] = ""
        if bundle is not None:
            bundle["publish"] = True
            bundle["public_surface_reviewed"] = True
            bundle["public_surface_review_note"] = ""
        if current_section == "unresolved_large_moves":
            current_items.append(routed)
        else:
            routed_unresolved_items.append(routed)
        return True

    routed_unresolved_items: list[dict[str, Any]] = []
    for section in ["top_events", "must_read_movers", "unresolved_large_moves"]:
        items: list[dict[str, Any]] = []
        for index, raw_item in enumerate(list(updated_payload.get(section) or [])):
            item = dict(raw_item or {})
            bundle_id = str(item.get("bundle_id") or "").strip()
            revision = revisions.get((section, index, bundle_id)) or revisions.get((section, index, ""))
            bundle = updated_bundle_map.get(bundle_id) if bundle_id else None
            if not revision:
                cause_status = str(item.get("surface_cause_status") or item.get("cause_status") or "").strip().lower()
                if section != "unresolved_large_moves" and cause_status == "unresolved":
                    if _route_as_unresolved_or_drop(
                        item,
                        bundle,
                        current_section=section,
                        current_items=items,
                        routed_unresolved_items=routed_unresolved_items,
                    ):
                        changed += 1
                    else:
                        dropped += 1
                    continue
                if not _has_current_public_driver(item, bundle) and not _has_zopedia_business_context(item, bundle):
                    if _route_as_unresolved_or_drop(
                        item,
                        bundle,
                        current_section=section,
                        current_items=items,
                        routed_unresolved_items=routed_unresolved_items,
                    ):
                        changed += 1
                    else:
                        dropped += 1
                    continue
                if _drop_public_top_event(section, item, bundle):
                    if _route_as_unresolved_or_drop(
                        item,
                        bundle,
                        current_section=section,
                        current_items=items,
                        routed_unresolved_items=routed_unresolved_items,
                    ):
                        changed += 1
                    else:
                        dropped += 1
                    continue
                if _suppress_stale_driver(item, bundle):
                    if _route_as_unresolved_or_drop(
                        item,
                        bundle,
                        current_section=section,
                        current_items=items,
                        routed_unresolved_items=routed_unresolved_items,
                    ):
                        changed += 1
                    else:
                        dropped += 1
                    continue
                items.append(item)
                continue
            if not bool(revision.get("publish")):
                if _route_as_unresolved_or_drop(
                    item,
                    bundle,
                    current_section=section,
                    current_items=items,
                    routed_unresolved_items=routed_unresolved_items,
                ):
                    changed += 1
                else:
                    dropped += 1
                continue
            for field in text_fields:
                value = clean_attention_text(revision.get(field))
                item[field] = value
                if bundle is not None:
                    bundle[field] = value
            surface_summary = clean_attention_text(revision.get("surface_summary_text"))
            surface_fields = {
                "surface_summary_text": surface_summary,
                "surface_what_changed_text": clean_attention_text(revision.get("what_changed_text")),
                "surface_why_text": clean_attention_text(
                    revision.get("why_happened_text") or revision.get("why_now_text")
                ),
                "surface_what_else_moved_text": clean_attention_text(revision.get("what_else_moved_text")),
                "surface_business_context_text": clean_attention_text(revision.get("business_context_text")),
                "surface_source_summary": "",
            }
            cause_status = str(revision.get("cause_status") or "").strip().lower()
            if cause_status in {"supported", "partial", "unresolved"}:
                item["cause_status"] = cause_status
                item["surface_cause_status"] = cause_status
                if bundle is not None:
                    bundle["cause_status"] = cause_status
                    bundle["surface_cause_status"] = cause_status
            for field, value in surface_fields.items():
                item[field] = value
                if bundle is not None:
                    bundle[field] = value
            _neutralize_stale_unresolved_title(item, bundle)
            public_title = clean_attention_text(item.get("event_title") or item.get("headline"))
            if _suppress_stale_driver(item, bundle):
                if _route_as_unresolved_or_drop(
                    item,
                    bundle,
                    current_section=section,
                    current_items=items,
                    routed_unresolved_items=routed_unresolved_items,
                ):
                    changed += 1
                else:
                    dropped += 1
                continue
            if _drop_public_top_event(section, item, bundle):
                if _route_as_unresolved_or_drop(
                    item,
                    bundle,
                    current_section=section,
                    current_items=items,
                    routed_unresolved_items=routed_unresolved_items,
                ):
                    changed += 1
                else:
                    dropped += 1
                continue
            if not public_title:
                dropped += 1
                _mark_unpublished(item, bundle)
                continue
            item["public_surface_reviewed"] = True
            item["public_surface_review_note"] = ""
            if bundle is not None:
                bundle["public_surface_reviewed"] = True
                bundle["public_surface_review_note"] = ""
            changed += 1
            items.append(item)
        updated_payload[section] = items
    if routed_unresolved_items:
        existing = list(updated_payload.get("unresolved_large_moves") or [])
        updated_payload["unresolved_large_moves"] = routed_unresolved_items + existing

    coverage = dict(updated_payload.get("coverage_summary") or {})
    coverage["public_surface_quality_reviewed_count"] = changed + dropped
    coverage["public_surface_quality_changed_count"] = changed
    coverage["public_surface_quality_dropped_count"] = dropped
    updated_payload["coverage_summary"] = coverage
    return updated_payload, updated_bundle_map, {"changed": changed, "dropped": dropped, "reviewed": changed + dropped}


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
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    review_items = _home_surface_quality_items(
        payload,
        bundle_map,
        enrichment_rows=enrichment_rows,
    )
    if not review_items:
        return payload, bundle_map, {"status": "skipped", "reason": "no public Home items"}
    query = (
        "You are the Zopedia/AQL public-surface editor for the Attention Home feed. "
        f"As-of timestamp: {_zopedia_asof_text(asof_time_utc)}. "
        "Review the public text that will be shown on Home. Use only the supplied item data, business context, "
        "Zopedia enrichment text, and source summaries. Preserve useful business, sector, macro, peer, customer, "
        "supplier, policy, financing, commodity, or liquidity explanations. Rewrite titles and bodies so they say "
        "what is supported. Do not invent facts. Do not turn missing evidence into public prose. If an item has an "
        "observed move but the reason is not supported, keep the observed move in the title/body, clear the why fields, "
        "and put a concrete watch-next evidence slot in watch_next_text. Do not set publish=false just because the "
        "driver is partial, unresolved, or thin; a useful observed move with business, sector, macro, peer, source, "
        "or watch-next value should remain publish=true and be written as partial or unresolved. If an item has only "
        "a price move with no substantive business, sector, macro, peer, source, or watch-next value, set publish=false. "
        "Use the supplied freshness_quality, same_day_evidence_count, and evidence_trace as hard evidence boundaries. "
        "Evidence is current enough for a public driver when same_day_evidence_count is positive, an evidence_trace "
        "claim has freshness_class=current or same_day, or a source is dated within roughly the current market window "
        "(about 72 hours before the as-of timestamp). Older articles can explain business sensitivity but cannot be "
        "written as today's driver. Undated evidence may support a cautious partial read only when the claim itself "
        "is specific and not contradicted by dated stale sources. If evidence is only old/stale, do not publish a "
        "supported-driver card: rewrite it as an unresolved observed move with why fields empty and a concrete "
        "watch-next evidence target. Use publish=false only when the item has no useful observed move, business "
        "context, sector/peer context, source context, or watch-next value. "
        "Do not put stale causes in titles. "
        "Use supplied symbols when a title would otherwise be vague; do not write titles like 'one stock' or "
        "'some companies' when the item has specific tickers or a named group. "
        "Do not copy supplied wording that says or implies the reason is missing, unclear, absent, unconfirmed, or "
        "unavailable. That rule applies to every returned display field, including what_else_moved_text, "
        "affected_assets_summary_text, business_context_text, surface_summary_text, and watch_next_text. Treat "
        "absence as structured state: clear unsupported why fields, use watch_next_text for the specific evidence "
        "needed, or publish=false when the item has no useful investor context. "
        "Evidence counts are supplied for each item. If a top_events item has no evidence, no same-day evidence, "
        "and no specific business or macro transmission mechanism in the supplied context, do not leave it as a "
        "supported top event; keep it only as partial/unresolved if it still has a useful observed move or context, "
        "otherwise publish=false. "
        "Do not keep an unresolved item in top_events; unresolved items belong off the public Home top-event rail "
        "unless there is a concrete evidence target and the item is moved by the pipeline to an unresolved section. "
        "A public card is invalid if it sounds like a statistical recap, raw tool report, or an explanation of what "
        "was not found. Every published item should help an investor understand what changed, why it may matter, "
        "or what specific evidence would resolve it next. Set cause_status=supported only when the driver is "
        "directly supported, partial when the card has useful business/sector context but not a fully resolved "
        "driver, and unresolved only when why fields are empty and the item still has a concrete watch-next "
        "evidence target. Keep review_note operational and non-public; it must not contain the absent or unclear "
        "reason wording that would be invalid in product copy.\n\n"
        "Return one item for every supplied item, using the same section, index, bundle_id, and symbol.\n\n"
        "Home surface items:\n"
        f"{json.dumps(review_items, ensure_ascii=True, sort_keys=True, default=str)}"
    )
    result = _call_with_timeout(
        "attention-home-public-surface-quality",
        timeout_seconds,
        lambda: run_aql_zopedia_structured_agent(
            query=query,
            schema_name="attention_home_public_surface_quality",
            schema=_HOME_SURFACE_QUALITY_SCHEMA,
            task="attention_home_public_surface_quality",
            surface="attention_home.public_surface_quality",
            max_tool_calls=0,
            persist_findings=False,
        ),
    )
    if not isinstance(result, dict) or str(result.get("status") or "") != "completed":
        raise AttentionHomeBuildError(
            f"Home public-surface quality review failed: {str((result or {}).get('error') or 'unknown error')}"
        )
    quality_payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    if not bool(quality_payload.get("accepted")):
        issues = [str(item).strip() for item in list(quality_payload.get("issues") or []) if str(item).strip()]
        raise AttentionHomeBuildError(
            "Home public-surface quality review rejected the payload: "
            + ("; ".join(issues[:5]) if issues else "no issue details")
        )
    updated_payload, updated_bundle_map, counts = _apply_home_surface_quality_items(
        payload,
        bundle_map,
        list(quality_payload.get("items") or []),
    )
    return updated_payload, updated_bundle_map, {
        "status": "completed",
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
        print("[warn] attention-home-build skipped: missing mover inputs")
        return {}
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
        print("[warn] attention-home-build skipped: shortlist empty")
        return {}
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
    entity_master = build_attention_entity_master(shortlist, taxonomy_frame=taxonomy_labels_frame)
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
        context_payloads=context_payloads,
        entity_master=entity_master,
        topology_universe_frame=taxonomy_labels_frame,
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
    market_summary_row = next(
        (
            row
            for row in enrichment_rows
            if isinstance(row, dict)
            and str(row.get("symbol") or "").upper().strip() == ZOPEDIA_MARKET_SUMMARY_KEY
        ),
        {},
    )
    market_summary_payload = _homepage_summary_payload_from_market_summary_row(market_summary_row)
    if market_summary_payload:
        payload["homepage_summary"] = market_summary_payload
    elif not (
        isinstance(payload.get("homepage_summary"), dict)
        and str((payload.get("homepage_summary") or {}).get("summary_text") or "").strip()
    ):
        payload.pop("homepage_summary", None)

    payload, artifacts.bundle_map = _attach_zopedia_enrichment_to_payload(
        payload,
        artifacts.bundle_map,
        enrichment_rows,
    )
    print("[info] attention-home-build reviewing public Home surface with Zopedia")
    payload, artifacts.bundle_map, public_surface_review = _review_home_public_surface(
        payload,
        artifacts.bundle_map,
        enrichment_rows=enrichment_rows,
        asof_time_utc=ctx.asof,
        timeout_seconds=_home_surface_quality_timeout_seconds(),
    )
    print(
        "[info] attention-home-build public Home surface review: "
        f"status={public_surface_review.get('status')} "
        f"reviewed={public_surface_review.get('reviewed', 0)} "
        f"changed={public_surface_review.get('changed', 0)} "
        f"dropped={public_surface_review.get('dropped', 0)}"
    )
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

    def _materialize_ticker_snapshots() -> pd.DataFrame:
        print(f"[info] attention-home-build materializing ticker snapshots: symbols={len(snapshot_symbols)}")
        return build_attention_ticker_snapshot_frame(
            snapshot_symbols,
            price_history_frame=price_history_frame,
            universe_snapshot_frame=universe_snapshot_frame,
            asof_time_utc=ctx.asof,
            run_id=ctx.run_id,
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
        futures = {executor.submit(fn): name for name, fn in parallel_tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                frame = future.result()
            except Exception as exc:
                print(f"[warn] attention-home-build materialization failed dataset={name}: {type(exc).__name__}: {exc}")
                frame = pd.DataFrame()
            artifacts.frames[name] = frame
            print(f"[info] attention-home-build materialized {name} rows={len(frame)}")

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
        job_progress_fn(
            ctx,
            conn,
            stage="research_candidates",
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
    return persist_frames


__all__ = [
    "AttentionHomeBuildError",
    "build_attention_home_output_frames",
    "run_attention_home_build",
]
