from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
from typing import Any

import pandas as pd

from compute.analytics import performance_table
from compute.anomalies import AttentionConfig, attention_preset, build_attention_feed, build_attention_rollups, filter_attention_events, normalize_horizon, normalize_horizons
from compute.fred import build_fred_dashboard_from_pipeline
from compute.fundamentals import load_quarterly_fundamentals
from compute.ownership import normalize_share_fraction, project_account_view, project_portfolio_timeseries, project_positions_view
from compute.portfolio import (
    build_portfolio_timeseries,
    compute_holding_roc,
    filter_portfolio_timeseries_period,
    normalize_timeseries_view,
    select_holding_roc_view,
)
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
from services.company_baseline import deserialize_company_baseline_frame
from services.attention_home_1d import (
    build_attention_entity_master,
    resolve_macro_anchor_symbols,
    build_attention_home_1d,
    shortlist_attention_symbols_1d,
)
from services.company import build_company_description, load_asset_metadata, load_recent_news
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
from services.aql.evidence_index import parse_json_list
from services.market import load_price_history, scan_commodity_regimes, scan_correlation_phase_shifts, scan_daily_movers, scan_event_significance, scan_momentum_profiles
from services.options import analyze_option_candidates, load_option_chain, load_option_surface, select_option_surface_window
from services.pipeline_store import latest_job_status_table, load_dataset_frame_asof, load_latest_dataset_frame, pipeline_store_configured, start_source_refresh_job
from services.saa import load_retained_document, load_retained_document_metadata, search_retained_documents, search_retained_evidence_chunks
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


def _normalized_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _coerce_text(value).lower()).strip()


