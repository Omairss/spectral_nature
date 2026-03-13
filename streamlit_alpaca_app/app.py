from __future__ import annotations

from contextlib import contextmanager
from datetime import timezone
import importlib
import logging
import os
import time

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from services.alpaca_api import AlpacaAPI, AlpacaAPIError
from services.analytics import (
    BENCHMARKS,
    build_metric_bar,
    build_portfolio_vs_benchmarks_fig,
    normalize_to_100,
    performance_table,
)
from services.company import build_company_description, load_asset_metadata, load_recent_news, summarize_recent_news
from services.config import AppConfig, load_config
from services.data_cache import (
    cache_bundle_exists,
    cache_data_root,
    cache_policy_path,
    cached_frame,
    cached_frame_dict,
    cached_fred_dashboard,
    cached_news_payload,
    cached_option_chain,
    cached_scalar_dict,
    dataset_scope,
)
from services.fred import (
    FRED_CATEGORY_BLURBS,
    FredAPIError,
    FredSeriesSpec,
    build_fred_figure,
    build_fred_series_summary,
    format_fred_delta,
    format_fred_value,
    fred_categories,
    fred_specs_by_category,
    load_fred_api_key,
    load_fred_dashboard,
)
from services.pipeline_store import (
    load_latest_dataset_frame,
    pipeline_store_configured,
    start_source_refresh_job,
)
from services.fundamentals import load_quarterly_fundamentals, plot_statement
from services.market import (
    DEFAULT_UNIVERSE,
    business_focus_description,
    business_focus_options,
    business_focus_universe,
    commodity_dependency_graph,
    commodity_focus_description,
    commodity_focus_options,
    commodity_focus_universe,
    commodity_proxy_profile,
    commodity_reference_universe,
    load_price_history,
    scan_commodity_regimes,
    scan_correlation_phase_shifts,
    scan_daily_movers,
    scan_momentum_profiles,
)
from services.options import (
    analyze_option_candidates,
    load_option_chain,
    load_option_surface,
    rank_options,
    select_option_surface_window,
)
from services.technicals import build_technical_figure

_SIGNALS_IMPORT_ERROR: str | None = None
try:
    importlib.invalidate_caches()
    _signals = importlib.import_module("services.signals")
    build_forecast_cone_figure = _signals.build_forecast_cone_figure
    build_price_channel_figure = _signals.build_price_channel_figure
    build_pullback_figure = _signals.build_pullback_figure
    build_signal_frame = _signals.build_signal_frame
    build_terminal_distribution_figure = _signals.build_terminal_distribution_figure
    forecast_next_week = _signals.forecast_next_week
    summarize_signal_frame = _signals.summarize_signal_frame
except ModuleNotFoundError as exc:
    if exc.name != "services.signals":
        raise
    _SIGNALS_IMPORT_ERROR = str(exc)

    def build_signal_frame(price: pd.DataFrame, channel_window: int = 63) -> pd.DataFrame:
        return pd.DataFrame()

    def summarize_signal_frame(frame: pd.DataFrame) -> dict[str, float | str]:
        return {}

    def forecast_next_week(frame: pd.DataFrame, horizon: int = 5, simulations: int = 1500) -> dict[str, object]:
        return {}

    def build_price_channel_figure(frame: pd.DataFrame, ticker: str) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", title=f"{ticker} Price Channel")
        return fig

    def build_pullback_figure(frame: pd.DataFrame, ticker: str) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", title=f"{ticker} Pullback From ATH")
        return fig

    def build_forecast_cone_figure(history_frame: pd.DataFrame, forecast: dict[str, object], ticker: str) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", title=f"{ticker} Next-Week Probability Cone")
        return fig

    def build_terminal_distribution_figure(forecast: dict[str, object], ticker: str) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", title=f"{ticker} 5-Day Terminal Price Distribution")
        return fig


st.set_page_config(page_title="Spectral Nature - Alpaca + Streamlit", page_icon="chart_with_upwards_trend", layout="wide")

LOGGER = logging.getLogger("spectral_nature.streamlit_app")
if not LOGGER.handlers:
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    LOGGER.addHandler(stream_handler)

    file_handler = logging.FileHandler("/tmp/spectral_streamlit.log")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def _log_event(message: str, **fields: object) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    if details:
        LOGGER.info("%s | %s", message, details)
    else:
        LOGGER.info("%s", message)


@contextmanager
def _timed(step: str, **fields: object):
    started = time.perf_counter()
    _log_event(f"START {step}", **fields)
    try:
        yield
    except Exception as exc:
        elapsed = time.perf_counter() - started
        _log_event(f"ERROR {step}", elapsed_s=f"{elapsed:.3f}", error=type(exc).__name__)
        raise
    else:
        elapsed = time.perf_counter() - started
        _log_event(f"END {step}", elapsed_s=f"{elapsed:.3f}")



def _to_float(payload: dict, key: str) -> float:
    try:
        return float(payload.get(key, 0.0))
    except Exception:
        return 0.0



def _make_api(cfg: AppConfig) -> AlpacaAPI:
    return AlpacaAPI(cfg)


def _alpaca_cache_scope(cfg: AppConfig) -> str:
    trading_scope = cfg.alpaca_trading_base_url.replace("https://", "").replace("http://", "").replace("/", "_")
    data_scope = cfg.alpaca_data_base_url.replace("https://", "").replace("http://", "").replace("/", "_")
    account_scope = dataset_scope("acct", cfg.alpaca_api_key)
    return f"{trading_scope}__{data_scope}__{account_scope}"


def _fred_cache_scope(api_key: str) -> str:
    return dataset_scope("fred", api_key)


def _render_connection_issue(summary: str, *, details: str | None = None, setup_code: str | None = None) -> None:
    st.error(summary)
    if details:
        st.caption(details)
    if setup_code:
        st.code(setup_code, language="bash")


def _has_live_api(api: AlpacaAPI | None, message: str) -> bool:
    if api is not None:
        return True
    st.warning(message)
    return False


def _source_force_requested(source: str) -> bool:
    flags = st.session_state.get("_source_force_refresh", {})
    if not isinstance(flags, dict):
        return False
    return bool(flags.get(source, False))


def _load_pipeline_dataset(dataset_name: str) -> pd.DataFrame:
    frame, _ = load_latest_dataset_frame(dataset_name)
    if frame.empty:
        return frame
    return frame.reset_index(drop=True)


def _build_fred_dashboard_from_pipeline(
    summary: pd.DataFrame,
    observations: pd.DataFrame,
    years: int,
) -> dict[str, object]:
    summary_frame = summary.copy()
    obs_frame = observations.copy()

    if "date" in obs_frame.columns:
        obs_frame["date"] = pd.to_datetime(obs_frame["date"], errors="coerce")
        cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.DateOffset(years=max(int(years), 1))
        obs_frame = obs_frame[obs_frame["date"] >= cutoff].copy()
    if "value" in obs_frame.columns:
        obs_frame["value"] = pd.to_numeric(obs_frame["value"], errors="coerce")

    summary_frame["series_id"] = summary_frame.get("series_id", pd.Series(dtype=str)).astype(str)
    metadata_by_id: dict[str, dict[str, object]] = {}
    for _, row in summary_frame.iterrows():
        sid = str(row.get("series_id") or "").strip()
        if not sid:
            continue
        metadata_by_id[sid] = {
            "series_id": sid,
            "units_short": row.get("units_short"),
            "frequency_short": row.get("frequency_short"),
            "title": row.get("source_title") or row.get("indicator") or sid,
            "last_updated": row.get("last_updated"),
        }

    series_data: dict[str, pd.DataFrame] = {}
    if not obs_frame.empty and "series_id" in obs_frame.columns:
        for sid, frame in obs_frame.groupby("series_id", sort=False):
            series_data[str(sid)] = frame[[col for col in ["date", "value"] if col in frame.columns]].reset_index(drop=True)

    series_index_cols = [col for col in ["series_id", "indicator", "frequency_short", "units_short", "source_title"] if col in summary_frame.columns]
    series_index = summary_frame[series_index_cols].copy() if series_index_cols else pd.DataFrame()
    if "indicator" in series_index.columns:
        series_index = series_index.rename(columns={"indicator": "title"})

    release_index = pd.DataFrame()
    if "release_id" in obs_frame.columns:
        release_ids = pd.to_numeric(obs_frame["release_id"], errors="coerce").dropna().astype(int).drop_duplicates().sort_values()
        if not release_ids.empty:
            release_index = pd.DataFrame({"release_id": release_ids})

    return {
        "summary": summary_frame.reset_index(drop=True),
        "series_data": series_data,
        "metadata": metadata_by_id,
        "specs_by_category": fred_specs_by_category(),
        "category_blurbs": FRED_CATEGORY_BLURBS,
        "series_index": series_index.reset_index(drop=True),
        "observations": obs_frame.reset_index(drop=True),
        "release_index": release_index.reset_index(drop=True),
    }


def _load_account_cached(cfg: AppConfig, force_refresh: bool = False) -> dict[str, object]:
    return cached_scalar_dict(
        "account",
        _alpaca_cache_scope(cfg),
        lambda: _make_api(cfg).get_account(),
        force_refresh=force_refresh,
    )


def _load_positions_cached(cfg: AppConfig, force_refresh: bool = False) -> pd.DataFrame:
    return cached_frame(
        "positions",
        _alpaca_cache_scope(cfg),
        lambda: _make_api(cfg).get_positions(),
        force_refresh=force_refresh,
    )


def _load_timeseries_cached(cfg: AppConfig, period: str, force_refresh: bool = False) -> pd.DataFrame:
    return cached_frame(
        "portfolio_timeseries",
        f"{_alpaca_cache_scope(cfg)}__period_{period}",
        lambda: _build_timeseries(_make_api(cfg), period),
        force_refresh=force_refresh,
    )


def _load_holding_roc_cached(
    cfg: AppConfig,
    symbols: list[str],
    days: int = 365,
    force_refresh: bool = False,
) -> pd.DataFrame:
    normalized_symbols = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
    symbol_scope = dataset_scope("symbols", ",".join(normalized_symbols))
    return cached_frame(
        "holding_roc",
        f"{_alpaca_cache_scope(cfg)}__{symbol_scope}__{days}d",
        lambda: _compute_holding_roc(_make_api(cfg), normalized_symbols, days=days),
        force_refresh=force_refresh,
    )


