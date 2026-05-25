from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from presentation import dashboard_loaders
from services.alpaca_api import AlpacaAPI, AlpacaAPIError
from services.config import AppConfig
from services.options import rank_options
from views._shared import (
    _has_live_api,
    _log_event,
    _prepare_scatter_size,
    _responsive_columns,
    _responsive_two_panel,
    _timed,
)


def _render_option_strategizer_section(
    cfg: AppConfig,
    api: AlpacaAPI | None,
    *,
    force_data_refresh: bool,
) -> None:
    st.title("Option Strategizer")
    ticker = st.text_input("Ticker", value="AAPL", key="opt_ticker").upper().strip()

    if not ticker or not _has_live_api(
        api,
        "Option Strategizer requires a working live market connection or retained market snapshots.",
        allow_pipeline=True,
    ):
        return

    spot_price = np.nan
    try:
        with _timed("option_reference_price", ticker=ticker):
            reference_frame = dashboard_loaders._load_price_history_cached(cfg, ticker, days=30, force_refresh=force_data_refresh)
        if not reference_frame.empty and "close" in reference_frame.columns:
            spot_price = float(pd.to_numeric(reference_frame["close"], errors="coerce").dropna().iloc[-1])
    except Exception as exc:
        _log_event("option_reference_price_failed", ticker=ticker, error=str(exc)[:200])

    try:
        with st.spinner("Loading option expirations..."):
            with _timed("load_option_chain", ticker=ticker):
                expirations, _, _ = dashboard_loaders._load_option_chain_cached(
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
        return

    exp = st.selectbox("Expiration", expirations)
    try:
        with st.spinner("Loading option quotes..."):
            with _timed("load_option_chain_expiration", ticker=ticker, expiration=exp):
                _, calls, puts = dashboard_loaders._load_option_chain_cached(
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

    c1, c2 = _responsive_two_panel()
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
    if not np.isfinite(spot_price):
        st.info("A recent stock price was not available, so the Greek-based scenario selector is hidden for this ticker.")
        return

    st.caption(
        "Scenario model uses delta, gamma, and theta to approximate option value at your target date, "
        "then scores contracts for projected return, leverage, lower decay, lower cost, and liquidity."
    )
    control_cols = _responsive_columns(3)
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
                surface = dashboard_loaders._load_option_surface_cached(
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
        return

    candidate_payload = dashboard_loaders._load_option_candidates_cached(
        cfg,
        ticker,
        expected_price=float(expected_price),
        horizon_days=int(scenario_days),
        underlying_price=float(spot_price),
        surface=surface,
        force_refresh=force_data_refresh,
    )
    candidates = candidate_payload.get("candidates", pd.DataFrame())
    summary = candidate_payload.get("summary", {})

    if candidates.empty:
        st.info("No option candidates had enough quote and Greek data for scenario analysis.")
        return

    best = candidates.iloc[0]
    metric_cols = _responsive_columns(5)
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