def _dedupe_text_items(items: list[str], *, limit: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        text = " ".join(str(raw or "").split()).strip()
        if not text:
            continue
        key = _normalized_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if limit is not None and len(out) >= max(int(limit), 1):
            break
    return out


def _provider_display_label(value: object) -> str:
    text = _coerce_text(value)
    lowered = text.lower()
    if "tavily" in lowered:
        return "Tavily"
    if "serpapi" in lowered or "serp api" in lowered:
        return "SerpApi"
    return text


def _coerce_bool_or_none(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    lowered = _coerce_text(value).lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    return None


def _query_tokens(value: object) -> list[str]:
    tokens = [token for token in re.split(r"[^a-z0-9]+", _coerce_text(value).lower()) if len(token) >= 2]
    return _dedupe_text_items(tokens)


def _key_contains_any(value: object, targets: list[str]) -> bool:
    haystack = _coerce_text(value)
    if not haystack or not targets:
        return False
    return any(f"|{target}|" in haystack for target in targets)


def _dates_match_filters(
    row: pd.Series,
    *,
    exact_dates: list[str],
    start_date: pd.Timestamp | None,
    end_date: pd.Timestamp | None,
) -> bool:
    candidates = parse_json_list(row.get("mentioned_dates_json"))
    published_date = _coerce_text(row.get("published_date"))
    primary_date = _coerce_text(row.get("primary_date"))
    if published_date:
        candidates.append(published_date)
    if primary_date:
        candidates.append(primary_date)
    candidates = _dedupe_text_items(candidates)
    if exact_dates and not any(item in exact_dates for item in candidates):
        return False
    if start_date is None and end_date is None:
        return True
    for value in candidates:
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(parsed):
            continue
        if start_date is not None and parsed < start_date:
            continue
        if end_date is not None and parsed > end_date:
            continue
        return True
    return False


def _is_relevant_bundle_news_item(item: dict[str, Any] | None) -> bool:
    payload = item if isinstance(item, dict) else {}
    source_kind = _coerce_text(payload.get("source_kind")).lower()
    if source_kind not in {"news", "search"} and not _coerce_text(payload.get("search_provider")):
        return False
    is_important = _coerce_bool_or_none(payload.get("is_important"))
    if is_important is None:
        return True
    return bool(is_important)


def _headline_source_label(item: dict[str, Any] | None) -> str:
    payload = item if isinstance(item, dict) else {}
    base_source = _coerce_text(payload.get("source")) or "News"
    provider = _provider_display_label(payload.get("search_provider") or payload.get("origin_provider"))
    if provider and provider.lower() not in base_source.lower():
        return f"{base_source} (via {provider})"
    return base_source


def _relevant_news_message(symbol: str, *, has_tavily: bool) -> str:
    target = _coerce_text(symbol).upper()
    if has_tavily:
        return f"No relevant catalyst found in Tavily coverage for {target} in the latest agentic run."
    return f"No relevant catalyst found in web coverage for {target} in the latest agentic run."


def _bundle_recent_headlines(bundle: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for field in ("evidence", "background_context"):
        for item in list(bundle.get(field) or []):
            payload = item if isinstance(item, dict) else {}
            if not _is_relevant_bundle_news_item(payload):
                continue
            headline = _coerce_text((item or {}).get("headline"))
            excerpt = _coerce_text((item or {}).get("display_excerpt") or (item or {}).get("summary"))
            if not headline and not excerpt:
                continue
            url = _coerce_text((item or {}).get("url"))
            dedupe_key = (_normalized_text(headline or excerpt), url.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            rows.append(
                {
                    "headline": headline or excerpt,
                    "source": _headline_source_label(payload),
                    "published_at": _coerce_text((item or {}).get("published_at")),
                    "url": url,
                    "summary": excerpt,
                    "search_provider": _coerce_text(payload.get("search_provider")),
                    "evidence_role": _coerce_text(payload.get("evidence_role")),
                }
            )
            if len(rows) >= max(int(limit), 1):
                return rows
    return rows


def _bundle_news_summary_lines(bundle: dict[str, Any], recent_headlines: list[dict[str, Any]]) -> list[str]:
    if not recent_headlines:
        return []
    same_day_count = sum(1 for item in recent_headlines if _coerce_text((item or {}).get("evidence_role")).lower() == "same_day")
    background_count = max(int(len(recent_headlines)) - int(same_day_count), 0)
    provider_labels = _dedupe_text_items(
        [
            _provider_display_label((item or {}).get("search_provider"))
            for item in recent_headlines
            if _provider_display_label((item or {}).get("search_provider"))
        ],
        limit=3,
    )
    intro = f"{same_day_count} same-day important item(s) and {background_count} background item(s) from agentic research"
    if provider_labels:
        intro = f"{intro} via {', '.join(provider_labels)}"
    lines = [intro + "."]
    for item in recent_headlines[:4]:
        headline = _coerce_text((item or {}).get("headline"))
        if headline:
            lines.append(headline)
    return _dedupe_text_items(lines, limit=5)


def _bundle_description_text(bundle: dict[str, Any], *, fallback: str = "") -> str:
    fields = [
        _coerce_text(bundle.get("headline")),
        _coerce_text(bundle.get("what_changed_text")),
        _coerce_text(bundle.get("why_now_text")),
    ]
    merged = _dedupe_text_items(fields, limit=3)
    if merged:
        return " ".join(merged)
    return _coerce_text(fallback)


def _is_template_company_background(text: str) -> bool:
    return TEMPLATE_COMPANY_BACKGROUND_PHRASE in _coerce_text(text).lower()


def _is_low_signal_company_context(text: object) -> bool:
    cleaned = " ".join(str(text or "").split()).strip().lower()
    if not cleaned:
        return True
    if re.fullmatch(r"\d+\s+recent article\(s\) over roughly the last \d+\s+day\(s\); tone is [^.]+\.", cleaned):
        return True
    return (
        cleaned.startswith("no relevant catalyst found")
        or cleaned.startswith("company background is unavailable")
        or cleaned
        == "the current narrative is still thin, so the best read comes from the linked price action and recent headlines."
    )


def _ensure_company_background_text(
    symbol: str,
    *,
    company_name: str = "",
    current_text: str = "",
) -> str:
    existing = _coerce_text(current_text)
    if existing and not _is_template_company_background(existing):
        return existing
    target = _coerce_text(symbol).upper()
    name = _coerce_text(company_name)
    generated = ""
    try:
        generated = build_company_description(
            target,
            {"name": name or target},
            {},
            {},
            news_payload={"articles": pd.DataFrame()},
        )
    except Exception:
        generated = ""
    generated = _coerce_text(generated)
    if generated and not _is_template_company_background(generated):
        return generated
    if name:
        return f"{name} ({target}) is a publicly traded company."
    if target:
        return f"{target} is a publicly traded company."
    return "Company background is unavailable in the latest snapshot."


def _bundle_web_signal_score(bundle: dict[str, Any] | None) -> int:
    payload = bundle if isinstance(bundle, dict) else {}
    if not payload:
        return 0

    recent_headlines = _bundle_recent_headlines(payload, limit=12)
    web_item_count = 0
    provider_count = 0
    providers: set[str] = set()
    for field in ("evidence", "background_context"):
        for item in list(payload.get(field) or []):
            candidate = item if isinstance(item, dict) else {}
            if not _is_relevant_bundle_news_item(candidate):
                continue
            source_kind = _coerce_text(candidate.get("source_kind")).lower()
            provider = _provider_display_label(candidate.get("search_provider") or candidate.get("origin_provider"))
            source = _coerce_text(candidate.get("source")).lower()
            if source_kind not in {"news", "search"} and not provider:
                continue
            web_item_count += 1
            if provider:
                providers.add(provider.lower())
            elif "tavily" in source:
                providers.add("tavily")
            elif "serpapi" in source or "serp api" in source:
                providers.add("serpapi")
    provider_count = len(providers)

    same_day_count = max(int(payload.get("same_day_evidence_count") or 0), 0)
    important_count = max(int(payload.get("important_news_count") or 0), 0)
    return (
        same_day_count * 100
        + important_count * 20
        + len(recent_headlines) * 10
        + web_item_count * 3
        + provider_count
    )


def _precomputed_symbol_bundles_only() -> bool:
    raw = str(os.getenv("ATTENTION_SYMBOL_BUNDLE_PRECOMPUTED_ONLY") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _overlay_background_payload_from_bundle(
    symbol: str,
    *,
    base_payload: dict[str, Any] | None,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(base_payload or {})
    normalized_symbol = _coerce_text(symbol).upper()
    bundle_recent_headlines = _bundle_recent_headlines(bundle, limit=6)
    base_recent_headlines = [item for item in list(payload.get("recent_headlines") or []) if isinstance(item, dict)]
    recent_headlines = bundle_recent_headlines or base_recent_headlines
    source_trace = payload.get("source_trace")
    if not isinstance(source_trace, dict):
        source_trace = {}

    provider_labels = _dedupe_text_items(
        [
            _provider_display_label((item or {}).get("search_provider"))
            for item in recent_headlines
            if _provider_display_label((item or {}).get("search_provider"))
        ],
        limit=3,
    )
    if not provider_labels:
        provider_labels = _dedupe_text_items([_provider_display_label(item) for item in list(source_trace.get("news_provider_mix") or [])], limit=3)
    has_tavily = any(label.lower() == "tavily" for label in provider_labels)

    base_description_text = _coerce_text(payload.get("description_text"))
    company_background_text = _ensure_company_background_text(
        normalized_symbol,
        company_name=_coerce_text(payload.get("company_name")),
        current_text=_coerce_text(payload.get("company_background_text")) or base_description_text,
    )
    bundle_description_text = _bundle_description_text(bundle, fallback="")
    base_summary_lines = _dedupe_text_items([_coerce_text(item) for item in list(payload.get("news_summary_lines") or [])], limit=5)
    bundle_summary_lines = _bundle_news_summary_lines(bundle, bundle_recent_headlines)

    if bundle_recent_headlines:
        description_text = bundle_description_text or base_description_text
        summary_lines = bundle_summary_lines or base_summary_lines
    elif base_recent_headlines:
        description_text = base_description_text or bundle_description_text
        summary_lines = base_summary_lines or bundle_summary_lines
    else:
        description_text = ""
        summary_lines = []

    if not description_text:
        description_text = _relevant_news_message(normalized_symbol, has_tavily=has_tavily)
    if not summary_lines:
        summary_lines = [description_text]

    payload.update(
        {
            "symbol": normalized_symbol,
            "company_background_text": company_background_text,
            "description_text": description_text,
            "news_summary_lines": summary_lines,
            "news_summary_lines_json": json.dumps(summary_lines, ensure_ascii=False, default=str),
            "recent_headlines": recent_headlines,
            "recent_headlines_json": json.dumps(recent_headlines, ensure_ascii=False, default=str),
            "run_id": _coerce_text(bundle.get("run_id") or payload.get("run_id")),
            "asof_time_utc": _coerce_text(payload.get("asof_time_utc")),
            "prompt_version": _coerce_text(bundle.get("prompt_version") or payload.get("prompt_version")),
            "model_name": _coerce_text(bundle.get("model_name") or payload.get("model_name") or "agentic"),
        }
    )
    existing_source = _coerce_text(source_trace.get("source"))
    existing_evidence_count = int(source_trace.get("evidence_count") or 0)
    existing_same_day_count = int(source_trace.get("same_day_evidence_count") or 0)
    existing_important_count = int(source_trace.get("important_news_count") or 0)
    bundle_evidence_count = int(bundle.get("evidence_count") or 0)
    bundle_same_day_count = int(bundle.get("same_day_evidence_count") or 0)
    bundle_important_count = int(bundle.get("important_news_count") or 0)

    source_trace.update(
        {
            "bundle_id": _coerce_text(bundle.get("bundle_id")) or f"symbol::{normalized_symbol}",
            "bundle_type": _coerce_text(bundle.get("bundle_type")) or "symbol",
            "source_summary": _coerce_text(bundle.get("source_summary") or source_trace.get("source_summary")),
            "evidence_count": max(bundle_evidence_count, existing_evidence_count),
            "same_day_evidence_count": max(bundle_same_day_count, existing_same_day_count),
            "important_news_count": max(bundle_important_count, existing_important_count),
            "relevant_news_count": int(len(recent_headlines)),
            "news_provider_mix": provider_labels,
            "source": "attention_research_bundle" if bundle_recent_headlines else (existing_source or "attention_ticker_background_snapshots"),
        }
    )
    payload["source_trace"] = source_trace
    payload["source_trace_json"] = json.dumps(source_trace, ensure_ascii=False, default=str)
    return payload


ATTENTION_HOME_SNAPSHOT_DATASETS = ("attention_home_snapshots_1d", "attention_home_1d")
ATTENTION_BUNDLE_SNAPSHOT_DATASETS = ("attention_bundle_snapshots", "attention_research_bundles")
ATTENTION_TICKER_SNAPSHOT_DATASETS = ("attention_ticker_snapshots_1d",)
ATTENTION_TICKER_BACKGROUND_DATASETS = ("attention_ticker_background_snapshots",)
COMPANY_BASELINE_DATASETS = ("company_baselines",)
MARKET_OPPORTUNITY_FEED_DATASETS = ("market_opportunity_feed",)
PAGE_AGENTIC_SUMMARY_DATASETS = ("page_agentic_summaries",)
TEMPLATE_COMPANY_BACKGROUND_PHRASE = "is being tracked here as an individual company narrative inside the market dashboard"
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
    "macro_release_events_1d",
    "attention_macro_context_1d",
    "macro_causal_graph_edges_v1",
    "macro_relationship_checks_1d",
    "attention_hypotheses_1d",
    "attention_ticker_snapshots_1d",
    "attention_ticker_background_snapshots",
    "attention_home_snapshots_1d",
    "attention_bundle_snapshots",
)

LEGACY_ATTENTION_TEXT_SNIPPETS = (
    "market-wide relief read",
    "renewed market stress",
    "rates-relief move broadens across market activity",
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

    def _attention_home_coverage_summary(self, *, force_refresh: bool) -> dict[str, Any]:
        materialized = self._first_materialized_frame(
            ATTENTION_HOME_SNAPSHOT_DATASETS,
            force_refresh=force_refresh,
        )
        if materialized is None:
            return {}
        _, frame, _ = materialized
        payload = deserialize_attention_home_payload(frame)
        return dict(payload.get("coverage_summary") or {}) if isinstance(payload, dict) else {}

    def _macro_feed_provenance_details(self, *, force_refresh: bool) -> dict[str, Any]:
        dataset_names = (
            "macro_release_events_1d",
            "macro_causal_graph_edges_v1",
            "macro_relationship_checks_1d",
            "attention_hypotheses_1d",
        )
        dataset_versions: dict[str, str] = {}
        staleness_summary: dict[str, Any] = {}
        relationship_summary = {"holding": 0, "mixed": 0, "broken": 0, "unresolved": 0}
        hypothesis_summary = {"supported": 0, "continuation": 0, "conflicting": 0, "unresolved": 0}
        for dataset_name in dataset_names:
            materialized = self._try_pipeline_frame(dataset_name, force_refresh=force_refresh)
            if materialized is None:
                continue
            frame, details = materialized
            version_id = _coerce_text(details.get("dataset_version_id"))
            if version_id:
                dataset_versions[dataset_name] = version_id
            if dataset_name == "macro_release_events_1d" and not frame.empty:
                if "release_time_utc" in frame.columns:
                    release_times = pd.to_datetime(frame["release_time_utc"], utc=True, errors="coerce").dropna()
                    if not release_times.empty:
                        now_utc = pd.Timestamp.utcnow()
                        if now_utc.tzinfo is None:
                            now_utc = now_utc.tz_localize("UTC")
                        else:
                            now_utc = now_utc.tz_convert("UTC")
                        age_hours = (now_utc - release_times.max()).total_seconds() / 3600.0
                        staleness_summary["macro_release_events_age_hours"] = round(float(max(age_hours, 0.0)), 2)
                if "surprise_score" in frame.columns:
                    staleness_summary["macro_release_count"] = int(len(frame))
            if dataset_name == "macro_relationship_checks_1d" and not frame.empty and "consistency_status" in frame.columns:
                status_counts = frame["consistency_status"].astype(str).str.lower().value_counts()
                for key in relationship_summary:
                    relationship_summary[key] = int(status_counts.get(key, 0))
            if dataset_name == "attention_hypotheses_1d" and not frame.empty and "support_status" in frame.columns:
                status_counts = frame["support_status"].astype(str).str.lower().value_counts()
                for key in hypothesis_summary:
                    hypothesis_summary[key] = int(status_counts.get(key, 0))

        coverage_summary = self._attention_home_coverage_summary(force_refresh=force_refresh)
        release_visibility_summary = {
            "detected": int(coverage_summary.get("macro_release_detected_count") or 0),
            "qualifying": int(coverage_summary.get("macro_release_qualifying_count") or 0),
            "promoted": int(coverage_summary.get("macro_release_promoted_count") or 0),
            "suppressed": int(coverage_summary.get("macro_release_suppressed_count") or 0),
        }
        macro_live_enabled = bool((os.getenv("ATTENTION_MACRO_SCORE_LIVE_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"})
        macro_shadow_enabled = bool((os.getenv("ATTENTION_MACRO_SCORE_SHADOW_ENABLED") or "1").strip().lower() not in {"0", "false", "no", "off"})
        return {
            "macro_dataset_version_ids": dataset_versions,
            "macro_staleness_summary": staleness_summary,
            "macro_scoring_mode": "live" if macro_live_enabled else ("shadow" if macro_shadow_enabled else "disabled"),
            "macro_relationship_summary": relationship_summary,
            "macro_hypothesis_summary": hypothesis_summary,
            "macro_release_visibility_summary": release_visibility_summary,
        }

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

    def _payload_uses_stat_dump_text(self, payload: dict[str, Any]) -> bool:
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

    def _bundle_uses_stat_dump_text(self, payload: dict[str, Any]) -> bool:
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
            lambda: search_symbol_news_payload(
                target,
                company_name=company_name,
                max_results=8,
                llm_client=load_llm_client(),
            ),
            force_refresh=force_refresh,
            version=5,
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

    def _resolve_symbol_agentic_bundle(self, symbol: str, *, force_refresh: bool) -> dict[str, Any]:
        target = str(symbol or "").upper().strip()
        if not target or self.materialized_only or self.cfg is None:
            return {}
        cache_scope = f"{pd.Timestamp.utcnow().date().isoformat()}__{target}"

        def _fetch() -> dict[str, Any]:
            company_name = ""
            universe_materialized = self._try_pipeline_frame("universe_snapshot", force_refresh=force_refresh)
            if universe_materialized is not None:
                frame, _ = universe_materialized
                if isinstance(frame, pd.DataFrame) and not frame.empty and "symbol" in frame.columns:
                    scoped = frame.copy()
                    scoped["symbol"] = scoped["symbol"].astype(str).str.upper().str.strip()
                    match = scoped[scoped["symbol"] == target].head(1)
                    if not match.empty:
                        for column in ("security_name", "company_name", "name"):
                            candidate = _coerce_text(match.iloc[0].get(column))
                            if candidate:
                                company_name = candidate
                                break

            news_payload = self._resolve_web_search_news(
                target,
                company_name=company_name,
                force_refresh=force_refresh,
            )
            if not isinstance(news_payload, dict):
                news_payload = {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}

            try:
                context_payload = self.resolve_attention_context(
                    target,
                    force_refresh=force_refresh,
                ).payload
            except Exception:
                context_payload = {}

            try:
                movers = self.resolve_daily_movers(
                    symbols=[target],
                    force_refresh=force_refresh,
                ).payload
            except Exception:
                movers = pd.DataFrame()
            if not isinstance(movers, pd.DataFrame):
                movers = pd.DataFrame()

            if not movers.empty and "symbol" in movers.columns:
                movers = movers.copy()
                movers["symbol"] = movers["symbol"].astype(str).str.upper().str.strip()
                movers = movers[movers["symbol"] == target].head(1).reset_index(drop=True)
            if movers.empty:
                movers = pd.DataFrame(
                    [
                        {
                            "symbol": target,
                            "change_pct": 0.1,
                            "close": pd.NA,
                            "prev_close": pd.NA,
                            "volume": pd.NA,
                            "dollar_volume": pd.NA,
                        }
                    ]
                )
            for column in ["change_pct", "close", "prev_close", "volume", "dollar_volume"]:
                movers[column] = pd.to_numeric(movers.get(column), errors="coerce")
            if pd.isna(movers.iloc[0].get("change_pct")):
                close = pd.to_numeric(movers.iloc[0].get("close"), errors="coerce")
                prev_close = pd.to_numeric(movers.iloc[0].get("prev_close"), errors="coerce")
                if pd.notna(close) and pd.notna(prev_close) and float(prev_close) != 0.0:
                    movers.at[0, "change_pct"] = (float(close) - float(prev_close)) / float(prev_close) * 100.0
                else:
                    movers.at[0, "change_pct"] = 0.1
            if pd.isna(movers.iloc[0].get("dollar_volume")):
                close = pd.to_numeric(movers.iloc[0].get("close"), errors="coerce")
                volume = pd.to_numeric(movers.iloc[0].get("volume"), errors="coerce")
                if pd.notna(close) and pd.notna(volume):
                    movers.at[0, "dollar_volume"] = float(close) * float(volume)

            bars_by_symbol: dict[str, pd.DataFrame] = {}
            try:
                bars_by_symbol = _make_api(self.cfg).get_stock_bars(
                    [target],
                    start=datetime.now(timezone.utc) - pd.Timedelta(days=120),
                    end=datetime.now(timezone.utc),
                    timeframe="1Day",
                    feed="iex",
                )
            except Exception:
                bars_by_symbol = {}

            filings_frame = self._resolve_attention_edgar_filings([target], force_refresh=force_refresh)
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

            attention_rows = pd.DataFrame(
                [
                    {
                        "entity_id": target,
                        "attention_score": 1.0,
                        "severity_score": 1.0,
                        "observed_value": float(pd.to_numeric(movers.iloc[0].get("change_pct"), errors="coerce") or 0.1),
                    }
                ]
            )
            artifacts = build_bottom_up_attention_artifacts(
                movers,
                attention_rows=attention_rows,
                bars_by_symbol=bars_by_symbol,
                news_payloads={target: news_payload if isinstance(news_payload, dict) else {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}},
                context_payloads={target: context_payload if isinstance(context_payload, dict) else {}},
                entity_master=build_attention_entity_master([target]),
                holdings=[],
                generated_at_utc=pd.Timestamp.utcnow(),
                filings_frame=filings_frame,
                fred_summary_frame=fred_summary_frame,
                yield_curve_facts_frame=yield_curve_facts_frame,
                llm_client=load_llm_client(),
                embedding_client=load_embedding_client(),
                top_events_limit=1,
                must_read_limit=1,
                unresolved_limit=1,
                research_limit=1,
            )
            bundle = dict((artifacts.bundle_map or {}).get(f"symbol::{target}") or {})
            return bundle

        return cached_scalar_dict(
            "attention_symbol_agentic_bundle",
            cache_scope,
            _fetch,
            force_refresh=force_refresh,
            version=5,
        )

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
                "homepage_graph": {},
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
            topology_universe_frame = pd.DataFrame()
            if pipeline_store_configured():
                try:
                    fred_summary_frame, _ = self._pipeline_frame("fred_summary")
                except Exception:
                    fred_summary_frame = pd.DataFrame()
                try:
                    yield_curve_facts_frame, _ = self._pipeline_frame("yield_curve_facts_1d")
                except Exception:
                    yield_curve_facts_frame = pd.DataFrame()
                try:
                    topology_universe_frame, _ = self._pipeline_frame("entity_taxonomy_labels")
                except Exception:
                    topology_universe_frame = pd.DataFrame()

            artifacts = build_bottom_up_attention_artifacts(
                movers,
                attention_rows=attention_rows,
                bars_by_symbol=bars_by_symbol,
                news_payloads=news_payloads,
                context_payloads=context_payloads,
                entity_master=entity_master,
                topology_universe_frame=topology_universe_frame,
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
        materialized_only = self._materialized_only_result({}, datasets=("account",))
        if materialized_only is not None:
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
        materialized_timeseries = self._try_pipeline_frame("portfolio_timeseries_snapshot", force_refresh=force_refresh)
        if materialized_timeseries is not None:
            frame, details = materialized_timeseries
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                filtered = filter_portfolio_timeseries_period(frame, period)
                materialized_details = dict(details)
                materialized_details["period"] = period
                return self._resolved(filtered.reset_index(drop=True), mode="materialized", datasets=("portfolio_timeseries_snapshot",), details=materialized_details)
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
        materialized_profiles = self._try_pipeline_frame("momentum_profiles", force_refresh=force_refresh)
        if materialized_profiles is not None:
            frame, details = materialized_profiles
            if isinstance(frame, pd.DataFrame):
                payload = select_holding_roc_view(frame, normalized_symbols)
                materialized_details = dict(details)
                materialized_details.update({"days": days, "symbols": normalized_symbols})
                return self._resolved(payload, mode="materialized", datasets=("momentum_profiles",), details=materialized_details)
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
        requested_symbols = sorted({str(symbol).upper().strip() for symbol in list(symbols or []) if str(symbol).strip()})
        materialized_filter_details: dict[str, Any] = {}
        materialized = self._try_pipeline_frame("daily_movers", force_refresh=force_refresh)
        if materialized is not None:
            pipeline, details = materialized
            if not pipeline.empty:
                source_row_count = int(len(pipeline))
                if requested_symbols and "symbol" in pipeline.columns:
                    allowed = set(requested_symbols)
                    filtered = pipeline[pipeline["symbol"].astype(str).str.upper().isin(allowed)].copy()
                    filtered_symbols = sorted(
                        {
                            str(item).upper().strip()
                            for item in filtered["symbol"].dropna().tolist()
                            if str(item).strip()
                        }
                    )
                    missing_symbols = sorted(symbol for symbol in requested_symbols if symbol not in set(filtered_symbols))
                    if (not missing_symbols and not filtered.empty) or self.materialized_only:
                        resolved_details = dict(details)
                        resolved_details.update(
                            {
                                "symbols": requested_symbols,
                                "materialized_rows_seen": source_row_count,
                                "filtered_rows": int(len(filtered)),
                                "filtered_symbols_present": filtered_symbols,
                            }
                        )
                        if filtered.empty:
                            resolved_details.update(
                                {
                                    "empty_reason": "materialized_symbol_filter_no_matches",
                                    "filtered_symbols_missing": missing_symbols or requested_symbols,
                                    "fallback_attempted": False,
                                    "next_tool_hint": "Retry with force_refresh=true or use dataset.price_history for explicit symbols.",
                                    "user_safe_explanation": "The materialized movers snapshot did not include the requested symbols.",
                                }
                            )
                        return self._resolved(filtered.reset_index(drop=True), mode="materialized", datasets=("daily_movers",), details=resolved_details)
                    materialized_symbols = sorted(
                        {
                            str(item).upper().strip()
                            for item in pipeline["symbol"].dropna().tolist()
                            if str(item).strip()
                        }
                    )
                    materialized_filter_details = {
                        "materialized_rows_seen": source_row_count,
                        "filtered_rows": int(len(filtered)),
                        "filtered_symbols_present": filtered_symbols,
                        "filtered_symbols_missing": missing_symbols or requested_symbols,
                        "materialized_symbols_sample": materialized_symbols[:25],
                        "materialized_filter_empty_reason": (
                            "materialized_symbol_filter_partial_matches"
                            if filtered_symbols
                            else "materialized_symbol_filter_no_matches"
                        ),
                    }
                elif not requested_symbols:
                    return self._resolved(pipeline.reset_index(drop=True), mode="materialized", datasets=("daily_movers",), details=details)

        universe = requested_symbols if requested_symbols else self._attention_home_equity_universe(force_refresh=force_refresh)
        universe = sorted({str(symbol).upper().strip() for symbol in universe if str(symbol).strip()})
        if not universe:
            return self._resolved(
                pd.DataFrame(),
                mode="on_demand",
                datasets=("daily_movers",),
                details={
                    "symbols": [],
                    "empty_reason": "empty_universe",
                    "warning": "empty_universe",
                    "fallback_attempted": bool(materialized_filter_details),
                    "user_safe_explanation": "No symbols were available for the movers lookup.",
                },
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
        resolved_details = {
            "symbols": sorted(universe),
            "fallback_attempted": bool(materialized_filter_details),
            **materialized_filter_details,
        }
        if isinstance(frame, pd.DataFrame) and frame.empty:
            resolved_details.update(
                {
                    "empty_reason": "on_demand_no_mover_rows",
                    "next_tool_hint": "Use dataset.price_history for explicit symbols and compute the event-window move from raw prices.",
                    "user_safe_explanation": "No daily mover rows were returned for the requested symbols; raw price history may still be available.",
                }
            )
        return self._resolved(frame, mode="on_demand", datasets=("daily_movers",), details=resolved_details)

    def resolve_event_significance(
        self,
        *,
        event_date: str,
        symbols: list[str],
        pre_window_days: int = 60,
        post_window_days: int = 30,
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        clean_symbols = sorted({str(s).upper().strip() for s in symbols if str(s).strip()})
        symbol_scope = dataset_scope("event-sig", ",".join(clean_symbols) + f"_{event_date}_{pre_window_days}_{post_window_days}")
        cache_key = f"{_alpaca_cache_scope(self.cfg)}__{symbol_scope}" if self.cfg is not None else f"missing-config__{symbol_scope}"
        frame = cached_frame(
            "event_significance",
            cache_key,
            lambda: scan_event_significance(
                _make_api(self.cfg),
                event_date=event_date,
                symbols=clean_symbols,
                pre_window_days=pre_window_days,
                post_window_days=post_window_days,
            ),
            force_refresh=force_refresh,
        )
        diagnostics = dict(getattr(frame, "attrs", {}).get("diagnostics") or {}) if isinstance(frame, pd.DataFrame) else {}
        details: dict[str, Any] = {
            "event_date": event_date,
            "symbols": clean_symbols,
            "pre_window_days": pre_window_days,
            "post_window_days": post_window_days,
            **diagnostics,
        }
        if isinstance(frame, pd.DataFrame) and frame.empty:
            details.setdefault("empty_reason", "no_event_significance_rows")
            details.setdefault("required_event_observations", 3)
            details.setdefault("next_tool_hint", "Use dataset.price_history or event-window returns when significance windows are too short.")
            reason = str(details.get("empty_reason") or "")
            if reason == "insufficient_observations":
                details.setdefault(
                    "user_safe_explanation",
                    "The event study did not have enough observations after the event date for a significance test.",
                )
            else:
                details.setdefault(
                    "user_safe_explanation",
                    "The event study produced no qualifying rows; raw price history may still answer event-window movement.",
                )
        return self._resolved(
            frame,
            mode="on_demand",
            datasets=("event_significance",),
            details=details,
        )

    def resolve_momentum_profiles(self, *, days: int = 180, symbols: list[str] | None = None, force_refresh: bool = False) -> ResolvedPayload:
        requested_symbols = sorted({str(symbol).upper().strip() for symbol in (symbols or []) if str(symbol).strip()})
        materialized = self._try_pipeline_frame("momentum_profiles", force_refresh=force_refresh)
        if materialized is not None:
            pipeline, details = materialized
            if not pipeline.empty:
                filtered = pipeline
                if requested_symbols and "symbol" in pipeline.columns:
                    allowed = set(requested_symbols)
                    filtered = pipeline[pipeline["symbol"].astype(str).str.upper().isin(allowed)].copy()
                if not requested_symbols or not filtered.empty or self.materialized_only:
                    materialized_details = dict(details)
                    materialized_details.update({"days": days, "symbols": requested_symbols})
                    return self._resolved(
                        filtered.reset_index(drop=True),
                        mode="materialized",
                        datasets=("momentum_profiles",),
                        details=materialized_details,
                    )

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
        materialized_payload: dict[str, Any] = {}
        materialized_mode = "on_demand"
        materialized_datasets: tuple[str, ...] = ATTENTION_TICKER_BACKGROUND_DATASETS
        materialized_details: dict[str, Any] = {"ticker": target}
        baseline_payload: dict[str, Any] = {}
        baseline_datasets: tuple[str, ...] = ()
        baseline_details: dict[str, Any] = {}
        baseline = self._first_materialized_frame(
            COMPANY_BASELINE_DATASETS,
            force_refresh=force_refresh,
        )
        if baseline is not None:
            baseline_dataset_name, baseline_frame, baseline_meta = baseline
            baseline_payload = deserialize_company_baseline_frame(baseline_frame, target)
            if baseline_payload:
                baseline_datasets = (baseline_dataset_name,)
                baseline_details = {**baseline_meta, "ticker": target}
        materialized = self._first_materialized_frame(
            ATTENTION_TICKER_BACKGROUND_DATASETS,
            force_refresh=force_refresh,
        )
        if materialized is not None:
            dataset_name, frame, details = materialized
            payload = deserialize_attention_ticker_background_frame(frame, target)
            if payload:
                materialized_payload = payload
                materialized_mode = "materialized"
                materialized_datasets = (dataset_name,)
                materialized_details = {**details, "ticker": target}
        if materialized_payload:
            materialized_payload["company_background_text"] = _ensure_company_background_text(
                target,
                company_name=_coerce_text(materialized_payload.get("company_name")),
                current_text=_coerce_text(materialized_payload.get("company_background_text"))
                or _coerce_text(materialized_payload.get("description_text")),
            )
            baseline_background = _coerce_text(baseline_payload.get("company_background_text"))
            if baseline_background and _is_low_signal_company_context(materialized_payload.get("company_background_text")):
                materialized_payload["company_background_text"] = baseline_background
            if baseline_background and _is_low_signal_company_context(materialized_payload.get("description_text")):
                materialized_payload["description_text"] = baseline_background
            for key in ("company_name", "business_lens", "company_background_text"):
                if not _coerce_text(materialized_payload.get(key)) and _coerce_text(baseline_payload.get(key)):
                    materialized_payload[key] = baseline_payload.get(key)
            if baseline_payload:
                trace = materialized_payload.get("source_trace") if isinstance(materialized_payload.get("source_trace"), dict) else {}
                materialized_payload["source_trace"] = {**trace, "company_baseline_source": _coerce_text(baseline_payload.get("baseline_source"))}
                materialized_datasets = tuple(dict.fromkeys([*materialized_datasets, *baseline_datasets]))
        elif baseline_payload:
            materialized_payload = {
                "symbol": target,
                "company_name": _coerce_text(baseline_payload.get("company_name")),
                "business_lens": _coerce_text(baseline_payload.get("business_lens")),
                "company_background_text": _coerce_text(baseline_payload.get("company_background_text")),
                "description_text": _coerce_text(baseline_payload.get("description_text")),
                "news_summary_lines": [],
                "recent_headlines": [],
                "source_trace": {
                    "source": _coerce_text(baseline_payload.get("baseline_source")) or "company_baselines",
                    "company_baseline_source": _coerce_text(baseline_payload.get("baseline_source")) or "company_baselines",
                },
                "run_id": _coerce_text(baseline_payload.get("run_id")),
                "asof_time_utc": _coerce_text(baseline_payload.get("asof_time_utc")),
            }
            materialized_mode = "materialized"
            materialized_datasets = baseline_datasets or COMPANY_BASELINE_DATASETS
            materialized_details = baseline_details or {"ticker": target}

        if self.materialized_only:
            if materialized_payload:
                return self._resolved(
                    materialized_payload,
                    mode=materialized_mode,
                    datasets=materialized_datasets,
                    details=materialized_details,
                )
            return self._resolved(
                {},
                mode="materialized",
                datasets=ATTENTION_TICKER_BACKGROUND_DATASETS + COMPANY_BASELINE_DATASETS,
                details={"ticker": target, "materialized_only": True},
            )

        bundle_resolved = self.resolve_attention_research_bundle(
            f"symbol::{target}",
            force_refresh=force_refresh,
        )
        bundle_payload = bundle_resolved.payload if isinstance(bundle_resolved.payload, dict) else {}
        has_bundle_story = bool(
            _coerce_text(bundle_payload.get("headline"))
            or _coerce_text(bundle_payload.get("what_changed_text"))
            or _coerce_text(bundle_payload.get("why_now_text"))
            or list(bundle_payload.get("evidence") or [])
        )
        if has_bundle_story:
            merged_payload = _overlay_background_payload_from_bundle(
                target,
                base_payload=materialized_payload,
                bundle=bundle_payload,
            )
            merged_datasets = tuple(
                dict.fromkeys(
                    list(materialized_datasets if materialized_payload else [])
                    + list(bundle_resolved.provenance.datasets)
                    + list(baseline_datasets)
                )
            )
            merged_details = dict(materialized_details if materialized_payload else {"ticker": target})
            merged_details.update(
                {
                    "bundle_mode": bundle_resolved.provenance.mode,
                    "bundle_id": _coerce_text(bundle_payload.get("bundle_id")) or f"symbol::{target}",
                }
            )
            return self._resolved(
                merged_payload,
                mode=("materialized" if materialized_payload else "on_demand"),
                datasets=merged_datasets or ATTENTION_TICKER_BACKGROUND_DATASETS,
                details=merged_details,
            )

        if materialized_payload:
            return self._resolved(
                materialized_payload,
                mode=materialized_mode,
                datasets=materialized_datasets,
                details=materialized_details,
            )

        return self._resolved(
            {},
            mode="on_demand",
            datasets=ATTENTION_TICKER_BACKGROUND_DATASETS + COMPANY_BASELINE_DATASETS,
            details={"ticker": target},
        )

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
                and not self._payload_uses_stat_dump_text(payload)
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

    def resolve_market_opportunity_feed(
        self,
        *,
        business_filter: str = "All Market",
        selected_horizon_col: str = "return_1m_pct",
        selected_horizon_label: str = "1 Month",
        symbols: list[str] | None = None,
        limit: int = 80,
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        normalized_symbols = [
            _coerce_text(symbol).upper()
            for symbol in list(symbols or [])
            if _coerce_text(symbol)
        ]
        details: dict[str, Any] = {
            "business_filter": _coerce_text(business_filter) or "All Market",
            "selected_horizon_col": _coerce_text(selected_horizon_col) or "return_1m_pct",
            "selected_horizon_label": _coerce_text(selected_horizon_label) or "1 Month",
            "symbol_count": int(len(normalized_symbols)),
            "limit": int(limit),
        }
        materialized = self._first_materialized_frame(
            MARKET_OPPORTUNITY_FEED_DATASETS,
            force_refresh=False,
        )
        if materialized is not None:
            dataset_name, frame, materialized_details = materialized
            from services.market_opportunity import select_market_opportunity_feed

            payload = select_market_opportunity_feed(
                frame,
                business_filter=business_filter,
                selected_horizon_col=selected_horizon_col,
                symbols=normalized_symbols,
                limit=limit,
            )
            resolved_details = {**materialized_details, **details}
            if isinstance(payload, pd.DataFrame) and payload.empty:
                resolved_details["warning"] = "No materialized market opportunity rows matched this view."
            return self._resolved(
                payload,
                mode="materialized",
                datasets=(dataset_name,),
                details=resolved_details,
            )

        materialized_only = self._materialized_only_result(
            pd.DataFrame(),
            datasets=MARKET_OPPORTUNITY_FEED_DATASETS,
            details=details,
        )
        if materialized_only is not None:
            return materialized_only
        return self._resolved(
            pd.DataFrame(),
            mode="unavailable",
            datasets=MARKET_OPPORTUNITY_FEED_DATASETS,
            details={**details, "warning": "No materialized market opportunity feed is available."},
        )

    def resolve_page_agentic_summary(
        self,
        *,
        surface: str,
        context_signature: str,
        ticker: str = "",
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        details: dict[str, Any] = {
            "surface": _coerce_text(surface),
            "context_signature": _coerce_text(context_signature),
            "ticker": _coerce_text(ticker).upper(),
        }
        materialized = self._first_materialized_frame(
            PAGE_AGENTIC_SUMMARY_DATASETS,
            force_refresh=False,
        )
        if materialized is not None:
            dataset_name, frame, materialized_details = materialized
            from services.page_agentic_summary import materialized_page_agentic_summary

            payload = materialized_page_agentic_summary(
                frame,
                surface=surface,
                context_signature=context_signature,
                ticker=ticker,
            )
            if payload:
                return self._resolved(
                    payload,
                    mode="materialized",
                    datasets=(dataset_name,),
                    details={**materialized_details, **details},
                )

        materialized_only = self._materialized_only_result(
            {},
            datasets=PAGE_AGENTIC_SUMMARY_DATASETS,
            details=details,
        )
        if materialized_only is not None:
            return materialized_only
        return self._resolved(
            {},
            mode="unavailable",
            datasets=PAGE_AGENTIC_SUMMARY_DATASETS,
            details={**details, "warning": "No materialized page summary matched this view."},
        )

    def resolve_attention_research_bundle(self, bundle_id: str, *, force_refresh: bool = False) -> ResolvedPayload:
        normalized_bundle_id = str(bundle_id or "").strip()
        materialized_payload: dict[str, Any] = {}
        materialized_dataset_name = ""
        materialized_details: dict[str, Any] = {"bundle_id": normalized_bundle_id}
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
                and not self._bundle_uses_stat_dump_text(payload)
            ):
                materialized_payload = payload
                materialized_dataset_name = dataset_name
                materialized_details = {**details, "bundle_id": normalized_bundle_id}

        materialized_only = self._materialized_only_result({}, datasets=ATTENTION_BUNDLE_SNAPSHOT_DATASETS, details={"bundle_id": normalized_bundle_id})
        if materialized_only is not None:
            if materialized_payload:
                return self._resolved(
                    materialized_payload,
                    mode="materialized",
                    datasets=(materialized_dataset_name or ATTENTION_BUNDLE_SNAPSHOT_DATASETS[0],),
                    details=materialized_details,
                )
            return materialized_only

        if normalized_bundle_id.lower().startswith("symbol::"):
            symbol = normalized_bundle_id.split("::", 1)[1].upper().strip()
            if _precomputed_symbol_bundles_only() and not force_refresh:
                if materialized_payload:
                    return self._resolved(
                        materialized_payload,
                        mode="materialized",
                        datasets=(materialized_dataset_name or ATTENTION_BUNDLE_SNAPSHOT_DATASETS[0],),
                        details={**materialized_details, "precomputed_only": True},
                    )
                return self._resolved(
                    {},
                    mode="materialized",
                    datasets=ATTENTION_BUNDLE_SNAPSHOT_DATASETS,
                    details={"bundle_id": normalized_bundle_id, "precomputed_only": True},
                )

            materialized_signal_score = _bundle_web_signal_score(materialized_payload)
            should_try_direct = force_refresh or materialized_signal_score <= 0
            if should_try_direct:
                direct_payload = self._resolve_symbol_agentic_bundle(symbol, force_refresh=force_refresh)
                if direct_payload:
                    direct_signal_score = _bundle_web_signal_score(direct_payload)
                    if force_refresh or not materialized_payload or direct_signal_score > materialized_signal_score:
                        return self._resolved(
                            direct_payload,
                            mode="on_demand",
                            datasets=(
                                "attention_web_search_news",
                                "attention_search_requests",
                                "attention_search_results",
                                "attention_source_documents",
                                "attention_evidence_chunks",
                                "attention_claims",
                            ),
                            details={"bundle_id": normalized_bundle_id, "run_id": _coerce_text(direct_payload.get("run_id"))},
                        )
            if materialized_payload:
                return self._resolved(
                    materialized_payload,
                    mode="materialized",
                    datasets=(materialized_dataset_name or ATTENTION_BUNDLE_SNAPSHOT_DATASETS[0],),
                    details=materialized_details,
                )

        if materialized_payload:
            return self._resolved(
                materialized_payload,
                mode="materialized",
                datasets=(materialized_dataset_name or ATTENTION_BUNDLE_SNAPSHOT_DATASETS[0],),
                details=materialized_details,
            )
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

    def resolve_attention_evidence_search(
        self,
        *,
        query: str = "",
        tickers: list[str] | None = None,
        commodities: list[str] | None = None,
        event_tags: list[str] | None = None,
        dates: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        source_kinds: list[str] | None = None,
        providers: list[str] | None = None,
        research_scopes: list[str] | None = None,
        run_id: str | None = None,
        limit: int = 20,
        force_refresh: bool = False,
    ) -> ResolvedPayload:
        materialized = self._try_pipeline_frame("attention_evidence_chunks", force_refresh=force_refresh)
        if materialized is None:
            return self._resolved(
                pd.DataFrame(),
                mode="materialized" if self.materialized_only else "on_demand",
                datasets=("attention_evidence_chunks",),
                details={"warning": "attention_evidence_chunks unavailable"},
            )

        frame, details = materialized
        if frame.empty:
            return self._resolved(
                frame.reset_index(drop=True),
                mode="materialized",
                datasets=("attention_evidence_chunks",),
                details={**details, "filters": {"query": _coerce_text(query)}},
            )

        out = frame.copy()
        for column in (
            "bundle_subject",
            "title",
            "display_excerpt",
            "chunk_text",
            "source_kind",
            "source_provider",
            "search_provider",
            "research_scope",
            "mentioned_tickers_key",
            "mentioned_commodities_key",
            "event_tags_key",
        ):
            if column not in out.columns:
                out[column] = ""

        exact_dates = _dedupe_text_items([_coerce_text(value) for value in list(dates or []) if _coerce_text(value)])
        parsed_start = pd.to_datetime(start_date, utc=True, errors="coerce") if _coerce_text(start_date) else pd.NaT
        parsed_end = pd.to_datetime(end_date, utc=True, errors="coerce") if _coerce_text(end_date) else pd.NaT
        parsed_start = None if pd.isna(parsed_start) else parsed_start
        parsed_end = None if pd.isna(parsed_end) else parsed_end

        normalized_run_id = _coerce_text(run_id)
        if normalized_run_id and "run_id" in out.columns:
            out = out[out["run_id"].astype(str) == normalized_run_id].copy()

        normalized_scopes = [_coerce_text(value).lower() for value in list(research_scopes or []) if _coerce_text(value)]
        if normalized_scopes:
            out = out[out["research_scope"].astype(str).str.lower().isin(set(normalized_scopes))].copy()

        normalized_source_kinds = [_coerce_text(value).lower() for value in list(source_kinds or []) if _coerce_text(value)]
        if normalized_source_kinds:
            out = out[out["source_kind"].astype(str).str.lower().isin(set(normalized_source_kinds))].copy()

        normalized_providers = [_coerce_text(value).lower() for value in list(providers or []) if _coerce_text(value)]
        if normalized_providers:
            provider_series = out["source_provider"].astype(str).str.lower()
            search_provider_series = out["search_provider"].astype(str).str.lower()
            out = out[provider_series.isin(set(normalized_providers)) | search_provider_series.isin(set(normalized_providers))].copy()

        normalized_tickers = [_coerce_text(value).upper() for value in list(tickers or []) if _coerce_text(value)]
        if normalized_tickers:
            out = out[
                out["bundle_subject"].astype(str).str.upper().isin(set(normalized_tickers))
                | out["mentioned_tickers_key"].map(lambda value: _key_contains_any(value, normalized_tickers))
            ].copy()

        normalized_commodities = [_coerce_text(value).lower() for value in list(commodities or []) if _coerce_text(value)]
        if normalized_commodities:
            out = out[out["mentioned_commodities_key"].map(lambda value: _key_contains_any(str(value).lower(), normalized_commodities))].copy()

        normalized_event_tags = [_coerce_text(value).lower() for value in list(event_tags or []) if _coerce_text(value)]
        if normalized_event_tags:
            out = out[out["event_tags_key"].map(lambda value: _key_contains_any(str(value).lower(), normalized_event_tags))].copy()

        if exact_dates or parsed_start is not None or parsed_end is not None:
            out = out[out.apply(lambda row: _dates_match_filters(row, exact_dates=exact_dates, start_date=parsed_start, end_date=parsed_end), axis=1)].copy()

        query_text = _coerce_text(query)
        query_tokens = _query_tokens(query_text)
        out["search_score"] = 0.0
        if query_tokens:
            out["_search_blob"] = (
                out["title"].astype(str)
                + " "
                + out["display_excerpt"].astype(str)
                + " "
                + out["chunk_text"].astype(str)
            ).str.lower()
            token_scores = sum(out["_search_blob"].str.contains(token, regex=False).astype(int) for token in query_tokens)
            phrase_bonus = out["_search_blob"].str.contains(query_text.lower(), regex=False).astype(int) * 4
            out["search_score"] = out["search_score"] + token_scores.astype(float) * 6.0 + phrase_bonus.astype(float)
            out = out[(token_scores + phrase_bonus) > 0].copy()

        if normalized_tickers:
            out["search_score"] = (
                out["search_score"]
                + out["bundle_subject"].astype(str).str.upper().isin(set(normalized_tickers)).astype(float) * 8.0
                + out["mentioned_tickers_key"].map(lambda value: 4.0 if _key_contains_any(value, normalized_tickers) else 0.0)
            )
        if normalized_commodities:
            out["search_score"] = out["search_score"] + out["mentioned_commodities_key"].map(lambda value: 3.0 if _key_contains_any(str(value).lower(), normalized_commodities) else 0.0)
        if normalized_event_tags:
            out["search_score"] = out["search_score"] + out["event_tags_key"].map(lambda value: 3.0 if _key_contains_any(str(value).lower(), normalized_event_tags) else 0.0)

        out["search_score"] = out["search_score"] + (4 - pd.to_numeric(out.get("authority_rank"), errors="coerce").fillna(3)).clip(lower=0, upper=4)
        out["published_at"] = pd.to_datetime(out.get("published_at"), utc=True, errors="coerce")
        out = out.sort_values(
            by=["search_score", "published_at", "authority_rank"],
            ascending=[False, False, True],
            na_position="last",
        ).head(max(int(limit), 1)).reset_index(drop=True)

        out["mentioned_tickers"] = out.get("mentioned_tickers_json", pd.Series(dtype=object)).map(parse_json_list)
        out["mentioned_commodities"] = out.get("mentioned_commodities_json", pd.Series(dtype=object)).map(parse_json_list)
        out["event_tags"] = out.get("event_tags_json", pd.Series(dtype=object)).map(parse_json_list)
        out["mentioned_dates"] = out.get("mentioned_dates_json", pd.Series(dtype=object)).map(parse_json_list)
        drop_columns = [column for column in ("mentioned_tickers_key", "mentioned_commodities_key", "event_tags_key", "mentioned_dates_key", "_search_blob") if column in out.columns]
        if drop_columns:
            out = out.drop(columns=drop_columns)

        return self._resolved(
            out,
            mode="materialized",
            datasets=("attention_evidence_chunks",),
            details={
                **details,
                "filters": {
                    "query": query_text,
                    "tickers": normalized_tickers,
                    "commodities": normalized_commodities,
                    "event_tags": normalized_event_tags,
                    "dates": exact_dates,
                    "start_date": _coerce_text(start_date),
                    "end_date": _coerce_text(end_date),
                    "source_kinds": normalized_source_kinds,
                    "providers": normalized_providers,
                    "research_scopes": normalized_scopes,
                    "run_id": normalized_run_id,
                    "limit": max(int(limit), 1),
                },
            },
        )

    def resolve_saa_document_search(
        self,
        *,
        query: str = "",
        tickers: list[str] | None = None,
        commodities: list[str] | None = None,
        event_tags: list[str] | None = None,
        dates: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        source_kinds: list[str] | None = None,
        providers: list[str] | None = None,
        run_id: str | None = None,
        limit: int = 20,
    ) -> ResolvedPayload:
        normalized_tickers = [_coerce_text(value).upper() for value in list(tickers or []) if _coerce_text(value)]
        normalized_commodities = [_coerce_text(value).lower() for value in list(commodities or []) if _coerce_text(value)]
        normalized_event_tags = [_coerce_text(value).lower() for value in list(event_tags or []) if _coerce_text(value)]
        exact_dates = _dedupe_text_items([_coerce_text(value) for value in list(dates or []) if _coerce_text(value)])
        normalized_source_kinds = [_coerce_text(value).lower() for value in list(source_kinds or []) if _coerce_text(value)]
        normalized_providers = [_coerce_text(value).lower() for value in list(providers or []) if _coerce_text(value)]
        normalized_run_id = _coerce_text(run_id)
        query_text = _coerce_text(query)
        out = search_retained_documents(
            query=query_text,
            tickers=normalized_tickers,
            commodities=normalized_commodities,
            event_tags=normalized_event_tags,
            dates=exact_dates,
            start_date=_coerce_text(start_date) or None,
            end_date=_coerce_text(end_date) or None,
            source_kinds=normalized_source_kinds,
            providers=normalized_providers,
            run_id=normalized_run_id or None,
            limit=max(int(limit), 1),
        )
        if isinstance(out, pd.DataFrame) and not out.empty:
            drop_columns = [column for column in ("search_text", "metadata_json") if column in out.columns]
            if drop_columns:
                out = out.drop(columns=drop_columns)
        return self._resolved(
            out.reset_index(drop=True),
            mode="service_backed",
            datasets=("saa_documents",),
            details={
                "filters": {
                    "query": query_text,
                    "tickers": normalized_tickers,
                    "commodities": normalized_commodities,
                    "event_tags": normalized_event_tags,
                    "dates": exact_dates,
                    "start_date": _coerce_text(start_date),
                    "end_date": _coerce_text(end_date),
                    "source_kinds": normalized_source_kinds,
                    "providers": normalized_providers,
                    "run_id": normalized_run_id,
                    "limit": max(int(limit), 1),
                },
                "row_count": int(len(out)),
                **({"warning": "no_retained_documents_matched"} if out.empty else {}),
            },
        )

    def resolve_saa_chunk_search(
        self,
        *,
        query: str = "",
        tickers: list[str] | None = None,
        commodities: list[str] | None = None,
        event_tags: list[str] | None = None,
        dates: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        source_kinds: list[str] | None = None,
        providers: list[str] | None = None,
        research_scopes: list[str] | None = None,
        run_id: str | None = None,
        canonical_document_id: str | None = None,
        limit: int = 20,
        use_semantic: bool = True,
    ) -> ResolvedPayload:
        normalized_tickers = [_coerce_text(value).upper() for value in list(tickers or []) if _coerce_text(value)]
        normalized_commodities = [_coerce_text(value).lower() for value in list(commodities or []) if _coerce_text(value)]
        normalized_event_tags = [_coerce_text(value).lower() for value in list(event_tags or []) if _coerce_text(value)]
        exact_dates = _dedupe_text_items([_coerce_text(value) for value in list(dates or []) if _coerce_text(value)])
        normalized_source_kinds = [_coerce_text(value).lower() for value in list(source_kinds or []) if _coerce_text(value)]
        normalized_providers = [_coerce_text(value).lower() for value in list(providers or []) if _coerce_text(value)]
        normalized_scopes = [_coerce_text(value).lower() for value in list(research_scopes or []) if _coerce_text(value)]
        normalized_run_id = _coerce_text(run_id)
        normalized_document_id = _coerce_text(canonical_document_id)
        query_text = _coerce_text(query)
        out = search_retained_evidence_chunks(
            query=query_text,
            tickers=normalized_tickers,
            commodities=normalized_commodities,
            event_tags=normalized_event_tags,
            dates=exact_dates,
            start_date=_coerce_text(start_date) or None,
            end_date=_coerce_text(end_date) or None,
            source_kinds=normalized_source_kinds,
            providers=normalized_providers,
            research_scopes=normalized_scopes,
            run_id=normalized_run_id or None,
            canonical_document_id=normalized_document_id or None,
            limit=max(int(limit), 1),
            use_semantic=bool(use_semantic),
        )
        if isinstance(out, pd.DataFrame) and not out.empty:
            drop_columns = [column for column in ("search_text", "metadata_json", "embedding_vector_json") if column in out.columns]
            if drop_columns:
                out = out.drop(columns=drop_columns)
        return self._resolved(
            out.reset_index(drop=True),
            mode="service_backed",
            datasets=("saa_evidence_chunks",),
            details={
                "filters": {
                    "query": query_text,
                    "tickers": normalized_tickers,
                    "commodities": normalized_commodities,
                    "event_tags": normalized_event_tags,
                    "dates": exact_dates,
                    "start_date": _coerce_text(start_date),
                    "end_date": _coerce_text(end_date),
                    "source_kinds": normalized_source_kinds,
                    "providers": normalized_providers,
                    "research_scopes": normalized_scopes,
                    "run_id": normalized_run_id,
                    "canonical_document_id": normalized_document_id,
                    "limit": max(int(limit), 1),
                    "use_semantic": bool(use_semantic),
                },
                "row_count": int(len(out)),
                **({"warning": "no_retained_chunks_matched"} if out.empty else {}),
            },
        )

    def resolve_saa_document(
        self,
        canonical_document_id: str,
        *,
        include_raw_text: bool = True,
    ) -> ResolvedPayload:
        normalized_id = _coerce_text(canonical_document_id)
        if not normalized_id:
            return self._resolved(
                {},
                mode="service_backed",
                datasets=("saa_documents",),
                details={"warning": "canonical_document_id_required"},
            )
        metadata = load_retained_document_metadata(normalized_id)
        if metadata is None:
            return self._resolved(
                {},
                mode="service_backed",
                datasets=("saa_documents",),
                details={"canonical_document_id": normalized_id, "warning": "retained_document_not_found"},
            )
        payload = dict(metadata)
        if include_raw_text:
            retained = load_retained_document(normalized_id) or {}
            if isinstance(retained, dict):
                payload.update(retained)
        else:
            payload.pop("search_text", None)
            payload.pop("metadata_json", None)
        return self._resolved(
            payload,
            mode="service_backed",
            datasets=("saa_documents",),
            details={
                "canonical_document_id": normalized_id,
                "include_raw_text": bool(include_raw_text),
                "has_blob": bool(_coerce_text(payload.get("raw_text_blob_path"))),
            },
        )

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
                macro_details = self._macro_feed_provenance_details(force_refresh=force_refresh)
                return self._resolved(
                    feed.reset_index(drop=True),
                    mode="materialized",
                    datasets=(candidate_dataset,),
                    details={
                        **candidate_details,
                        **macro_details,
                        "filters": {"horizons": list(normalize_horizons(horizons)), "sensitivity": sensitivity or "balanced"},
                    },
                )

        feed, details = self._pipeline_frame(dataset_key)
        if feed.empty:
            return self._resolved(
                feed,
                mode="materialized",
                datasets=(dataset_key,),
                details={**details, **self._macro_feed_provenance_details(force_refresh=force_refresh)},
            )

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
        return self._resolved(
            out.reset_index(drop=True),
            mode="materialized",
            datasets=(dataset_key,),
            details={**details, **self._macro_feed_provenance_details(force_refresh=force_refresh)},
        )

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
                optional_frames: dict[str, pd.DataFrame] = {}
                optional_details: dict[str, dict[str, Any]] = {}
                for dataset_name in ("fred_series_index", "fred_release_index"):
                    materialized_optional = self._try_pipeline_frame(dataset_name, force_refresh=force_refresh)
                    if materialized_optional is None:
                        continue
                    frame, dataset_details = materialized_optional
                    optional_frames[dataset_name] = frame
                    optional_details[dataset_name] = dataset_details

                payload = build_fred_dashboard_from_pipeline(
                    summary,
                    observations,
                    years,
                    series_index=optional_frames.get("fred_series_index"),
                    release_index=optional_frames.get("fred_release_index"),
                )
                datasets = ["fred_summary", "fred_observations"]
                if "fred_series_index" in optional_frames:
                    datasets.append("fred_series_index")
                if "fred_release_index" in optional_frames:
                    datasets.append("fred_release_index")
                details_payload: dict[str, Any] = {
                    "summary": details["fred_summary"],
                    "observations": details["fred_observations"],
                    "years": years,
                }
                if "fred_series_index" in optional_details:
                    details_payload["series_index"] = optional_details["fred_series_index"]
                if "fred_release_index" in optional_details:
                    details_payload["release_index"] = optional_details["fred_release_index"]
                return self._resolved(
                    payload,
                    mode="materialized",
                    datasets=tuple(datasets),
                    details=details_payload,
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

    def resolve_materialized_dataset(self, dataset_name: str, *, force_refresh: bool = False) -> ResolvedPayload:
        normalized_name = str(dataset_name or "").strip()
        if not normalized_name:
            return self._resolved(pd.DataFrame(), mode="materialized", datasets=(), details={"warning": "dataset_name is required"})
        materialized = self._try_pipeline_frame(normalized_name, force_refresh=force_refresh)
        if materialized is not None:
            frame, details = materialized
            resolved_details = dict(details)
            if normalized_name == "macro_relationship_checks_1d" and isinstance(frame, pd.DataFrame) and frame.empty:
                resolved_details.update(
                    {
                        "empty_reason": "no_precomputed_relationship_rows",
                        "artifact_role": "precomputed_relationship_artifact",
                        "fallback_attempted": False,
                        "next_tool_hint": "Use primitive yield observations and price_history, then analysis.run_python for relationship checks.",
                        "user_safe_explanation": "No precomputed macro relationship rows matched this snapshot; this does not mean underlying market or yield data is unavailable.",
                    }
                )
            return self._resolved(frame.reset_index(drop=True), mode="materialized", datasets=(normalized_name,), details=resolved_details)
        materialized_only = self._materialized_only_result(pd.DataFrame(), datasets=(normalized_name,))
        if materialized_only is not None:
            return materialized_only
        if normalized_name == "macro_relationship_checks_1d":
            return self._resolved(
                pd.DataFrame(),
                mode="on_demand",
                datasets=(normalized_name,),
                details={
                    "empty_reason": "precomputed_relationship_dataset_unavailable",
                    "artifact_role": "precomputed_relationship_artifact",
                    "next_tool_hint": "Use primitive yield observations and price_history, then analysis.run_python for relationship checks.",
                    "user_safe_explanation": "The precomputed macro relationship artifact is unavailable; use raw yield and price datasets for fresh analysis.",
                },
            )
        return self._resolved(pd.DataFrame(), mode="on_demand", datasets=(normalized_name,), details={"warning": "materialized dataset not available"})

    def latest_job_status(self) -> ResolvedPayload:
        return self._resolved(latest_job_status_table(), mode="materialized", datasets=("job_runs",))

    def trigger_source_refresh(self, source: str) -> ResolvedPayload:
        ok, message = start_source_refresh_job(source)
        return self._resolved({"ok": ok, "message": message}, mode="command", datasets=("job_runs",), details={"source": source})


# ---------------------------------------------------------------------------
# Historical homepage replay
# ---------------------------------------------------------------------------

_HOMEPAGE_REPLAY_DATASETS = (
    "attention_home_1d",
    "attention_ticker_snapshots_1d",
    "attention_research_bundles",
    "attention_ticker_background_snapshots",
)


def resolve_homepage_asof(target_date: str) -> dict[str, Any]:
    """Load a complete homepage payload for a past date.

    Returns a dict with:
    - ``home_payload``: deserialized attention home payload (summary, graph,
      events, movers — everything the homepage renders)
    - ``ticker_snapshots``: per-symbol sparkline/profile map keyed by symbol
    - ``research_bundles``: raw research bundle frame for on-demand drilldowns
    - ``ticker_backgrounds``: deeper ticker profile frame
    - ``metadata``: dict of dataset name -> PipelineDataset for provenance
    - ``target_date``: the requested date

    All data comes from stored pipeline outputs — no LLM calls, no live API
    calls.  If a dataset has no version on or before *target_date*, its entry
    is empty.
    """
    metadata: dict[str, Any] = {}
    result: dict[str, Any] = {"target_date": target_date, "metadata": metadata}

    # 1. Main homepage payload (summary, graph, events, movers)
    home_frame, home_meta = load_dataset_frame_asof("attention_home_1d", target_date)
    metadata["attention_home_1d"] = home_meta
    result["home_payload"] = deserialize_attention_home_payload(home_frame) if not home_frame.empty else {}

    # 2. Ticker snapshots (sparkline charts, company names, market caps)
    snap_frame, snap_meta = load_dataset_frame_asof("attention_ticker_snapshots_1d", target_date)
    metadata["attention_ticker_snapshots_1d"] = snap_meta
    ticker_map: dict[str, dict[str, Any]] = {}
    if not snap_frame.empty and "symbol" in snap_frame.columns:
        for symbol in snap_frame["symbol"].dropna().unique():
            symbol_str = str(symbol).upper().strip()
            if symbol_str:
                ticker_map[symbol_str] = deserialize_attention_ticker_snapshot_frame(snap_frame, symbol_str)
    result["ticker_snapshots"] = ticker_map

    # 3. Research bundles (detailed per-event research, loaded on click)
    bundle_frame, bundle_meta = load_dataset_frame_asof("attention_research_bundles", target_date)
    metadata["attention_research_bundles"] = bundle_meta
    result["research_bundles"] = bundle_frame

    # 4. Ticker background snapshots (deeper profiles with news context)
    bg_frame, bg_meta = load_dataset_frame_asof("attention_ticker_background_snapshots", target_date)
    metadata["attention_ticker_background_snapshots"] = bg_meta
    result["ticker_backgrounds"] = bg_frame

    return result