def _scan_daily_movers_cached(
    cfg: AppConfig,
    symbols: list[str] | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    effective_force = force_refresh or _source_force_requested("equities")
    if not effective_force and pipeline_store_configured():
        pipeline = _load_pipeline_dataset("daily_movers")
        if not pipeline.empty:
            if symbols and "symbol" in pipeline.columns:
                allowed = {str(item).upper().strip() for item in symbols if str(item).strip()}
                pipeline = pipeline[pipeline["symbol"].astype(str).str.upper().isin(allowed)].copy()
            return pipeline.reset_index(drop=True)

    universe = symbols or DEFAULT_UNIVERSE
    symbol_scope = dataset_scope("market-universe", ",".join(sorted({str(symbol).upper() for symbol in universe})))
    return cached_frame(
        "daily_movers",
        f"{_alpaca_cache_scope(cfg)}__{symbol_scope}",
        lambda: scan_daily_movers(_make_api(cfg), symbols=universe),
        force_refresh=effective_force,
    )


def _scan_momentum_profiles_cached(
    cfg: AppConfig,
    days: int = 180,
    symbols: list[str] | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    effective_force = force_refresh or _source_force_requested("equities")
    if not effective_force and pipeline_store_configured():
        pipeline = _load_pipeline_dataset("momentum_profiles")
        if not pipeline.empty:
            if symbols and "symbol" in pipeline.columns:
                allowed = {str(item).upper().strip() for item in symbols if str(item).strip()}
                pipeline = pipeline[pipeline["symbol"].astype(str).str.upper().isin(allowed)].copy()
            return pipeline.reset_index(drop=True)

    universe = symbols or DEFAULT_UNIVERSE
    symbol_scope = dataset_scope("market-universe", ",".join(sorted({str(symbol).upper() for symbol in universe})))
    return cached_frame(
        "momentum_profiles",
        f"{_alpaca_cache_scope(cfg)}__{days}d__{symbol_scope}",
        lambda: scan_momentum_profiles(_make_api(cfg), symbols=universe, days=days),
        force_refresh=effective_force,
        version=5,
    )


def _load_correlation_phase_shift_cached(
    cfg: AppConfig,
    benchmark: str,
    days: int,
    corr_window: int,
    roc_window: int,
    momentum_window: int,
    symbols: list[str] | None = None,
    force_refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    effective_force = force_refresh or _source_force_requested("derivatives")
    if not effective_force and pipeline_store_configured():
        summary = _load_pipeline_dataset("correlation_phase_shift_summary")
        history = _load_pipeline_dataset("correlation_phase_shift_history")
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

            return {
                "summary": summary.reset_index(drop=True),
                "history": history.reset_index(drop=True),
            }

    universe = symbols or DEFAULT_UNIVERSE
    symbol_scope = dataset_scope("phase-shift-universe", ",".join(sorted({str(symbol).upper() for symbol in universe})))
    return cached_frame_dict(
        "correlation_phase_shift",
        (
            f"{_alpaca_cache_scope(cfg)}__{benchmark.upper()}__{days}d__corr_{corr_window}"
            f"__roc_{roc_window}__mom_{momentum_window}__{symbol_scope}"
        ),
        lambda: {
            key: value
            for key, value in scan_correlation_phase_shifts(
                _make_api(cfg),
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
        force_refresh=effective_force,
        version=3,
    )


def _load_commodity_regime_cached(
    cfg: AppConfig,
    commodity_symbols: list[str],
    days: int,
    corr_window: int,
    roc_window: int,
    momentum_window: int,
    symbols: list[str] | None = None,
    force_refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    effective_force = force_refresh or _source_force_requested("commodities")
    if not effective_force and pipeline_store_configured():
        summary = _load_pipeline_dataset("commodity_regime_summary")
        history = _load_pipeline_dataset("commodity_regime_history")
        if not summary.empty or not history.empty:
            return {
                "summary": summary.reset_index(drop=True),
                "history": history.reset_index(drop=True),
            }

    universe = symbols or DEFAULT_UNIVERSE
    symbol_scope = dataset_scope("commodity-universe", ",".join(sorted({str(symbol).upper() for symbol in universe})))
    commodity_scope = dataset_scope(
        "commodity-basket",
        ",".join(sorted({str(symbol).upper() for symbol in commodity_symbols})),
    )
    return cached_frame_dict(
        "commodity_regime",
        (
            f"{_alpaca_cache_scope(cfg)}__{days}d__corr_{corr_window}__roc_{roc_window}"
            f"__mom_{momentum_window}__{symbol_scope}__{commodity_scope}"
        ),
        lambda: {
            key: value
            for key, value in scan_commodity_regimes(
                _make_api(cfg),
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
        force_refresh=effective_force,
        version=2,
    )


def _load_price_history_cached(cfg: AppConfig, ticker: str, days: int, force_refresh: bool = False) -> pd.DataFrame:
    effective_force = force_refresh or _source_force_requested("equities")
    if not effective_force and pipeline_store_configured():
        pipeline = _load_pipeline_dataset("price_history")
        if not pipeline.empty and {"symbol", "timestamp"}.issubset(set(pipeline.columns)):
            out = pipeline[pipeline["symbol"].astype(str).str.upper() == ticker.upper()].copy()
            if not out.empty:
                out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
                out = out.dropna(subset=["timestamp"]).sort_values("timestamp")
                if days > 0:
                    cutoff = out["timestamp"].max() - pd.Timedelta(days=max(int(days), 1))
                    out = out[out["timestamp"] >= cutoff].copy()
                return out.reset_index(drop=True)

    return cached_frame(
        "price_history",
        f"{_alpaca_cache_scope(cfg)}__{ticker.upper()}__{days}d",
        lambda: load_price_history(_make_api(cfg), ticker, days=days),
        force_refresh=effective_force,
    )


def _load_technical_signal_history_cached(
    cfg: AppConfig,
    ticker: str,
    days: int,
    force_refresh: bool = False,
) -> pd.DataFrame:
    effective_force = force_refresh or _source_force_requested("derivatives")
    if not effective_force and pipeline_store_configured():
        pipeline = _load_pipeline_dataset("technical_signal_history")
        if not pipeline.empty and {"symbol", "timestamp"}.issubset(set(pipeline.columns)):
            out = pipeline[pipeline["symbol"].astype(str).str.upper() == ticker.upper()].copy()
            if not out.empty:
                out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
                out = out.dropna(subset=["timestamp"]).sort_values("timestamp")
                if days > 0:
                    cutoff = out["timestamp"].max() - pd.Timedelta(days=max(int(days), 1))
                    out = out[out["timestamp"] >= cutoff].copy()
                return out.reset_index(drop=True)

    base_days = max(int(days), 365)
    price = _load_price_history_cached(cfg, ticker, days=base_days, force_refresh=effective_force)
    if price.empty:
        return pd.DataFrame()
    frame = build_signal_frame(price)
    if frame.empty:
        return frame
    frame = frame.copy()
    frame["symbol"] = ticker.upper().strip()
    return frame.reset_index(drop=True)


def _load_technical_signal_summary_cached(
    cfg: AppConfig,
    ticker: str,
    signal_frame: pd.DataFrame,
    force_refresh: bool = False,
) -> dict[str, float | str]:
    effective_force = force_refresh or _source_force_requested("derivatives")
    if not effective_force and pipeline_store_configured():
        latest = _load_pipeline_dataset("technical_signals_latest")
        if not latest.empty and "symbol" in latest.columns:
            rows = latest[latest["symbol"].astype(str).str.upper() == ticker.upper()].copy()
            if not rows.empty:
                row = rows.iloc[0]
                return {
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

    if signal_frame.empty:
        return {}
    return summarize_signal_frame(signal_frame)


def _load_option_chain_cached(
    cfg: AppConfig,
    ticker: str,
    expiration: str | None = None,
    force_refresh: bool = False,
) -> tuple[list[str], pd.DataFrame, pd.DataFrame]:
    effective_force = force_refresh or _source_force_requested("options")
    if not effective_force and pipeline_store_configured():
        pipeline = _load_pipeline_dataset("option_contract_snapshots")
        if not pipeline.empty and {"symbol", "expiration", "type"}.issubset(set(pipeline.columns)):
            options = pipeline[pipeline["symbol"].astype(str).str.upper() == ticker.upper()].copy()
            if not options.empty:
                expirations = sorted(
                    {str(value) for value in options["expiration"].dropna().astype(str).tolist() if str(value).strip()}
                )
                if expirations:
                    if expiration is None:
                        return expirations, pd.DataFrame(), pd.DataFrame()

                    selected_expiration = expiration if expiration in expirations else expirations[0]
                    scoped = options[options["expiration"].astype(str) == selected_expiration].copy()
                    scoped = scoped.sort_values([col for col in ["strike", "contractSymbol"] if col in scoped.columns])
                    calls = scoped[scoped["type"].astype(str).str.lower() == "call"].copy()
                    puts = scoped[scoped["type"].astype(str).str.lower() == "put"].copy()
                    return expirations, calls.reset_index(drop=True), puts.reset_index(drop=True)

        if expiration is None:
            exp_only = _load_pipeline_dataset("option_expirations")
            if not exp_only.empty and {"symbol", "expiration"}.issubset(set(exp_only.columns)):
                options = exp_only[exp_only["symbol"].astype(str).str.upper() == ticker.upper()].copy()
                expirations = sorted(
                    {str(value) for value in options["expiration"].dropna().astype(str).tolist() if str(value).strip()}
                )
                if expirations:
                    return expirations, pd.DataFrame(), pd.DataFrame()

    expiration_scope = expiration or "expirations"
    return cached_option_chain(
        "option_chain",
        f"{_alpaca_cache_scope(cfg)}__{ticker.upper()}__{expiration_scope}",
        lambda: load_option_chain(_make_api(cfg), ticker, expiration),
        force_refresh=effective_force,
    )


def _load_option_surface_cached(
    cfg: AppConfig,
    ticker: str,
    expected_price: float,
    horizon_days: int,
    underlying_price: float,
    force_refresh: bool = False,
) -> pd.DataFrame:
    effective_force = force_refresh or _source_force_requested("options")
    if not effective_force and pipeline_store_configured():
        pipeline = _load_pipeline_dataset("option_contract_snapshots")
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
                    return windowed.reset_index(drop=True)

    return cached_frame(
        "option_surface",
        (
            f"{_alpaca_cache_scope(cfg)}__{ticker.upper()}__exp_{expected_price:.2f}"
            f"__h_{int(horizon_days)}__spot_{underlying_price:.2f}"
        ),
        lambda: load_option_surface(
            _make_api(cfg),
            ticker,
            underlying_price=underlying_price,
            expected_price=expected_price,
            horizon_days=horizon_days,
        ),
        force_refresh=effective_force,
    )


def _load_quarterly_fundamentals_cached(ticker: str, force_refresh: bool = False) -> dict[str, pd.DataFrame]:
    effective_force = force_refresh or _source_force_requested("fundamentals")
    if not effective_force and pipeline_store_configured():
        pipeline = _load_pipeline_dataset("quarterly_fundamentals")
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
                    return out

    return cached_frame_dict(
        "quarterly_fundamentals",
        ticker.upper(),
        lambda: load_quarterly_fundamentals(ticker),
        keys=["income", "balance", "cashflow"],
        force_refresh=effective_force,
    )


def _load_asset_metadata_cached(cfg: AppConfig, ticker: str, force_refresh: bool = False) -> dict[str, object]:
    return cached_scalar_dict(
        "asset_metadata",
        f"{_alpaca_cache_scope(cfg)}__{ticker.upper()}",
        lambda: load_asset_metadata(_make_api(cfg), ticker),
        force_refresh=force_refresh,
    )


def _load_recent_news_cached(
    cfg: AppConfig,
    ticker: str,
    days: int = 14,
    limit: int = 8,
    force_refresh: bool = False,
) -> dict[str, object]:
    effective_force = force_refresh or _source_force_requested("news")
    if not effective_force and pipeline_store_configured():
        pipeline = _load_pipeline_dataset("news_articles")
        if not pipeline.empty:
            target = ticker.upper().strip()

            def _has_symbol(value: object) -> bool:
                if isinstance(value, (list, tuple, set)):
                    vals = [str(item).upper().strip() for item in value]
                    return target in vals
                blob = str(value or "").replace("|", ",")
                vals = [item.upper().strip() for item in blob.split(",") if item.strip()]
                return target in vals

            rows = pipeline.copy()
            if "symbols" in rows.columns:
                rows = rows[rows["symbols"].apply(_has_symbol)].copy()
            if not rows.empty:
                if "published_at" in rows.columns:
                    rows["published_at"] = pd.to_datetime(rows["published_at"], utc=True, errors="coerce")
                    rows = rows.sort_values("published_at", ascending=False, na_position="last")
                rows = rows.head(limit).reset_index(drop=True)
                return {"articles": rows, "fallback_summary": None, "source": "pipeline"}

    return cached_news_payload(
        "recent_news",
        f"{_alpaca_cache_scope(cfg)}__{ticker.upper()}__{days}d__{limit}",
        lambda: load_recent_news(_make_api(cfg), ticker, days=days, limit=limit),
        force_refresh=effective_force,
    )


def _load_fred_dashboard_cached(api_key: str, years: int, force_refresh: bool = False) -> dict[str, object]:
    effective_force = force_refresh or _source_force_requested("fred")
    if not effective_force and pipeline_store_configured():
        summary = _load_pipeline_dataset("fred_summary")
        observations = _load_pipeline_dataset("fred_observations")
        if not summary.empty and not observations.empty:
            return _build_fred_dashboard_from_pipeline(summary, observations, years)

    return cached_fred_dashboard(
        "fred_dashboard",
        f"{_fred_cache_scope(api_key)}__{years}y",
        lambda: load_fred_dashboard(api_key, years=years),
        force_refresh=effective_force,
    )


def _prepare_scatter_size(df: pd.DataFrame, column: str) -> tuple[pd.DataFrame, str | None]:
    if df.empty or column not in df.columns:
        return df, None
    out = df.copy()
    size_col = f"{column}_plot_size"
    out[size_col] = pd.to_numeric(out[column], errors="coerce").fillna(0).clip(lower=0)
    if not out[size_col].gt(0).any():
        return out, None
    return out, size_col


def _dataframe_selected_rows(event: object) -> list[int]:
    selection = getattr(event, "selection", None)
    if selection is not None:
        rows = getattr(selection, "rows", None)
        if rows is not None:
            return list(rows)
        if isinstance(selection, dict):
            return list(selection.get("rows", []))
    if isinstance(event, dict):
        return list(event.get("selection", {}).get("rows", []))
    return []


def _render_selectable_ticker_table(
    title: str,
    df: pd.DataFrame,
    columns: list[str],
    key: str,
    column_config: dict[str, object] | None = None,
) -> str | None:
    st.subheader(title)
    if df.empty:
        st.info("No rows available.")
        return None

    table = df[columns].copy()
    st.caption("Click a row to open the ticker details below.")
    event = st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config=column_config,
        row_height=48 if "sparkline_3m" in table.columns else None,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    rows = _dataframe_selected_rows(event)
    if not rows:
        return None
    row_idx = rows[0]
    if row_idx >= len(table):
        return None
    return str(table.iloc[row_idx]["symbol"])


def _render_help_popover(title: str, body: str, label: str = "How to read") -> None:
    with st.popover(label, help=title, use_container_width=True):
        st.markdown(body)


def _load_symbol_name_map(
    cfg: AppConfig,
    symbols: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for symbol in sorted({str(value).upper().strip() for value in symbols if str(value).strip()}):
        try:
            asset = _load_asset_metadata_cached(cfg, symbol, force_refresh=force_refresh)
        except Exception:
            asset = {}
        name = str(asset.get("name") or "").strip()
        if name:
            out[symbol] = name
    return out


def _prepare_momentum_table(
    df: pd.DataFrame,
    *,
    name_map: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    table = df.copy()
    if "symbol" in table.columns:
        table["company_name"] = [
            str((name_map or {}).get(str(symbol).upper().strip()) or "")
            for symbol in table["symbol"]
        ]
    if "sparkline_3m" in table.columns:
        table["sparkline_3m"] = [
            list(value) if isinstance(value, (list, tuple, np.ndarray)) else []
            for value in table["sparkline_3m"]
        ]

    column_config: dict[str, object] = {
        "symbol": st.column_config.TextColumn("Ticker"),
    }
    if "company_name" in table.columns:
        column_config["company_name"] = st.column_config.TextColumn(
            "Company",
            help="Company name shown directly because Streamlit's native selectable dataframe does not support per-row hover tooltips reliably.",
            width="medium",
        )
    if "sparkline_3m" in table.columns:
        column_config["sparkline_3m"] = st.column_config.LineChartColumn(
            "Mini Chart",
            help="Normalized 3-month price path.",
            y_min=80,
            y_max=140,
            width="medium",
        )
    return table, column_config


def _build_rank_timeseries_figure(
    history: pd.DataFrame,
    rank_df: pd.DataFrame,
    title: str,
    *,
    days: int,
    value_col: str = "asset_norm",
) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(template="plotly_dark", title=title, xaxis_title="Date", yaxis_title="Normalized Price", hovermode="x unified")
    if history.empty or rank_df.empty or value_col not in history.columns:
        return fig

    symbols = [str(symbol).upper().strip() for symbol in rank_df.get("symbol", pd.Series(dtype=str)).tolist() if str(symbol).strip()]
    if not symbols:
        return fig

    frame = history[history["symbol"].astype(str).isin(symbols)].copy()
    if frame.empty:
        return fig

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp", value_col]).sort_values("timestamp")
    if frame.empty:
        return fig

    cutoff = frame["timestamp"].max() - pd.Timedelta(days=max(int(days), 30))
    frame = frame[frame["timestamp"] >= cutoff].copy()
    if frame.empty:
        return fig

    label_map: dict[str, str] = {}
    for row in rank_df.itertuples(index=False):
        symbol = str(getattr(row, "symbol", "")).upper().strip()
        name = str(getattr(row, "commodity_label", "") or getattr(row, "name", "") or symbol).strip()
        label_map[symbol] = f"{name} ({symbol})" if name and name != symbol else symbol

    for symbol in symbols:
        symbol_frame = frame[frame["symbol"].astype(str) == symbol].copy()
        if symbol_frame.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=symbol_frame["timestamp"],
                y=pd.to_numeric(symbol_frame[value_col], errors="coerce"),
                mode="lines",
                name=label_map.get(symbol, symbol),
            )
        )

    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0))
    return fig


def _sync_market_ticker_from_widget() -> None:
    ticker = st.session_state.get("market_ticker_detail_widget")
    if ticker:
        st.session_state["market_selected_ticker"] = ticker


def _daily_series(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    if df.empty or "timestamp" not in df.columns:
        return pd.DataFrame(columns=["timestamp", value_col])

    frame = df.copy()
    ts = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["timestamp"] = ts.dt.tz_convert("US/Eastern").dt.floor("D").dt.tz_localize(None)
    frame[value_col] = pd.to_numeric(frame[value_col], errors="coerce")
    frame = frame.dropna(subset=["timestamp", value_col])
    return frame.groupby("timestamp", as_index=False)[value_col].last().sort_values("timestamp")



def _build_timeseries(api: AlpacaAPI, period: str) -> pd.DataFrame:
    with _timed("portfolio_history", period=period):
        history = api.get_portfolio_history(period=period, timeframe="1D")
    if history.empty:
        _log_event("portfolio_history empty", period=period)
        return pd.DataFrame()

    portfolio = _daily_series(history, "equity").rename(columns={"equity": "portfolio"})
    start = history["timestamp"].min().to_pydatetime().replace(tzinfo=timezone.utc)
    with _timed("benchmark_bars", symbols=len(BENCHMARKS), period=period):
        bench_bars = api.get_stock_bars(BENCHMARKS, start=start, timeframe="1Day", feed="iex")

    merged = portfolio.copy()
    for symbol in BENCHMARKS:
        frame = bench_bars.get(symbol, pd.DataFrame())
        if frame.empty:
            continue
        col = symbol.replace("-", ".")
        daily = _daily_series(frame, "close").rename(columns={"close": col})
        merged = merged.merge(daily, on="timestamp", how="left")

    merged = merged.sort_values("timestamp")
    for col in [c for c in merged.columns if c != "timestamp"]:
        merged[col] = merged[col].ffill()
    out = merged.dropna(subset=["portfolio"])
    _log_event("timeseries_ready", rows=len(out), cols=len(out.columns), period=period)
    return out



def _build_normalized_view(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw

    out = raw.copy()
    for col in [c for c in out.columns if c != "timestamp"]:
        out[col] = normalize_to_100(out[col])
    return out



def _compute_holding_roc(api: AlpacaAPI, symbols: list[str], days: int = 365) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()

    with _timed("holdings_bars", symbols=len(symbols), days=days):
        bars = api.get_stock_bars(symbols, timeframe="1Day", feed="iex")
    rows = []
    windows = {"1d": 2, "1w": 5, "1m": 21, "3m": 63}

    for symbol in symbols:
        frame = bars.get(symbol, pd.DataFrame())
        if frame.empty or "close" not in frame.columns:
            continue

        s = pd.to_numeric(frame["close"], errors="coerce").dropna().tail(days)
        if len(s) < max(windows.values()):
            continue

        def slope(series: pd.Series, n: int) -> float:
            chunk = np.log(series.tail(n).to_numpy(dtype=float))
            x = np.arange(len(chunk), dtype=float)
            m, _ = np.polyfit(x, chunk, 1)
            return float(m)

        s1d = slope(s, windows["1d"])
        s1w = slope(s, windows["1w"])
        s1m = slope(s, windows["1m"])
        s3m = slope(s, windows["3m"])

        rows.append(
            {
                "symbol": symbol,
                "roc_1d_to_1w": (s1w / s1d - 1.0) if s1d else np.nan,
                "roc_1w_to_1m": (s1m / s1w - 1.0) if s1w else np.nan,
                "roc_1m_to_3m": (s3m / s1m - 1.0) if s1m else np.nan,
            }
        )

    out = pd.DataFrame(rows)
    _log_event("holding_roc_ready", rows=len(out), symbols=len(symbols))
    return out


def _render_market_opportunity_experiments(
    cfg: AppConfig,
    force_data_refresh: bool,
    advanced_view: str,
    lens_label: str,
    lens_name: str,
    lens_symbols: list[str],
) -> None:
    heading_cols = st.columns([10, 2])
    with heading_cols[0]:
        st.subheader("Advanced")
        st.caption("Less-standard scanners for regime shifts, structural leadership, and commodity transmission.")
        st.caption(f"{lens_label}: {lens_name} | {len(lens_symbols)} names")
    with heading_cols[1]:
        _render_help_popover(
            "How to read advanced views",
            """
Use this area when you want signals that are a little more structural than simple movers or momentum tables.

- `Broad Markets` looks for names breaking away from or re-linking with a market benchmark.
- `Commodity Section` is commodity-first and answers what is moving, in what direction, and how moves can transmit across the chain.
            """,
        )

    if advanced_view == "Commodity Section":
        _render_commodity_experiment(cfg, force_data_refresh, lens_name, lens_symbols)
        return

    _render_phase_shift_experiment(cfg, force_data_refresh, lens_name, lens_symbols)


def _render_phase_shift_experiment(
    cfg: AppConfig,
    force_data_refresh: bool,
    business_filter: str,
    business_symbols: list[str],
) -> None:
    heading_cols = st.columns([10, 2])
    with heading_cols[0]:
        st.markdown("##### Broad Markets")
        st.caption(
            "Correlation phase-shift analyzer: searches for changes in rolling market correlation versus multi-horizon compounding "
            "momentum to surface decoupling leaders, beta-linked breakouts, and crowded unwinds."
        )
        st.caption(f"Business lens: {business_filter}")
    with heading_cols[1]:
        _render_help_popover(
            "How to read this experiment",
            """
**Plain-English version**

- `Correlation` means how tightly a stock is moving with the benchmark, like `SPY`.
- `RoC` means `rate of change`, which is just how fast a signal is changing.
- `Compounding momentum` means returns are stacking across short and medium windows, not just drifting up a little.

**What this experiment is trying to find**

- `Decoupling leaders`: strong momentum while correlation to the market is falling.
- `Beta-linked breakouts`: strong momentum while correlation to the market is still rising or staying high.
- `Crowded unwinds / washouts`: weak momentum with unstable or rising correlation stress.
            """,
        )

    control_cols = st.columns(5)
    with control_cols[0]:
        benchmark = st.selectbox(
            "Benchmark",
            ["SPY", "QQQ", "DIA", "IWM", "XLK", "XLF", "XLE", "TLT"],
            index=0,
            key="market_experiment_benchmark",
        )
    with control_cols[1]:
        experiment_days = st.slider("History (days)", 126, 504, 252, step=21, key="market_experiment_days")
    with control_cols[2]:
        corr_window = st.slider("Corr Window", 10, 60, 20, step=5, key="market_experiment_corr_window")
    with control_cols[3]:
        roc_window = st.slider("Corr RoC", 5, 30, 10, step=1, key="market_experiment_roc_window")
    with control_cols[4]:
        momentum_window = st.slider("Comp Momentum", 21, 126, 63, step=21, key="market_experiment_momentum_window")

    try:
        with st.spinner("Scanning correlation phase shifts..."):
            with _timed(
                "scan_correlation_phase_shifts",
                benchmark=benchmark,
                days=experiment_days,
                corr_window=corr_window,
                roc_window=roc_window,
                momentum_window=momentum_window,
            ):
                experiment_data = _load_correlation_phase_shift_cached(
                    cfg,
                    benchmark=benchmark,
                    days=experiment_days,
                    corr_window=corr_window,
                    roc_window=roc_window,
                    momentum_window=momentum_window,
                    symbols=business_symbols,
                    force_refresh=force_data_refresh,
                )
    except AlpacaAPIError as exc:
        _log_event("scan_correlation_phase_shifts_failed", benchmark=benchmark, error=str(exc)[:200])
        st.error(f"Could not run the phase-shift analyzer: {exc}")
        return

    summary = experiment_data.get("summary", pd.DataFrame())
    history = experiment_data.get("history", pd.DataFrame())
    if summary.empty or history.empty:
        st.info("Not enough market history was returned to run the experiment.")
        return

    selected_experiment_ticker: str | None = None
    decoupling = summary.nlargest(10, "decoupling_score")[
        [
            "symbol",
            "close",
            "correlation_now",
            "correlation_roc",
            "compounding_momentum_pct",
            "momentum_roc_pct",
            "decoupling_score",
            "phase_regime",
        ]
    ]
    beta_breakouts = summary.nlargest(10, "beta_breakout_score")[
        [
            "symbol",
            "close",
            "correlation_now",
            "correlation_roc",
            "compounding_momentum_pct",
            "momentum_roc_pct",
            "beta_breakout_score",
            "phase_regime",
        ]
    ]
    correlation_breaks = summary.nlargest(10, "correlation_break_score")[
        [
            "symbol",
            "close",
            "correlation_now",
            "correlation_roc",
            "compounding_momentum_pct",
            "momentum_roc_pct",
            "correlation_break_score",
            "phase_regime",
        ]
    ]

    table_left, table_right = st.columns(2)
    with table_left:
        selected_experiment_ticker = _render_selectable_ticker_table(
            "Decoupling Leaders",
            decoupling,
            ["symbol", "correlation_now", "correlation_roc", "compounding_momentum_pct", "decoupling_score", "phase_regime"],
            key="market_experiment_decoupling",
        ) or selected_experiment_ticker
    with table_right:
        selected_experiment_ticker = _render_selectable_ticker_table(
            "Beta-Linked Breakouts",
            beta_breakouts,
            ["symbol", "correlation_now", "correlation_roc", "compounding_momentum_pct", "beta_breakout_score", "phase_regime"],
            key="market_experiment_beta_breakouts",
        ) or selected_experiment_ticker

    selected_experiment_ticker = _render_selectable_ticker_table(
        "Correlation Break Alerts",
        correlation_breaks,
        ["symbol", "correlation_now", "correlation_roc", "momentum_roc_pct", "correlation_break_score", "phase_regime"],
        key="market_experiment_correlation_breaks",
    ) or selected_experiment_ticker

    scatter_frame = summary.copy()
    scatter_frame["corr_break_abs"] = pd.to_numeric(scatter_frame["correlation_break_score"], errors="coerce").abs()
    scatter_frame, phase_size_col = _prepare_scatter_size(scatter_frame, "corr_break_abs")

    chart_left, chart_right = st.columns(2)
    with chart_left:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Phase Shift Map",
                """
This is the quickest chart to read.

- `X axis`: how fast correlation is changing.
- `Y axis`: how strong compounded momentum is.
- upper-left: strong momentum, but becoming less tied to the benchmark
- upper-right: strong momentum, still moving with the benchmark
- lower half: weak or fading momentum

The further a ticker is from the center, the more unusual the regime shift is.
                """,
            )
        fig_phase = px.scatter(
            scatter_frame,
            x="correlation_roc",
            y="compounding_momentum_pct",
            color="phase_regime",
            size=phase_size_col,
            hover_name="symbol",
            hover_data={
                "correlation_now": ":.2f",
                "momentum_roc_pct": ":.2f",
                "decoupling_score": ":.1f",
                "beta_breakout_score": ":.1f",
            },
            template="plotly_dark",
            title=f"Phase Shift Map vs {benchmark}",
            labels={
                "correlation_roc": "Correlation RoC",
                "compounding_momentum_pct": "Compounding Momentum %",
            },
        )
        fig_phase.add_hline(y=0, line_dash="dot", line_color="#666")
        fig_phase.add_vline(x=0, line_dash="dot", line_color="#666")
        st.plotly_chart(fig_phase, use_container_width=True)

    with chart_right:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Correlation Phase Surface",
                """
This is the 3D version of the phase map.

- `Current Corr`: how linked the stock is to the benchmark right now
- `Corr RoC`: whether that link is tightening or breaking
- `Comp Momentum %`: whether returns are reinforcing across time windows

Use this when you want to compare several names at once and see which ones are both strong and changing regime.
                """,
            )
        fig_phase_3d = px.scatter_3d(
            scatter_frame,
            x="correlation_now",
            y="correlation_roc",
            z="compounding_momentum_pct",
            color="decoupling_score",
            size=phase_size_col,
            hover_name="symbol",
            hover_data={
                "phase_regime": True,
                "momentum_roc_pct": ":.2f",
                "beta_breakout_score": ":.1f",
                "correlation_break_score": ":.1f",
            },
            template="plotly_dark",
            title=f"Correlation Phase Surface vs {benchmark}",
            labels={
                "correlation_now": "Current Corr",
                "correlation_roc": "Corr RoC",
                "compounding_momentum_pct": "Comp Momentum %",
                "decoupling_score": "Decoupling Score",
            },
        )
        fig_phase_3d.update_traces(marker=dict(opacity=0.8))
        st.plotly_chart(fig_phase_3d, use_container_width=True)

    experiment_symbol_options = sorted(summary["symbol"].astype(str).unique().tolist())
    exp_selected_key = "market_experiment_selected_ticker"
    exp_widget_key = "market_experiment_ticker_widget"
    fallback_experiment_ticker = st.session_state.get(exp_selected_key) or experiment_symbol_options[0]
    if fallback_experiment_ticker not in experiment_symbol_options:
        fallback_experiment_ticker = experiment_symbol_options[0]

    current_experiment_ticker = st.session_state.get(exp_selected_key)
    if current_experiment_ticker not in experiment_symbol_options:
        current_experiment_ticker = fallback_experiment_ticker

    if selected_experiment_ticker and selected_experiment_ticker in experiment_symbol_options:
        if selected_experiment_ticker != current_experiment_ticker:
            st.session_state[exp_selected_key] = selected_experiment_ticker
            st.session_state[exp_widget_key] = selected_experiment_ticker
            st.rerun()
    elif exp_selected_key not in st.session_state or st.session_state[exp_selected_key] not in experiment_symbol_options:
        st.session_state[exp_selected_key] = fallback_experiment_ticker

    if exp_widget_key not in st.session_state or st.session_state[exp_widget_key] not in experiment_symbol_options:
        st.session_state[exp_widget_key] = st.session_state[exp_selected_key]
    elif st.session_state[exp_widget_key] != st.session_state[exp_selected_key]:
        st.session_state[exp_widget_key] = st.session_state[exp_selected_key]

    experiment_ticker = st.selectbox(
        "Experiment Detail",
        experiment_symbol_options,
        key=exp_widget_key,
        on_change=lambda: st.session_state.__setitem__(exp_selected_key, st.session_state.get(exp_widget_key)),
    )
    st.session_state[exp_selected_key] = experiment_ticker

    detail_row = summary[summary["symbol"] == experiment_ticker].head(1)
    detail_history = history[history["symbol"] == experiment_ticker].copy()
    if detail_row.empty or detail_history.empty:
        st.info("No detail history available for the selected experiment ticker.")
        return

    detail = detail_row.iloc[0]
    metric_cols = st.columns(6)
    with metric_cols[0]:
        st.metric("Phase Regime", str(detail.get("phase_regime") or "n/a"))
    with metric_cols[1]:
        st.metric("Current Corr", f"{pd.to_numeric(detail.get('correlation_now'), errors='coerce'):.2f}")
    with metric_cols[2]:
        st.metric("Corr RoC", f"{pd.to_numeric(detail.get('correlation_roc'), errors='coerce'):.2f}")
    with metric_cols[3]:
        st.metric("Comp Momentum", f"{pd.to_numeric(detail.get('compounding_momentum_pct'), errors='coerce'):.1f}%")
    with metric_cols[4]:
        st.metric("Momentum RoC", f"{pd.to_numeric(detail.get('momentum_roc_pct'), errors='coerce'):.1f}%")
    with metric_cols[5]:
        st.metric("Decoupling Score", f"{pd.to_numeric(detail.get('decoupling_score'), errors='coerce'):.1f}")

    detail_history = detail_history.sort_values("timestamp").copy()
    visible_cutoff = detail_history["timestamp"].max() - pd.Timedelta(days=min(experiment_days, 180))
    visible_history = detail_history[detail_history["timestamp"] >= visible_cutoff].copy()
    if visible_history.empty:
        visible_history = detail_history.copy()

    detail_chart_left, detail_chart_right = st.columns(2)
    with detail_chart_left:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Relative Path",
                """
This compares the selected stock against the benchmark on the same starting scale.

- if the stock line is rising faster than the benchmark line, it is outperforming
- if the gap is widening, leadership is strengthening
- if the stock starts to outperform while correlation is falling, that usually points to a real phase shift instead of simple market beta
                """,
            )
        fig_relative = go.Figure()
        fig_relative.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["asset_norm"],
                mode="lines",
                name=experiment_ticker,
            )
        )
        fig_relative.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["benchmark_norm"],
                mode="lines",
                name=benchmark,
            )
        )
        fig_relative.update_layout(
            template="plotly_dark",
            title=f"{experiment_ticker} vs {benchmark} Relative Path",
            xaxis_title="Date",
            yaxis_title="Normalized Price",
            hovermode="x unified",
        )
        st.plotly_chart(fig_relative, use_container_width=True)

    with detail_chart_right:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Correlation Regime",
                """
This shows the market linkage directly.

- `Rolling Corr`: the current relationship to the benchmark
- `Corr RoC`: the speed and direction of the change in that relationship

If `Rolling Corr` is still high but `Corr RoC` is dropping, the stock may be starting to break away from the market.
                """,
            )
        fig_corr = go.Figure()
        fig_corr.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["rolling_correlation"],
                mode="lines",
                name="Rolling Corr",
            )
        )
        fig_corr.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["correlation_roc"],
                mode="lines",
                name="Corr RoC",
            )
        )
        fig_corr.update_layout(
            template="plotly_dark",
            title=f"{experiment_ticker} Correlation Regime vs {benchmark}",
            xaxis_title="Date",
            yaxis_title="Correlation",
            hovermode="x unified",
        )
        st.plotly_chart(fig_corr, use_container_width=True)

    momentum_help_cols = st.columns([10, 2])
    with momentum_help_cols[1]:
        _render_help_popover(
            "Compounding Momentum Phase",
            """
This is not just raw price momentum.

- `Comp Momentum %`: returns compounded across multiple windows
- `Momentum RoC %`: whether that compounded momentum is accelerating or fading

Easy interpretation:

- above zero and rising: trend is strengthening
- above zero but falling: still strong, but losing speed
- below zero: the move is weakening or reversing
            """,
        )
    fig_momentum = go.Figure()
    fig_momentum.add_trace(
        go.Scatter(
            x=visible_history["timestamp"],
            y=visible_history["compounding_momentum"] * 100.0,
            mode="lines",
            name="Comp Momentum %",
        )
    )
    fig_momentum.add_trace(
        go.Scatter(
            x=visible_history["timestamp"],
            y=visible_history["momentum_roc"] * 100.0,
            mode="lines",
            name="Momentum RoC %",
        )
    )
    fig_momentum.update_layout(
        template="plotly_dark",
        title=f"{experiment_ticker} Compounding Momentum Phase",
        xaxis_title="Date",
        yaxis_title="Percent",
        hovermode="x unified",
    )
    st.plotly_chart(fig_momentum, use_container_width=True)


