"""One owned Trading workspace: durable proposals, broker facts and call history."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timezone
import base64
import hashlib
import json
from typing import Any

from compute.paper_portfolio import attribute_fills, number, call_track_record
from compute.paper_sizing import RiskPolicy
from services import brokerage_mcp_store as store
from services.json_utils import to_jsonable

TERMINAL_ORDERS = frozenset({"filled", "canceled", "expired", "rejected", "replaced"})


def identity(context: Any, candidate_id: str) -> str:
    from services.alpaca_paper import environment_name

    return hashlib.sha256(
        json.dumps(
            [context.user_id, context.portfolio_id, environment_name(), candidate_id]
        ).encode()
    ).hexdigest()[:40]


def _date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
    except ValueError:
        return None


def retain(context: Any, candidates: list[dict], *, proposed_at: Any = None) -> None:
    from services.alpaca_paper import _admin, environment_name

    _admin(context)
    now = datetime.now(timezone.utc)
    rows = []
    for candidate in candidates:
        if not candidate.get("candidate_id") or candidate.get("stance") not in {
            "long",
            "short",
        }:
            continue
        end = (
            _date((candidate.get("forecast") or {}).get("end_at_utc"))
            if candidate.get("intent", "entry") == "entry"
            else None
        )
        rows.append(
            {
                "id": identity(context, candidate["candidate_id"]),
                "candidate": candidate,
                "intent": candidate.get("intent", "entry"),
                "parent_call_id": candidate.get("parent_call_id"),
                "proposed_at": _date(proposed_at) or now,
                "valid_until": end,
            }
        )
    store.retain_paper_calls(context, environment=environment_name(), calls=rows)


def account_binding(link: dict) -> str:
    return hashlib.sha256(
        json.dumps([link["id"], link["server_info"]["account_id"]]).encode()
    ).hexdigest()


def policy(context: Any, *, saved_policy: dict | None = None) -> dict:
    from services.alpaca_paper import _admin, environment_name, connection

    _admin(context)
    saved = (
        saved_policy
        if saved_policy is not None
        else store.paper_policy(context, environment=environment_name())
    )
    result = {
        "mode": "approval",
        "entries_paused": False,
        "revision": 0,
        "risk_policy": asdict(RiskPolicy()),
        **saved,
        "risk_policy": {**asdict(RiskPolicy()), **(saved.get("risk_policy") or {})},
    }

    if result["mode"] == "autonomous":
        link = connection(context)
        if not link or saved.get("account_binding") != account_binding(link):
            result.update(mode="approval", autopilot_status="account_changed")
    result.pop("account_binding", None)
    return result


def change_policy(
    context: Any,
    *,
    mode: str,
    entries_paused: bool,
    risk_policy: dict,
    expected_revision: int,
) -> dict:
    from services.alpaca_paper import connection, environment_name, _admin

    _admin(context)
    if mode not in {"approval", "autonomous"}:
        raise ValueError("Choose approval or autonomous paper trading.")
    RiskPolicy(**risk_policy).validate()
    if mode == "autonomous" and not connection(context):
        raise ValueError("Connect a paper account before enabling paper autopilot.")
    link = connection(context)
    with (
        store.paper_control_lock(link["server_info"]["account_id"])
        if link
        else nullcontext()
    ):
        _admin(context)
        return store.update_paper_policy(
            context,
            environment=environment_name(),
            mode=mode,
            entries_paused=entries_paused,
            risk_policy=risk_policy,
            expected_revision=expected_revision,
            account_binding=account_binding(link) if link else None,
        )


def decision(context: Any, candidate_id: str, action: str) -> dict:
    from services.alpaca_paper import _admin, environment_name

    _admin(context)
    row = store.paper_call(
        context, environment=environment_name(), identity=candidate_id
    )
    if row is None:
        from services.trading_agent_view import build_trading_agent_view

        current = build_trading_agent_view(context)
        candidates = [
            c
            for h in current.get("horizons", [])
            for c in h.get("candidates", [])
            if c.get("candidate_id") == candidate_id
        ]
        retain(context, candidates, proposed_at=current.get("as_of"))
        row = store.paper_call(
            context, environment=environment_name(), identity=candidate_id
        )
    if row is None:
        raise ValueError("This paper call is unavailable.")
    if action != "reject":
        raise ValueError("Approve the exact paper order through its order review.")
    from services.alpaca_paper import connection

    link = connection(context)
    with (
        store.paper_control_lock(link["server_info"]["account_id"])
        if link
        else nullcontext()
    ):
        _admin(context)
        store.decide_paper_call(
            context,
            environment=environment_name(),
            identity=row["id"],
            decision="denied",
        )
    store.paper_event(
        context,
        environment=environment_name(),
        identity="denied:" + row["id"],
        kind="call_denied",
        occurred_at=datetime.now(timezone.utc),
        call_id=row.get("parent_call_id") or row["id"],
        payload={
            "ticker": row["candidate"].get("ticker"),
            "intent": row["intent"],
            "candidate_id": candidate_id,
        },
    )
    return {"status": "recorded", "candidate_id": candidate_id, "action": "reject"}


def build(context: Any, view: dict, *, before: tuple[str, str] | None = None) -> dict:
    from services.alpaca_paper import _admin, environment_name

    _admin(context)
    saved = store.paper_state(context, environment=environment_name(), before=before)
    calls = saved["calls"]
    by_candidate = {r["candidate_id"]: r for r in calls}
    now = datetime.now(timezone.utc)
    candidates = [c for h in view.get("horizons", []) for c in h.get("candidates", [])]
    proposals = []
    for row in calls:
        if row["decision"] != "pending" or row.get("performance", {}).get("status") in {
            "open",
            "closed",
        }:
            continue
        end = _date(row.get("valid_until"))
        proposals.append(
            {
                **row["candidate"],
                "call_id": row["id"],
                "intent": row["intent"],
                "parent_call_id": row.get("parent_call_id"),
                "proposed_at": row["proposed_at"],
                "status": "expired" if end and end < now else "pending",
            }
        )
    for candidate in candidates:
        if (
            candidate["candidate_id"] in by_candidate
            or candidate.get("decision")
            or candidate.get("stance") not in {"long", "short"}
        ):
            continue
        end = _date((candidate.get("forecast") or {}).get("end_at_utc"))
        proposals.append(
            {
                **candidate,
                "call_id": identity(context, candidate["candidate_id"]),
                "intent": "entry",
                "proposed_at": view.get("as_of"),
                "status": "expired" if end and end < now else "pending",
            }
        )
    unresolved_actions = {
        o["action_id"]
        for o in saved["orders"]
        if o["data"].get("status") in {"pending_submit", "submission_unknown"}
    }
    proposals = [
        {**p, "status": "reconciling"} if p["call_id"] in unresolved_actions else p
        for p in proposals
    ]
    proposals.sort(
        key=lambda c: (c.get("status") == "pending", str(c.get("proposed_at") or "")),
        reverse=True,
    )
    orders = [
        {**r["data"], "call_id": r["call_id"], "action_id": r["action_id"]}
        for r in saved["orders"]
    ]
    events = saved["events"]
    next_cursor = None
    if saved.get("has_earlier_activity") and events:
        last = events[-1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps([last["occurred_at"], last["id"]]).encode()
        ).decode()
    performance_calls = [
        {
            **r["candidate"],
            "call_id": r["id"],
            "proposed_at": r["proposed_at"],
            "decision": r["decision"],
            **(r.get("performance") or {}),
        }
        for r in calls
        if r["intent"] == "entry"
    ]
    return to_jsonable(
        {
            "status": saved["status"],
            "proposals": proposals,
            "calls": performance_calls,
            "working_orders": [
                o for o in orders if o.get("status") not in TERMINAL_ORDERS
            ],
            "activity": events,
            "next_cursor": next_cursor,
            "policy": policy(context, saved_policy=saved["policy"]),
            "track_record": call_track_record(performance_calls, saved["orders"]),
            "capabilities": {
                "paper_account": True,
                "order_review": True,
                "position_management": True,
                "policy": True,
                "autonomous_execution": True,
            },
        }
    )


def decode_cursor(value: str) -> tuple[str, str]:
    try:
        if len(value) > 1024:
            raise ValueError
        date, identifier = json.loads(base64.urlsafe_b64decode(value))
        if (
            _date(date) is None
            or not isinstance(identifier, str)
            or len(identifier) > 240
        ):
            raise ValueError
        return date, identifier
    except Exception as exc:
        raise ValueError("Invalid activity cursor.") from exc


def call_detail(context: Any, call_id: str) -> dict | None:
    from services.alpaca_paper import _admin, environment_name

    _admin(context)
    row = store.paper_call(context, environment=environment_name(), identity=call_id)
    if row is None:
        from services.trading_agent_view import _build_report_view

        current = _build_report_view(context)
        candidate = next(
            (
                c
                for h in current.get("horizons", [])
                for c in h.get("candidates", [])
                if c["candidate_id"] == call_id
                or identity(context, c["candidate_id"]) == call_id
            ),
            None,
        )
        if candidate is None:
            return None
        return {
            "call_id": identity(context, candidate["candidate_id"]),
            "candidate": candidate,
            "performance": {},
            "intent": "entry",
            "decision": "pending",
            "path": [],
            "activity": [],
        }
    history = store.paper_call_history(
        context, environment=environment_name(), call_id=row["id"]
    )
    marks = [e["payload"] for e in history if e["kind"] == "call_mark"]
    return to_jsonable(
        {
            "call_id": row["id"],
            "candidate": row["candidate"],
            "intent": row["intent"],
            "proposed_at": row["proposed_at"],
            "decision": row["decision"],
            "performance": row["performance"],
            "path": marks,
            "activity": [
                e
                for e in history
                if e["kind"] not in {"call_mark", "broker_activity", "broker_revision"}
            ],
        }
    )


def reconcile(context: Any, link: dict, snapshot: dict) -> None:
    """Map order receipts, retain broker activities, then materialize call valuations."""
    from services.alpaca_paper import environment_name, ORDER_FIELDS

    env = environment_name()
    details = snapshot.get("details") or {}
    state = store.paper_state(context, environment=env)
    # An accepted response can be lost after POST. The ticket retains the exact
    # client ID and original call; reconciliation never submits a replacement.
    schema = store.auth_store.auth_store_schema_name()
    with store._paper_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT id,created_at,request_summary_json AS payload,result_summary_json AS result
            FROM {schema}.brokerage_order_tickets t WHERE user_id=%s::uuid AND connection_id=%s::uuid
            AND environment=%s AND provider='alpaca_paper' AND (status IN ('executing','unknown') OR
            (status='executed' AND COALESCE(request_summary_json->>'intent','entry')<>'cancel' AND NOT EXISTS
                (SELECT 1 FROM {schema}.paper_trading_orders o WHERE o.ticket_id=t.id)))""",
            (context.user_id, link["id"], env),
        )
        tickets = store._paper_rows(cur)
    from services.alpaca_api import AlpacaAPIError
    from services.alpaca_paper import api_for_connection

    for ticket in tickets:
        payload = ticket["payload"]
        candidate = payload.get("candidate") or {}
        if payload.get("intent") == "cancel":
            if (
                payload.get("cancel_order_id")
                and payload.get("portfolio_id") == context.portfolio_id
            ):
                existing = next(
                    (
                        o
                        for o in details.get("orders") or []
                        if o.get("id") == payload["cancel_order_id"]
                    ),
                    None,
                )
                if existing and existing.get("status") in TERMINAL_ORDERS:
                    store.finish_order_ticket(
                        ticket_id=ticket["id"],
                        status="executed",
                        result_summary={"order": existing, "reconciled": True},
                    )
            continue
        if (
            payload.get("portfolio_id") != context.portfolio_id
            or payload.get("account_id") != link["server_info"]["account_id"]
            or not candidate.get("candidate_id")
        ):
            continue
        if not any(
            c["candidate_id"] == candidate["candidate_id"] for c in state["calls"]
        ):
            retain(context, [candidate], proposed_at=ticket["created_at"])
        action_id = identity(context, candidate["candidate_id"])
        call_id = candidate.get("parent_call_id") or action_id
        receipt = next(
            (
                o
                for o in details.get("orders") or []
                if o.get("client_order_id") == payload["order"]["client_order_id"]
            ),
            None,
        )
        if receipt is None:
            receipt = (ticket.get("result") or {}).get("order")
            if (
                receipt
                and receipt.get("status") not in TERMINAL_ORDERS
                and receipt.get("id")
            ):
                receipt = api_for_connection(link).paper_request(
                    "GET", "/v2/orders/" + receipt["id"]
                )
        if receipt is None:
            try:
                receipt = api_for_connection(link).paper_request(
                    "GET",
                    "/v2/orders:by_client_order_id",
                    params={"client_order_id": payload["order"]["client_order_id"]},
                )
            except AlpacaAPIError as exc:
                if exc.status_code != 404:
                    raise
                receipt = {**payload["order"], "status": "submission_unknown"}
        if receipt.get("id"):
            store.finish_order_ticket(
                ticket_id=ticket["id"],
                status="executed",
                result_summary={"order": receipt, "reconciled": True},
            )
        if receipt.get("client_order_id"):
            store.retain_paper_order(
                context,
                environment=env,
                connection_id=link["id"],
                call_id=call_id,
                action_id=action_id,
                ticket_id=ticket["id"],
                order=receipt,
            )
    state = store.paper_state(context, environment=env)
    for item in details.get("orders") or []:
        mapping = next(
            (
                m
                for m in state["orders"]
                if m["client_order_id"] == item.get("client_order_id")
            ),
            None,
        )
        if not mapping:
            continue
        current_call = next(
            (c for c in state["calls"] if c["id"] == mapping["action_id"]), {}
        )
        selected_item = {k: item[k] for k in ORDER_FIELDS if k in item}
        if (
            mapping["data"] == selected_item
            and current_call.get("decision") != "pending"
        ):
            continue
        # Keep the original ticket identity when refreshing mutable broker state.
        with store._paper_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {schema}.paper_trading_orders SET broker_order_id=%s,order_json=%s::jsonb,updated_at=%s
                WHERE connection_id=%s::uuid AND client_order_id=%s AND user_id=%s::uuid AND portfolio_id=%s::uuid AND environment=%s""",
                (
                    item["id"],
                    json.dumps({k: item[k] for k in ORDER_FIELDS if k in item}),
                    datetime.now(timezone.utc),
                    link["id"],
                    item["client_order_id"],
                    context.user_id,
                    context.portfolio_id,
                    env,
                ),
            )
        try:
            store.decide_paper_call(
                context,
                environment=env,
                identity=mapping["action_id"],
                decision="submitted",
            )
        except ValueError:
            pass  # A denied call is never silently changed to approved.
        store.paper_event(
            context,
            environment=env,
            identity="order:"
            + link["id"]
            + ":"
            + item["id"]
            + ":"
            + str(item.get("status")),
            kind="order_" + str(item.get("status")),
            occurred_at=item.get("updated_at")
            or item.get("filled_at")
            or item.get("submitted_at")
            or datetime.now(timezone.utc),
            call_id=mapping["call_id"],
            connection_id=link["id"],
            payload={k: item[k] for k in ORDER_FIELDS if k in item},
        )
    state = store.paper_state(context, environment=env)
    by_order = {
        o["broker_order_id"]: o["call_id"]
        for o in state["orders"]
        if o.get("broker_order_id")
    }
    events = []
    for activity in details.get("new_activities") or []:
        if not activity.get("id") or not (
            activity.get("transaction_time") or activity.get("date")
        ):
            raise ValueError("An account activity has no durable identity or date.")
        events.append(
            {
                "id": "activity:" + link["id"] + ":" + activity["id"],
                "kind": "broker_activity",
                "call_id": by_order.get(activity.get("order_id")),
                "connection_id": link["id"],
                "occurred_at": activity.get("transaction_time") or activity["date"],
                "payload": activity,
            }
        )
    store.retain_paper_events(context, environment=env, events=events)
    state = store.paper_state(context, environment=env, include_activities=True)
    as_of = (details.get("coverage") or {}).get("positions") or str(
        snapshot.get("source_updated_at")
    )
    valued = attribute_fills(
        state["calls"],
        state["orders"],
        state["activities"],
        snapshot.get("positions") or [],
        as_of=as_of,
    )
    marks = []
    updates = {}
    previous = {c["id"]: c.get("performance") or {} for c in state["calls"]}
    for call_id, result in valued.items():
        if not details.get("activities_complete") and result["fills"]:
            result.update(
                status="needs_reconciliation",
                pl=None,
                return_pct=None,
                unrealized_pl=None,
            )
        summary = {k: v for k, v in result.items() if k not in {"as_of", "fills"}}
        summary["fill_count"] = len(result["fills"])
        old_summary = {
            k: v for k, v in previous.get(call_id, {}).items() if k != "as_of"
        }
        if result["status"] == "closed" and summary == old_summary:
            continue
        updates[call_id] = result
        if result.get("pl") is None or not result["fills"]:
            continue
        observed = result.get("closed_at") or as_of
        marks.append(
            {
                "id": "mark:" + call_id + ":" + observed[:10],
                "kind": "call_mark",
                "call_id": call_id,
                "occurred_at": observed,
                "payload": {
                    "date": observed,
                    "pl": result["pl"],
                    "return_pct": result["return_pct"],
                },
            }
        )
        first = result["opened_at"]
        marks.append(
            {
                "id": "entry:" + call_id,
                "kind": "call_mark",
                "call_id": call_id,
                "occurred_at": first,
                "payload": {"date": first, "pl": 0, "return_pct": 0},
            }
        )
    store.save_paper_performances(context, environment=env, performances=updates)
    store.retain_paper_events(context, environment=env, events=marks)


def run_portfolios(*, run_id: str, llm_client: Any) -> None:
    """The existing Trading producer monitors owned calls and acts on opt-in policy.

    Research stays in AQL/Zopedia. Code checks identities, permissions, sizing and
    execution; it does not decide whether a business thesis is still persuasive.
    """
    from decimal import Decimal, ROUND_DOWN
    from services import alpaca_paper
    from services.auth_service import UserContext
    from services.aql_zopedia_engine import run_aql_zopedia_structured_agent
    from services.trading_agent_view import build_trading_agent_view

    env = alpaca_paper.environment_name()
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["reviews"],
        "properties": {
            "reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["call_id", "action", "quantity_fraction", "reason"],
                    "properties": {
                        "call_id": {"type": "string"},
                        "action": {
                            "type": "string",
                            "enum": ["hold", "reduce", "close"],
                        },
                        "quantity_fraction": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "reason": {"type": "string"},
                    },
                },
            }
        },
    }
    for owner in store.paper_contexts(environment=env):
        context = UserContext(
            **{**owner, "share_fraction": float(owner["share_fraction"])}
        )
        try:
            retain(context, [])
            alpaca_paper.refresh(context)
            view = build_trading_agent_view(context)
            workspace = view["workspace"]
            saved = workspace["policy"]
            active = [c for c in workspace["calls"] if c.get("status") == "open"]
            if active and llm_client is not None:
                originals = [
                    store.paper_call(context, environment=env, identity=c["call_id"])
                    for c in active
                ]
                evidence = {
                    "account": view["paper_portfolio"],
                    "recent_activity": workspace["activity"],
                    "open_calls": [
                        {
                            "call_id": c["id"],
                            "original": c["candidate"],
                            "performance": c["performance"],
                        }
                        for c in originals
                        if c
                    ],
                }
                result = run_aql_zopedia_structured_agent(
                    query=json.dumps(to_jsonable(evidence)),
                    schema_name="paper_position_review",
                    schema=schema,
                    instructions=(
                        "Review each owned paper trading call against its original thesis, forecast dates, invalidation, "
                        "actual fills and current portfolio. Research current developments through the shared tools when needed. "
                        "Choose hold, reduce or close and explain the decisive business or portfolio reason in two sentences. "
                        "A past forecast date is a reason to reassess, not an automatic liquidation rule. "
                        "Use only supplied call_id values. quantity_fraction is the fraction of remaining allocated shares to close; "
                        "use zero for hold and one for close. Consider hedges and shared exposures across calls. "
                        "Account identifiers and private holdings are private context; do not publish them to shared knowledge."
                    ),
                    task="paper_position_review",
                    surface="trading_agent.portfolio",
                    llm_client=llm_client,
                    reuse_scope=f"private:{env}:{context.user_id}:{context.portfolio_id}",
                    max_tool_calls=4,
                    persist_findings=False,
                    write_policy="none",
                )
                if result.get("status") != "completed":
                    raise RuntimeError("Position review did not complete.")
                by_id = {c["call_id"]: c for c in active}
                pending = {
                    p.get("parent_call_id")
                    for p in workspace["proposals"]
                    if p["status"] == "pending"
                }
                seen = set()
                for review in result["payload"]["reviews"]:
                    call = by_id.get(review["call_id"])
                    action = review["action"]
                    fraction = Decimal(str(review["quantity_fraction"]))
                    if call is None or review["call_id"] in seen:
                        raise ValueError(
                            "The position review referenced an unavailable or duplicate call."
                        )
                    seen.add(review["call_id"])
                    if (
                        action == "hold"
                        and fraction != 0
                        or action == "close"
                        and fraction != 1
                        or action == "reduce"
                        and not 0 < fraction < 1
                    ):
                        raise ValueError(
                            "The position review returned an inconsistent quantity."
                        )
                    store.paper_event(
                        context,
                        environment=env,
                        identity="review:" + run_id + ":" + call["call_id"],
                        kind="position_review",
                        occurred_at=datetime.now(timezone.utc),
                        call_id=call["call_id"],
                        payload={**review, "ticker": call["ticker"]},
                    )
                    if action == "hold" or call["call_id"] in pending:
                        continue
                    original = store.paper_call(
                        context, environment=env, identity=call["call_id"]
                    )["candidate"]
                    quantity = (Decimal(call["quantity"]) * fraction).quantize(
                        Decimal("0.000001"), rounding=ROUND_DOWN
                    )
                    if quantity <= 0:
                        store.paper_event(
                            context,
                            environment=env,
                            identity="quantity:" + run_id + ":" + call["call_id"],
                            kind="action_needs_review",
                            call_id=call["call_id"],
                            occurred_at=datetime.now(timezone.utc),
                            payload={
                                "ticker": call["ticker"],
                                "reason": "The proposed reduction is below the share increment.",
                            },
                        )
                        continue
                    proposal = {
                        **original,
                        "candidate_id": "manage-"
                        + hashlib.sha256(
                            (run_id + call["call_id"]).encode()
                        ).hexdigest()[:40],
                        "intent": action,
                        "proposal_source": "agent",
                        "parent_call_id": call["call_id"],
                        "quantity": str(quantity),
                        "quantity_fraction": float(fraction),
                        "reason": review["reason"],
                    }
                    retain(context, [proposal])
            # Only proposals created after explicit opt-in qualify for automatic
            # execution. Older proposals stay available for the owner's review.
            if saved["mode"] == "autonomous":
                latest = build_trading_agent_view(context)["workspace"]
                enabled = _date(saved.get("updated_at"))
                for proposal in latest["proposals"]:
                    if (
                        proposal.get(
                            "proposal_source",
                            (
                                "agent"
                                if proposal.get("intent", "entry") == "entry"
                                else "owner"
                            ),
                        )
                        != "agent"
                    ):
                        continue
                    proposed = _date(proposal.get("proposed_at"))
                    if (
                        proposal["status"] != "pending"
                        or not enabled
                        or not proposed
                        or proposed <= enabled
                    ):
                        continue
                    if (
                        saved["entries_paused"]
                        and proposal.get("intent", "entry") == "entry"
                    ):
                        continue
                    try:
                        ticket = alpaca_paper.review_order(
                            context, candidate_id=proposal["candidate_id"]
                        )
                        alpaca_paper.confirm_order(
                            context,
                            ticket_id=ticket["ticket_id"],
                            arguments_hash=ticket["arguments_hash"],
                            automatic=True,
                        )
                        alpaca_paper.refresh(context)
                    except (ValueError, alpaca_paper.PaperAccountError) as exc:
                        store.paper_event(
                            context,
                            environment=env,
                            identity="blocked:"
                            + run_id
                            + ":"
                            + proposal["candidate_id"],
                            kind="action_needs_review",
                            call_id=proposal.get("parent_call_id")
                            or proposal["call_id"],
                            occurred_at=datetime.now(timezone.utc),
                            payload={"ticker": proposal["ticker"], "reason": str(exc)},
                        )
        except Exception as exc:
            from services.llm_execution import record_execution_issue

            record_execution_issue("paper_portfolio", exc)
            try:
                store.paper_event(
                    context,
                    environment=env,
                    identity="monitor:" + run_id + ":" + context.portfolio_id,
                    kind="monitor_unavailable",
                    occurred_at=datetime.now(timezone.utc),
                    payload={},
                )
            except Exception as persistence_error:
                record_execution_issue("paper_portfolio.persistence", persistence_error)
            print(f"[warn] paper portfolio monitor failed: {type(exc).__name__}")
