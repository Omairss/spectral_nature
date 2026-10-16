from __future__ import annotations

import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from services.config import AppConfig
from services.json_utils import to_list
from services import robinhood_mcp
from services.pipeline_store import (
    load_latest_dataset_frame,
    load_recent_dataset_frames,
    record_trading_agent_action,
    trading_agent_actions_table,
)
from services.trading_agent import (
    TRADING_AGENT_NARRATIVE_CONTRACT_VERSION,
    TRADING_AGENT_LEGACY_NARRATIVE_CONTRACTS,
    normalize_trading_agent_candidate,
)
from views._shared import (
    LOGGER,
    TRADING_AGENT_SECTION,
    _current_user_context,
    _current_user_is_admin,
    _responsive_columns,
)

def _trading_agent_text(value: object) -> str:
    if value is None:
        return ""
    out = str(value).strip()
    return "" if out.lower() == "nan" else out

def _trading_agent_float(value: object) -> float | None:
    if isinstance(value, (list, tuple, set, pd.Series, pd.Index, np.ndarray)):
        values = list(value)
        value = values[0] if values else None
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return None
    return float(numeric)

def _trading_agent_pct(value: object) -> str:
    numeric = _trading_agent_float(value)
    if numeric is None:
        return "n/a"
    return f"{numeric:+.1f}%"