def _render_commodity_experiment(
    cfg: AppConfig,
    force_data_refresh: bool,
    commodity_focus: str,
    commodity_symbols: list[str],
) -> None:
    heading_cols = st.columns([10, 2])
    with heading_cols[0]:
        st.markdown("##### Commodity Section")
        st.caption(
            "Commodity-first view that answers what is moving, in what direction, and how those moves can transmit across upstream and downstream links."
        )
        st.caption(f"Commodity filter: {commodity_focus}")
    with heading_cols[1]:
        _render_help_popover(
            "How to read this section",
            """
**Plain-English version**

- `Direction` tells you whether a commodity is rising, falling, or changing pace.
- `Relative strength` tells you whether it is outperforming the broad commodity basket.
- `Dependency graph` shows where one commodity often transmits into another through energy inputs, fertilizer costs, electrification demand, or soft-commodity spillovers.

**What this section is trying to answer**

- Which commodities are moving up or down right now?
- Which moves are accelerating versus cooling off?
- Which commodities may be pressuring or feeding into other commodities?
            """,
        )

    control_cols = st.columns(4)
    with control_cols[0]:
        experiment_days = st.slider("History (days)", 126, 504, 252, step=21, key="market_commodity_days")
    with control_cols[1]:
        corr_window = st.slider("Beta Window", 10, 60, 20, step=5, key="market_commodity_corr_window")
    with control_cols[2]:
        roc_window = st.slider("Beta RoC", 5, 30, 10, step=1, key="market_commodity_roc_window")
    with control_cols[3]:
        momentum_window = st.slider("Transmission Window", 21, 126, 63, step=21, key="market_commodity_momentum_window")

    st.caption(commodity_focus_description(commodity_focus))
    reference_symbols = commodity_reference_universe()
    st.caption(
        "Broad commodity reference basket: "
        + ", ".join(f"{commodity_proxy_profile(symbol)['commodity']} ({symbol})" for symbol in reference_symbols)
    )
    with st.expander("Commodity Lens Constituents", expanded=False):
        lens_profiles = pd.DataFrame([commodity_proxy_profile(symbol) for symbol in commodity_symbols])
        st.dataframe(
            lens_profiles[["symbol", "name", "commodity", "description"]],
            use_container_width=True,
            hide_index=True,
        )

    try:
        with st.spinner("Scanning commodity market structure..."):
            with _timed(
                "scan_commodity_regimes",
                basket=commodity_focus,
                days=experiment_days,
                corr_window=corr_window,
                roc_window=roc_window,
                momentum_window=momentum_window,
            ):
                experiment_data = _load_commodity_regime_cached(
                    cfg,
                    commodity_symbols=reference_symbols,
                    days=experiment_days,
                    corr_window=corr_window,
                    roc_window=roc_window,
                    momentum_window=momentum_window,
                    symbols=commodity_symbols,
                    force_refresh=force_data_refresh,
                )
                momentum = _scan_momentum_profiles_cached(
                    cfg,
                    experiment_days,
                    symbols=commodity_symbols,
                    force_refresh=force_data_refresh,
                )
    except AlpacaAPIError as exc:
        _log_event("scan_commodity_regimes_failed", basket=commodity_focus, error=str(exc)[:200])
        st.error(f"Could not run the commodity analyzer: {exc}")
        return

    summary = experiment_data.get("summary", pd.DataFrame())
    history = experiment_data.get("history", pd.DataFrame())
    if summary.empty or history.empty or momentum.empty:
        st.info("Not enough commodity history was returned to build this section.")
        return

    summary = summary.merge(
        momentum[
            [
                "symbol",
                "daily_change_pct",
                "return_1w_pct",
                "return_1m_pct",
                "return_3m_pct",
                "momentum_score",
                "momentum_roc_score",
                "trend_r2_3m",
                "trend_fit_gap",
            ]
        ],
        on="symbol",
        how="left",
    )
    for column in ["name", "commodity_label", "description"]:
        if column not in summary.columns:
            summary[column] = None

    for row_idx, symbol in summary["symbol"].astype(str).items():
        profile = commodity_proxy_profile(symbol)
        if pd.isna(summary.at[row_idx, "name"]) or not str(summary.at[row_idx, "name"]).strip():
            summary.at[row_idx, "name"] = profile["name"]
        if pd.isna(summary.at[row_idx, "commodity_label"]) or not str(summary.at[row_idx, "commodity_label"]).strip():
            summary.at[row_idx, "commodity_label"] = profile["commodity"]
        if pd.isna(summary.at[row_idx, "description"]) or not str(summary.at[row_idx, "description"]).strip():
            summary.at[row_idx, "description"] = profile["description"]

    def commodity_direction_label(row: pd.Series) -> str:
        return_1m = pd.to_numeric(row.get("return_1m_pct"), errors="coerce")
        roc = pd.to_numeric(row.get("momentum_roc_score"), errors="coerce")
        daily = pd.to_numeric(row.get("daily_change_pct"), errors="coerce")
        if pd.notna(return_1m) and return_1m >= 0 and pd.notna(roc) and roc >= 0:
            return "Up and accelerating"
        if pd.notna(return_1m) and return_1m >= 0:
            return "Up but cooling"
        if pd.notna(return_1m) and return_1m < 0 and pd.notna(roc) and roc < 0:
            return "Down and worsening"
        if pd.notna(return_1m) and return_1m < 0:
            return "Down but stabilizing"
        if pd.notna(daily) and daily >= 0:
            return "Positive turn"
        return "Mixed"

    summary["direction_label"] = summary.apply(commodity_direction_label, axis=1)
    summary["trend_consistency_pct"] = pd.to_numeric(summary["trend_r2_3m"], errors="coerce") * 100.0
    summary["pullback_abs"] = pd.to_numeric(summary["pullback_from_high_pct"], errors="coerce").abs()

    breadth_positive = int(pd.to_numeric(summary["daily_change_pct"], errors="coerce").gt(0).sum())
    breadth_negative = int(pd.to_numeric(summary["daily_change_pct"], errors="coerce").lt(0).sum())
    leader = summary.sort_values("return_1m_pct", ascending=False, na_position="last").head(1)
    laggard = summary.sort_values("return_1m_pct", ascending=True, na_position="last").head(1)
    accelerator = summary.sort_values("momentum_roc_score", ascending=False, na_position="last").head(1)

    metric_cols = st.columns(4)
    with metric_cols[0]:
        if not leader.empty:
            st.metric(
                "1M Leader",
                str(leader.iloc[0]["commodity_label"]),
                f"{pd.to_numeric(leader.iloc[0]['return_1m_pct'], errors='coerce'):.1f}%",
            )
    with metric_cols[1]:
        if not laggard.empty:
            st.metric(
                "1M Laggard",
                str(laggard.iloc[0]["commodity_label"]),
                f"{pd.to_numeric(laggard.iloc[0]['return_1m_pct'], errors='coerce'):.1f}%",
            )
    with metric_cols[2]:
        st.metric("Daily Breadth", f"{breadth_positive} up / {breadth_negative} down")
    with metric_cols[3]:
        if not accelerator.empty:
            st.metric(
                "Fastest Rotation",
                str(accelerator.iloc[0]["commodity_label"]),
                f"{pd.to_numeric(accelerator.iloc[0]['momentum_roc_score'], errors='coerce'):.2f}",
            )

    selected_commodity_ticker: str | None = None
    moving_up = summary[pd.to_numeric(summary["return_1m_pct"], errors="coerce") > 0].sort_values(
        ["return_1m_pct", "daily_change_pct"],
        ascending=[False, False],
        na_position="last",
    ).head(10)[
        [
            "symbol",
            "name",
            "commodity_label",
            "daily_change_pct",
            "return_1w_pct",
            "return_1m_pct",
            "direction_label",
        ]
    ]
    moving_down = summary[pd.to_numeric(summary["return_1m_pct"], errors="coerce") < 0].sort_values(
        ["return_1m_pct", "daily_change_pct"],
        ascending=[True, True],
        na_position="last",
    ).head(10)[
        [
            "symbol",
            "name",
            "commodity_label",
            "daily_change_pct",
            "return_1w_pct",
            "return_1m_pct",
            "direction_label",
        ]
    ]
    consistent_trends = summary.sort_values(["trend_fit_gap", "return_1m_pct"], ascending=[True, False], na_position="last").head(10)[
        [
            "symbol",
            "name",
            "commodity_label",
            "trend_consistency_pct",
            "pullback_from_high_pct",
            "relative_strength_pct",
        ]
    ]

    table_left, table_right = st.columns(2)
    with table_left:
        selected_commodity_ticker = _render_selectable_ticker_table(
            "Moving Up",
            moving_up,
            ["symbol", "name", "commodity_label", "daily_change_pct", "return_1w_pct", "return_1m_pct"],
            key="market_commodity_beneficiaries",
        ) or selected_commodity_ticker
    with table_right:
        selected_commodity_ticker = _render_selectable_ticker_table(
            "Moving Down",
            moving_down,
            ["symbol", "name", "commodity_label", "daily_change_pct", "return_1w_pct", "return_1m_pct"],
            key="market_commodity_squeezes",
        ) or selected_commodity_ticker

    selected_commodity_ticker = _render_selectable_ticker_table(
        "Most Consistent Trends",
        consistent_trends,
        ["symbol", "name", "commodity_label", "trend_consistency_pct", "pullback_from_high_pct", "relative_strength_pct"],
        key="market_commodity_decouplers",
    ) or selected_commodity_ticker

    series_left, series_right = st.columns(2)
    with series_left:
        if moving_up.empty:
            st.info("No commodities are currently in the `up` bucket for this filter.")
        else:
            st.plotly_chart(
                _build_rank_timeseries_figure(
                    history,
                    moving_up,
                    f"Moving Up Time Series: {commodity_focus}",
                    days=experiment_days,
                ),
                use_container_width=True,
            )
    with series_right:
        if moving_down.empty:
            st.info("No commodities are currently in the `down` bucket for this filter.")
        else:
            st.plotly_chart(
                _build_rank_timeseries_figure(
                    history,
                    moving_down,
                    f"Moving Down Time Series: {commodity_focus}",
                    days=experiment_days,
                ),
                use_container_width=True,
            )

    if consistent_trends.empty:
        st.info("No consistent trend series were available for this commodity filter.")
    else:
        st.plotly_chart(
            _build_rank_timeseries_figure(
                history,
                consistent_trends,
                f"Most Consistent Trend Time Series: {commodity_focus}",
                days=experiment_days,
            ),
            use_container_width=True,
        )

    heatmap_frame = summary.sort_values("return_1m_pct", ascending=False, na_position="last").reset_index(drop=True)
    heatmap_values = heatmap_frame[
        ["daily_change_pct", "return_1w_pct", "return_1m_pct", "return_3m_pct", "relative_strength_pct"]
    ].apply(pd.to_numeric, errors="coerce")
    heatmap_labels = [f"{row['commodity_label']} ({row['symbol']})" for _, row in heatmap_frame.iterrows()]

    scatter_frame = summary.copy()
    scatter_frame["rotation_abs"] = pd.to_numeric(scatter_frame["momentum_roc_score"], errors="coerce").abs()
    scatter_frame, commodity_size_col = _prepare_scatter_size(scatter_frame, "rotation_abs")

    chart_left, chart_right = st.columns(2)
    with chart_left:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Direction Heatmap",
                """
This is the fastest answer to "what is moving and in what direction?"

- rows are commodities in the selected filter
- columns are return horizons
- green means rising, red means falling
- the further from zero, the stronger the move
                """,
            )
        fig_heatmap = go.Figure(
            data=go.Heatmap(
                z=heatmap_values.to_numpy(dtype=float),
                x=["1D %", "1W %", "1M %", "3M %", "Rel Strength %"],
                y=heatmap_labels,
                colorscale="RdYlGn",
                zmid=0,
                colorbar=dict(title="%"),
                hovertemplate="%{y}<br>%{x}: %{z:.2f}%<extra></extra>",
            )
        )
        fig_heatmap.update_layout(template="plotly_dark", title=f"Commodity Direction Heatmap: {commodity_focus}")
        st.plotly_chart(fig_heatmap, use_container_width=True)

    with chart_right:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Leadership vs Pullback",
                """
This separates strong leaders from weak laggards.

- `X axis`: relative strength versus the broad commodity basket
- `Y axis`: 1-month direction of travel
- `Marker size`: change in momentum pace
- higher and further right usually means stronger commodity leadership
                """,
            )
        fig_commodity = px.scatter(
            scatter_frame,
            x="relative_strength_pct",
            y="return_1m_pct",
            color="direction_label",
            size=commodity_size_col,
            hover_name="name",
            hover_data={
                "symbol": True,
                "commodity_label": True,
                "daily_change_pct": ":.2f",
                "return_3m_pct": ":.2f",
                "transmission_gap_pct": ":.2f",
                "pullback_from_high_pct": ":.2f",
            },
            template="plotly_dark",
            title=f"Commodity Leadership Map: {commodity_focus}",
            labels={
                "relative_strength_pct": "Relative Strength vs Broad Basket %",
                "return_1m_pct": "1M Return %",
            },
        )
        fig_commodity.add_hline(y=0, line_dash="dot", line_color="#666")
        fig_commodity.add_vline(x=0, line_dash="dot", line_color="#666")
        st.plotly_chart(fig_commodity, use_container_width=True)

    dependency_edges = commodity_dependency_graph(commodity_symbols)
    if not dependency_edges.empty:
        sankey_left, sankey_right = st.columns([2.2, 1.2])
        with sankey_left:
            chart_help_cols = st.columns([10, 2])
            with chart_help_cols[1]:
                _render_help_popover(
                    "Commodity Dependency Graph",
                    """
This graph shows common transmission paths.

- links point from likely upstream pressure into likely downstream reaction
- thicker links represent stronger expected transmission
- node color reflects current 1-month direction of the commodity
                    """,
                )

            value_map = summary.set_index("symbol")["return_1m_pct"].to_dict()

            def _node_color(value: float) -> str:
                if not np.isfinite(value):
                    return "#888888"
                if value >= 8:
                    return "#1f9d55"
                if value > 0:
                    return "#7bc96f"
                if value <= -8:
                    return "#c23b22"
                if value < 0:
                    return "#f28b82"
                return "#888888"

            def _link_color(value: float) -> str:
                if not np.isfinite(value):
                    return "rgba(160,160,160,0.35)"
                if value >= 0:
                    return "rgba(31,157,85,0.35)"
                return "rgba(194,59,34,0.35)"

            nodes = list(dict.fromkeys(dependency_edges["source"].tolist() + dependency_edges["target"].tolist()))
            node_index = {symbol: idx for idx, symbol in enumerate(nodes)}
            node_labels = [f"{commodity_proxy_profile(symbol)['commodity']} ({symbol})" for symbol in nodes]
            node_colors = [_node_color(pd.to_numeric(value_map.get(symbol), errors="coerce")) for symbol in nodes]
            link_colors = [_link_color(pd.to_numeric(value_map.get(symbol), errors="coerce")) for symbol in dependency_edges["source"]]
            link_labels = [
                f"{row.source_commodity} -> {row.target_commodity}<br>{row.relation}: {row.description}"
                for row in dependency_edges.itertuples(index=False)
            ]

            fig_sankey = go.Figure(
                go.Sankey(
                    arrangement="snap",
                    node=dict(label=node_labels, color=node_colors, pad=18, thickness=18),
                    link=dict(
                        source=[node_index[symbol] for symbol in dependency_edges["source"]],
                        target=[node_index[symbol] for symbol in dependency_edges["target"]],
                        value=dependency_edges["weight"].astype(float).tolist(),
                        color=link_colors,
                        label=link_labels,
                        hovertemplate="%{label}<extra></extra>",
                    ),
                )
            )
            fig_sankey.update_layout(template="plotly_dark", title=f"Commodity Dependency Graph: {commodity_focus}")
            st.plotly_chart(fig_sankey, use_container_width=True)

        with sankey_right:
            edge_view = dependency_edges[
                ["source_commodity", "target_commodity", "relation", "weight"]
            ].rename(
                columns={
                    "source_commodity": "Upstream",
                    "target_commodity": "Downstream",
                    "relation": "Link",
                    "weight": "Weight",
                }
            )
            st.subheader("Key Links")
            st.dataframe(edge_view, use_container_width=True, hide_index=True)

    commodity_symbol_options = sorted(summary["symbol"].astype(str).unique().tolist())
    commodity_selected_key = "market_commodity_selected_ticker"
    commodity_widget_key = "market_commodity_ticker_widget"
    fallback_commodity_ticker = st.session_state.get(commodity_selected_key) or commodity_symbol_options[0]
    if fallback_commodity_ticker not in commodity_symbol_options:
        fallback_commodity_ticker = commodity_symbol_options[0]

    current_commodity_ticker = st.session_state.get(commodity_selected_key)
    if current_commodity_ticker not in commodity_symbol_options:
        current_commodity_ticker = fallback_commodity_ticker

    if selected_commodity_ticker and selected_commodity_ticker in commodity_symbol_options:
        if selected_commodity_ticker != current_commodity_ticker:
            st.session_state[commodity_selected_key] = selected_commodity_ticker
            st.session_state[commodity_widget_key] = selected_commodity_ticker
            st.rerun()
    elif commodity_selected_key not in st.session_state or st.session_state[commodity_selected_key] not in commodity_symbol_options:
        st.session_state[commodity_selected_key] = fallback_commodity_ticker

    if commodity_widget_key not in st.session_state or st.session_state[commodity_widget_key] not in commodity_symbol_options:
        st.session_state[commodity_widget_key] = st.session_state[commodity_selected_key]
    elif st.session_state[commodity_widget_key] != st.session_state[commodity_selected_key]:
        st.session_state[commodity_widget_key] = st.session_state[commodity_selected_key]

    commodity_ticker = st.selectbox(
        "Commodity Detail",
        commodity_symbol_options,
        key=commodity_widget_key,
        on_change=lambda: st.session_state.__setitem__(commodity_selected_key, st.session_state.get(commodity_widget_key)),
    )
    st.session_state[commodity_selected_key] = commodity_ticker

    detail_row = summary[summary["symbol"] == commodity_ticker].head(1)
    detail_history = history[history["symbol"] == commodity_ticker].copy()
    if detail_row.empty or detail_history.empty:
        st.info("No detail history available for the selected commodity ticker.")
        return

    detail = detail_row.iloc[0]
    commodity_name = str(detail.get("name") or commodity_proxy_profile(commodity_ticker)["name"])
    commodity_description = str(detail.get("description") or commodity_proxy_profile(commodity_ticker)["description"])
    commodity_label = str(detail.get("commodity_label") or commodity_proxy_profile(commodity_ticker)["commodity"])
    st.subheader(f"{commodity_name} ({commodity_ticker})")
    st.write(commodity_description)
    st.caption(f"Commodity type: {commodity_label} | Filter: {commodity_focus}")

    metric_cols = st.columns(6)
    with metric_cols[0]:
        st.metric("Direction", str(detail.get("direction_label") or "n/a"))
    with metric_cols[1]:
        st.metric("1D Move", f"{pd.to_numeric(detail.get('daily_change_pct'), errors='coerce'):.1f}%")
    with metric_cols[2]:
        st.metric("1W Move", f"{pd.to_numeric(detail.get('return_1w_pct'), errors='coerce'):.1f}%")
    with metric_cols[3]:
        st.metric("1M Move", f"{pd.to_numeric(detail.get('return_1m_pct'), errors='coerce'):.1f}%")
    with metric_cols[4]:
        st.metric("Relative Strength", f"{pd.to_numeric(detail.get('relative_strength_pct'), errors='coerce'):.1f}%")
    with metric_cols[5]:
        st.metric("Pullback vs High", f"{pd.to_numeric(detail.get('pullback_from_high_pct'), errors='coerce'):.1f}%")

    detail_history = detail_history.sort_values("timestamp").copy()
    visible_cutoff = detail_history["timestamp"].max() - pd.Timedelta(days=min(experiment_days, 180))
    visible_history = detail_history[detail_history["timestamp"] >= visible_cutoff].copy()
    if visible_history.empty:
        visible_history = detail_history.copy()

    detail_chart_left, detail_chart_right = st.columns(2)
    with detail_chart_left:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Relative Path vs Commodity Basket",
                """
This compares the selected commodity against the broad commodity basket from the same starting scale.

- if the commodity line rises faster, it is leading the broad market
- if the basket rises faster, leadership is broad and this commodity is lagging
- a widening gap often matters more than the absolute beta reading
                """,
            )
        fig_relative = go.Figure()
        fig_relative.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["asset_norm"],
                mode="lines",
                name=commodity_ticker,
            )
        )
        fig_relative.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["commodity_norm"],
                mode="lines",
                name="Broad Commodity Basket",
            )
        )
        fig_relative.update_layout(
            template="plotly_dark",
            title=f"{commodity_name} vs Broad Commodity Basket",
            xaxis_title="Date",
            yaxis_title="Normalized Price",
            hovermode="x unified",
        )
        st.plotly_chart(fig_relative, use_container_width=True)

    with detail_chart_right:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Return Ladder",
                """
This condenses the move across horizons into one chart.

- left of zero means the commodity is still under pressure
- right of zero means it is moving higher
- compare short and medium horizons to see whether the move is strengthening or fading
                """,
            )
        return_ladder = pd.DataFrame(
            {
                "horizon": ["1D", "1W", "1M", "3M", "Rel Strength"],
                "value": [
                    pd.to_numeric(detail.get("daily_change_pct"), errors="coerce"),
                    pd.to_numeric(detail.get("return_1w_pct"), errors="coerce"),
                    pd.to_numeric(detail.get("return_1m_pct"), errors="coerce"),
                    pd.to_numeric(detail.get("return_3m_pct"), errors="coerce"),
                    pd.to_numeric(detail.get("relative_strength_pct"), errors="coerce"),
                ],
            }
        )
        fig_ladder = px.bar(
            return_ladder,
            x="value",
            y="horizon",
            orientation="h",
            color="value",
            color_continuous_scale="RdYlGn",
            template="plotly_dark",
            title=f"{commodity_name} Return Ladder",
            labels={"value": "Percent", "horizon": ""},
        )
        fig_ladder.add_vline(x=0, line_dash="dot", line_color="#666")
        st.plotly_chart(fig_ladder, use_container_width=True)

    transmission_help_cols = st.columns([10, 2])
    with transmission_help_cols[1]:
        _render_help_popover(
            "Trend Structure",
            """
This compares the commodity's trend strength against the broad basket and its pullback from a recent high.

- `Commodity Comp Momentum %`: the selected commodity's own stacked return impulse
- `Broad Basket Momentum %`: the same idea for the broad commodity market
- `Pullback %`: how stretched or washed out the commodity is versus its recent high

Strong momentum with a shallow pullback usually signals leadership. Weak momentum with a deep pullback usually signals stress.
            """,
        )
    fig_transmission = go.Figure()
    fig_transmission.add_trace(
        go.Scatter(
            x=visible_history["timestamp"],
            y=visible_history["asset_compounding_momentum"] * 100.0,
            mode="lines",
            name="Commodity Comp Momentum %",
        )
    )
    fig_transmission.add_trace(
        go.Scatter(
            x=visible_history["timestamp"],
            y=visible_history["commodity_compounding_momentum"] * 100.0,
            mode="lines",
            name="Broad Basket Momentum %",
        )
    )
    fig_transmission.add_trace(
        go.Scatter(
            x=visible_history["timestamp"],
            y=visible_history["pullback_from_high"] * 100.0,
            mode="lines",
            name="Pullback vs High %",
        )
    )
    fig_transmission.update_layout(
        template="plotly_dark",
        title=f"{commodity_name} Trend and Pullback",
        xaxis_title="Date",
        yaxis_title="Percent",
        hovermode="x unified",
    )
    st.plotly_chart(fig_transmission, use_container_width=True)

    related_edges = dependency_edges[
        (dependency_edges["source"] == commodity_ticker) | (dependency_edges["target"] == commodity_ticker)
    ].copy() if not dependency_edges.empty else pd.DataFrame()
    st.subheader("Dependency Context")
    if related_edges.empty:
        st.info("No curated upstream/downstream dependency links are defined for this commodity in the current filter.")
    else:
        related_edges["flow"] = [
            f"{row.source_commodity} -> {row.target_commodity}" for row in related_edges.itertuples(index=False)
        ]
        st.dataframe(
            related_edges[["flow", "relation", "description", "weight"]],
            use_container_width=True,
            hide_index=True,
        )


