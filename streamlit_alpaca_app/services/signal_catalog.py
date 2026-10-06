from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import pandas as pd

from compute.fred import FRED_SERIES_SPECS
from compute.fundamentals import (
    BALANCE_METRICS,
    CASHFLOW_METRICS,
    INCOME_METRICS,
    SHARE_COUNT_METRICS,
)
from compute.treasury_yields import TREASURY_SPREAD_SPECS, TREASURY_YIELD_FIELD_SPECS


CATALOG_COLUMNS = [
    "signal_id",
    "family",
    "source_dataset",
    "source_field",
    "source_entity",
    "entity_scope",
    "grain",
    "coverage_scope",
    "signal_kind",
    "materialization",
    "ranking_role",
    "ranking_stage",
    "directionality",
    "time_safety",
    "horizons",
    "description",
    "availability",
    "row_count",
    "non_null_count",
    "coverage_ratio",
    "dataset_version_id",
    "dataset_asof_time_utc",
]


@dataclass(frozen=True)
class SignalSpec:
    signal_id: str
    family: str
    source_dataset: str
    source_field: str
    entity_scope: str
    grain: str
    coverage_scope: str
    signal_kind: str = "numeric"
    materialization: str = "materialized"
    ranking_role: str = "direct"
    ranking_stage: str = "candidate_rerank"
    directionality: str = "two_sided"
    time_safety: str = "snapshot_only"
    horizons: str = "multi_horizon"
    description: str = ""
    source_entity: str = ""


@dataclass(frozen=True)
class SignalGroup:
    family: str
    source_dataset: str
    fields: tuple[str, ...]
    entity_scope: str
    grain: str
    coverage_scope: str
    description: str
    signal_kind: str = "numeric"
    materialization: str = "materialized"
    ranking_role: str = "direct"
    ranking_stage: str = "candidate_rerank"
    directionality: str = "two_sided"
    time_safety: str = "snapshot_only"
    horizons: str = "multi_horizon"


def _group(
    family: str,
    dataset: str,
    fields: Iterable[str],
    *,
    scope: str = "equity",
    grain: str = "symbol_snapshot",
    coverage: str = "equity_universe",
    description: str,
    **overrides: str,
) -> SignalGroup:
    return SignalGroup(
        family=family,
        source_dataset=dataset,
        fields=tuple(fields),
        entity_scope=scope,
        grain=grain,
        coverage_scope=coverage,
        description=description,
        **overrides,
    )


