from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pandas as pd

from data_access.contracts import ChartModel, ChartTraceModel, QueryRequest, QueryResponse, ResolvedPayload, frame_to_records
from data_access.layer import DataAccessLayer
from compute.portfolio import normalize_timeseries_view


DATASET_CAPABILITIES: dict[str, dict[str, Any]] = {
    "account": {"params": [], "resolution": "live_cached"},
    "positions": {"params": [], "resolution": "live_cached"},
    "portfolio_timeseries": {"params": ["period", "force_refresh"], "resolution": "live_cached"},
    "performance_table": {"params": ["period", "force_refresh"], "resolution": "computed_from_live_cached"},
    "daily_movers": {"params": ["symbols", "force_refresh"], "resolution": "materialized_first"},
    "momentum_profiles": {"params": ["days", "symbols", "force_refresh"], "resolution": "materialized_first"},
    "price_history": {"params": ["ticker", "days", "force_refresh"], "resolution": "materialized_first"},
    "technical_signal_history": {"params": ["ticker", "days", "force_refresh"], "resolution": "materialized_first_then_computed"},
    "technical_signal_summary": {"params": ["ticker", "force_refresh"], "resolution": "materialized_first_then_computed"},
    "forecast_next_week": {"params": ["ticker", "days", "horizon", "simulations", "force_refresh"], "resolution": "computed_from_signal_history"},
    "quarterly_fundamentals": {"params": ["ticker", "force_refresh"], "resolution": "materialized_first"},
    "recent_news": {"params": ["ticker", "days", "limit", "force_refresh"], "resolution": "materialized_first"},
    "option_chain": {"params": ["ticker", "expiration", "force_refresh"], "resolution": "materialized_first"},
    "option_surface": {"params": ["ticker", "expected_price", "horizon_days", "underlying_price", "force_refresh"], "resolution": "materialized_first_then_on_demand"},
    "option_candidates": {"params": ["ticker", "expected_price", "horizon_days", "underlying_price", "force_refresh"], "resolution": "computed_from_option_surface"},
    "fred_dashboard": {"params": ["years", "force_refresh"], "resolution": "materialized_first"},
    "attention_feed": {"params": ["limit", "entity_ids", "horizons", "statuses", "sensitivity", "min_attention_score", "residual_zscore_threshold", "force_refresh"], "resolution": "materialized"},
    "attention_rollups": {"params": ["rollup_type", "horizons", "statuses", "sensitivity", "min_attention_score", "residual_zscore_threshold", "high_priority_threshold", "limit", "force_refresh"], "resolution": "materialized"},
    "commodity_attention_feed": {"params": ["limit", "entity_ids", "horizons", "statuses", "sensitivity", "min_attention_score", "residual_zscore_threshold", "force_refresh"], "resolution": "materialized"},
    "commodity_attention_rollups": {"params": ["rollup_type", "horizons", "statuses", "sensitivity", "min_attention_score", "residual_zscore_threshold", "high_priority_threshold", "limit", "force_refresh"], "resolution": "materialized"},
    "job_status": {"params": [], "resolution": "materialized"},
}

CHART_CAPABILITIES: dict[str, dict[str, Any]] = {
    "portfolio_vs_benchmarks": {"params": ["period", "force_refresh"], "resolution": "computed_from_portfolio_timeseries"},
    "technical_price_channel": {"params": ["ticker", "days", "force_refresh"], "resolution": "computed_from_signal_history"},
    "technical_pullback": {"params": ["ticker", "days", "force_refresh"], "resolution": "computed_from_signal_history"},
    "fundamental_statement": {"params": ["ticker", "statement", "force_refresh"], "resolution": "computed_from_quarterly_fundamentals"},
}


