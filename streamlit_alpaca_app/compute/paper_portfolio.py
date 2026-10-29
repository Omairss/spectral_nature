"""Cashflow-aware paper account performance and explicitly linked fill accounting.

Account balances remain broker-owned. A call only owns fills explicitly linked
through its approved order; ticker proximity never assigns a manual trade.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from typing import Any

from compute.analytics import performance_summary

EXTERNAL_CASH = frozenset({"CSD", "CSW", "ACATC", "JNLC"})


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("A broker amount is not finite.")
        return result
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("A broker amount is unavailable.") from exc


def equity_history(history: dict) -> list[dict]:
    """Preserve broker observations and external flows, including their coverage.

    Daily Modified Dietz uses a midpoint weight when the history response supplies
    only per-window cashflows. It is exact on zero-flow windows. No external flow
    is treated as investment income; dividends and fees remain in performance.
    """
    stamps = history.get("timestamp") or []
    values = history.get("equity") or []
    if len(stamps) != len(values):
        raise ValueError("Broker equity history has inconsistent arrays.")
    flows = history.get("cashflow")
    if flows is not None and not isinstance(flows, dict):
        raise ValueError("Broker cashflow history is invalid.")
    for kind, amounts in (flows or {}).items():
        if (
            not isinstance(amounts, list)
            or len(amounts) != len(stamps)
            or any(number(x) is None for x in amounts)
        ):
            raise ValueError("Broker cashflow history has inconsistent arrays.")
    result = []
    for index, (stamp, value) in enumerate(zip(stamps, values)):
        equity = number(value)
        if equity is None:
            raise ValueError("Broker equity history contains an invalid amount.")
        external = sum(
            float(amounts[index])
            for kind, amounts in (flows or {}).items()
            if kind in EXTERNAL_CASH
        )
        result.append(
            {
                "date": datetime.fromtimestamp(float(stamp), timezone.utc).isoformat(),
                "equity": equity,
                "external_cashflow": external,
                "cashflow_coverage": (
                    "reported" if flows is not None else "not_reported"
                ),
            }
        )
    return result


def period_performance(points: list[dict]) -> dict:
    funded = next(
        (i for i, p in enumerate(points) if (number(p.get("equity")) or 0) != 0), None
    )
    selected = points[funded:] if funded is not None else []
    if not selected:
        return {"path": [], "performance": {}, "return_pct": None, "profit_loss": None}
    wealth, total_flow = 1.0, 0.0
    valid = (number(selected[0].get("equity")) or 0) > 0
    output = []
    for index, point in enumerate(selected):
        value = number(point.get("equity"))
        flow = number(point.get("external_cashflow"))
        if index:
            previous = number(selected[index - 1].get("equity"))
            # Old snapshots did not retain cashflow arrays. Their raw percentage
            # is not promoted to an account investment return on assumption.
            if point.get("cashflow_coverage") != "reported" or flow is None:
                valid = False
            if flow is not None:
                total_flow += flow
            denominator = (previous or 0) + 0.5 * (flow or 0)
            if valid and value is not None and previous is not None and denominator > 0:
                daily = (value - previous - flow) / denominator
                wealth *= 1 + daily
                if not math.isfinite(wealth):
                    valid = False
            else:
                valid = False
        output.append({**point, "return_pct": (wealth - 1) * 100 if valid else None})
    initial, final = number(selected[0].get("equity")), number(
        selected[-1].get("equity")
    )
    summary = performance_summary(output) if valid else {}
    if valid:
        peak = 1.0
        drawdown = 0.0
        for point in output:
            current = 1 + point["return_pct"] / 100
            peak = max(peak, current)
            drawdown = min(drawdown, current / peak - 1)
        summary["max_drawdown_pct"] = drawdown * 100
    return {
        "path": output,
        "performance": summary,
        "return_pct": output[-1]["return_pct"],
        "profit_loss": (
            final - initial - total_flow
            if valid and initial is not None and final is not None
            else None
        ),
        "external_cashflow": total_flow,
        "method": "daily_modified_dietz_midpoint",
        "cashflow_coverage": "reported" if valid else "incomplete",
    }


def select_period(points: list[dict], period: str) -> list[dict]:
    import pandas as pd

    if period not in {"1D", "1W", "1M", "3M", "1Y", "ALL"}:
        raise ValueError("Invalid portfolio period.")
    if not points or period == "ALL":
        return points
    if period == "1D":
        return points[-2:]
    end = pd.to_datetime(points[-1]["date"], utc=True)
    offset = (
        pd.DateOffset(weeks=1)
        if period == "1W"
        else pd.DateOffset(months={"1M": 1, "3M": 3, "1Y": 12}[period])
    )
    cutoff = end - offset
    anchor = next(
        (
            i
            for i in range(len(points) - 1, -1, -1)
            if pd.to_datetime(points[i]["date"], utc=True) <= cutoff
        ),
        0,
    )
    return points[anchor:]


def attribute_fills(
    calls: list[dict],
    orders: list[dict],
    activities: list[dict],
    positions: list[dict],
    *,
    as_of: str,
) -> dict[str, dict]:
    """Weighted entry cost per immutable call; reductions only close that call.

    Manual/unmapped trades are left unattributed. Quantity mismatches, unsupported
    corporate actions and incomplete receipts withhold current call valuation.
    They do not overwrite the broker account or invent a matching allocation.
    """
    entries = {c["id"]: c for c in calls if c.get("intent") == "entry"}
    mapping = {o["broker_order_id"]: o for o in orders if o.get("broker_order_id")}
    state = {
        key: {
            "quantity": Decimal(0),
            "cost": Decimal(0),
            "entry_capital": Decimal(0),
            "realized": Decimal(0),
            "fills": [],
            "fees": Decimal(0),
            "invalid": False,
        }
        for key in entries
    }
    seen = set()
    actions = sorted(
        activities,
        key=lambda a: (
            str(a.get("transaction_time") or a.get("date") or ""),
            str(a.get("id") or ""),
        ),
    )
    corporate = [
        a
        for a in activities
        if a.get("activity_type") in {"SPLIT", "MA", "SPIN", "REORG", "JNLS", "ACATS"}
    ]
    for activity in actions:
        identity = str(activity.get("id") or "")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        order = mapping.get(str(activity.get("order_id") or ""))
        if not order or order["call_id"] not in state:
            continue
        key = order["call_id"]
        current = state[key]
        call = entries[key]
        if activity.get("activity_type") == "FEE":
            amount = number(activity.get("net_amount"))
            if amount is not None:
                current["fees"] += decimal(amount)
            continue
        if activity.get("activity_type") != "FILL":
            continue
        try:
            qty, price = decimal(activity["qty"]), decimal(activity["price"])
            if (
                qty <= 0
                or price <= 0
                or str(activity.get("symbol")) != str(call["candidate"].get("ticker"))
            ):
                raise ValueError("The fill does not match its call.")
            sign = (
                Decimal(1) if call["candidate"].get("stance") == "long" else Decimal(-1)
            )
            entering = activity.get("side") == ("buy" if sign > 0 else "sell")
            if entering:
                if order.get("action_id", key) != key:
                    raise ValueError(
                        "A position-management fill cannot add entry shares."
                    )
                current["cost"] += qty * price
                current["quantity"] += qty
                current["entry_capital"] += qty * price
            else:
                if qty > current["quantity"] or current["quantity"] <= 0:
                    raise ValueError("An exit exceeds this call’s allocated shares.")
                average = current["cost"] / current["quantity"]
                current["realized"] += qty * (price - average) * sign
                current["quantity"] -= qty
                current["cost"] -= qty * average
            current["fills"].append(
                {
                    **{
                        k: activity[k]
                        for k in (
                            "id",
                            "order_id",
                            "symbol",
                            "side",
                            "qty",
                            "transaction_time",
                        )
                        if k in activity
                    },
                    "price": float(price),
                }
            )
        except (KeyError, ValueError):
            current["invalid"] = True
    for order_id, order in mapping.items():
        current = state.get(order["call_id"])
        if current is None:
            continue
        receipt = order.get("data") or order
        expected = number(receipt.get("filled_qty"))
        if expected is not None:
            observed = sum(
                decimal(fill["qty"])
                for fill in current["fills"]
                if str(fill.get("order_id")) == order_id
            )
            if abs(observed - decimal(expected)) > Decimal("0.000001"):
                current["invalid"] = True
    broker = {str(p.get("symbol")): p for p in positions}
    allocated = defaultdict(Decimal)
    for key, current in state.items():
        sign = (
            Decimal(1)
            if entries[key]["candidate"].get("stance") == "long"
            else Decimal(-1)
        )
        allocated[str(entries[key]["candidate"].get("ticker"))] += (
            sign * current["quantity"]
        )
    result = {}
    for key, current in state.items():
        candidate = entries[key]["candidate"]
        symbol = str(candidate.get("ticker") or "")
        qty = current["quantity"]
        row = broker.get(symbol, {})
        price = number(row.get("current_price"))
        actual = number(row.get("qty"))
        sign = Decimal(1) if candidate.get("stance") == "long" else Decimal(-1)
        fills = current["fills"]
        opened = str(fills[0].get("transaction_time") or "")[:10] if fills else ""
        ended = (
            str(fills[-1].get("transaction_time") or "") if fills and not qty else as_of
        )[:10]
        affected = (
            any(
                str(a.get("symbol") or "").upper() == symbol
                and opened
                <= str(a.get("transaction_time") or a.get("date") or "")[:10]
                <= ended
                for a in corporate
            )
            if fills
            else False
        )
        valid = not current["invalid"] and not affected
        if qty:
            valid = (
                valid
                and actual is not None
                and allocated[symbol] * decimal(actual) > 0
                and abs(allocated[symbol]) <= abs(decimal(actual)) + Decimal("0.000001")
                and price is not None
            )
        open_pl = (
            (qty * decimal(price) - current["cost"]) * sign
            if qty and valid and price is not None
            else Decimal(0) if not qty else None
        )
        total = (
            current["realized"] + open_pl + current["fees"]
            if valid and open_pl is not None
            else None
        )
        capital = current["entry_capital"]
        fills = current["fills"]
        result[key] = {
            "quantity": str(qty),
            "average_entry": float(current["cost"] / qty) if qty else None,
            "entry_capital": float(capital),
            "realized_pl": float(current["realized"] + current["fees"]),
            "unrealized_pl": float(open_pl) if open_pl is not None else None,
            "pl": float(total) if total is not None else None,
            "return_pct": (
                float(total / capital * 100)
                if total is not None and capital > 0
                else None
            ),
            "linked_fees": float(current["fees"]),
            "fills": fills,
            "status": (
                "needs_reconciliation"
                if not valid
                else "open" if qty else "closed" if fills else "not_filled"
            ),
            "as_of": as_of,
            "opened_at": str(fills[0].get("transaction_time") or "") if fills else "",
            "closed_at": (
                str(fills[-1].get("transaction_time") or "")
                if fills and not qty
                else ""
            ),
        }
    return result