SIGNAL_GROUPS: tuple[SignalGroup, ...] = (
    _group(
        "price_liquidity",
        "daily_movers",
        ("change_pct", "volume"),
        description="Current daily move and traded share volume.",
        horizons="1d",
    ),
    _group(
        "cross_asset_context",
        "macro_anchor_daily_movers",
        ("change_pct", "volume"),
        description="Current move and activity in configured index, rates, currency, volatility, and commodity anchors.",
        scope="macro_anchor",
        coverage="configured_macro_anchors",
        ranking_role="conditioning",
        horizons="1d",
    ),
    _group(
        "price_liquidity",
        "universe_snapshot",
        ("change_pct", "volume", "dollar_volume"),
        description="Current move and liquidity measurements for the ranked equity universe.",
        horizons="1d",
    ),
    _group(
        "price_liquidity",
        "universe_snapshot",
        ("liquidity_rank",),
        description="Cross-sectional liquidity rank; lower ranks are more liquid.",
        directionality="lower",
        horizons="current",
    ),
    _group(
        "price_history",
        "price_history",
        ("open", "high", "low", "close", "volume"),
        description="Point-in-time daily bars used to derive technical and return signals.",
        grain="symbol_day",
        signal_kind="raw_series",
        ranking_role="source_input",
        ranking_stage="feature_derivation",
        directionality="not_applicable",
        time_safety="point_in_time",
    ),
    _group(
        "momentum",
        "momentum_profiles",
        (
            "return_1d_pct",
            "return_7d_pct",
            "daily_change_pct",
            "return_1w_pct",
            "return_1m_pct",
            "return_3m_pct",
            "return_1y_pct",
            "return_5y_pct",
            "momentum_1w",
            "momentum_1m",
            "momentum_3m",
            "roc_1d_to_1w",
            "roc_1w_to_1m",
            "roc_1m_to_3m",
        ),
        description="Multi-horizon returns, trend slopes, and changes in trend speed.",
    ),
    _group(
        "trend_quality",
        "momentum_profiles",
        ("trend_r2_3m",),
        description="Consistency of the three-month log-price trend.",
        directionality="higher",
    ),
    _group(
        "trend_quality",
        "momentum_profiles",
        ("trend_fit_gap",),
        description="Distance from a clean three-month trend; lower is more consistent.",
        directionality="lower",
    ),
    _group(
        "momentum",
        "momentum_profiles",
        ("momentum_score", "momentum_roc_score"),
        description="Existing momentum composites retained for compatibility and provenance.",
        ranking_role="existing_composite",
    ),
    _group(
        "opportunity_retrieval",
        "market_opportunity_feed",
        (
            "opportunity_score",
            "retrieval_signal_count",
            "horizon_retrieval_rank",
            "momentum_change_retrieval_rank",
            "daily_move_retrieval_rank",
            "trend_quality_retrieval_rank",
        ),
        description="Current retrieval output and component ranks; these summarize underlying signals rather than adding new evidence.",
        ranking_role="existing_composite",
    ),
    _group(
        "opportunity_retrieval",
        "market_opportunity_feed",
        ("horizon_metric_available",),
        description="Whether the selected horizon has a usable return measurement.",
        signal_kind="boolean",
        ranking_role="quality_gate",
        directionality="categorical",
    ),
    _group(
        "technical",
        "technical_signals_latest",
        (
            "ret_5_pct",
            "ret_21_pct",
            "ret_63_pct",
            "rsi_14",
            "vol_20_ann_pct",
            "pullback_from_ath_pct",
            "channel_position",
            "dist_to_support_pct",
            "dist_to_resistance_pct",
        ),
        description="Latest return, volatility, oscillator, drawdown, and price-channel state.",
    ),
    _group(
        "technical",
        "technical_signal_history",
        ("channel_width_pct",),
        description="Historical channel width; requires an as-of latest-row selection before ranking.",
        grain="symbol_day",
        ranking_role="source_input",
        ranking_stage="feature_derivation",
        time_safety="point_in_time",
    ),
    _group(
        "technical",
        "technical_signals_latest",
        ("regime",),
        description="Categorical technical regime derived from trend and channel state.",
        signal_kind="categorical",
        ranking_role="conditioning",
        directionality="categorical",
    ),
    _group(
        "technical_forecast",
        "price_history",
        (
            "up_probability",
            "breakout_probability",
            "support_break_probability",
            "expected_return_pct",
            "median_return_pct",
            "expected_path_return_pct",
            "analog_count",
        ),
        description="Five-day analog forecast derived on demand from technical history.",
        materialization="derived_on_demand",
        ranking_role="research_only",
        ranking_stage="research",
        time_safety="point_in_time_if_asof_sliced",
        horizons="1w",
    ),
    _group(
        "market_relationship",
        "correlation_phase_shift_summary",
        (
            "correlation_now",
            "correlation_roc",
            "compound_1m_pct",
            "compound_3m_pct",
            "compounding_momentum_pct",
            "momentum_roc_pct",
            "decoupling_score",
            "beta_breakout_score",
            "correlation_break_score",
        ),
        description="Relationship, decoupling, and relative momentum versus the market benchmark.",
    ),
    _group(
        "market_relationship",
        "correlation_phase_shift_summary",
        ("phase_regime",),
        description="Categorical phase relationship versus the benchmark.",
        signal_kind="categorical",
        ranking_role="conditioning",
        directionality="categorical",
    ),
    _group(
        "relative_expectation",
        "price_expectations",
        (
            "observed_return_pct",
            "trend_expected_return_pct",
            "peer_expected_return_pct",
            "benchmark_expected_return_pct",
            "residual_return_pct",
            "residual_zscore",
            "trend_zscore",
            "peer_zscore",
            "benchmark_zscore",
            "vol_20_ann_pct",
            "momentum_score",
            "momentum_roc_score",
            "correlation_now",
            "correlation_roc",
        ),
        description="Observed move and peer-, trend-, and benchmark-relative surprise measurements.",
        grain="symbol_horizon_snapshot",
    ),
    _group(
        "relative_expectation",
        "price_expectations",
        ("blended_expected_return_pct",),
        description="Existing weighted expectation composite.",
        grain="symbol_horizon_snapshot",
        ranking_role="existing_composite",
    ),
    _group(
        "attention",
        "anomaly_events",
        ("residual_value", "residual_zscore", "persistence_score", "novelty_score"),
        description="Size, persistence, and novelty of a move relative to its expected path.",
        grain="symbol_horizon_event",
    ),
    _group(
        "attention",
        "anomaly_events",
        (
            "severity_score",
            "impact_score",
            "relevance_score",
            "confidence_score",
            "attention_score",
            "attention_score_v2_shadow",
            "attention_score_v2",
        ),
        description="Existing attention composites; index them but do not combine them blindly with their components.",
        grain="symbol_horizon_event",
        ranking_role="existing_composite",
    ),
    _group(
        "macro_transmission",
        "anomaly_events",
        ("macro_alignment_score", "macro_conflict_score", "macro_signal_count", "macro_data_fresh"),
        description="Ticker-level macro alignment, conflict, breadth, and freshness.",
        grain="symbol_horizon_event",
        ranking_role="conditioning",
    ),
    _group(
        "portfolio",
        "anomaly_events",
        ("portfolio_exposure_weight",),
        description="Current portfolio exposure used for personalized urgency, not expected return.",
        scope="portfolio",
        grain="symbol_horizon_event",
        coverage="held_positions",
        ranking_role="conditioning",
        ranking_stage="portfolio_overlay",
    ),
    _group(
        "event_detection",
        "attention_candidates_1d",
        (
            "change_pct",
            "abs_change_pct",
            "expected_move_pct",
            "surprise_pct",
            "surprise_z",
            "anomaly_score",
            "return_z",
            "volume_z",
            "range_z",
            "anomaly_history_count",
            "volume",
            "dollar_volume",
        ),
        description="Current move, expected move, anomaly history, standardized activity, and liquidity.",
        coverage="current_attention_universe",
        horizons="1d",
    ),
    _group(
        "event_detection",
        "attention_candidates_1d",
        ("liquidity_rank",),
        description="Liquidity rank inside the current attention universe; lower is more liquid.",
        coverage="current_attention_universe",
        directionality="lower",
        horizons="1d",
    ),
    _group(
        "event_detection",
        "attention_candidates_1d",
        ("attention_score", "severity_score", "candidate_score"),
        description="Existing one-day event-selection composites.",
        coverage="current_attention_universe",
        ranking_role="existing_composite",
        horizons="1d",
    ),
    _group(
        "evidence_quality",
        "attention_candidates_1d",
        ("source_count", "evidence_count", "same_day_evidence_count"),
        description="Source breadth, evidence volume, and same-day support for a detected event.",
        coverage="event_triggered_symbols",
        ranking_role="quality_gate",
        ranking_stage="research",
        directionality="higher",
        horizons="1d",
    ),
    _group(
        "evidence_quality",
        "attention_candidates_1d",
        ("best_authority_rank",),
        description="Best supporting source authority rank; lower is stronger.",
        coverage="event_triggered_symbols",
        ranking_role="quality_gate",
        ranking_stage="research",
        directionality="lower",
        horizons="1d",
    ),
    _group(
        "event_detection",
        "attention_event_clusters_1d",
        ("event_score",),
        description="Cross-symbol event-cluster score.",
        scope="event_cluster",
        grain="event_snapshot",
        coverage="detected_event_clusters",
        ranking_role="existing_composite",
        horizons="1d",
    ),
    _group(
        "market_breadth",
        "attention_rollups",
        (
            "active_event_count",
            "high_priority_event_count",
            "top_attention_score",
            "breadth_positive",
            "breadth_negative",
            "mean_residual_zscore",
            "net_attention_score",
        ),
        description="Market or peer-group anomaly breadth and aggregate attention state.",
        scope="market_or_peer_group",
        grain="rollup_snapshot",
        coverage="active_attention_events",
        ranking_role="conditioning",
    ),
    _group(
        "evidence_quality",
        "attention_claims",
        ("relevance_score", "causal_score", "confidence_score", "is_same_day", "supports_hypothesis", "freshness_class"),
        description="Claim-level relevance, causal strength, confidence, freshness, and hypothesis support.",
        grain="claim",
        coverage="researched_attention_candidates",
        signal_kind="mixed",
        ranking_role="quality_gate",
        ranking_stage="research",
    ),
    _group(
        "macro_relationship",
        "macro_relationship_checks_1d",
        (
            "expected_sign",
            "observed_sign",
            "observed_strength",
            "consistency_status",
            "strength_weight",
            "confidence_prior",
        ),
        description="Observed-versus-expected response to a macro release along a declared causal edge.",
        scope="macro_edge",
        grain="release_relationship",
        coverage="macro_release_targets",
        ranking_role="conditioning",
        signal_kind="mixed",
        horizons="1d",
    ),
    _group(
        "macro_release",
        "macro_release_events_1d",
        ("surprise_score", "surprise_z", "cross_asset_reaction_pct", "release_direction"),
        description="Release surprise and immediate cross-asset response.",
        scope="macro_release",
        grain="release_event",
        coverage="detected_macro_releases",
        ranking_role="conditioning",
        horizons="1d",
        time_safety="release_time_required_for_backtest",
    ),
    _group(
        "macro_transmission",
        "attention_macro_context_1d",
        ("macro_alignment_score", "macro_conflict_score", "macro_signal_count", "macro_staleness_hours"),
        description="Per-symbol macro alignment, conflict, supporting-signal count, and freshness.",
        grain="symbol_horizon_snapshot",
        coverage="macro_linked_symbols",
        ranking_role="conditioning",
    ),
    _group(
        "macro_hypothesis",
        "attention_hypotheses_1d",
        ("support_score", "contradiction_score", "evidence_count", "support_status"),
        description="Evidence support and contradiction for a macro transmission hypothesis.",
        scope="macro_hypothesis",
        grain="hypothesis_snapshot",
        coverage="detected_macro_releases",
        signal_kind="mixed",
        ranking_role="quality_gate",
        ranking_stage="research",
        horizons="1d",
    ),
    _group(
        "macro_model_prior",
        "macro_causal_graph_edges_v1",
        ("expected_sign", "min_abs_change_pct", "strength_weight", "confidence_prior", "lag_window", "regime_filter"),
        description="Declared macro transmission prior; it conditions interpretation but is not an observed market measurement.",
        scope="macro_edge",
        grain="causal_edge",
        coverage="configured_macro_edges",
        signal_kind="mixed",
        ranking_role="conditioning",
        directionality="not_applicable",
        horizons="configured",
    ),
    _group(
        "commodity_transmission",
        "commodity_regime_summary",
        (
            "correlation_now",
            "correlation_roc",
            "beta_now",
            "beta_roc",
            "asset_compounding_momentum_pct",
            "commodity_compounding_momentum_pct",
            "transmission_gap_pct",
            "relative_strength_pct",
            "pullback_from_high_pct",
            "beneficiary_score",
            "squeeze_score",
            "decoupler_score",
        ),
        description="Equity or proxy sensitivity, relative strength, and transmission versus a commodity basket.",
        coverage="commodity_proxy_universe",
        ranking_role="conditioning",
    ),
    _group(
        "commodity_transmission",
        "commodity_regime_summary",
        ("commodity_regime",),
        description="Categorical commodity-transmission regime.",
        coverage="commodity_proxy_universe",
        signal_kind="categorical",
        ranking_role="conditioning",
        directionality="categorical",
    ),
    _group(
        "commodity_expectation",
        "commodity_price_expectations",
        (
            "observed_return_pct",
            "trend_expected_return_pct",
            "peer_expected_return_pct",
            "benchmark_expected_return_pct",
            "blended_expected_return_pct",
            "residual_return_pct",
            "residual_zscore",
            "trend_zscore",
            "peer_zscore",
            "benchmark_zscore",
            "vol_20_ann_pct",
            "momentum_score",
            "momentum_roc_score",
            "correlation_now",
            "correlation_roc",
        ),
        description="Observed and expected returns for commodity-linked proxies and their peer-relative residuals.",
        scope="commodity_proxy",
        grain="symbol_horizon_snapshot",
        coverage="commodity_proxy_universe",
        ranking_role="conditioning",
    ),
    _group(
        "commodity_attention",
        "commodity_attention_candidates",
        (
            "residual_value",
            "residual_zscore",
            "severity_score",
            "impact_score",
            "confidence_score",
            "attention_score",
            "persistence_score",
            "novelty_score",
        ),
        description="Commodity-proxy anomaly magnitude, persistence, confidence, and attention state.",
        scope="commodity_proxy",
        grain="symbol_horizon_event",
        coverage="commodity_proxy_universe",
        ranking_role="conditioning",
    ),
    _group(
        "commodity_attention",
        "commodity_attention_rollups",
        (
            "active_event_count",
            "high_priority_event_count",
            "top_attention_score",
            "breadth_positive",
            "breadth_negative",
            "mean_residual_zscore",
            "net_attention_score",
        ),
        description="Aggregate commodity anomaly breadth and attention state.",
        scope="commodity_market",
        grain="rollup_snapshot",
        coverage="active_commodity_events",
        ranking_role="conditioning",
    ),
    _group(
        "options",
        "option_contract_snapshots",
        (
            "strike",
            "dte",
            "lastPrice",
            "bid",
            "ask",
            "premium",
            "impliedVolatility",
            "delta",
            "gamma",
            "theta",
            "vega",
            "rho",
            "volume",
            "openInterest",
        ),
        description="Contract-level price, volatility, Greeks, activity, and open interest.",
        grain="option_contract_snapshot",
        coverage="top_liquid_symbols_only",
        ranking_role="source_input",
        ranking_stage="feature_derivation",
        time_safety="point_in_time",
    ),
    _group(
        "options",
        "option_contract_snapshots",
        (
            "projected_return_pct",
            "delta_leverage",
            "gamma_convexity",
            "theta_drag_pct",
            "vega_risk_pct",
            "liquidity_score",
            "target_gap_pct",
            "horizon_fit_days",
            "selection_score",
        ),
        description="Option-selection fields derived on demand after a stock target and horizon already exist.",
        grain="option_contract_scenario",
        coverage="researched_symbols_with_options",
        materialization="derived_on_demand",
        ranking_role="research_only",
        ranking_stage="decision",
        time_safety="point_in_time_if_asof_sliced",
    ),
    _group(
        "taxonomy",
        "entity_taxonomy_labels",
        (
            "is_etf",
            "asset_class",
            "security_type",
            "sector",
            "industry",
            "country",
            "commodity_role",
            "rates_role",
            "defensive_role",
            "macro_role_tags",
            "business_role_tags",
        ),
        description="Entity classification for peer normalization and macro or business exposure mapping.",
        grain="symbol_label_snapshot",
        coverage="listed_equities",
        signal_kind="categorical",
        ranking_role="conditioning",
        directionality="categorical",
        horizons="structural",
    ),
    _group(
        "portfolio",
        "positions_snapshot",
        (
            "qty",
            "market_value",
            "avg_entry_price",
            "current_price",
            "unrealized_pl",
            "unrealized_plpc",
            "cost_basis",
            "change_today",
        ),
        description="Current funded-account position and profit/loss state.",
        scope="portfolio",
        coverage="held_positions",
        ranking_role="conditioning",
        ranking_stage="portfolio_overlay",
        horizons="current",
    ),
    _group(
        "research_quality",
        "trading_agent_research_packages",
        ("status", "confidence"),
        description="Research completion and confidence gates available only after candidate retrieval.",
        coverage="selected_research_pool",
        signal_kind="categorical",
        ranking_role="quality_gate",
        ranking_stage="decision",
        directionality="categorical",
    ),
    _group(
        "evidence_quality",
        "attention_bundle_snapshots",
        ("cause_status", "evidence_quality", "freshness_quality"),
        description="Event-bundle cause resolution, evidence quality, and freshness state.",
        coverage="researched_attention_candidates",
        signal_kind="categorical",
        ranking_role="quality_gate",
        ranking_stage="research",
        directionality="categorical",
        horizons="1d",
    ),
    _group(
        "filing_context",
        "attention_context_bundle",
        ("filing_count_lookback",),
        description="Recent filing count retained for the company context window.",
        coverage="filing_matched_symbols",
        ranking_role="source_input",
        ranking_stage="research",
        directionality="not_applicable",
    ),
    _group(
        "filing_context",
        "attention_context_llm",
        ("llm_confidence",),
        description="Confidence of the filing-grounded company-context synthesis.",
        coverage="filing_matched_symbols",
        signal_kind="categorical",
        ranking_role="quality_gate",
        ranking_stage="research",
        directionality="categorical",
    ),
    _group(
        "fundamentals",
        "attention_ticker_background_snapshots",
        ("market_cap", "share_count"),
        description="Current market capitalization and lagged reported share count for researched symbols.",
        coverage="attention_snapshot_symbols",
        ranking_role="source_input",
        ranking_stage="feature_derivation",
        time_safety="share_report_date_and_price_asof_required",
    ),
    _group(
        "research_quality",
        "attention_ticker_zopedia_enrichments",
        ("status", "confidence"),
        description="Zopedia enrichment completion and confidence for attention symbols.",
        coverage="zopedia_enriched_symbols",
        signal_kind="categorical",
        ranking_role="quality_gate",
        ranking_stage="research",
        directionality="categorical",
    ),
    _group(
        "research_quality",
        "zopedia_ticker_business_model_stacks",
        ("status", "confidence"),
        description="Business-model research completion and confidence for researched companies.",
        coverage="business_model_researched_symbols",
        signal_kind="categorical",
        ranking_role="quality_gate",
        ranking_stage="research",
        directionality="categorical",
    ),
    _group(
        "outcome",
        "trading_agent_outcomes",
        (
            "raw_return_pct",
            "signed_return_pct",
            "benchmark_return_pct",
            "excess_signed_return_pct",
            "is_win",
            "is_matured",
        ),
        description="Post-decision outcomes reserved for evaluation and model fitting.",
        coverage="matured_agent_calls",
        ranking_role="outcome_label",
        ranking_stage="evaluation",
        directionality="not_applicable",
        time_safety="post_decision_prohibited",
    ),
)