with _timed("load_config"):
    cfg = load_config()
api: AlpacaAPI | None = None
account: dict[str, object] = {}
startup_error_summary: str | None = None
startup_error_details: str | None = None
startup_setup_code: str | None = None

if cfg is None:
    _log_event("config_invalid")
    key_raw = (os.getenv("APCA_API_KEY") or os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY") or "").strip()
    secret_raw = (os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY") or "").strip()
    startup_setup_code = (
        "export APCA_API_KEY='...'\n"
        "export APCA_API_SECRET_KEY='...'\n"
        "export APCA_API_BASE_URL='https://paper-api.alpaca.markets'\n"
        "streamlit run app.py"
    )

    if key_raw.lower() == "your_key_here" or (secret_raw and secret_raw.lower() == "your_secret_here"):
        startup_error_summary = (
            "Alpaca credentials are incomplete: placeholder value detected. Replace your_key_here and your_secret_here."
        )
    else:
        missing = []
        if not (os.getenv("APCA_API_KEY_ID") or os.getenv("APCA_API_KEY") or os.getenv("ALPACA_API_KEY")):
            missing.append("APCA_API_KEY (or APCA_API_KEY_ID)")
        if not (os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")):
            missing.append("APCA_API_SECRET_KEY")

        if missing:
            startup_error_summary = "Alpaca credentials are missing or incomplete."
            startup_error_details = "Missing: " + ", ".join(missing)
        else:
            startup_error_summary = (
                "Alpaca credentials are missing or incomplete. Set APCA_API_KEY and APCA_API_SECRET_KEY."
            )

elif cfg.alpaca_trading_base_url.startswith("hhttps://") or not cfg.alpaca_trading_base_url.startswith(("http://", "https://")):
    _log_event("config_invalid_base_url", base_url=cfg.alpaca_trading_base_url)
    startup_error_summary = (
        "Invalid APCA_API_BASE_URL value. Expected https://paper-api.alpaca.markets or https://api.alpaca.markets."
    )
    startup_setup_code = "APCA_API_BASE_URL=https://api.alpaca.markets"

elif cfg.alpaca_api_key.strip().lower() in {"your_key_here", ""}:
    _log_event("config_placeholder_key")
    startup_error_summary = "APCA_API_KEY is still a placeholder. Set your real Alpaca API key."

elif cfg.alpaca_secret_key.strip().lower() == "your_secret_here":
    _log_event("config_placeholder_secret")
    startup_error_summary = "APCA_API_SECRET_KEY is a placeholder. Set your real Alpaca API secret key."

else:
    with _timed("create_api_client"):
        api = _make_api(cfg)

st.sidebar.title("Spectral Nature")
st.sidebar.caption("Alpaca + Streamlit")
_log_event("ui_sidebar_ready")

section = st.sidebar.radio(
    "Section",
    [
        "Home",
        "Portfolio Overview",
        "Performance",
        "FRED Macro",
        "Market Opportunity",
        "Technical Strategizer",
        "Option Strategizer",
        "Fundamental Strategizer",
    ],
)

period = st.sidebar.selectbox("History Period", ["1M", "3M", "6M", "1Y", "2Y", "5Y"], index=3)
_log_event("section_selected", section=section, period=period)
force_data_refresh = st.sidebar.button(
    "Update Cached Data",
    help="Refreshes the CSV cache for the data loaded in this run, even if the cache is not stale yet.",
)
if force_data_refresh:
    _log_event("cache_refresh_requested", section=section)
    st.sidebar.success("Refreshing requested data for this run.")

source_refresh_flags: dict[str, bool] = {}
source_labels = {
    "equities": "Equities",
    "fred": "FRED",
    "commodities": "Commodities",
    "options": "Options",
    "news": "News",
    "fundamentals": "Fundamentals",
    "derivatives": "Derivatives",
}
st.sidebar.markdown("### Source Controls")
st.sidebar.caption("Dashboard reads pipeline snapshots by default. Use buttons below to trigger source-specific refresh jobs.")
for source_key, source_label in source_labels.items():
    clicked = st.sidebar.button(
        f"Force Refresh {source_label}",
        key=f"source_refresh_{source_key}",
        help=f"Triggers the `{source_label}` pipeline job and bypasses local cache logic for this run.",
    )
    source_refresh_flags[source_key] = bool(clicked)
    if clicked:
        ok, msg = start_source_refresh_job(source_key)
        if ok:
            st.sidebar.success(msg)
        else:
            st.sidebar.warning(msg)

st.session_state["_source_force_refresh"] = source_refresh_flags
if pipeline_store_configured():
    st.sidebar.caption("Data mode: Pipeline metadata + parquet snapshots")
else:
    st.sidebar.caption("Data mode: Live API fallback (pipeline config not detected)")

st.sidebar.caption(f"CSV cache: {cache_data_root()}")
st.sidebar.caption(f"Cache policy: {cache_policy_path()}")

st.sidebar.markdown("---")
sidebar_connection = st.sidebar.empty()
sidebar_status = st.sidebar.empty()
sidebar_buying_power = st.sidebar.empty()
sidebar_connection.metric("Connection", "Configured" if api is not None else "Unavailable")
sidebar_status.metric("Account Status", "NOT LOADED" if api is not None else "UNAVAILABLE")
sidebar_buying_power.metric("Buying Power", "Not loaded" if api is not None else "Unavailable")

if startup_error_summary:
    _render_connection_issue(
        startup_error_summary,
        details=startup_error_details,
        setup_code=startup_setup_code,
    )

if section == "Home":
    st.title("Spectral Nature")
    st.write("Choose a section from the sidebar. Data-heavy views load on demand now, so the app shell renders first.")
    if api is None:
        st.info("Fix the Alpaca configuration to enable portfolio, market, technical, and options views. FRED Macro uses its own API key.")
    else:
        st.success("Alpaca configuration loaded. Open a section when you want live data.")

elif section == "Portfolio Overview":
    st.title("Portfolio Overview")
    if not _has_live_api(api, "Portfolio Overview requires a working Alpaca connection."):
        st.info("Fix the Alpaca connection to load positions, portfolio history, and benchmark comparisons.")
    else:
        try:
            with st.spinner("Loading account summary..."):
                with _timed("get_account"):
                    account = _load_account_cached(cfg, force_refresh=force_data_refresh)
            sidebar_status.metric("Account Status", str(account.get("status", "unknown")).upper())
            sidebar_buying_power.metric("Buying Power", f"${_to_float(account, 'buying_power'):,.2f}")
        except AlpacaAPIError as exc:
            _log_event("get_account_failed", error=str(exc)[:200])
            st.warning(f"Could not load account summary: {exc}")
            account = {}
            sidebar_status.metric("Account Status", "ERROR")
            sidebar_buying_power.metric("Buying Power", "Unavailable")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Equity", f"${_to_float(account, 'equity'):,.2f}")
        col2.metric("Cash", f"${_to_float(account, 'cash'):,.2f}")
        col3.metric("Portfolio Value", f"${_to_float(account, 'portfolio_value'):,.2f}")
        col4.metric("Daytrade Count", str(account.get("daytrade_count", "0")))

        try:
            with st.spinner("Loading positions..."):
                with _timed("get_positions"):
                    positions = _load_positions_cached(cfg, force_refresh=force_data_refresh)
        except AlpacaAPIError as exc:
            _log_event("get_positions_failed", error=str(exc)[:200])
            st.warning(f"Could not load positions: {exc}")
            positions = pd.DataFrame()

        left, right = st.columns([1.2, 1.8])

        with left:
            st.subheader("Current Positions")
            if positions.empty:
                st.info("No open positions.")
            else:
                show_cols = [
                    c
                    for c in [
                        "symbol",
                        "qty",
                        "avg_entry_price",
                        "current_price",
                        "market_value",
                        "unrealized_pl",
                        "unrealized_plpc",
                        "change_today",
                    ]
                    if c in positions.columns
                ]
                st.dataframe(positions[show_cols], use_container_width=True, hide_index=True)

                if "market_value" in positions.columns and "symbol" in positions.columns:
                    pie = px.pie(
                        positions,
                        values="market_value",
                        names="symbol",
                        title="Position Weights",
                        template="plotly_dark",
                    )
                    st.plotly_chart(pie, use_container_width=True)

        with right:
            st.subheader("Portfolio vs Benchmarks")
            try:
                with st.spinner("Loading portfolio history and benchmarks..."):
                    with _timed("build_timeseries", period=period):
                        raw = _load_timeseries_cached(cfg, period, force_refresh=force_data_refresh)
                norm = _build_normalized_view(raw)
                if norm.empty:
                    st.info("No portfolio history available.")
                else:
                    fig = build_portfolio_vs_benchmarks_fig(norm)
                    st.plotly_chart(fig, use_container_width=True)
            except AlpacaAPIError as exc:
                _log_event("build_timeseries_failed", error=str(exc)[:200])
                st.warning(f"Could not load portfolio history: {exc}")

        st.subheader("RoC Momentum (Holdings Snapshot)")
        if positions.empty or "symbol" not in positions.columns:
            st.info("No holdings available for momentum view.")
        else:
            symbols = positions["symbol"].astype(str).str.upper().tolist()[:12]
            try:
                with _timed("compute_holding_roc", symbols=len(symbols)):
                    roc = _load_holding_roc_cached(cfg, symbols, force_refresh=force_data_refresh)
                if roc.empty:
                    st.info("Not enough bar history to compute momentum.")
                else:
                    long = roc.melt(id_vars=["symbol"], var_name="metric", value_name="value")
                    fig = px.bar(
                        long,
                        x="symbol",
                        y="value",
                        color="metric",
                        barmode="group",
                        template="plotly_dark",
                        title="Slope Rate-of-Change by Holding",
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(roc, use_container_width=True, hide_index=True)
            except AlpacaAPIError as exc:
                _log_event("compute_holding_roc_failed", error=str(exc)[:200])
                st.warning(f"Could not compute momentum: {exc}")

elif section == "Performance":
    st.title("Performance")
    if not _has_live_api(api, "Performance requires a working Alpaca connection."):
        st.info("Fix the Alpaca connection to compute portfolio and benchmark performance.")
    else:
        try:
            with st.spinner("Loading performance data..."):
                with _timed("performance_build_timeseries", period=period):
                    raw = _load_timeseries_cached(cfg, period, force_refresh=force_data_refresh)
            norm = _build_normalized_view(raw)
        except AlpacaAPIError as exc:
            _log_event("performance_timeseries_failed", error=str(exc)[:200])
            st.error(f"Could not fetch timeseries: {exc}")
            st.stop()

        if raw.empty:
            st.info("No performance data available.")
            st.stop()

        perf = performance_table(norm)
        if perf.empty:
            st.info("Not enough data to compute metrics.")
        else:
            st.dataframe(perf, use_container_width=True, hide_index=True)

            metric = st.selectbox(
                "Metric",
                ["annual_return", "sharpe_ratio", "beta_vs_spy", "alpha_vs_spy", "max_drawdown"],
            )
            st.plotly_chart(build_metric_bar(perf, metric), use_container_width=True)

elif section == "FRED Macro":
    st.title("FRED Macro")
    st.caption(
        "Economic indicators sourced from FRED. Observations are loaded from FRED v2 bulk release downloads, "
        "then filtered interactively in-app."
    )

    fred_api_key = load_fred_api_key()
    if not fred_api_key:
        st.info(
            "The macro dashboard looks for Azure Key Vault secret `Fred` in `spectral-nature-kvault` first, "
            "then falls back to `FRED_API_KEY`."
        )
        st.code(
            "export AZURE_KEY_VAULT_NAME='spectral-nature-kvault'\n"
            "# or fallback:\n"
            "export FRED_API_KEY='...'\n"
            "streamlit run app.py",
            language="bash",
        )
    else:
        fred_control_cols = st.columns([2, 1])
        with fred_control_cols[0]:
            lookback_years = st.slider("Lookback (years)", 3, 20, 10, step=1)
        with fred_control_cols[1]:
            show_stationary_overlay = st.checkbox(
                "Overlay stationarized change",
                value=False,
                help="Adds an obs-to-obs transformed series on a secondary axis. Level series use percent change; rate-like series use first differences.",
            )
        fred_cache_key = f"{_fred_cache_scope(fred_api_key)}__{lookback_years}y"
        fred_cache_ready = cache_bundle_exists(
            "fred_dashboard",
            fred_cache_key,
            required_files=["summary.csv", "observations.csv"],
        )
        load_fred_now = st.button(
            "Load FRED Data",
            type="primary" if not fred_cache_ready else "secondary",
            help="Cold bulk downloads can take a while on remote sessions. Cached data loads immediately.",
        )
        if not fred_cache_ready and not load_fred_now and not force_data_refresh:
            st.info(
                "FRED bulk downloads are deferred until requested. This prevents the app from appearing to hang on "
                "a cold remote load. Click `Load FRED Data` once, or use cached data on the next visit."
            )
            st.stop()
        try:
            with st.spinner("Loading FRED macro dashboard..."):
                with _timed("load_fred_dashboard", years=lookback_years):
                    dashboard = _load_fred_dashboard_cached(
                        fred_api_key,
                        lookback_years,
                        force_refresh=(force_data_refresh or load_fred_now),
                    )
        except FredAPIError as exc:
            _log_event("load_fred_dashboard_failed", error=str(exc)[:200], years=lookback_years)
            st.error(f"Could not load FRED data: {exc}")
            st.stop()

        summary = dashboard["summary"].copy()
        category_blurbs = dashboard["category_blurbs"]
        specs_by_category = dashboard["specs_by_category"]
        metadata_by_id = dashboard["metadata"]
        series_data = dashboard["series_data"]
        series_index = dashboard.get("series_index", pd.DataFrame())
        observations = dashboard.get("observations", pd.DataFrame())
        release_index = dashboard.get("release_index", pd.DataFrame())

        if summary.empty:
            st.info("No macro indicators were returned from FRED.")
            st.stop()

        if not series_index.empty:
            release_count = int(release_index["release_id"].nunique()) if not release_index.empty else 0
            series_count = int(series_index["series_id"].nunique())
            bulk_cols = st.columns(3)
            with bulk_cols[0]:
                st.metric("Loaded Releases", f"{release_count}")
            with bulk_cols[1]:
                st.metric("Loaded Series", f"{series_count}")
            with bulk_cols[2]:
                st.metric("Curated Indicators", f"{len(summary)}")
            st.caption(
                "FRED v2 bulk is release-scoped rather than full-catalog. This dashboard loads the releases needed "
                "for inflation, labor, housing, credit distress, and money-supply analysis."
            )

        overview = summary.copy()
        overview["latest"] = [
            format_fred_value(value, units)
            for value, units in zip(overview["latest_value"], overview["units_short"])
        ]
        overview["prev"] = [
            format_fred_delta(value, units)
            for value, units in zip(overview["prev_delta"], overview["units_short"])
        ]
        overview["yoy"] = [
            format_fred_delta(value, units)
            for value, units in zip(overview["yoy_delta"], overview["units_short"])
        ]
        overview["latest_date"] = pd.to_datetime(overview["latest_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        st.subheader("Indicator Snapshot")
        st.dataframe(
            overview[["category", "indicator", "latest", "prev", "yoy", "latest_date"]],
            use_container_width=True,
            hide_index=True,
        )

        if not series_index.empty and not observations.empty:
            st.subheader("Series Explorer")
            explorer_cols = st.columns([2, 1])
            with explorer_cols[0]:
                search_query = st.text_input(
                    "Search loaded series",
                    key="fred_series_search",
                    placeholder="cpi, mortgage, delinquency, money stock",
                ).strip()
            with explorer_cols[1]:
                release_options = sorted(series_index["release_name"].dropna().astype(str).unique().tolist())
                selected_release_names = st.multiselect(
                    "Filter releases",
                    release_options,
                    key="fred_release_filter",
                )

            filtered_series = series_index.copy()
            if selected_release_names:
                filtered_series = filtered_series[filtered_series["release_name"].isin(selected_release_names)]
            if search_query:
                search_mask = (
                    filtered_series["series_id"].astype(str).str.contains(search_query, case=False, na=False)
                    | filtered_series["title"].astype(str).str.contains(search_query, case=False, na=False)
                    | filtered_series["notes"].astype(str).str.contains(search_query, case=False, na=False)
                )
                filtered_series = filtered_series[search_mask]

            filtered_series = filtered_series.sort_values(["release_name", "title", "series_id"], na_position="last")
            st.dataframe(
                filtered_series[["release_name", "series_id", "title", "frequency", "units"]].head(250),
                use_container_width=True,
                hide_index=True,
            )

            if not filtered_series.empty:
                option_rows = filtered_series[["series_id", "title"]].drop_duplicates().copy()
                option_labels = option_rows.apply(
                    lambda row: f"{row['series_id']} | {row['title']}",
                    axis=1,
                ).tolist()
                label_by_series = {label.split(" | ", 1)[0]: label for label in option_labels}
                selected_series_id = st.session_state.get("fred_explorer_series_id")
                if selected_series_id not in label_by_series:
                    selected_series_id = option_rows.iloc[0]["series_id"]
                selected_label = st.selectbox(
                    "Explorer series",
                    option_labels,
                    index=option_labels.index(label_by_series[selected_series_id]),
                    key="fred_explorer_series_label",
                )
                selected_series_id = selected_label.split(" | ", 1)[0]
                st.session_state["fred_explorer_series_id"] = selected_series_id

                selected_meta = metadata_by_id.get(selected_series_id, {})
                selected_frame = observations[observations["series_id"] == selected_series_id][["date", "value"]].copy()
                selected_spec = FredSeriesSpec(
                    "Explorer",
                    selected_series_id,
                    str(selected_meta.get("title") or selected_series_id),
                    "",
                )
                selected_summary = build_fred_series_summary(selected_spec, selected_meta, selected_frame)

                explorer_metric_cols = st.columns(3)
                with explorer_metric_cols[0]:
                    st.metric(
                        "Latest",
                        format_fred_value(selected_summary.get("latest_value"), selected_meta.get("units_short")),
                        format_fred_delta(selected_summary.get("prev_delta"), selected_meta.get("units_short")),
                    )
                with explorer_metric_cols[1]:
                    st.metric(
                        "YoY",
                        format_fred_delta(selected_summary.get("yoy_delta"), selected_meta.get("units_short")),
                    )
                with explorer_metric_cols[2]:
                    last_obs = pd.to_datetime(selected_summary.get("latest_date"), errors="coerce")
                    st.metric("Last Obs", last_obs.strftime("%Y-%m-%d") if pd.notna(last_obs) else "n/a")

                selected_release_name = str(selected_meta.get("release_name") or "n/a")
                selected_frequency = str(selected_meta.get("frequency_short") or selected_meta.get("frequency") or "")
                st.caption(
                    f"{selected_release_name} | {selected_frequency} | Units: {selected_meta.get('units_short') or selected_meta.get('units') or 'n/a'}"
                )
                selected_notes = str(selected_meta.get("notes") or "").strip()
                if selected_notes:
                    st.caption(selected_notes[:600] + ("..." if len(selected_notes) > 600 else ""))
                st.plotly_chart(
                    build_fred_figure(
                        selected_spec,
                        selected_meta,
                        selected_frame,
                        show_stationary_overlay=show_stationary_overlay,
                    ),
                    use_container_width=True,
                )

        tabs = st.tabs(fred_categories())
        for tab, category in zip(tabs, fred_categories()):
            with tab:
                st.caption(category_blurbs.get(category, ""))
                category_summary = summary[summary["category"] == category].copy()
                if category_summary.empty:
                    st.info("No indicators available for this category.")
                    continue

                table = category_summary.copy()
                table["latest"] = [
                    format_fred_value(value, units)
                    for value, units in zip(table["latest_value"], table["units_short"])
                ]
                table["prev"] = [
                    format_fred_delta(value, units)
                    for value, units in zip(table["prev_delta"], table["units_short"])
                ]
                table["yoy"] = [
                    format_fred_delta(value, units)
                    for value, units in zip(table["yoy_delta"], table["units_short"])
                ]
                table["latest_date"] = pd.to_datetime(table["latest_date"], errors="coerce").dt.strftime("%Y-%m-%d")
                st.dataframe(
                    table[["indicator", "latest", "prev", "yoy", "latest_date"]],
                    use_container_width=True,
                    hide_index=True,
                )

                chart_cols = st.columns(2)
                for idx, spec in enumerate(specs_by_category.get(category, [])):
                    with chart_cols[idx % 2]:
                        row = category_summary[category_summary["series_id"] == spec.series_id]
                        meta = metadata_by_id.get(spec.series_id, {})
                        frame = series_data.get(spec.series_id, pd.DataFrame())
                        latest_value = row["latest_value"].iloc[0] if not row.empty else None
                        prev_delta = row["prev_delta"].iloc[0] if not row.empty else None
                        yoy_delta = row["yoy_delta"].iloc[0] if not row.empty else None
                        latest_date = pd.to_datetime(row["latest_date"].iloc[0], errors="coerce") if not row.empty else pd.NaT

                        st.metric(
                            spec.label,
                            format_fred_value(latest_value, meta.get("units_short")),
                            format_fred_delta(prev_delta, meta.get("units_short")),
                        )
                        date_label = latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else "n/a"
                        st.caption(
                            f"{spec.blurb} | YoY: {format_fred_delta(yoy_delta, meta.get('units_short'))} | "
                            f"{meta.get('frequency_short', '')} | Last obs: {date_label}"
                        )
                        st.plotly_chart(
                            build_fred_figure(spec, meta, frame, show_stationary_overlay=show_stationary_overlay),
                            use_container_width=True,
                        )

elif section == "Market Opportunity":
    st.title("Market Opportunity")
    if not _has_live_api(api, "Market Opportunity requires a working Alpaca connection."):
        st.info("Fix the Alpaca connection to scan movers and load price history.")
    else:
        market_subpage = st.radio(
            "Market Opportunity View",
            ["Markets", "Advanced"],
            horizontal=True,
            key="market_opportunity_subpage",
        )
        if market_subpage == "Advanced":
            advanced_view = st.radio(
                "Advanced View",
                ["Broad Markets", "Commodity Section"],
                horizontal=True,
                key="market_experiment_view",
            )

            lens_cols = st.columns([2.2, 3.8])
            if advanced_view == "Commodity Section":
                with lens_cols[0]:
                    experiment_filter = st.selectbox(
                        "Commodity Filter",
                        commodity_focus_options(),
                        index=0,
                        key="market_commodity_focus",
                    )
                with lens_cols[1]:
                    st.caption("Commodity-market lens for the experiment view.")
                    st.caption(commodity_focus_description(experiment_filter))
                experiment_symbols = commodity_focus_universe(experiment_filter)
                _render_market_opportunity_experiments(
                    cfg,
                    force_data_refresh,
                    advanced_view,
                    "Commodity Filter",
                    experiment_filter,
                    experiment_symbols,
                )
            else:
                with lens_cols[0]:
                    experiment_filter = st.selectbox(
                        "Business Filter",
                        business_focus_options(),
                        index=0,
                        key="market_business_filter",
                    )
                with lens_cols[1]:
                    st.caption(
                        "Custom business lens based on what the company primarily sells, not standard sector classifications."
                    )
                    st.caption(business_focus_description(experiment_filter))
                experiment_symbols = business_focus_universe(experiment_filter)
                _render_market_opportunity_experiments(
                    cfg,
                    force_data_refresh,
                    advanced_view,
                    "Business Filter",
                    experiment_filter,
                    experiment_symbols,
                )
            st.stop()

        lens_cols = st.columns([2.2, 3.8])
        with lens_cols[0]:
            business_filter = st.selectbox(
                "Business Filter",
                business_focus_options(),
                index=0,
                key="market_business_filter",
            )
        with lens_cols[1]:
            st.caption(
                "Custom business lens based on what the company primarily sells, not standard sector classifications."
            )
            st.caption(business_focus_description(business_filter))
        business_symbols = business_focus_universe(business_filter)

        momentum_days = st.slider("Momentum Lookback (days)", 90, 365, 180, step=30)

        try:
            with st.spinner("Scanning market movers..."):
                with _timed("scan_daily_movers"):
                    movers = _scan_daily_movers_cached(
                        cfg,
                        symbols=business_symbols,
                        force_refresh=force_data_refresh,
                    )
        except AlpacaAPIError as exc:
            _log_event("scan_daily_movers_failed", error=str(exc)[:200])
            st.warning(f"Could not scan movers: {exc}")
            movers = pd.DataFrame()

        selected_market_ticker: str | None = None

        try:
            with st.spinner("Scanning momentum profiles..."):
                with _timed("scan_momentum_profiles", days=momentum_days):
                    momentum = _scan_momentum_profiles_cached(
                        cfg,
                        momentum_days,
                        symbols=business_symbols,
                        force_refresh=force_data_refresh,
                    )
        except AlpacaAPIError as exc:
            _log_event("scan_momentum_profiles_failed", error=str(exc)[:200], days=momentum_days)
            st.warning(f"Could not scan momentum profiles: {exc}")
            momentum = pd.DataFrame()

        if not momentum.empty:
            raw_up = momentum.nlargest(20, "momentum_score")[
                ["symbol", "sparkline_3m", "close", "daily_change_pct", "return_1m_pct", "return_3m_pct", "momentum_score"]
            ]
            raw_down = momentum.nsmallest(20, "momentum_score")[
                ["symbol", "sparkline_3m", "close", "daily_change_pct", "return_1m_pct", "return_3m_pct", "momentum_score"]
            ]
            roc_up = momentum.nlargest(20, "momentum_roc_score")[
                ["symbol", "sparkline_3m", "close", "return_1w_pct", "return_1m_pct", "roc_1m_to_3m", "momentum_roc_score"]
            ]
            roc_down = momentum.nsmallest(20, "momentum_roc_score")[
                ["symbol", "sparkline_3m", "close", "return_1w_pct", "return_1m_pct", "roc_1m_to_3m", "momentum_roc_score"]
            ]

            st.markdown("##### Momentum Raw")
            visible_market_symbols = sorted(
                {
                    str(symbol).upper().strip()
                    for table in (raw_up, raw_down, roc_up, roc_down)
                    for symbol in table.get("symbol", pd.Series(dtype=str)).tolist()
                    if str(symbol).strip()
                }
            )
            row1_left, row1_right = st.columns(2)
            name_map = _load_symbol_name_map(
                cfg,
                visible_market_symbols,
                force_refresh=force_data_refresh,
            )
            raw_up_table, raw_up_column_config = _prepare_momentum_table(raw_up, name_map=name_map)
            raw_down_table, raw_down_column_config = _prepare_momentum_table(raw_down, name_map=name_map)
            with row1_left:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Top 20 Up",
                    raw_up_table,
                    ["symbol", "company_name", "sparkline_3m", "close", "daily_change_pct", "return_1m_pct", "momentum_score"],
                    key="market_momentum_raw_up",
                    column_config=raw_up_column_config,
                ) or selected_market_ticker
            with row1_right:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Top 20 Down",
                    raw_down_table,
                    ["symbol", "company_name", "sparkline_3m", "close", "daily_change_pct", "return_1m_pct", "momentum_score"],
                    key="market_momentum_raw_down",
                    column_config=raw_down_column_config,
                ) or selected_market_ticker

            st.markdown("##### Momentum RoC")
            row2_left, row2_right = st.columns(2)
            roc_up_table, roc_up_column_config = _prepare_momentum_table(roc_up, name_map=name_map)
            roc_down_table, roc_down_column_config = _prepare_momentum_table(roc_down, name_map=name_map)
            with row2_left:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Up",
                    roc_up_table,
                    ["symbol", "company_name", "sparkline_3m", "close", "return_1w_pct", "return_1m_pct", "momentum_roc_score"],
                    key="market_momentum_roc_up",
                    column_config=roc_up_column_config,
                ) or selected_market_ticker
            with row2_right:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Down",
                    roc_down_table,
                    ["symbol", "company_name", "sparkline_3m", "close", "return_1w_pct", "return_1m_pct", "momentum_roc_score"],
                    key="market_momentum_roc_down",
                    column_config=roc_down_column_config,
                ) or selected_market_ticker

            st.markdown("##### Momentum Consistency")
            st.caption("Sorted by the lowest 3-month trendline-fit gap; lower means price has stayed closer to trend.")
            momentum = momentum.copy()
            momentum["trend_consistency_pct"] = (1.0 - pd.to_numeric(momentum["trend_fit_gap"], errors="coerce")) * 100.0
            consistency_up = momentum[pd.to_numeric(momentum["momentum_score"], errors="coerce") > 0].nsmallest(20, "trend_fit_gap")[
                ["symbol", "sparkline_3m", "close", "return_1m_pct", "return_3m_pct", "trend_consistency_pct", "trend_fit_gap"]
            ]
            consistency_down = momentum[pd.to_numeric(momentum["momentum_score"], errors="coerce") < 0].nsmallest(20, "trend_fit_gap")[
                ["symbol", "sparkline_3m", "close", "return_1m_pct", "return_3m_pct", "trend_consistency_pct", "trend_fit_gap"]
            ]
            consistency_symbols = sorted(
                {
                    *visible_market_symbols,
                    *[
                        str(symbol).upper().strip()
                        for table in (consistency_up, consistency_down)
                        for symbol in table.get("symbol", pd.Series(dtype=str)).tolist()
                        if str(symbol).strip()
                    ],
                }
            )
            if len(consistency_symbols) != len(visible_market_symbols):
                name_map.update(
                    _load_symbol_name_map(
                        cfg,
                        consistency_symbols,
                        force_refresh=force_data_refresh,
                    )
                )
            consistency_up_table, consistency_up_column_config = _prepare_momentum_table(consistency_up, name_map=name_map)
            consistency_down_table, consistency_down_column_config = _prepare_momentum_table(consistency_down, name_map=name_map)
            row3_left, row3_right = st.columns(2)
            with row3_left:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Up",
                    consistency_up_table,
                    ["symbol", "company_name", "sparkline_3m", "close", "return_1m_pct", "trend_consistency_pct", "trend_fit_gap"],
                    key="market_momentum_consistency_up",
                    column_config=consistency_up_column_config,
                ) or selected_market_ticker
            with row3_right:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Down",
                    consistency_down_table,
                    ["symbol", "company_name", "sparkline_3m", "close", "return_1m_pct", "trend_consistency_pct", "trend_fit_gap"],
                    key="market_momentum_consistency_down",
                    column_config=consistency_down_column_config,
                ) or selected_market_ticker
        else:
            st.info("No momentum profiles were returned for this market lens.")

        if not movers.empty:
            tree = px.treemap(
                movers,
                path=["symbol"],
                values="volume",
                color="change_pct",
                color_continuous_scale="RdYlGn",
                template="plotly_dark",
                title=f"Daily Movers - {business_filter} (Volume / Change %)",
            )
            st.plotly_chart(tree, use_container_width=True)
        else:
            st.info("Daily mover snapshots were unavailable for this market lens, so only the momentum-based views are shown.")

        detail_symbols = set(movers["symbol"].astype(str).tolist()) if not movers.empty else set()
        if not momentum.empty:
            detail_symbols.update(momentum["symbol"].astype(str).tolist())
        if not detail_symbols:
            st.info("No market symbols were available for the detail view.")
            st.stop()
        detail_symbol_options = sorted(detail_symbols)
        selected_key = "market_selected_ticker"
        widget_key = "market_ticker_detail_widget"
        fallback_market_ticker = st.session_state.get(selected_key) or detail_symbol_options[0]
        if fallback_market_ticker not in detail_symbol_options:
            fallback_market_ticker = detail_symbol_options[0]

        current_market_ticker = st.session_state.get(selected_key)
        if current_market_ticker not in detail_symbol_options:
            current_market_ticker = fallback_market_ticker

        if selected_market_ticker and selected_market_ticker in detail_symbol_options:
            if selected_market_ticker != current_market_ticker:
                st.session_state[selected_key] = selected_market_ticker
                st.session_state[widget_key] = selected_market_ticker
                st.rerun()
        elif selected_key not in st.session_state or st.session_state[selected_key] not in detail_symbol_options:
            st.session_state[selected_key] = fallback_market_ticker

        if widget_key not in st.session_state or st.session_state[widget_key] not in detail_symbol_options:
            st.session_state[widget_key] = st.session_state[selected_key]
        elif st.session_state[widget_key] != st.session_state[selected_key]:
            st.session_state[widget_key] = st.session_state[selected_key]

        ticker = st.selectbox(
            "Ticker Detail",
            detail_symbol_options,
            key=widget_key,
            on_change=_sync_market_ticker_from_widget,
        )
        st.session_state[selected_key] = ticker
        days = st.slider("Days", 60, 720, 365, step=30)

        analysis_days = max(days, 3650)
        try:
            with st.spinner("Loading price history..."):
                with _timed("load_price_history", ticker=ticker, days=analysis_days):
                    price = _load_price_history_cached(
                        cfg,
                        ticker,
                        analysis_days,
                        force_refresh=force_data_refresh,
                    )
        except AlpacaAPIError as exc:
            _log_event("load_price_history_failed", ticker=ticker, error=str(exc)[:200])
            st.warning(f"Could not load price history: {exc}")
            price = pd.DataFrame()

        signal_summary: dict[str, object] = {}
        if not price.empty:
            if _SIGNALS_IMPORT_ERROR:
                st.warning(
                    "Advanced signal charts are temporarily unavailable because the optional signals module "
                    f"did not load: {_SIGNALS_IMPORT_ERROR}"
                )
                fig = go.Figure(
                    go.Scatter(
                        x=price["timestamp"],
                        y=price["close"],
                        mode="lines",
                        name=ticker,
                    )
                )
                fig.update_layout(template="plotly_dark", title=f"{ticker} Price", xaxis_title="Date", yaxis_title="Price")
                st.plotly_chart(fig, use_container_width=True)

            signal_frame = _load_technical_signal_history_cached(
                cfg,
                ticker,
                days=max(days, 180),
                force_refresh=force_data_refresh,
            )
            if signal_frame.empty:
                if not _SIGNALS_IMPORT_ERROR:
                    st.info("Not enough valid price history to compute signals.")
            else:
                cutoff = signal_frame["timestamp"].max() - pd.Timedelta(days=days)
                visible_signal_frame = signal_frame[signal_frame["timestamp"] >= cutoff].copy()
                if visible_signal_frame.empty:
                    visible_signal_frame = signal_frame.tail(min(len(signal_frame), 120)).copy()

                signal_summary = _load_technical_signal_summary_cached(
                    cfg,
                    ticker,
                    signal_frame,
                    force_refresh=force_data_refresh,
                )
                forecast = forecast_next_week(signal_frame)

                metric_cols = st.columns(6)
                with metric_cols[0]:
                    st.metric("Pullback vs ATH", f"{signal_summary.get('pullback_from_ath_pct', np.nan):.1f}%")
                with metric_cols[1]:
                    st.metric("Channel Position", f"{signal_summary.get('channel_position', np.nan) * 100:.0f}%")
                with metric_cols[2]:
                    st.metric("Support Buffer", f"{signal_summary.get('dist_to_support_pct', np.nan):.1f}%")
                with metric_cols[3]:
                    st.metric("Room to Resistance", f"{signal_summary.get('dist_to_resistance_pct', np.nan):.1f}%")
                with metric_cols[4]:
                    up_probability = forecast.get("up_probability", np.nan) if forecast else np.nan
                    st.metric("1W Up Probability", f"{up_probability * 100:.0f}%")
                with metric_cols[5]:
                    breakout_probability = forecast.get("breakout_probability", np.nan) if forecast else np.nan
                    st.metric("1W Breakout Prob", f"{breakout_probability * 100:.0f}%")

                if signal_summary:
                    st.caption(
                        f"Signal regime: {signal_summary.get('regime', 'n/a')} | "
                        f"RSI 14: {signal_summary.get('rsi_14', np.nan):.1f} | "
                        f"20D realized vol: {signal_summary.get('vol_20_ann_pct', np.nan):.1f}% | "
                        "ATH and channel signals are computed from up to 10 years of daily bars."
                    )

                channel_left, channel_right = st.columns(2)
                with channel_left:
                    st.plotly_chart(build_price_channel_figure(visible_signal_frame, ticker), use_container_width=True)
                with channel_right:
                    st.plotly_chart(build_pullback_figure(visible_signal_frame, ticker), use_container_width=True)

                forecast_left, forecast_right = st.columns(2)
                with forecast_left:
                    if forecast:
                        st.plotly_chart(
                            build_forecast_cone_figure(visible_signal_frame, forecast, ticker),
                            use_container_width=True,
                        )
                    else:
                        st.info("Not enough history to build the next-week probability model.")
                with forecast_right:
                    if forecast:
                        st.plotly_chart(
                            build_terminal_distribution_figure(forecast, ticker),
                            use_container_width=True,
                        )
                        st.caption(
                            f"Analog model: {forecast.get('analog_count', 0)} similar historical setups, "
                            f"expected 5-day return {forecast.get('expected_return_pct', np.nan):.2f}%."
                        )
                    else:
                        st.info("Forecast distribution unavailable for this ticker.")

        try:
            with st.spinner("Loading selected ticker fundamentals..."):
                with _timed("load_market_detail_fundamentals", ticker=ticker):
                    market_fundamentals = _load_quarterly_fundamentals_cached(
                        ticker,
                        force_refresh=force_data_refresh,
                    )
        except Exception as exc:
            _log_event("load_market_detail_fundamentals_failed", ticker=ticker, error=str(exc)[:200])
            st.warning(f"Could not load fundamentals for {ticker}: {exc}")
            market_fundamentals = {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()}

        try:
            with st.spinner("Loading company profile and recent news..."):
                with _timed("load_market_detail_context", ticker=ticker):
                    asset = _load_asset_metadata_cached(cfg, ticker, force_refresh=force_data_refresh)
                    news_payload = _load_recent_news_cached(
                        cfg,
                        ticker,
                        days=14,
                        limit=6,
                        force_refresh=force_data_refresh,
                    )
        except Exception as exc:
            _log_event("load_market_detail_context_failed", ticker=ticker, error=str(exc)[:200])
            st.warning(f"Could not load company context for {ticker}: {exc}")
            asset = {}
            news_payload = {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}

        st.subheader(f"{ticker} Overview")
        description = build_company_description(
            ticker,
            asset,
            market_fundamentals,
            signal_summary,
            news_payload=news_payload,
            active_lens=business_filter,
        )
        st.write(description)

        news_summary = summarize_recent_news(ticker, news_payload)
        news_source = news_summary.get("source")
        if news_source:
            st.caption(f"Recent news source: {str(news_source).title()}")
        summary_lines = news_summary.get("summary_lines", [])
        if summary_lines:
            st.markdown("\n".join(f"- {line}" for line in summary_lines))
        else:
            st.info("No recent news summary was available for this ticker.")

        news_articles = news_summary.get("articles", pd.DataFrame())
        if isinstance(news_articles, pd.DataFrame) and not news_articles.empty:
            with st.expander("Recent Headlines", expanded=False):
                for _, row in news_articles.iterrows():
                    headline = str(row.get("headline") or "Untitled").strip()
                    source = str(row.get("source") or "News").strip()
                    published_at = pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")
                    published_label = published_at.strftime("%Y-%m-%d") if pd.notna(published_at) else "n/a"
                    url = str(row.get("url") or "").strip()
                    prefix = f"{source} | {published_label}"
                    if url:
                        st.markdown(f"- [{headline}]({url})")
                    else:
                        st.markdown(f"- {headline}")
                    st.caption(prefix)

        income = market_fundamentals.get("income", pd.DataFrame())
        balance = market_fundamentals.get("balance", pd.DataFrame())
        cashflow = market_fundamentals.get("cashflow", pd.DataFrame())
        st.subheader(f"{ticker} Fundamentals")
        if income.empty and balance.empty and cashflow.empty:
            st.info("No quarterly fundamentals found for this ticker in the local dataset.")
        else:
            fund_left, fund_right, fund_bottom = st.columns(3)
            with fund_left:
                st.plotly_chart(plot_statement(income, f"{ticker} Income"), use_container_width=True)
            with fund_right:
                st.plotly_chart(plot_statement(balance, f"{ticker} Balance"), use_container_width=True)
            with fund_bottom:
                st.plotly_chart(plot_statement(cashflow, f"{ticker} Cash Flow"), use_container_width=True)

