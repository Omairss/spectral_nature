from __future__ import annotations

import base64
from typing import Any, Callable

import pandas as pd
import requests
import streamlit as st

from compute.fundamentals import latest_share_count
from services.config import AppConfig
from services.pipeline_store import latest_dataset_metadata, load_latest_dataset_frame

_CurrentUserContextProvider = Callable[[], Any]
_DataAccessLayerFactory = Callable[..., Any]
_PresentationLayerOnlyProvider = Callable[[], bool]

_current_user_context_provider: _CurrentUserContextProvider | None = None
_data_access_layer_factory: _DataAccessLayerFactory | None = None
_presentation_layer_only_provider: _PresentationLayerOnlyProvider | None = None


def configure_dashboard_loaders(
    *,
    current_user_context_provider: _CurrentUserContextProvider,
    data_access_layer_factory: _DataAccessLayerFactory,
    presentation_layer_only_provider: _PresentationLayerOnlyProvider,
) -> None:
    global _current_user_context_provider
    global _data_access_layer_factory
    global _presentation_layer_only_provider
    _current_user_context_provider = current_user_context_provider
    _data_access_layer_factory = data_access_layer_factory
    _presentation_layer_only_provider = presentation_layer_only_provider


def _current_user_context() -> Any:
    if _current_user_context_provider is None:
        raise RuntimeError("dashboard loaders are not configured")
    return _current_user_context_provider()


def _data_access_layer(*, cfg: AppConfig | None = None, fred_api_key: str | None = None) -> Any:
    if _data_access_layer_factory is None:
        raise RuntimeError("dashboard loaders are not configured")
    return _data_access_layer_factory(cfg=cfg, fred_api_key=fred_api_key)


def _presentation_layer_only() -> bool:
    if _presentation_layer_only_provider is None:
        raise RuntimeError("dashboard loaders are not configured")
    return bool(_presentation_layer_only_provider())


def _consume_source_force_requested(source: str) -> bool:
    source_key = str(source or "").strip()
    if not source_key:
        return False
    flags = st.session_state.get("_source_force_refresh", {})
    if not isinstance(flags, dict):
        return False
    should_force = bool(flags.get(source_key, False))
    if should_force:
        flags = dict(flags)
        flags[source_key] = False
        st.session_state["_source_force_refresh"] = flags
    return should_force


def _resolve_data_access_payload(
    resolver: str,
    *,
    cfg: AppConfig | None = None,
    fred_api_key: str | None = None,
    source: str | None = None,
    force_refresh: bool = False,
    **kwargs: object,
):
    pending_source_refresh = source is not None and _consume_source_force_requested(str(source))
    effective_force = force_refresh or pending_source_refresh
    if _presentation_layer_only():
        effective_force = False
    method = getattr(_data_access_layer(cfg=cfg, fred_api_key=fred_api_key), resolver)
    return method(force_refresh=effective_force, **kwargs).payload


def _load_account_cached(cfg: AppConfig, force_refresh: bool = False) -> dict[str, object]:
    context = _current_user_context()
    if context is not None and not context.can_view_full_portfolio:
        return _resolve_data_access_payload("resolve_user_account", cfg=cfg, user_context=context, force_refresh=force_refresh)
    return _resolve_data_access_payload("resolve_account", cfg=cfg, force_refresh=force_refresh)


def _load_positions_cached(cfg: AppConfig, force_refresh: bool = False) -> pd.DataFrame:
    context = _current_user_context()
    if context is not None and not context.can_view_full_portfolio:
        return _resolve_data_access_payload("resolve_user_positions", cfg=cfg, user_context=context, force_refresh=force_refresh)
    return _resolve_data_access_payload("resolve_positions", cfg=cfg, force_refresh=force_refresh)


def _load_timeseries_cached(cfg: AppConfig, period: str, force_refresh: bool = False) -> pd.DataFrame:
    context = _current_user_context()
    if context is not None and not context.can_view_full_portfolio:
        return _resolve_data_access_payload(
            "resolve_user_portfolio_timeseries",
            cfg=cfg,
            user_context=context,
            period=period,
            force_refresh=force_refresh,
        )
    return _resolve_data_access_payload(
        "resolve_portfolio_timeseries",
        cfg=cfg,
        period=period,
        force_refresh=force_refresh,
    )


