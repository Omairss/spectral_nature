from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from presentation import dashboard_loaders
from services.alpaca_api import AlpacaAPI
from services.config import AppConfig
from services.market import (
    business_focus_description,
    business_focus_options,
    business_focus_universe,
    commodity_focus_description,
    commodity_focus_options,
    commodity_focus_universe,
    extend_symbol_universe,
)
from services.page_agentic_summary import market_summary_context
from views._shared import (
    MARKET_EXPLORER_SECTION,
    MARKET_MOMENTUM_HORIZON_COLUMNS,
    MARKET_MOMENTUM_HORIZON_LABELS,
    STOCK_INVESTIGATOR_SECTION,
    _has_live_api,
    _open_attention_target,
    _prime_widget_choice,
    _render_page_agentic_summary_panel,
    _render_selectable_ticker_table,
    _responsive_columns,
    _set_workspace_ticker,
)


def _render_market_explorer_section(
    cfg: AppConfig,
    api: AlpacaAPI | None,
    *,
    force_data_refresh: bool,
) -> None:
    st.title(MARKET_EXPLORER_SECTION)
    if not _has_live_api(
        api,
        f"{MARKET_EXPLORER_SECTION} requires a working live market connection or retained market snapshots.",
        allow_pipeline=True,
    ):
        st.info("Restore the live market connection or configure retained market snapshots to scan movers and load price history.")
        return

    market_view_options = ["Markets", "Broad Markets", "Commodity Section"]
    _prime_widget_choice("market_view", market_view_options, fallback="Markets", pending_key="_pending_market_view")
    market_view = st.segmented_control(
        "Market View",
        market_view_options,
        key="market_view",
        width="stretch",
    )
    movers = pd.DataFrame()
    momentum = pd.DataFrame()
    selected_market_ticker: str | None = None
    business_filter = ""
    requested_market_ticker = str(st.session_state.get("market_selected_ticker") or "").upper().strip()
    if market_view == "Commodity Section":
        commodity_options = commodity_focus_options()
        _prime_widget_choice(
            "market_commodity_focus",
            commodity_options,
            fallback="Broad Commodity Market",
            pending_key="_pending_market_commodity_focus",
        )
        lens_cols = _responsive_columns([2.2, 3.8])
        with lens_cols[0]:
            experiment_filter = st.selectbox(
                "Commodity Filter",
                commodity_options,
                key="market_commodity_focus",
            )
        with lens_cols[1]:
            st.caption("Commodity-market lens for the experiment view.")
            st.caption(commodity_focus_description(experiment_filter))
        experiment_symbols = commodity_focus_universe(experiment_filter)
        from views.experiments import _render_market_opportunity_experiments
        _render_market_opportunity_experiments(
            cfg,
            force_data_refresh,
            market_view,
            "Commodity Filter",
            experiment_filter,
            experiment_symbols,
        )
    elif market_view == "Broad Markets":
        business_options = business_focus_options()
        _prime_widget_choice(
            "market_business_filter",
            business_options,
            fallback="All Market",
            pending_key="_pending_market_business_filter",
        )
        lens_cols = _responsive_columns([2.2, 3.8])
        with lens_cols[0]:
            experiment_filter = st.selectbox(
                "Business Filter",
                business_options,
                key="market_business_filter",
            )
        with lens_cols[1]:
            st.caption(
                "Custom business lens based on what the company primarily sells, not standard sector classifications."
            )
            st.caption(business_focus_description(experiment_filter))
        experiment_symbols = business_focus_universe(experiment_filter)
        from views.experiments import _render_market_opportunity_experiments
        _render_market_opportunity_experiments(
            cfg,
            force_data_refresh,
            market_view,
            "Business Filter",
            experiment_filter,
            experiment_symbols,
        )
    else:
        business_options = business_focus_options()
        _prime_widget_choice(
            "market_business_filter",
            business_options,
            fallback="All Market",
            pending_key="_pending_market_business_filter",
        )

        lens_cols = _responsive_columns([2.2, 3.8])
        with lens_cols[0]:
            business_filter = st.selectbox(
                "Business Filter",
                business_options,
                key="market_business_filter",
            )
        with lens_cols[1]:
            st.caption(
                "Custom business lens based on what the company primarily sells, not standard sector classifications."
            )
            st.caption(business_focus_description(business_filter))
        business_symbols = extend_symbol_universe(
            business_focus_universe(business_filter),
            [requested_market_ticker] if requested_market_ticker else None,
        )
        horizon_options = list(MARKET_MOMENTUM_HORIZON_COLUMNS.keys())
        momentum_horizon = st.selectbox(
            "Momentum Horizon",
            horizon_options,
            index=horizon_options.index("1m"),
            format_func=lambda key: MARKET_MOMENTUM_HORIZON_LABELS.get(key, str(key)),
            key="market_momentum_horizon",
        )
        selected_horizon_col = MARKET_MOMENTUM_HORIZON_COLUMNS.get(momentum_horizon, "return_1m_pct")
        selected_horizon_label = MARKET_MOMENTUM_HORIZON_LABELS.get(momentum_horizon, momentum_horizon)
        opportunity_feed = dashboard_loaders._load_market_opportunity_feed_cached(
            cfg,
            business_filter=business_filter,
            selected_horizon_col=selected_horizon_col,
            selected_horizon_label=selected_horizon_label,
            symbols=business_symbols,
            limit=80,
            force_refresh=force_data_refresh,
        )

    if market_view != "Markets":
        st.stop()

    selected_horizon_col = locals().get("selected_horizon_col", "return_1m_pct")
    selected_horizon_label = locals().get("selected_horizon_label", "1 Month")
    opportunity_feed = locals().get("opportunity_feed", pd.DataFrame())

    if opportunity_feed.empty:
        st.info("No market opportunity rows matched this lens.")
    else:
        _render_page_agentic_summary_panel(
            MARKET_EXPLORER_SECTION,
            market_summary_context(
                business_filter=business_filter,
                selected_horizon_label=selected_horizon_label,
                opportunity_feed=opportunity_feed,
                movers=movers,
                momentum=momentum,
            ),
            key_prefix="market_explorer",
        )
        opportunity_column_config = {
            "symbol": st.column_config.TextColumn("Ticker", width="small"),
            "company_name": st.column_config.TextColumn("Company", width="medium"),
            "opportunity": st.column_config.TextColumn("Opportunity", width="medium"),
            "direction": st.column_config.TextColumn("Direction", width="medium"),
            "opportunity_score": st.column_config.NumberColumn("Score", format="%.1f", width="small"),
            "sparkline_3m": st.column_config.LineChartColumn(
                "Mini Chart",
                y_min=80,
                y_max=140,
                width="medium",
            ),
            "close": st.column_config.NumberColumn("Close", format="$%.2f", width="small"),
            "daily_change_pct": st.column_config.NumberColumn("1D", format="%+.1f%%", width="small"),
            selected_horizon_col: st.column_config.NumberColumn(selected_horizon_label, format="%+.1f%%", width="small"),
            "return_1w_pct": st.column_config.NumberColumn("1W", format="%+.1f%%", width="small"),
            "return_3m_pct": st.column_config.NumberColumn("3M", format="%+.1f%%", width="small"),
            "momentum_score": st.column_config.NumberColumn("Momentum", format="%.2f", width="small"),
            "momentum_roc_score": st.column_config.NumberColumn("RoC", format="%+.2f", width="small"),
            "trend_fit_gap": st.column_config.NumberColumn("Trend Gap", format="%.2f", width="small"),
            "details": st.column_config.TextColumn("Details", width="large"),
        }
        selected_market_ticker = _render_selectable_ticker_table(
            "Market Opportunity Feed",
            opportunity_feed,
            [
                "symbol",
                "company_name",
                "opportunity",
                "direction",
                "opportunity_score",
                "sparkline_3m",
                "close",
                "daily_change_pct",
                selected_horizon_col,
                "momentum_roc_score",
                "trend_fit_gap",
                "details",
            ],
            key="market_opportunity_feed",
            column_config=opportunity_column_config,
        ) or selected_market_ticker

    tree_frame = pd.DataFrame()
    if not opportunity_feed.empty and {"symbol", "volume", "daily_change_pct"}.issubset(opportunity_feed.columns):
        tree_frame = opportunity_feed.copy()
        tree_frame["volume"] = pd.to_numeric(tree_frame["volume"], errors="coerce")
        tree_frame["change_pct"] = pd.to_numeric(tree_frame["daily_change_pct"], errors="coerce")
        tree_frame = tree_frame[tree_frame["symbol"].astype(str).str.strip().ne("")]
        tree_frame = tree_frame[tree_frame["volume"].fillna(0) > 0]
    if not tree_frame.empty:
        tree = px.treemap(
            tree_frame,
            path=["symbol"],
            values="volume",
            color="change_pct",
            color_continuous_scale="RdYlGn",
            template="plotly_dark",
            title=f"Daily Movers - {business_filter} (Volume / Change %)",
        )
        st.plotly_chart(tree, use_container_width=True)
    else:
        st.info("Daily mover volume was not available, so the treemap is hidden.")

    scanned_detail_symbols: set[str] = set()
    if not opportunity_feed.empty and "symbol" in opportunity_feed.columns:
        scanned_detail_symbols.update(
            str(symbol).upper().strip()
            for symbol in opportunity_feed["symbol"].astype(str).tolist()
            if str(symbol).strip()
        )

    focus_ticker = str(st.session_state.get("market_selected_ticker") or "").upper().strip()
    if selected_market_ticker:
        focus_ticker = _set_workspace_ticker(selected_market_ticker) or focus_ticker
    elif requested_market_ticker:
        focus_ticker = _set_workspace_ticker(requested_market_ticker) or focus_ticker

    if requested_market_ticker and requested_market_ticker not in scanned_detail_symbols:
        st.caption(
            f"{requested_market_ticker} was opened from attention and pinned into the ticker handoff because it is outside the current {business_filter} scan lens."
        )

    if focus_ticker:
        handoff_cols = _responsive_columns([4.8, 1.4])
        with handoff_cols[0]:
            st.caption(
                f"Stock focus: {focus_ticker}. Open Stock Investigator for technicals, fundamentals, and company context."
            )
        with handoff_cols[1]:
            if st.button(
                "Open Stock Investigator",
                key=f"market_open_stock_investigator_{focus_ticker}",
                use_container_width=True,
            ):
                _open_attention_target(STOCK_INVESTIGATOR_SECTION, {"ticker": focus_ticker})
    else:
        st.info("Select a ticker from a market table to continue in Stock Investigator.")
