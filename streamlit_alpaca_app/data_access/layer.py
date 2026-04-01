from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any

import pandas as pd

from compute.analytics import performance_table
from compute.anomalies import AttentionConfig, attention_preset, build_attention_feed, build_attention_rollups, filter_attention_events, normalize_horizon, normalize_horizons
from compute.fred import build_fred_dashboard_from_pipeline
from compute.fundamentals import load_quarterly_fundamentals
from compute.ownership import normalize_share_fraction, project_account_view, project_portfolio_timeseries, project_positions_view
from compute.portfolio import build_portfolio_timeseries, compute_holding_roc, normalize_timeseries_view
from compute.treasury_yields import build_treasury_yield_facts_1d, build_treasury_yield_observations, build_treasury_yield_summary
from data_access.contracts import DataProvenance, ResolvedPayload
from services.alpaca_api import AlpacaAPI, AlpacaAPIError
from services.attention_agentic import build_bottom_up_attention_artifacts, search_symbol_news_payload
from services.attention_materialized import (
    deserialize_attention_home_payload,
    deserialize_attention_research_bundle_frame,
    deserialize_attention_ticker_background_frame,
    deserialize_attention_ticker_snapshot_frame,
)
from services.attention_home_1d import (
    build_attention_entity_master,
    resolve_macro_anchor_symbols,
    build_attention_home_1d,
    shortlist_attention_symbols_1d,
)
from services.company import load_asset_metadata, load_recent_news
from services.config import AppConfig, load_config
from services.data_cache import (
    cached_frame,
    cached_frame_dict,
    cached_fred_dashboard,
    cached_news_payload,
    cached_option_chain,
    cached_scalar_dict,
    dataset_scope,
)
from services.edgar import EdgarClient
from services.fred import load_fred_api_key, load_fred_dashboard
from services.llm import load_embedding_client, load_llm_client
from services.market import load_price_history, scan_commodity_regimes, scan_correlation_phase_shifts, scan_daily_movers, scan_momentum_profiles
from services.options import analyze_option_candidates, load_option_chain, load_option_surface, select_option_surface_window
from services.pipeline_store import latest_job_status_table, load_latest_dataset_frame, pipeline_store_configured, start_source_refresh_job
from services.treasury_yields import TreasuryYieldError, load_treasury_yield_curve
from services.universe import build_liquidity_ranked_equity_universe

try:
    from compute.signals import build_signal_frame, forecast_next_week, summarize_signal_frame
except Exception:
    build_signal_frame = None
    forecast_next_week = None
    summarize_signal_frame = None


def _make_api(cfg: AppConfig | None) -> AlpacaAPI:
    if cfg is None:
        raise AlpacaAPIError("Alpaca configuration is unavailable.")
    return AlpacaAPI(cfg)


def _alpaca_cache_scope(cfg: AppConfig) -> str:
    return dataset_scope("alpaca", cfg.alpaca_api_key)


def _fred_cache_scope(api_key: str) -> str:
    return dataset_scope("fred", api_key)