def _load_portfolio_performance_cached(cfg: AppConfig, period: str, force_refresh: bool = False) -> pd.DataFrame:
    context = _current_user_context()
    if context is not None and not context.can_view_full_portfolio:
        return _resolve_data_access_payload(
            "resolve_user_portfolio_performance",
            cfg=cfg,
            user_context=context,
            period=period,
            force_refresh=force_refresh,
        )
    return _resolve_data_access_payload(
        "resolve_portfolio_performance",
        cfg=cfg,
        period=period,
        force_refresh=force_refresh,
    )


def _load_holding_roc_cached(
    cfg: AppConfig,
    symbols: list[str],
    days: int = 365,
    force_refresh: bool = False,
) -> pd.DataFrame:
    normalized_symbols = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
    return _resolve_data_access_payload(
        "resolve_holding_roc",
        cfg=cfg,
        symbols=normalized_symbols,
        days=days,
        force_refresh=force_refresh,
    )


def _scan_daily_movers_cached(
    cfg: AppConfig,
    symbols: list[str] | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    return _resolve_data_access_payload(
        "resolve_daily_movers",
        cfg=cfg,
        source="equities",
        symbols=symbols,
        force_refresh=force_refresh,
    )


def _scan_momentum_profiles_cached(
    cfg: AppConfig,
    days: int = 180,
    symbols: list[str] | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    return _resolve_data_access_payload(
        "resolve_momentum_profiles",
        cfg=cfg,
        source="equities",
        days=days,
        symbols=symbols,
        force_refresh=force_refresh,
    )


@st.cache_data(ttl=300, show_spinner=False)
def _load_market_opportunity_feed_cached(
    cfg: AppConfig,
    business_filter: str = "All Market",
    selected_horizon_col: str = "return_1m_pct",
    selected_horizon_label: str = "1 Month",
    symbols: list[str] | None = None,
    limit: int = 80,
    force_refresh: bool = False,
) -> pd.DataFrame:
    normalized_symbols = sorted({str(symbol).upper().strip() for symbol in list(symbols or []) if str(symbol).strip()})
    return _resolve_data_access_payload(
        "resolve_market_opportunity_feed",
        cfg=cfg,
        business_filter=str(business_filter or "All Market"),
        selected_horizon_col=str(selected_horizon_col or "return_1m_pct"),
        selected_horizon_label=str(selected_horizon_label or "1 Month"),
        symbols=normalized_symbols,
        limit=int(limit),
        force_refresh=force_refresh,
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
    return _resolve_data_access_payload(
        "resolve_correlation_phase_shift",
        cfg=cfg,
        source="derivatives",
        benchmark=benchmark,
        days=days,
        corr_window=corr_window,
        roc_window=roc_window,
        momentum_window=momentum_window,
        symbols=symbols,
        force_refresh=force_refresh,
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
    return _resolve_data_access_payload(
        "resolve_commodity_regime",
        cfg=cfg,
        source="commodities",
        commodity_symbols=commodity_symbols,
        days=days,
        corr_window=corr_window,
        roc_window=roc_window,
        momentum_window=momentum_window,
        symbols=symbols,
        force_refresh=force_refresh,
    )


def _load_price_history_cached(cfg: AppConfig, ticker: str, days: int, force_refresh: bool = False) -> pd.DataFrame:
    return _resolve_data_access_payload(
        "resolve_price_history",
        cfg=cfg,
        source="equities",
        ticker=ticker,
        days=days,
        force_refresh=force_refresh,
    )


def _load_technical_signal_history_cached(
    cfg: AppConfig,
    ticker: str,
    days: int,
    force_refresh: bool = False,
) -> pd.DataFrame:
    return _resolve_data_access_payload(
        "resolve_technical_signal_history",
        cfg=cfg,
        source="derivatives",
        ticker=ticker,
        days=days,
        force_refresh=force_refresh,
    )


def _load_technical_signal_summary_cached(
    cfg: AppConfig,
    ticker: str,
    signal_frame: pd.DataFrame,
    force_refresh: bool = False,
) -> dict[str, float | str]:
    return _resolve_data_access_payload(
        "resolve_technical_signal_summary",
        cfg=cfg,
        source="derivatives",
        ticker=ticker,
        signal_frame=signal_frame,
        force_refresh=force_refresh,
    )


def _load_forecast_next_week_cached(
    cfg: AppConfig,
    ticker: str,
    days: int,
    signal_frame: pd.DataFrame | None = None,
    force_refresh: bool = False,
) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_forecast_next_week",
        cfg=cfg,
        source="derivatives",
        ticker=ticker,
        days=days,
        signal_frame=signal_frame,
        force_refresh=force_refresh,
    )


def _load_option_chain_cached(
    cfg: AppConfig,
    ticker: str,
    expiration: str | None = None,
    force_refresh: bool = False,
) -> tuple[list[str], pd.DataFrame, pd.DataFrame]:
    return _resolve_data_access_payload(
        "resolve_option_chain",
        cfg=cfg,
        source="options",
        ticker=ticker,
        expiration=expiration,
        force_refresh=force_refresh,
    )


def _load_option_surface_cached(
    cfg: AppConfig,
    ticker: str,
    expected_price: float,
    horizon_days: int,
    underlying_price: float,
    force_refresh: bool = False,
) -> pd.DataFrame:
    return _resolve_data_access_payload(
        "resolve_option_surface",
        cfg=cfg,
        source="options",
        ticker=ticker,
        expected_price=expected_price,
        horizon_days=horizon_days,
        underlying_price=underlying_price,
        force_refresh=force_refresh,
    )


def _load_option_candidates_cached(
    cfg: AppConfig,
    ticker: str,
    expected_price: float,
    horizon_days: int,
    underlying_price: float,
    surface: pd.DataFrame | None = None,
    force_refresh: bool = False,
) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_option_candidates",
        cfg=cfg,
        source="options",
        ticker=ticker,
        expected_price=expected_price,
        horizon_days=horizon_days,
        underlying_price=underlying_price,
        surface=surface,
        force_refresh=force_refresh,
    )


def _load_quarterly_fundamentals_cached(ticker: str, force_refresh: bool = False) -> dict[str, pd.DataFrame]:
    return _resolve_data_access_payload(
        "resolve_quarterly_fundamentals",
        source="fundamentals",
        ticker=ticker,
        force_refresh=force_refresh,
    )


def _load_asset_metadata_cached(cfg: AppConfig, ticker: str, force_refresh: bool = False) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_asset_metadata",
        cfg=cfg,
        ticker=ticker,
        force_refresh=force_refresh,
    )


def _load_universe_security_name_map_uncached() -> dict[str, str]:
    frame, _ = load_latest_dataset_frame("universe_snapshot")
    if frame.empty or "symbol" not in frame.columns or "security_name" not in frame.columns:
        return {}
    table = frame[["symbol", "security_name"]].copy()
    table["symbol"] = table["symbol"].astype(str).str.upper().str.strip()
    table["security_name"] = table["security_name"].astype(str).str.strip()
    table = table[table["symbol"].ne("") & table["security_name"].ne("")]
    return dict(table.drop_duplicates(subset=["symbol"], keep="first").itertuples(index=False, name=None))


@st.cache_data(ttl=3600, show_spinner=False)
def _load_universe_security_name_map_memoized() -> dict[str, str]:
    return _load_universe_security_name_map_uncached()


def _load_universe_security_name_map(force_refresh: bool = False) -> dict[str, str]:
    if force_refresh:
        return _load_universe_security_name_map_uncached()
    return _load_universe_security_name_map_memoized()


@st.cache_data(ttl=300, show_spinner=False)
def _load_page_agentic_summary_cached(
    surface: str,
    context_signature: str,
    ticker: str = "",
    force_refresh: bool = False,
    dataset_version_token: str = "",
) -> dict[str, object]:
    del dataset_version_token
    return _resolve_data_access_payload(
        "resolve_page_agentic_summary",
        source="attention",
        surface=str(surface or ""),
        context_signature=str(context_signature or ""),
        ticker=str(ticker or ""),
        force_refresh=force_refresh,
    )


def _latest_page_agentic_summary_cache_token() -> str:
    try:
        metadata = latest_dataset_metadata("page_agentic_summaries")
    except Exception:
        return ""
    if metadata is None:
        return ""
    return str(metadata.dataset_version_id or metadata.asof_time_utc or "").strip()


def _latest_close_from_price_history(frame: pd.DataFrame) -> float | None:
    if frame.empty or "close" not in frame.columns:
        return None
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if close.empty:
        return None
    return float(close.iloc[-1])


def _public_chart_range(days: int) -> str:
    lookback_days = max(int(days), 1)
    if lookback_days <= 30:
        return "1mo"
    if lookback_days <= 90:
        return "3mo"
    if lookback_days <= 180:
        return "6mo"
    if lookback_days <= 365:
        return "1y"
    if lookback_days <= 730:
        return "2y"
    return "5y"


def _load_public_price_history_uncached(
    symbol: str,
    *,
    days: int,
) -> pd.DataFrame:
    target = str(symbol or "").upper().strip()
    if not target:
        return pd.DataFrame()

    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{target}",
            params={
                "interval": "1d",
                "range": _public_chart_range(days),
                "includeAdjustedClose": "true",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return pd.DataFrame()

    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame()
    first = result[0] if isinstance(result[0], dict) else {}
    timestamps = list(first.get("timestamp") or [])
    quote = (((first.get("indicators") or {}).get("quote") or [{}])[0] or {})
    if not timestamps or not isinstance(quote, dict):
        return pd.DataFrame()

    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps, unit="s", utc=True, errors="coerce"),
            "open": pd.to_numeric(quote.get("open"), errors="coerce"),
            "high": pd.to_numeric(quote.get("high"), errors="coerce"),
            "low": pd.to_numeric(quote.get("low"), errors="coerce"),
            "close": pd.to_numeric(quote.get("close"), errors="coerce"),
            "volume": pd.to_numeric(quote.get("volume"), errors="coerce"),
        }
    )
    frame = frame.dropna(subset=["timestamp", "close"]).sort_values("timestamp").reset_index(drop=True)
    return frame


