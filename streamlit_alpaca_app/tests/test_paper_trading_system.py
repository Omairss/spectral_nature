"""Accounting and real database execution invariants, without submitting a trade."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import uuid

import psycopg
import pytest

from compute.paper_portfolio import attribute_fills, equity_history, period_performance
from compute.paper_sizing import RiskPolicy
from services import (
    alpaca_paper,
    auth_store,
    brokerage_mcp_store as store,
    trading_portfolio,
)


def test_funding_does_not_become_return_and_loss_to_zero_stays_visible():
    path = equity_history(
        {
            "timestamp": [100, 200, 300],
            "equity": [100, 200, 220],
            "cashflow": {"CSD": [100, 100, 0]},
        }
    )
    result = period_performance(path)
    assert result["profit_loss"] == 20
    assert result["return_pct"] == pytest.approx(10)
    assert (
        period_performance(
            [
                {
                    "date": "2026-10-28",
                    "equity": 100,
                    "external_cashflow": 0,
                    "cashflow_coverage": "reported",
                },
                {
                    "date": "2026-10-29",
                    "equity": 0,
                    "external_cashflow": 0,
                    "cashflow_coverage": "reported",
                },
            ]
        )["performance"]["max_drawdown_pct"]
        == -100
    )
    missing = period_performance(
        [{"date": "2026-10-28", "equity": 100}, {"date": "2026-10-29", "equity": 200}]
    )
    assert missing["return_pct"] is None
    assert missing["path"][-1]["equity"] == 200


@pytest.mark.parametrize(
    "stance,entry_side,exit_side,mark,expected",
    [("long", "buy", "sell", "120", 250), ("short", "sell", "buy", "80", -250)],
)
def test_partial_fills_reductions_fees_and_unmapped_trades(
    stance, entry_side, exit_side, mark, expected
):
    call = {
        "id": "call",
        "intent": "entry",
        "candidate": {"ticker": "A", "stance": stance},
    }
    orders = [
        {"broker_order_id": "entry", "call_id": "call", "action_id": "call"},
        {"broker_order_id": "exit", "call_id": "call", "action_id": "reduce"},
    ]
    activities = [
        {
            "id": "1",
            "activity_type": "FILL",
            "order_id": "entry",
            "symbol": "A",
            "side": entry_side,
            "qty": "10",
            "price": "100",
            "transaction_time": "2026-10-27T15:00:00Z",
        },
        {
            "id": "2",
            "activity_type": "FILL",
            "order_id": "entry",
            "symbol": "A",
            "side": entry_side,
            "qty": "10",
            "price": "110",
            "transaction_time": "2026-10-27T15:01:00Z",
        },
        {
            "id": "3",
            "activity_type": "FILL",
            "order_id": "exit",
            "symbol": "A",
            "side": exit_side,
            "qty": "5",
            "price": "125",
            "transaction_time": "2026-10-28T15:00:00Z",
        },
        {
            "id": "fee",
            "activity_type": "FEE",
            "order_id": "exit",
            "net_amount": "-1",
            "date": "2026-10-28",
        },
        {
            "id": "manual",
            "activity_type": "FILL",
            "order_id": "manual",
            "symbol": "A",
            "side": entry_side,
            "qty": "100",
            "price": "1",
            "transaction_time": "2026-10-28T16:00:00Z",
        },
    ]
    signed = "15" if stance == "long" else "-15"
    result = attribute_fills(
        [call],
        orders,
        activities,
        [{"symbol": "A", "qty": signed, "current_price": mark}],
        as_of="2026-10-29T15:00:00Z",
    )["call"]
    # Long: +100 realized, +225 open; short: -100 realized,+375 open.
    expected_pl = 324 if stance == "long" else 274
    assert result["pl"] == expected_pl
    assert result["quantity"] == "15"
    assert result["average_entry"] == 105
    assert result["return_pct"] == pytest.approx(expected_pl / 2100 * 100)
    mismatch = attribute_fills(
        [call],
        orders,
        activities,
        [{"symbol": "A", "qty": "1", "current_price": mark}],
        as_of="2026-10-29T15:00:00Z",
    )["call"]
    assert mismatch["status"] == "needs_reconciliation"
    assert mismatch["pl"] is None


@pytest.fixture
def owned_db(monkeypatch):
    dsn = os.getenv("PAPER_INTEGRATION_DATABASE_URL")
    if not dsn:
        pytest.skip("An explicit isolated integration database is required.")
    schema = "paper_system_" + uuid.uuid4().hex[:10]
    monkeypatch.setenv("APP_ACCESS_SCHEMA", schema)
    monkeypatch.setenv("APP_TRACK", "qa")
    monkeypatch.setattr(
        auth_store, "open_auth_store_connection", lambda: psycopg.connect(dsn)
    )
    now = datetime.now(timezone.utc)
    owner, portfolio, foreign = (str(uuid.uuid4()) for _ in range(3))
    context = SimpleNamespace(
        user_id=owner, portfolio_id=portfolio, email="owner@example.invalid"
    )
    try:
        assert store.ensure_brokerage_mcp_schema()
        with psycopg.connect(dsn) as conn:
            for identity in [owner, foreign]:
                conn.execute(
                    f"""INSERT INTO {schema}.users(id,email,first_name,last_name,status,role,created_at,updated_at)
                    VALUES(%s,%s,'QA','Owner','active','admin',%s,%s)""",
                    (identity, identity + "@example.invalid", now, now),
                )
            conn.execute(
                f"""INSERT INTO {schema}.portfolios(id,slug,name,brokerage_source,status,created_at,updated_at)
                VALUES(%s,'qa','QA','unlinked','active',%s,%s)""",
                (portfolio, now, now),
            )
            conn.execute(
                f"""INSERT INTO {schema}.portfolio_memberships
                (id,user_id,portfolio_id,role,share_fraction,effective_from,created_at,updated_at)
                VALUES(%s,%s,%s,'admin',1,%s,%s,%s)""",
                (
                    str(uuid.uuid4()),
                    owner,
                    portfolio,
                    now - timedelta(days=1),
                    now,
                    now,
                ),
            )
        link = store.save_direct_connection(
            user_id=owner,
            provider="alpaca_paper",
            resource_url=alpaca_paper.RESOURCE_URL,
            environment="qa",
            access_token_ciphertext="",
            refresh_token_ciphertext="",
            device_token_ciphertext="",
            token_type="key_vault",
            scope="paper_trading",
            token_expires_at=None,
            server_info={"account_id": "account"},
        )
        yield context, link, schema, dsn, foreign
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_durable_ownership_policy_compare_and_swap_and_stale_marks(owned_db):
    context, link, schema, dsn, foreign = owned_db
    candidate = {
        "candidate_id": "original",
        "ticker": "A",
        "stance": "long",
        "hypothesis": "Original thesis",
    }
    trading_portfolio.retain(context, [candidate])
    trading_portfolio.retain(context, [{**candidate, "hypothesis": "Replacement"}])
    call = store.paper_call(context, environment="qa", identity="original")
    assert call["candidate"]["hypothesis"] == "Original thesis"
    assert (
        store.paper_call(
            SimpleNamespace(user_id=foreign, portfolio_id=context.portfolio_id),
            environment="qa",
            identity=call["id"],
        )
        is None
    )
    assert (
        store.paper_call(context, environment="production", identity=call["id"]) is None
    )
    saved = trading_portfolio.policy(context)

    def save():
        try:
            return store.update_paper_policy(
                context,
                environment="qa",
                expected_revision=saved["revision"],
                mode="approval",
                entries_paused=True,
                risk_policy=asdict(RiskPolicy()),
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(r is not None for r in pool.map(lambda _: save(), range(2))) == 1
    store.save_paper_performance(
        context,
        environment="qa",
        call_id=call["id"],
        performance={"as_of": "2026-10-29T00:00:00Z", "pl": 20},
    )
    store.save_paper_performance(
        context,
        environment="qa",
        call_id=call["id"],
        performance={"as_of": "2026-10-28T00:00:00Z", "pl": 0},
    )
    assert (
        store.paper_call(context, environment="qa", identity=call["id"])["performance"][
            "pl"
        ]
        == 20
    )
    store.decide_paper_call(
        context, environment="qa", identity=call["id"], decision="denied"
    )
    with pytest.raises(ValueError):
        store.decide_paper_call(
            context, environment="qa", identity=call["id"], decision="submitted"
        )


def test_one_confirmation_survives_retry_and_pause_blocks_entry(owned_db, monkeypatch):
    context, link, schema, dsn, foreign = owned_db
    candidate = {
        "candidate_id": "entry",
        "ticker": "A",
        "stance": "long",
        "intent": "entry",
    }
    trading_portfolio.retain(context, [candidate])
    saved = trading_portfolio.policy(context)
    order = {
        "client_order_id": "stable-client",
        "symbol": "A",
        "qty": "2",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
    }
    payload = {
        "candidate": candidate,
        "order": order,
        "risk": {"policy": asdict(RiskPolicy()), "price": 100},
        "intent": "entry",
        "account_id": "account",
        "portfolio_id": context.portfolio_id,
        "policy_revision": saved["revision"],
    }
    ticket = store.create_order_ticket(
        connection_id=link["id"],
        user_id=context.user_id,
        provider="alpaca_paper",
        environment="qa",
        review_tool_name="review",
        execution_tool_name="submit",
        arguments_hash="a" * 64,
        ref_id=str(uuid.uuid4()),
        request_summary=payload,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    receipts = []

    class Broker:
        def paper_request(self, method, path, **kwargs):
            if path == "/v2/account":
                return {"id": "account"}
            if method == "POST":
                receipts.append(
                    {**kwargs["body"], "id": str(uuid.uuid4()), "status": "accepted"}
                )
                return receipts[-1]
            if path == "/v2/orders:by_client_order_id":
                return receipts[-1]
            raise AssertionError((method, path))

        def validate_order_receipt(self, result, expected):
            assert all(str(result[k]) == str(v) for k, v in expected.items())

    monkeypatch.setattr(alpaca_paper, "api_for_connection", lambda _: Broker())
    monkeypatch.setattr(
        alpaca_paper,
        "_order_inputs",
        lambda *args: (order, {"price": 100, "capacity_quantity": "2"}),
    )
    monkeypatch.setattr(alpaca_paper, "risk_after_quantity", lambda risk, qty: risk)
    from services import pipeline_store

    monkeypatch.setattr(
        pipeline_store, "record_trading_agent_action", lambda **kwargs: (True, "")
    )
    for _ in range(2):
        assert (
            alpaca_paper.confirm_order(
                context, ticket_id=ticket["id"], arguments_hash="a" * 64
            )["status"]
            == "submitted"
        )
    assert len(receipts) == 1
    state = store.paper_state(context, environment="qa")
    assert len(state["orders"]) == 1
    assert state["calls"][0]["decision"] == "submitted"
    next_call = {**candidate, "candidate_id": "next"}
    trading_portfolio.retain(context, [next_call])
    trading_portfolio.change_policy(
        context,
        mode="approval",
        entries_paused=True,
        risk_policy=saved["risk_policy"],
        expected_revision=saved["revision"],
    )
    with pytest.raises(alpaca_paper.PaperAccountError, match="paused"):
        alpaca_paper._execution_policy(context, next_call)


def test_autopilot_permission_is_bound_to_the_connected_account(owned_db):
    context, link, schema, dsn, foreign = owned_db
    trading_portfolio.retain(context, [])
    saved = trading_portfolio.policy(context)
    trading_portfolio.change_policy(
        context,
        mode="autonomous",
        entries_paused=False,
        risk_policy=saved["risk_policy"],
        expected_revision=saved["revision"],
    )
    assert trading_portfolio.policy(context)["mode"] == "autonomous"
    with pytest.raises(PermissionError, match="exact confirmation"):
        alpaca_paper._execution_policy(
            context, {"intent": "close", "proposal_source": "owner"}, automatic=True
        )
    with psycopg.connect(dsn) as conn:
        conn.execute(
            f"UPDATE {schema}.brokerage_connections SET server_info_json=%s::jsonb WHERE id=%s::uuid",
            ('{"account_id":"another-account"}', link["id"]),
        )
    assert trading_portfolio.policy(context)["mode"] == "approval"
    with pytest.raises(PermissionError):
        alpaca_paper._execution_policy(context, {"intent": "entry"}, automatic=True)


def test_cancellation_request_is_not_reported_as_a_fill_or_repeated(
    owned_db, monkeypatch
):
    context, link, schema, dsn, foreign = owned_db
    original = {
        "candidate_id": "entry",
        "ticker": "A",
        "stance": "long",
        "intent": "entry",
    }
    trading_portfolio.retain(context, [original])
    parent = trading_portfolio.identity(context, "entry")
    action = {
        **original,
        "candidate_id": "cancel",
        "intent": "cancel",
        "parent_call_id": parent,
    }
    trading_portfolio.retain(context, [action])
    saved = trading_portfolio.policy(context)
    order = {
        "id": str(uuid.uuid4()),
        "symbol": "A",
        "side": "buy",
        "qty": "2",
        "client_order_id": "entry-client",
        "status": "accepted",
    }
    ticket = store.create_order_ticket(
        connection_id=link["id"],
        user_id=context.user_id,
        provider="alpaca_paper",
        environment="qa",
        review_tool_name="review",
        execution_tool_name="submit",
        arguments_hash="a" * 64,
        ref_id=str(uuid.uuid4()),
        request_summary={
            "candidate": action,
            "order": order,
            "risk": {"policy": saved["risk_policy"]},
            "intent": "cancel",
            "cancel_order_id": order["id"],
            "account_id": "account",
            "portfolio_id": context.portfolio_id,
            "policy_revision": saved["revision"],
        },
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    deletes = []
    status = ["accepted"]

    class Broker:
        def paper_request(self, method, path, **kwargs):
            if path == "/v2/account":
                return {"id": "account"}
            assert path == "/v2/orders/" + order["id"]
            if method == "DELETE":
                deletes.append(path)
                status[0] = "pending_cancel"
                return {}
            return {**order, "status": status[0]}

    monkeypatch.setattr(alpaca_paper, "api_for_connection", lambda _: Broker())
    first = alpaca_paper.confirm_order(
        context, ticket_id=ticket["id"], arguments_hash="a" * 64
    )
    assert first["status"] == "cancel_requested"
    assert first["order"]["status"] == "pending_cancel"
    status[0] = "canceled"
    second = alpaca_paper.confirm_order(
        context, ticket_id=ticket["id"], arguments_hash="a" * 64
    )
    assert second["order"]["status"] == "canceled"
    assert len(deletes) == 1
    assert store.paper_state(context, environment="qa")["orders"] == []


def test_reduction_keeps_the_original_call_allocation_through_reconciliation(
    owned_db, monkeypatch
):
    import numpy as np
    import pandas as pd

    context, link, schema, dsn, foreign = owned_db
    candidate = {
        "candidate_id": "original",
        "ticker": "A",
        "stance": "long",
        "intent": "entry",
    }
    trading_portfolio.retain(context, [candidate])
    call_id = trading_portfolio.identity(context, "original")
    store.decide_paper_call(
        context, environment="qa", identity=call_id, decision="submitted"
    )
    store.save_paper_performance(
        context,
        environment="qa",
        call_id=call_id,
        performance={
            "status": "open",
            "quantity": "10",
            "as_of": "2026-10-28T15:00:00Z",
        },
    )
    entry = {
        "id": str(uuid.uuid4()),
        "client_order_id": "original-entry",
        "symbol": "A",
        "qty": "10",
        "filled_qty": "10",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "status": "filled",
    }
    entry_ticket = store.create_order_ticket(
        connection_id=link["id"],
        user_id=context.user_id,
        provider="alpaca_paper",
        environment="qa",
        review_tool_name="review",
        execution_tool_name="submit",
        arguments_hash="b" * 64,
        ref_id=str(uuid.uuid4()),
        request_summary={},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    store.retain_paper_order(
        context,
        environment="qa",
        connection_id=link["id"],
        call_id=call_id,
        action_id=call_id,
        ticket_id=entry_ticket["id"],
        order=entry,
    )
    state = {"quantity": "10", "receipt": None}
    posted = []

    class Broker:
        _normalize_symbol = staticmethod(lambda symbol: symbol)

        def paper_request(self, method, path, **kw):
            if path == "/v2/account":
                return {"id": "account", "equity": "100100", "cash": "99000"}
            if path == "/v2/clock":
                return {"is_open": True}
            if path == "/v2/positions":
                return [
                    {
                        "symbol": "A",
                        "qty": state["quantity"],
                        "qty_available": state["quantity"],
                        "current_price": "110",
                        "market_value": str(float(state["quantity"]) * 110),
                        "asset_class": "us_equity",
                    }
                ]
            if path == "/v2/assets/A":
                return {"class": "us_equity", "fractionable": True, "tradable": True}
            if path == "/v2/orders" and method == "GET":
                return []
            if path == "/v2/orders" and method == "POST":
                posted.append(kw["body"])
                state["quantity"] = "7"
                state["receipt"] = {
                    **kw["body"],
                    "id": str(uuid.uuid4()),
                    "status": "filled",
                    "filled_qty": "3",
                    "filled_avg_price": "110",
                    "filled_at": "2026-10-29T15:00:00Z",
                }
                return state["receipt"]
            raise AssertionError((method, path))

        validate_order_receipt = staticmethod(
            alpaca_paper.AlpacaAPI.validate_order_receipt
        )

    monkeypatch.setattr(alpaca_paper, "api_for_connection", lambda _: Broker())
    monkeypatch.setattr(
        alpaca_paper,
        "_risk_returns",
        lambda *args: pd.DataFrame(
            {"A": np.tile([-0.01, 0.01], 150)},
            index=pd.bdate_range("2025-01-01", periods=300),
        ),
    )
    review = alpaca_paper.review_order(
        context, intent="reduce", call_id=call_id, quantity="3"
    )
    assert review["order"]["qty"] == "3"
    assert (
        alpaca_paper.confirm_order(
            context,
            ticket_id=review["ticket_id"],
            arguments_hash=review["arguments_hash"],
        )["order"]["filled_qty"]
        == "3"
    )
    assert len(posted) == 1
    receipt = state["receipt"]
    activities = [
        {
            "id": "entry-fill",
            "activity_type": "FILL",
            "order_id": entry["id"],
            "symbol": "A",
            "side": "buy",
            "qty": "10",
            "price": "100",
            "transaction_time": "2026-10-28T15:00:00Z",
        },
        {
            "id": "exit-fill",
            "activity_type": "FILL",
            "order_id": receipt["id"],
            "symbol": "A",
            "side": "sell",
            "qty": "3",
            "price": "110",
            "transaction_time": "2026-10-29T15:00:00Z",
        },
    ]
    trading_portfolio.reconcile(
        context,
        link,
        {
            "positions": Broker().paper_request("GET", "/v2/positions"),
            "details": {
                "orders": [entry, receipt],
                "new_activities": activities,
                "activities_complete": True,
                "coverage": {"positions": "2026-10-29T15:01:00Z"},
            },
        },
    )
    result = store.paper_call(context, environment="qa", identity=call_id)[
        "performance"
    ]
    assert result["quantity"] == "7"
    assert result["average_entry"] == 100
    assert result["realized_pl"] == 30
    assert result["unrealized_pl"] == 70
    assert result["pl"] == 100
    assert len(result["fills"]) == 2
    summary = store.paper_state(context, environment="qa")["calls"]
    assert all("fills" not in row["performance"] for row in summary)