def _pipeline_details(metadata: Any | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    return {
        "dataset_name": metadata.dataset_name,
        "dataset_version_id": metadata.dataset_version_id,
        "blob_path": metadata.blob_path,
        "asof_time_utc": metadata.asof_time_utc,
        "ingested_at_utc": metadata.ingested_at_utc,
        "row_count": metadata.row_count,
    }


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


ATTENTION_HOME_SNAPSHOT_DATASETS = ("attention_home_snapshots_1d", "attention_home_1d")
ATTENTION_BUNDLE_SNAPSHOT_DATASETS = ("attention_bundle_snapshots", "attention_research_bundles")
ATTENTION_TICKER_SNAPSHOT_DATASETS = ("attention_ticker_snapshots_1d",)
ATTENTION_TICKER_BACKGROUND_DATASETS = ("attention_ticker_background_snapshots",)
ATTENTION_TRACE_DATASETS = (
    "attention_candidates_1d",
    "attention_research_plans",
    "attention_search_requests",
    "attention_search_results",
    "attention_source_documents",
    "attention_evidence_chunks",
    "attention_claims",
    "attention_candidate_graph",
    "attention_event_clusters_1d",
    "attention_ticker_snapshots_1d",
    "attention_ticker_background_snapshots",
    "attention_home_snapshots_1d",
    "attention_bundle_snapshots",
)

LEGACY_ATTENTION_TEXT_SNIPPETS = (
    "market-wide relief read",
    "renewed market stress",
    "rates-relief move broadens across the tape",
    "rates scare spreads across risk assets",
    "defensive bid strengthens",
    "defensive bid fades",
    "broad risk-on move takes hold",
    "broad risk-off move takes hold",
)

_ATTENTION_STAT_FIELDS = (
    "what_happened_text",
    "why_happened_text",
    "affected_assets_summary_text",
    "what_changed_text",
    "why_now_text",
    "what_else_moved_text",
    "surface_summary_text",
    "surface_what_changed_text",
    "surface_why_text",
    "surface_what_else_moved_text",
)

_ATTENTION_CAUSAL_PATTERNS = (
    r"\bbecause\b",
    r"\bdue to\b",
    r"\bafter\b",
    r"\bamid\b",
    r"\bdriven by\b",
    r"\bwhich (?:lifted|helped|pressured|hurt|weighed|boosted)\b",
    r"\bmargins?\b",
    r"\bdemand\b",
    r"\binflation\b",
    r"\bsupply\b",
    r"\bpricing\b",
    r"\bguidance\b",
)


@dataclass(frozen=True)
class DataAccessLayer:
    cfg: AppConfig | None = None
    fred_api_key: str | None = None
    materialized_only: bool = False

    @classmethod
    def from_environment(cls) -> "DataAccessLayer":
        return cls(cfg=load_config(), fred_api_key=load_fred_api_key())

    def _resolved(self, payload: Any, *, mode: str, datasets: tuple[str, ...], details: dict[str, Any] | None = None) -> ResolvedPayload:
        return ResolvedPayload(payload=payload, provenance=DataProvenance(mode=mode, datasets=datasets, details=details or {}))

    def _pipeline_frame(self, dataset_name: str) -> tuple[pd.DataFrame, dict[str, Any]]:
        frame, metadata = load_latest_dataset_frame(dataset_name)
        return frame, _pipeline_details(metadata)

    def _materialized_only_result(
        self,
        payload: Any,
        *,
        datasets: tuple[str, ...],
        details: dict[str, Any] | None = None,
    ) -> ResolvedPayload | None:
        if not self.materialized_only:
            return None
        merged = dict(details or {})
        merged.setdefault("materialized_only", True)
        merged.setdefault("warning", "materialized_only_presentation_layer")
        return self._resolved(payload, mode="materialized", datasets=datasets, details=merged)

    def _attention_candidate_dataset_name(self, dataset_name: str) -> str | None:
        dataset_key = str(dataset_name or "").strip()
        if dataset_key == "attention_feed":
            return "attention_candidates"
        if dataset_key == "commodity_attention_feed":
            return "commodity_attention_candidates"
        if dataset_key == "attention_rollups":
            return "attention_candidates"
        if dataset_key == "commodity_attention_rollups":
            return "commodity_attention_candidates"
        return None

    def _attention_config_from_params(
        self,
        *,
        sensitivity: str | None,
        residual_zscore_threshold: float | None,
        min_attention_score: float | None,
        high_priority_threshold: float | None,
    ) -> AttentionConfig:
        preset = attention_preset(sensitivity)
        return AttentionConfig(
            residual_zscore_threshold=float(residual_zscore_threshold if residual_zscore_threshold is not None else preset["residual_zscore_threshold"]),
            min_attention_score=float(min_attention_score if min_attention_score is not None else preset["min_attention_score"]),
            high_priority_threshold=float(high_priority_threshold if high_priority_threshold is not None else preset["high_priority_threshold"]),
        )

    def _decorate_runtime_commodity_rollups(self, rollups: pd.DataFrame) -> pd.DataFrame:
        if rollups.empty:
            return rollups
        out = rollups.copy()
        rollup_types = out["rollup_type"].astype(str).str.lower()
        market_mask = rollup_types == "market"
        portfolio_mask = rollup_types == "portfolio"
        focus_mask = rollup_types == "business_lens"
        out.loc[market_mask, "rollup_type"] = "commodity_market"
        out.loc[market_mask, "rollup_id"] = "commodity_market"
        out.loc[market_mask, "rollup_name"] = "Commodities"
        out.loc[portfolio_mask, "rollup_id"] = "commodity_portfolio"
        out.loc[portfolio_mask, "rollup_name"] = "Commodity Portfolio"
        out.loc[focus_mask, "rollup_type"] = "commodity_focus"
        return out

    def _should_try_pipeline(self, force_refresh: bool) -> bool:
        return (not force_refresh) and pipeline_store_configured()

    def _try_pipeline_frame(self, dataset_name: str, *, force_refresh: bool) -> tuple[pd.DataFrame, dict[str, Any]] | None:
        if not self._should_try_pipeline(force_refresh):
            return None
        frame, details = self._pipeline_frame(dataset_name)
        if frame.empty and not details:
            return None
        return frame, details

    def _try_pipeline_frames(
        self,
        dataset_names: tuple[str, ...],
        *,
        force_refresh: bool,
    ) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, Any]]] | None:
        if not self._should_try_pipeline(force_refresh):
            return None

        frames: dict[str, pd.DataFrame] = {}
        details: dict[str, dict[str, Any]] = {}
        for dataset_name in dataset_names:
            frame, metadata = self._pipeline_frame(dataset_name)
            if frame.empty and not metadata:
                return None
            frames[dataset_name] = frame
            details[dataset_name] = metadata
        return frames, details

    def _first_materialized_frame(
        self,
        dataset_names: tuple[str, ...],
        *,
        force_refresh: bool,
    ) -> tuple[str, pd.DataFrame, dict[str, Any]] | None:
        for dataset_name in dataset_names:
            materialized = self._try_pipeline_frame(dataset_name, force_refresh=force_refresh)
            if materialized is None:
                continue
            frame, details = materialized
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                return dataset_name, frame, details
        return None

    def _contains_legacy_attention_text(self, value: Any) -> bool:
        text = str(value or "").strip().lower()
        if not text:
            return False
        return any(snippet in text for snippet in LEGACY_ATTENTION_TEXT_SNIPPETS)

    def _has_causal_language(self, value: Any) -> bool:
        text = str(value or "").strip().lower()
        if not text:
            return False
        return any(re.search(pattern, text) for pattern in _ATTENTION_CAUSAL_PATTERNS)

    def _looks_like_attention_stat_dump(self, value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        lowered = text.lower()
        pct_count = len(re.findall(r"[+\-]?\d+(?:\.\d+)?%", text))
        bps_count = len(re.findall(r"[+\-]?\d+(?:\.\d+)?\s*bps\b", lowered))
        ticker_count = len(re.findall(r"\b[A-Z]{2,5}\b", text))
        ticker_pct_pairs = len(re.findall(r"\b[A-Z]{2,5}\s*[+\-]\d+(?:\.\d+)?%", text))
        if re.search(r"\bup:\b|\bdown:\b", lowered):
            return True
        if ticker_pct_pairs >= 2:
            return True
        if pct_count + bps_count >= 4:
            return True
        if ticker_count >= 4 and pct_count + bps_count >= 3:
            return True
        if "treasury yields" in lowered and pct_count + bps_count >= 4 and not self._has_causal_language(text):
            return True
        return False

    def _payload_uses_stat_dump_copy(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict) or not payload:
            return False
        for section in ("top_events", "must_read_movers", "unresolved_large_moves"):
            for item in list(payload.get(section) or []):
                if not isinstance(item, dict):
                    continue
                for key in _ATTENTION_STAT_FIELDS:
                    if self._looks_like_attention_stat_dump(item.get(key)):
                        return True
        return False

    def _payload_uses_legacy_attention_titles(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict) or not payload:
            return False
        for event in list(payload.get("top_events") or []):
            if self._contains_legacy_attention_text(event.get("event_title")):
                return True
            if self._contains_legacy_attention_text(event.get("headline")):
                return True
        for mover in list(payload.get("must_read_movers") or []):
            if self._contains_legacy_attention_text(mover.get("headline")):
                return True
        return False

    def _bundle_uses_legacy_attention_titles(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict) or not payload:
            return False
        return any(
            self._contains_legacy_attention_text(payload.get(key))
            for key in ("event_title", "headline", "surface_summary_text", "why_happened_text", "why_now_text")
        )

    def _bundle_uses_stat_dump_copy(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict) or not payload:
            return False
        return any(self._looks_like_attention_stat_dump(payload.get(key)) for key in _ATTENTION_STAT_FIELDS)

    def _user_share_fraction(self, user_context: Any | None) -> float:
        if isinstance(user_context, dict):
            return normalize_share_fraction(user_context.get("share_fraction"))
        return normalize_share_fraction(getattr(user_context, "share_fraction", 0.0))

    def _user_can_view_full(self, user_context: Any | None) -> bool:
        if isinstance(user_context, dict):
            return bool(user_context.get("can_view_full_portfolio"))
        return bool(getattr(user_context, "can_view_full_portfolio", False))

    def _user_id(self, user_context: Any | None) -> str:
        if isinstance(user_context, dict):
            return str(user_context.get("user_id") or "")
        return str(getattr(user_context, "user_id", "") or "")

    def _attention_home_equity_universe(self, *, force_refresh: bool = False) -> list[str]:
        materialized = self._try_pipeline_frame("universe_snapshot", force_refresh=force_refresh)
        if materialized is not None:
            pipeline, _ = materialized
            if not pipeline.empty and "symbol" in pipeline.columns:
                values = [
                    str(value).upper().strip()
                    for value in pipeline["symbol"].dropna().astype(str).tolist()
                    if str(value).strip()
                ]
                if values:
                    return list(dict.fromkeys(values))[:1500]

        if self.materialized_only:
            return []
        if self.cfg is None:
            return []

        frame = cached_frame(
            "attention_home_equity_universe",
            f"{_alpaca_cache_scope(self.cfg)}__liquidity_1500",
            lambda: build_liquidity_ranked_equity_universe(
                _make_api(self.cfg),
                target_size=1500,
            ),
            force_refresh=force_refresh,
            version=1,
        )
        if not frame.empty and "symbol" in frame.columns:
            values = [
                str(value).upper().strip()
                for value in frame["symbol"].dropna().astype(str).tolist()
                if str(value).strip()
            ]
            if values:
                return list(dict.fromkeys(values))[:1500]
        return []

    def _resolve_web_search_news(self, ticker: str, *, company_name: str = "", force_refresh: bool = False) -> dict[str, Any]:
        target = str(ticker or "").upper().strip()
        if not target:
            return {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}

        cache_scope = f"{pd.Timestamp.utcnow().date().isoformat()}__{target}__{company_name.strip().lower()}"
        return cached_news_payload(
            "web_search_news",
            cache_scope,
            lambda: search_symbol_news_payload(target, company_name=company_name, max_results=8),
            force_refresh=force_refresh,
            version=1,
        )

    def _resolve_materialized_asset_metadata(self, ticker: str, *, force_refresh: bool = False) -> ResolvedPayload | None:
        target = str(ticker or "").upper().strip()
        if not target:
            return None

        universe_materialized = self._try_pipeline_frame("universe_snapshot", force_refresh=force_refresh)
        if universe_materialized is not None:
            frame, details = universe_materialized
            if not frame.empty and "symbol" in frame.columns:
                name_column = ""
                for candidate in ("security_name", "company_name", "name"):
                    if candidate in frame.columns:
                        name_column = candidate
                        break
                if name_column:
                    rows = frame.copy()
                    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
                    match = rows[rows["symbol"] == target].head(1)
                    if not match.empty:
                        name = _coerce_text(match.iloc[0].get(name_column))
                        if name:
                            return self._resolved(
                                {"symbol": target, "name": name},
                                mode="materialized",
                                datasets=("universe_snapshot",),
                                details={**details, "ticker": target},
                            )

        background = self.resolve_attention_ticker_background(target, force_refresh=force_refresh)
        company_name = _coerce_text((background.payload or {}).get("company_name"))
        if company_name:
            return self._resolved(
                {"symbol": target, "name": company_name},
                mode=background.provenance.mode,
                datasets=background.provenance.datasets,
                details={**background.provenance.details, "ticker": target},
            )
        return None

    def _resolve_materialized_recent_news_from_search(
        self,
        ticker: str,
        *,
        limit: int,
        force_refresh: bool = False,
    ) -> ResolvedPayload | None:
        target = str(ticker or "").upper().strip()
        materialized = self._try_pipeline_frame("attention_web_search_news", force_refresh=force_refresh)
        if materialized is None:
            return None
        frame, details = materialized
        if frame.empty or "symbol" not in frame.columns:
            return None

        rows = frame.copy()
        rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
        rows = rows[rows["symbol"] == target].copy()
        if rows.empty:
            return None

        if "published_at" in rows.columns:
            rows["published_at"] = pd.to_datetime(rows["published_at"], utc=True, errors="coerce")
            rows = rows.sort_values("published_at", ascending=False, na_position="last")

        row_types = rows.get("row_type", pd.Series(dtype=str)).astype(str).str.lower()
        article_rows = rows[row_types.ne("summary")].copy() if "row_type" in rows.columns else rows.copy()
        summary_rows = rows[row_types.eq("summary")].copy() if "row_type" in rows.columns else pd.DataFrame()
        fallback_summary = ""
        if not summary_rows.empty:
            for _, row in summary_rows.iterrows():
                candidate = _coerce_text(row.get("fallback_summary")) or _coerce_text(row.get("summary"))
                if candidate:
                    fallback_summary = candidate
                    break

        keep = [col for col in ["headline", "summary", "description", "published_at", "source", "url", "sentiment", "symbols"] if col in article_rows.columns]
        articles = article_rows[keep].head(limit).reset_index(drop=True) if keep else pd.DataFrame()
        if not articles.empty and "symbols" not in articles.columns:
            articles["symbols"] = [[target]] * len(articles)

        source_values = [
            _coerce_text(value)
            for value in rows.get("payload_source", pd.Series(dtype=object)).tolist()
            + rows.get("source", pd.Series(dtype=object)).tolist()
            if _coerce_text(value)
        ]
        source = "+".join(dict.fromkeys(source_values)) if source_values else "attention_web_search_news"
        if articles.empty and not fallback_summary:
            return None
        return self._resolved(
            {
                "articles": articles,
                "fallback_summary": fallback_summary or None,
                "source": source,
            },
            mode="materialized",
            datasets=("attention_web_search_news",),
            details={**details, "ticker": target, "limit": limit},
        )

    def _resolve_materialized_recent_news_from_background(
        self,
        ticker: str,
        *,
        limit: int,
        force_refresh: bool = False,
    ) -> ResolvedPayload | None:
        target = str(ticker or "").upper().strip()
        background = self.resolve_attention_ticker_background(target, force_refresh=force_refresh)
        payload = background.payload if isinstance(background.payload, dict) else {}
        recent_headlines = payload.get("recent_headlines", [])
        news_summary_lines = payload.get("news_summary_lines", [])

        article_rows: list[dict[str, Any]] = []
        if isinstance(recent_headlines, list):
            for item in recent_headlines[: max(int(limit), 1)]:
                if not isinstance(item, dict):
                    continue
                headline = _coerce_text(item.get("headline"))
                if not headline:
                    continue
                article_rows.append(
                    {
                        "headline": headline,
                        "summary": headline,
                        "description": headline,
                        "published_at": pd.to_datetime(item.get("published_at"), utc=True, errors="coerce"),
                        "source": _coerce_text(item.get("source")),
                        "url": _coerce_text(item.get("url")),
                        "symbols": [target],
                    }
                )
        fallback_summary = ""
        if isinstance(news_summary_lines, list):
            fallback_summary = "\n".join(
                str(item).strip()
                for item in news_summary_lines
                if str(item).strip()
            ).strip()

        articles = pd.DataFrame(article_rows)
        if isinstance(articles, pd.DataFrame) and not articles.empty and "published_at" in articles.columns:
            articles["published_at"] = pd.to_datetime(articles["published_at"], utc=True, errors="coerce")
            articles = articles.sort_values("published_at", ascending=False, na_position="last").reset_index(drop=True)

        if articles.empty and not fallback_summary:
            return None
        return self._resolved(
            {
                "articles": articles,
                "fallback_summary": fallback_summary or None,
                "source": "attention_ticker_background_snapshots",
            },
            mode=background.provenance.mode,
            datasets=background.provenance.datasets,
            details={**background.provenance.details, "ticker": target, "limit": limit},
        )

    def _resolve_attention_edgar_filings(
        self,
        symbols: list[str],
        *,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        normalized = [
            str(symbol or "").upper().strip()
            for symbol in symbols
            if str(symbol or "").strip()
        ]
        normalized = list(dict.fromkeys(normalized))
        if not normalized:
            return pd.DataFrame()

        cache_scope = f"{pd.Timestamp.utcnow().date().isoformat()}__{'-'.join(normalized[:12])}"
        return cached_frame(
            "attention_research_edgar",
            cache_scope,
            lambda: EdgarClient().load_recent_filings(
                normalized,
                days=180,
                max_filings_per_symbol=4,
                fetch_document_text=True,
                max_document_fetches_per_symbol=2,
            ),
            force_refresh=force_refresh,
            version=1,
        )

    def _combine_mover_frames(self, *frames: pd.DataFrame, macro_anchor_symbols: list[str] | None = None) -> pd.DataFrame:
        non_empty = [frame.copy() for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
        if not non_empty:
            return pd.DataFrame()
        out = pd.concat(non_empty, ignore_index=True, sort=False)
        if "symbol" in out.columns:
            out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
        for column in ["change_pct", "close", "prev_close", "volume", "dollar_volume"]:
            if column in out.columns:
                out[column] = pd.to_numeric(out[column], errors="coerce")
        if "dollar_volume" not in out.columns and {"close", "volume"}.issubset(set(out.columns)):
            out["dollar_volume"] = pd.to_numeric(out["close"], errors="coerce") * pd.to_numeric(out["volume"], errors="coerce")
        if "symbol" in out.columns:
            anchors = {str(symbol).upper().strip() for symbol in list(macro_anchor_symbols or []) if str(symbol).strip()}
            out["_priority"] = out["symbol"].isin(anchors).astype(int)
            out["_abs_move"] = pd.to_numeric(out.get("change_pct"), errors="coerce").abs()
            out = out.sort_values(
                ["_priority", "_abs_move", "dollar_volume", "symbol"],
                ascending=[False, False, False, True],
                na_position="last",
            )
            out = out.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
            out = out.drop(columns=["_priority", "_abs_move"], errors="ignore")
        return out

    def _resolve_live_attention_artifacts(self, *, force_refresh: bool) -> dict[str, Any]:
        if self.materialized_only:
            return {}
        cache_scope = (
            f"{_alpaca_cache_scope(self.cfg)}__{pd.Timestamp.utcnow().date().isoformat()}"
            if self.cfg is not None
            else f"missing-config__{pd.Timestamp.utcnow().date().isoformat()}"
        )

        def _fetch() -> dict[str, Any]:
            empty_payload = {
                "top_events": [],
                "must_read_movers": [],
                "unresolved_large_moves": [],
                "generated_at_utc": pd.Timestamp.utcnow().isoformat(),
                "coverage_summary": {
                    "candidate_count": 0,
                    "event_count": 0,
                    "must_read_count": 0,
                    "unresolved_count": 0,
                    "today_only": True,
                },
                "taxonomy_horizon_trends": [],
                "event_candidates_1d": [],
                "event_impacts_1d": [],
                "entity_master": [],
                "run_id": "",
            }
            if self.cfg is None:
                return {"home_payload": empty_payload, "bundle_map": {}, "run_id": ""}

            equity_universe = self._attention_home_equity_universe(force_refresh=force_refresh)
            macro_anchor_symbols = resolve_macro_anchor_symbols(equity_universe) if equity_universe else []
            holdings: list[str] = []
            try:
                positions = self.resolve_positions(force_refresh=force_refresh).payload
                if isinstance(positions, pd.DataFrame) and not positions.empty and "symbol" in positions.columns:
                    holdings = [
                        str(value).upper().strip()
                        for value in positions["symbol"].dropna().astype(str).tolist()
                        if str(value).strip()
                    ]
            except Exception:
                holdings = []

            equity_movers = self.resolve_daily_movers(symbols=equity_universe, force_refresh=force_refresh).payload
            macro_movers = self.resolve_daily_movers(symbols=macro_anchor_symbols, force_refresh=force_refresh).payload if macro_anchor_symbols else pd.DataFrame()
            movers = self._combine_mover_frames(equity_movers, macro_movers, macro_anchor_symbols=macro_anchor_symbols)

            attention_parts: list[pd.DataFrame] = []
            trend_horizons = list(normalize_horizons(["1d", "1w", "1mo", "3mo", "1yr"]))
            for dataset_name in ("attention_feed", "commodity_attention_feed"):
                try:
                    resolved = self.resolve_attention_feed(
                        dataset_name=dataset_name,
                        limit=80,
                        horizons=trend_horizons,
                        statuses=["active", "cooling"],
                        sensitivity="aggressive",
                        force_refresh=force_refresh,
                    ).payload
                except Exception:
                    resolved = pd.DataFrame()
                if isinstance(resolved, pd.DataFrame) and not resolved.empty:
                    attention_parts.append(resolved)
            attention_rows = pd.concat(attention_parts, ignore_index=True, sort=False) if attention_parts else pd.DataFrame()

            shortlist = shortlist_attention_symbols_1d(
                movers,
                holdings=holdings,
                attention_rows=attention_rows,
                max_count=100,
            )
            entity_master = build_attention_entity_master(shortlist)

            bars_by_symbol: dict[str, pd.DataFrame] = {}
            if shortlist:
                try:
                    bars_by_symbol = _make_api(self.cfg).get_stock_bars(
                        shortlist,
                        start=datetime.now(timezone.utc) - pd.Timedelta(days=120),
                        end=datetime.now(timezone.utc),
                        timeframe="1Day",
                        feed="iex",
                    )
                except Exception:
                    bars_by_symbol = {}

            research_symbols = shortlist[:40]
            news_payloads: dict[str, dict[str, Any]] = {}
            context_payloads: dict[str, dict[str, Any]] = {}
            for symbol in research_symbols:
                try:
                    news_payloads[symbol] = self.resolve_recent_news(
                        symbol,
                        days=3,
                        limit=8,
                        force_refresh=force_refresh,
                    ).payload
                except Exception:
                    news_payloads[symbol] = {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}
                try:
                    context_payloads[symbol] = self.resolve_attention_context(
                        symbol,
                        force_refresh=force_refresh,
                    ).payload
                except Exception:
                    context_payloads[symbol] = {}

            filings_frame = self._resolve_attention_edgar_filings(shortlist[:60], force_refresh=force_refresh)
            fred_summary_frame = pd.DataFrame()
            yield_curve_facts_frame = pd.DataFrame()
            if pipeline_store_configured():
                try:
                    fred_summary_frame, _ = self._pipeline_frame("fred_summary")
                except Exception:
                    fred_summary_frame = pd.DataFrame()
                try:
                    yield_curve_facts_frame, _ = self._pipeline_frame("yield_curve_facts_1d")
                except Exception:
                    yield_curve_facts_frame = pd.DataFrame()

            artifacts = build_bottom_up_attention_artifacts(
                movers,
                attention_rows=attention_rows,
                bars_by_symbol=bars_by_symbol,
                news_payloads=news_payloads,
                context_payloads=context_payloads,
                entity_master=entity_master,
                holdings=holdings,
                generated_at_utc=pd.Timestamp.utcnow(),
                filings_frame=filings_frame,
                fred_summary_frame=fred_summary_frame,
                yield_curve_facts_frame=yield_curve_facts_frame,
                llm_client=load_llm_client(),
                embedding_client=load_embedding_client(),
                top_events_limit=5,
                must_read_limit=10,
                unresolved_limit=5,
            )
            payload = dict(artifacts.home_payload or {})
            coverage = dict(payload.get("coverage_summary") or {})
            coverage.update(
                {
                    "equity_universe_count": len(equity_universe),
                    "macro_anchor_target_count": len(macro_anchor_symbols),
                    "research_symbol_count": len(research_symbols),
                }
            )
            payload["coverage_summary"] = coverage
            return {
                "home_payload": payload,
                "bundle_map": dict(artifacts.bundle_map or {}),
                "run_id": _coerce_text(payload.get("run_id")),
            }

        return cached_scalar_dict(
            "attention_agentic_live",
            cache_scope,
            _fetch,
            force_refresh=force_refresh,
            version=1,
        )

    def resolve_account(self, *, force_refresh: bool = False) -> ResolvedPayload:
        materialized_positions = self._try_pipeline_frame("positions_snapshot", force_refresh=force_refresh)
        if materialized_positions is not None:
            positions_frame, details = materialized_positions
            if isinstance(positions_frame, pd.DataFrame) and not positions_frame.empty:
                market_value = pd.to_numeric(positions_frame.get("market_value"), errors="coerce").fillna(0.0)
                payload = {
                    "status": "materialized",
                    "equity": float(market_value.sum()),
                    "cash": 0.0,
                    "portfolio_value": float(market_value.sum()),
                    "buying_power": 0.0,
                    "daytrade_count": 0,
                    "position_count": int(len(positions_frame)),
                }
                return self._resolved(payload, mode="materialized", datasets=("positions_snapshot",), details=details)
        materialized_only = self._materialized_only_result({}, datasets=("account",))
        if materialized_only is not None:
            return materialized_only
        frame = cached_scalar_dict(
            "account",
            _alpaca_cache_scope(self.cfg) if self.cfg is not None else "missing-config",
            lambda: _make_api(self.cfg).get_account(),
            force_refresh=force_refresh,
        )
        return self._resolved(frame, mode="on_demand", datasets=("account",))

    def resolve_user_account(self, user_context: Any, *, force_refresh: bool = False) -> ResolvedPayload:
        resolved = self.resolve_account(force_refresh=force_refresh)
        if self._user_can_view_full(user_context):
            return resolved
        projected = project_account_view(resolved.payload, self._user_share_fraction(user_context))
        details = dict(resolved.provenance.details)
        details.update({"projected": True, "user_id": self._user_id(user_context)})
        return self._resolved(projected, mode=resolved.provenance.mode, datasets=resolved.provenance.datasets + ("ownership_projection",), details=details)

    def resolve_positions(self, *, force_refresh: bool = False) -> ResolvedPayload:
        materialized_positions = self._try_pipeline_frame("positions_snapshot", force_refresh=force_refresh)
        if materialized_positions is not None:
            frame, details = materialized_positions
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                return self._resolved(frame.reset_index(drop=True), mode="materialized", datasets=("positions_snapshot",), details=details)
        materialized_only = self._materialized_only_result(pd.DataFrame(), datasets=("positions",))
        if materialized_only is not None:
            return materialized_only
        frame = cached_frame(
            "positions",
            _alpaca_cache_scope(self.cfg) if self.cfg is not None else "missing-config",
            lambda: _make_api(self.cfg).get_positions(),
            force_refresh=force_refresh,
        )
        return self._resolved(frame, mode="on_demand", datasets=("positions",))

    def resolve_user_positions(self, user_context: Any, *, force_refresh: bool = False) -> ResolvedPayload:
        resolved = self.resolve_positions(force_refresh=force_refresh)
        if self._user_can_view_full(user_context):
            return resolved
        projected = project_positions_view(resolved.payload, self._user_share_fraction(user_context))
        details = dict(resolved.provenance.details)
        details.update({"projected": True, "user_id": self._user_id(user_context)})
        return self._resolved(projected, mode=resolved.provenance.mode, datasets=resolved.provenance.datasets + ("ownership_projection",), details=details)

    def resolve_portfolio_timeseries(self, period: str, *, force_refresh: bool = False) -> ResolvedPayload:
        materialized_only = self._materialized_only_result(
            pd.DataFrame(),
            datasets=("portfolio_timeseries",),
            details={"period": period},
        )
        if materialized_only is not None:
            return materialized_only
        frame = cached_frame(
            "portfolio_timeseries",
            f"{_alpaca_cache_scope(self.cfg)}__period_{period}" if self.cfg is not None else f"missing-config__period_{period}",
            lambda: build_portfolio_timeseries(_make_api(self.cfg), period),
            force_refresh=force_refresh,
        )
        return self._resolved(frame, mode="on_demand", datasets=("portfolio_timeseries",), details={"period": period})

    def resolve_user_portfolio_timeseries(self, user_context: Any, period: str, *, force_refresh: bool = False) -> ResolvedPayload:
        resolved = self.resolve_portfolio_timeseries(period, force_refresh=force_refresh)
        if self._user_can_view_full(user_context):
            return resolved
        projected = project_portfolio_timeseries(resolved.payload, self._user_share_fraction(user_context))
        details = dict(resolved.provenance.details)
        details.update({"projected": True, "user_id": self._user_id(user_context)})
        return self._resolved(projected, mode=resolved.provenance.mode, datasets=resolved.provenance.datasets + ("ownership_projection",), details=details)

    def resolve_portfolio_performance(self, period: str, *, force_refresh: bool = False) -> ResolvedPayload:
        resolved = self.resolve_portfolio_timeseries(period, force_refresh=force_refresh)
        normalized = normalize_timeseries_view(resolved.payload)
        table = performance_table(normalized)
        return self._resolved(table, mode=resolved.provenance.mode, datasets=("portfolio_timeseries", "performance_table"), details={"period": period})

    def resolve_user_portfolio_performance(self, user_context: Any, period: str, *, force_refresh: bool = False) -> ResolvedPayload:
        resolved = self.resolve_user_portfolio_timeseries(user_context, period, force_refresh=force_refresh)
        normalized = normalize_timeseries_view(resolved.payload)
        table = performance_table(normalized)
        details = dict(resolved.provenance.details)
        details.update({"period": period})
        return self._resolved(table, mode=resolved.provenance.mode, datasets=("portfolio_timeseries", "performance_table"), details=details)

    def resolve_holding_roc(self, symbols: list[str], *, days: int = 365, force_refresh: bool = False) -> ResolvedPayload:
        normalized_symbols = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
        materialized_only = self._materialized_only_result(
            pd.DataFrame(),
            datasets=("holding_roc",),
            details={"days": days, "symbols": normalized_symbols},
        )
        if materialized_only is not None:
            return materialized_only
        symbol_scope = dataset_scope("symbols", ",".join(normalized_symbols))
        frame = cached_frame(
            "holding_roc",
            f"{_alpaca_cache_scope(self.cfg)}__{symbol_scope}__{days}d" if self.cfg is not None else f"missing-config__{symbol_scope}__{days}d",
            lambda: compute_holding_roc(_make_api(self.cfg), normalized_symbols, days=days),
            force_refresh=force_refresh,
        )
        return self._resolved(frame, mode="on_demand", datasets=("holding_roc",), details={"days": days, "symbols": normalized_symbols})

    def resolve_daily_movers(self, *, symbols: list[str] | None = None, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("daily_movers", force_refresh=force_refresh)
        if materialized is not None:
            pipeline, details = materialized
            if not pipeline.empty:
                if symbols and "symbol" in pipeline.columns:
                    allowed = {str(item).upper().strip() for item in symbols if str(item).strip()}
                    pipeline = pipeline[pipeline["symbol"].astype(str).str.upper().isin(allowed)].copy()
                return self._resolved(pipeline.reset_index(drop=True), mode="materialized", datasets=("daily_movers",), details=details)

        universe = symbols if symbols is not None else self._attention_home_equity_universe(force_refresh=force_refresh)
        universe = sorted({str(symbol).upper().strip() for symbol in universe if str(symbol).strip()})
        if not universe:
            return self._resolved(
                pd.DataFrame(),
                mode="on_demand",
                datasets=("daily_movers",),
                details={"symbols": [], "warning": "empty_universe"},
            )
        materialized_only = self._materialized_only_result(
            pd.DataFrame(),
            datasets=("daily_movers",),
            details={"symbols": sorted(universe)},
        )
        if materialized_only is not None:
            return materialized_only
        symbol_scope = dataset_scope("market-universe", ",".join(sorted({str(symbol).upper() for symbol in universe})))
        frame = cached_frame(
            "daily_movers",
            f"{_alpaca_cache_scope(self.cfg)}__{symbol_scope}" if self.cfg is not None else f"missing-config__{symbol_scope}",
            lambda: scan_daily_movers(_make_api(self.cfg), symbols=universe),
            force_refresh=force_refresh,
        )
        return self._resolved(frame, mode="on_demand", datasets=("daily_movers",), details={"symbols": sorted(universe)})

    def resolve_momentum_profiles(self, *, days: int = 180, symbols: list[str] | None = None, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("momentum_profiles", force_refresh=force_refresh)
        if materialized is not None:
            pipeline, details = materialized
            if not pipeline.empty:
                if symbols and "symbol" in pipeline.columns:
                    allowed = {str(item).upper().strip() for item in symbols if str(item).strip()}
                    pipeline = pipeline[pipeline["symbol"].astype(str).str.upper().isin(allowed)].copy()
                return self._resolved(pipeline.reset_index(drop=True), mode="materialized", datasets=("momentum_profiles",), details=details)

        universe = symbols if symbols is not None else self._attention_home_equity_universe(force_refresh=force_refresh)
        universe = sorted({str(symbol).upper().strip() for symbol in universe if str(symbol).strip()})
        if not universe:
            return self._resolved(
                pd.DataFrame(),
                mode="on_demand",
                datasets=("momentum_profiles",),
                details={"days": days, "symbols": [], "warning": "empty_universe"},
            )
        materialized_only = self._materialized_only_result(
            pd.DataFrame(),
            datasets=("momentum_profiles",),
            details={"days": days, "symbols": sorted(universe)},
        )
        if materialized_only is not None:
            return materialized_only
        symbol_scope = dataset_scope("market-universe", ",".join(sorted({str(symbol).upper() for symbol in universe})))
        frame = cached_frame(
            "momentum_profiles",
            f"{_alpaca_cache_scope(self.cfg)}__{days}d__{symbol_scope}" if self.cfg is not None else f"missing-config__{days}d__{symbol_scope}",
            lambda: scan_momentum_profiles(_make_api(self.cfg), symbols=universe, days=days),
            force_refresh=force_refresh,
            version=5,
        )
        return self._resolved(frame, mode="on_demand", datasets=("momentum_profiles",), details={"days": days, "symbols": sorted(universe)})

    def resolve_correlation_phase_shift(
        self,
        *,
        benchmark: str,
        days: int,
        corr_window: int,
        roc_window: int,
        momentum_window: int,
        symbols: list[str] | None = None,
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        materialized = self._try_pipeline_frames(
            ("correlation_phase_shift_summary", "correlation_phase_shift_history"),
            force_refresh=force_refresh,
        )
        if materialized is not None:
            frames, details = materialized
            summary = frames["correlation_phase_shift_summary"]
            history = frames["correlation_phase_shift_history"]
            if not summary.empty or not history.empty:
                bench = str(benchmark or "SPY").upper().strip()
                if "phase_benchmark" in summary.columns:
                    summary = summary[summary["phase_benchmark"].astype(str).str.upper() == bench].copy()
                if "benchmark" in summary.columns:
                    summary = summary[summary["benchmark"].astype(str).str.upper() == bench].copy()
                if "phase_benchmark" in history.columns:
                    history = history[history["phase_benchmark"].astype(str).str.upper() == bench].copy()
                if "benchmark" in history.columns:
                    history = history[history["benchmark"].astype(str).str.upper() == bench].copy()
                if symbols:
                    allowed = {str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()}
                    if "symbol" in summary.columns:
                        summary = summary[summary["symbol"].astype(str).str.upper().isin(allowed)].copy()
                    if "symbol" in history.columns:
                        history = history[history["symbol"].astype(str).str.upper().isin(allowed)].copy()
                return self._resolved(
                    {"summary": summary.reset_index(drop=True), "history": history.reset_index(drop=True)},
                    mode="materialized",
                    datasets=("correlation_phase_shift_summary", "correlation_phase_shift_history"),
                    details={
                        "summary": details["correlation_phase_shift_summary"],
                        "history": details["correlation_phase_shift_history"],
                        "benchmark": bench,
                    },
                )

        universe = symbols if symbols is not None else self._attention_home_equity_universe(force_refresh=force_refresh)
        universe = sorted({str(symbol).upper().strip() for symbol in universe if str(symbol).strip()})
        if not universe:
            return self._resolved(
                {"summary": pd.DataFrame(), "history": pd.DataFrame()},
                mode="on_demand",
                datasets=("correlation_phase_shift_summary", "correlation_phase_shift_history"),
                details={
                    "benchmark": benchmark,
                    "days": days,
                    "corr_window": corr_window,
                    "roc_window": roc_window,
                    "momentum_window": momentum_window,
                    "symbols": [],
                    "warning": "empty_universe",
                },
            )
        materialized_only = self._materialized_only_result(
            {"summary": pd.DataFrame(), "history": pd.DataFrame()},
            datasets=("correlation_phase_shift_summary", "correlation_phase_shift_history"),
            details={
                "benchmark": benchmark,
                "days": days,
                "corr_window": corr_window,
                "roc_window": roc_window,
                "momentum_window": momentum_window,
                "symbols": sorted(universe),
            },
        )
        if materialized_only is not None:
            return materialized_only
        symbol_scope = dataset_scope("phase-shift-universe", ",".join(sorted({str(symbol).upper() for symbol in universe})))
        payload = cached_frame_dict(
            "correlation_phase_shift",
            (
                f"{_alpaca_cache_scope(self.cfg)}__{benchmark.upper()}__{days}d__corr_{corr_window}"
                f"__roc_{roc_window}__mom_{momentum_window}__{symbol_scope}"
            )
            if self.cfg is not None
            else f"missing-config__{benchmark.upper()}__{days}d__corr_{corr_window}__roc_{roc_window}__mom_{momentum_window}__{symbol_scope}",
            lambda: {
                key: value
                for key, value in scan_correlation_phase_shifts(
                    _make_api(self.cfg),
                    symbols=universe,
                    benchmark=benchmark,
                    days=days,
                    corr_window=corr_window,
                    roc_window=roc_window,
                    momentum_window=momentum_window,
                ).items()
                if key in {"summary", "history"}
            },
            keys=["summary", "history"],
            force_refresh=force_refresh,
            version=3,
        )
        return self._resolved(
            payload,
            mode="on_demand",
            datasets=("correlation_phase_shift_summary", "correlation_phase_shift_history"),
            details={
                "benchmark": benchmark,
                "days": days,
                "corr_window": corr_window,
                "roc_window": roc_window,
                "momentum_window": momentum_window,
                "symbols": sorted(universe),
            },
        )

    def resolve_commodity_regime(
        self,
        *,
        commodity_symbols: list[str],
        days: int,
        corr_window: int,
        roc_window: int,
        momentum_window: int,
        symbols: list[str] | None = None,
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        materialized = self._try_pipeline_frames(
            ("commodity_regime_summary", "commodity_regime_history"),
            force_refresh=force_refresh,
        )
        if materialized is not None:
            frames, details = materialized
            summary = frames["commodity_regime_summary"]
            history = frames["commodity_regime_history"]
            if not summary.empty or not history.empty:
                return self._resolved(
                    {"summary": summary.reset_index(drop=True), "history": history.reset_index(drop=True)},
                    mode="materialized",
                    datasets=("commodity_regime_summary", "commodity_regime_history"),
                    details={
                        "summary": details["commodity_regime_summary"],
                        "history": details["commodity_regime_history"],
                    },
                )

        universe = symbols if symbols is not None else self._attention_home_equity_universe(force_refresh=force_refresh)
        universe = sorted({str(symbol).upper().strip() for symbol in universe if str(symbol).strip()})
        if not universe:
            return self._resolved(
                {"summary": pd.DataFrame(), "history": pd.DataFrame()},
                mode="on_demand",
                datasets=("commodity_regime_summary", "commodity_regime_history"),
                details={
                    "days": days,
                    "corr_window": corr_window,
                    "roc_window": roc_window,
                    "momentum_window": momentum_window,
                    "symbols": [],
                    "commodity_symbols": sorted(commodity_symbols),
                    "warning": "empty_universe",
                },
            )
        materialized_only = self._materialized_only_result(
            {"summary": pd.DataFrame(), "history": pd.DataFrame()},
            datasets=("commodity_regime_summary", "commodity_regime_history"),
            details={
                "days": days,
                "corr_window": corr_window,
                "roc_window": roc_window,
                "momentum_window": momentum_window,
                "symbols": sorted(universe),
                "commodity_symbols": sorted(commodity_symbols),
            },
        )
        if materialized_only is not None:
            return materialized_only
        symbol_scope = dataset_scope("commodity-universe", ",".join(sorted({str(symbol).upper() for symbol in universe})))
        commodity_scope = dataset_scope("commodity-basket", ",".join(sorted({str(symbol).upper() for symbol in commodity_symbols})))
        payload = cached_frame_dict(
            "commodity_regime",
            (
                f"{_alpaca_cache_scope(self.cfg)}__{days}d__corr_{corr_window}__roc_{roc_window}"
                f"__mom_{momentum_window}__{symbol_scope}__{commodity_scope}"
            )
            if self.cfg is not None
            else f"missing-config__{days}d__corr_{corr_window}__roc_{roc_window}__mom_{momentum_window}__{symbol_scope}__{commodity_scope}",
            lambda: {
                key: value
                for key, value in scan_commodity_regimes(
                    _make_api(self.cfg),
                    symbols=universe,
                    commodity_symbols=commodity_symbols,
                    days=days,
                    corr_window=corr_window,
                    roc_window=roc_window,
                    momentum_window=momentum_window,
                ).items()
                if key in {"summary", "history"}
            },
            keys=["summary", "history"],
            force_refresh=force_refresh,
            version=2,
        )
        return self._resolved(
            payload,
            mode="on_demand",
            datasets=("commodity_regime_summary", "commodity_regime_history"),
            details={
                "days": days,
                "corr_window": corr_window,
                "roc_window": roc_window,
                "momentum_window": momentum_window,
                "symbols": sorted(universe),
                "commodity_symbols": sorted(commodity_symbols),
            },
        )

    def resolve_price_history(self, ticker: str, *, days: int, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("price_history", force_refresh=force_refresh)
        if materialized is not None:
            pipeline, details = materialized
            if not pipeline.empty and {"symbol", "timestamp"}.issubset(set(pipeline.columns)):
                out = pipeline[pipeline["symbol"].astype(str).str.upper() == ticker.upper()].copy()
                if not out.empty:
                    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
                    out = out.dropna(subset=["timestamp"]).sort_values("timestamp")
                    if days > 0:
                        cutoff = out["timestamp"].max() - pd.Timedelta(days=max(int(days), 1))
                        out = out[out["timestamp"] >= cutoff].copy()
                    return self._resolved(out.reset_index(drop=True), mode="materialized", datasets=("price_history",), details=details)

        materialized_only = self._materialized_only_result(
            pd.DataFrame(),
            datasets=("price_history",),
            details={"ticker": ticker.upper(), "days": days},
        )
        if materialized_only is not None:
            return materialized_only
        frame = cached_frame(
            "price_history",
            f"{_alpaca_cache_scope(self.cfg)}__{ticker.upper()}__{days}d" if self.cfg is not None else f"missing-config__{ticker.upper()}__{days}d",
            lambda: load_price_history(_make_api(self.cfg), ticker, days=days),
            force_refresh=force_refresh,
        )
        return self._resolved(frame, mode="on_demand", datasets=("price_history",), details={"ticker": ticker.upper(), "days": days})

    def resolve_technical_signal_history(self, ticker: str, *, days: int, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("technical_signal_history", force_refresh=force_refresh)
        if materialized is not None:
            pipeline, details = materialized
            if not pipeline.empty and {"symbol", "timestamp"}.issubset(set(pipeline.columns)):
                out = pipeline[pipeline["symbol"].astype(str).str.upper() == ticker.upper()].copy()
                if not out.empty:
                    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
                    out = out.dropna(subset=["timestamp"]).sort_values("timestamp")
                    if days > 0:
                        cutoff = out["timestamp"].max() - pd.Timedelta(days=max(int(days), 1))
                        out = out[out["timestamp"] >= cutoff].copy()
                    return self._resolved(out.reset_index(drop=True), mode="materialized", datasets=("technical_signal_history",), details=details)

        materialized_only = self._materialized_only_result(
            pd.DataFrame(),
            datasets=("technical_signal_history",),
            details={"ticker": ticker.upper(), "days": days},
        )
        if materialized_only is not None:
            return materialized_only
        if build_signal_frame is None:
            return self._resolved(pd.DataFrame(), mode="on_demand", datasets=("technical_signal_history",), details={"warning": "signals module unavailable"})

        base_days = max(int(days), 365)
        price = self.resolve_price_history(ticker, days=base_days, force_refresh=force_refresh)
        if price.payload.empty:
            return self._resolved(pd.DataFrame(), mode=price.provenance.mode, datasets=("technical_signal_history",), details=price.provenance.details)

        frame = build_signal_frame(price.payload)
        if frame.empty:
            return self._resolved(frame, mode=price.provenance.mode, datasets=("technical_signal_history",), details=price.provenance.details)
        frame = frame.copy()
        frame["symbol"] = ticker.upper().strip()
        return self._resolved(frame.reset_index(drop=True), mode="computed", datasets=("price_history", "technical_signal_history"), details=price.provenance.details)

    def resolve_technical_signal_summary(
        self,
        ticker: str,
        *,
        signal_frame: pd.DataFrame | None = None,
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("technical_signals_latest", force_refresh=force_refresh)
        if materialized is not None:
            latest, details = materialized
            if not latest.empty and "symbol" in latest.columns:
                rows = latest[latest["symbol"].astype(str).str.upper() == ticker.upper()].copy()
                if not rows.empty:
                    row = rows.iloc[0]
                    payload = {
                        key: row.get(key)
                        for key in [
                            "close",
                            "ath",
                            "pullback_from_ath_pct",
                            "channel_support",
                            "channel_resistance",
                            "channel_position",
                            "dist_to_support_pct",
                            "dist_to_resistance_pct",
                            "ret_5_pct",
                            "ret_21_pct",
                            "ret_63_pct",
                            "rsi_14",
                            "vol_20_ann_pct",
                            "regime",
                        ]
                    }
                    return self._resolved(payload, mode="materialized", datasets=("technical_signals_latest",), details=details)

        materialized_only = self._materialized_only_result(
            {},
            datasets=("technical_signals_latest",),
            details={"ticker": ticker.upper()},
        )
        if materialized_only is not None:
            return materialized_only
        if summarize_signal_frame is None:
            return self._resolved({}, mode="on_demand", datasets=("technical_signals_latest",), details={"warning": "signals module unavailable"})

        frame = signal_frame
        details: dict[str, Any] = {}
        if frame is None:
            resolved = self.resolve_technical_signal_history(ticker, days=365, force_refresh=force_refresh)
            frame = resolved.payload
            details = dict(resolved.provenance.details)
        summary = summarize_signal_frame(frame) if isinstance(frame, pd.DataFrame) and not frame.empty else {}
        return self._resolved(summary, mode="computed", datasets=("technical_signal_history", "technical_signals_latest"), details=details)

    def resolve_forecast_next_week(
        self,
        ticker: str,
        *,
        signal_frame: pd.DataFrame | None = None,
        days: int = 365,
        horizon: int = 5,
        simulations: int = 1500,
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        if forecast_next_week is None:
            return self._resolved({}, mode="on_demand", datasets=("technical_signal_history",), details={"warning": "signals module unavailable"})
        frame = signal_frame
        details: dict[str, Any] = {"ticker": ticker.upper(), "days": days, "horizon": horizon, "simulations": simulations}
        resolved_mode = "computed"
        if frame is None:
            materialized = self._try_pipeline_frame("technical_signal_history", force_refresh=force_refresh)
            if materialized is not None:
                pipeline, pipeline_details = materialized
                if not pipeline.empty and "symbol" in pipeline.columns:
                    rows = pipeline.copy()
                    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
                    rows = rows[rows["symbol"] == ticker.upper()].copy()
                    if not rows.empty:
                        if "timestamp" in rows.columns:
                            rows["timestamp"] = pd.to_datetime(rows["timestamp"], utc=True, errors="coerce")
                            rows = rows.sort_values("timestamp", ascending=True, na_position="last")
                        frame = rows.reset_index(drop=True)
                        details = {**details, **pipeline_details}
                        resolved_mode = "materialized"
            if frame is None and not self.materialized_only:
                resolved = self.resolve_technical_signal_history(ticker, days=days, force_refresh=force_refresh)
                frame = resolved.payload
                details = {**details, **resolved.provenance.details}
                resolved_mode = resolved.provenance.mode
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            materialized_only = self._materialized_only_result(
                {},
                datasets=("technical_signal_history", "technical_forecast"),
                details=details,
            )
            if materialized_only is not None:
                return materialized_only
            return self._resolved({}, mode=resolved_mode, datasets=("technical_signal_history", "technical_forecast"), details=details)
        forecast = forecast_next_week(frame, horizon=horizon, simulations=simulations) if isinstance(frame, pd.DataFrame) and not frame.empty else {}
        return self._resolved(
            forecast,
            mode="computed" if forecast else resolved_mode,
            datasets=("technical_signal_history", "technical_forecast"),
            details=details,
        )

    def resolve_option_chain(self, ticker: str, *, expiration: str | None = None, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("option_contract_snapshots", force_refresh=force_refresh)
        if materialized is not None:
            pipeline, details = materialized
            if not pipeline.empty and {"symbol", "expiration", "type"}.issubset(set(pipeline.columns)):
                options = pipeline[pipeline["symbol"].astype(str).str.upper() == ticker.upper()].copy()
                if not options.empty:
                    expirations = sorted({str(value) for value in options["expiration"].dropna().astype(str).tolist() if str(value).strip()})
                    if expirations:
                        if expiration is None:
                            return self._resolved((expirations, pd.DataFrame(), pd.DataFrame()), mode="materialized", datasets=("option_contract_snapshots",), details=details)
                        selected_expiration = expiration if expiration in expirations else expirations[0]
                        scoped = options[options["expiration"].astype(str) == selected_expiration].copy()
                        scoped = scoped.sort_values([col for col in ["strike", "contractSymbol"] if col in scoped.columns])
                        calls = scoped[scoped["type"].astype(str).str.lower() == "call"].copy()
                        puts = scoped[scoped["type"].astype(str).str.lower() == "put"].copy()
                        return self._resolved((expirations, calls.reset_index(drop=True), puts.reset_index(drop=True)), mode="materialized", datasets=("option_contract_snapshots",), details=details)

            if expiration is None:
                expirations_only_materialized = self._try_pipeline_frame("option_expirations", force_refresh=force_refresh)
                if expirations_only_materialized is None:
                    expirations_only = pd.DataFrame()
                    exp_details: dict[str, Any] = {}
                else:
                    expirations_only, exp_details = expirations_only_materialized
                if not expirations_only.empty and {"symbol", "expiration"}.issubset(set(expirations_only.columns)):
                    options = expirations_only[expirations_only["symbol"].astype(str).str.upper() == ticker.upper()].copy()
                    expirations = sorted({str(value) for value in options["expiration"].dropna().astype(str).tolist() if str(value).strip()})
                    if expirations:
                        return self._resolved((expirations, pd.DataFrame(), pd.DataFrame()), mode="materialized", datasets=("option_expirations",), details=exp_details)

        materialized_only = self._materialized_only_result(
            ([], pd.DataFrame(), pd.DataFrame()),
            datasets=("option_contract_snapshots",),
            details={"ticker": ticker.upper(), "expiration": expiration},
        )
        if materialized_only is not None:
            return materialized_only
        expiration_scope = expiration or "expirations"
        payload = cached_option_chain(
            "option_chain",
            f"{_alpaca_cache_scope(self.cfg)}__{ticker.upper()}__{expiration_scope}" if self.cfg is not None else f"missing-config__{ticker.upper()}__{expiration_scope}",
            lambda: load_option_chain(_make_api(self.cfg), ticker, expiration),
            force_refresh=force_refresh,
        )
        return self._resolved(payload, mode="on_demand", datasets=("option_chain",), details={"ticker": ticker.upper(), "expiration": expiration})

    def resolve_option_surface(
        self,
        ticker: str,
        *,
        expected_price: float,
        horizon_days: int,
        underlying_price: float,
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("option_contract_snapshots", force_refresh=force_refresh)
        if materialized is not None:
            pipeline, details = materialized
            if not pipeline.empty and {"symbol", "expiration"}.issubset(set(pipeline.columns)):
                scoped = pipeline[pipeline["symbol"].astype(str).str.upper() == ticker.upper()].copy()
                if not scoped.empty:
                    windowed = select_option_surface_window(
                        scoped,
                        underlying_price=underlying_price,
                        expected_price=expected_price,
                        horizon_days=horizon_days,
                        max_contracts=450,
                    )
                    if not windowed.empty:
                        return self._resolved(windowed.reset_index(drop=True), mode="materialized", datasets=("option_contract_snapshots",), details=details)

        materialized_only = self._materialized_only_result(
            pd.DataFrame(),
            datasets=("option_contract_snapshots",),
            details={"ticker": ticker.upper(), "expected_price": expected_price, "horizon_days": horizon_days, "underlying_price": underlying_price},
        )
        if materialized_only is not None:
            return materialized_only
        frame = cached_frame(
            "option_surface",
            (
                f"{_alpaca_cache_scope(self.cfg)}__{ticker.upper()}__exp_{expected_price:.2f}"
                f"__h_{int(horizon_days)}__spot_{underlying_price:.2f}"
            )
            if self.cfg is not None
            else f"missing-config__{ticker.upper()}__exp_{expected_price:.2f}__h_{int(horizon_days)}__spot_{underlying_price:.2f}",
            lambda: load_option_surface(
                _make_api(self.cfg),
                ticker,
                underlying_price=underlying_price,
                expected_price=expected_price,
                horizon_days=horizon_days,
            ),
            force_refresh=force_refresh,
        )
        return self._resolved(
            frame,
            mode="on_demand",
            datasets=("option_surface",),
            details={"ticker": ticker.upper(), "expected_price": expected_price, "horizon_days": horizon_days, "underlying_price": underlying_price},
        )

    def resolve_option_candidates(
        self,
        ticker: str,
        *,
        surface: pd.DataFrame | None = None,
        expected_price: float,
        horizon_days: int,
        underlying_price: float,
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        materialized_only = self._materialized_only_result(
            {"candidates": pd.DataFrame(), "summary": {}},
            datasets=("option_surface", "option_candidates"),
            details={
                "ticker": ticker.upper(),
                "expected_price": expected_price,
                "horizon_days": horizon_days,
                "underlying_price": underlying_price,
            },
        )
        if materialized_only is not None:
            return materialized_only
        details = {
            "ticker": ticker.upper(),
            "expected_price": expected_price,
            "horizon_days": horizon_days,
            "underlying_price": underlying_price,
        }
        resolved_mode = "computed"
        surface_frame = surface
        if surface_frame is None:
            resolved = self.resolve_option_surface(
                ticker,
                expected_price=expected_price,
                horizon_days=horizon_days,
                underlying_price=underlying_price,
                force_refresh=force_refresh,
            )
            surface_frame = resolved.payload
            details = {**details, **resolved.provenance.details}
            resolved_mode = resolved.provenance.mode
        candidates, summary = analyze_option_candidates(
            surface_frame,
            underlying_price=underlying_price,
            expected_price=expected_price,
            horizon_days=horizon_days,
        )
        return self._resolved(
            {"candidates": candidates, "summary": summary},
            mode="computed" if not candidates.empty else resolved_mode,
            datasets=("option_surface", "option_candidates"),
            details=details,
        )

    def resolve_quarterly_fundamentals(self, ticker: str, *, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("quarterly_fundamentals", force_refresh=force_refresh)
        if materialized is not None:
            pipeline, details = materialized
            if not pipeline.empty and "ticker" in pipeline.columns:
                rows = pipeline[pipeline["ticker"].astype(str).str.upper() == ticker.upper()].copy()
                if not rows.empty:
                    if "report_date" in rows.columns:
                        rows["report_date"] = pd.to_datetime(rows["report_date"], errors="coerce")
                    if "statement" in rows.columns:
                        rows["statement"] = rows["statement"].astype(str).str.lower()
                    out = {
                        "income": rows[rows.get("statement", pd.Series(dtype=str)) == "income"].copy().reset_index(drop=True),
                        "balance": rows[rows.get("statement", pd.Series(dtype=str)) == "balance"].copy().reset_index(drop=True),
                        "cashflow": rows[rows.get("statement", pd.Series(dtype=str)) == "cashflow"].copy().reset_index(drop=True),
                    }
                    if any(not part.empty for part in out.values()):
                        return self._resolved(out, mode="materialized", datasets=("quarterly_fundamentals",), details=details)

        materialized_only = self._materialized_only_result(
            {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()},
            datasets=("quarterly_fundamentals",),
            details={"ticker": ticker.upper()},
        )
        if materialized_only is not None:
            return materialized_only
        payload = cached_frame_dict(
            "quarterly_fundamentals",
            ticker.upper(),
            lambda: load_quarterly_fundamentals(ticker),
            keys=["income", "balance", "cashflow"],
            force_refresh=force_refresh,
        )
        return self._resolved(payload, mode="on_demand", datasets=("quarterly_fundamentals",), details={"ticker": ticker.upper()})

    def resolve_asset_metadata(self, ticker: str, *, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._resolve_materialized_asset_metadata(ticker, force_refresh=force_refresh)
        if materialized is not None:
            return materialized
        materialized_only = self._materialized_only_result({}, datasets=("asset_metadata",), details={"ticker": ticker.upper()})
        if materialized_only is not None:
            return materialized_only
        payload = cached_scalar_dict(
            "asset_metadata",
            f"{_alpaca_cache_scope(self.cfg)}__{ticker.upper()}" if self.cfg is not None else f"missing-config__{ticker.upper()}",
            lambda: load_asset_metadata(_make_api(self.cfg), ticker),
            force_refresh=force_refresh,
        )
        return self._resolved(payload, mode="on_demand", datasets=("asset_metadata",), details={"ticker": ticker.upper()})

    def resolve_recent_news(self, ticker: str, *, days: int = 14, limit: int = 8, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("news_articles", force_refresh=force_refresh)
        if materialized is not None:
            pipeline, details = materialized
            if not pipeline.empty:
                target = ticker.upper().strip()

                def _has_symbol(value: object) -> bool:
                    if value is None:
                        return False
                    if isinstance(value, str):
                        items = [value]
                    elif isinstance(value, (list, tuple, set, pd.Series, pd.Index)):
                        items = list(value)
                    else:
                        tolist = getattr(value, "tolist", None)
                        if callable(tolist):
                            try:
                                listed = tolist()
                            except Exception:
                                listed = value
                            if isinstance(listed, list):
                                items = listed
                            elif isinstance(listed, tuple):
                                items = list(listed)
                            else:
                                items = [listed]
                        else:
                            items = [value]

                    tokens: list[str] = []
                    for item in items:
                        for token in str(item).replace("|", ",").split(","):
                            cleaned = token.upper().strip()
                            if cleaned and cleaned.lower() != "nan":
                                tokens.append(cleaned)
                    return target in tokens

                rows = pipeline.copy()
                if "symbols" in rows.columns:
                    rows = rows[rows["symbols"].apply(_has_symbol)].copy()
                if not rows.empty:
                    if "published_at" in rows.columns:
                        rows["published_at"] = pd.to_datetime(rows["published_at"], utc=True, errors="coerce")
                        rows = rows.sort_values("published_at", ascending=False, na_position="last")
                    rows = rows.head(limit).reset_index(drop=True)
                    return self._resolved({"articles": rows, "fallback_summary": None, "source": "pipeline"}, mode="materialized", datasets=("news_articles",), details=details)

        search_materialized = self._resolve_materialized_recent_news_from_search(
            ticker,
            limit=limit,
            force_refresh=force_refresh,
        )
        if search_materialized is not None:
            return search_materialized

        background_materialized = self._resolve_materialized_recent_news_from_background(
            ticker,
            limit=limit,
            force_refresh=force_refresh,
        )
        if background_materialized is not None:
            return background_materialized

        materialized_only = self._materialized_only_result(
            {"articles": pd.DataFrame(), "fallback_summary": None, "source": "pipeline"},
            datasets=("news_articles",),
            details={"ticker": ticker.upper(), "days": days, "limit": limit},
        )
        if materialized_only is not None:
            return materialized_only
        payload = cached_news_payload(
            "recent_news",
            f"{_alpaca_cache_scope(self.cfg)}__{ticker.upper()}__{days}d__{limit}" if self.cfg is not None else f"missing-config__{ticker.upper()}__{days}d__{limit}",
            lambda: load_recent_news(_make_api(self.cfg), ticker, days=days, limit=limit),
            force_refresh=force_refresh,
        )
        return self._resolved(payload, mode="on_demand", datasets=("recent_news",), details={"ticker": ticker.upper(), "days": days, "limit": limit})

    def resolve_attention_context(self, ticker: str, *, force_refresh: bool = False) -> ResolvedPayload:
        target = str(ticker or "").upper().strip()
        empty_payload = {
            "symbol": target,
            "context_story_text": "",
            "primary_source_excerpt": "",
            "latest_filing_excerpt": "",
            "source_line": "",
            "llm_headline": "",
            "llm_summary_text": "",
            "llm_narrative_text": "",
            "llm_why_now": "",
            "llm_management_signal": "",
            "llm_confidence": "",
            "llm_source_line": "",
            "llm_supporting_points": [],
            "top_filing_links": [],
        }

        materialized = self._try_pipeline_frame("attention_context_bundle", force_refresh=force_refresh)
        if materialized is None:
            return self._resolved(empty_payload, mode="materialized", datasets=("attention_context_bundle",), details={"ticker": target})

        pipeline, details = materialized
        if pipeline.empty or "symbol" not in pipeline.columns:
            return self._resolved(empty_payload, mode="materialized", datasets=("attention_context_bundle",), details=details)

        rows = pipeline.copy()
        rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
        match = rows[rows["symbol"] == target].head(1)
        if match.empty:
            return self._resolved(empty_payload, mode="materialized", datasets=("attention_context_bundle",), details=details)

        payload = match.iloc[0].to_dict()
        links_raw = payload.get("top_filing_links_json")
        links: list[dict[str, object]] = []
        if isinstance(links_raw, str) and links_raw.strip():
            try:
                parsed = json.loads(links_raw)
                if isinstance(parsed, list):
                    links = [item for item in parsed if isinstance(item, dict)]
            except Exception:
                links = []
        payload["top_filing_links"] = links
        supporting_points_raw = payload.get("llm_supporting_points_json")
        supporting_points: list[str] = []
        if isinstance(supporting_points_raw, str) and supporting_points_raw.strip():
            try:
                parsed_points = json.loads(supporting_points_raw)
                if isinstance(parsed_points, list):
                    supporting_points = [str(item).strip() for item in parsed_points if str(item).strip()]
            except Exception:
                supporting_points = []
        payload["llm_supporting_points"] = supporting_points
        return self._resolved(payload, mode="materialized", datasets=("attention_context_bundle",), details=details)

    def resolve_attention_ticker_snapshot(self, ticker: str, *, force_refresh: bool = False) -> ResolvedPayload:
        target = str(ticker or "").upper().strip()
        materialized = self._first_materialized_frame(
            ATTENTION_TICKER_SNAPSHOT_DATASETS,
            force_refresh=force_refresh,
        )
        if materialized is not None:
            dataset_name, frame, details = materialized
            payload = deserialize_attention_ticker_snapshot_frame(frame, target)
            if payload:
                return self._resolved(
                    payload,
                    mode="materialized",
                    datasets=(dataset_name,),
                    details={**details, "ticker": target},
                )
        return self._resolved({}, mode="materialized" if self.materialized_only else "on_demand", datasets=ATTENTION_TICKER_SNAPSHOT_DATASETS, details={"ticker": target, **({"materialized_only": True} if self.materialized_only else {})})

    def resolve_attention_ticker_background(self, ticker: str, *, force_refresh: bool = False) -> ResolvedPayload:
        target = str(ticker or "").upper().strip()
        materialized = self._first_materialized_frame(
            ATTENTION_TICKER_BACKGROUND_DATASETS,
            force_refresh=force_refresh,
        )
        if materialized is not None:
            dataset_name, frame, details = materialized
            payload = deserialize_attention_ticker_background_frame(frame, target)
            if payload:
                return self._resolved(
                    payload,
                    mode="materialized",
                    datasets=(dataset_name,),
                    details={**details, "ticker": target},
                )
        return self._resolved({}, mode="materialized" if self.materialized_only else "on_demand", datasets=ATTENTION_TICKER_BACKGROUND_DATASETS, details={"ticker": target, **({"materialized_only": True} if self.materialized_only else {})})

    def resolve_attention_home_1d(self, *, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._first_materialized_frame(
            ATTENTION_HOME_SNAPSHOT_DATASETS,
            force_refresh=force_refresh,
        )
        if materialized is not None:
            dataset_name, frame, details = materialized
            payload = deserialize_attention_home_payload(frame)
            if (
                payload
                and not self._payload_uses_legacy_attention_titles(payload)
                and not self._payload_uses_stat_dump_copy(payload)
            ):
                return self._resolved(
                    payload,
                    mode="materialized",
                    datasets=(dataset_name,),
                    details=details,
                )

        materialized_only = self._materialized_only_result({}, datasets=ATTENTION_HOME_SNAPSHOT_DATASETS, details={"today_only": True})
        if materialized_only is not None:
            return materialized_only
        payload = self._resolve_live_attention_artifacts(force_refresh=force_refresh).get("home_payload") or {}
        return self._resolved(
            payload,
            mode="on_demand",
            datasets=(
                "daily_movers",
                "attention_feed",
                "commodity_attention_feed",
                "attention_candidates_1d",
                "attention_research_plans",
                "attention_source_documents",
                "attention_claims",
                "attention_event_clusters_1d",
            ),
            details={"today_only": True, "run_id": _coerce_text((payload or {}).get("run_id"))},
        )

    def resolve_attention_research_bundle(self, bundle_id: str, *, force_refresh: bool = False) -> ResolvedPayload:
        normalized_bundle_id = str(bundle_id or "").strip()
        materialized = self._first_materialized_frame(
            ATTENTION_BUNDLE_SNAPSHOT_DATASETS,
            force_refresh=force_refresh,
        )
        if materialized is not None:
            dataset_name, frame, details = materialized
            payload = deserialize_attention_research_bundle_frame(frame, normalized_bundle_id)
            if (
                payload
                and not self._bundle_uses_legacy_attention_titles(payload)
                and not self._bundle_uses_stat_dump_copy(payload)
            ):
                return self._resolved(
                    payload,
                    mode="materialized",
                    datasets=(dataset_name,),
                    details={**details, "bundle_id": normalized_bundle_id},
                )

        materialized_only = self._materialized_only_result({}, datasets=ATTENTION_BUNDLE_SNAPSHOT_DATASETS, details={"bundle_id": normalized_bundle_id})
        if materialized_only is not None:
            return materialized_only
        live = self._resolve_live_attention_artifacts(force_refresh=force_refresh)
        bundle_map = dict(live.get("bundle_map") or {})
        payload = dict(bundle_map.get(normalized_bundle_id) or {})
        return self._resolved(
            payload,
            mode="on_demand",
            datasets=(
                "attention_home_snapshots_1d",
                "attention_bundle_snapshots",
                "attention_source_documents",
                "attention_evidence_chunks",
                "attention_claims",
            ),
            details={
                "bundle_id": normalized_bundle_id,
                "run_id": _coerce_text(payload.get("run_id") or live.get("run_id")),
            },
        )

    def resolve_attention_run_trace(self, run_id: str, *, force_refresh: bool = False) -> ResolvedPayload:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return self._resolved({}, mode="materialized", datasets=(), details={"run_id": normalized_run_id})

        frames_result = self._try_pipeline_frames(ATTENTION_TRACE_DATASETS, force_refresh=force_refresh)
        if frames_result is None:
            return self._resolved({}, mode="on_demand", datasets=ATTENTION_TRACE_DATASETS, details={"run_id": normalized_run_id})

        frames, details = frames_result
        trace: dict[str, Any] = {"run_id": normalized_run_id, "datasets": {}}
        for dataset_name in ATTENTION_TRACE_DATASETS:
            frame = frames.get(dataset_name, pd.DataFrame())
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                trace["datasets"][dataset_name] = {"row_count": 0, "rows": []}
                continue
            scoped = frame.copy()
            if "run_id" in scoped.columns:
                scoped = scoped[scoped["run_id"].astype(str) == normalized_run_id].copy()
            elif dataset_name == "attention_home_snapshots_1d":
                scoped = scoped[scoped.get("run_id", pd.Series(dtype=str)).astype(str) == normalized_run_id].copy()
            trace["datasets"][dataset_name] = {
                "row_count": int(len(scoped)),
                "rows": scoped.head(50).to_dict(orient="records"),
            }
        return self._resolved(trace, mode="materialized", datasets=ATTENTION_TRACE_DATASETS, details={"run_id": normalized_run_id, "frames": details})

    def resolve_attention_feed(
        self,
        *,
        dataset_name: str = "attention_feed",
        limit: int = 10,
        entity_ids: list[str] | None = None,
        horizons: list[str] | None = None,
        statuses: list[str] | None = None,
        sensitivity: str | None = None,
        min_attention_score: float | None = None,
        residual_zscore_threshold: float | None = None,
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        del force_refresh
        dataset_key = str(dataset_name or "attention_feed").strip() or "attention_feed"
        if not pipeline_store_configured():
            return self._resolved(
                pd.DataFrame(),
                mode="materialized",
                datasets=(dataset_key,),
                details={"warning": "pipeline store unavailable"},
            )

        wants_tuned_feed = any(
            [
                sensitivity is not None,
                residual_zscore_threshold is not None,
                min_attention_score is not None,
                bool(normalize_horizons(horizons)),
            ]
        )
        candidate_dataset = self._attention_candidate_dataset_name(dataset_key)
        if wants_tuned_feed and candidate_dataset:
            candidate_frame, candidate_details = self._pipeline_frame(candidate_dataset)
            if not candidate_frame.empty:
                config = self._attention_config_from_params(
                    sensitivity=sensitivity,
                    residual_zscore_threshold=residual_zscore_threshold,
                    min_attention_score=min_attention_score,
                    high_priority_threshold=None,
                )
                filtered = filter_attention_events(
                    candidate_frame,
                    config=config,
                    horizons=horizons,
                    entity_ids=entity_ids,
                    statuses=statuses,
                )
                feed = build_attention_feed(filtered, pd.DataFrame(), top_n=int(limit))
                return self._resolved(
                    feed.reset_index(drop=True),
                    mode="materialized",
                    datasets=(candidate_dataset,),
                    details={**candidate_details, "filters": {"horizons": list(normalize_horizons(horizons)), "sensitivity": sensitivity or "balanced"}},
                )

        feed, details = self._pipeline_frame(dataset_key)
        if feed.empty:
            return self._resolved(feed, mode="materialized", datasets=(dataset_key,), details=details)

        out = feed.copy()
        if "entity_id" in out.columns and entity_ids:
            allowed = {str(value).upper().strip() for value in entity_ids if str(value).strip()}
            out = out[out["entity_id"].astype(str).str.upper().isin(allowed)].copy()
        if "horizon" in out.columns and horizons:
            selected_horizons = set(normalize_horizons(horizons))
            if selected_horizons:
                out = out[out["horizon"].astype(str).map(normalize_horizon).isin(selected_horizons)].copy()
        if "status" in out.columns and statuses:
            allowed_statuses = {str(value).lower().strip() for value in statuses if str(value).strip()}
            out = out[out["status"].astype(str).str.lower().isin(allowed_statuses)].copy()
        if "attention_score" in out.columns and min_attention_score is not None:
            out["attention_score"] = pd.to_numeric(out["attention_score"], errors="coerce")
            out = out[out["attention_score"] >= float(min_attention_score)].copy()
        if "asof_time_utc" in out.columns:
            out["asof_time_utc"] = pd.to_datetime(out["asof_time_utc"], utc=True, errors="coerce")
        if "feed_rank" in out.columns:
            out = out.sort_values(["feed_rank", "attention_score"], ascending=[True, False], na_position="last")
        elif "attention_score" in out.columns:
            out = out.sort_values("attention_score", ascending=False, na_position="last")
        if int(limit) > 0:
            out = out.head(int(limit))
        return self._resolved(out.reset_index(drop=True), mode="materialized", datasets=(dataset_key,), details=details)

    def resolve_attention_rollups(
        self,
        *,
        dataset_name: str = "attention_rollups",
        rollup_type: str | None = None,
        horizons: list[str] | None = None,
        statuses: list[str] | None = None,
        sensitivity: str | None = None,
        min_attention_score: float | None = None,
        residual_zscore_threshold: float | None = None,
        high_priority_threshold: float | None = None,
        limit: int = 10,
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        del force_refresh
        dataset_key = str(dataset_name or "attention_rollups").strip() or "attention_rollups"
        if not pipeline_store_configured():
            return self._resolved(
                pd.DataFrame(),
                mode="materialized",
                datasets=(dataset_key,),
                details={"warning": "pipeline store unavailable"},
            )

        wants_tuned_rollups = any(
            [
                sensitivity is not None,
                residual_zscore_threshold is not None,
                min_attention_score is not None,
                high_priority_threshold is not None,
                bool(normalize_horizons(horizons)),
            ]
        )
        candidate_dataset = self._attention_candidate_dataset_name(dataset_key)
        if wants_tuned_rollups and candidate_dataset:
            candidate_frame, candidate_details = self._pipeline_frame(candidate_dataset)
            if not candidate_frame.empty:
                config = self._attention_config_from_params(
                    sensitivity=sensitivity,
                    residual_zscore_threshold=residual_zscore_threshold,
                    min_attention_score=min_attention_score,
                    high_priority_threshold=high_priority_threshold,
                )
                filtered = filter_attention_events(
                    candidate_frame,
                    config=config,
                    horizons=horizons,
                    statuses=statuses,
                )
                rollups = build_attention_rollups(
                    filtered,
                    peer_group_membership=pd.DataFrame(),
                    high_priority_threshold=config.high_priority_threshold,
                )
                if dataset_key == "commodity_attention_rollups":
                    rollups = self._decorate_runtime_commodity_rollups(rollups)
                details = {**candidate_details, "filters": {"horizons": list(normalize_horizons(horizons)), "sensitivity": sensitivity or "balanced"}}
                if rollup_type and "rollup_type" in rollups.columns:
                    rollups = rollups[rollups["rollup_type"].astype(str).str.lower() == str(rollup_type).lower().strip()].copy()
                if int(limit) > 0:
                    rollups = rollups.head(int(limit))
                return self._resolved(rollups.reset_index(drop=True), mode="materialized", datasets=(candidate_dataset,), details=details)

        rollups, details = self._pipeline_frame(dataset_key)
        if rollups.empty:
            return self._resolved(rollups, mode="materialized", datasets=(dataset_key,), details=details)

        out = rollups.copy()
        if rollup_type and "rollup_type" in out.columns:
            out = out[out["rollup_type"].astype(str).str.lower() == str(rollup_type).lower().strip()].copy()
        if "asof_time_utc" in out.columns:
            out["asof_time_utc"] = pd.to_datetime(out["asof_time_utc"], utc=True, errors="coerce")
        sort_cols: list[str] = []
        ascending: list[bool] = []
        if "net_attention_score" in out.columns:
            sort_cols.append("net_attention_score")
            ascending.append(False)
        if "top_attention_score" in out.columns:
            sort_cols.append("top_attention_score")
            ascending.append(False)
        if "active_event_count" in out.columns:
            sort_cols.append("active_event_count")
            ascending.append(False)
        if sort_cols:
            out = out.sort_values(sort_cols, ascending=ascending, na_position="last")
        if int(limit) > 0:
            out = out.head(int(limit))
        return self._resolved(out.reset_index(drop=True), mode="materialized", datasets=(dataset_key,), details=details)

    def resolve_fred_dashboard(self, *, years: int, force_refresh: bool = False) -> ResolvedPayload:
        api_key = (self.fred_api_key or "").strip()
        materialized = self._try_pipeline_frames(
            ("fred_summary", "fred_observations"),
            force_refresh=force_refresh,
        )
        if materialized is not None:
            frames, details = materialized
            summary = frames["fred_summary"]
            observations = frames["fred_observations"]
            if not summary.empty and not observations.empty:
                payload = build_fred_dashboard_from_pipeline(summary, observations, years)
                return self._resolved(
                    payload,
                    mode="materialized",
                    datasets=("fred_summary", "fred_observations"),
                    details={
                        "summary": details["fred_summary"],
                        "observations": details["fred_observations"],
                        "years": years,
                    },
                )

        materialized_only = self._materialized_only_result({}, datasets=("fred_summary", "fred_observations"), details={"years": years})
        if materialized_only is not None:
            return materialized_only
        if not api_key:
            raise RuntimeError("FRED API key unavailable and no materialized FRED datasets were found.")

        payload = cached_fred_dashboard(
            "fred_dashboard",
            f"{_fred_cache_scope(api_key)}__{years}y",
            lambda: load_fred_dashboard(api_key, years=years),
            force_refresh=force_refresh,
        )
        return self._resolved(payload, mode="on_demand", datasets=("fred_dashboard",), details={"years": years})

    def resolve_yield_curve_summary(self, *, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("yield_curve_summary", force_refresh=force_refresh)
        if materialized is not None:
            frame, details = materialized
            return self._resolved(frame.reset_index(drop=True), mode="materialized", datasets=("yield_curve_summary",), details=details)

        materialized_only = self._materialized_only_result(pd.DataFrame(), datasets=("yield_curve_summary",))
        if materialized_only is not None:
            return materialized_only
        try:
            wide = load_treasury_yield_curve(years=3)
            summary = build_treasury_yield_summary(wide)
        except TreasuryYieldError as exc:
            raise RuntimeError(f"Treasury yield data unavailable and no materialized yield summary was found: {exc}") from exc
        return self._resolved(summary.reset_index(drop=True), mode="on_demand", datasets=("yield_curve_summary",), details={"source": "treasury_direct"})

    def resolve_yield_curve_observations(self, *, days: int = 365, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("yield_curve_observations", force_refresh=force_refresh)
        if materialized is not None:
            frame, details = materialized
            out = frame.copy()
            if "date" in out.columns:
                out["date"] = pd.to_datetime(out["date"], errors="coerce")
                cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=max(int(days), 1))
                out = out[out["date"] >= cutoff].copy()
            return self._resolved(out.reset_index(drop=True), mode="materialized", datasets=("yield_curve_observations",), details={**details, "days": int(days)})

        materialized_only = self._materialized_only_result(pd.DataFrame(), datasets=("yield_curve_observations",), details={"days": int(days)})
        if materialized_only is not None:
            return materialized_only
        try:
            wide = load_treasury_yield_curve(years=max(1, min(10, int(days // 365) + 1)))
            observations = build_treasury_yield_observations(wide)
        except TreasuryYieldError as exc:
            raise RuntimeError(f"Treasury yield data unavailable and no materialized yield observations were found: {exc}") from exc
        if "date" in observations.columns:
            cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=max(int(days), 1))
            observations = observations[observations["date"] >= cutoff].copy()
        return self._resolved(observations.reset_index(drop=True), mode="on_demand", datasets=("yield_curve_observations",), details={"days": int(days), "source": "treasury_direct"})

    def resolve_yield_curve_facts_1d(self, *, force_refresh: bool = False) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("yield_curve_facts_1d", force_refresh=force_refresh)
        if materialized is not None:
            frame, details = materialized
            return self._resolved(frame.reset_index(drop=True), mode="materialized", datasets=("yield_curve_facts_1d",), details=details)

        materialized_only = self._materialized_only_result(pd.DataFrame(), datasets=("yield_curve_facts_1d",))
        if materialized_only is not None:
            return materialized_only
        try:
            wide = load_treasury_yield_curve(years=1)
            facts = build_treasury_yield_facts_1d(wide, asof_time_utc=pd.Timestamp.utcnow())
        except TreasuryYieldError as exc:
            raise RuntimeError(f"Treasury yield facts unavailable and no materialized daily facts were found: {exc}") from exc
        return self._resolved(facts.reset_index(drop=True), mode="on_demand", datasets=("yield_curve_facts_1d",), details={"source": "treasury_direct"})

    def latest_job_status(self) -> ResolvedPayload:
        return self._resolved(latest_job_status_table(), mode="materialized", datasets=("job_runs",))

    def trigger_source_refresh(self, source: str) -> ResolvedPayload:
        ok, message = start_source_refresh_job(source)
        return self._resolved({"ok": ok, "message": message}, mode="command", datasets=("job_runs",), details={"source": source})