elif section == "Technical Strategizer":
    st.title("Technical Strategizer")
    ticker = st.text_input("Ticker", value="AAPL").upper().strip()
    days = st.slider("Lookback (days)", 90, 1095, 365, step=15)

    if ticker and _has_live_api(api, "Technical Strategizer requires a working Alpaca connection."):
        try:
            with st.spinner("Loading technical data..."):
                with _timed("technical_load_price_history", ticker=ticker, days=days):
                    frame = _load_price_history_cached(cfg, ticker, days=days, force_refresh=force_data_refresh)
        except AlpacaAPIError as exc:
            _log_event("technical_load_price_history_failed", ticker=ticker, error=str(exc)[:200])
            st.error(f"Could not load technical data: {exc}")
            st.stop()

        if frame.empty:
            st.info("No bars returned for this ticker.")
        else:
            st.plotly_chart(build_technical_figure(frame, f"Technical View - {ticker}"), use_container_width=True)
            st.dataframe(frame.tail(40), use_container_width=True, hide_index=True)

            signal_frame = _load_technical_signal_history_cached(
                cfg,
                ticker,
                days=max(days, 180),
                force_refresh=force_data_refresh,
            )
            signal_summary = _load_technical_signal_summary_cached(
                cfg,
                ticker,
                signal_frame,
                force_refresh=force_data_refresh,
            )
            if signal_summary:
                metric_cols = st.columns(5)
                with metric_cols[0]:
                    st.metric("Signal Regime", str(signal_summary.get("regime") or "n/a"))
                with metric_cols[1]:
                    st.metric("RSI 14", f"{pd.to_numeric(signal_summary.get('rsi_14'), errors='coerce'):.1f}")
                with metric_cols[2]:
                    st.metric("Pullback vs ATH", f"{pd.to_numeric(signal_summary.get('pullback_from_ath_pct'), errors='coerce'):.1f}%")
                with metric_cols[3]:
                    st.metric("Channel Position", f"{pd.to_numeric(signal_summary.get('channel_position'), errors='coerce') * 100:.0f}%")
                with metric_cols[4]:
                    st.metric("20D Vol (ann)", f"{pd.to_numeric(signal_summary.get('vol_20_ann_pct'), errors='coerce'):.1f}%")

