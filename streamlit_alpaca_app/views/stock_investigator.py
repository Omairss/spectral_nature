from __future__ import annotations

import importlib

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from presentation import dashboard_loaders
from services.alpaca_api import AlpacaAPIError
from services.config import AppConfig
from services.company import summarize_recent_news
from services.page_agentic_summary import stock_summary_context
from services.technicals import build_technical_figure
from views._shared import (
    MARKET_EXPLORER_SECTION,
    STOCK_INVESTIGATOR_SECTION,
    _collect_evidence_links,
    _first_substantive_company_context_line,
    _log_event,
    _market_business_filter_for_symbol,
    _open_attention_target,
    _render_compact_background_sections,
    _render_overview_fundamentals,
    _render_page_agentic_summary_panel,
    _responsive_columns,
    _responsive_two_panel,
    _set_workspace_ticker,
    _taxonomy_summary_text,
    _timed,
)

_SIGNALS_IMPORT_ERROR: str | None = None
try:
    importlib.invalidate_caches()
    _signals = importlib.import_module("services.signals")
    build_forecast_cone_figure = _signals.build_forecast_cone_figure
    build_price_channel_figure = _signals.build_price_channel_figure
    build_pullback_figure = _signals.build_pullback_figure
    build_terminal_distribution_figure = _signals.build_terminal_distribution_figure
except ModuleNotFoundError as exc:
    if exc.name != "services.signals":
        raise
    _SIGNALS_IMPORT_ERROR = str(exc)

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


def _sync_stock_investigator_ticker_from_widget() -> None:
    ticker = str(st.session_state.get("stock_investigator_ticker_widget") or "").upper().strip()
    if ticker:
        _set_workspace_ticker(ticker)


