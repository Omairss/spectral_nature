from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from compute.analytics import performance_table
from compute.portfolio import filter_portfolio_timeseries_period, normalize_timeseries_view
from presentation import dashboard_loaders
from services import auth_service
from services import robinhood_main
from services import robinhood_main_analytics
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


_MAIN_PENDING_AUTH_KEY = "_robinhood_main_pending_auth"
_MAIN_STOP_POSITIONS_KEY = "_robinhood_main_stop_positions"
_MAIN_STOP_REVIEW_KEY = "_robinhood_main_stop_review"
_MAIN_OPTION_HISTORY_KEY = "_robinhood_main_option_history"
_MAIN_SET_SNAPSHOT_KEY = "_robinhood_main_opportunity_snapshot"
_MAIN_TRADING_ACCOUNTS_KEY = "_robinhood_main_trading_accounts"
_MAIN_BASKET_REVIEW_KEY = "_robinhood_main_basket_review"


def _clear_main_account_session() -> None:
    for key in (
        _MAIN_PENDING_AUTH_KEY,
        _MAIN_STOP_POSITIONS_KEY,
        _MAIN_STOP_REVIEW_KEY,
        _MAIN_OPTION_HISTORY_KEY,
        _MAIN_SET_SNAPSHOT_KEY,
        _MAIN_TRADING_ACCOUNTS_KEY,
        _MAIN_BASKET_REVIEW_KEY,
    ):
        st.session_state.pop(key, None)


def _finish_main_account_connection(
    current_user: auth_service.UserContext,
    authenticated: dict[str, object],
) -> None:
    robinhood_main.save_authentication(user_id=current_user.user_id, authenticated=authenticated)
    robinhood_main.link_portfolio(user_id=current_user.user_id, portfolio_id=current_user.portfolio_id)
    _clear_main_account_session()


def _render_main_data_status(
    current_user: auth_service.UserContext | None,
    snapshot: dict[str, object] | None,
) -> bool:
    if current_user is None:
        return False
    status = robinhood_main.portfolio_data_status(
        user_id=current_user.user_id,
        portfolio_id=current_user.portfolio_id,
    )
    state = str(status.get("state") or "")
    if state == "in_progress":
        st.info("Robinhood portfolio refresh · In progress")
    if snapshot is not None:
        return True
    if state == "failed":
        st.error("Robinhood portfolio refresh failed.")
        return True
    if state in {"in_progress", "waiting", "unavailable"}:
        if state == "waiting":
            st.info("Robinhood portfolio refresh · Waiting to start")
        elif state == "unavailable":
            st.warning("Robinhood portfolio data is unavailable.")
        return True
    return False


def _render_main_account_connection(current_user: auth_service.UserContext | None) -> None:
    if current_user is None or not current_user.is_admin:
        return
    status = robinhood_main.connection_status(
        user_id=current_user.user_id,
        portfolio_id=current_user.portfolio_id,
    )
    connected = status.get("status") == "connected"
    with st.expander("Robinhood main accounts", expanded=not connected):
        if connected:
            labels = [str(item) for item in list(status.get("account_labels") or []) if str(item).strip()]
            st.write(", ".join(labels) if labels else f"{int(status.get('account_count') or 0)} accounts")
            refresh_col, disconnect_col = _responsive_columns(2)
            if refresh_col.button("Refresh main accounts", key="robinhood_main_refresh", use_container_width=True):
                try:
                    robinhood_main.request_portfolio_refresh(
                        user_id=current_user.user_id,
                        portfolio_id=current_user.portfolio_id,
                    )
                except robinhood_main.RobinhoodMainError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.pop(_MAIN_STOP_POSITIONS_KEY, None)
                    st.session_state.pop(_MAIN_STOP_REVIEW_KEY, None)
                    st.rerun()
            if disconnect_col.button("Disconnect", key="robinhood_main_disconnect", use_container_width=True):
                robinhood_main.disconnect(
                    user_id=current_user.user_id,
                    portfolio_id=current_user.portfolio_id,
                )
                _clear_main_account_session()
                st.rerun()
            return

        pending = st.session_state.get(_MAIN_PENDING_AUTH_KEY)
        if isinstance(pending, dict):
            challenge_type = str(pending.get("challenge_type") or "prompt").lower()
            st.write("Approve the sign-in in Robinhood, then finish the connection.")
            verification_code = ""
            if challenge_type in {"sms", "email"}:
                verification_code = st.text_input(
                    f"{challenge_type.title()} code",
                    key="robinhood_main_verification_code",
                )
            finish_col, restart_col = _responsive_columns(2)
            if finish_col.button("Finish connection", key="robinhood_main_finish", type="primary", use_container_width=True):
                try:
                    outcome = robinhood_main.complete_authentication(
                        username=str(pending.get("username") or ""),
                        password=str(pending.get("password") or ""),
                        pending=dict(pending.get("verification") or {}),
                        verification_code=verification_code,
                    )
                    if outcome.get("status") == "authenticated":
                        _finish_main_account_connection(current_user, outcome)
                        st.rerun()
                    else:
                        pending["verification"] = outcome
                        st.session_state[_MAIN_PENDING_AUTH_KEY] = pending
                        st.info("Robinhood approval is still pending.")
                except robinhood_main.RobinhoodMainError as exc:
                    st.error(str(exc))
            if restart_col.button("Start again", key="robinhood_main_restart", use_container_width=True):
                st.session_state.pop(_MAIN_PENDING_AUTH_KEY, None)
                st.rerun()
            return

        with st.form("robinhood_main_login_form", clear_on_submit=False):
            username = st.text_input("Robinhood email", autocomplete="username")
            password = st.text_input("Robinhood password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Connect main accounts", type="primary", use_container_width=True)
        if submitted:
            try:
                outcome = robinhood_main.start_authentication(
                    username=username,
                    password=password,
                    device_token=robinhood_main.stored_device_token(user_id=current_user.user_id),
                )
                if outcome.get("status") == "authenticated":
                    _finish_main_account_connection(current_user, outcome)
                    st.rerun()
                else:
                    st.session_state[_MAIN_PENDING_AUTH_KEY] = {
                        "username": username,
                        "password": password,
                        "verification": outcome,
                        "challenge_type": outcome.get("challenge_type"),
                    }
                    st.rerun()
            except robinhood_main.RobinhoodMainError as exc:
                st.error(str(exc))