@st.cache_data(ttl=21600, show_spinner=False)
def _load_public_price_history_memoized(
    symbol: str,
    *,
    days: int,
) -> pd.DataFrame:
    return _load_public_price_history_uncached(symbol, days=days)


def _load_public_price_history_cached(
    symbol: str,
    *,
    days: int,
    force_refresh: bool = False,
) -> pd.DataFrame:
    if force_refresh:
        return _load_public_price_history_uncached(symbol, days=days)
    return _load_public_price_history_memoized(symbol, days=days)


def _format_market_cap_label(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    amount = float(value)
    magnitude = abs(amount)
    if magnitude >= 1_000_000_000_000:
        return f"${amount / 1_000_000_000_000:.2f}T"
    if magnitude >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}B"
    if magnitude >= 1_000_000:
        return f"${amount / 1_000_000:.0f}M"
    return f"${amount:,.0f}"


def _sparkline_svg(frame: pd.DataFrame, *, width: int = 164, height: int = 56) -> str:
    if frame.empty or "close" not in frame.columns:
        return ""
    series = pd.to_numeric(frame["close"], errors="coerce").dropna().tail(30)
    if len(series) < 2:
        return ""

    values = series.astype(float).tolist()
    minimum = min(values)
    maximum = max(values)
    spread = maximum - minimum
    if spread == 0:
        spread = max(abs(maximum), 1.0) * 0.01 or 1.0
        minimum -= spread / 2.0
        maximum += spread / 2.0
        spread = maximum - minimum

    x_step = width / max(len(values) - 1, 1)
    points: list[str] = []
    for idx, value in enumerate(values):
        x_pos = round(idx * x_step, 2)
        y_pos = round(height - (((value - minimum) / spread) * height), 2)
        points.append(f"{x_pos},{y_pos}")

    stroke = "#16a34a" if values[-1] >= values[0] else "#dc2626"
    baseline = round(height - (((values[0] - minimum) / spread) * height), 2)
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
        f"preserveAspectRatio='none' aria-hidden='true'>"
        f"<polyline fill='none' stroke='rgba(148,163,184,0.22)' stroke-width='1' points='0,{baseline} {width},{baseline}' />"
        f"<polyline fill='none' stroke='{stroke}' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round' "
        f"points='{' '.join(points)}' />"
        f"</svg>"
    )


