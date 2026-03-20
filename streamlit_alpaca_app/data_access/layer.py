from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from compute.analytics import performance_table
from compute.anomalies import AttentionConfig, attention_preset, build_attention_feed, build_attention_rollups, filter_attention_events, normalize_horizon, normalize_horizons
from compute.fred import build_fred_dashboard_from_pipeline
from compute.fundamentals import load_quarterly_fundamentals
from compute.portfolio import build_portfolio_timeseries, compute_holding_roc, normalize_timeseries_view
from data_access.contracts import DataProvenance, ResolvedPayload
from services.alpaca_api import AlpacaAPI, AlpacaAPIError
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
from services.fred import load_fred_api_key, load_fred_dashboard
from services.market import DEFAULT_UNIVERSE, load_price_history, scan_commodity_regimes, scan_correlation_phase_shifts, scan_daily_movers, scan_momentum_profiles
from services.options import analyze_option_candidates, load_option_chain, load_option_surface, select_option_surface_window
from services.pipeline_store import latest_job_status_table, load_latest_dataset_frame, pipeline_store_configured, start_source_refresh_job

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


@dataclass(frozen=True)
class DataAccessLayer:
    cfg: AppConfig | None = None
    fred_api_key: str | None = None

    @classmethod
    def from_environment(cls) -> "DataAccessLayer":
        return cls(cfg=load_config(), fred_api_key=load_fred_api_key())

    def _resolved(self, payload: Any, *, mode: str, datasets: tuple[str, ...], details: dict[str, Any] | None = None) -> ResolvedPayload:
        return ResolvedPayload(payload=payload, provenance=DataProvenance(mode=mode, datasets=datasets, details=details or {}))

    def _pipeline_frame(self, dataset_name: str) -> tuple[pd.DataFrame, dict[str, Any]]:
        frame, metadata = load_latest_dataset_frame(dataset_name)
        return frame, _pipeline_details(metadata)

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
        return self._pipeline_frame(dataset_name)

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
            frames[dataset_name] = frame
            details[dataset_name] = metadata
        return frames, details

    def resolve_account(self, *, force_refresh: bool = False) -> ResolvedPayload:
        frame = cached_scalar_dict(
            "account",
            _alpaca_cache_scope(self.cfg) if self.cfg is not None else "missing-config",
            lambda: _make_api(self.cfg).get_account(),
            force_refresh=force_refresh,
        )
        return self._resolved(frame, mode="on_demand", datasets=("account",))

    def resolve_positions(self, *, force_refresh: bool = False) -> ResolvedPayload:
        frame = cached_frame(
            "positions",
            _alpaca_cache_scope(self.cfg) if self.cfg is not None else "missing-config",
            lambda: _make_api(self.cfg).get_positions(),
            force_refresh=force_refresh,
        )
        return self._resolved(frame, mode="on_demand", datasets=("positions",))

    def resolve_portfolio_timeseries(self, period: str, *, force_refresh: bool = False) -> ResolvedPayload:
        frame = cached_frame(
            "portfolio_timeseries",
            f"{_alpaca_cache_scope(self.cfg)}__period_{period}" if self.cfg is not None else f"missing-config__period_{period}",
            lambda: build_portfolio_timeseries(_make_api(self.cfg), period),
            force_refresh=force_refresh,
        )
        return self._resolved(frame, mode="on_demand", datasets=("portfolio_timeseries",), details={"period": period})

    def resolve_portfolio_performance(self, period: str, *, force_refresh: bool = False) -> ResolvedPayload:
        resolved = self.resolve_portfolio_timeseries(period, force_refresh=force_refresh)
        normalized = normalize_timeseries_view(resolved.payload)
        table = performance_table(normalized)
        return self._resolved(table, mode=resolved.provenance.mode, datasets=("portfolio_timeseries", "performance_table"), details={"period": period})

    def resolve_holding_roc(self, symbols: list[str], *, days: int = 365, force_refresh: bool = False) -> ResolvedPayload:
        normalized_symbols = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
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

        universe = symbols or DEFAULT_UNIVERSE
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

        universe = symbols or DEFAULT_UNIVERSE
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

        universe = symbols or DEFAULT_UNIVERSE
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

        universe = symbols or DEFAULT_UNIVERSE
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
            resolved = self.resolve_technical_signal_history(ticker, days=days, force_refresh=force_refresh)
            frame = resolved.payload
            details = {**details, **resolved.provenance.details}
            resolved_mode = resolved.provenance.mode
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

        payload = cached_frame_dict(
            "quarterly_fundamentals",
            ticker.upper(),
            lambda: load_quarterly_fundamentals(ticker),
            keys=["income", "balance", "cashflow"],
            force_refresh=force_refresh,
        )
        return self._resolved(payload, mode="on_demand", datasets=("quarterly_fundamentals",), details={"ticker": ticker.upper()})

    def resolve_asset_metadata(self, ticker: str, *, force_refresh: bool = False) -> ResolvedPayload:
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
                    if isinstance(value, (list, tuple, set)):
                        return target in [str(item).upper().strip() for item in value]
                    blob = str(value or "").replace("|", ",")
                    return target in [item.upper().strip() for item in blob.split(",") if item.strip()]

                rows = pipeline.copy()
                if "symbols" in rows.columns:
                    rows = rows[rows["symbols"].apply(_has_symbol)].copy()
                if not rows.empty:
                    if "published_at" in rows.columns:
                        rows["published_at"] = pd.to_datetime(rows["published_at"], utc=True, errors="coerce")
                        rows = rows.sort_values("published_at", ascending=False, na_position="last")
                    rows = rows.head(limit).reset_index(drop=True)
                    return self._resolved({"articles": rows, "fallback_summary": None, "source": "pipeline"}, mode="materialized", datasets=("news_articles",), details=details)

        payload = cached_news_payload(
            "recent_news",
            f"{_alpaca_cache_scope(self.cfg)}__{ticker.upper()}__{days}d__{limit}" if self.cfg is not None else f"missing-config__{ticker.upper()}__{days}d__{limit}",
            lambda: load_recent_news(_make_api(self.cfg), ticker, days=days, limit=limit),
            force_refresh=force_refresh,
        )
        return self._resolved(payload, mode="on_demand", datasets=("recent_news",), details={"ticker": ticker.upper(), "days": days, "limit": limit})

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

        if not api_key:
            raise RuntimeError("FRED API key unavailable and no materialized FRED datasets were found.")

        payload = cached_fred_dashboard(
            "fred_dashboard",
            f"{_fred_cache_scope(api_key)}__{years}y",
            lambda: load_fred_dashboard(api_key, years=years),
            force_refresh=force_refresh,
        )
        return self._resolved(payload, mode="on_demand", datasets=("fred_dashboard",), details={"years": years})

    def latest_job_status(self) -> ResolvedPayload:
        return self._resolved(latest_job_status_table(), mode="materialized", datasets=("job_runs",))

    def trigger_source_refresh(self, source: str) -> ResolvedPayload:
        ok, message = start_source_refresh_job(source)
        return self._resolved({"ok": ok, "message": message}, mode="command", datasets=("job_runs",), details={"source": source})
