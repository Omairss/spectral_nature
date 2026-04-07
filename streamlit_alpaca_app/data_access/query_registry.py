from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from compute.portfolio import normalize_timeseries_view
from data_access.contracts import ChartModel, ChartTraceModel, ResolvedPayload, frame_to_records

DatasetHandler = Callable[[Any, dict[str, Any]], ResolvedPayload]
ChartHandler = Callable[[Any, dict[str, Any]], ResolvedPayload]


@dataclass(frozen=True)
class DatasetSpec:
    params: tuple[str, ...]
    resolution: str
    handler: DatasetHandler


@dataclass(frozen=True)
class ChartSpec:
    params: tuple[str, ...]
    resolution: str
    handler: ChartHandler


def _capabilities(specs: dict[str, DatasetSpec] | dict[str, ChartSpec]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "params": list(spec.params),
            "resolution": spec.resolution,
        }
        for name, spec in specs.items()
    }


def _force_refresh(params: dict[str, Any]) -> bool:
    return bool(params.get("force_refresh", False))


def _text(params: dict[str, Any], key: str, default: str = "") -> str:
    return str(params.get(key) or default).strip()


def _upper_text(params: dict[str, Any], key: str, default: str = "") -> str:
    return _text(params, key, default).upper()


def _int_value(params: dict[str, Any], key: str, default: int) -> int:
    return int(params.get(key) or default)


def _required_int(params: dict[str, Any], key: str) -> int:
    return int(params.get(key))


def _required_float(params: dict[str, Any], key: str) -> float:
    return float(params.get(key))


def _optional_float(params: dict[str, Any], key: str) -> float | None:
    return float(params[key]) if params.get(key) is not None else None


def _optional_text(params: dict[str, Any], key: str) -> str | None:
    value = _text(params, key)
    return value or None


def _optional_list(params: dict[str, Any], key: str) -> list[Any] | None:
    values = list(params.get(key) or [])
    return values or None