def _expand_group(group: SignalGroup) -> list[SignalSpec]:
    return [
        SignalSpec(
            signal_id=f"{group.source_dataset}.{field}",
            family=group.family,
            source_dataset=group.source_dataset,
            source_field=field,
            entity_scope=group.entity_scope,
            grain=group.grain,
            coverage_scope=group.coverage_scope,
            signal_kind=group.signal_kind,
            materialization=group.materialization,
            ranking_role=group.ranking_role,
            ranking_stage=group.ranking_stage,
            directionality=group.directionality,
            time_safety=group.time_safety,
            horizons=group.horizons,
            description=group.description,
        )
        for field in group.fields
    ]


def _fred_specs() -> list[SignalSpec]:
    rows: list[SignalSpec] = []
    summary_fields = ("latest_value", "prev_delta", "yoy_delta", "yoy_pct")
    extracted_fields = (
        "trend_dir",
        "trend_slope",
        "trend_r2",
        "trend_accel",
        "trend_accel_value",
        "roc_short_pct",
        "roc_mid_pct",
        "roc_long_pct",
        "roc_of_roc",
        "momentum_score",
        "zscore",
        "regime",
        "regime_duration",
    )
    for series in FRED_SERIES_SPECS:
        entity = series.series_id
        description = f"{series.label}: {series.blurb}"
        for field in summary_fields:
            rows.append(
                SignalSpec(
                    signal_id=f"fred.{entity}.{field}",
                    family="macro",
                    source_dataset="fred_summary",
                    source_field=field,
                    source_entity=entity,
                    entity_scope="macro_series",
                    grain="series_snapshot",
                    coverage_scope="global_market_context",
                    ranking_role="conditioning",
                    ranking_stage="candidate_rerank",
                    time_safety="release_time_required_for_backtest",
                    description=description,
                )
            )
        for field in extracted_fields:
            categorical = field in {"trend_dir", "trend_accel", "regime"}
            rows.append(
                SignalSpec(
                    signal_id=f"fred.{entity}.{field}",
                    family="macro",
                    source_dataset="fred_observations",
                    source_field=field,
                    source_entity=entity,
                    entity_scope="macro_series",
                    grain="series_snapshot",
                    coverage_scope="global_market_context",
                    signal_kind="categorical" if categorical else "numeric",
                    materialization="derived_on_demand",
                    ranking_role="conditioning",
                    ranking_stage="candidate_rerank",
                    directionality="categorical" if categorical else "two_sided",
                    time_safety="release_time_required_for_backtest",
                    description=description,
                )
            )
    return rows


