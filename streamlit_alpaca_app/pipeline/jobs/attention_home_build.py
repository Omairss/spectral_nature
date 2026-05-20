from __future__ import annotations

import json
import os
import queue
import threading
from typing import Any, Callable

import pandas as pd

from services.attention_agentic import build_bottom_up_attention_artifacts, search_symbol_news_payload
from services.attention_home_summary import build_attention_home_summary_payload
from services.aql_zopedia_engine import (
    attach_aql_zopedia_summary_audio as attach_attention_home_summary_audio,
    build_aql_zopedia_attention_home_summary_with_trace as build_attention_agentic_summary_with_trace,
    load_aql_zopedia_llm_client,
)
from services.attention_home_1d import build_attention_entity_master, resolve_macro_anchor_symbols, shortlist_attention_symbols_1d
from services.attention_materialized import bars_by_symbol_from_price_history, serialize_attention_home_payload, serialize_attention_research_bundles
from services.attention_ticker_snapshots import (
    build_attention_ticker_background_snapshot_frame,
    build_attention_ticker_snapshot_frame,
    collect_attention_ticker_symbols,
    deserialize_attention_ticker_background_frame,
)
from services.elevenlabs_tts import ElevenLabsTTSAPIError
from services.fred import format_fred_delta, format_fred_value
from services.knowledge_graph_proposals import build_attention_knowledge_graph_proposals
from services.json_utils import to_list
from services.llm import LLMAPIError, load_embedding_client
from services.market import business_focus_options, business_focus_universe
from services.market_opportunity import build_market_opportunity_feed, build_materialized_market_opportunity_feeds
from services.page_agentic_summary import (
    broad_economy_summary_context,
    build_materialized_page_agentic_summary_row,
    build_page_agentic_summary,
    build_unavailable_page_agentic_summary,
    market_summary_context,
    stock_summary_context,
)
from services.pipeline_store import load_latest_dataset_frame


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
    return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def _attention_home_research_limit() -> int:
    raw = (os.getenv("ATTENTION_HOME_RESEARCH_LIMIT") or "12").strip()
    try:
        parsed = int(raw)
    except Exception:
        parsed = 12
    return max(parsed, 1)


def _attention_home_search_backfill_limit() -> int:
    raw = (os.getenv("ATTENTION_HOME_SEARCH_BACKFILL_LIMIT") or "100").strip()
    try:
        parsed = int(raw)
    except Exception:
        parsed = 100
    return max(parsed, 1)


def _env_positive_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        parsed = int(raw)
    except Exception:
        parsed = default
    return max(parsed, 1)


def _attention_home_summary_timeout_seconds() -> int:
    return _env_positive_int("ATTENTION_HOME_SUMMARY_TIMEOUT_SECONDS", 300)


def _page_agentic_summary_timeout_seconds() -> int:
    return _env_positive_int("PAGE_AGENTIC_SUMMARY_TIMEOUT_SECONDS", 120)


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


def _build_materialized_homepage_summary(
    payload: dict[str, Any],
    *,
    llm_client: Any | None = None,
    embedding_client: Any | None = None,
) -> dict[str, Any]:
    summary_payload: dict[str, Any]
    if llm_client is not None:
        try:
            print("[info] attention-home-build homepage AQL summary starting")
            summary_payload = _call_with_timeout(
                "attention homepage AQL summary",
                _attention_home_summary_timeout_seconds(),
                lambda: build_attention_agentic_summary_with_trace(
                    payload,
                    llm_client=llm_client,
                    embedding_client=embedding_client,
                )[0],
            )
            print("[info] attention-home-build homepage AQL summary completed")
        except Exception as exc:
            print(f"[warn] attention-home-build agentic summary failed, falling back: {type(exc).__name__}: {exc}")
            summary_payload = build_attention_home_summary_payload(payload)
    else:
        summary_payload = build_attention_home_summary_payload(payload)
    try:
        return attach_attention_home_summary_audio(summary_payload)
    except ElevenLabsTTSAPIError as exc:
        print(f"[warn] attention-home-build ElevenLabs narration unavailable: {exc}")
        return summary_payload
    except Exception as exc:
        print(f"[warn] attention-home-build unexpected ElevenLabs narration error: {type(exc).__name__}: {exc}")
        return summary_payload