def _sparkline_data_uri(frame: pd.DataFrame, *, width: int = 164, height: int = 56) -> str:
    svg = _sparkline_svg(frame, width=width, height=height)
    if not svg:
        return ""
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _load_ticker_snapshot_profile(
    cfg: AppConfig | None,
    symbol: str,
    *,
    force_refresh: bool = False,
    allow_live_fallback: bool = True,
) -> dict[str, str]:
    target = str(symbol or "").upper().strip()
    if not target:
        return {"symbol": "", "company_name": "", "market_cap_label": "n/a", "sparkline_data_uri": ""}

    company_name_hint = ""
    market_cap_label_hint = "n/a"
    sparkline_hint = ""

    materialized_map = _load_attention_ticker_snapshot_map_cached(force_refresh=force_refresh)
    materialized_row = dict(materialized_map.get(target) or {})
    if materialized_row:
        company_name_hint = str(materialized_row.get("company_name") or target).strip()
        market_cap_label_hint = str(materialized_row.get("market_cap_label") or "n/a").strip()
        sparkline_hint = str(materialized_row.get("sparkline_data_uri") or "").strip()
        if sparkline_hint:
            return {
                "symbol": target,
                "company_name": company_name_hint,
                "market_cap_label": market_cap_label_hint,
                "sparkline_data_uri": sparkline_hint,
            }

    universe_names = _load_universe_security_name_map(force_refresh=force_refresh)
    if not allow_live_fallback:
        return {
            "symbol": target,
            "company_name": company_name_hint or str(universe_names.get(target) or target).strip(),
            "market_cap_label": market_cap_label_hint,
            "sparkline_data_uri": sparkline_hint,
        }

    if cfg is not None:
        try:
            materialized = _load_attention_ticker_snapshot_cached(
                cfg,
                target,
                force_refresh=force_refresh,
            )
        except Exception:
            materialized = {}
        if isinstance(materialized, dict) and str(materialized.get("symbol") or "").upper().strip() == target:
            company_name_hint = company_name_hint or str(materialized.get("company_name") or target).strip()
            market_cap_label_hint = market_cap_label_hint if market_cap_label_hint != "n/a" else str(materialized.get("market_cap_label") or "n/a").strip()
            sparkline_hint = sparkline_hint or str(materialized.get("sparkline_data_uri") or "").strip()
            if sparkline_hint:
                return {
                    "symbol": target,
                    "company_name": company_name_hint,
                    "market_cap_label": market_cap_label_hint,
                    "sparkline_data_uri": sparkline_hint,
                }

    asset: dict[str, object] = {}
    if cfg is not None:
        try:
            asset = _load_asset_metadata_cached(cfg, target, force_refresh=force_refresh)
        except Exception:
            asset = {}
    company_name = company_name_hint or str(asset.get("name") or universe_names.get(target) or target).strip()

    price_history = pd.DataFrame()
    if cfg is not None:
        try:
            price_history = _load_price_history_cached(cfg, target, days=60, force_refresh=force_refresh)
        except Exception:
            price_history = pd.DataFrame()
    if price_history.empty:
        price_history = _load_public_price_history_cached(
            target,
            days=60,
            force_refresh=force_refresh,
        )
    latest_close = _latest_close_from_price_history(price_history)
    shares_outstanding, _, _ = latest_share_count(target)
    market_cap = (latest_close * shares_outstanding) if latest_close is not None and shares_outstanding else None
    market_cap_label = market_cap_label_hint if market_cap_label_hint != "n/a" else _format_market_cap_label(market_cap)

    return {
        "symbol": target,
        "company_name": company_name,
        "market_cap_label": market_cap_label,
        "sparkline_data_uri": sparkline_hint or _sparkline_data_uri(price_history),
    }