elif section == "Option Strategizer":
    st.title("Option Strategizer")
    ticker = st.text_input("Ticker", value="AAPL", key="opt_ticker").upper().strip()

    if ticker and _has_live_api(api, "Option Strategizer requires a working Alpaca connection."):
        spot_price = np.nan
        try:
            with _timed("option_reference_price", ticker=ticker):
                reference_frame = _load_price_history_cached(cfg, ticker, days=30, force_refresh=force_data_refresh)
            if not reference_frame.empty and "close" in reference_frame.columns:
                spot_price = float(pd.to_numeric(reference_frame["close"], errors="coerce").dropna().iloc[-1])
        except Exception as exc:
            _log_event("option_reference_price_failed", ticker=ticker, error=str(exc)[:200])

        try:
            with st.spinner("Loading option expirations..."):
                with _timed("load_option_chain", ticker=ticker):
                    expirations, _, _ = _load_option_chain_cached(
                        cfg,
                        ticker,
                        force_refresh=force_data_refresh,
                    )
        except AlpacaAPIError as exc:
            _log_event("load_option_chain_failed", ticker=ticker, error=str(exc)[:200])
            st.warning(f"Could not load option chain: {exc}")
            expirations = []

        if not expirations:
            st.info("No options chain available for this ticker.")
        else:
            exp = st.selectbox("Expiration", expirations)
            try:
                with st.spinner("Loading option quotes..."):
                    with _timed("load_option_chain_expiration", ticker=ticker, expiration=exp):
                        _, calls, puts = _load_option_chain_cached(
                            cfg,
                            ticker,
                            exp,
                            force_refresh=force_data_refresh,
                        )
            except AlpacaAPIError as exc:
                _log_event("load_option_chain_expiration_failed", ticker=ticker, expiration=exp, error=str(exc)[:200])
                st.warning(f"Could not load option quotes: {exc}")
                calls = pd.DataFrame()
                puts = pd.DataFrame()

            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Top Calls")
                st.dataframe(rank_options(calls), use_container_width=True, hide_index=True)
            with c2:
                st.subheader("Top Puts")
                st.dataframe(rank_options(puts), use_container_width=True, hide_index=True)

            plot_calls = calls.dropna(subset=["strike", "impliedVolatility"]).copy()
            plot_calls, size_col = _prepare_scatter_size(plot_calls, "openInterest")
            if not plot_calls.empty:
                fig_c = px.scatter(
                    plot_calls,
                    x="strike",
                    y="impliedVolatility",
                    size=size_col,
                    color="volume" if "volume" in plot_calls.columns else None,
                    template="plotly_dark",
                    title=f"{ticker} Calls IV Surface ({exp})",
                )
                st.plotly_chart(fig_c, use_container_width=True)
            elif not calls.empty:
                st.info("No call rows had both strike and implied volatility for plotting.")

            st.markdown("---")
            st.subheader("Greek-Based Scenario Selector")
            if np.isfinite(spot_price):
                st.caption(
                    "Scenario model uses delta, gamma, and theta to approximate option value at your target date, "
                    "then scores contracts for projected return, leverage, lower decay, lower cost, and liquidity."
                )
                control_cols = st.columns(3)
                with control_cols[0]:
                    st.metric("Spot Reference", f"${spot_price:,.2f}")
                with control_cols[1]:
                    expected_price = st.number_input(
                        "Expected Price",
                        min_value=0.01,
                        value=float(round(spot_price * 1.05, 2)),
                        step=float(max(round(spot_price * 0.01, 2), 0.5)),
                        key=f"option_expected_price_{ticker}",
                    )
                with control_cols[2]:
                    scenario_days = st.slider(
                        "Timespan (days from now)",
                        1,
                        180,
                        30,
                        key=f"option_scenario_days_{ticker}",
                    )

                scenario_bias = "Bullish calls" if expected_price >= spot_price else "Bearish puts"
                st.caption(
                    f"Scenario bias: {scenario_bias}. "
                    "Projected option change ~= delta*dS + 0.5*gamma*dS^2 + theta*days. Vega and vol-surface changes are not modeled."
                )

                try:
                    with st.spinner("Building cross-expiration option surface..."):
                        with _timed(
                            "load_option_surface",
                            ticker=ticker,
                            expected_price=f"{expected_price:.2f}",
                            horizon_days=scenario_days,
                        ):
                            surface = _load_option_surface_cached(
                                cfg,
                                ticker,
                                float(expected_price),
                                int(scenario_days),
                                float(spot_price),
                                force_refresh=force_data_refresh,
                            )
                except AlpacaAPIError as exc:
                    _log_event("load_option_surface_failed", ticker=ticker, error=str(exc)[:200])
                    st.warning(f"Could not build option surface: {exc}")
                    surface = pd.DataFrame()

                if surface.empty:
                    st.info("No option surface data was available for this scenario.")
                else:
                    candidates, summary = analyze_option_candidates(
                        surface,
                        underlying_price=float(spot_price),
                        expected_price=float(expected_price),
                        horizon_days=int(scenario_days),
                    )

                    if candidates.empty:
                        st.info("No option candidates had enough quote and Greek data for scenario analysis.")
                    else:
                        best = candidates.iloc[0]
                        metric_cols = st.columns(5)
                        with metric_cols[0]:
                            st.metric("Best Contract", str(best.get("contractSymbol") or "n/a"))
                        with metric_cols[1]:
                            st.metric("Selection Score", f"{pd.to_numeric(best.get('selection_score'), errors='coerce'):.1f}")
                        with metric_cols[2]:
                            st.metric("Projected Return", f"{pd.to_numeric(best.get('projected_return_pct'), errors='coerce'):.1f}%")
                        with metric_cols[3]:
                            st.metric("Upfront Cost", f"${pd.to_numeric(best.get('contracts_cost'), errors='coerce'):,.0f}")
                        with metric_cols[4]:
                            st.metric("Theta Drag to Target", f"{pd.to_numeric(best.get('theta_drag_pct'), errors='coerce'):.1f}%")

                        st.caption(
                            f"Top side: {summary.get('preferred_side', 'n/a')} | "
                            f"Expected move: {summary.get('expected_move_pct', np.nan):.1f}% | "
                            f"Candidates scored: {summary.get('candidate_count', 0)}"
                        )

                        plot_surface = candidates.dropna(
                            subset=["strike", "dte", "selection_score", "projected_return_pct"]
                        ).copy()
                        plot_surface, surface_size_col = _prepare_scatter_size(plot_surface, "delta_leverage")
                        if not plot_surface.empty:
                            fig_surface = px.scatter_3d(
                                plot_surface.head(250),
                                x="strike",
                                y="dte",
                                z="selection_score",
                                color="projected_return_pct",
                                size=surface_size_col,
                                hover_name="contractSymbol",
                                hover_data={
                                    "type": True,
                                    "premium": ":.2f",
                                    "contracts_cost": ":.0f",
                                    "delta": ":.3f",
                                    "gamma": ":.4f",
                                    "theta": ":.3f",
                                    "vega": ":.3f",
                                    "delta_leverage": ":.2f",
                                    "theta_drag_pct": ":.1f",
                                    "projected_return_pct": ":.1f",
                                    "selection_score": ":.1f",
                                },
                                template="plotly_dark",
                                title=f"{ticker} Option Opportunity Surface",
                                labels={
                                    "strike": "Strike",
                                    "dte": "Days to Expiry",
                                    "selection_score": "Selection Score",
                                    "projected_return_pct": "Projected Return %",
                                },
                            )
                            fig_surface.update_traces(marker=dict(opacity=0.78))
                            st.plotly_chart(fig_surface, use_container_width=True)

                        greek_tradeoff = candidates.dropna(
                            subset=["delta", "theta_drag_pct", "projected_return_pct", "selection_score"]
                        ).copy()
                        greek_tradeoff, greek_size_col = _prepare_scatter_size(greek_tradeoff, "gamma_convexity")
                        if not greek_tradeoff.empty:
                            fig_greeks = px.scatter_3d(
                                greek_tradeoff.head(250),
                                x="delta",
                                y="theta_drag_pct",
                                z="projected_return_pct",
                                color="selection_score",
                                size=greek_size_col,
                                hover_name="contractSymbol",
                                hover_data={
                                    "strike": ":.2f",
                                    "dte": ":.0f",
                                    "premium": ":.2f",
                                    "contracts_cost": ":.0f",
                                    "delta_leverage": ":.2f",
                                    "gamma_convexity": ":.2f",
                                    "selection_score": ":.1f",
                                },
                                template="plotly_dark",
                                title=f"{ticker} Greek Tradeoff Surface",
                                labels={
                                    "delta": "Delta",
                                    "theta_drag_pct": "Theta Drag to Target %",
                                    "projected_return_pct": "Projected Return %",
                                    "selection_score": "Selection Score",
                                },
                            )
                            fig_greeks.update_traces(marker=dict(opacity=0.78))
                            st.plotly_chart(fig_greeks, use_container_width=True)

                        st.dataframe(
                            candidates.head(15)[
                                [
                                    "contractSymbol",
                                    "type",
                                    "expiration",
                                    "dte",
                                    "strike",
                                    "premium",
                                    "contracts_cost",
                                    "delta",
                                    "gamma",
                                    "theta",
                                    "delta_leverage",
                                    "theta_drag_pct",
                                    "projected_return_pct",
                                    "selection_score",
                                ]
                            ],
                            use_container_width=True,
                            hide_index=True,
                        )
            else:
                st.info("A recent stock price was not available, so the Greek-based scenario selector is hidden for this ticker.")