def _build_materialized_homepage_summary_with_trace(
    payload: dict[str, Any],
    *,
    llm_client: Any | None = None,
    embedding_client: Any | None = None,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    summary_trace_frames: dict[str, pd.DataFrame] = {}
    summary_payload: dict[str, Any]
    if llm_client is not None:
        try:
            print("[info] attention-home-build homepage AQL summary starting")
            summary_payload, summary_trace_frames = _call_with_timeout(
                "attention homepage AQL summary",
                _attention_home_summary_timeout_seconds(),
                lambda: build_attention_agentic_summary_with_trace(
                    payload,
                    llm_client=llm_client,
                    embedding_client=embedding_client,
                ),
            )
            print("[info] attention-home-build homepage AQL summary completed")
        except Exception as exc:
            print(f"[warn] attention-home-build agentic summary failed, falling back: {type(exc).__name__}: {exc}")
            summary_payload = build_attention_home_summary_payload(payload)
            summary_trace_frames = {}
    else:
        summary_payload = build_attention_home_summary_payload(payload)
    try:
        summary_payload = attach_attention_home_summary_audio(summary_payload)
    except ElevenLabsTTSAPIError as exc:
        print(f"[warn] attention-home-build ElevenLabs narration unavailable: {exc}")
    except Exception as exc:
        print(f"[warn] attention-home-build unexpected ElevenLabs narration error: {type(exc).__name__}: {exc}")
    return summary_payload, summary_trace_frames


def _page_agentic_stock_summary_limit() -> int:
    raw = (os.getenv("PAGE_AGENTIC_STOCK_SUMMARY_LIMIT") or "4").strip()
    try:
        return max(int(raw), 0)
    except Exception:
        return 4


def _fred_page_summary_lookback_years() -> int:
    raw = (os.getenv("FRED_LOOKBACK_YEARS") or "10").strip()
    try:
        return max(int(raw), 1)
    except Exception:
        return 10


def _market_opportunity_focus_symbol_map() -> dict[str, list[str]]:
    focus_map: dict[str, list[str]] = {}
    for focus in business_focus_options():
        focus_label = str(focus or "").strip()
        if not focus_label:
            continue
        try:
            focus_map[focus_label] = [
                _normalize_symbol(symbol)
                for symbol in business_focus_universe(focus_label)
                if _normalize_symbol(symbol)
            ]
        except Exception:
            focus_map[focus_label] = []
    focus_map.setdefault("All Market", [])
    return focus_map


def _fred_overview_for_page_summary(fred_summary_frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(fred_summary_frame, pd.DataFrame) or fred_summary_frame.empty:
        return pd.DataFrame()
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
    fred_summary_frame: pd.DataFrame,
    fred_release_index_frame: pd.DataFrame,
    ticker_background_frame: pd.DataFrame,
    technical_signals_latest_frame: pd.DataFrame,
    universe_snapshot_frame: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
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
        rows.append(
            _materialized_page_summary_row(
                surface="Market Explorer",
                context=market_context,
                llm_client=llm_client,
                generated_at_utc=ctx.asof,
                run_id=ctx.run_id,
                context_label="All Market / 1 Month",
            )
        )

    fred_overview = _fred_overview_for_page_summary(fred_summary_frame)
    if not fred_overview.empty:
        broad_context = broad_economy_summary_context(
            overview=fred_overview,
            release_index=fred_release_index_frame,
            lookback_years=_fred_page_summary_lookback_years(),
        )
        rows.append(
            _materialized_page_summary_row(
                surface="Broad Economy",
                context=broad_context,
                llm_client=llm_client,
                generated_at_utc=ctx.asof,
                run_id=ctx.run_id,
                context_label="Latest FRED snapshot",
            )
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
            rows.append(
                _materialized_page_summary_row(
                    surface="Stock Investigator",
                    context=stock_context,
                    llm_client=llm_client,
                    generated_at_utc=ctx.asof,
                    run_id=ctx.run_id,
                    ticker=symbol,
                    context_label=symbol,
                )
            )

    return pd.DataFrame(rows)


_TRACE_DEDUPE_KEY_BY_DATASET: dict[str, str] = {
    "attention_search_requests": "query_id",
    "attention_search_results": "result_id",
    "attention_source_documents": "document_id",
    "attention_evidence_chunks": "chunk_id",
    "attention_claims": "claim_id",
    "aql_zopedia_engine_runs": "run_id",
}


def _merge_trace_frame(existing: pd.DataFrame | None, extra: pd.DataFrame | None, *, dataset_name: str) -> pd.DataFrame:
    existing_frame = existing.copy() if isinstance(existing, pd.DataFrame) else pd.DataFrame()
    extra_frame = extra.copy() if isinstance(extra, pd.DataFrame) else pd.DataFrame()
    if existing_frame.empty:
        return extra_frame.reset_index(drop=True) if not extra_frame.empty else existing_frame
    if extra_frame.empty:
        return existing_frame.reset_index(drop=True)
    merged = pd.concat([existing_frame, extra_frame], ignore_index=True, sort=False)
    dedupe_key = _TRACE_DEDUPE_KEY_BY_DATASET.get(dataset_name)
    if dedupe_key and dedupe_key in merged.columns:
        merged = merged.drop_duplicates(subset=[dedupe_key], keep="first")
    return merged.reset_index(drop=True)


def _merge_summary_trace_frames(
    frames: dict[str, pd.DataFrame],
    summary_trace_frames: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    merged = dict(frames or {})
    for dataset_name in (
        "attention_search_requests",
        "attention_search_results",
        "attention_source_documents",
        "attention_evidence_chunks",
        "attention_claims",
        "aql_zopedia_engine_runs",
    ):
        merged[dataset_name] = _merge_trace_frame(
            merged.get(dataset_name, pd.DataFrame()),
            summary_trace_frames.get(dataset_name, pd.DataFrame()) if isinstance(summary_trace_frames, dict) else pd.DataFrame(),
            dataset_name=dataset_name,
        )
    return merged



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
) -> dict[str, dict[str, Any]]:
    normalized_symbols = [_normalize_symbol(symbol) for symbol in symbols if str(symbol or "").strip()]
    normalized_symbols = [symbol for symbol in normalized_symbols if symbol]
    if not isinstance(news_frame, pd.DataFrame) or news_frame.empty or not normalized_symbols:
        return {}

    rows_by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in normalized_symbols}
    frame = news_frame.copy()
    if "published_at" in frame.columns:
        frame["published_at"] = pd.to_datetime(frame["published_at"], utc=True, errors="coerce")

    for _, row in frame.iterrows():
        row_symbols = _normalize_symbol_list(row.get("symbols"))
        if not row_symbols:
            continue
        article = {
            "headline": str(row.get("headline") or "").strip(),
            "summary": str(row.get("summary") or row.get("description") or "").strip(),
            "description": str(row.get("description") or row.get("summary") or "").strip(),
            "source": str(row.get("source") or "").strip(),
            "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
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
            articles["published_at"] = pd.to_datetime(articles["published_at"], utc=True, errors="coerce")
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

    backfilled: dict[str, dict[str, Any]] = {}
    for symbol in normalized_symbols:
        if _news_payload_has_articles(existing_payloads.get(symbol)):
            continue
        try:
            payload = search_symbol_news_payload(
                symbol,
                company_name=str((company_name_by_symbol or {}).get(symbol) or "").strip(),
                max_results=max(int(limit), 1),
            )
        except Exception as exc:
            print(f"[warn] attention-home-build news backfill failed symbol={symbol}: {type(exc).__name__}: {exc}")
            continue
        if _news_payload_has_articles(payload) or str(payload.get("fallback_summary") or "").strip():
            backfilled[symbol] = payload
    return backfilled


def _materialize_news_payload_frame(payloads: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, payload in (payloads or {}).items():
        articles = payload.get("articles")
        payload_source = str(payload.get("source") or "").strip()
        if not isinstance(articles, pd.DataFrame) or articles.empty:
            continue
        scoped = articles.copy()
        if "published_at" in scoped.columns:
            scoped["published_at"] = pd.to_datetime(scoped["published_at"], utc=True, errors="coerce")
        for _, row in scoped.iterrows():
            rows.append(
                {
                    "symbol": symbol,
                    "symbols": [symbol],
                    "headline": str(row.get("headline") or "").strip(),
                    "summary": str(row.get("summary") or row.get("description") or "").strip(),
                    "description": str(row.get("description") or row.get("summary") or "").strip(),
                    "source": str(row.get("source") or payload_source or "").strip(),
                    "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
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
        out["published_at"] = pd.to_datetime(out["published_at"], utc=True, errors="coerce")
        out = out.sort_values("published_at", ascending=False, na_position="last")
    return out.drop_duplicates(subset=["symbol", "headline", "url"], keep="first").reset_index(drop=True)


def _merge_news_frames(*frames: pd.DataFrame) -> pd.DataFrame:
    parts = [frame.copy() for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
    if not parts:
        return pd.DataFrame()
    merged = pd.concat(parts, ignore_index=True, sort=False)
    if "published_at" in merged.columns:
        merged["published_at"] = pd.to_datetime(merged["published_at"], utc=True, errors="coerce")
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
        fallback_summary = str(payload.get("fallback_summary") or "").strip()
        articles = payload.get("articles")
        if isinstance(articles, pd.DataFrame) and not articles.empty:
            scoped = articles.copy()
            if "published_at" in scoped.columns:
                scoped["published_at"] = pd.to_datetime(scoped["published_at"], utc=True, errors="coerce")
            for _, row in scoped.iterrows():
                rows.append(
                    {
                        "symbol": symbol,
                        "row_type": "article",
                        "headline": str(row.get("headline") or "").strip(),
                        "summary": str(row.get("summary") or row.get("description") or "").strip(),
                        "source": str(row.get("source") or source or "").strip(),
                        "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
                        "url": str(row.get("url") or "").strip(),
                        "payload_source": source,
                        "fallback_summary": "",
                        "asof_time_utc": asof_text,
                    }
                )
        elif fallback_summary:
            rows.append(
                {
                    "symbol": symbol,
                    "row_type": "summary",
                    "headline": "",
                    "summary": fallback_summary,
                    "source": source,
                    "published_at": pd.NaT,
                    "url": "",
                    "payload_source": source,
                    "fallback_summary": fallback_summary,
                    "asof_time_utc": asof_text,
                }
            )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    if "published_at" in out.columns:
        out["published_at"] = pd.to_datetime(out["published_at"], utc=True, errors="coerce")
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

    universe_snapshot_frame = load_materialized_frame_fn("universe_snapshot")
    taxonomy_labels_frame = load_materialized_frame_fn("entity_taxonomy_labels")
    company_name_by_symbol = _company_name_map(universe_snapshot_frame)
    entity_master = build_attention_entity_master(shortlist)
    bars_by_symbol = bars_by_symbol_from_price_history(
        price_history_frame,
        shortlist,
        asof_time_utc=ctx.asof,
        lookback_days=120,
    )
    news_payloads = _news_payloads_from_articles_frame(news_frame, symbols=shortlist, limit=8)
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
    fred_summary_frame = load_materialized_frame_fn("fred_summary")
    yield_curve_facts_frame = load_materialized_frame_fn("yield_curve_facts_1d")
    embedding_client = load_embedding_client()

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
        top_events_limit=5,
        must_read_limit=10,
        unresolved_limit=5,
        research_limit=research_limit,
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

    homepage_summary, summary_trace_frames = _build_materialized_homepage_summary_with_trace(
        payload,
        llm_client=llm_client,
        embedding_client=embedding_client,
    )
    payload["homepage_summary"] = homepage_summary
    artifacts.frames = _merge_summary_trace_frames(artifacts.frames, summary_trace_frames)
    artifacts.frames["knowledge_graph_update_proposals"] = build_attention_knowledge_graph_proposals(
        run_id=ctx.run_id,
        asof_time_utc=ctx.asof,
        claims_frame=artifacts.frames.get("attention_claims", pd.DataFrame()),
        macro_edges_frame=artifacts.frames.get("macro_causal_graph_edges_v1", pd.DataFrame()),
        relationship_checks_frame=artifacts.frames.get("macro_relationship_checks_1d", pd.DataFrame()),
    )
    artifacts.frames["attention_home_snapshots_1d"] = serialize_attention_home_payload(payload)
    if "attention_bundle_snapshots" not in artifacts.frames:
        artifacts.frames["attention_bundle_snapshots"] = serialize_attention_research_bundles(
            artifacts.bundle_map,
            generated_at_utc=ctx.asof,
        )

    snapshot_symbols = collect_attention_ticker_symbols(payload, artifacts.bundle_map, max_symbols=120)
    merged_news_frame = _merge_news_frames(
        news_frame,
        _materialize_news_payload_frame(search_backfill_payloads),
    )
    artifacts.frames["attention_ticker_snapshots_1d"] = build_attention_ticker_snapshot_frame(
        snapshot_symbols,
        price_history_frame=price_history_frame,
        universe_snapshot_frame=universe_snapshot_frame,
        asof_time_utc=ctx.asof,
        run_id=ctx.run_id,
    )
    artifacts.frames["attention_ticker_background_snapshots"] = build_attention_ticker_background_snapshot_frame(
        snapshot_symbols,
        price_history_frame=price_history_frame,
        universe_snapshot_frame=universe_snapshot_frame,
        news_frame=merged_news_frame,
        attention_context_frame=attention_context_frame,
        asof_time_utc=ctx.asof,
        run_id=ctx.run_id,
    )
    momentum_profiles_frame = load_materialized_frame_fn("momentum_profiles")
    print("[info] attention-home-build materializing market_opportunity_feed")
    artifacts.frames["market_opportunity_feed"] = build_materialized_market_opportunity_feeds(
        movers=daily_movers,
        momentum=momentum_profiles_frame,
        name_map=_company_name_map(universe_snapshot_frame),
        focus_symbol_map=_market_opportunity_focus_symbol_map(),
        asof_time_utc=ctx.asof,
        run_id=ctx.run_id,
        limit=80,
    )
    print(
        "[info] attention-home-build materialized market_opportunity_feed "
        f"rows={len(artifacts.frames['market_opportunity_feed'])}"
    )

    try:
        print("[info] attention-home-build materializing page_agentic_summaries")
        artifacts.frames["page_agentic_summaries"] = _build_page_agentic_summary_frame(
            ctx=ctx,
            llm_client=llm_client,
            daily_movers=daily_movers,
            momentum_profiles=momentum_profiles_frame,
            fred_summary_frame=fred_summary_frame,
            fred_release_index_frame=load_materialized_frame_fn("fred_release_index"),
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

    try:
        llm_client = load_aql_zopedia_llm_client(surface="attention.home_build")
    except LLMAPIError as exc:
        print(f"[warn] attention home build LLM unavailable: {exc}")
        llm_client = None
    if llm_client is None:
        print("[warn] attention home build running without LLM; narratives will use heuristic fallbacks")

    job_progress_fn(
        ctx,
        conn,
        stage="loading_inputs",
        message="Loading attention build source datasets.",
        progress_pct=12.0,
    )
    research_limit = _attention_home_research_limit()
    job_progress_fn(
        ctx,
        conn,
        stage="building_narratives",
        message=f"Building attention narratives and graph from materialized inputs (research_limit={research_limit}).",
        progress_pct=18.0,
    )

    def _research_progress(index: int, total: int, candidate: dict[str, Any]) -> None:
        total_safe = max(int(total), 1)
        completed = max(min(int(index) - 1, total_safe), 0)
        progress_pct = 18.0 + (54.0 * completed / total_safe)
        symbol = _normalize_symbol(candidate.get("symbol"))
        message = f"Researching narrative candidate {min(index, total_safe)}/{total_safe}"
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