def _render_batch_stop_losses(current_user: auth_service.UserContext | None) -> None:
    if current_user is None or not current_user.is_admin:
        return
    status = robinhood_main.connection_status(
        user_id=current_user.user_id,
        portfolio_id=current_user.portfolio_id,
    )
    if not status.get("linked") or not status.get("execution_enabled"):
        return

    st.subheader("Batch stop losses")
    if st.button("Load positions", key="robinhood_main_load_stop_positions"):
        try:
            rows = robinhood_main.stop_loss_positions(
                user_id=current_user.user_id,
                portfolio_id=current_user.portfolio_id,
            )
            frame = pd.DataFrame(rows)
            if not frame.empty:
                frame.insert(0, "protect", True)
                frame["stop_price"] = (pd.to_numeric(frame["current_price"], errors="coerce") * 0.9).round(2)
            st.session_state[_MAIN_STOP_POSITIONS_KEY] = frame
            st.session_state.pop(_MAIN_STOP_REVIEW_KEY, None)
        except robinhood_main.RobinhoodMainError as exc:
            st.error(str(exc))

    frame = st.session_state.get(_MAIN_STOP_POSITIONS_KEY)
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        edited = st.data_editor(
            frame,
            key="robinhood_main_stop_editor",
            use_container_width=True,
            hide_index=True,
            disabled=["position_key", "account", "symbol", "current_price"],
            column_config={
                "position_key": None,
                "protect": st.column_config.CheckboxColumn("Protect"),
                "account": "Account",
                "symbol": "Symbol",
                "quantity": st.column_config.NumberColumn("Shares", min_value=0.000001, format="%.6f"),
                "current_price": st.column_config.NumberColumn("Current", format="$%.2f"),
                "stop_price": st.column_config.NumberColumn("Stop price", min_value=0.000001, format="$%.4f"),
            },
        )
        selected = edited[edited["protect"].fillna(False)].copy()
        if st.button(
            f"Review {len(selected)} stop orders",
            key="robinhood_main_review_stops",
            disabled=selected.empty,
            use_container_width=True,
        ):
            orders = selected[["position_key", "symbol", "quantity", "stop_price"]].to_dict(orient="records")
            try:
                review = robinhood_main.review_batch_stop_losses(
                    user_id=current_user.user_id,
                    portfolio_id=current_user.portfolio_id,
                    orders=orders,
                )
                st.session_state[_MAIN_STOP_REVIEW_KEY] = review
                st.rerun()
            except robinhood_main.RobinhoodMainError as exc:
                st.error(str(exc))

    review = st.session_state.get(_MAIN_STOP_REVIEW_KEY)
    if isinstance(review, dict):
        st.dataframe(pd.DataFrame(list(review.get("orders") or [])), use_container_width=True, hide_index=True)
        place_col, cancel_col = _responsive_columns(2)
        order_count = len(list(review.get("orders") or []))
        if place_col.button(
            f"Place {order_count} stop orders",
            key="robinhood_main_place_stops",
            type="primary",
            use_container_width=True,
        ):
            try:
                result = robinhood_main.confirm_batch_stop_losses(
                    user_id=current_user.user_id,
                    ticket_id=str(review.get("ticket_id") or ""),
                    orders=list(review.get("canonical_orders") or []),
                )
                st.session_state.pop(_MAIN_STOP_REVIEW_KEY, None)
                st.session_state.pop(_MAIN_STOP_POSITIONS_KEY, None)
                st.dataframe(pd.DataFrame(list(result.get("results") or [])), use_container_width=True, hide_index=True)
            except robinhood_main.RobinhoodMainError as exc:
                st.error(str(exc))
        if cancel_col.button("Cancel", key="robinhood_main_cancel_stops", use_container_width=True):
            robinhood_main.cancel_batch_review(
                user_id=current_user.user_id,
                ticket_id=str(review.get("ticket_id") or ""),
            )
            st.session_state.pop(_MAIN_STOP_REVIEW_KEY, None)
            st.rerun()


def _main_holdings(snapshot: dict[str, object], asset_class: str = "") -> pd.DataFrame:
    details = snapshot.get("details") if isinstance(snapshot.get("details"), dict) else {}
    frame = pd.DataFrame(list(details.get("holdings") or []))
    if asset_class and not frame.empty and "asset_class" in frame.columns:
        frame = frame[frame["asset_class"] == asset_class].copy()
    return frame.reset_index(drop=True)


def _holding_column_config() -> dict[str, object]:
    return {
        "account": "Account",
        "symbol": "Symbol",
        "description": "Contract",
        "strategy": "Strategy",
        "direction": "Direction",
        "qty": st.column_config.NumberColumn("Shares", format="%.6g"),
        "quantity": st.column_config.NumberColumn("Quantity", format="%.6g"),
        "available_quantity": st.column_config.NumberColumn("Available", format="%.6g"),
        "avg_entry_price": st.column_config.NumberColumn("Average cost", format="$%.2f"),
        "average_open_price": st.column_config.NumberColumn("Average cost", format="$%.2f"),
        "current_price": st.column_config.NumberColumn("Current", format="$%.2f"),
        "market_value": st.column_config.NumberColumn("Market value", format="$%.2f"),
        "daily_pl": st.column_config.NumberColumn("Day P/L", format="$%.2f"),
        "unrealized_pl": st.column_config.NumberColumn("Unrealized P/L", format="$%.2f"),
        "realized_pl": st.column_config.NumberColumn("Realized P/L", format="$%.2f"),
        "unrealized_plpc": st.column_config.NumberColumn("Total return", format="percent"),
        "change_today": st.column_config.NumberColumn("Today", format="percent"),
        "implied_volatility": st.column_config.NumberColumn("IV", format="percent"),
        "expirations": "Expirations",
        "expiration": "Expiration",
        "multiplier": st.column_config.NumberColumn("Multiplier", format="%.4g"),
        "open_interest": st.column_config.NumberColumn("Open interest", format="%d"),
        "volume": st.column_config.NumberColumn("Volume", format="%d"),
        "delta": st.column_config.NumberColumn("Delta", format="%.2f"),
        "gamma": st.column_config.NumberColumn("Gamma", format="%.2f"),
        "theta": st.column_config.NumberColumn("Theta", format="%.2f"),
        "vega": st.column_config.NumberColumn("Vega", format="%.2f"),
        "rho": st.column_config.NumberColumn("Rho", format="%.2f"),
    }


def _render_option_history(
    current_user: auth_service.UserContext,
    options: pd.DataFrame,
) -> None:
    choices: list[tuple[str, str]] = []
    for row in options.to_dict(orient="records"):
        for leg in list(row.get("legs") or []):
            if not isinstance(leg, dict) or not leg.get("leg_key"):
                continue
            label = (
                f"{row.get('symbol', '')} {str(leg.get('expiration') or '')} "
                f"{_to_float(leg, 'strike'):g} {str(leg.get('option_type') or '').title()} "
                f"({str(leg.get('side') or '').title()})"
            )
            choices.append((label, str(leg["leg_key"])))
    if not choices:
        return
    labels = [label for label, _ in choices]
    selected_label = st.selectbox("Option contract history", labels, key="robinhood_option_history_contract")
    selected_key = dict(choices)[selected_label]
    rows = robinhood_main.option_leg_history(
        user_id=current_user.user_id,
        portfolio_id=current_user.portfolio_id,
        leg_key=selected_key,
        span="5year",
    )
    history = pd.DataFrame(rows)
    if not history.empty:
        history["timestamp"] = pd.to_datetime(history["timestamp"], utc=True, errors="coerce")
        history["close"] = pd.to_numeric(history["close"], errors="coerce")
        history = history.dropna(subset=["timestamp", "close"])
        if not history.empty:
            figure = px.line(history, x="timestamp", y="close", title=selected_label)
            figure.update_layout(hovermode="x unified")
            st.plotly_chart(figure, use_container_width=True, key="robinhood_option_history_chart")