elif section == "Fundamental Strategizer":
    st.title("Fundamental Strategizer")
    ticker = st.text_input("Ticker", value="AAPL", key="fund_ticker").upper().strip()

    if ticker:
        try:
            with st.spinner("Loading quarterly fundamentals..."):
                with _timed("load_quarterly_fundamentals", ticker=ticker):
                    data = _load_quarterly_fundamentals_cached(ticker, force_refresh=force_data_refresh)
        except Exception as exc:
            _log_event("load_quarterly_fundamentals_failed", ticker=ticker, error=str(exc)[:200])
            st.warning(f"Could not load quarterly fundamentals: {exc}")
            data = {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()}
        income = data.get("income", pd.DataFrame())
        balance = data.get("balance", pd.DataFrame())
        cashflow = data.get("cashflow", pd.DataFrame())

        if income.empty and balance.empty and cashflow.empty:
            st.info("No quarterly fundamentals found for this ticker in the local quarterly dataset.")
        else:
            st.plotly_chart(plot_statement(income, f"{ticker} - Income Statement (Quarterly)"), use_container_width=True)
            st.plotly_chart(plot_statement(balance, f"{ticker} - Balance Sheet (Quarterly)"), use_container_width=True)
            st.plotly_chart(plot_statement(cashflow, f"{ticker} - Cash Flow (Quarterly)"), use_container_width=True)

            with st.expander("Show Raw Fundamental Tables"):
                st.subheader("Income")
                st.dataframe(income, use_container_width=True, hide_index=True)
                st.subheader("Balance")
                st.dataframe(balance, use_container_width=True, hide_index=True)
                st.subheader("Cash Flow")
                st.dataframe(cashflow, use_container_width=True, hide_index=True)