def _render_stock_investigator_workspace(
    cfg: AppConfig,
    *,
    force_data_refresh: bool,
) -> None:
    default_ticker = (
        str(st.session_state.get("stock_investigator_ticker") or "").upper().strip()
        or str(st.session_state.get("market_selected_ticker") or "").upper().strip()
        or "AAPL"
    )
    if not str(st.session_state.get("stock_investigator_ticker") or "").strip():
        _set_workspace_ticker(default_ticker)
    if not str(st.session_state.get("stock_investigator_ticker_widget") or "").strip():
        st.session_state["stock_investigator_ticker_widget"] = default_ticker

    control_cols = _responsive_columns([2.4, 2.2, 1.4])
    with control_cols[0]:
        ticker = st.text_input(
            "Ticker",
            key="stock_investigator_ticker_widget",
            placeholder="AAPL",
            on_change=_sync_stock_investigator_ticker_from_widget,
        ).upper().strip()
    with control_cols[1]:
        days = st.slider("Days", 60, 720, 365, step=30, key="stock_investigator_days")
    with control_cols[2]:
        if st.button(
            f"Open {MARKET_EXPLORER_SECTION}",
            key="stock_investigator_open_market",
            use_container_width=True,
            disabled=not bool(ticker),
        ):
            _open_attention_target(
                MARKET_EXPLORER_SECTION,
                {
                    "ticker": ticker,
                    "market_view": "Markets",
                    "business_filter": _market_business_filter_for_symbol(ticker),
                },
            )

    ticker = str(ticker or "").upper().strip()
    if not ticker:
        st.info("Enter a ticker to load technicals, company context, and fundamentals.")
        return

    if ticker != str(st.session_state.get("stock_investigator_ticker") or "").upper().strip():
        _set_workspace_ticker(ticker)

    taxonomy_summary = _taxonomy_summary_text(ticker)
    if taxonomy_summary:
        st.caption(taxonomy_summary)
    stock_agentic_summary_slot = st.empty()

    analysis_days = max(days, 3650)
    try:
        with st.spinner("Loading price history..."):
            with _timed("load_price_history", ticker=ticker, days=analysis_days):
                price = dashboard_loaders._load_price_history_cached(
                    cfg,
                    ticker,
                    analysis_days,
                    force_refresh=force_data_refresh,
                )
    except AlpacaAPIError as exc:
        _log_event("load_price_history_failed", ticker=ticker, error=str(exc)[:200])
        st.warning(f"Could not load price history: {exc}")
        price = pd.DataFrame()

    st.subheader(f"{ticker} Technicals")
    signal_summary: dict[str, object] = {}
    forecast: dict[str, object] = {}
    if not price.empty:
        try:
            recent_cutoff = price["timestamp"].max() - pd.Timedelta(days=days)
            visible_price = price[price["timestamp"] >= recent_cutoff].copy()
            if visible_price.empty:
                visible_price = price.tail(min(len(price), 220)).copy()
            st.plotly_chart(
                build_technical_figure(visible_price, f"Technical View - {ticker}"),
                use_container_width=True,
                key=f"stock_investigator_{ticker}_technical_chart",
            )
        except Exception as exc:
            _log_event("stock_investigator_technical_overview_failed", ticker=ticker, error=str(exc)[:200])
            st.caption("Technical overview chart unavailable for this ticker.")

        with st.expander("Show Recent Price Data", expanded=False):
            st.dataframe(price.tail(40), use_container_width=True, hide_index=True)

        if _SIGNALS_IMPORT_ERROR:
            st.warning(
                "Advanced signal charts are temporarily unavailable because the optional signals module "
                f"did not load: {_SIGNALS_IMPORT_ERROR}"
            )

        signal_frame = dashboard_loaders._load_technical_signal_history_cached(
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

            signal_summary = dashboard_loaders._load_technical_signal_summary_cached(
                cfg,
                ticker,
                signal_frame,
                force_refresh=force_data_refresh,
            )
            forecast = dashboard_loaders._load_forecast_next_week_cached(
                cfg,
                ticker,
                days=max(days, 180),
                signal_frame=signal_frame,
                force_refresh=force_data_refresh,
            )

            metric_cols = _responsive_columns(6)
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

            channel_left, channel_right = _responsive_two_panel()
            with channel_left:
                st.plotly_chart(
                    build_price_channel_figure(visible_signal_frame, ticker),
                    use_container_width=True,
                    key=f"stock_investigator_{ticker}_price_channel_chart",
                )
            with channel_right:
                st.plotly_chart(
                    build_pullback_figure(visible_signal_frame, ticker),
                    use_container_width=True,
                    key=f"stock_investigator_{ticker}_pullback_chart",
                )

            forecast_left, forecast_right = _responsive_two_panel()
            with forecast_left:
                if forecast:
                    st.plotly_chart(
                        build_forecast_cone_figure(visible_signal_frame, forecast, ticker),
                        use_container_width=True,
                        key=f"stock_investigator_{ticker}_forecast_cone_chart",
                    )
                else:
                    st.info("Not enough history to build the next-week probability model.")
            with forecast_right:
                if forecast:
                    st.plotly_chart(
                        build_terminal_distribution_figure(forecast, ticker),
                        use_container_width=True,
                        key=f"stock_investigator_{ticker}_terminal_distribution_chart",
                    )
                    st.caption(
                        f"Analog model: {forecast.get('analog_count', 0)} similar historical setups, "
                        f"expected 5-day return {forecast.get('expected_return_pct', np.nan):.2f}%."
                    )
                else:
                    st.info("Forecast distribution unavailable for this ticker.")
    else:
        st.info("No price history was available for this ticker.")

    try:
        with st.spinner("Loading company profile and recent news..."):
            with _timed("load_market_detail_context", ticker=ticker):
                news_payload = dashboard_loaders._load_recent_news_cached(
                    cfg,
                    ticker,
                    days=14,
                    limit=6,
                    force_refresh=force_data_refresh,
                )
                attention_context = dashboard_loaders._load_attention_context_cached(
                    cfg,
                    ticker,
                    force_refresh=force_data_refresh,
                )
                background_payload = dashboard_loaders._load_attention_ticker_background_cached(
                    cfg,
                    ticker,
                    force_refresh=force_data_refresh,
                )
    except Exception as exc:
        _log_event("load_market_detail_context_failed", ticker=ticker, error=str(exc)[:200])
        st.warning(f"Could not load company context for {ticker}: {exc}")
        news_payload = {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}
        attention_context = {
            "context_story_text": "",
            "llm_headline": "",
            "llm_summary_text": "",
        }
        background_payload = {}

    st.subheader(f"{ticker} Company Context")
    news_summary = summarize_recent_news(ticker, news_payload)
    summary_lines = [
        str(item).strip()
        for item in list(news_summary.get("summary_lines") or [])
        if str(item).strip()
    ]
    llm_headline = str(attention_context.get("llm_headline") or "").strip()
    llm_summary_text = str(attention_context.get("llm_summary_text") or "").strip()
    primary_context_text = str(attention_context.get("context_story_text") or "").strip()
    description_text = str(background_payload.get("description_text") or "").strip()
    company_background_text = str(background_payload.get("company_background_text") or "").strip()
    background_summary = _first_substantive_company_context_line(
        [
            llm_summary_text,
            primary_context_text,
            company_background_text,
            str(background_payload.get("llm_summary_text") or "").strip(),
            description_text,
        ]
    )
    what_happened_summary = _first_substantive_company_context_line(
        [
            llm_headline,
            *summary_lines,
            description_text,
        ]
    )
    evidence_links = _collect_evidence_links(
        recent_headlines=list(background_payload.get("recent_headlines") or []),
        articles=news_summary.get("articles", pd.DataFrame()),
        limit=8,
    )
    with stock_agentic_summary_slot.container():
        _render_page_agentic_summary_panel(
            STOCK_INVESTIGATOR_SECTION,
            stock_summary_context(
                ticker=ticker,
                taxonomy_summary=taxonomy_summary,
                signal_summary=signal_summary,
                forecast=forecast,
                news_summary=news_summary,
                attention_context=attention_context,
                background_payload=background_payload,
            ),
            key_prefix="stock_investigator",
        )

    _render_compact_background_sections(
        ticker,
        background_summary=background_summary,
        what_happened_summary=what_happened_summary,
        evidence_links=evidence_links,
        key_prefix=f"stock_investigator_{ticker}_background",
    )

    st.subheader(f"{ticker} Fundamentals")
    _render_overview_fundamentals(
        cfg,
        ticker,
        force_data_refresh=force_data_refresh,
        asof_time_utc=background_payload.get("asof_time_utc"),
        key_prefix=f"stock_investigator_{ticker}_fundamentals",
    )