def _render_main_holdings(
    current_user: auth_service.UserContext,
    snapshot: dict[str, object],
    period: str,
) -> None:
    holdings = _main_holdings(snapshot)
    equities = _main_holdings(snapshot, "equity")
    options = _main_holdings(snapshot, "option")
    futures = _main_holdings(snapshot, "future")

    st.subheader("Holdings")
    equity_tab, option_tab, futures_tab = st.tabs(
        [f"Equities ({len(equities)})", f"Options ({len(options)})", f"Futures ({len(futures)})"]
    )
    with equity_tab:
        if equities.empty:
            st.info("No equity positions.")
        else:
            columns = [
                name
                for name in (
                    "account", "symbol", "direction", "qty", "available_quantity",
                    "avg_entry_price", "current_price", "market_value", "daily_pl",
                    "unrealized_pl", "unrealized_plpc", "change_today",
                )
                if name in equities.columns
            ]
            st.dataframe(
                equities[columns],
                use_container_width=True,
                hide_index=True,
                column_config=_holding_column_config(),
            )
    with option_tab:
        if options.empty:
            st.info("No option positions.")
        else:
            columns = [
                name
                for name in (
                    "account", "symbol", "description", "strategy", "direction", "quantity",
                    "expirations", "average_open_price", "market_value", "daily_pl",
                    "unrealized_pl", "unrealized_plpc", "implied_volatility", "open_interest",
                    "volume", "delta", "gamma", "theta", "vega", "rho",
                )
                if name in options.columns
            ]
            st.dataframe(
                options[columns],
                use_container_width=True,
                hide_index=True,
                column_config=_holding_column_config(),
            )
            greek_columns = [name for name in ("delta", "gamma", "theta", "vega", "rho") if name in options.columns]
            if greek_columns:
                greeks = options[["description", *greek_columns]].melt(
                    id_vars="description", var_name="greek", value_name="exposure"
                )
                greek_figure = px.bar(
                    greeks,
                    x="description",
                    y="exposure",
                    color="greek",
                    barmode="group",
                    title="Position Greek exposure",
                    labels={"description": "Contract", "exposure": "Exposure", "greek": "Greek"},
                )
                st.plotly_chart(greek_figure, use_container_width=True, key="robinhood_option_greeks_chart")
            _render_option_history(current_user, options)
    with futures_tab:
        if futures.empty:
            st.info("No futures positions.")
        else:
            columns = [
                name
                for name in (
                    "account", "symbol", "description", "direction", "quantity", "expiration",
                    "multiplier", "average_open_price", "current_price", "market_value", "daily_pl",
                    "unrealized_pl", "realized_pl", "unrealized_plpc", "change_today",
                )
                if name in futures.columns
            ]
            st.dataframe(
                futures[columns],
                use_container_width=True,
                hide_index=True,
                column_config=_holding_column_config(),
            )

    if holdings.empty:
        return
    chart_left, chart_right = _responsive_columns(2, gap="large")
    with chart_left:
        exposure = holdings.copy()
        exposure["absolute_market_value"] = pd.to_numeric(exposure.get("market_value"), errors="coerce").abs()
        exposure = exposure[exposure["absolute_market_value"] > 0]
        if not exposure.empty:
            figure = px.sunburst(
                exposure,
                path=["asset_class", "symbol"],
                values="absolute_market_value",
                color="unrealized_pl",
                color_continuous_scale="RdYlGn",
                title="Portfolio exposure",
                labels={
                    "absolute_market_value": "Absolute market value",
                    "unrealized_pl": "Unrealized P/L",
                    "asset_class": "Asset class",
                },
            )
            st.plotly_chart(figure, use_container_width=True, key="robinhood_exposure_chart")
    with chart_right:
        pnl = holdings.copy()
        pnl["unrealized_pl"] = pd.to_numeric(pnl.get("unrealized_pl"), errors="coerce")
        pnl["holding"] = pnl.get("description", pnl.get("symbol", "")).fillna(pnl.get("symbol", ""))
        pnl = pnl.dropna(subset=["unrealized_pl"]).sort_values("unrealized_pl")
        if not pnl.empty:
            figure = px.bar(
                pnl,
                x="unrealized_pl",
                y="holding",
                color="unrealized_pl",
                orientation="h",
                color_continuous_scale="RdYlGn",
                title="Unrealized P/L contribution",
                labels={"unrealized_pl": "Unrealized P/L", "holding": "Holding"},
            )
            figure.update_layout(xaxis_tickprefix="$", coloraxis_colorbar_title="Unrealized P/L")
            st.plotly_chart(figure, use_container_width=True, key="robinhood_pnl_contribution_chart")
    if not equities.empty:
        _render_equity_diagnostics(current_user, equities, period)


def _correlation_figure(correlation: pd.DataFrame, title: str) -> go.Figure | None:
    if correlation.empty:
        return None
    labels = correlation.columns.tolist()
    values = correlation.to_numpy(dtype=float)
    return go.Figure(
        go.Heatmap(
            z=values,
            x=labels,
            y=correlation.index.tolist(),
            zmin=-1,
            zmax=1,
            zmid=0,
            colorscale="RdBu_r",
            colorbar={"title": "Correlation"},
            hovertemplate="%{y} × %{x}<br>%{z:.2f}<extra></extra>",
        ),
        layout={"title": title, "height": max(460, 24 * len(labels))},
    )