def _treasury_specs() -> list[SignalSpec]:
    entities = [(spec.series_id, spec.label) for spec in TREASURY_YIELD_FIELD_SPECS]
    entities.extend((series_id, label) for series_id, label, _, _ in TREASURY_SPREAD_SPECS)
    rows: list[SignalSpec] = []
    for entity, label in entities:
        for field in ("latest_value", "prev_delta_bps", "five_day_delta_bps"):
            rows.append(
                SignalSpec(
                    signal_id=f"treasury.{entity}.{field}",
                    family="rates",
                    source_dataset="yield_curve_summary",
                    source_field=field,
                    source_entity=entity,
                    entity_scope="rates_series",
                    grain="series_snapshot",
                    coverage_scope="global_market_context",
                    ranking_role="conditioning",
                    ranking_stage="candidate_rerank",
                    time_safety="point_in_time",
                    description=f"{label} level and recent change.",
                )
            )
    return rows


def _fundamental_specs() -> list[SignalSpec]:
    metric_groups = {
        "income": tuple(INCOME_METRICS),
        "balance": tuple(BALANCE_METRICS),
        "cashflow": (*tuple(CASHFLOW_METRICS), "Free Cash Flow"),
        "shares": tuple(SHARE_COUNT_METRICS),
    }
    rows: list[SignalSpec] = []
    for statement, metrics in metric_groups.items():
        for metric in metrics:
            key = metric.lower().replace(" ", "_")
            rows.append(
                SignalSpec(
                    signal_id=f"fundamentals.{statement}.{key}",
                    family="fundamentals",
                    source_dataset="quarterly_fundamentals",
                    source_field="value",
                    source_entity=metric,
                    entity_scope="equity",
                    grain="symbol_quarter_metric",
                    coverage_scope="fundamentals_covered_equities",
                    ranking_role="source_input",
                    ranking_stage="feature_derivation",
                    time_safety="report_date_and_availability_lag_required",
                    horizons="quarterly",
                    description=f"Reported quarterly {metric.lower()} level; cross-sectional use requires scaling and lagging.",
                )
            )
    return rows


