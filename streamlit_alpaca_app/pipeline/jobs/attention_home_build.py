from __future__ import annotations

import json
import os
from typing import Any, Callable

import pandas as pd

from services.attention_agentic import build_bottom_up_attention_artifacts, search_symbol_news_payload
from services.attention_home_summary import (
    attach_attention_home_summary_audio,
    build_attention_home_summary_payload,
)
from services.attention_home_1d import build_attention_entity_master, resolve_macro_anchor_symbols, shortlist_attention_symbols_1d
from services.attention_materialized import bars_by_symbol_from_price_history, serialize_attention_home_payload, serialize_attention_research_bundles
from services.attention_ticker_snapshots import (
    build_attention_ticker_background_snapshot_frame,
    build_attention_ticker_snapshot_frame,
    collect_attention_ticker_symbols,
)
from services.elevenlabs_tts import ElevenLabsTTSAPIError
from services.llm import LLMAPIError, load_embedding_client, load_llm_client
from services.pipeline_store import load_latest_dataset_frame


PersistDatasetFn = Callable[[str, pd.DataFrame, Any, Any | None], None]
JobProgressFn = Callable[..., None]
LoadFrameFn = Callable[[str], pd.DataFrame]
ResearchProgressFn = Callable[[int, int, dict[str, Any]], None]


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


def _build_materialized_homepage_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary_payload = build_attention_home_summary_payload(payload)
    try:
        return attach_attention_home_summary_audio(summary_payload)
    except ElevenLabsTTSAPIError as exc:
        print(f"[warn] attention-home-build ElevenLabs narration unavailable: {exc}")
        return summary_payload
    except Exception as exc:
        print(f"[warn] attention-home-build unexpected ElevenLabs narration error: {type(exc).__name__}: {exc}")
        return summary_payload


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
    payload["homepage_summary"] = _build_materialized_homepage_summary(payload)
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
        llm_client = load_llm_client()
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

    persist_frames = build_attention_home_output_frames(
        ctx=ctx,
        daily_movers=load_materialized_frame_fn("daily_movers"),
        macro_movers=load_materialized_frame_fn("macro_anchor_daily_movers"),
        positions_frame=load_materialized_frame_fn("positions_snapshot"),
        price_history_frame=load_materialized_frame_fn("price_history"),
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
        job_progress_fn(
            ctx,
            conn,
            stage="skipped",
            message="Attention home build skipped because required inputs were unavailable.",
            progress_pct=100.0,
        )
        return {}

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
    "build_attention_home_output_frames",
    "run_attention_home_build",
]