def _render_equity_diagnostics(
    current_user: auth_service.UserContext,
    equities: pd.DataFrame,
    period: str,
) -> None:
    analytics = robinhood_main_analytics.equity_position_analytics(
        portfolio_id=current_user.portfolio_id,
        environment=robinhood_main.environment_name(),
        holdings=equities,
        lookback=period,
    )
    diagnostics = analytics["positions"]
    if diagnostics.empty or not (diagnostics.get("history_status") == "Ready").any():
        return

    st.subheader("Equity diagnostics")
    stats = dict(analytics.get("stats") or {})
    metric_columns = _responsive_columns(5)
    coverage = stats.get("history_coverage")
    metric_columns[0].metric("History coverage", f"{coverage:.0%}" if coverage is not None else "—")
    portfolio_volatility = stats.get("portfolio_volatility")
    metric_columns[1].metric(
        "Trailing volatility",
        f"{portfolio_volatility:.1%}" if portfolio_volatility is not None else "—",
    )
    beta = stats.get("portfolio_beta_vs_spy")
    metric_columns[2].metric("SPY beta", f"{beta:.2f}" if beta is not None else "—")
    effective_positions = stats.get("effective_positions")
    metric_columns[3].metric(
        "Effective positions",
        f"{effective_positions:.1f}" if effective_positions is not None else "—",
    )
    effective_bets = stats.get("effective_risk_bets")
    metric_columns[4].metric(
        "Independent risk bets",
        f"{effective_bets:.1f}" if effective_bets is not None else "—",
    )

    risk_tab, contribution_tab, diversification_tab, movement_tab = st.tabs(
        ["Risk and return", "Risk concentration", "Diversification", "Movement outliers"]
    )
    ready = diagnostics[diagnostics["history_status"] == "Ready"].copy()
    with risk_tab:
        risk_map = ready.dropna(subset=["annualized_volatility", "period_return", "gross_market_value"])
        if not risk_map.empty:
            figure = px.scatter(
                risk_map,
                x="annualized_volatility",
                y="period_return",
                size="gross_market_value",
                color="risk_share",
                text="symbol",
                custom_data=[
                    "allocation", "max_drawdown", "tail_loss_5pct", "beta_vs_spy",
                    "momentum_regime", "gross_market_value",
                ],
                color_continuous_scale="RdBu_r",
                color_continuous_midpoint=0,
                size_max=48,
                title=f"{period} return vs trailing one-year risk",
                labels={
                    "annualized_volatility": "Annualized volatility",
                    "period_return": f"{period} return",
                    "risk_share": "Variance share",
                },
            )
            figure.add_hline(y=0, line_width=1, line_dash="dot", line_color="#64748b")
            figure.add_vline(
                x=float(risk_map["annualized_volatility"].median()),
                line_width=1,
                line_dash="dot",
                line_color="#64748b",
            )
            figure.update_traces(
                textposition="top center",
                hovertemplate=(
                    "<b>%{text}</b><br>Return %{y:.1%}<br>Volatility %{x:.1%}"
                    "<br>Allocation %{customdata[0]:.1%}<br>Max drawdown %{customdata[1]:.1%}"
                    "<br>Daily tail loss %{customdata[2]:.1%}<br>SPY beta %{customdata[3]:.2f}"
                    "<br>Regime %{customdata[4]}<br>Market value $%{customdata[5]:,.0f}<extra></extra>"
                ),
            )
            figure.update_layout(
                xaxis_tickformat=".0%",
                yaxis_tickformat=".0%",
                coloraxis_colorbar_title="Risk share",
                height=610,
            )
            st.plotly_chart(figure, use_container_width=True, key="robinhood_equity_risk_return_chart")
        table_columns = [
            "symbol", "allocation", "period_return", "annualized_volatility", "max_drawdown",
            "tail_loss_5pct", "beta_vs_spy", "momentum_regime",
        ]
        st.dataframe(
            ready[[column for column in table_columns if column in ready.columns]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "symbol": "Symbol",
                "allocation": st.column_config.NumberColumn("Allocation", format="percent"),
                "period_return": st.column_config.NumberColumn(f"{period} return", format="percent"),
                "annualized_volatility": st.column_config.NumberColumn("Annualized volatility", format="percent"),
                "max_drawdown": st.column_config.NumberColumn("Max drawdown", format="percent"),
                "tail_loss_5pct": st.column_config.NumberColumn("Daily expected shortfall", format="percent"),
                "beta_vs_spy": st.column_config.NumberColumn("SPY beta", format="%.2f"),
                "momentum_regime": "Momentum regime",
            },
        )
    with contribution_tab:
        contribution = ready.dropna(subset=["volatility_contribution"]).sort_values(
            "volatility_contribution"
        )
        if not contribution.empty:
            figure = px.bar(
                contribution,
                x="volatility_contribution",
                y="symbol",
                color="volatility_contribution",
                orientation="h",
                color_continuous_scale="RdBu_r",
                color_continuous_midpoint=0,
                custom_data=["risk_share", "allocation", "annualized_volatility"],
                title="Contribution to portfolio volatility",
                labels={"volatility_contribution": "Annualized volatility contribution", "symbol": ""},
            )
            figure.update_traces(
                hovertemplate=(
                    "<b>%{y}</b><br>Volatility contribution %{x:.2%}"
                    "<br>Variance share %{customdata[0]:.1%}<br>Allocation %{customdata[1]:.1%}"
                    "<br>Standalone volatility %{customdata[2]:.1%}<extra></extra>"
                )
            )
            figure.update_layout(xaxis_tickformat=".1%", coloraxis_showscale=False, height=560)
            st.plotly_chart(figure, use_container_width=True, key="robinhood_equity_risk_contribution_chart")
    with diversification_tab:
        correlation = analytics["correlation"]
        components = analytics["components"]
        left, right = _responsive_columns([1.25, 0.75], gap="large")
        with left:
            figure = _correlation_figure(correlation, "Clustered one-year return correlation")
            if figure is not None:
                st.plotly_chart(figure, use_container_width=True, key="robinhood_equity_correlation_chart")
        with right:
            if not components.empty:
                component_positions = components.merge(
                    ready[["symbol", "allocation", "momentum_regime"]], on="symbol", how="left"
                )
                figure = px.scatter(
                    component_positions,
                    x="pc1",
                    y="pc2",
                    size="allocation",
                    color="momentum_regime",
                    text="symbol",
                    size_max=42,
                    title="Correlation-factor map",
                    labels={"pc1": "Primary shared factor", "pc2": "Secondary factor"},
                )
                figure.update_traces(textposition="top center")
                figure.update_layout(height=500, legend_title_text="Regime")
                st.plotly_chart(figure, use_container_width=True, key="robinhood_equity_factor_chart")
    with movement_tab:
        outliers = ready.dropna(subset=["latest_move_zscore"]).sort_values("latest_move_zscore")
        if not outliers.empty:
            figure = px.bar(
                outliers,
                x="latest_move_zscore",
                y="symbol",
                color="latest_move_zscore",
                orientation="h",
                color_continuous_scale="RdBu_r",
                color_continuous_midpoint=0,
                custom_data=["change_today", "annualized_volatility", "momentum_regime"],
                title="Latest move relative to each holding's own history",
                labels={"latest_move_zscore": "Robust standard deviations", "symbol": ""},
            )
            figure.add_vline(x=-2, line_width=1, line_dash="dot", line_color="#64748b")
            figure.add_vline(x=2, line_width=1, line_dash="dot", line_color="#64748b")
            figure.update_traces(
                hovertemplate=(
                    "<b>%{y}</b><br>Move score %{x:.2f}σ<br>Today %{customdata[0]:.1%}"
                    "<br>Volatility %{customdata[1]:.1%}<br>Regime %{customdata[2]}<extra></extra>"
                )
            )
            figure.update_layout(coloraxis_showscale=False, height=560)
            st.plotly_chart(figure, use_container_width=True, key="robinhood_equity_movement_chart")