@dataclass(frozen=True)
class QueryService:
    data_access: DataAccessLayer

    @classmethod
    def from_environment(cls) -> "QueryService":
        return cls(data_access=DataAccessLayer.from_environment())

    def list_capabilities(self) -> dict[str, Any]:
        return {"datasets": deepcopy(DATASET_CAPABILITIES), "charts": deepcopy(CHART_CAPABILITIES)}

    def fetch_dataset(self, name: str, params: dict[str, Any] | None = None) -> ResolvedPayload:
        params = dict(params or {})
        key = str(name or "").strip().lower()

        if key == "account":
            return self.data_access.resolve_account(force_refresh=bool(params.get("force_refresh", False)))
        if key == "positions":
            return self.data_access.resolve_positions(force_refresh=bool(params.get("force_refresh", False)))
        if key == "portfolio_timeseries":
            period = str(params.get("period") or "1Y")
            return self.data_access.resolve_portfolio_timeseries(period, force_refresh=bool(params.get("force_refresh", False)))
        if key == "performance_table":
            period = str(params.get("period") or "1Y")
            return self.data_access.resolve_portfolio_performance(period, force_refresh=bool(params.get("force_refresh", False)))
        if key == "daily_movers":
            return self.data_access.resolve_daily_movers(
                symbols=list(params.get("symbols") or []) or None,
                force_refresh=bool(params.get("force_refresh", False)),
            )
        if key == "momentum_profiles":
            return self.data_access.resolve_momentum_profiles(
                days=int(params.get("days") or 180),
                symbols=list(params.get("symbols") or []) or None,
                force_refresh=bool(params.get("force_refresh", False)),
            )
        if key == "price_history":
            ticker = str(params.get("ticker") or "").strip()
            days = int(params.get("days") or 365)
            return self.data_access.resolve_price_history(ticker, days=days, force_refresh=bool(params.get("force_refresh", False)))
        if key == "technical_signal_history":
            ticker = str(params.get("ticker") or "").strip()
            days = int(params.get("days") or 365)
            return self.data_access.resolve_technical_signal_history(ticker, days=days, force_refresh=bool(params.get("force_refresh", False)))
        if key == "technical_signal_summary":
            ticker = str(params.get("ticker") or "").strip()
            return self.data_access.resolve_technical_signal_summary(ticker, force_refresh=bool(params.get("force_refresh", False)))
        if key == "forecast_next_week":
            ticker = str(params.get("ticker") or "").strip()
            return self.data_access.resolve_forecast_next_week(
                ticker,
                days=int(params.get("days") or 365),
                horizon=int(params.get("horizon") or 5),
                simulations=int(params.get("simulations") or 1500),
                force_refresh=bool(params.get("force_refresh", False)),
            )
        if key == "quarterly_fundamentals":
            ticker = str(params.get("ticker") or "").strip()
            return self.data_access.resolve_quarterly_fundamentals(ticker, force_refresh=bool(params.get("force_refresh", False)))
        if key == "recent_news":
            ticker = str(params.get("ticker") or "").strip()
            return self.data_access.resolve_recent_news(
                ticker,
                days=int(params.get("days") or 14),
                limit=int(params.get("limit") or 8),
                force_refresh=bool(params.get("force_refresh", False)),
            )
        if key == "option_chain":
            ticker = str(params.get("ticker") or "").strip()
            expiration = params.get("expiration")
            return self.data_access.resolve_option_chain(ticker, expiration=str(expiration) if expiration else None, force_refresh=bool(params.get("force_refresh", False)))
        if key == "option_surface":
            ticker = str(params.get("ticker") or "").strip()
            return self.data_access.resolve_option_surface(
                ticker,
                expected_price=float(params.get("expected_price")),
                horizon_days=int(params.get("horizon_days")),
                underlying_price=float(params.get("underlying_price")),
                force_refresh=bool(params.get("force_refresh", False)),
            )
        if key == "option_candidates":
            ticker = str(params.get("ticker") or "").strip()
            return self.data_access.resolve_option_candidates(
                ticker,
                expected_price=float(params.get("expected_price")),
                horizon_days=int(params.get("horizon_days")),
                underlying_price=float(params.get("underlying_price")),
                force_refresh=bool(params.get("force_refresh", False)),
            )
        if key == "fred_dashboard":
            return self.data_access.resolve_fred_dashboard(years=int(params.get("years") or 10), force_refresh=bool(params.get("force_refresh", False)))
        if key == "attention_feed":
            return self.data_access.resolve_attention_feed(
                limit=int(params.get("limit") or 10),
                entity_ids=list(params.get("entity_ids") or []) or None,
                horizons=list(params.get("horizons") or []) or None,
                statuses=list(params.get("statuses") or []) or None,
                sensitivity=str(params.get("sensitivity") or "").strip() or None,
                min_attention_score=float(params["min_attention_score"]) if params.get("min_attention_score") is not None else None,
                residual_zscore_threshold=float(params["residual_zscore_threshold"]) if params.get("residual_zscore_threshold") is not None else None,
                force_refresh=bool(params.get("force_refresh", False)),
            )
        if key == "attention_rollups":
            return self.data_access.resolve_attention_rollups(
                rollup_type=str(params.get("rollup_type") or "").strip() or None,
                horizons=list(params.get("horizons") or []) or None,
                statuses=list(params.get("statuses") or []) or None,
                sensitivity=str(params.get("sensitivity") or "").strip() or None,
                min_attention_score=float(params["min_attention_score"]) if params.get("min_attention_score") is not None else None,
                residual_zscore_threshold=float(params["residual_zscore_threshold"]) if params.get("residual_zscore_threshold") is not None else None,
                high_priority_threshold=float(params["high_priority_threshold"]) if params.get("high_priority_threshold") is not None else None,
                limit=int(params.get("limit") or 10),
                force_refresh=bool(params.get("force_refresh", False)),
            )
        if key == "commodity_attention_feed":
            return self.data_access.resolve_attention_feed(
                dataset_name="commodity_attention_feed",
                limit=int(params.get("limit") or 10),
                entity_ids=list(params.get("entity_ids") or []) or None,
                horizons=list(params.get("horizons") or []) or None,
                statuses=list(params.get("statuses") or []) or None,
                sensitivity=str(params.get("sensitivity") or "").strip() or None,
                min_attention_score=float(params["min_attention_score"]) if params.get("min_attention_score") is not None else None,
                residual_zscore_threshold=float(params["residual_zscore_threshold"]) if params.get("residual_zscore_threshold") is not None else None,
                force_refresh=bool(params.get("force_refresh", False)),
            )
        if key == "commodity_attention_rollups":
            return self.data_access.resolve_attention_rollups(
                dataset_name="commodity_attention_rollups",
                rollup_type=str(params.get("rollup_type") or "").strip() or None,
                horizons=list(params.get("horizons") or []) or None,
                statuses=list(params.get("statuses") or []) or None,
                sensitivity=str(params.get("sensitivity") or "").strip() or None,
                min_attention_score=float(params["min_attention_score"]) if params.get("min_attention_score") is not None else None,
                residual_zscore_threshold=float(params["residual_zscore_threshold"]) if params.get("residual_zscore_threshold") is not None else None,
                high_priority_threshold=float(params["high_priority_threshold"]) if params.get("high_priority_threshold") is not None else None,
                limit=int(params.get("limit") or 10),
                force_refresh=bool(params.get("force_refresh", False)),
            )
        if key == "job_status":
            return self.data_access.latest_job_status()

        raise ValueError(f"Unsupported dataset '{name}'.")

    def build_chart(self, name: str, params: dict[str, Any] | None = None) -> ResolvedPayload:
        params = dict(params or {})
        key = str(name or "").strip().lower()

        if key == "portfolio_vs_benchmarks":
            period = str(params.get("period") or "1Y")
            resolved = self.data_access.resolve_portfolio_timeseries(period, force_refresh=bool(params.get("force_refresh", False)))
            normalized = normalize_timeseries_view(resolved.payload)
            traces = [
                ChartTraceModel(
                    trace_type="line",
                    name=column,
                    x="timestamp",
                    y=column,
                    style={"mode": "lines", "line": {"width": 3 if column == "portfolio" else 1.5}, "opacity": 1.0 if column == "portfolio" else 0.65},
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

        if key == "technical_price_channel":
            ticker = str(params.get("ticker") or "").strip().upper()
            days = int(params.get("days") or 365)
            resolved = self.data_access.resolve_technical_signal_history(ticker, days=days, force_refresh=bool(params.get("force_refresh", False)))
            frame = resolved.payload if isinstance(resolved.payload, pd.DataFrame) else pd.DataFrame()
            chart = ChartModel(
                chart_id="technical_price_channel",
                title=f"{ticker} Price Channel",
                datasets={"primary": frame_to_records(frame)},
                traces=[
                    ChartTraceModel(trace_type="line", name="Support", x="timestamp", y="channel_support", style={"mode": "lines", "line": {"color": "rgba(45, 212, 191, 0.45)", "width": 1.5}}),
                    ChartTraceModel(trace_type="line", name="Resistance", x="timestamp", y="channel_resistance", style={"mode": "lines", "line": {"color": "rgba(249, 115, 22, 0.45)", "width": 1.5}, "fill": "tonexty", "fillcolor": "rgba(148, 163, 184, 0.12)"}),
                    ChartTraceModel(trace_type="line", name="Close", x="timestamp", y="close", style={"mode": "lines", "line": {"color": "#f8fafc", "width": 2.5}}),
                    ChartTraceModel(trace_type="line", name="ATH", x="timestamp", y="ath", style={"mode": "lines", "line": {"color": "#a78bfa", "width": 1, "dash": "dash"}}),
                ],
                layout={"template": "plotly_dark", "xaxis_title": "Date", "yaxis_title": "Price", "hovermode": "x unified"},
                metadata={"ticker": ticker, "days": days},
            )
            return ResolvedPayload(payload=chart, provenance=resolved.provenance)

        if key == "technical_pullback":
            ticker = str(params.get("ticker") or "").strip().upper()
            days = int(params.get("days") or 365)
            resolved = self.data_access.resolve_technical_signal_history(ticker, days=days, force_refresh=bool(params.get("force_refresh", False)))
            frame = resolved.payload if isinstance(resolved.payload, pd.DataFrame) else pd.DataFrame()
            chart = ChartModel(
                chart_id="technical_pullback",
                title=f"{ticker} Pullback From ATH",
                datasets={"primary": frame_to_records(frame)},
                traces=[
                    ChartTraceModel(
                        trace_type="line",
                        name="Pullback %",
                        x="timestamp",
                        y="pullback_from_ath_pct",
                        style={"mode": "lines", "line": {"color": "#38bdf8", "width": 2}, "fill": "tozeroy", "fillcolor": "rgba(56, 189, 248, 0.18)"},
                    )
                ],
                layout={"template": "plotly_dark", "xaxis_title": "Date", "yaxis_title": "Pullback %", "hovermode": "x unified"},
                metadata={"ticker": ticker, "days": days},
            )
            return ResolvedPayload(payload=chart, provenance=resolved.provenance)

        if key == "fundamental_statement":
            ticker = str(params.get("ticker") or "").strip().upper()
            statement = str(params.get("statement") or "income").strip().lower()
            resolved = self.data_access.resolve_quarterly_fundamentals(ticker, force_refresh=bool(params.get("force_refresh", False)))
            datasets = resolved.payload if isinstance(resolved.payload, dict) else {}
            frame = datasets.get(statement, pd.DataFrame())
            metric_names = sorted({str(value) for value in frame.get("metric", pd.Series(dtype=str)).dropna().astype(str).tolist()})
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
                layout={"template": "plotly_dark", "xaxis_title": "Report Date", "yaxis_title": "Value", "hovermode": "x unified"},
                metadata={"ticker": ticker, "statement": statement},
            )
            return ResolvedPayload(payload=chart, provenance=resolved.provenance)

        raise ValueError(f"Unsupported chart '{name}'.")

    def execute(self, request: QueryRequest | dict[str, Any]) -> QueryResponse:
        query = request if isinstance(request, QueryRequest) else QueryRequest.from_dict(request)

        if query.operation == "capabilities":
            return QueryResponse(request=query, result_type="capabilities", payload=self.list_capabilities())
        if query.operation == "dataset":
            resolved = self.fetch_dataset(query.name, query.params)
            payload = resolved.payload
            if isinstance(payload, pd.DataFrame):
                payload = frame_to_records(payload)
            elif isinstance(payload, dict):
                payload = {
                    key: frame_to_records(value) if isinstance(value, pd.DataFrame) else value
                    for key, value in payload.items()
                }
            elif isinstance(payload, tuple):
                payload = [
                    frame_to_records(value) if isinstance(value, pd.DataFrame) else value
                    for value in payload
                ]
            return QueryResponse(request=query, result_type="dataset", payload=payload, provenance=resolved.provenance)
        if query.operation == "chart":
            resolved = self.build_chart(query.name, query.params)
            return QueryResponse(request=query, result_type="chart_model", payload=resolved.payload, provenance=resolved.provenance)

        raise ValueError(f"Unsupported operation '{query.operation}'.")
