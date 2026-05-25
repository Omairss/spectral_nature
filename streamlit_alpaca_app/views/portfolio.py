from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from compute.portfolio import normalize_timeseries_view
from presentation import dashboard_loaders
from services import auth_service
from services.alpaca_api import AlpacaAPI, AlpacaAPIError
from services.analytics import build_metric_bar, build_portfolio_vs_benchmarks_fig
from services.config import AppConfig
from views._shared import (
    PORTFOLIO_PERFORMANCE_SECTION,
    PORTFOLIO_SECTION,
    _current_user_share_fraction,
    _has_live_api,
    _log_event,
    _responsive_columns,
    _timed,
    _to_float,
)


def _render_portfolio_section(
    cfg: AppConfig,
    api: AlpacaAPI | None,
    *,
    force_data_refresh: bool,
    current_user: auth_service.UserContext | None,
    sidebar_status: object,
    sidebar_buying_power: object,
) -> None:
    header_cols = _responsive_columns([3.2, 1.6])
    with header_cols[0]:
        st.title(PORTFOLIO_SECTION)
        if current_user is not None and not current_user.can_view_full_portfolio:
            st.caption(f"Viewing your {_current_user_share_fraction() * 100:.2f}% economic share of the master portfolio.")
    with header_cols[1]:
        period = st.selectbox("History Period", ["1M", "3M", "6M", "1Y", "2Y", "5Y"], index=3, key="portfolio_overview_period")
    if not _has_live_api(api, f"{PORTFOLIO_SECTION} requires a working live account connection."):
        st.info("Restore the live account connection to load positions, portfolio history, and benchmark comparisons.")
    else:
        try:
            with st.spinner("Loading account summary..."):
                with _timed("get_account"):
                    account = dashboard_loaders._load_account_cached(cfg, force_refresh=force_data_refresh)
            if current_user is not None and not current_user.can_view_full_portfolio:
                sidebar_status.metric("Access", "Investor")
                sidebar_buying_power.metric("Portfolio Share", f"{_current_user_share_fraction() * 100:.2f}%")
            else:
                sidebar_status.metric("Account Status", str(account.get("status", "unknown")).upper())
                sidebar_buying_power.metric("Buying Power", f"${_to_float(account, 'buying_power'):,.2f}")
        except AlpacaAPIError as exc:
            _log_event("get_account_failed", error=str(exc)[:200])
            st.warning(f"Could not load account summary: {exc}")
            account = {}
            if current_user is not None and not current_user.can_view_full_portfolio:
                sidebar_status.metric("Access", "ERROR")
                sidebar_buying_power.metric("Portfolio Share", f"{_current_user_share_fraction() * 100:.2f}%")
            else:
                sidebar_status.metric("Account Status", "ERROR")
                sidebar_buying_power.metric("Buying Power", "Unavailable")

        col1, col2, col3, col4 = _responsive_columns(4)
        col1.metric("Equity", f"${_to_float(account, 'equity'):,.2f}")
        col2.metric("Cash", f"${_to_float(account, 'cash'):,.2f}")
        col3.metric("Portfolio Value", f"${_to_float(account, 'portfolio_value'):,.2f}")
        if current_user is not None and not current_user.can_view_full_portfolio:
            col4.metric("Portfolio Share", f"{_current_user_share_fraction() * 100:.2f}%")
        else:
            col4.metric("Daytrade Count", str(account.get("daytrade_count", "0")))

        try:
            with st.spinner("Loading positions..."):
                with _timed("get_positions"):
                    positions = dashboard_loaders._load_positions_cached(cfg, force_refresh=force_data_refresh)
        except AlpacaAPIError as exc:
            _log_event("get_positions_failed", error=str(exc)[:200])
            st.warning(f"Could not load positions: {exc}")
            positions = pd.DataFrame()

        left, right = _responsive_columns([1.2, 1.8], gap="large")

        with left:
            st.subheader("Current Positions")
            if positions.empty:
                st.info("No open positions.")
            else:
                show_cols = [
                    c
                    for c in [
                        "symbol",
                        "effective_qty",
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
                        raw = dashboard_loaders._load_timeseries_cached(cfg, period, force_refresh=force_data_refresh)
                if current_user is not None and not current_user.can_view_full_portfolio and not raw.empty and "portfolio" in raw.columns:
                    dollar_fig = px.line(
                        raw,
                        x="timestamp",
                        y="portfolio",
                        template="plotly_dark",
                        title="Your Dollar Equity",
                    )
                    st.plotly_chart(dollar_fig, use_container_width=True)
                norm = normalize_timeseries_view(raw)
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
                    roc = dashboard_loaders._load_holding_roc_cached(cfg, symbols, force_refresh=force_data_refresh)
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


def _render_portfolio_performance_section(
    cfg: AppConfig,
    api: AlpacaAPI | None,
    *,
    force_data_refresh: bool,
    current_user: auth_service.UserContext | None,
) -> None:
    header_cols = _responsive_columns([3.2, 1.6])
    with header_cols[0]:
        st.title(PORTFOLIO_PERFORMANCE_SECTION)
        if current_user is not None and not current_user.can_view_full_portfolio:
            st.caption("Return-based metrics match the master portfolio while your ownership share remains fixed.")
    with header_cols[1]:
        period = st.selectbox("History Period", ["1M", "3M", "6M", "1Y", "2Y", "5Y"], index=3, key="performance_period")
    if not _has_live_api(
        api,
        f"{PORTFOLIO_PERFORMANCE_SECTION} requires a working portfolio history snapshot or live account connection.",
        allow_pipeline=True,
    ):
        st.info("Restore the live account connection or portfolio history snapshot to compute portfolio and benchmark performance.")
    else:
        try:
            with st.spinner("Loading performance data..."):
                with _timed("load_portfolio_timeseries", period=period):
                    raw_timeseries = dashboard_loaders._load_timeseries_cached(cfg, period, force_refresh=force_data_refresh)
                with _timed("load_portfolio_performance", period=period):
                    perf = dashboard_loaders._load_portfolio_performance_cached(cfg, period, force_refresh=force_data_refresh)
        except AlpacaAPIError as exc:
            _log_event("load_portfolio_performance_failed", error=str(exc)[:200])
            st.error(f"Could not load performance data: {exc}")
            st.stop()

        if perf.empty:
            st.info("No performance data available.")
            st.stop()
        if current_user is not None and not current_user.can_view_full_portfolio and not raw_timeseries.empty and "portfolio" in raw_timeseries.columns:
            dollar_fig = px.line(
                raw_timeseries,
                x="timestamp",
                y="portfolio",
                template="plotly_dark",
                title="Your Dollar Equity Over Time",
            )
            st.plotly_chart(dollar_fig, use_container_width=True)
        st.dataframe(perf, use_container_width=True, hide_index=True)

        metric = st.selectbox(
            "Metric",
            ["annual_return", "sharpe_ratio", "beta_vs_spy", "alpha_vs_spy", "max_drawdown"],
        )
        st.plotly_chart(build_metric_bar(perf, metric), use_container_width=True)