def _load_recent_news_cached(
    cfg: AppConfig,
    ticker: str,
    days: int = 14,
    limit: int = 8,
    force_refresh: bool = False,
) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_recent_news",
        cfg=cfg,
        source="news",
        ticker=ticker,
        days=days,
        limit=limit,
        force_refresh=force_refresh,
    )


def _load_attention_context_cached(
    cfg: AppConfig,
    ticker: str,
    force_refresh: bool = False,
) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_attention_context",
        cfg=cfg,
        source="news",
        ticker=ticker,
        force_refresh=force_refresh,
    )


def _load_attention_ticker_snapshot_map_uncached() -> dict[str, dict[str, object]]:
    frame, _ = load_latest_dataset_frame("attention_ticker_snapshots_1d")
    if frame.empty or "symbol" not in frame.columns:
        return {}
    rows = frame.copy()
    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
    rows = rows[rows["symbol"].ne("")].drop_duplicates(subset=["symbol"], keep="first")
    out: dict[str, dict[str, object]] = {}
    for record in rows.to_dict(orient="records"):
        symbol = str(record.get("symbol") or "").upper().strip()
        if symbol:
            out[symbol] = record
    return out


@st.cache_data(ttl=900, show_spinner=False)
def _load_attention_ticker_snapshot_map_memoized() -> dict[str, dict[str, object]]:
    return _load_attention_ticker_snapshot_map_uncached()