def _trading_agent_time_label(value: object) -> str:
    parsed = pd.to_datetime(_trading_agent_text(value), utc=True, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _robinhood_walk_values(value: object, *, keys: set[str]) -> list[object]:
    matches: list[object] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = "".join(char for char in str(key).lower() if char.isalnum())
            if normalized_key in keys and item not in (None, ""):
                matches.append(item)
            matches.extend(_robinhood_walk_values(item, keys=keys))
    elif isinstance(value, list):
        for item in value:
            matches.extend(_robinhood_walk_values(item, keys=keys))
    return matches


def _robinhood_first_value(value: object, *keys: str) -> object | None:
    normalized = {"".join(char for char in key.lower() if char.isalnum()) for key in keys}
    values = _robinhood_walk_values(value, keys=normalized)
    return values[0] if values else None


def _robinhood_money(value: object) -> str:
    numeric = _trading_agent_float(value)
    return f"${numeric:,.2f}" if numeric is not None else "n/a"


def _robinhood_list_count(value: object) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("results", "accounts", "positions", "items"):
            item = value.get(key)
            if isinstance(item, list):
                return len(item)
    return None


def _render_robinhood_snapshot(snapshot: dict[str, object]) -> None:
    results = snapshot.get("results") if isinstance(snapshot, dict) else {}
    errors = snapshot.get("errors") if isinstance(snapshot, dict) else {}
    if not isinstance(results, dict):
        results = {}
    if not isinstance(errors, dict):
        errors = {}

    portfolio = results.get("get_portfolio")
    positions = results.get("get_equity_positions")
    accounts = results.get("get_accounts")
    approvals = results.get("get_trade_approval_setting")
    total_value = _robinhood_first_value(
        portfolio,
        "total_value",
        "portfolio_value",
        "equity",
        "market_value",
    )
    buying_power = _robinhood_first_value(
        portfolio,
        "buying_power",
        "realtime_buying_power",
        "available_buying_power",
    )
    approval_value = _robinhood_first_value(
        approvals,
        "trade_approvals_enabled",
        "trade_approval_setting",
        "enabled",
        "status",
    )
    position_count = _robinhood_list_count(positions)
    account_count = _robinhood_list_count(accounts)

    metrics: list[tuple[str, str]] = []
    if total_value is not None:
        metrics.append(("Portfolio value", _robinhood_money(total_value)))
    if buying_power is not None:
        metrics.append(("Buying power", _robinhood_money(buying_power)))
    if position_count is not None:
        metrics.append(("Equity positions", str(position_count)))
    if account_count is not None:
        metrics.append(("Accounts", str(account_count)))
    if approval_value is not None:
        if isinstance(approval_value, bool):
            approval_label = "On" if approval_value else "Off"
        else:
            approval_label = _trading_agent_text(approval_value)
        metrics.append(("Trade approvals", approval_label))
    if metrics:
        for column, (label, value) in zip(_responsive_columns(len(metrics)), metrics):
            column.metric(label, value)
    elif results:
        st.success("Robinhood account data refreshed.")

    if errors:
        with st.expander("Unavailable Robinhood account fields", expanded=False):
            for tool_name, message in errors.items():
                st.markdown(f"- `{tool_name}`: {_trading_agent_text(message)}")


def _render_robinhood_equity_order(*, user_id: str, snapshot: dict[str, object]) -> None:
    if not robinhood_mcp.execution_enabled():
        return
    raw_accounts = snapshot.get("agentic_accounts") if isinstance(snapshot, dict) else []
    accounts = [item for item in list(raw_accounts or []) if isinstance(item, dict)]
    if not accounts:
        st.caption("Refresh account data to load the Robinhood account enabled for agent trading.")
        return

    with st.expander("Equity order", expanded=False):
        account_index = st.selectbox(
            "Account",
            options=list(range(len(accounts))),
            format_func=lambda index: _trading_agent_text(accounts[index].get("label")) or "Robinhood account",
            key="robinhood_order_account_index",
        )
        first_row = _responsive_columns(3)
        side = first_row[0].selectbox(
            "Side",
            ["buy", "sell"],
            format_func=str.title,
            key="robinhood_order_side",
        )
        symbol = first_row[1].text_input(
            "Symbol",
            key="robinhood_order_symbol",
            max_chars=32,
        )
        order_type = first_row[2].selectbox(
            "Order type",
            ["market", "limit", "stop_market", "stop_limit"],
            format_func=lambda value: value.replace("_", " ").title(),
            key="robinhood_order_type",
        )

        second_row = _responsive_columns(3)
        size_type = second_row[0].selectbox(
            "Size",
            ["shares", "dollars"] if order_type == "market" else ["shares"],
            key="robinhood_order_size_type",
        )
        size_value = second_row[1].text_input(
            "Quantity" if size_type == "shares" else "Dollar amount",
            key="robinhood_order_size_value",
        )
        time_in_force = second_row[2].selectbox(
            "Time in force",
            ["gfd", "gtc"],
            format_func=lambda value: "Good for day" if value == "gfd" else "Good till canceled",
            key="robinhood_order_time_in_force",
        )

        price_row = _responsive_columns(3)
        limit_price = ""
        stop_price = ""
        if order_type in {"limit", "stop_limit"}:
            limit_price = price_row[0].text_input(
                "Limit price",
                key="robinhood_order_limit_price",
            )
        if order_type in {"stop_market", "stop_limit"}:
            stop_price = price_row[1].text_input(
                "Stop price",
                key="robinhood_order_stop_price",
            )
        market_hours_options = ["regular_hours"]
        if order_type == "limit":
            market_hours_options.extend(["extended_hours", "all_day_hours"])
        market_hours = price_row[2].selectbox(
            "Market hours",
            market_hours_options,
            format_func=lambda value: value.replace("_", " ").title(),
            key="robinhood_order_market_hours",
        )

        if st.button(
            "Review order",
            key="robinhood_review_order",
            use_container_width=True,
        ):
            st.session_state.pop("_robinhood_pending_order", None)
            arguments: dict[str, object] = {
                "account_number": _trading_agent_text(accounts[int(account_index)].get("account_number")),
                "side": side,
                "symbol": symbol,
                "type": order_type,
                "market_hours": market_hours,
                "time_in_force": time_in_force,
            }
            arguments["quantity" if size_type == "shares" else "dollar_amount"] = size_value
            if limit_price:
                arguments["limit_price"] = limit_price
            if stop_price:
                arguments["stop_price"] = stop_price
            try:
                reviewed = robinhood_mcp.review_equity_order(
                    user_id=user_id,
                    arguments=arguments,
                )
                st.session_state["_robinhood_pending_order"] = {
                    "ticket_id": reviewed.get("ticket_id"),
                    "arguments": robinhood_mcp.normalize_equity_order_arguments(arguments),
                    "order": reviewed.get("order"),
                    "review": reviewed.get("review"),
                    "expires_at": reviewed.get("expires_at"),
                }
            except Exception as exc:
                st.error(str(exc))

        pending = st.session_state.get("_robinhood_pending_order")
        if not isinstance(pending, dict):
            return
        order = pending.get("order") if isinstance(pending.get("order"), dict) else {}
        size_label = (
            f"{order.get('quantity')} shares"
            if order.get("quantity")
            else f"${order.get('dollar_amount')}"
        )
        st.markdown(
            f"**{str(order.get('side') or '').upper()} {size_label} of "
            f"{str(order.get('symbol') or '').upper()} · "
            f"{str(order.get('type') or '').replace('_', ' ')} · {order.get('account', '')}**"
        )
        if order.get("limit_price"):
            st.caption(f"Limit ${order['limit_price']}")
        if order.get("stop_price"):
            st.caption(f"Stop ${order['stop_price']}")
        review = pending.get("review")
        if review not in (None, "", {}, []):
            st.markdown("**Robinhood review**")
            if isinstance(review, (dict, list)):
                st.json(review, expanded=False)
            else:
                st.write(review)
        confirm_cols = _responsive_columns([2.0, 1.0])
        if confirm_cols[0].button(
            "Place order",
            key="robinhood_confirm_order",
            type="primary",
            use_container_width=True,
        ):
            try:
                outcome = robinhood_mcp.confirm_equity_order(
                    user_id=user_id,
                    ticket_id=_trading_agent_text(pending.get("ticket_id")),
                    arguments=dict(pending.get("arguments") or {}),
                )
                st.session_state.pop("_robinhood_pending_order", None)
                if outcome.get("status") == "executed":
                    st.success("Order submitted to Robinhood.")
                    if outcome.get("result") not in (None, "", {}, []):
                        st.json(outcome.get("result"), expanded=False)
                else:
                    st.error("Robinhood rejected the order.")
            except Exception as exc:
                st.session_state.pop("_robinhood_pending_order", None)
                st.error(str(exc))
        if confirm_cols[1].button(
            "Cancel",
            key="robinhood_cancel_pending_order",
            use_container_width=True,
        ):
            try:
                robinhood_mcp.cancel_equity_order_review(
                    user_id=user_id,
                    ticket_id=_trading_agent_text(pending.get("ticket_id")),
                )
                st.session_state.pop("_robinhood_pending_order", None)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def _render_robinhood_connection_panel() -> None:
    user = _current_user_context()
    if user is None:
        return
    notice = st.session_state.pop("_robinhood_oauth_notice", None)
    if isinstance(notice, dict):
        message = _trading_agent_text(notice.get("message"))
        if notice.get("status") == "success":
            st.success(message)
        else:
            st.error(message)

    try:
        status = robinhood_mcp.connection_status(user_id=user.user_id)
    except Exception as exc:
        st.error(str(exc))
        return
    state = _trading_agent_text(status.get("status") or "disconnected").lower()

    with st.container(border=True):
        header, state_col = _responsive_columns([3.0, 1.0])
        header.subheader("Robinhood")
        state_col.caption("Connected" if state == "connected" else "Not connected")

        if state == "connected":
            verified = _trading_agent_time_label(status.get("last_verified_at"))
            tool_count = int(status.get("tool_count") or 0)
            summary_cols = _responsive_columns(2)
            summary_cols[0].metric("Available tools", tool_count)
            summary_cols[1].metric("Last verified", verified or "Not yet")
            action_cols = _responsive_columns([1.3, 1.0, 2.0])
            if action_cols[0].button(
                "Refresh account",
                key="robinhood_refresh_account",
                use_container_width=True,
            ):
                try:
                    robinhood_mcp.refresh_connection(user_id=user.user_id)
                    st.session_state["_robinhood_account_snapshot"] = robinhood_mcp.load_account_snapshot(
                        user_id=user.user_id
                    )
                    st.session_state.pop("_robinhood_pending_order", None)
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
            if action_cols[1].button(
                "Disconnect",
                key="robinhood_disconnect",
                use_container_width=True,
            ):
                robinhood_mcp.disconnect(user_id=user.user_id)
                st.session_state.pop("_robinhood_account_snapshot", None)
                st.session_state.pop("_robinhood_authorization_url", None)
                st.session_state.pop("_robinhood_pending_order", None)
                st.rerun()
            snapshot = st.session_state.get("_robinhood_account_snapshot")
            if isinstance(snapshot, dict):
                _render_robinhood_snapshot(snapshot)
                _render_robinhood_equity_order(user_id=user.user_id, snapshot=snapshot)
            return

        if state in {"reauth_required", "error"} and _trading_agent_text(status.get("last_error")):
            st.warning(_trading_agent_text(status.get("last_error")))
        if st.button(
            "Connect Robinhood",
            key="robinhood_connect",
            type="primary",
            use_container_width=True,
        ):
            try:
                authorization = robinhood_mcp.begin_authorization(user_id=user.user_id)
                st.session_state["_robinhood_authorization_url"] = str(
                    authorization.get("authorization_url") or ""
                )
            except Exception as exc:
                st.error(str(exc))
        authorization_url = _trading_agent_text(st.session_state.get("_robinhood_authorization_url"))
        if authorization_url:
            st.link_button(
                "Continue to Robinhood",
                authorization_url,
                use_container_width=True,
                type="primary",
            )

def _trading_agent_source_freshness(opportunity: dict[str, object]) -> str:
    label = _trading_agent_time_label(opportunity.get("asof_time_utc"))
    return f"Price data as of {label}" if label else ""

def _trading_agent_sparkline_values(value: object) -> list[float]:
    values: list[float] = []
    raw_values = to_list(value)
    if len(raw_values) == 1 and isinstance(raw_values[0], str):
        try:
            parsed = json.loads(raw_values[0])
            raw_values = to_list(parsed)
        except Exception:
            raw_values = []
    for item in raw_values:
        numeric = _trading_agent_float(item)
        if numeric is not None:
            values.append(numeric)
    return values[-90:]

def _trading_agent_signal_model(
    opportunity: dict[str, object],
    controls: dict[str, object],
) -> dict[str, object]:
    selected_horizon_col = _trading_agent_text(
        controls.get("selected_horizon_col") or opportunity.get("selected_horizon_col") or "return_1m_pct"
    )
    selected_horizon_label = _trading_agent_text(
        controls.get("momentum_horizon") or opportunity.get("selected_horizon_label") or "1 Month"
    )
    horizon_return = _trading_agent_float(opportunity.get(selected_horizon_col))
    daily_move = _trading_agent_float(opportunity.get("daily_change_pct"))

    return {
        "market_pattern": _trading_agent_text(opportunity.get("direction") or opportunity.get("opportunity")) or "Pattern unavailable",
        "selected_horizon_col": selected_horizon_col,
        "selected_horizon_label": selected_horizon_label,
        "horizon_return": horizon_return,
        "daily_move": daily_move,
        "sparkline": _trading_agent_sparkline_values(opportunity.get("sparkline_3m")),
        "freshness": _trading_agent_source_freshness(opportunity),
    }

def _render_trading_agent_sparkline(values: list[float], *, key: str) -> None:
    if len(values) < 2:
        return
    frame = pd.DataFrame({"point": range(len(values)), "value": values})
    fig = go.Figure(
        go.Scatter(
            x=frame["point"],
            y=frame["value"],
            mode="lines",
            line={"color": "#2563eb", "width": 2},
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        height=92,
        margin={"l": 4, "r": 4, "t": 4, "b": 4},
        xaxis={"visible": False},
        yaxis={"visible": False},
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)

def _trading_agent_context_lookup(context: dict[str, object]) -> dict[str, dict[str, object]]:
    lookup: dict[str, dict[str, object]] = {}
    for row in to_list((context or {}).get("market_opportunity_feed")):
        if not isinstance(row, dict):
            continue
        symbol = _trading_agent_text(row.get("symbol") or row.get("ticker")).upper()
        if not symbol:
            continue
        lookup.setdefault(symbol, {})["opportunity"] = row
    for item in to_list((context or {}).get("ticker_evidence")):
        if not isinstance(item, dict):
            continue
        symbol = _trading_agent_text(item.get("symbol") or item.get("ticker")).upper()
        if not symbol:
            continue
        lookup.setdefault(symbol, {})["ticker_evidence"] = item
        opportunity = item.get("opportunity")
        if isinstance(opportunity, dict) and opportunity:
            lookup.setdefault(symbol, {}).setdefault("opportunity", opportunity)
    source_summaries = dict((context or {}).get("source_summaries") or {})
    for item in to_list(source_summaries.get("stock_investigator")):
        if not isinstance(item, dict):
            continue
        symbol = _trading_agent_text(item.get("ticker") or item.get("symbol")).upper()
        if symbol:
            lookup.setdefault(symbol, {})["stock_summary"] = item
    return lookup

def _trading_agent_company_name(symbol: str, context_item: dict[str, object]) -> str:
    opportunity = context_item.get("opportunity") if isinstance(context_item, dict) else {}
    if isinstance(opportunity, dict):
        company = _trading_agent_text(opportunity.get("company_name") or opportunity.get("security_name") or opportunity.get("name"))
        if company and company.upper() != symbol.upper():
            return company
    return ""

def _render_trading_agent_candidate_card(
    candidate: dict[str, object],
    *,
    context_item: dict[str, object],
    controls: dict[str, object],
    idx: int,
    action_state: dict[str, object] | None = None,
    show_narrative: bool = True,
) -> None:
    ticker = _trading_agent_text(candidate.get("ticker")).upper()
    direction = _trading_agent_text(candidate.get("direction") or "watch").upper()
    stance = _trading_agent_text(candidate.get("stance") or "no_call").lower()
    horizon = _trading_agent_text(candidate.get("suggested_horizon"))
    company_name = _trading_agent_company_name(ticker, context_item)
    opportunity = context_item.get("opportunity") if isinstance(context_item, dict) else {}
    ticker_evidence = context_item.get("ticker_evidence") if isinstance(context_item, dict) else {}
    if not isinstance(opportunity, dict):
        opportunity = {}
    if not isinstance(ticker_evidence, dict):
        ticker_evidence = {}
    else:
        ticker_evidence = dict(ticker_evidence)
    stock_summary = context_item.get("stock_summary") if isinstance(context_item, dict) else {}
    if isinstance(stock_summary, dict) and stock_summary:
        ticker_evidence.setdefault("stock_summary", stock_summary)
    signal_model = _trading_agent_signal_model(opportunity, controls)

    with st.container(border=True):
        header_cols = _responsive_columns([4.7, 1.0, 1.1])
        identity = ticker or "n/a"
        if company_name:
            identity = f"{identity} · {company_name}"
        header_cols[0].markdown(f"### {identity}")
        header_cols[1].markdown(f"**{direction}**")
        header_cols[2].caption(horizon or "Horizon n/a")

        setup = _trading_agent_text(candidate.get("setup")) if show_narrative else ""
        if setup:
            st.markdown(f"**Why it is on the list:** {setup}")

        sparkline = to_list(signal_model.get("sparkline"))
        if len(sparkline) >= 2:
            st.markdown("**Recent price path**")
            _render_trading_agent_sparkline(
                sparkline,
                key=f"trading_agent_sparkline_{idx}_{ticker or 'na'}",
            )

        price_cols = _responsive_columns(3)
        price_cols[0].metric("1D move", _trading_agent_pct(signal_model.get("daily_move")))
        price_cols[1].metric(
            f"{_trading_agent_text(signal_model.get('selected_horizon_label'))} return",
            _trading_agent_pct(signal_model.get("horizon_return")),
        )
        pattern = _trading_agent_text(signal_model.get("market_pattern"))
        price_cols[2].metric("Price pattern", pattern or "n/a")
        freshness = _trading_agent_text(signal_model.get("freshness"))
        if freshness:
            st.caption(freshness)

        hypothesis = _trading_agent_text(candidate.get("hypothesis")) if show_narrative else ""
        if hypothesis:
            st.markdown(f"**Thesis:** {hypothesis}")

        forecast = candidate.get("forecast") or {}
        if show_narrative and forecast:
            st.markdown("**At this price**")
            st.write(forecast.get("pricing_assessment") or "")
            with st.expander("Assumptions and opposing case"):
                for assumption in forecast.get("assumptions") or []:
                    st.write(assumption)
                st.write(forecast.get("opposing_case") or "")

        evidence = [
            _trading_agent_text(item)
            for item in to_list(candidate.get("evidence"))
            if show_narrative and _trading_agent_text(item)
        ]
        invalidation = _trading_agent_text(candidate.get("invalidation")) if show_narrative else ""
        risks = [
            _trading_agent_text(item)
            for item in to_list(candidate.get("tail_risks"))
            if show_narrative and _trading_agent_text(item)
        ]
        evidence_limits = [
            _trading_agent_text(item)
            for item in to_list(candidate.get("evidence_limits"))
            if show_narrative and _trading_agent_text(item)
        ]
        news_lines = [
            _trading_agent_text(item)
            for item in to_list(ticker_evidence.get("news_summary_lines"))
            if _trading_agent_text(item)
        ]
        attention_context = ticker_evidence.get("attention_context") if isinstance(ticker_evidence, dict) else {}
        attention_lines = []
        if isinstance(attention_context, dict):
            attention_lines = [
                _trading_agent_text(attention_context.get(key))
                for key in ["llm_headline", "llm_why_now", "llm_management_signal"]
                if _trading_agent_text(attention_context.get(key))
            ]

        if show_narrative:
            body_cols = _responsive_columns([1.15, 1.0])
            with body_cols[0]:
                if evidence:
                    st.markdown("**Evidence**")
                    st.markdown("\n".join(f"- {item}" for item in evidence[:5]))
                if invalidation:
                    st.markdown(f"**What would disprove it**  \n{invalidation}")
            with body_cols[1]:
                if risks:
                    st.markdown("**What could go wrong**")
                    st.markdown("\n".join(f"- {item}" for item in risks[:5]))
                if evidence_limits:
                    st.markdown("**What we still need**")
                    st.markdown("\n".join(f"- {item}" for item in evidence_limits[:5]))
                if attention_lines or news_lines:
                    with st.expander("Company context", expanded=False):
                        for item in attention_lines[:3]:
                            st.markdown(f"- {item}")
                        for item in news_lines[:3]:
                            st.markdown(f"- {item}")

        candidate_id = _trading_agent_text(candidate.get("candidate_id"))
        latest_action = _trading_agent_text((action_state or {}).get("action"))
        action_label = ""
        if latest_action in {"place_requested", "approved"}:
            action_label = "Approved"
        elif latest_action == "rejected":
            action_label = "Rejected"
        if action_label:
            st.caption(f"Decision: {action_label}")

        action_cols = _responsive_columns([1.0, 1.0, 2.4])
        action_recorded = bool(latest_action in {"place_requested", "approved", "rejected"})
        place_disabled = action_recorded or not candidate_id or stance not in {"long", "short"}
        reject_disabled = action_recorded or not candidate_id
        with action_cols[0]:
            if st.button(
                "Approve and track",
                key=f"trading_agent_approve_{idx}_{candidate_id or ticker}",
                use_container_width=True,
                disabled=place_disabled,
            ):
                user = _current_user_context()
                ok, msg = record_trading_agent_action(
                    candidate=dict(candidate),
                    action="approve",
                    requested_by=str(getattr(user, "user_id", "") or ""),
                    requested_email=str(getattr(user, "email", "") or ""),
                )
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.warning(msg)
        with action_cols[1]:
            if st.button(
                "Reject",
                key=f"trading_agent_reject_{idx}_{candidate_id or ticker}",
                use_container_width=True,
                disabled=reject_disabled,
            ):
                user = _current_user_context()
                ok, msg = record_trading_agent_action(
                    candidate=dict(candidate),
                    action="reject",
                    requested_by=str(getattr(user, "user_id", "") or ""),
                    requested_email=str(getattr(user, "email", "") or ""),
                )
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.warning(msg)
        with action_cols[2]:
            if ticker and st.button(
                "Open Stock Investigator",
                key=f"trading_agent_open_{idx}_{ticker}_{candidate_id or 'na'}",
                use_container_width=True,
            ):
                from views._shared import _set_workspace_ticker, _open_workspace_section, STOCK_INVESTIGATOR_SECTION
                _set_workspace_ticker(ticker)
                _open_workspace_section(STOCK_INVESTIGATOR_SECTION)

def _trading_agent_json_value(value: object, fallback: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    text = _trading_agent_text(value)
    if not text:
        return fallback
    try:
        parsed = json.loads(text)
    except Exception:
        return fallback
    return parsed if parsed is not None else fallback

def _load_latest_trading_agent_output() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, object | None]:
    actions = trading_agent_actions_table(limit=500)
    try:
        run_versions = load_recent_dataset_frames("trading_agent_runs", limit=8)
    except Exception:
        run_versions = []
    try:
        candidate_versions = load_recent_dataset_frames("trading_agent_candidates", limit=8)
    except Exception:
        candidate_versions = []

    for runs, metadata in run_versions:
        if not isinstance(runs, pd.DataFrame) or runs.empty or "run_id" not in runs.columns:
            continue
        run_ids = [
            _trading_agent_text(value)
            for value in runs["run_id"].dropna().astype(str).tolist()
            if _trading_agent_text(value)
        ]
        if not run_ids:
            continue
        run_id_set = set(run_ids)
        for candidates, _candidate_metadata in candidate_versions:
            if not isinstance(candidates, pd.DataFrame) or candidates.empty or "run_id" not in candidates.columns:
                continue
            scoped_candidates = candidates[
                candidates["run_id"].astype(str).map(_trading_agent_text).isin(run_id_set)
            ].copy()
            if scoped_candidates.empty:
                continue
            return (
                runs.copy(),
                scoped_candidates,
                actions.copy() if isinstance(actions, pd.DataFrame) else pd.DataFrame(),
                metadata,
            )

    try:
        runs, metadata = load_latest_dataset_frame("trading_agent_runs")
    except Exception:
        runs, metadata = pd.DataFrame(), None
    try:
        candidates, _ = load_latest_dataset_frame("trading_agent_candidates")
    except Exception:
        candidates = pd.DataFrame()
    return (
        runs.copy() if isinstance(runs, pd.DataFrame) else pd.DataFrame(),
        candidates.copy() if isinstance(candidates, pd.DataFrame) else pd.DataFrame(),
        actions.copy() if isinstance(actions, pd.DataFrame) else pd.DataFrame(),
        metadata,
    )

def _latest_trading_agent_action_map(actions: pd.DataFrame) -> dict[str, dict[str, object]]:
    if not isinstance(actions, pd.DataFrame) or actions.empty or "candidate_id" not in actions.columns:
        return {}
    out = actions.copy()
    if "created_at_utc" in out.columns:
        out["_created_at_utc"] = pd.to_datetime(out["created_at_utc"], utc=True, errors="coerce")
        out = out.sort_values("_created_at_utc", ascending=False, na_position="last")
    latest: dict[str, dict[str, object]] = {}
    for _, row in out.iterrows():
        candidate_id = _trading_agent_text(row.get("candidate_id"))
        if candidate_id and candidate_id not in latest:
            latest[candidate_id] = row.to_dict()
    return latest

def _trading_agent_candidate_from_row(row: pd.Series) -> dict[str, object]:
    candidate = _trading_agent_json_value(row.get("candidate_json"), {})
    if not isinstance(candidate, dict):
        candidate = {}
    candidate = dict(candidate)
    stored_narrative_contract_version = _trading_agent_text(
        candidate.get("narrative_contract_version")
    )
    candidate.update(
        {
            "candidate_id": _trading_agent_text(row.get("candidate_id")),
            "trading_agent_run_id": _trading_agent_text(row.get("trading_agent_run_id")),
            "run_id": _trading_agent_text(row.get("run_id")),
            "horizon_key": _trading_agent_text(row.get("horizon_key")),
            "horizon_label": _trading_agent_text(row.get("horizon_label")),
            "selected_horizon_col": _trading_agent_text(row.get("selected_horizon_col")),
            "ticker": _trading_agent_text(candidate.get("ticker") or row.get("ticker")).upper(),
            "direction": _trading_agent_text(candidate.get("direction") or row.get("direction")),
            "stance": _trading_agent_text(candidate.get("stance") or row.get("stance")),
            "research_status": _trading_agent_text(
                candidate.get("research_status") or row.get("research_status")
            ),
            "setup": _trading_agent_text(candidate.get("setup") or row.get("setup")),
            "hypothesis": _trading_agent_text(candidate.get("hypothesis") or row.get("hypothesis")),
            "invalidation": _trading_agent_text(candidate.get("invalidation") or row.get("invalidation")),
            "suggested_horizon": _trading_agent_text(candidate.get("suggested_horizon") or row.get("suggested_horizon")),
            "confidence": _trading_agent_text(candidate.get("confidence") or row.get("confidence") or "low"),
        }
    )
    candidate = normalize_trading_agent_candidate(candidate)
    candidate["narrative_contract_version"] = stored_narrative_contract_version
    candidate["decision_status"] = (
        "open" if candidate["stance"] in {"long", "short"} else "research_only"
    )
    evidence = candidate.get("evidence")
    if not to_list(evidence):
        evidence = _trading_agent_json_value(row.get("evidence_json"), [])
    candidate["evidence"] = [_trading_agent_text(item) for item in to_list(evidence) if _trading_agent_text(item)]
    risks = candidate.get("tail_risks")
    if not to_list(risks):
        risks = _trading_agent_json_value(row.get("tail_risks_json"), [])
    candidate["tail_risks"] = [_trading_agent_text(item) for item in to_list(risks) if _trading_agent_text(item)]
    evidence_limits = candidate.get("evidence_limits")
    if not to_list(evidence_limits):
        evidence_limits = _trading_agent_json_value(row.get("evidence_limits_json"), [])
    candidate["evidence_limits"] = [
        _trading_agent_text(item)
        for item in to_list(evidence_limits)
        if _trading_agent_text(item)
    ]
    return candidate

def _trading_agent_context_from_run(row: pd.Series) -> dict[str, object]:
    context = _trading_agent_json_value(row.get("context_json"), {})
    return context if isinstance(context, dict) else {}

def _trading_agent_result_from_run(row: pd.Series) -> dict[str, object]:
    result = _trading_agent_json_value(row.get("result_json"), {})
    if not isinstance(result, dict):
        result = {}
    result.setdefault("status", _trading_agent_text(row.get("status")))
    result.setdefault("regime_read", _trading_agent_text(row.get("regime_read")))
    result.setdefault("portfolio_posture", _trading_agent_text(row.get("portfolio_posture")))
    result.setdefault("data_gaps", _trading_agent_json_value(row.get("data_gaps_json"), []))
    aql_agent = _trading_agent_json_value(row.get("aql_agent_json"), {})
    if isinstance(aql_agent, dict):
        result.setdefault("aql_agent", aql_agent)
    return result

def _trading_agent_horizon_sort_key(value: object) -> int:
    order = {"1w": 0, "1m": 1, "3m": 2, "1y": 3, "5y": 4}
    return order.get(_trading_agent_text(value), 99)

def _render_approved_call_performance() -> None:
    from services.trading_call_tracking import tracking_view
    user = _current_user_context()
    try:
        calls = tracking_view(owner_id=str(user.user_id))["calls"]
        if not calls:
            st.write("Approve a call to track its performance.")
            return
        by_id = {row["tracking_id"]: row for row in calls}
        selected = st.selectbox("Approved call", list(by_id), format_func=lambda key: " · ".join(
            str(by_id[key].get(field) or "") for field in ("ticker", "stance", "horizon_label", "approved_at_utc")))
        row = tracking_view(owner_id=str(user.user_id), tracking_id=selected)["calls"][0]
    except Exception:
        st.error("Call tracking is unavailable.")
        return
    benchmark = "SPY short" if row["stance"] == "short" else "SPY long"
    columns = _responsive_columns(3)
    for column, label, key in zip(columns, ("Return", f"Vs {benchmark}", "Drawdown"), ("return_pct", "excess_return_pct", "max_drawdown_pct")):
        column.metric(label, _trading_agent_pct(row.get(key)))
    if row["status"] == "pending_entry":
        st.write("Entry at the next trading day's close.")
    elif row["status"] == "price_unavailable":
        st.write("Price data is unavailable.")
    path = pd.DataFrame(row.get("path") or [])
    if not path.empty:
        figure = go.Figure()
        figure.add_trace(go.Scatter(x=pd.to_datetime(path["date"]), y=path["return_pct"], name="Call", mode="lines"))
        figure.add_trace(go.Scatter(x=pd.to_datetime(path["date"]), y=path["benchmark_return_pct"], name=benchmark, mode="lines", connectgaps=False))
        figure.update_layout(yaxis_title="Return (%)", xaxis_title=None)
        st.plotly_chart(figure, use_container_width=True, key=f"approved_call_{selected}")
    if row.get("entry_date"):
        st.caption(f"Entry {row['entry_date']} · Target {row.get('target_date')} · Prices through {row.get('last_price_date')}")
    if row.get("hypothesis"):
        st.markdown(row["hypothesis"])
    st.dataframe(pd.DataFrame(calls)[["ticker", "stance", "horizon_label", "approved_at_utc", "status", *(["return_pct"] if any("return_pct" in call for call in calls) else [])]], hide_index=True, use_container_width=True)


def _render_trading_agent_section(
    cfg: AppConfig,
    *,
    force_data_refresh: bool,
) -> None:
    if not _current_user_is_admin():
        st.error("Only admin users can access this section.")
        return

    st.title(TRADING_AGENT_SECTION)
    section = st.segmented_control("Trading view", ["Calls", "Paper performance"], default="Calls", key="trading_view_section")
    if section == "Paper performance":
        _render_approved_call_performance()
        return
    _render_robinhood_connection_panel()

    runs, candidates_frame, actions, metadata = _load_latest_trading_agent_output()
    user = _current_user_context()
    if not actions.empty:
        actions = actions[actions["requested_by"].astype(str).eq(str(user.user_id))].copy()
    asof_time = _trading_agent_time_label(getattr(metadata, "asof_time_utc", "")) if metadata is not None else ""
    if asof_time:
        st.caption(f"As of {asof_time}")

    if runs.empty or candidates_frame.empty:
        st.info("No Trading Agent candidates are available yet.")
        return

    if "generated_at_utc" in runs.columns:
        runs = runs.assign(_generated_at=pd.to_datetime(runs["generated_at_utc"], utc=True, errors="coerce"))
        latest_generated = runs["_generated_at"].max()
        if pd.notna(latest_generated):
            runs = runs[runs["_generated_at"].eq(latest_generated)].copy()
    if "run_id" in runs.columns and not runs.empty:
        latest_run_id = _trading_agent_text(runs.iloc[0].get("run_id"))
        if latest_run_id and "run_id" in candidates_frame.columns:
            candidates_frame = candidates_frame[candidates_frame["run_id"].astype(str).eq(latest_run_id)].copy()

    horizon_values = sorted(
        {
            _trading_agent_text(value)
            for value in runs.get("horizon_key", pd.Series(dtype=str)).tolist()
            if _trading_agent_text(value)
        },
        key=_trading_agent_horizon_sort_key,
    )
    if not horizon_values:
        st.info("No horizon data available.")
        return
    horizon_labels = {
        _trading_agent_text(row.get("horizon_key")): _trading_agent_text(row.get("horizon_label")) or _trading_agent_text(row.get("horizon_key"))
        for _, row in runs.iterrows()
    }
    selected_horizon = st.segmented_control(
        "Horizon",
        horizon_values,
        default=horizon_values[0],
        format_func=lambda key: horizon_labels.get(str(key), str(key)),
        key="trading_agent_materialized_horizon",
        width="stretch",
    )
    selected_horizon = _trading_agent_text(selected_horizon) or horizon_values[0]

    run_rows = runs[runs["horizon_key"].astype(str).eq(selected_horizon)].copy() if "horizon_key" in runs.columns else runs.head(1)
    if run_rows.empty:
        st.info("No Trading Agent run was found for this horizon.")
        return
    run_row = run_rows.iloc[0]
    result = _trading_agent_result_from_run(run_row)
    context = _trading_agent_context_from_run(run_row)
    context_lookup = _trading_agent_context_lookup(context)
    controls_context = dict(context.get("controls") or {})
    controls_context.setdefault("selected_horizon_label", _trading_agent_text(run_row.get("horizon_label")))
    controls_context.setdefault("selected_horizon_col", _trading_agent_text(run_row.get("selected_horizon_col")))
    status = _trading_agent_text(result.get("status"))
    if status != "ok":
        st.info("Analysis unavailable for this horizon.")
        return

    show_narrative = (
        _trading_agent_text(result.get("narrative_contract_version"))
        in {TRADING_AGENT_NARRATIVE_CONTRACT_VERSION, *TRADING_AGENT_LEGACY_NARRATIVE_CONTRACTS}
    )
    if not show_narrative:
        st.info("Analysis unavailable for this horizon.")
        return
    st.subheader("Market Backdrop")
    st.write(_trading_agent_text(result.get("regime_read")))
    posture = _trading_agent_text(result.get("portfolio_posture"))
    if posture:
        st.caption(posture)

    horizon_candidates = candidates_frame.copy()
    if "horizon_key" in horizon_candidates.columns:
        horizon_candidates = horizon_candidates[horizon_candidates["horizon_key"].astype(str).eq(selected_horizon)].copy()
    if "rank" in horizon_candidates.columns:
        horizon_candidates["_rank"] = pd.to_numeric(horizon_candidates["rank"], errors="coerce")
        horizon_candidates = horizon_candidates.sort_values("_rank", ascending=True, na_position="last")

    if horizon_candidates.empty:
        st.info("No watchlist candidates cleared the evidence bar for this horizon.")
        return

    action_map = _latest_trading_agent_action_map(actions)
    candidates = [_trading_agent_candidate_from_row(row) for _, row in horizon_candidates.iterrows()]
    long_candidates = [
        item
        for item in candidates
        if _trading_agent_text(item.get("stance")).lower() == "long"
    ]
    short_candidates = [
        item
        for item in candidates
        if _trading_agent_text(item.get("stance")).lower() == "short"
    ]
    research_candidates = [
        item
        for item in candidates
        if _trading_agent_text(item.get("stance")).lower() not in {"long", "short"}
    ]

    if long_candidates:
        st.subheader("Long Calls")
        for idx, candidate in enumerate(long_candidates):
            ticker = _trading_agent_text(candidate.get("ticker")).upper()
            candidate_id = _trading_agent_text(candidate.get("candidate_id"))
            _render_trading_agent_candidate_card(
                candidate,
                context_item=context_lookup.get(ticker, {}),
                controls=controls_context,
                idx=idx,
                action_state=action_map.get(candidate_id, {}),
                show_narrative=show_narrative,
            )

    if short_candidates:
        st.subheader("Short Calls")
        offset = len(long_candidates)
        for idx, candidate in enumerate(short_candidates, start=offset):
            ticker = _trading_agent_text(candidate.get("ticker")).upper()
            candidate_id = _trading_agent_text(candidate.get("candidate_id"))
            _render_trading_agent_candidate_card(
                candidate,
                context_item=context_lookup.get(ticker, {}),
                controls=controls_context,
                idx=idx,
                action_state=action_map.get(candidate_id, {}),
                show_narrative=show_narrative,
            )

    if research_candidates:
        st.subheader("Watchlist")
        offset = len(long_candidates) + len(short_candidates)
        for idx, candidate in enumerate(research_candidates, start=offset):
            ticker = _trading_agent_text(candidate.get("ticker")).upper()
            candidate_id = _trading_agent_text(candidate.get("candidate_id"))
            _render_trading_agent_candidate_card(
                candidate,
                context_item=context_lookup.get(ticker, {}),
                controls=controls_context,
                idx=idx,
                action_state=action_map.get(candidate_id, {}),
                show_narrative=show_narrative,
            )

    with st.expander("Decisions", expanded=False):
        if actions.empty:
            st.info("No decisions yet.")
        else:
            display_actions = actions.copy()
            if "action" in display_actions.columns:
                display_actions["Decision"] = display_actions["action"].map(
                    {"place_requested": "Approved", "approved": "Approved", "rejected": "Rejected"}
                ).fillna(display_actions["action"])
            display_actions = display_actions.rename(
                columns={
                    "created_at_utc": "Time",
                    "ticker": "Ticker",
                    "horizon_key": "Horizon",
                    "requested_email": "Reviewed by",
                }
            )
            show_cols = [
                column
                for column in [
                    "Time",
                    "Ticker",
                    "Horizon",
                    "Decision",
                    "Reviewed by",
                ]
                if column in display_actions.columns
            ]
            st.dataframe(display_actions[show_cols], use_container_width=True, hide_index=True)
