"""Owned Alpaca paper account. Broker accounting, retained reads, explicit refresh.

Key Vault references live on an existing brokerage connection. This account is
independent of the main Portfolio provider and of historical research outcomes.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math
import os
import time
from typing import Any
import uuid

import pandas as pd

from services import auth_store, brokerage_mcp_store as store
from services.alpaca_api import AlpacaAPI
from services.auth_service import UserContext
from services.config import AppConfig
from services.secrets import get_secret_value_from_vault
from compute.paper_sizing import RiskPolicy, recommend_position, risk_after_quantity

PROVIDER = "alpaca_paper"
RESOURCE_URL = "https://paper-api.alpaca.markets"
EXECUTION_PRICE_TOLERANCE = 0.01
ACCOUNT_FIELDS = (
    "status",
    "currency",
    "equity",
    "cash",
    "buying_power",
    "last_equity",
    "long_market_value",
    "short_market_value",
    "initial_margin",
    "maintenance_margin",
    "trading_blocked",
    "account_blocked",
    "shorting_enabled",
    "pattern_day_trader",
    "daytrade_count",
)
POSITION_FIELDS = (
    "symbol",
    "asset_class",
    "qty",
    "side",
    "market_value",
    "cost_basis",
    "avg_entry_price",
    "current_price",
    "unrealized_pl",
    "unrealized_plpc",
    "change_today",
)
ORDER_FIELDS = (
    "id",
    "client_order_id",
    "symbol",
    "side",
    "type",
    "time_in_force",
    "qty",
    "notional",
    "filled_qty",
    "filled_avg_price",
    "status",
    "submitted_at",
    "filled_at",
    "canceled_at",
    "updated_at",
    "position_intent",
)
RISK_FIELDS = (
    "method",
    "policy",
    "symbol",
    "stance",
    "price",
    "price_as_of",
    "price_source",
    "price_dataset_version",
    "equity",
    "current_weight_pct",
    "target_weight_pct",
    "suggested_notional",
    "suggested_quantity",
    "asset_volatility_pct",
    "portfolio_volatility_before_pct",
    "portfolio_volatility_after_pct",
    "binding_limit",
    "historical_daily_expected_shortfall_pct",
    "observations",
    "history_start",
    "history_end",
    "covariance_shrinkage",
    "execution_price_tolerance_pct",
)


class PaperAccountError(RuntimeError):
    pass


def environment_name() -> str:
    value = (os.getenv("APP_TRACK") or "local").strip().lower()
    if not value or not all(c.isalnum() or c in "-_" for c in value):
        raise PaperAccountError("Invalid application environment.")
    return value


def _admin(context: UserContext) -> None:
    if not auth_store.user_has_active_portfolio_admin_membership(
        user_id=context.user_id, portfolio_id=context.portfolio_id
    ):
        raise PermissionError("Administrator access is required for paper trading.")


def connection(context: UserContext) -> dict[str, Any] | None:
    _admin(context)
    return store.get_connection(
        user_id=context.user_id,
        provider=PROVIDER,
        resource_url=RESOURCE_URL,
        environment=environment_name(),
    )


def api_for_connection(link: dict[str, Any]) -> AlpacaAPI:
    if (
        link.get("provider") != PROVIDER
        or link.get("resource_url") != RESOURCE_URL
        or link.get("environment") != environment_name()
        or link.get("status") != "connected"
    ):
        raise PaperAccountError("Paper account connection is unavailable.")
    refs = link.get("server_info") or {}
    key = get_secret_value_from_vault(
        refs.get("key_secret_name", ""), vault_name=refs.get("vault_name", "")
    )
    secret = get_secret_value_from_vault(
        refs.get("secret_secret_name", ""), vault_name=refs.get("vault_name", "")
    )
    if not key or not secret:
        raise PaperAccountError("Paper account credentials are unavailable.")
    return AlpacaAPI(AppConfig(key, secret, alpaca_trading_base_url=RESOURCE_URL))


def _account(api: AlpacaAPI, link: dict[str, Any]) -> dict:
    account = api.paper_request("GET", "/v2/account")
    if str(account.get("id")) != str(link["server_info"]["account_id"]):
        raise PaperAccountError(
            "The paper credentials now refer to a different account. Reconnect it explicitly."
        )
    return account


def connect(
    context: UserContext,
    *,
    vault_name: str,
    key_secret_name: str,
    secret_secret_name: str,
) -> dict[str, Any]:
    """Operator setup; no public route accepts secret names or raw credentials."""
    _admin(context)
    refs = {
        "vault_name": vault_name,
        "key_secret_name": key_secret_name,
        "secret_secret_name": secret_secret_name,
    }
    pending = {
        "provider": PROVIDER,
        "resource_url": RESOURCE_URL,
        "environment": environment_name(),
        "status": "connected",
        "server_info": refs,
    }
    account = api_for_connection(pending).paper_request("GET", "/v2/account")
    if not account.get("id") or account.get("status") != "ACTIVE":
        raise PaperAccountError("The Alpaca paper account is not active.")
    refs["account_id"] = account["id"]
    store.save_direct_connection(
        user_id=context.user_id,
        provider=PROVIDER,
        resource_url=RESOURCE_URL,
        environment=environment_name(),
        access_token_ciphertext="",
        refresh_token_ciphertext="",
        device_token_ciphertext="",
        token_type="key_vault",
        scope="paper_trading",
        token_expires_at=None,
        server_info=refs,
    )
    return refresh(context)


def _snapshot(context: UserContext, link: dict[str, Any]) -> dict[str, Any] | None:
    snapshot = store.get_portfolio_snapshot(
        portfolio_id=context.portfolio_id,
        connection_id=link["id"],
        user_id=context.user_id,
        environment=environment_name(),
    )
    if (
        snapshot
        and (snapshot.get("details") or {}).get("account_id")
        == link["server_info"]["account_id"]
    ):
        return snapshot
    return None


def _select(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: row[key] for key in fields if key in row}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def project(snapshot: dict[str, Any], *, period: str = "1M") -> dict[str, Any]:
    """Explicit public allowlist. Never return connection metadata or raw account identity."""
    from compute.paper_portfolio import period_performance, select_period

    details = snapshot.get("details") or {}
    summary = _select(snapshot.get("account_summary") or {}, ACCOUNT_FIELDS)
    for field in (
        "equity",
        "cash",
        "buying_power",
        "last_equity",
        "long_market_value",
        "short_market_value",
        "initial_margin",
        "maintenance_margin",
        "daytrade_count",
    ):
        if field in summary:
            summary[field] = _number(summary[field])
    equity, previous = summary.get("equity"), summary.get("last_equity")
    summary["day_pl"] = (
        equity - previous if equity is not None and previous is not None else None
    )
    summary["day_return_pct"] = (
        (equity / previous - 1) * 100
        if equity is not None and previous and previous > 0
        else None
    )
    points = [
        _select(
            row,
            (
                "date",
                "equity",
                "profit_loss",
                "return_pct",
                "external_cashflow",
                "cashflow_coverage",
            ),
        )
        for row in snapshot.get("timeseries") or []
    ]
    first_funded = next(
        (
            index
            for index, point in enumerate(points)
            if (_number(point.get("equity")) or 0) != 0
        ),
        None,
    )
    points = points[first_funded:] if first_funded is not None else []
    intraday = details.get("intraday") or []
    daily_anchor = next(
        (
            point
            for point in reversed(points)
            if intraday
            and pd.to_datetime(point["date"], utc=True)
            < pd.to_datetime(intraday[0]["date"], utc=True)
        ),
        None,
    )
    day_points = (
        ([daily_anchor] if daily_anchor else []) + intraday
        if intraday
        else select_period(points, "1D")
    )
    day_performance = period_performance(day_points)
    summary["day_pl"] = day_performance["profit_loss"]
    summary["day_return_pct"] = day_performance["return_pct"]
    analytics = (
        day_performance
        if period == "1D"
        else period_performance(select_period(points, period))
    )
    points = analytics["path"]
    positions = [
        _select(row, POSITION_FIELDS) for row in snapshot.get("positions") or []
    ]
    for position in positions:
        for field in (
            "market_value",
            "cost_basis",
            "avg_entry_price",
            "current_price",
            "unrealized_pl",
            "unrealized_plpc",
            "change_today",
        ):
            if field in position:
                position[field] = _number(position[field])
    summary["unrealized_pl"] = (
        sum(position.get("unrealized_pl") or 0 for position in positions)
        if all(position.get("unrealized_pl") is not None for position in positions)
        else None
    )
    orders = [_select(row, ORDER_FIELDS) for row in details.get("orders") or []]
    for order in orders:
        if "filled_avg_price" in order:
            order["filled_avg_price"] = _number(order["filled_avg_price"])
    observed = snapshot.get("source_updated_at")
    as_of = (
        observed.isoformat() if isinstance(observed, datetime) else str(observed or "")
    )
    return {
        "status": "partial" if details.get("errors") else "available",
        "provider": PROVIDER,
        "risk_policy": asdict(RiskPolicy()),
        "as_of": as_of,
        "period": period,
        "summary": summary,
        "holdings": positions,
        "path": points,
        "return_pct": analytics["return_pct"],
        "profit_loss": analytics["profit_loss"],
        "performance": analytics["performance"],
        "performance_method": analytics.get("method"),
        "cashflow_coverage": analytics.get("cashflow_coverage"),
        "max_drawdown_pct": analytics["performance"].get("max_drawdown_pct"),
        "orders": orders,
        "coverage": details.get("coverage") or {},
        "unavailable": sorted((details.get("errors") or {}).keys()),
    }


def view(context: UserContext, *, period: str = "1M") -> dict[str, Any]:
    link = connection(context)
    if not link or link.get("status") != "connected":
        return {"status": "unlinked", "provider": PROVIDER}
    snapshot = _snapshot(context, link)
    return (
        project(snapshot, period=period)
        if snapshot
        else {"status": "pending", "provider": PROVIDER}
    )


def _activity_page(api: AlpacaAPI, previous: dict, now: datetime) -> dict:
    state = dict(previous.get("activities_sync") or {})
    if not state.get("started_at"):
        state["started_at"] = now.isoformat()
        if state.get("complete_through"):
            state["after"] = (
                datetime.fromisoformat(state["complete_through"]) - timedelta(days=2)
            ).isoformat()
    params = {"direction": "asc", "page_size": 100}
    if state.get("after"):
        params["after"] = state["after"]
    rows = []
    deadline = time.monotonic() + 20
    allowed = (
        "id",
        "activity_type",
        "type",
        "order_id",
        "transaction_time",
        "date",
        "symbol",
        "qty",
        "price",
        "side",
        "cum_qty",
        "leaves_qty",
        "order_status",
        "net_amount",
        "description",
    )
    for _ in range(5):
        if state.get("page_token"):
            params["page_token"] = state["page_token"]
        page = api.paper_request("GET", "/v2/account/activities", params=params)
        if not isinstance(page, list) or any(
            not item.get("id") or not (item.get("transaction_time") or item.get("date"))
            for item in page
        ):
            raise ValueError("Broker activities are invalid.")
        rows.extend(_select(item, allowed) for item in page)
        if len(page) < 100:
            return {
                "rows": rows,
                "state": {"complete_through": state["started_at"]},
                "complete": True,
            }
        token = str(page[-1]["id"])
        if token == state.get("page_token"):
            raise ValueError("Broker activity pagination did not advance.")
        state["page_token"] = token
        if time.monotonic() >= deadline:
            break
    return {"rows": rows, "state": state, "complete": False}


def refresh(context: UserContext) -> dict[str, Any]:
    """Explicit bounded broker acquisition and durable reconciliation; never a GET."""
    from compute.paper_portfolio import equity_history
    from services import trading_portfolio
    from services.trading_agent_view import _build_report_view

    link = connection(context)
    if not link:
        raise PaperAccountError("No Alpaca paper account is linked.")
    api = api_for_connection(link)
    old = _snapshot(context, link) or {}
    old_details = old.get("details") or {}
    current = _build_report_view(context)
    trading_portfolio.retain(
        context,
        [
            c
            for h in current.get("horizons", [])
            for c in h.get("candidates", [])
            if not c.get("decision")
        ],
        proposed_at=current.get("as_of"),
    )
    account = _account(api, link)
    now = datetime.now(timezone.utc)
    coverage = dict(old_details.get("coverage") or {})
    coverage["account"] = now.isoformat()
    errors, fetched = {}, {}
    history_params = {"period": "1A", "timeframe": "1D", "cashflow_types": "ALL"}
    if account.get("created_at"):
        history_params = {
            "start": account["created_at"],
            "end": now.isoformat(),
            "timeframe": "1D",
            "cashflow_types": "ALL",
        }
    queries = {
        "positions": ("/v2/positions", {}),
        "orders": ("/v2/orders", {"status": "all", "limit": 100, "direction": "desc"}),
        "history": ("/v2/account/portfolio/history", history_params),
        "intraday": (
            "/v2/account/portfolio/history",
            {
                "period": "1W",
                "end": now.isoformat(),
                "timeframe": "5Min",
                "cashflow_types": "ALL",
                "intraday_reporting": "market_hours",
                "pnl_reset": "no_reset",
            },
        ),
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {
            pool.submit(api.paper_request, "GET", path, params=params): key
            for key, (path, params) in queries.items()
        }
        pending[pool.submit(_activity_page, api, old_details, now)] = "activities"
        for future in as_completed(pending):
            key = pending[future]
            try:
                value = future.result()
                if not isinstance(
                    value,
                    dict if key in {"history", "intraday", "activities"} else list,
                ):
                    raise ValueError("Invalid broker response.")
                fetched[key] = value
                coverage[key] = now.isoformat()
            except Exception as exc:
                errors[key] = type(exc).__name__
    intraday = old_details.get("intraday") or []
    if "intraday" in fetched:
        try:
            received = equity_history(fetched["intraday"])
            dates = [
                pd.to_datetime(point["date"], utc=True)
                .tz_convert("America/New_York")
                .date()
                for point in received
            ]
            latest = max(dates) if dates else None
            intraday = [point for point, date in zip(received, dates) if date == latest]
        except ValueError:
            errors["intraday"] = "InvalidHistory"
            coverage["intraday"] = (old_details.get("coverage") or {}).get("intraday")
    points = old.get("timeseries") or []
    if "history" in fetched:
        try:
            received = equity_history(fetched["history"])
            points = sorted(
                {p["date"]: p for p in [*points, *received]}.values(),
                key=lambda p: p["date"],
            )
        except ValueError:
            errors["history"] = "InvalidHistory"
            coverage["history"] = (old_details.get("coverage") or {}).get("history")
    activity_result = fetched.get("activities") or {}
    new_activities = activity_result.get("rows") or []
    # The ledger commits before its pagination checkpoint advances.
    store.retain_paper_events(
        context,
        environment=environment_name(),
        events=[
            {
                "id": "activity:" + link["id"] + ":" + str(row["id"]),
                "kind": "broker_activity",
                "connection_id": link["id"],
                "occurred_at": row.get("transaction_time") or row.get("date"),
                "payload": row,
            }
            for row in new_activities
        ],
    )
    mapped_orders = [
        o["data"]
        for o in store.paper_state(context, environment=environment_name())["orders"]
        if o["data"].get("id")
    ]
    retained_orders = {
        str(o["id"]): o
        for o in [
            *mapped_orders,
            *(old_details.get("orders") or []),
            *fetched.get("orders", []),
        ]
        if o.get("id")
    }
    # Refresh every known nonterminal order even after it falls outside the
    # provider's newest-100 query. A long-lived order cannot disappear from view.
    order_deadline = time.monotonic() + 20
    for order in list(retained_orders.values()):
        if time.monotonic() >= order_deadline:
            errors["orders"] = "RefreshIncomplete"
            break
        if order.get(
            "status"
        ) not in trading_portfolio.TERMINAL_ORDERS and order not in fetched.get(
            "orders", []
        ):
            try:
                latest = api.paper_request(
                    "GET", "/v2/orders/" + str(uuid.UUID(str(order["id"])))
                )
                retained_orders[str(latest["id"])] = latest
            except Exception as exc:
                errors["orders"] = type(exc).__name__
    saved = store.save_portfolio_snapshot(
        portfolio_id=context.portfolio_id,
        connection_id=link["id"],
        provider=PROVIDER,
        environment=environment_name(),
        account_summary=_select(account, ACCOUNT_FIELDS),
        positions=[
            _select(row, POSITION_FIELDS)
            for row in fetched.get("positions", old.get("positions") or [])
        ],
        timeseries=points,
        source_updated_at=now,
        details={
            "account_id": link["server_info"]["account_id"],
            "orders": [_select(row, ORDER_FIELDS) for row in retained_orders.values()],
            "activities_sync": activity_result.get("state")
            or old_details.get("activities_sync")
            or {},
            "activities_complete": activity_result.get(
                "complete", old_details.get("activities_complete", False)
            ),
            "new_activities": new_activities,
            "intraday": intraday,
            "coverage": coverage,
            "errors": errors,
        },
    )
    trading_portfolio.reconcile(context, link, saved)
    return project(saved)


def _risk_returns(
    api: AlpacaAPI, symbols: list[str], now: pd.Timestamp
) -> pd.DataFrame:
    frames = api.get_stock_bars(
        symbols,
        start=(now - pd.Timedelta(days=550)).to_pydatetime(),
        end=now.to_pydatetime(),
        timeout_seconds=30,
    )
    frame = (
        pd.concat(
            [data.assign(symbol=key) for key, data in frames.items() if not data.empty],
            ignore_index=True,
        )
        if any(not data.empty for data in frames.values())
        else pd.DataFrame(columns=["symbol", "timestamp", "close"])
    )
    from services.trading_call_tracking import completed_daily_prices

    prices = completed_daily_prices(frame, now)
    matrix = prices.pivot(
        index="session_date", columns="symbol", values="close"
    ).sort_index()
    today = now.tz_convert("America/New_York").date()
    calendar = api.paper_request(
        "GET",
        "/v2/calendar",
        params={
            "start": (today - timedelta(days=550)).isoformat(),
            "end": today.isoformat(),
        },
    )
    last_session = max(
        str(row["date"]) for row in calendar if str(row["date"]) < today.isoformat()
    )
    if (
        matrix.empty
        or matrix.index[-1] != last_session
        or any(
            key not in matrix or pd.isna(matrix.loc[last_session, key])
            for key in symbols
        )
    ):
        raise PaperAccountError(
            "The portfolio’s daily risk history is not current through the last closed session."
        )
    matrix = matrix.reindex(
        sorted(
            str(row["date"]) for row in calendar if str(row["date"]) < today.isoformat()
        )
    )
    # Reindex to the observed common market sessions before differencing: absent
    # bars remain missing, never a zero or an unlabelled multi-day return.
    daily_returns = matrix.pct_change(fill_method=None)
    return daily_returns


def _order_inputs(
    context: UserContext, link: dict[str, Any], candidate: dict, policy: RiskPolicy
) -> tuple[dict, dict]:
    """Fresh broker facts plus the versioned completed-session research data."""
    api = api_for_connection(link)
    symbol = api._normalize_symbol(candidate.get("ticker") or "")
    account = _account(api, link)
    if (
        account.get("status") != "ACTIVE"
        or account.get("trading_blocked")
        or account.get("account_blocked")
    ):
        raise PaperAccountError("Alpaca has blocked paper trading on this account.")
    clock = api.paper_request("GET", "/v2/clock")
    if not clock.get("is_open"):
        raise PaperAccountError(
            "Paper market orders can be reviewed while the market is open."
        )
    positions = api.paper_request("GET", "/v2/positions")
    orders = api.paper_request(
        "GET", "/v2/orders", params={"status": "open", "limit": 1}
    )
    if orders:
        raise PaperAccountError(
            "Wait for the open paper order to fill or cancel before sizing another trade."
        )
    if any(str(p.get("asset_class")) != "us_equity" for p in positions):
        raise PaperAccountError(
            "The sizing model needs a risk model for the non-equity holdings in this account."
        )
    asset = api.paper_request("GET", "/v2/assets/" + symbol)
    if not asset.get("tradable") or asset.get("class") != "us_equity":
        raise PaperAccountError("This asset is not available for equity paper trading.")
    if candidate.get("stance") == "short" and (
        not account.get("shorting_enabled")
        or not asset.get("shortable")
        or not asset.get("easy_to_borrow")
    ):
        raise PaperAccountError("Alpaca does not currently offer this paper short.")
    quote = (
        api._request(
            "GET",
            api.config.alpaca_data_base_url,
            "/v2/stocks/snapshots",
            params={"symbols": symbol, "feed": "iex"},
            timeout=10,
            max_attempts=1,
        ).get(symbol)
        or {}
    )
    latest = quote.get("latestTrade") or {}
    price = _number(latest.get("p"))
    now = pd.Timestamp.now(tz="UTC")
    observed = pd.to_datetime(latest.get("t"), utc=True, errors="coerce")
    if (
        price is None
        or price <= 0
        or pd.isna(observed)
        or not 0 <= (now - observed).total_seconds() <= 120
    ):
        raise PaperAccountError("A recent trade price is unavailable for order sizing.")
    equity = float(account["equity"])
    existing = next((p for p in positions if str(p.get("symbol")) == symbol), None)
    if existing and (float(existing["qty"]) > 0) != (candidate["stance"] == "long"):
        raise PaperAccountError(
            "Close or reduce the existing opposite position before entering this call."
        )
    weights = {str(p["symbol"]): float(p["market_value"]) / equity for p in positions}
    symbols = sorted({symbol, *weights})
    daily_returns = _risk_returns(api, symbols, now)
    risk = recommend_position(
        returns=daily_returns,
        weights=weights,
        symbol=symbol,
        stance=candidate["stance"],
        equity=equity,
        buying_power=float(account["buying_power"]),
        cash=float(account["cash"]),
        price=price,
        policy=policy,
    )
    quantum = (
        Decimal("0.000001")
        if asset.get("fractionable") and candidate["stance"] == "long"
        else Decimal("1")
    )
    capacity = Decimal(str(risk["suggested_quantity"]))
    # Reserve price movement before the human confirms. Revalidation compares
    # the exact reviewed shares with fresh full capacity, not another buffered
    # recommendation (which would reject every upward price tick).
    quantity = (capacity * Decimal(str(1 - EXECUTION_PRICE_TOLERANCE))).quantize(
        quantum, rounding=ROUND_DOWN
    )
    if quantity <= 0:
        raise PaperAccountError(
            "The risk allocation is below Alpaca’s order quantity increment."
        )
    observations = daily_returns[risk["symbols"]].dropna().tail(252)
    retained_returns = {
        "dates": observations.index.tolist(),
        "symbols": observations.columns.tolist(),
        "values": observations.to_numpy().tolist(),
    }
    risk.update(
        capacity_quantity=str(capacity),
        execution_price_tolerance_pct=EXECUTION_PRICE_TOLERANCE * 100,
        risk_as_of=now.isoformat(),
        price_as_of=observed.isoformat(),
        price_source="Alpaca · IEX · all corporate-action adjustments",
        price_dataset_version=hashlib.sha256(
            json.dumps(retained_returns, sort_keys=True).encode()
        ).hexdigest(),
        retained_returns=retained_returns,
        suggested_quantity=str(quantity),
        suggested_notional=float(quantity) * price,
    )
    risk = risk_after_quantity(risk, str(quantity))
    client_id = (
        "sn-"
        + hashlib.sha256(
            json.dumps(
                [
                    context.user_id,
                    context.portfolio_id,
                    environment_name(),
                    account["id"],
                    candidate["candidate_id"],
                ]
            ).encode()
        ).hexdigest()[:40]
    )
    order = {
        "symbol": symbol,
        "side": "buy" if candidate["stance"] == "long" else "sell",
        "qty": str(quantity),
        "position_intent": (
            "buy_to_open" if candidate["stance"] == "long" else "sell_to_open"
        ),
        "type": "market",
        "time_in_force": "day",
        "client_order_id": client_id,
    }
    return order, risk


def _candidate(context: UserContext, candidate_id: str) -> dict:
    from services import trading_portfolio

    retained = store.paper_call(
        context, environment=environment_name(), identity=candidate_id
    )
    if retained:
        if retained["decision"] not in {"pending", "draft"}:
            raise ValueError("This proposal was already decided.")
        candidate = dict(retained["candidate"])
        candidate["intent"] = retained["intent"]
        candidate["parent_call_id"] = retained.get("parent_call_id")
        if retained["intent"] == "entry":
            end = trading_portfolio._date(retained.get("valid_until"))
            if end and end <= datetime.now(timezone.utc):
                raise ValueError("The original call’s forecast horizon has ended.")
        return candidate
    from services.trading_agent_view import build_trading_agent_view

    current = build_trading_agent_view(context)
    candidate = next(
        (
            row
            for h in current.get("horizons", [])
            for row in h.get("candidates", [])
            if row.get("candidate_id") == candidate_id
        ),
        None,
    )
    if not candidate or candidate.get("stance") not in {"long", "short"}:
        raise ValueError("This directional call is unavailable.")
    trading_portfolio.retain(context, [candidate], proposed_at=current.get("as_of"))
    return _candidate(context, candidate_id)


def _management_inputs(
    context: UserContext, link: dict, candidate: dict, policy: RiskPolicy
) -> tuple[dict, dict]:
    from compute.paper_sizing import measure_position_change
    from services import trading_portfolio

    api = api_for_connection(link)
    account = _account(api, link)
    if not api.paper_request("GET", "/v2/clock").get("is_open"):
        raise PaperAccountError(
            "Paper market orders can be reviewed while the market is open."
        )
    symbol = api._normalize_symbol(candidate["ticker"])
    positions = api.paper_request("GET", "/v2/positions")
    position = next((p for p in positions if p.get("symbol") == symbol), None)
    if not position or position.get("asset_class") != "us_equity":
        raise PaperAccountError("This paper equity position is no longer open.")
    working = api.paper_request(
        "GET", "/v2/orders", params={"status": "open", "limit": 100}
    )
    if any(o.get("symbol") == symbol for o in working):
        raise PaperAccountError(
            "An order for this position is still working. Reconcile it before another change."
        )
    actual = Decimal(str(position["qty"]))
    available = abs(Decimal(str(position.get("qty_available", actual))))
    parent = candidate.get("parent_call_id")
    allocated = available
    if parent:
        original = store.paper_call(
            context, environment=environment_name(), identity=parent
        )
        performance = (original or {}).get("performance") or {}
        if performance.get("status") != "open":
            raise PaperAccountError(
                "This call’s fill allocation must be reconciled before changing it."
            )
        allocated = Decimal(str(performance["quantity"]))
        if (actual > 0) != (candidate["stance"] == "long") or allocated > abs(actual):
            raise PaperAccountError(
                "The broker position changed. Refresh and reconcile the call first."
            )
    asset = api.paper_request("GET", "/v2/assets/" + symbol)
    quantum = (
        Decimal("0.000001")
        if actual > 0 and asset.get("fractionable")
        else Decimal("1")
    )
    quantity = Decimal(str(candidate.get("quantity") or allocated))
    if (
        quantity <= 0
        or quantity > min(available, allocated)
        or quantity.as_tuple().exponent < -6
    ):
        raise ValueError(
            "The quantity must fit this call’s available shares, with at most six decimal places."
        )
    if quantity % quantum:
        raise ValueError(
            "This position requires whole shares."
            if quantum == 1
            else "Use at most six decimal places."
        )
    if candidate.get("intent") == "reduce" and quantity >= allocated:
        raise ValueError("Choose close to exit the entire call.")
    price = _number(position.get("current_price"))
    equity = _number(account.get("equity"))
    if not price or not equity or equity <= 0:
        raise PaperAccountError(
            "A current broker value is unavailable for this position."
        )
    delta = (-1 if actual > 0 else 1) * float(quantity) * price / equity
    weights = {p["symbol"]: float(p["market_value"]) / equity for p in positions}
    risk = {
        "policy": asdict(policy),
        "risk_status": "unavailable",
        "price": price,
        "price_as_of": datetime.now(timezone.utc).isoformat(),
        "price_source": "Alpaca paper position mark",
        "equity": equity,
        "suggested_quantity": str(quantity),
        "suggested_notional": float(quantity) * price,
        "current_quantity": str(abs(actual)),
        "remaining_quantity": str(abs(actual) - quantity),
        "binding_limit": "call_quantity",
        "execution_price_tolerance_pct": EXECUTION_PRICE_TOLERANCE * 100,
    }
    try:
        if any(p.get("asset_class") != "us_equity" for p in positions):
            raise ValueError("The account contains unsupported risk exposures.")
        returns = _risk_returns(api, sorted(weights), pd.Timestamp.now(tz="UTC"))
        risk.update(
            measure_position_change(
                returns=returns,
                weights=weights,
                symbol=symbol,
                delta_weight=delta,
                policy=policy,
            ),
            risk_status="available",
        )
    except (ValueError, PaperAccountError) as exc:
        risk["risk_error"] = str(exc)
    client_id = (
        "sn-"
        + hashlib.sha256(
            json.dumps(
                [
                    context.user_id,
                    context.portfolio_id,
                    environment_name(),
                    account["id"],
                    candidate["candidate_id"],
                ]
            ).encode()
        ).hexdigest()[:40]
    )
    return {
        "symbol": symbol,
        "side": "sell" if actual > 0 else "buy",
        "qty": str(quantity),
        "position_intent": "sell_to_close" if actual > 0 else "buy_to_close",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": client_id,
    }, risk


def _execution_policy(
    context: UserContext,
    candidate: dict,
    *,
    expected_revision: int | None = None,
    automatic: bool = False,
    expected_link: dict | None = None,
) -> dict:
    from services.trading_portfolio import policy, _date

    _admin(context)
    if expected_link is not None:
        fresh = connection(context)
        if (
            not fresh
            or fresh["id"] != expected_link["id"]
            or fresh["status"] != "connected"
            or fresh["server_info"]["account_id"]
            != expected_link["server_info"]["account_id"]
        ):
            raise PaperAccountError(
                "The paper connection changed. Review the order again."
            )
    if candidate.get("candidate_id"):
        current = store.paper_call(
            context, environment=environment_name(), identity=candidate["candidate_id"]
        )
        if not current or current["decision"] not in {"pending", "draft"}:
            raise ValueError("This proposal was already decided.")
    saved = policy(context)
    if expected_revision is not None and saved["revision"] != expected_revision:
        raise ValueError("Trading settings changed. Review this order again.")
    if automatic and saved["mode"] != "autonomous":
        raise PermissionError("Paper autopilot is not enabled.")
    intent = candidate.get("intent") or "entry"
    source = candidate.get("proposal_source", "agent" if intent == "entry" else "owner")
    if automatic and source != "agent":
        raise PermissionError(
            "Owner-requested changes require their exact confirmation."
        )
    if intent == "entry":
        if saved["entries_paused"]:
            raise PaperAccountError("New entries are paused.")
        end = _date((candidate.get("forecast") or {}).get("end_at_utc"))
        if end and end <= datetime.now(timezone.utc):
            raise ValueError("This call’s forecast horizon has ended.")
    return saved


def review_order(
    context: UserContext,
    *,
    candidate_id: str = "",
    policy: RiskPolicy | None = None,
    intent: str = "entry",
    call_id: str = "",
    quantity: str | None = None,
    order_id: str = "",
    symbol: str = "",
) -> dict:
    from services import trading_portfolio

    link = connection(context)
    if not link:
        raise PaperAccountError("No Alpaca paper account is linked.")
    if intent not in {"entry", "reduce", "close", "cancel"}:
        raise ValueError("Unsupported paper action.")
    if candidate_id:
        candidate = _candidate(context, candidate_id)
        intent = candidate.get("intent") or "entry"
    else:
        original = (
            store.paper_call(context, environment=environment_name(), identity=call_id)
            if call_id
            else None
        )
        if call_id and original is None:
            raise ValueError("This call is unavailable.")
        if original:
            candidate = {**original["candidate"], "parent_call_id": original["id"]}
        elif intent in {"reduce", "close"} and symbol:
            api = api_for_connection(link)
            _account(api, link)
            positions = api.paper_request("GET", "/v2/positions")
            position = next((p for p in positions if p.get("symbol") == symbol), None)
            if not position:
                raise ValueError("This position is unavailable.")
            owned = store.paper_state(context, environment=environment_name())["calls"]
            if any(
                c["intent"] == "entry"
                and c["candidate"].get("ticker") == symbol
                and (c.get("performance") or {}).get("status") == "open"
                for c in owned
            ):
                raise ValueError(
                    "Choose a linked call to close so its fills remain attributed."
                )
            candidate = {
                "ticker": symbol,
                "stance": "long" if Decimal(str(position["qty"])) > 0 else "short",
                "hypothesis": "Owner-requested position management",
                "horizon_label": "Paper position",
            }
        elif intent == "cancel":
            state = store.paper_state(context, environment=environment_name())
            mapping = next(
                (o for o in state["orders"] if o.get("broker_order_id") == order_id),
                None,
            )
            if not mapping:
                raise ValueError("This paper order is unavailable.")
            original = store.paper_call(
                context, environment=environment_name(), identity=mapping["call_id"]
            )
            candidate = {**original["candidate"], "parent_call_id": original["id"]}
        else:
            raise ValueError("Choose an existing call or position.")
        candidate = {
            **candidate,
            "candidate_id": "action-" + str(uuid.uuid4()),
            "intent": intent,
            "proposal_source": "owner",
        }
        if quantity is not None:
            candidate["quantity"] = quantity
        trading_portfolio.retain(context, [candidate])
    saved = _execution_policy(context, candidate, expected_link=link)
    policy = policy or RiskPolicy(**saved["risk_policy"])
    policy.validate()
    if any(value > saved["risk_policy"][key] for key, value in asdict(policy).items()):
        raise ValueError(
            "The review exceeds saved Trading limits. Update those limits before reviewing this order."
        )
    if intent == "cancel":
        api = api_for_connection(link)
        _account(api, link)
        order = api.paper_request("GET", "/v2/orders/" + str(uuid.UUID(order_id)))
        if order.get("status") in trading_portfolio.TERMINAL_ORDERS:
            raise ValueError("This order is already terminal.")
        risk = {"policy": asdict(policy), "risk_status": "not_applicable"}
    elif intent in {"reduce", "close"}:
        order, risk = _management_inputs(context, link, candidate, policy)
    else:
        order, risk = _order_inputs(context, link, candidate, policy)
    payload = {
        "order": order,
        "candidate": candidate,
        "risk": risk,
        "portfolio_id": context.portfolio_id,
        "account_id": link["server_info"]["account_id"],
        "intent": intent,
        "policy_revision": saved["revision"],
    }
    if intent == "cancel":
        payload["cancel_order_id"] = order["id"]
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(seconds=120)
    ticket = store.create_order_ticket(
        connection_id=link["id"],
        user_id=context.user_id,
        provider=PROVIDER,
        environment=environment_name(),
        review_tool_name="review_paper_order",
        execution_tool_name="submit_paper_order",
        arguments_hash=digest,
        ref_id=str(uuid.uuid4()),
        request_summary=payload,
        expires_at=expires,
    )
    public_risk = _select(
        risk,
        (
            *RISK_FIELDS,
            "risk_status",
            "risk_error",
            "current_quantity",
            "remaining_quantity",
            "gross_after_pct",
        ),
    )
    return {
        "ticket_id": ticket["id"],
        "arguments_hash": digest,
        "expires_at": expires.isoformat(),
        "intent": intent,
        "order": _select(order, ORDER_FIELDS),
        "risk": public_risk,
    }


def confirm_order(
    context: UserContext,
    *,
    ticket_id: str,
    arguments_hash: str,
    automatic: bool = False,
) -> dict:
    """Confirm an exact review under the paper account's cross-replica lock."""
    from services.alpaca_api import AlpacaAPIError
    from services import trading_portfolio

    link = connection(context)
    if not link:
        raise PaperAccountError("No Alpaca paper account is linked.")
    ticket = store.get_order_ticket(
        ticket_id=ticket_id,
        user_id=context.user_id,
        provider=PROVIDER,
        environment=environment_name(),
    )
    if (
        not ticket
        or ticket["connection_id"] != link["id"]
        or ticket.get("arguments_hash") != arguments_hash
    ):
        raise ValueError("The paper order review is unavailable.")
    payload = ticket["request_summary"]
    candidate = payload["candidate"]
    order = payload["order"]
    intent = payload.get("intent", "entry")
    if payload.get("portfolio_id") != context.portfolio_id:
        raise PermissionError("This order belongs to another portfolio.")
    if payload.get("account_id") != link["server_info"]["account_id"]:
        raise ValueError("The paper account changed. Review the order again.")
    api = api_for_connection(link)
    action_id = trading_portfolio.identity(context, candidate["candidate_id"])
    call_id = candidate.get("parent_call_id") or action_id
    lock = auth_store.open_auth_store_connection()
    if lock is None:
        raise PaperAccountError("Paper trading is unavailable.")
    try:
        with lock, lock.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
                ("alpaca_paper:" + payload["account_id"],),
            )
            if not cur.fetchone()[0]:
                raise PaperAccountError(
                    "Another paper order is being processed. Refresh shortly."
                )
            _admin(context)
            _account(api, link)
            if ticket["status"] in {"executed", "executing", "unknown"}:
                try:
                    existing = (
                        api.paper_request(
                            "GET", "/v2/orders/" + payload["cancel_order_id"]
                        )
                        if intent == "cancel"
                        else api.paper_request(
                            "GET",
                            "/v2/orders:by_client_order_id",
                            params={"client_order_id": order["client_order_id"]},
                        )
                    )
                    if (
                        intent == "cancel"
                        and ticket["status"] != "executed"
                        and existing.get("status")
                        not in trading_portfolio.TERMINAL_ORDERS | {"pending_cancel"}
                    ):
                        raise PaperAccountError(
                            "Cancellation status is still being reconciled. Refresh its status."
                        )
                    if intent != "cancel":
                        api.validate_order_receipt(existing, order)
                    receipt = _select(existing, ORDER_FIELDS)
                    store.finish_order_ticket(
                        ticket_id=ticket_id,
                        status="executed",
                        result_summary={"order": receipt, "reconciled": True},
                    )
                except AlpacaAPIError as exc:
                    raise PaperAccountError(
                        "This submission is still being reconciled. Refresh its status."
                    ) from exc
            else:
                saved = _execution_policy(
                    context,
                    candidate,
                    expected_revision=payload.get("policy_revision"),
                    automatic=automatic,
                    expected_link=link,
                )
                current = store.paper_call(
                    context, environment=environment_name(), identity=action_id
                )
                if not current or current["decision"] not in {"pending", "draft"}:
                    raise ValueError("This proposal has already been decided.")
                # Unresolved submissions reserve the account, even across environments.
                schema = auth_store.auth_store_schema_name()
                cur.execute(
                    f"""SELECT 1 FROM {schema}.brokerage_order_tickets
                    WHERE provider=%s AND id<>%s::uuid AND status IN ('executing','unknown')
                    AND request_summary_json->>'account_id'=%s LIMIT 1""",
                    (PROVIDER, ticket_id, payload["account_id"]),
                )
                if cur.fetchone():
                    raise PaperAccountError(
                        "An earlier submission needs reconciliation before another order."
                    )
                if not store.consume_order_ticket(
                    ticket_id=ticket_id,
                    user_id=context.user_id,
                    provider=PROVIDER,
                    environment=environment_name(),
                    arguments_hash=arguments_hash,
                ):
                    raise ValueError(
                        "This review expired or was already used. Review again."
                    )
                attempted = False
                risk = None
                try:
                    limits = RiskPolicy(**payload["risk"]["policy"])
                    if intent == "cancel":
                        existing = api.paper_request(
                            "GET",
                            "/v2/orders/" + str(uuid.UUID(payload["cancel_order_id"])),
                        )
                        if existing.get("client_order_id") != order.get(
                            "client_order_id"
                        ):
                            raise ValueError("The reviewed order changed.")
                        if existing.get("status") in trading_portfolio.TERMINAL_ORDERS:
                            result = existing
                        else:
                            _execution_policy(
                                context,
                                candidate,
                                expected_revision=saved["revision"],
                                automatic=automatic,
                                expected_link=link,
                            )
                            attempted = True
                            api.paper_request("DELETE", "/v2/orders/" + existing["id"])
                            result = api.paper_request(
                                "GET", "/v2/orders/" + existing["id"]
                            )
                    else:
                        if intent in {"reduce", "close"}:
                            _, risk = _management_inputs(
                                context,
                                link,
                                {**candidate, "quantity": order["qty"]},
                                limits,
                            )
                            if automatic and (
                                risk.get("risk_status") != "available"
                                or risk["portfolio_volatility_after_pct"]
                                > limits.portfolio_volatility_pct
                                or risk["gross_after_pct"] > limits.max_gross_pct
                            ):
                                raise PaperAccountError(
                                    "The position change needs owner review under current risk limits."
                                )
                        else:
                            _, risk = _order_inputs(context, link, candidate, limits)
                            if Decimal(order["qty"]) > Decimal(
                                risk["capacity_quantity"]
                            ):
                                raise ValueError(
                                    "The reviewed shares exceed the current allocation. Review again."
                                )
                            risk = risk_after_quantity(risk, order["qty"])
                        if (
                            abs(
                                float(risk["price"]) / float(payload["risk"]["price"])
                                - 1
                            )
                            > EXECUTION_PRICE_TOLERANCE
                        ):
                            raise ValueError(
                                "The price changed by more than 1%. Review again."
                            )
                        store.retain_paper_order(
                            context,
                            environment=environment_name(),
                            connection_id=link["id"],
                            call_id=call_id,
                            action_id=action_id,
                            ticket_id=ticket_id,
                            order={**order, "status": "pending_submit"},
                        )
                        _execution_policy(
                            context,
                            candidate,
                            expected_revision=saved["revision"],
                            automatic=automatic,
                            expected_link=link,
                        )
                        attempted = True
                        result = api.paper_request("POST", "/v2/orders", body=order)
                        api.validate_order_receipt(result, order)
                    receipt = _select(result, ORDER_FIELDS)
                    store.finish_order_ticket(
                        ticket_id=ticket_id,
                        status="executed",
                        result_summary={"order": receipt, "confirmed_risk": risk},
                    )
                except Exception as exc:
                    definite = (
                        not attempted
                        or isinstance(exc, AlpacaAPIError)
                        and exc.status_code is not None
                        and 400 <= exc.status_code < 500
                    )
                    if intent != "cancel":
                        store.retain_paper_order(
                            context,
                            environment=environment_name(),
                            connection_id=link["id"],
                            call_id=call_id,
                            action_id=action_id,
                            ticket_id=ticket_id,
                            order={
                                **order,
                                "status": (
                                    "rejected" if definite else "submission_unknown"
                                ),
                            },
                        )
                    store.finish_order_ticket(
                        ticket_id=ticket_id,
                        status="failed" if definite else "unknown",
                        result_summary={
                            "client_order_id": order.get("client_order_id"),
                            "intent": intent,
                        },
                        error=type(exc).__name__,
                    )
                    if definite:
                        raise PaperAccountError(
                            str(exc)
                            if isinstance(exc, (ValueError, PaperAccountError))
                            else "Alpaca rejected the paper action."
                        ) from exc
                    raise PaperAccountError(
                        "Submission status is unknown. Refresh to reconcile this order."
                    ) from exc
            if intent != "cancel":
                store.retain_paper_order(
                    context,
                    environment=environment_name(),
                    connection_id=link["id"],
                    call_id=call_id,
                    action_id=action_id,
                    ticket_id=ticket_id,
                    order=receipt,
                )
            store.decide_paper_call(
                context,
                environment=environment_name(),
                identity=action_id,
                decision="submitted",
            )
            status = "submitted"
            if intent == "cancel":
                status = (
                    "canceled"
                    if receipt.get("status") == "canceled"
                    else (
                        "cancel_unnecessary"
                        if receipt.get("status") in trading_portfolio.TERMINAL_ORDERS
                        else "cancel_requested"
                    )
                )
            store.paper_event(
                context,
                environment=environment_name(),
                identity="confirmed:" + ticket_id,
                kind=status,
                call_id=call_id,
                connection_id=link["id"],
                occurred_at=datetime.now(timezone.utc),
                payload={**receipt, "intent": intent, "automatic": automatic},
            )
    finally:
        lock.close()
    if intent == "entry":
        from services.pipeline_store import record_trading_agent_action

        record_trading_agent_action(
            candidate=candidate,
            action="approve",
            requested_by=context.user_id,
            requested_email=context.email,
        )
    return {"status": status, "order": receipt}