def signal_specs() -> tuple[SignalSpec, ...]:
    rows: list[SignalSpec] = []
    for group in SIGNAL_GROUPS:
        rows.extend(_expand_group(group))
    rows.extend(_fred_specs())
    rows.extend(_treasury_specs())
    rows.extend(_fundamental_specs())
    return tuple(sorted(rows, key=lambda item: item.signal_id))


def _metadata_value(metadata: object, field: str, default: object = "") -> object:
    if metadata is None:
        return default
    if isinstance(metadata, Mapping):
        return metadata.get(field, default)
    return getattr(metadata, field, default)


def _matching_series(frame: pd.DataFrame, spec: SignalSpec) -> tuple[pd.Series | None, int]:
    if frame.empty:
        return None, 0
    source_field = spec.source_field
    if spec.source_dataset == "fred_summary" and spec.source_entity and "series_id" in frame.columns:
        scoped = frame[frame["series_id"].astype(str).eq(spec.source_entity)]
        return (scoped[source_field] if source_field in scoped.columns else None), len(scoped)
    if spec.source_dataset == "yield_curve_summary" and spec.source_entity and "series_id" in frame.columns:
        scoped = frame[frame["series_id"].astype(str).eq(spec.source_entity)]
        return (scoped[source_field] if source_field in scoped.columns else None), len(scoped)
    if spec.source_dataset == "quarterly_fundamentals" and spec.source_entity and "metric" in frame.columns:
        scoped = frame[frame["metric"].astype(str).eq(spec.source_entity)]
        return (scoped[source_field] if source_field in scoped.columns else None), len(scoped)
    if source_field in frame.columns:
        return frame[source_field], len(frame)
    return None, len(frame)