def _resolve_account(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_account(force_refresh=_force_refresh(params))


def _resolve_positions(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_positions(force_refresh=_force_refresh(params))


def _resolve_portfolio_timeseries(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_portfolio_timeseries(
        _text(params, "period", "1Y"),
        force_refresh=_force_refresh(params),
    )


def _resolve_performance_table(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_portfolio_performance(
        _text(params, "period", "1Y"),
        force_refresh=_force_refresh(params),
    )


def _resolve_daily_movers(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_daily_movers(
        symbols=_optional_list(params, "symbols"),
        force_refresh=_force_refresh(params),
    )


def _resolve_momentum_profiles(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_momentum_profiles(
        days=_int_value(params, "days", 180),
        symbols=_optional_list(params, "symbols"),
        force_refresh=_force_refresh(params),
    )


def _resolve_price_history(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_price_history(
        _text(params, "ticker"),
        days=_int_value(params, "days", 365),
        force_refresh=_force_refresh(params),
    )


def _resolve_technical_signal_history(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_technical_signal_history(
        _text(params, "ticker"),
        days=_int_value(params, "days", 365),
        force_refresh=_force_refresh(params),
    )


def _resolve_technical_signal_summary(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_technical_signal_summary(
        _text(params, "ticker"),
        force_refresh=_force_refresh(params),
    )


def _resolve_forecast_next_week(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_forecast_next_week(
        _text(params, "ticker"),
        days=_int_value(params, "days", 365),
        horizon=_int_value(params, "horizon", 5),
        simulations=_int_value(params, "simulations", 1500),
        force_refresh=_force_refresh(params),
    )


def _resolve_quarterly_fundamentals(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_quarterly_fundamentals(
        _text(params, "ticker"),
        force_refresh=_force_refresh(params),
    )


def _resolve_yield_curve_summary(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_yield_curve_summary(force_refresh=_force_refresh(params))


def _resolve_yield_curve_observations(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_yield_curve_observations(
        days=_int_value(params, "days", 365),
        force_refresh=_force_refresh(params),
    )


def _resolve_yield_curve_facts_1d(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_yield_curve_facts_1d(force_refresh=_force_refresh(params))


def _resolve_recent_news(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_recent_news(
        _text(params, "ticker"),
        days=_int_value(params, "days", 14),
        limit=_int_value(params, "limit", 8),
        force_refresh=_force_refresh(params),
    )


def _resolve_attention_context(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_attention_context(
        _text(params, "ticker"),
        force_refresh=_force_refresh(params),
    )


def _resolve_attention_ticker_snapshot(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_attention_ticker_snapshot(
        _text(params, "ticker"),
        force_refresh=_force_refresh(params),
    )


def _resolve_attention_ticker_background(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_attention_ticker_background(
        _text(params, "ticker"),
        force_refresh=_force_refresh(params),
    )


def _resolve_attention_home_1d(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_attention_home_1d(force_refresh=_force_refresh(params))


def _resolve_attention_research_bundle(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_attention_research_bundle(
        _text(params, "bundle_id"),
        force_refresh=_force_refresh(params),
    )


def _resolve_attention_run_trace(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_attention_run_trace(
        _text(params, "run_id"),
        force_refresh=_force_refresh(params),
    )


def _resolve_option_chain(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    expiration = params.get("expiration")
    return data_access.resolve_option_chain(
        _text(params, "ticker"),
        expiration=str(expiration) if expiration else None,
        force_refresh=_force_refresh(params),
    )


def _resolve_option_surface(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_option_surface(
        _text(params, "ticker"),
        expected_price=_required_float(params, "expected_price"),
        horizon_days=_required_int(params, "horizon_days"),
        underlying_price=_required_float(params, "underlying_price"),
        force_refresh=_force_refresh(params),
    )


def _resolve_option_candidates(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_option_candidates(
        _text(params, "ticker"),
        expected_price=_required_float(params, "expected_price"),
        horizon_days=_required_int(params, "horizon_days"),
        underlying_price=_required_float(params, "underlying_price"),
        force_refresh=_force_refresh(params),
    )


def _resolve_fred_dashboard(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_fred_dashboard(
        years=_int_value(params, "years", 10),
        force_refresh=_force_refresh(params),
    )


def _resolve_macro_release_events_1d(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_materialized_dataset(
        "macro_release_events_1d",
        force_refresh=_force_refresh(params),
    )


def _resolve_macro_relationship_checks_1d(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_materialized_dataset(
        "macro_relationship_checks_1d",
        force_refresh=_force_refresh(params),
    )


def _resolve_macro_causal_graph_edges_v1(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_materialized_dataset(
        "macro_causal_graph_edges_v1",
        force_refresh=_force_refresh(params),
    )


def _resolve_attention_hypotheses_1d(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_materialized_dataset(
        "attention_hypotheses_1d",
        force_refresh=_force_refresh(params),
    )


def _resolve_attention_macro_context_1d(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return data_access.resolve_materialized_dataset(
        "attention_macro_context_1d",
        force_refresh=_force_refresh(params),
    )


def _resolve_attention_feed(
    data_access: Any,
    params: dict[str, Any],
    *,
    dataset_name: str = "attention_feed",
) -> ResolvedPayload:
    return data_access.resolve_attention_feed(
        dataset_name=dataset_name,
        limit=_int_value(params, "limit", 10),
        entity_ids=_optional_list(params, "entity_ids"),
        horizons=_optional_list(params, "horizons"),
        statuses=_optional_list(params, "statuses"),
        sensitivity=_optional_text(params, "sensitivity"),
        min_attention_score=_optional_float(params, "min_attention_score"),
        residual_zscore_threshold=_optional_float(params, "residual_zscore_threshold"),
        force_refresh=_force_refresh(params),
    )


def _resolve_attention_rollups(
    data_access: Any,
    params: dict[str, Any],
    *,
    dataset_name: str = "attention_rollups",
) -> ResolvedPayload:
    return data_access.resolve_attention_rollups(
        dataset_name=dataset_name,
        rollup_type=_optional_text(params, "rollup_type"),
        horizons=_optional_list(params, "horizons"),
        statuses=_optional_list(params, "statuses"),
        sensitivity=_optional_text(params, "sensitivity"),
        min_attention_score=_optional_float(params, "min_attention_score"),
        residual_zscore_threshold=_optional_float(params, "residual_zscore_threshold"),
        high_priority_threshold=_optional_float(params, "high_priority_threshold"),
        limit=_int_value(params, "limit", 10),
        force_refresh=_force_refresh(params),
    )


def _resolve_commodity_attention_feed(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return _resolve_attention_feed(data_access, params, dataset_name="commodity_attention_feed")


def _resolve_commodity_attention_rollups(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return _resolve_attention_rollups(data_access, params, dataset_name="commodity_attention_rollups")


def _resolve_job_status(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    del params
    return data_access.latest_job_status()


def _build_portfolio_vs_benchmarks_chart(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    period = _text(params, "period", "1Y")
    resolved = data_access.resolve_portfolio_timeseries(period, force_refresh=_force_refresh(params))
    normalized = normalize_timeseries_view(resolved.payload)
    traces = [
        ChartTraceModel(
            trace_type="line",
            name=column,
            x="timestamp",
            y=column,
            style={
                "mode": "lines",
                "line": {"width": 3 if column == "portfolio" else 1.5},
                "opacity": 1.0 if column == "portfolio" else 0.65,
            },
        )
        for column in normalized.columns
        if column != "timestamp"
    ]
    chart = ChartModel(
        chart_id="portfolio_vs_benchmarks",
        title="Portfolio vs Benchmarks (Normalized to 100)",
        datasets={"primary": frame_to_records(normalized)},
        traces=traces,
        layout={
            "template": "plotly_dark",
            "xaxis_title": "Date",
            "yaxis_title": "Normalized Value",
            "hovermode": "x unified",
        },
        metadata={"period": period},
    )
    return ResolvedPayload(payload=chart, provenance=resolved.provenance)


def _build_signal_history_chart(
    data_access: Any,
    params: dict[str, Any],
    *,
    chart_id: str,
    title_suffix: str,
    traces: list[ChartTraceModel],
    yaxis_title: str,
) -> ResolvedPayload:
    ticker = _upper_text(params, "ticker")
    days = _int_value(params, "days", 365)
    resolved = data_access.resolve_technical_signal_history(
        ticker,
        days=days,
        force_refresh=_force_refresh(params),
    )
    frame = resolved.payload if isinstance(resolved.payload, pd.DataFrame) else pd.DataFrame()
    chart = ChartModel(
        chart_id=chart_id,
        title=f"{ticker} {title_suffix}",
        datasets={"primary": frame_to_records(frame)},
        traces=traces,
        layout={
            "template": "plotly_dark",
            "xaxis_title": "Date",
            "yaxis_title": yaxis_title,
            "hovermode": "x unified",
        },
        metadata={"ticker": ticker, "days": days},
    )
    return ResolvedPayload(payload=chart, provenance=resolved.provenance)


def _build_technical_price_channel_chart(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return _build_signal_history_chart(
        data_access,
        params,
        chart_id="technical_price_channel",
        title_suffix="Price Channel",
        traces=[
            ChartTraceModel(
                trace_type="line",
                name="Support",
                x="timestamp",
                y="channel_support",
                style={"mode": "lines", "line": {"color": "rgba(45, 212, 191, 0.45)", "width": 1.5}},
            ),
            ChartTraceModel(
                trace_type="line",
                name="Resistance",
                x="timestamp",
                y="channel_resistance",
                style={
                    "mode": "lines",
                    "line": {"color": "rgba(249, 115, 22, 0.45)", "width": 1.5},
                    "fill": "tonexty",
                    "fillcolor": "rgba(148, 163, 184, 0.12)",
                },
            ),
            ChartTraceModel(
                trace_type="line",
                name="Close",
                x="timestamp",
                y="close",
                style={"mode": "lines", "line": {"color": "#f8fafc", "width": 2.5}},
            ),
            ChartTraceModel(
                trace_type="line",
                name="ATH",
                x="timestamp",
                y="ath",
                style={"mode": "lines", "line": {"color": "#a78bfa", "width": 1, "dash": "dash"}},
            ),
        ],
        yaxis_title="Price",
    )


def _build_technical_pullback_chart(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    return _build_signal_history_chart(
        data_access,
        params,
        chart_id="technical_pullback",
        title_suffix="Pullback From ATH",
        traces=[
            ChartTraceModel(
                trace_type="line",
                name="Pullback %",
                x="timestamp",
                y="pullback_from_ath_pct",
                style={
                    "mode": "lines",
                    "line": {"color": "#38bdf8", "width": 2},
                    "fill": "tozeroy",
                    "fillcolor": "rgba(56, 189, 248, 0.18)",
                },
            )
        ],
        yaxis_title="Pullback %",
    )


def _build_fundamental_statement_chart(data_access: Any, params: dict[str, Any]) -> ResolvedPayload:
    ticker = _upper_text(params, "ticker")
    statement = _text(params, "statement", "income").lower()
    resolved = data_access.resolve_quarterly_fundamentals(
        ticker,
        force_refresh=_force_refresh(params),
    )
    datasets = resolved.payload if isinstance(resolved.payload, dict) else {}
    frame = datasets.get(statement, pd.DataFrame())
    metric_names = sorted(
        {
            str(value)
            for value in frame.get("metric", pd.Series(dtype=str)).dropna().astype(str).tolist()
        }
    )
    traces = [
        ChartTraceModel(
            trace_type="line",
            name=metric,
            x="report_date",
            y="value",
            where={"metric": metric},
            style={"mode": "lines+markers"},
            options={"hover_data": ["year_quarter"]},
        )
        for metric in metric_names
    ]
    chart = ChartModel(
        chart_id="fundamental_statement",
        title=f"{ticker} - {statement.title()} Statement (Quarterly)",
        datasets={"primary": frame_to_records(frame)},
        traces=traces,
        layout={
            "template": "plotly_dark",
            "xaxis_title": "Report Date",
            "yaxis_title": "Value",
            "hovermode": "x unified",
        },
        metadata={"ticker": ticker, "statement": statement},
    )
    return ResolvedPayload(payload=chart, provenance=resolved.provenance)


DATASET_SPECS: dict[str, DatasetSpec] = {
    "account": DatasetSpec(params=(), resolution="live_cached", handler=_resolve_account),
    "positions": DatasetSpec(params=(), resolution="materialized_first", handler=_resolve_positions),
    "portfolio_timeseries": DatasetSpec(
        params=("period", "force_refresh"),
        resolution="materialized_first",
        handler=_resolve_portfolio_timeseries,
    ),
    "performance_table": DatasetSpec(
        params=("period", "force_refresh"),
        resolution="computed_from_materialized_first",
        handler=_resolve_performance_table,
    ),
    "daily_movers": DatasetSpec(
        params=("symbols", "force_refresh"),
        resolution="materialized_first",
        handler=_resolve_daily_movers,
    ),
    "momentum_profiles": DatasetSpec(
        params=("days", "symbols", "force_refresh"),
        resolution="materialized_first",
        handler=_resolve_momentum_profiles,
    ),
    "price_history": DatasetSpec(
        params=("ticker", "days", "force_refresh"),
        resolution="materialized_first",
        handler=_resolve_price_history,
    ),
    "technical_signal_history": DatasetSpec(
        params=("ticker", "days", "force_refresh"),
        resolution="materialized_first_then_computed",
        handler=_resolve_technical_signal_history,
    ),
    "technical_signal_summary": DatasetSpec(
        params=("ticker", "force_refresh"),
        resolution="materialized_first_then_computed",
        handler=_resolve_technical_signal_summary,
    ),
    "forecast_next_week": DatasetSpec(
        params=("ticker", "days", "horizon", "simulations", "force_refresh"),
        resolution="computed_from_signal_history",
        handler=_resolve_forecast_next_week,
    ),
    "quarterly_fundamentals": DatasetSpec(
        params=("ticker", "force_refresh"),
        resolution="materialized_first",
        handler=_resolve_quarterly_fundamentals,
    ),
    "yield_curve_summary": DatasetSpec(
        params=("force_refresh",),
        resolution="materialized_first",
        handler=_resolve_yield_curve_summary,
    ),
    "yield_curve_observations": DatasetSpec(
        params=("days", "force_refresh"),
        resolution="materialized_first",
        handler=_resolve_yield_curve_observations,
    ),
    "yield_curve_facts_1d": DatasetSpec(
        params=("force_refresh",),
        resolution="materialized_first",
        handler=_resolve_yield_curve_facts_1d,
    ),
    "recent_news": DatasetSpec(
        params=("ticker", "days", "limit", "force_refresh"),
        resolution="materialized_first",
        handler=_resolve_recent_news,
    ),
    "attention_context": DatasetSpec(
        params=("ticker", "force_refresh"),
        resolution="materialized",
        handler=_resolve_attention_context,
    ),
    "attention_ticker_snapshot": DatasetSpec(
        params=("ticker", "force_refresh"),
        resolution="materialized_first",
        handler=_resolve_attention_ticker_snapshot,
    ),
    "attention_ticker_background": DatasetSpec(
        params=("ticker", "force_refresh"),
        resolution="materialized_first",
        handler=_resolve_attention_ticker_background,
    ),
    "attention_home_1d": DatasetSpec(
        params=("force_refresh",),
        resolution="materialized_first_then_on_demand",
        handler=_resolve_attention_home_1d,
    ),
    "attention_research_bundle": DatasetSpec(
        params=("bundle_id", "force_refresh"),
        resolution="materialized_first_then_on_demand",
        handler=_resolve_attention_research_bundle,
    ),
    "attention_run_trace": DatasetSpec(
        params=("run_id", "force_refresh"),
        resolution="materialized",
        handler=_resolve_attention_run_trace,
    ),
    "option_chain": DatasetSpec(
        params=("ticker", "expiration", "force_refresh"),
        resolution="materialized_first",
        handler=_resolve_option_chain,
    ),
    "option_surface": DatasetSpec(
        params=("ticker", "expected_price", "horizon_days", "underlying_price", "force_refresh"),
        resolution="materialized_first_then_on_demand",
        handler=_resolve_option_surface,
    ),
    "option_candidates": DatasetSpec(
        params=("ticker", "expected_price", "horizon_days", "underlying_price", "force_refresh"),
        resolution="computed_from_option_surface",
        handler=_resolve_option_candidates,
    ),
    "fred_dashboard": DatasetSpec(
        params=("years", "force_refresh"),
        resolution="materialized_first",
        handler=_resolve_fred_dashboard,
    ),
    "macro_release_events_1d": DatasetSpec(
        params=("force_refresh",),
        resolution="materialized",
        handler=_resolve_macro_release_events_1d,
    ),
    "macro_relationship_checks_1d": DatasetSpec(
        params=("force_refresh",),
        resolution="materialized",
        handler=_resolve_macro_relationship_checks_1d,
    ),
    "macro_causal_graph_edges_v1": DatasetSpec(
        params=("force_refresh",),
        resolution="materialized",
        handler=_resolve_macro_causal_graph_edges_v1,
    ),
    "attention_hypotheses_1d": DatasetSpec(
        params=("force_refresh",),
        resolution="materialized",
        handler=_resolve_attention_hypotheses_1d,
    ),
    "attention_macro_context_1d": DatasetSpec(
        params=("force_refresh",),
        resolution="materialized",
        handler=_resolve_attention_macro_context_1d,
    ),
    "attention_feed": DatasetSpec(
        params=(
            "limit",
            "entity_ids",
            "horizons",
            "statuses",
            "sensitivity",
            "min_attention_score",
            "residual_zscore_threshold",
            "force_refresh",
        ),
        resolution="materialized",
        handler=_resolve_attention_feed,
    ),
    "attention_rollups": DatasetSpec(
        params=(
            "rollup_type",
            "horizons",
            "statuses",
            "sensitivity",
            "min_attention_score",
            "residual_zscore_threshold",
            "high_priority_threshold",
            "limit",
            "force_refresh",
        ),
        resolution="materialized",
        handler=_resolve_attention_rollups,
    ),
    "commodity_attention_feed": DatasetSpec(
        params=(
            "limit",
            "entity_ids",
            "horizons",
            "statuses",
            "sensitivity",
            "min_attention_score",
            "residual_zscore_threshold",
            "force_refresh",
        ),
        resolution="materialized",
        handler=_resolve_commodity_attention_feed,
    ),
    "commodity_attention_rollups": DatasetSpec(
        params=(
            "rollup_type",
            "horizons",
            "statuses",
            "sensitivity",
            "min_attention_score",
            "residual_zscore_threshold",
            "high_priority_threshold",
            "limit",
            "force_refresh",
        ),
        resolution="materialized",
        handler=_resolve_commodity_attention_rollups,
    ),
    "job_status": DatasetSpec(params=(), resolution="materialized", handler=_resolve_job_status),
}


CHART_SPECS: dict[str, ChartSpec] = {
    "portfolio_vs_benchmarks": ChartSpec(
        params=("period", "force_refresh"),
        resolution="computed_from_portfolio_timeseries",
        handler=_build_portfolio_vs_benchmarks_chart,
    ),
    "technical_price_channel": ChartSpec(
        params=("ticker", "days", "force_refresh"),
        resolution="computed_from_signal_history",
        handler=_build_technical_price_channel_chart,
    ),
    "technical_pullback": ChartSpec(
        params=("ticker", "days", "force_refresh"),
        resolution="computed_from_signal_history",
        handler=_build_technical_pullback_chart,
    ),
    "fundamental_statement": ChartSpec(
        params=("ticker", "statement", "force_refresh"),
        resolution="computed_from_quarterly_fundamentals",
        handler=_build_fundamental_statement_chart,
    ),
}


DATASET_CAPABILITIES = _capabilities(DATASET_SPECS)
CHART_CAPABILITIES = _capabilities(CHART_SPECS)