def _contributor_sankey(
    contributions: pd.DataFrame,
    group_performance: pd.DataFrame,
    *,
    value_column: str = "contribution",
    category_column: str = "direction",
    groups_per_tail: int = 3,
    contributors_per_direction: int = 3,
) -> go.Figure | None:
    if (
        contributions.empty
        or group_performance.empty
        or value_column not in contributions.columns
        or category_column not in contributions.columns
        or not {"group_key", "return"}.issubset(group_performance.columns)
    ):
        return None
    usable_contributions = contributions.copy()
    usable_contributions[value_column] = pd.to_numeric(
        usable_contributions[value_column], errors="coerce"
    )
    usable_contributions = usable_contributions.dropna(subset=[value_column, category_column])
    usable_contributions = usable_contributions[usable_contributions[value_column] != 0]
    if usable_contributions.empty:
        return None

    performance = group_performance[["group_key", "return"]].copy()
    performance["return"] = pd.to_numeric(performance["return"], errors="coerce")
    performance = (
        performance.dropna(subset=["group_key", "return"])
        .drop_duplicates(subset=["group_key"], keep="last")
    )
    available_keys = set(usable_contributions["group_key"].astype(str))
    performance["group_key"] = performance["group_key"].astype(str)
    performance = performance[performance["group_key"].isin(available_keys)]
    if performance.empty:
        return None
    tail_size = min(
        max(1, int(groups_per_tail)),
        max(1, len(performance) // 2),
    )
    best = performance.nlargest(tail_size, "return")
    worst = performance[~performance["group_key"].isin(best["group_key"])].nsmallest(
        tail_size,
        "return",
    )
    selected_performance = pd.concat([best, worst], ignore_index=True)
    selected_keys = set(selected_performance["group_key"])
    best_keys = set(best["group_key"])
    returns_by_group = selected_performance.set_index("group_key")["return"].to_dict()
    usable_contributions = usable_contributions[
        usable_contributions["group_key"].astype(str).isin(selected_keys)
    ]
    selected_rows: list[dict[str, object]] = []
    for _, group in usable_contributions.groupby("group_key", sort=False):
        for _, direction_rows in group.groupby(category_column, sort=False):
            selected = (
                direction_rows.assign(_absolute=direction_rows[value_column].abs())
                .nlargest(max(1, int(contributors_per_direction)), "_absolute")
                .drop(columns="_absolute")
            )
            selected_rows.extend(selected.to_dict(orient="records"))
    if not selected_rows:
        return None

    node_labels: list[str] = []
    node_colors: list[str] = []
    node_index: dict[str, int] = {}

    def add_node(identity: str, label: str, color: str) -> int:
        if identity not in node_index:
            node_index[identity] = len(node_labels)
            node_labels.append(label)
            node_colors.append(color)
        return node_index[identity]

    sources: list[int] = []
    targets: list[int] = []
    values: list[float] = []
    link_colors: list[str] = []
    palette = {
        "Winners": ("#22c55e", "#86efac", "rgba(34, 197, 94, 0.48)"),
        "Detractors": ("#ef4444", "#fca5a5", "rgba(239, 68, 68, 0.48)"),
        "Risk amplifiers": ("#f97316", "#fdba74", "rgba(249, 115, 22, 0.48)"),
        "Diversifiers": ("#8b5cf6", "#c4b5fd", "rgba(139, 92, 246, 0.48)"),
        "Accelerating": ("#22c55e", "#86efac", "rgba(34, 197, 94, 0.48)"),
        "Recovery": ("#14b8a6", "#5eead4", "rgba(20, 184, 166, 0.48)"),
        "Cooling": ("#eab308", "#fde047", "rgba(234, 179, 8, 0.48)"),
        "Reversal": ("#f97316", "#fdba74", "rgba(249, 115, 22, 0.48)"),
        "Weakening": ("#ef4444", "#fca5a5", "rgba(239, 68, 68, 0.48)"),
        "Stabilizing": ("#38bdf8", "#7dd3fc", "rgba(56, 189, 248, 0.48)"),
    }
    selected_frame = pd.DataFrame(selected_rows)
    for (group_key, group_name, category), direction_rows in selected_frame.groupby(
        ["group_key", "group", category_column], sort=False
    ):
        signed_total = float(pd.to_numeric(direction_rows[value_column], errors="coerce").sum())
        default = (
            ("#22c55e", "#86efac", "rgba(34, 197, 94, 0.48)")
            if signed_total >= 0
            else ("#ef4444", "#fca5a5", "rgba(239, 68, 68, 0.48)")
        )
        category_color, ticker_color, flow_color = palette.get(str(category), default)
        group_return = returns_by_group.get(str(group_key))
        performance_tail = "Best" if str(group_key) in best_keys else "Worst"
        group_label = (
            f"{performance_tail} · {group_name} · {group_return:+.1%}"
            if group_return is not None
            else f"{performance_tail} · {group_name}"
        )
        group_node = add_node(
            f"group:{group_key}",
            group_label,
            "#22c55e" if str(group_key) in best_keys else "#ef4444",
        )
        direction_node = add_node(
            f"category:{group_key}:{category}",
            str(category),
            category_color,
        )
        total_flow = float(pd.to_numeric(direction_rows[value_column], errors="coerce").abs().sum())
        if total_flow <= 0:
            continue
        sources.append(group_node)
        targets.append(direction_node)
        values.append(total_flow)
        link_colors.append(flow_color)
        for row in direction_rows.to_dict(orient="records"):
            ticker_node = add_node(
                f"ticker:{group_key}:{row['symbol']}",
                str(row["symbol"]),
                ticker_color,
            )
            sources.append(direction_node)
            targets.append(ticker_node)
            values.append(abs(float(row[value_column])))
            link_colors.append(flow_color)
    return go.Figure(
        go.Sankey(
            arrangement="snap",
            node={"label": node_labels, "color": node_colors, "pad": 16, "thickness": 15},
            link={"source": sources, "target": targets, "value": values, "color": link_colors},
        )
    )


def _render_opportunity_sets(
    current_user: auth_service.UserContext,
    snapshot: dict[str, object],
    period: str,
) -> None:
    details = snapshot.get("details") if isinstance(snapshot.get("details"), dict) else {}
    sets = [item for item in list(details.get("opportunity_sets") or []) if isinstance(item, dict)]
    if not sets:
        return
    st.subheader("Lists and screeners")
    label_to_key: dict[str, str] = {}
    key_to_item: dict[str, dict[str, object]] = {}
    for item in sets:
        key = str(item.get("set_key") or "")
        base = f"{str(item.get('name') or 'Untitled')} · {str(item.get('kind') or '').title()}"
        label = base
        suffix = 2
        while label in label_to_key:
            label = f"{base} ({suffix})"
            suffix += 1
        label_to_key[label] = key
        key_to_item[key] = item

    comparable_labels = [
        label
        for label, key in label_to_key.items()
        if str(key_to_item[key].get("kind") or "") != "screener"
        and str(key_to_item[key].get("analytics_status") or "") == "ready"
    ]
    default_labels = comparable_labels[: min(6, len(comparable_labels))]
    selected_labels = st.multiselect(
        "Compare groups",
        comparable_labels,
        default=default_labels,
        key="robinhood_opportunity_compare",
    )
    selected_keys = [label_to_key[label] for label in selected_labels]
    comparable_keys = [label_to_key[label] for label in comparable_labels]
    analytics = robinhood_main_analytics.opportunity_analytics(
        portfolio_id=current_user.portfolio_id,
        environment=robinhood_main.environment_name(),
        lookback=period,
        group_keys=comparable_keys,
    ) if comparable_keys else {
        "series": pd.DataFrame(),
        "rankings": pd.DataFrame(),
        "contributions": pd.DataFrame(),
        "correlation": pd.DataFrame(),
    }

    group_series = analytics["series"]
    if not group_series.empty:
        group_series = group_series[group_series["group_key"].isin(selected_keys)].copy()
    if not group_series.empty:
        figure = px.line(
            group_series,
            x="timestamp",
            y="index",
            color="group",
            title=f"Equal-weight group performance · {period}",
            labels={"index": "Indexed value", "timestamp": "", "group": "Group"},
        )
        figure.add_hline(y=100, line_width=1, line_dash="dot", line_color="rgba(148,163,184,0.7)")
        figure.update_layout(hovermode="x unified", legend_title_text="")
        st.plotly_chart(figure, use_container_width=True, key="robinhood_opportunity_comparison")

    rankings = analytics["rankings"]
    contributions = analytics["contributions"]
    if not rankings.empty:
        comparison_rankings = rankings[rankings["group_key"].isin(selected_keys)].copy()
        ranking_left, ranking_right = _responsive_columns([1.0, 1.55], gap="large")
        with ranking_left:
            ranking_chart = px.scatter(
                comparison_rankings.dropna(subset=["return", "volatility"]),
                x="volatility",
                y="return",
                size="members",
                color="breadth",
                text="group",
                custom_data=[
                    "max_drawdown", "tail_loss_5pct", "dispersion", "average_correlation",
                    "effective_risk_bets", "top_three_concentration", "regime",
                ],
                color_continuous_scale="RdYlGn",
                range_color=[0, 1],
                size_max=50,
                title=f"Group risk and return · {period}",
                labels={
                    "volatility": "Annualized volatility",
                    "return": f"{period} return",
                    "breadth": "Positive breadth",
                },
            )
            ranking_chart.add_hline(y=0, line_width=1, line_dash="dot", line_color="#64748b")
            ranking_chart.update_traces(
                textposition="top center",
                hovertemplate=(
                    "<b>%{text}</b><br>Return %{y:.1%}<br>Volatility %{x:.1%}"
                    "<br>Max drawdown %{customdata[0]:.1%}<br>Daily tail loss %{customdata[1]:.1%}"
                    "<br>Member dispersion %{customdata[2]:.1%}<br>Average correlation %{customdata[3]:.2f}"
                    "<br>Independent risk bets %{customdata[4]:.1f}<br>Top-three flow %{customdata[5]:.1%}"
                    "<br>Regime %{customdata[6]}<extra></extra>"
                ),
            )
            ranking_chart.update_layout(
                xaxis_tickformat=".0%",
                yaxis_tickformat=".0%",
                coloraxis_colorbar_title="Breadth",
                height=590,
            )
            st.plotly_chart(ranking_chart, use_container_width=True, key="robinhood_opportunity_ranking")
        with ranking_right:
            lens_col, depth_col = _responsive_columns([1.5, 1.0])
            with lens_col:
                lens = st.selectbox(
                    "Flow lens",
                    ["Return drivers", "Risk concentration", "Momentum regimes"],
                    key="robinhood_opportunity_flow_lens",
                )
            maximum_tail_size = min(5, max(1, len(rankings) // 2))
            with depth_col:
                groups_per_tail = st.select_slider(
                    "Groups per side",
                    options=list(range(1, maximum_tail_size + 1)),
                    value=min(3, maximum_tail_size),
                    key="robinhood_opportunity_groups_per_tail",
                )
            flow_contract = {
                "Return drivers": ("return_contribution", "direction", f"Best and worst group return drivers · {period}"),
                "Risk concentration": ("risk_contribution", "risk_direction", f"Risk inside the best and worst groups · {period}"),
                "Momentum regimes": ("regime_intensity", "regime", f"Momentum inside the best and worst groups · {period}"),
            }
            value_column, category_column, title = flow_contract[lens]
            sankey = _contributor_sankey(
                contributions,
                rankings,
                value_column=value_column,
                category_column=category_column,
                groups_per_tail=groups_per_tail,
            )
            if sankey is not None:
                sankey.update_layout(title=title, height=590)
                st.plotly_chart(sankey, use_container_width=True, key="robinhood_opportunity_sankey")
        diagnostics_tab, overlap_tab = st.tabs(["Group diagnostics", "Group overlap"])
        with diagnostics_tab:
            diagnostic_columns = [
                "group", "return", "breadth", "volatility", "downside_deviation",
                "tail_loss_5pct", "max_drawdown", "current_drawdown", "dispersion",
                "top_three_concentration", "average_correlation", "first_component_share",
                "effective_risk_bets", "diversification_ratio", "regime", "members",
            ]
            st.dataframe(
                comparison_rankings[
                    [column for column in diagnostic_columns if column in comparison_rankings.columns]
                ],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "group": "Group",
                    "return": st.column_config.NumberColumn("Return", format="percent"),
                    "breadth": st.column_config.NumberColumn("Positive breadth", format="percent"),
                    "volatility": st.column_config.NumberColumn("Annualized volatility", format="percent"),
                    "downside_deviation": st.column_config.NumberColumn("Downside deviation", format="percent"),
                    "tail_loss_5pct": st.column_config.NumberColumn("Daily expected shortfall", format="percent"),
                    "max_drawdown": st.column_config.NumberColumn("Max drawdown", format="percent"),
                    "current_drawdown": st.column_config.NumberColumn("Current drawdown", format="percent"),
                    "dispersion": st.column_config.NumberColumn("Member dispersion", format="percent"),
                    "top_three_concentration": st.column_config.NumberColumn("Top-three flow", format="percent"),
                    "average_correlation": st.column_config.NumberColumn("Average correlation", format="%.2f"),
                    "first_component_share": st.column_config.NumberColumn("Shared-factor risk", format="percent"),
                    "effective_risk_bets": st.column_config.NumberColumn("Independent risk bets", format="%.1f"),
                    "diversification_ratio": st.column_config.NumberColumn("Diversification ratio", format="%.2f"),
                    "regime": "Momentum regime",
                    "members": st.column_config.NumberColumn("Members", format="%d"),
                },
            )
        with overlap_tab:
            comparison_names = comparison_rankings["group"].astype(str).tolist()
            comparison_correlation = analytics.get("correlation", pd.DataFrame())
            if not comparison_correlation.empty:
                comparison_names = [
                    name
                    for name in comparison_names
                    if name in comparison_correlation.index and name in comparison_correlation.columns
                ]
                comparison_correlation = comparison_correlation.loc[
                    comparison_names,
                    comparison_names,
                ]
            correlation_figure = _correlation_figure(
                comparison_correlation,
                "Clustered group-return correlation",
            )
            if correlation_figure is not None:
                st.plotly_chart(
                    correlation_figure,
                    use_container_width=True,
                    key="robinhood_opportunity_correlation",
                )

    selected_label = st.selectbox("Group constituents", list(label_to_key), key="robinhood_opportunity_set")
    selected_key = label_to_key[selected_label]
    try:
        loaded = robinhood_main.opportunity_set_snapshot(
            user_id=current_user.user_id,
            portfolio_id=current_user.portfolio_id,
            set_key=selected_key,
            span=period,
        )
    except robinhood_main.RobinhoodMainError as exc:
        st.error(str(exc))
        return
    if loaded.get("kind") == "screener":
        return
    members = pd.DataFrame(list(loaded.get("members") or []))
    if members.empty:
        return
    can_execute = bool(current_user.is_admin and robinhood_main.execution_enabled())
    member_columns = [
        column
        for column in ("symbol", "current_price", "change_today", "period_return")
        if column in members.columns
    ]
    if not can_execute:
        st.dataframe(
            members[member_columns],
            use_container_width=True,
            hide_index=True,
            column_config={
                "symbol": "Symbol",
                "current_price": st.column_config.NumberColumn("Current", format="$%.2f"),
                "change_today": st.column_config.NumberColumn("Today", format="percent"),
                "period_return": st.column_config.NumberColumn(f"{period} return", format="percent"),
            },
        )
        return
    members["invest"] = False
    members["side"] = "Buy"
    members["quantity"] = 1.0
    edited = st.data_editor(
        members,
        use_container_width=True,
        hide_index=True,
        key=f"robinhood_opportunity_editor_{selected_key}",
        disabled=["symbol", "current_price", "change_today", "period_return"],
        column_config={
            "invest": st.column_config.CheckboxColumn("Include"),
            "side": st.column_config.SelectboxColumn("Side", options=["Buy", "Short"]),
            "quantity": st.column_config.NumberColumn("Shares", min_value=0.000001, format="%.6f"),
            "current_price": st.column_config.NumberColumn("Current", format="$%.2f"),
            "change_today": st.column_config.NumberColumn("Today", format="percent"),
            "period_return": st.column_config.NumberColumn(f"{period} return", format="percent"),
        },
    )
    accounts = robinhood_main.trading_accounts(
        user_id=current_user.user_id,
        portfolio_id=current_user.portfolio_id,
    )
    if not accounts:
        return
    account_labels = {str(item.get("label")): str(item.get("account_key")) for item in accounts}
    selected_account_label = st.selectbox("Trading account", list(account_labels), key="robinhood_basket_account")
    selected = edited[edited["invest"].fillna(False)]
    if st.button(
        f"Review {len(selected)} basket orders",
        key="robinhood_review_basket",
        disabled=selected.empty,
        use_container_width=True,
    ):
        orders = [
            {
                "account_key": account_labels[selected_account_label],
                "symbol": str(row["symbol"]),
                "side": "sell_short" if row["side"] == "Short" else "buy",
                "quantity": row["quantity"],
            }
            for row in selected.to_dict(orient="records")
        ]
        try:
            st.session_state[_MAIN_BASKET_REVIEW_KEY] = robinhood_main.review_batch_equity_orders(
                user_id=current_user.user_id,
                portfolio_id=current_user.portfolio_id,
                orders=orders,
            )
            st.rerun()
        except robinhood_main.RobinhoodMainError as exc:
            st.error(str(exc))
    review = st.session_state.get(_MAIN_BASKET_REVIEW_KEY)
    if isinstance(review, dict):
        st.dataframe(pd.DataFrame(list(review.get("orders") or [])), use_container_width=True, hide_index=True)
        place_col, cancel_col = _responsive_columns(2)
        if place_col.button(
            f"Place {len(list(review.get('orders') or []))} basket orders",
            key="robinhood_place_basket",
            type="primary",
            use_container_width=True,
        ):
            try:
                result = robinhood_main.confirm_batch_equity_orders(
                    user_id=current_user.user_id,
                    ticket_id=str(review.get("ticket_id") or ""),
                    orders=list(review.get("canonical_orders") or []),
                )
                st.session_state.pop(_MAIN_BASKET_REVIEW_KEY, None)
                st.dataframe(pd.DataFrame(list(result.get("results") or [])), use_container_width=True, hide_index=True)
            except robinhood_main.RobinhoodMainError as exc:
                st.error(str(exc))
        if cancel_col.button("Cancel basket", key="robinhood_cancel_basket", use_container_width=True):
            robinhood_main.cancel_batch_review(
                user_id=current_user.user_id,
                ticket_id=str(review.get("ticket_id") or ""),
            )
            st.session_state.pop(_MAIN_BASKET_REVIEW_KEY, None)
            st.rerun()


def _render_main_portfolio(
    current_user: auth_service.UserContext,
    snapshot: dict[str, object],
    period: str,
    sidebar_status: object,
    sidebar_buying_power: object,
) -> None:
    summary = dict(snapshot.get("account_summary") or {})
    sidebar_status.metric("Account Status", str(summary.get("status") or "active").upper())
    sidebar_buying_power.metric("Buying Power", f"${_to_float(summary, 'buying_power'):,.2f}")
    columns = _responsive_columns(6)
    columns[0].metric("Equity", f"${_to_float(summary, 'equity'):,.2f}")
    columns[1].metric("Cash", f"${_to_float(summary, 'cash'):,.2f}")
    columns[2].metric("Buying Power", f"${_to_float(summary, 'buying_power'):,.2f}")
    columns[3].metric("Day P/L", f"${_to_float(summary, 'day_pl'):,.2f}")
    columns[4].metric("Unrealized P/L", f"${_to_float(summary, 'total_unrealized_pl'):,.2f}")
    columns[5].metric("Accounts", str(int(_to_float(summary, "account_count"))))
    st.caption(
        f"Gross long ${_to_float(summary, 'gross_long_exposure'):,.2f} · "
        f"Gross short ${_to_float(summary, 'gross_short_exposure'):,.2f} · "
        f"Net exposure ${_to_float(summary, 'net_exposure'):,.2f}"
    )
    _render_main_holdings(current_user, snapshot, period)

    stored_timeseries = snapshot.get("timeseries")
    raw = stored_timeseries.copy() if isinstance(stored_timeseries, pd.DataFrame) else pd.DataFrame(list(stored_timeseries or []))
    raw = robinhood_main_analytics.filter_lookback(raw, period)
    if not raw.empty:
        st.subheader("Portfolio history")
        history_left, history_right = _responsive_columns(2, gap="large")
        with history_left:
            dollar_figure = px.line(raw, x="timestamp", y="portfolio", title="Portfolio value")
            st.plotly_chart(dollar_figure, use_container_width=True, key="robinhood_portfolio_value_chart")
        with history_right:
            normalized = normalize_timeseries_view(raw)
            if not normalized.empty:
                st.plotly_chart(
                    build_portfolio_vs_benchmarks_fig(normalized),
                    use_container_width=True,
                    key="robinhood_portfolio_benchmark_chart",
                )
    _render_batch_stop_losses(current_user)
    _render_opportunity_sets(current_user, snapshot, period)
    details = snapshot.get("details") if isinstance(snapshot.get("details"), dict) else {}
    errors = dict(details.get("collector_errors") or {})
    if errors:
        with st.expander("Data sources needing attention"):
            for source, message in errors.items():
                st.write(f"{source}: {message}")


def _monthly_return_heatmap(raw: pd.DataFrame) -> go.Figure | None:
    if raw.empty or len(raw) < 3:
        return None
    monthly = (
        raw.set_index("timestamp")["portfolio"]
        .resample("ME")
        .last()
        .pct_change()
        .dropna()
        .rename("return")
        .reset_index()
    )
    if monthly.empty:
        return None
    monthly["year"] = monthly["timestamp"].dt.year
    monthly["month"] = monthly["timestamp"].dt.strftime("%b")
    month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    pivot = monthly.pivot(index="year", columns="month", values="return").reindex(columns=month_order)
    text = pivot.map(lambda value: f"{value:.1%}" if pd.notna(value) else "")
    return go.Figure(
        go.Heatmap(
            z=pivot.to_numpy(dtype=float),
            x=pivot.columns.tolist(),
            y=[str(value) for value in pivot.index.tolist()],
            text=text.to_numpy(),
            texttemplate="%{text}",
            colorscale="RdYlGn",
            zmid=0,
            colorbar={"title": "Return"},
            hovertemplate="%{y} %{x}<br>%{z:.2%}<extra></extra>",
        )
    )


def _render_main_performance(snapshot: dict[str, object], period: str) -> None:
    stored_timeseries = snapshot.get("timeseries")
    raw = stored_timeseries.copy() if isinstance(stored_timeseries, pd.DataFrame) else pd.DataFrame(list(stored_timeseries or []))
    raw = robinhood_main_analytics.filter_lookback(raw, period)
    if raw.empty or "portfolio" not in raw.columns:
        st.info("No performance history available.")
        return
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True, errors="coerce")
    raw["portfolio"] = pd.to_numeric(raw["portfolio"], errors="coerce")
    raw = raw.dropna(subset=["timestamp", "portfolio"]).sort_values("timestamp")
    if raw.empty:
        st.info("No performance history available.")
        return
    first_value = float(raw["portfolio"].iloc[0])
    last_value = float(raw["portfolio"].iloc[-1])
    drawdown = raw["portfolio"] / raw["portfolio"].cummax() - 1.0
    daily = raw.set_index("timestamp")["portfolio"].resample("1D").last().dropna().pct_change().dropna()
    volatility = float(daily.std() * (252.0 ** 0.5)) if len(daily) > 1 else 0.0
    metrics = _responsive_columns(6)
    metrics[0].metric("Start", f"${first_value:,.2f}")
    metrics[1].metric("Current", f"${last_value:,.2f}")
    metrics[2].metric("Change", f"${last_value - first_value:,.2f}")
    metrics[3].metric("Value change", f"{(last_value / first_value - 1.0) if first_value else 0.0:.2%}")
    metrics[4].metric("Max drawdown", f"{float(drawdown.min()):.2%}")
    metrics[5].metric("Annualized volatility", f"{volatility:.2%}")
    value_figure = go.Figure(
        go.Scatter(
            x=raw["timestamp"],
            y=raw["portfolio"],
            mode="lines",
            fill="tozeroy",
            line={"color": "#38bdf8", "width": 2.4},
            fillcolor="rgba(56, 189, 248, 0.12)",
            hovertemplate="%{x|%b %d, %Y}<br>$%{y:,.2f}<extra></extra>",
        )
    )
    value_figure.update_layout(title=f"Portfolio value · {period}", hovermode="x unified")
    normalized = normalize_timeseries_view(raw)
    chart_left, chart_right = _responsive_columns([1.15, 0.85], gap="large")
    with chart_left:
        st.plotly_chart(value_figure, use_container_width=True, key="robinhood_performance_value_chart")
    with chart_right:
        if not normalized.empty:
            comparison = build_portfolio_vs_benchmarks_fig(normalized)
            comparison.update_layout(title=f"Portfolio vs benchmarks · {period}", hovermode="x unified")
            st.plotly_chart(
                comparison,
                use_container_width=True,
                key="robinhood_performance_benchmarks_chart",
            )
    drawdown_frame = pd.DataFrame({"timestamp": raw["timestamp"], "drawdown": drawdown})
    drawdown_figure = px.area(drawdown_frame, x="timestamp", y="drawdown", title=f"Underwater curve · {period}")
    drawdown_figure.update_traces(line_color="#ef4444", fillcolor="rgba(239,68,68,0.18)")
    lower_left, lower_right = _responsive_columns([0.85, 1.15], gap="large")
    with lower_left:
        st.plotly_chart(drawdown_figure, use_container_width=True, key="robinhood_drawdown_chart")
    with lower_right:
        monthly_figure = _monthly_return_heatmap(raw)
        if monthly_figure is not None:
            monthly_figure.update_layout(title="Monthly value changes")
            st.plotly_chart(monthly_figure, use_container_width=True, key="robinhood_monthly_returns_chart")
    table = performance_table(raw)
    if not table.empty:
        st.dataframe(table, use_container_width=True, hide_index=True)
    details = snapshot.get("details") if isinstance(snapshot.get("details"), dict) else {}
    account_performance = pd.DataFrame(list(details.get("performance_accounts") or []))
    if not account_performance.empty:
        st.subheader("Account performance")
        ordered = [
            column
            for column in (
                "account",
                "baseline",
                "current_value",
                "return_amount",
                "return_pct",
                "point_count",
            )
            if column in account_performance.columns
        ]
        st.dataframe(
            account_performance[ordered],
            use_container_width=True,
            hide_index=True,
            column_config={
                "account": st.column_config.TextColumn("Account"),
                "baseline": st.column_config.NumberColumn("Start value", format="$%.2f"),
                "current_value": st.column_config.NumberColumn("Current value", format="$%.2f"),
                "return_amount": st.column_config.NumberColumn("Change", format="$%.2f"),
                "return_pct": st.column_config.NumberColumn("Return", format="%.2f%%"),
                "point_count": st.column_config.NumberColumn("Observations", format="%d"),
            },
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
    with header_cols[1]:
        period = st.selectbox(
            "History Period",
            list(robinhood_main_analytics.LOOKBACK_OPTIONS),
            index=5,
            key="portfolio_overview_period",
        )
    _render_main_account_connection(current_user)
    main_snapshot = (
        robinhood_main.portfolio_snapshot(
            user_id=current_user.user_id,
            portfolio_id=current_user.portfolio_id,
        )
        if current_user is not None
        else None
    )
    if main_snapshot is not None and current_user is not None:
        _render_main_data_status(current_user, main_snapshot)
        _render_main_portfolio(
            current_user,
            main_snapshot,
            period,
            sidebar_status,
            sidebar_buying_power,
        )
        return
    if _render_main_data_status(current_user, main_snapshot):
        return
    if current_user is not None and not current_user.can_view_full_portfolio:
        st.caption(f"Viewing your {_current_user_share_fraction() * 100:.2f}% economic share of the master portfolio.")
    if main_snapshot is None and not _has_live_api(api, "Portfolio data unavailable."):
        return
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
            st.warning("Account summary unavailable.")
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
            st.warning("Positions unavailable.")
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
                    st.plotly_chart(pie, use_container_width=True, key="portfolio_position_weights_chart")

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
                    st.plotly_chart(dollar_fig, use_container_width=True, key="portfolio_dollar_equity_chart")
                norm = normalize_timeseries_view(raw)
                if norm.empty:
                    st.info("No portfolio history available.")
                else:
                    fig = build_portfolio_vs_benchmarks_fig(norm)
                    st.plotly_chart(fig, use_container_width=True, key="portfolio_vs_benchmarks_chart")
            except AlpacaAPIError as exc:
                _log_event("build_timeseries_failed", error=str(exc)[:200])
                st.warning("Portfolio history unavailable.")

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
                    st.plotly_chart(fig, use_container_width=True, key="portfolio_holding_roc_chart")
                    st.dataframe(roc, use_container_width=True, hide_index=True)
            except AlpacaAPIError as exc:
                _log_event("compute_holding_roc_failed", error=str(exc)[:200])
                st.warning("Momentum unavailable.")

        _render_batch_stop_losses(current_user)


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
    with header_cols[1]:
        period = st.selectbox(
            "History Period",
            list(robinhood_main_analytics.LOOKBACK_OPTIONS),
            index=5,
            key="performance_period",
        )
    _render_main_account_connection(current_user)
    main_snapshot = (
        robinhood_main.portfolio_snapshot(
            user_id=current_user.user_id,
            portfolio_id=current_user.portfolio_id,
        )
        if current_user is not None
        else None
    )
    if main_snapshot is not None:
        _render_main_data_status(current_user, main_snapshot)
        _render_main_performance(main_snapshot, period)
        return
    if _render_main_data_status(current_user, main_snapshot):
        return
    if current_user is not None and not current_user.can_view_full_portfolio:
        st.caption("Return-based metrics match the master portfolio while your ownership share remains fixed.")
    if main_snapshot is None and not _has_live_api(
        api,
        "Portfolio history unavailable.",
        allow_pipeline=True,
    ):
        return
    else:
        try:
            with st.spinner("Loading performance data..."):
                with _timed("load_portfolio_timeseries", period=period):
                    raw_timeseries = dashboard_loaders._load_timeseries_cached(cfg, period, force_refresh=force_data_refresh)
                with _timed("load_portfolio_performance", period=period):
                    perf = dashboard_loaders._load_portfolio_performance_cached(cfg, period, force_refresh=force_data_refresh)
        except AlpacaAPIError as exc:
            _log_event("load_portfolio_performance_failed", error=str(exc)[:200])
            st.error("Performance data unavailable.")
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
            st.plotly_chart(dollar_fig, use_container_width=True, key="portfolio_performance_dollar_equity_chart")
        st.dataframe(perf, use_container_width=True, hide_index=True)

        metric = st.selectbox(
            "Metric",
            ["annual_return", "sharpe_ratio", "beta_vs_spy", "alpha_vs_spy", "max_drawdown"],
        )
        st.plotly_chart(
            build_metric_bar(perf, metric),
            use_container_width=True,
            key=f"portfolio_performance_metric_{metric}_chart",
        )