def build_signal_catalog(
    *,
    frames: Mapping[str, pd.DataFrame] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> pd.DataFrame:
    """Build the canonical platform signal index and optional live coverage audit."""
    frame_map = dict(frames or {})
    metadata_map = dict(metadata or {})
    rows: list[dict[str, object]] = []
    for spec in signal_specs():
        frame = frame_map.get(spec.source_dataset)
        meta = metadata_map.get(spec.source_dataset)
        if isinstance(frame, pd.DataFrame):
            series, row_count = _matching_series(frame, spec)
        else:
            series = None
            row_count = int(_metadata_value(meta, "row_count", 0) or 0)
        non_null_count = int(series.notna().sum()) if series is not None else 0
        coverage_ratio = round(non_null_count / row_count, 6) if row_count > 0 and series is not None else pd.NA

        if spec.materialization == "derived_on_demand":
            availability = "derived_on_demand"
        elif isinstance(frame, pd.DataFrame):
            availability = "available" if series is not None and non_null_count > 0 else "missing_field_or_values"
        elif meta is not None and row_count > 0:
            availability = "materialized_not_loaded"
        else:
            availability = "defined_not_observed"

        rows.append(
            {
                **spec.__dict__,
                "availability": availability,
                "row_count": row_count,
                "non_null_count": non_null_count,
                "coverage_ratio": coverage_ratio,
                "dataset_version_id": str(_metadata_value(meta, "dataset_version_id", "") or ""),
                "dataset_asof_time_utc": str(_metadata_value(meta, "asof_time_utc", "") or ""),
            }
        )
    return pd.DataFrame(rows, columns=CATALOG_COLUMNS)


def catalog_summary(catalog: pd.DataFrame) -> pd.DataFrame:
    if catalog.empty:
        return pd.DataFrame(columns=["family", "signal_count", "source_datasets", "available", "defined_or_on_demand"])
    grouped = catalog.groupby("family", dropna=False)
    rows: list[dict[str, object]] = []
    for family, frame in grouped:
        rows.append(
            {
                "family": family,
                "signal_count": int(len(frame)),
                "source_datasets": int(frame["source_dataset"].nunique()),
                "available": int(frame["availability"].eq("available").sum()),
                "defined_or_on_demand": int(frame["availability"].isin(["defined_not_observed", "derived_on_demand"]).sum()),
            }
        )
    return pd.DataFrame(rows).sort_values(["signal_count", "family"], ascending=[False, True]).reset_index(drop=True)


__all__ = [
    "CATALOG_COLUMNS",
    "SIGNAL_GROUPS",
    "SignalGroup",
    "SignalSpec",
    "build_signal_catalog",
    "catalog_summary",
    "signal_specs",
]