def _load_attention_ticker_snapshot_map_cached(force_refresh: bool = False) -> dict[str, dict[str, object]]:
    if force_refresh:
        return _load_attention_ticker_snapshot_map_uncached()
    return _load_attention_ticker_snapshot_map_memoized()


def _load_attention_ticker_snapshot_uncached(
    cfg: AppConfig,
    ticker: str,
) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_attention_ticker_snapshot",
        cfg=cfg,
        source="news",
        ticker=ticker,
        force_refresh=True,
    )


@st.cache_data(ttl=900, show_spinner=False)
def _load_attention_ticker_snapshot_memoized(
    cfg: AppConfig,
    ticker: str,
) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_attention_ticker_snapshot",
        cfg=cfg,
        source="news",
        ticker=ticker,
        force_refresh=False,
    )


def _load_attention_ticker_snapshot_cached(
    cfg: AppConfig,
    ticker: str,
    force_refresh: bool = False,
) -> dict[str, object]:
    if force_refresh:
        return _load_attention_ticker_snapshot_uncached(cfg, ticker)
    return _load_attention_ticker_snapshot_memoized(cfg, ticker)


def _is_stale_fallback_background_payload(payload: dict[str, object] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    recent_headlines = list(payload.get("recent_headlines") or [])
    if recent_headlines:
        return False
    source_trace = payload.get("source_trace")
    if not isinstance(source_trace, dict):
        return False
    headline_count = int(source_trace.get("headline_count") or 0)
    relevant_news_count = int(source_trace.get("relevant_news_count") or 0)
    return headline_count > 0 and relevant_news_count <= 0


def _load_attention_ticker_background_uncached(
    cfg: AppConfig,
    ticker: str,
    *,
    force_refresh: bool = True,
) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_attention_ticker_background",
        cfg=cfg,
        source="news",
        ticker=ticker,
        force_refresh=force_refresh,
    )


@st.cache_data(ttl=120, show_spinner=False)
def _load_attention_ticker_background_memoized(
    cfg: AppConfig,
    ticker: str,
) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_attention_ticker_background",
        cfg=cfg,
        source="news",
        ticker=ticker,
        force_refresh=False,
    )


def _load_attention_ticker_background_cached(
    cfg: AppConfig,
    ticker: str,
    force_refresh: bool = False,
) -> dict[str, object]:
    if force_refresh:
        return _load_attention_ticker_background_uncached(cfg, ticker)
    payload = _load_attention_ticker_background_memoized(cfg, ticker)
    if _is_stale_fallback_background_payload(payload):
        return _load_attention_ticker_background_uncached(
            cfg,
            ticker,
            force_refresh=False,
        )
    return payload


def _load_attention_home_1d_uncached(
    cfg: AppConfig,
) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_attention_home_1d",
        cfg=cfg,
        source="equities",
        force_refresh=True,
    )


@st.cache_data(ttl=120, show_spinner=False)
def _load_attention_home_1d_memoized(cfg: AppConfig) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_attention_home_1d",
        cfg=cfg,
        source="equities",
        force_refresh=False,
    )


def _load_attention_home_1d_cached(
    cfg: AppConfig,
    force_refresh: bool = False,
) -> dict[str, object]:
    if force_refresh:
        return _load_attention_home_1d_uncached(cfg)
    return _load_attention_home_1d_memoized(cfg)


def _load_attention_home_1d(
    cfg: AppConfig,
    force_refresh: bool = False,
) -> dict[str, object]:
    return _load_attention_home_1d_cached(
        cfg,
        force_refresh=force_refresh,
    )


def _load_attention_research_bundle_cached(
    cfg: AppConfig,
    bundle_id: str,
    force_refresh: bool = False,
) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_attention_research_bundle",
        cfg=cfg,
        source="news",
        bundle_id=bundle_id,
        force_refresh=force_refresh,
    )


def _safe_load_attention_research_bundle_cached(
    cfg: AppConfig,
    bundle_id: str,
    force_refresh: bool = False,
) -> dict[str, object]:
    normalized_bundle_id = str(bundle_id or "").strip()
    if not normalized_bundle_id:
        return {}
    try:
        return _load_attention_research_bundle_cached(
            cfg,
            normalized_bundle_id,
            force_refresh=force_refresh,
        )
    except Exception:
        return {}


def _load_fred_dashboard_cached(api_key: str, years: int, force_refresh: bool = False) -> dict[str, object]:
    return _resolve_data_access_payload(
        "resolve_fred_dashboard",
        fred_api_key=api_key,
        source="fred",
        years=years,
        force_refresh=force_refresh,
    )


def _load_attention_feed_cached(
    cfg: AppConfig | None = None,
    *,
    dataset_name: str = "attention_feed",
    source: str = "derivatives",
    limit: int = 10,
    entity_ids: list[str] | None = None,
    horizons: list[str] | None = None,
    statuses: list[str] | None = None,
    sensitivity: str | None = None,
    min_attention_score: float | None = None,
    residual_zscore_threshold: float | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    return _resolve_data_access_payload(
        "resolve_attention_feed",
        cfg=cfg,
        source=source,
        dataset_name=dataset_name,
        limit=limit,
        entity_ids=entity_ids,
        horizons=horizons,
        statuses=statuses,
        sensitivity=sensitivity,
        min_attention_score=min_attention_score,
        residual_zscore_threshold=residual_zscore_threshold,
        force_refresh=force_refresh,
    )


def _load_attention_rollups_cached(
    cfg: AppConfig | None = None,
    *,
    dataset_name: str = "attention_rollups",
    source: str = "derivatives",
    rollup_type: str | None = None,
    horizons: list[str] | None = None,
    statuses: list[str] | None = None,
    sensitivity: str | None = None,
    min_attention_score: float | None = None,
    residual_zscore_threshold: float | None = None,
    high_priority_threshold: float | None = None,
    limit: int = 10,
    force_refresh: bool = False,
) -> pd.DataFrame:
    return _resolve_data_access_payload(
        "resolve_attention_rollups",
        cfg=cfg,
        source=source,
        dataset_name=dataset_name,
        rollup_type=rollup_type,
        horizons=horizons,
        statuses=statuses,
        sensitivity=sensitivity,
        min_attention_score=min_attention_score,
        residual_zscore_threshold=residual_zscore_threshold,
        high_priority_threshold=high_priority_threshold,
        limit=limit,
        force_refresh=force_refresh,
    )
