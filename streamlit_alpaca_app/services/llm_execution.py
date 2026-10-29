"""Provider execution accounting and account-wide terminal-failure recovery.

Research semantics stay in AQL/Zopedia. This boundary records actual attempts,
preserves causal ownership, and stops rejected requests against an empty account.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import Context, ContextVar
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import hashlib
from functools import wraps
from functools import lru_cache
import json
import logging
import math
import os
import re
import threading
import time
from typing import Any
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)
_CALL: ContextVar[dict[str, Any]] = ContextVar("llm_execution_call", default={})
_ACCOUNTS: dict[str, tuple[float, bool]] = {}
_ACCOUNT_LOCK = threading.RLock()
_OWNERSHIP: ContextVar[tuple] = ContextVar("execution_ownership", default=())
_PUBLICATION_FENCE: ContextVar[int] = ContextVar("execution_publication_fence", default=0)


@contextmanager
def execution_issue_context():
    """Share bounded operational outcomes across this producer's workers."""
    sink = {"lock": threading.Lock(), "items": {}}
    with model_call_context(execution_issues=sink):
        yield sink


def record_execution_issue(component: str, error: BaseException) -> None:
    sink = _CALL.get().get("execution_issues")
    if sink is None:
        return
    identity = (component, type(error).__name__, getattr(error, "sqlstate", None))
    with sink["lock"]:
        row = sink["items"].setdefault(identity, {"component": component, "error_type": type(error).__name__,
            "sqlstate": getattr(error, "sqlstate", None), "count": 0})
        row["count"] += 1


def execution_issues() -> list[dict[str, Any]]:
    sink = _CALL.get().get("execution_issues")
    if sink is None:
        return []
    with sink["lock"]:
        return [dict(row) for row in sink["items"].values()]


def execution_publication_fence() -> int:
    return _PUBLICATION_FENCE.get()


def check_execution_ownership() -> None:
    if _CALL.get().get("retaining_paid_receipt"):
        raise RuntimeError("Paid-result retention cannot start inference, acquisition or application publication.")
    for check in _OWNERSHIP.get():
        check()


def retain_paid_result(function):
    """Complete only paid-result retention in its own bounded execution context.

    Financial finalization outlives a cancelled child. It retains the explicit
    original identity, cannot acquire inference or publish product artifacts,
    and uses reserved database capacity for at most fifteen seconds.
    """
    from .database import database_workload
    from .work_scope import WorkScope
    context = {**_CALL.get(), "retaining_paid_receipt": True}
    def retain():
        with model_call_context(**context), database_workload("interactive"), WorkScope(time.monotonic() + 15).activate():
            return function()
    return Context().run(retain)


@contextmanager
def execution_ownership(namespace: str, key: str, *, wait_seconds: float = 0, lease_seconds: float = 300):
    """Coordinate work across processes without holding a database connection.

    A lost lease stops subsequent provider requests and publication. A killed
    worker's ownership expires; its durable checkpoints remain resumable.
    """
    from . import pipeline_store
    from .work_scope import WorkScope
    from .database import database_workload
    from contextvars import copy_context
    owner = uuid.uuid4().hex
    deadline = time.monotonic() + wait_seconds
    while True:
        # Use existing reserved database capacity for short ownership metadata,
        # never for the research body. Start the validity clock before SQL so
        # network latency cannot extend our local belief in the lease.
        renewed_at = time.monotonic()
        with database_workload("interactive"):
            claimed = pipeline_store.claim_execution_state(namespace, key, owner=owner, seconds=lease_seconds)
        if claimed:
            break
        if time.monotonic() >= deadline:
            yield False
            return
        check_execution_ownership()
        time.sleep(min(1, max(deadline-time.monotonic(), 0)))
    stop = threading.Event()
    lost = threading.Event()
    def check_owner():
        if lost.is_set() or time.monotonic()-renewed_at >= lease_seconds:
            work.cancel()
            raise RuntimeError("Execution ownership was lost; completed work remains checkpointed.")
    class OwnershipWork(WorkScope):
        def check(self):
            try:
                check_owner()
            except RuntimeError as exc:
                from .work_scope import WorkCancelled
                raise WorkCancelled(str(exc)) from exc
            super().check()
    # Renewal validity must not shorten a longer model/tool timeout. Every new
    # I/O still checks the owner, and failed renewal cancels active operations.
    work = OwnershipWork(float("inf"))
    def heartbeat():
        nonlocal renewed_at
        delay = lease_seconds / 3
        while not stop.wait(delay):
            remaining = renewed_at + lease_seconds - time.monotonic()
            if remaining <= 0:
                lost.set()
                work.cancel()
                return
            try:
                started = time.monotonic()
                with database_workload("interactive"), WorkScope(started + min(30, remaining)).activate():
                    renewed = pipeline_store.renew_execution_state(namespace, key, owner=owner, seconds=lease_seconds)
                if not renewed:
                    lost.set()
                    work.cancel()
                    return
                renewed_at = started
                delay = lease_seconds / 3
            except Exception as exc:
                # One delayed SQL operation is not proof that another worker
                # owns the lease. Retry only within its last confirmed validity.
                remaining = renewed_at + lease_seconds - time.monotonic()
                log.warning("execution_lease_renewal_failed namespace=%s key=%s error=%s valid_seconds=%.3f",
                            namespace, key, type(exc).__name__, max(remaining, 0))
                if remaining <= 0:
                    lost.set()
                    work.cancel()
                    return
                delay = min(5, lease_seconds / 12, remaining)
    thread = threading.Thread(target=copy_context().run, args=(heartbeat,), daemon=True,
                              name="execution-ownership")
    token = _OWNERSHIP.set((*_OWNERSHIP.get(), check_owner))
    fence_token = _PUBLICATION_FENCE.set(int(claimed) if namespace.startswith("pipeline_job:") else _PUBLICATION_FENCE.get())
    thread.start()
    try:
        with work.activate():
            yield True
            check_owner()
    finally:
        _OWNERSHIP.reset(token)
        _PUBLICATION_FENCE.reset(fence_token)
        lost.set()
        work.cancel()
        stop.set()
        thread.join(timeout=2)
        try:
            with database_workload("interactive"):
                pipeline_store.release_execution_state(namespace, key, owner=owner)
        except Exception:
            log.warning("Execution ownership release unavailable; lease will expire")


@contextmanager
def research_work_context(identity: str, *, prior: dict[str, Any]):
    """Freeze a dated investigation's identity and prior knowledge across retries."""
    status = {"failed": False}
    with model_call_context(work_instance=identity, prior_research=prior, work_status=status):
        yield status


def mark_research_work_incomplete() -> None:
    status = _CALL.get().get("work_status")
    if status is not None:
        status["failed"] = True


class LLMContextWindowExceeded(RuntimeError):
    """Capacity feedback for the agent; retrying an unchanged request cannot help."""


class ResearchWorkDeferred(RuntimeError):
    """This optional action cannot run under the remaining resources."""


class ResearchBudgetExceeded(ResearchWorkDeferred):
    """Stop optional spending and finish this run using its economy model."""


def _microusd(value: Any, *, maximum: bool = True) -> int:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Research cost must be finite and nonnegative.")
    return int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING if maximum else ROUND_FLOOR))


def _run_budget_key() -> tuple[str, str] | None:
    root = _CALL.get().get("root_run_id") or os.getenv("PIPELINE_RUN_ID")
    configured = os.getenv("RESEARCH_RUN_BUDGET_USD", "").strip()
    if configured and not root:
        raise ValueError("A research dollar budget requires a logical root run identity.")
    return ("research_run_budget", str(root)) if root and configured else None


def _run_budget_state(state: dict[str, Any]) -> dict[str, Any]:
    # Configuration selects the initial allowance for a new run. Existing
    # spending and explicit audited amendments outlive configuration changes.
    if state:
        return state
    cap = _microusd(os.environ["RESEARCH_RUN_BUDGET_USD"], maximum=False)
    fraction = Decimal(os.getenv("RESEARCH_RUN_SOFT_OVERRUN_FRACTION", "0.1"))
    if not fraction.is_finite() or fraction < 0:
        raise ValueError("Completion overrun fraction must be finite and nonnegative")
    initial = {"cap_microusd": cap, "attempts": {}, "currency": "USD",
               "soft_overrun_fraction": str(fraction)}
    if os.getenv("RESEARCH_BUDGET_MODE") == "event":
        margin = Decimal(os.getenv("RESEARCH_EVENT_MARGIN_FRACTION", "0.5"))
        if not margin.is_finite() or margin < 0:
            raise ValueError("Event margin must be finite and nonnegative")
        initial.update(base_microusd=cap, event_margin_fraction=str(margin),
                       event_ceiling_microusd=cap + int(cap * margin), event_extensions=[])
    return initial


@contextmanager
def event_research_context(work: dict[str, Any] | None):
    """Bind an existing model-selected investigation to its marginal funding.

    This is not an agent tool for changing an allowance. Producers supply a
    current nomination or completed change assessment and its evidence IDs.
    Repetition and raw headline counts cannot create financial authorization.
    """
    if work and not all(work.get(key) for key in ("work_id", "evidence_ids", "reason")):
        error = ValueError("Marginal research requires a selected investigation, evidence and reason")
        record_execution_issue("event_budget_validation", error)
        log.warning("Marginal research was not authorized: missing investigation, evidence or reason")
        work = None
    with model_call_context(event_work=work):
        yield


def _extend_event_allowance(state: dict[str, Any], required: int) -> bool:
    work = _CALL.get().get("event_work")
    if required <= 0:
        return True
    ceiling = state.get("event_ceiling_microusd", state["cap_microusd"])
    if not work or state.get("invalid_quote") or required > ceiling - state["cap_microusd"]:
        return False
    state["cap_microusd"] += required
    state["event_extensions"].append({**work, "additional_microusd": required,
                                      "working_allowance_microusd": state["cap_microusd"],
                                      "admitted_at_utc": datetime.now(timezone.utc).isoformat()})
    return True


def event_investigation_capacity(base_limit: int) -> int:
    """Offer research capacity, not a minimum count or a required spend."""
    key = _run_budget_key()
    if key is None or base_limit <= 0:
        return base_limit
    from .pipeline_store import read_execution_state
    from .database import database_workload
    with database_workload("interactive"):
        state = _run_budget_state(read_execution_state(*key, strict=True) or {})
    if not state.get("base_microusd"):
        return base_limit
    return int((Decimal(base_limit) * (1 + Decimal(state["event_margin_fraction"]))).to_integral_value(rounding=ROUND_CEILING))


def _run_remaining(state: dict[str, Any], *, for_admission: bool = False) -> int:
    cap = state["cap_microusd"]
    if for_admission and not _CALL.get().get("event_work"):
        cap = min(cap, state.get("base_microusd", cap))
    return cap - sum(
        value["charged_microusd"] if value["status"] == "settled" else value["maximum_microusd"]
        for value in state["attempts"].values()) - sum(
            value["remaining_microusd"] for value in state.get("core_obligations", {}).values())


def _provider_unreserved_microusd(state: dict[str, Any]) -> int:
    balance = int((Decimal(str(state.get("balance_usd", 0))) * 1_000_000).to_integral_value(rounding=ROUND_FLOOR))
    held = sum(_microusd(value["amount_usd"]) for value in state.get("reservations", {}).values()
        if not (value.get("status") == "unknown" and value.get("finished_at") is not None
                and value.get("liquidity_observed_at", 0) > value["finished_at"]))
    return balance - held


def _future_core_microusd(state: dict[str, Any]) -> int:
    """Unspent future work expires without becoming an uncertain paid request."""
    active = {key: value for key, value in state.get("future_core", {}).items()
              if value["deadline_at"] > time.time() and value["remaining_microusd"] > 0}
    state["future_core"] = active
    return sum(value["remaining_microusd"] for value in active.values())


def _sync_core_account(states, run_key, account_key, work_id) -> None:
    """Project the original run's remaining obligation in the same transaction."""
    obligation = states[run_key].get("core_obligations", {}).get(work_id)
    if not obligation or tuple(obligation.get("provider_account") or ()) != account_key:
        return
    account = states[account_key]
    book = account.setdefault("future_core", {})
    identity = obligation["account_core_id"]
    if obligation["status"] != "protected" or obligation["deadline_at"] <= time.time():
        book.pop(identity, None)
    else:
        book[identity] = {"run": list(run_key), "work_id": work_id,
            "remaining_microusd": obligation["remaining_microusd"],
            "deadline_at": obligation["deadline_at"], "required": obligation["required"]}
    account["future_core_shortfall_microusd"] = (
        max(0, _future_core_microusd(account) - _provider_unreserved_microusd(account))
        if account.get("observed_at") is not None else None)


def protect_core_research(*, config: Any, work: dict[str, Any], stages: int | dict[str, int] = 2,
                          required: bool = True) -> bool:
    """Fund declared writing/review work before discretionary acquisition.

    Producers declare their full batch together. The shared agent also calls
    this boundary for standalone work before planning. Quotes cover the actual
    supplied context and configured output allowance; later context growth is
    accounted at admission. This is a soft target, not permission to downgrade
    protected core work after optional spending.
    """
    if _CALL.get().get("optional_core_unfunded"):
        return False
    key = _run_budget_key()
    if key is None:
        return True
    from . import pipeline_store
    from .database import database_workload
    quoted = {identity: _microusd(_request_maximum_charge(config, {
        "model": config.model, "messages": [{"role": "user", "content": json.dumps(evidence, default=str)}],
    }) * (stages[identity] if isinstance(stages, dict) else stages)) for identity, evidence in work.items()}
    official = config.provider == "deepseek" and urlparse(config.base_url).hostname == "api.deepseek.com"
    account_key = ("provider_budget", _account_key(config)) if official else None
    from .work_scope import remaining_work_seconds
    remaining = remaining_work_seconds(float("inf"))
    if not math.isfinite(remaining):
        remaining = float(getattr(config, "timeout_seconds", 90)) * max(
            stages.values() if isinstance(stages, dict) else [stages])
    deadline_at = time.time() + remaining
    if account_key and not required:
        _provider_budget_state(config, sum(quoted.values()) / 1e6,
                               time.monotonic() + min(remaining, 30))
    admitted = True
    def protect(states):
        nonlocal admitted
        state = _run_budget_state(states[key])
        states[key] = state
        if state["cap_microusd"] == 0:
            admitted = False
            return states
        obligations = state.setdefault("core_obligations", {})
        new_work = {identity: quote for identity, quote in quoted.items()
                    if identity not in obligations or obligations[identity]["status"] == "completed"}
        new_quote = sum(new_work.values())
        fraction = Decimal(state.get("soft_overrun_fraction", "0.1"))
        if not fraction.is_finite() or fraction < 0:
            raise ValueError("Completion overrun fraction must be finite and nonnegative")
        if not required:
            if account_key:
                account = states[account_key]
                available = (_provider_unreserved_microusd(account) - _future_core_microusd(account))
                if not account.get("available") or new_quote > available:
                    admitted = False
                    return states
            # The completion buffer is not another discretionary expansion on
            # top of the event margin. Every marginal admission shares one cap.
            buffer = 0 if "event_ceiling_microusd" in state else int(state["cap_microusd"] * fraction)
            shortfall = new_quote - _run_remaining(state, for_admission=True) - buffer
            if shortfall > 0 and not _extend_event_allowance(state, shortfall):
                admitted = False
                return states
        for identity, quote in new_work.items():
            obligations[identity] = {"remaining_microusd": quote, "quoted_microusd": quote,
                "provider": config.provider, "model": config.model, "status": "protected",
                "generation": uuid.uuid4().hex}
            if account_key:
                obligations[identity].update(provider_account=list(account_key), required=required,
                    account_core_id=hashlib.sha256(json.dumps([*key, identity]).encode()).hexdigest(),
                    deadline_at=deadline_at)
                _sync_core_account(states, key, account_key, identity)
        for identity in quoted.keys() - new_work.keys():
            obligation = obligations[identity]
            if obligation["status"] == "interrupted" and obligation.get("deadline_at", float("inf")) > time.time():
                obligation["status"] = "protected"
                if account_key:
                    _sync_core_account(states, key, account_key, identity)
        return states
    with database_workload("interactive"):
        pipeline_store.mutate_execution_states([key, *([account_key] if account_key else [])], protect)
    return admitted


def complete_core_research(work_id: str) -> None:
    """Return unused completion funds only after the owning result exists."""
    key = _run_budget_key()
    if key is None:
        return
    from . import pipeline_store
    from .database import database_workload
    with database_workload("interactive"):
        state = pipeline_store.read_execution_state(*key, strict=True) or {}
    account_key = tuple((state.get("core_obligations", {}).get(work_id) or {}).get("provider_account") or ())
    def complete(states):
        state = states[key]
        obligation = state.get("core_obligations", {}).get(work_id)
        if obligation:
            obligation.update(remaining_microusd=0, status="completed")
            if account_key:
                _sync_core_account(states, key, account_key, work_id)
        return states
    with database_workload("interactive"):
        pipeline_store.mutate_execution_states([key, *([account_key] if account_key else [])], complete)


def interrupt_core_research(reason: str) -> None:
    """Release unspent account priority when this execution ends.

    Keep the original run funding and deadline for a native retry. Actual paid
    requests and unknown charges remain owned and reserved independently.
    """
    key = _run_budget_key()
    if key is None:
        return
    from . import pipeline_store
    from .database import database_workload
    with database_workload("interactive"):
        state = pipeline_store.read_execution_state(*key, strict=True) or {}
    accounts = {tuple(value["provider_account"]) for value in state.get("core_obligations", {}).values()
                if value.get("provider_account")}
    def interrupt(states):
        for identity, obligation in states[key].get("core_obligations", {}).items():
            if obligation["status"] == "protected":
                obligation.update(status="interrupted", interrupted_at=time.time(), interruption_reason=reason)
                account = tuple(obligation.get("provider_account") or ())
                if account:
                    _sync_core_account(states, key, account, identity)
        return states
    with database_workload("interactive"):
        pipeline_store.mutate_execution_states([key, *accounts], interrupt)


def funded_core_research() -> bool:
    if _CALL.get().get("optional_core_unfunded"):
        return False
    key = _run_budget_key()
    if key is None:
        return False
    from . import pipeline_store
    from .database import database_workload
    with database_workload("interactive"):
        state = _run_budget_state(pipeline_store.read_execution_state(*key, strict=True) or {})
    return state["cap_microusd"] > 0


def best_effort_required() -> bool:
    """A missing/exhausted allowance narrows current work, never queues it.

    Ordinary transport calls without a research owner keep their configured
    routing. The provider's actual credit remains a separate hard constraint.
    """
    if _CALL.get().get("completion_only") or _CALL.get().get("optional_core_unfunded"):
        return True
    root = _CALL.get().get("root_run_id") or os.getenv("PIPELINE_RUN_ID")
    if not root:
        return False
    key = _run_budget_key()
    if key is None:
        # A producer with no selected allowance owes a current economical
        # report. Unbudgeted interactive/offline research retains its explicit
        # model configuration and separate owner spending policy.
        return os.getenv("PIPELINE_JOB_NAME") in {"news-ingest-and-features", "attention-home-build", "zopedia-learning"}
    from . import pipeline_store
    from .database import database_workload
    with database_workload("interactive"):
        state = _run_budget_state(pipeline_store.read_execution_state(*key, strict=True) or {})
    remaining = _run_remaining(state, for_admission=True)
    if _CALL.get().get("event_work") and not state.get("invalid_quote"):
        remaining += max(0, state.get("event_ceiling_microusd", state["cap_microusd"]) - state["cap_microusd"])
    return (bool(state.get("invalid_quote")) or remaining <= 0 or
            remaining < state.get("best_effort", {}).get("resume_headroom_microusd", 0))


def finish_research_economically(reason: str, *, required_microusd: int = 0) -> None:
    """Stop optional work until returned completion funds can afford it.

    Recovery shares this decision. Releasing unused core funds can reopen
    useful exploration in later tasks without resetting spending or clocks.
    """
    key = _run_budget_key()
    if key is None:
        return
    from . import pipeline_store
    from .database import database_workload
    def finish(state):
        state = _run_budget_state(state)
        state.setdefault("best_effort", {"reason": reason, "started_at": time.time()})
        state["best_effort"]["resume_headroom_microusd"] = max(
            required_microusd, state["best_effort"].get("resume_headroom_microusd", 0))
        return state
    with database_workload("interactive"):
        pipeline_store.mutate_execution_state(*key, finish)


def amend_research_budget(*, root_run_id: str, cap_usd: Any, approved_by: str,
                          authorization: str, amendment_id: str) -> dict[str, Any]:
    """Owner-authorized allowance amendment; never exposed as an agent tool.

    The administrative caller owns authorization. Retain its audit reference
    and all incurred obligations; retries of one amendment are idempotent.
    """
    if not all(str(value).strip() for value in (root_run_id, approved_by, authorization, amendment_id)):
        raise ValueError("Budget amendments require owner authorization and an audit identity.")
    cap = _microusd(cap_usd, maximum=False)
    if cap <= 0:
        raise ValueError("The amended allowance must be positive.")
    from . import pipeline_store
    from .database import database_workload
    def amend(state):
        if not state:
            raise ValueError("The logical run has no retained budget to amend.")
        amendments = state.setdefault("amendments", {})
        previous = amendments.get(amendment_id)
        record = {"cap_microusd": cap, "approved_by": approved_by, "authorization": authorization}
        if previous:
            if any(previous.get(key) != value for key, value in record.items()):
                raise ValueError("An amendment identity cannot authorize different changes.")
            return state
        if cap < state["cap_microusd"] - _run_remaining(state):
            raise ValueError("The amended allowance cannot discard incurred or reserved obligations.")
        amendments[amendment_id] = {**record, "previous_cap_microusd": state["cap_microusd"], "approved_at": time.time()}
        state["cap_microusd"] = cap
        if "base_microusd" in state:
            state["base_microusd"] = cap
            state["event_ceiling_microusd"] = cap + int(cap * Decimal(state["event_margin_fraction"]))
        return state
    with database_workload("interactive"):
        return pipeline_store.mutate_execution_state("research_run_budget", root_run_id, amend)


def research_spending_context() -> dict[str, Any]:
    """Live shared financial state for the agent, independent of its time budget."""
    key = _run_budget_key()
    if key is None:
        economical = best_effort_required()
        return {"cap_usd": None, "best_effort": economical,
                "constraint": ("Without an allowance, produce a current best-effort answer using the configured cheapest suitable model. Skip optional paid exploration; do not defer the report."
                               if economical else "No production run target applies; preserve the explicit model, time, provider-credit and separate research-spending policies.")}
    from . import pipeline_store
    from .database import database_workload
    with database_workload("interactive"):
        state = _run_budget_state(pipeline_store.read_execution_state(*key, strict=True) or {})
        accounts = {}
        for obligation in state.get("core_obligations", {}).values():
            account_key = tuple(obligation.get("provider_account") or ())
            if account_key and account_key not in accounts:
                account = pipeline_store.read_execution_state(*account_key, strict=True) or {}
                required = _future_core_microusd(account)
                free = _provider_unreserved_microusd(account)
                accounts[account_key] = {"provider": obligation["provider"],
                    "observed_at": account.get("observed_at"), "balance_usd": account.get("balance_usd"),
                    "credit_after_requests_usd": free / 1e6 if account.get("observed_at") is not None else None,
                    "future_completion_required_usd": required / 1e6,
                    "future_completion_shortfall_usd": max(0, required - free) / 1e6
                        if account.get("observed_at") is not None else None}
    attempts = state["attempts"].values()
    return {"root_run_id": key[1], "cap_usd": state["cap_microusd"] / 1e6,
            "provider_accounts": list(accounts.values()),
            "base_budget_usd": state.get("base_microusd", state["cap_microusd"]) / 1e6,
            "event_ceiling_usd": state.get("event_ceiling_microusd", state["cap_microusd"]) / 1e6,
            "event_margin_fraction": float(state.get("event_margin_fraction", 0)),
            "event_extensions": state.get("event_extensions", []),
            "event_available_usd": max(0, _run_remaining(state) +
                state.get("event_ceiling_microusd", state["cap_microusd"]) - state["cap_microusd"]) / 1e6,
            "event_settled_usd": sum(v["charged_microusd"] for v in attempts
                if v["status"] == "settled" and v.get("event_work")) / 1e6,
            "available_usd": max(0, _run_remaining(state, for_admission=True)) / 1e6,
            "settled_usd": sum(v["charged_microusd"] for v in attempts if v["status"] == "settled") / 1e6,
            "reserved_usd": sum(v["maximum_microusd"] for v in attempts if v["status"] != "settled") / 1e6,
            "protected_core_usd": sum(v["remaining_microusd"] for v in state.get("core_obligations", {}).values()
                                      if v["status"] == "protected") / 1e6,
            "interrupted_unspent_core_usd": sum(v["remaining_microusd"] for v in state.get("core_obligations", {}).values()
                                                if v["status"] == "interrupted") / 1e6,
            "best_effort": bool(state.get("invalid_quote")) or _run_remaining(state) <= 0 or _run_remaining(state) < state.get("best_effort", {}).get("resume_headroom_microusd", 0),
            "overrun_usd": max(0, sum(v["charged_microusd"] for v in attempts if v["status"] == "settled") - state["cap_microusd"]) / 1e6,
            "committed_above_target_usd": max(0, -_run_remaining(state)) / 1e6,
            "cost_measurement": "Inference uses metered token-price estimates; acquisition uses configured charge bounds. Unknown attempts remain fully reserved.",
            "prices": _price_snapshot(),
            "tool_maximum_usd_per_request": json.loads(os.getenv("RESEARCH_TOOL_MAX_COST_USD_JSON") or "{}"),
            "allocation": "Core interpretation, synthesis and review retain the configured full model and protected funds. Only available_usd can fund optional exploration. Stop optional work when that headroom is exhausted; release unused core funds after completion and reassess useful work. A small core-completion overrun is allowed and recorded. With no funded allowance, use the configured cheapest suitable model for a current report. Never defer current news to another run. More dollars permit depth, not compulsory spending or a longer deadline."}


def reserve_research_cost(*, attempt_id: str, maximum_usd: Any, category: str,
                          account_key: tuple[str, str] | None = None, account_transform=None,
                          wait_seconds: float = 0) -> bool:
    """Atomically admit account credit and a run charge; no partial reservation."""
    from . import pipeline_store
    from .database import database_workload
    run_key = _run_budget_key()
    keys = [key for key in (run_key, account_key) if key is not None]
    if not keys:
        return False
    amount = _microusd(maximum_usd)
    economy_completion = category == "inference" and (_CALL.get().get("routing") or {}).get("role") == "best_effort"
    core_work_id = _CALL.get().get("core_work_id")
    core_completion = category == "inference" and (_CALL.get().get("routing") or {}).get("role") == "core"
    def reserve(states):
        if run_key is not None:
            state = _run_budget_state(states[run_key])
            if attempt_id in state["attempts"]:
                raise ValueError("An execution attempt cannot reserve twice.")
            obligation = state.get("core_obligations", {}).get(core_work_id) if core_completion else None
            if core_completion and not obligation:
                raise ValueError("Core inference requires its declared completion funding.")
            if obligation and obligation.get("deadline_at", float("inf")) <= time.time():
                from .work_scope import WorkCancelled
                raise WorkCancelled("The original core completion window has expired.")
            if obligation and obligation["status"] != "protected":
                raise ValueError("Interrupted completion must resume through its original funding boundary.")
            if state.get("invalid_quote") and not (economy_completion or core_completion):
                raise ResearchBudgetExceeded("Run admission is paused because a request exceeded its quoted maximum.")
            if not (economy_completion or core_completion):
                needed = max(amount, state.get("best_effort", {}).get("resume_headroom_microusd", 0)) - _run_remaining(state, for_admission=True)
                if needed > 0 and not _extend_event_allowance(state, needed):
                    error = ResearchBudgetExceeded("Stop optional spending and complete this run with the cheapest suitable model.")
                    error.required_microusd = amount
                    raise error
            funded = min(amount, obligation["remaining_microusd"]) if obligation else 0
            if obligation:
                obligation["remaining_microusd"] -= funded
            state["attempts"][attempt_id] = {"status": "pending", "maximum_microusd": amount,
                                             "category": category, "started_at": time.time(),
                                             "best_effort": economy_completion,
                                             "event_work": _CALL.get().get("event_work"),
                                             "core_work_id": core_work_id if core_completion else None,
                                             "core_generation": obligation.get("generation") if obligation else None,
                                             "completion_funding_microusd": funded}
            states[run_key] = state
        if account_key is not None:
            if run_key is not None and core_completion:
                _sync_core_account(states, run_key, account_key, core_work_id)
            states[account_key] = account_transform(states[account_key])
        return states
    with database_workload("interactive"):
        if run_key is None:
            pipeline_store.mutate_execution_state(*account_key, account_transform)
        else:
            pipeline_store.mutate_execution_states(keys, reserve)
    return True


def settle_research_cost(*, attempt_id: str, actual_usd: Any | None,
                         account_key: tuple[str, str] | None = None, account_transform=None,
                         reservation: dict[str, Any] | None = None) -> None:
    from . import pipeline_store
    from .database import database_workload
    # A recovery worker owns a different run. Receipts retain the original
    # reservation identity instead of deriving it from the recovering process.
    run_key = (tuple(reservation["run"]) if reservation.get("run") else None) if reservation is not None else _run_budget_key()
    keys = [key for key in (run_key, account_key) if key is not None]
    if not keys:
        return
    def settle(states):
        if run_key is not None:
            state = states[run_key]
            attempt = state.get("attempts", {}).get(attempt_id)
            if attempt is None:
                # Admission may have failed before creating either obligation.
                if account_key is not None:
                    states[account_key] = account_transform(states[account_key])
                return states
            if actual_usd is None:
                if attempt["status"] != "settled":
                    attempt["status"] = "unknown"
            else:
                charged = max(_microusd(actual_usd), attempt.get("charged_microusd", 0))
                attempt.update(status="settled", charged_microusd=charged)
                obligation = state.get("core_obligations", {}).get(attempt.get("core_work_id"))
                if (obligation and obligation["status"] in {"protected", "interrupted"}
                        and obligation.get("generation") == attempt.get("core_generation")):
                    refund = max(0, attempt.get("completion_funding_microusd", 0) - charged)
                    obligation["remaining_microusd"] += refund - attempt.get("completion_refund_microusd", 0)
                    attempt["completion_refund_microusd"] = refund
                if charged > attempt["maximum_microusd"]:
                    # Keep the actual cost, stop further admission, and expose
                    # an invalid quote rather than hiding a billing overrun.
                    log.error("Research request exceeded its reserved maximum: %s", attempt_id)
                    state["invalid_quote"] = True
            attempt["finished_at"] = time.time()
        if account_key is not None:
            if run_key is not None and attempt.get("core_work_id"):
                _sync_core_account(states, run_key, account_key, attempt["core_work_id"])
            states[account_key] = account_transform(states[account_key])
        return states
    with database_workload("interactive"):
        if run_key is None:
            pipeline_store.mutate_execution_state(*account_key, account_transform)
        else:
            pipeline_store.mutate_execution_states(keys, settle)


def metered_tool_request(request, *args, cost_key: str, **kwargs):
    """Apply the same run allowance at the paid acquisition transport boundary.

    Tool quotes are deployment-owned upper bounds, including plan-specific
    units. Missing pricing never silently counts paid acquisition as free.
    """
    from .work_scope import check_work
    check_work()
    check_execution_ownership()
    if best_effort_required():
        raise ResearchBudgetExceeded("Optional paid acquisition stopped; finish the current report from this run's available evidence.")
    if _run_budget_key() is None:
        return request(*args, **kwargs)
    prices = json.loads(os.getenv("RESEARCH_TOOL_MAX_COST_USD_JSON") or "{}")
    if cost_key not in prices:
        raise ResearchWorkDeferred("A capped run requires a maximum charge quote for " + cost_key)
    maximum = prices[cost_key]
    deadline = time.monotonic() + float(kwargs.get("timeout") or 0)
    attempt_id = "toolhttp_" + uuid.uuid4().hex
    reserve_research_cost(attempt_id=attempt_id, maximum_usd=maximum, category="acquisition:" + cost_key,
                          wait_seconds=float(kwargs.get("timeout") or 0))
    sent = False
    response = None
    try:
        check_work()
        check_execution_ownership()
        from .llm import remaining_request_timeout, LLMDeadlineExceeded
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LLMDeadlineExceeded("Acquisition admission exhausted its original request deadline.")
        kwargs["timeout"] = remaining_request_timeout(remaining)
        sent = True
        response = request(*args, **kwargs)
        return response
    finally:
        # Bill the full configured bound after success. A disconnect or rejected
        # response may still consume provider units and remains reserved.
        actual = 0 if not sent else maximum if response is not None and 200 <= response.status_code < 300 else None
        try:
            retain_paid_result(lambda: settle_research_cost(attempt_id=attempt_id, actual_usd=actual))
        except Exception:
            log.warning("Acquisition settlement unavailable; maximum remains reserved: %s", attempt_id)


def normalize_provider_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Preserve provider fields and expose the common chat/Responses units."""
    result = dict(usage) if isinstance(usage, dict) else {}
    for common, native in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"),
                           ("prompt_tokens_details", "input_tokens_details"),
                           ("completion_tokens_details", "output_tokens_details")):
        if common not in result and native in result:
            result[common] = result[native]
    return result


def request_context_budget(config: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Preflight the complete request, including schemas and output headroom.

    cl100k is an estimate for other tokenizers, not provider-reported usage.
    Leave headroom for that difference and handle exact provider feedback too.
    Unknown deployments must declare their window through configuration.
    """
    window = int(os.getenv("LLM_CONTEXT_WINDOW_TOKENS") or (
        1048576 if config.provider == "deepseek" and
        urlparse(config.base_url).hostname == "api.deepseek.com" else 0
    ))
    if not window or not (payload.get("messages") or payload.get("input") or payload.get("instructions")):
        return {}
    import tiktoken

    encoding = tiktoken.get_encoding("cl100k_base")
    text = json.dumps(payload, ensure_ascii=False, default=str)
    tokens = len(encoding.encode_ordinary(text))
    default_output = 65536
    if config.provider == "deepseek" and urlparse(config.base_url).hostname == "api.deepseek.com":
        effort = payload.get("reasoning_effort") or getattr(config, "reasoning_effort", "high")
        thinking = (payload.get("thinking") or {}).get("type") or getattr(config, "thinking_mode", "enabled")
        default_output = 131072 if effort == "max" else 8192 if effort == "none" or thinking == "disabled" else 65536
    reserve = int(payload.get("max_tokens") or payload.get("max_output_tokens")
                  or max(default_output, int(os.getenv("LLM_CONTEXT_OUTPUT_RESERVE_TOKENS") or 0)))
    estimate = (tokens * 5 + 3) // 4
    return {"window_tokens": window, "estimated_input_tokens": estimate,
            "reserved_output_tokens": reserve, "fits": estimate + reserve <= window,
            "measurement": "cl100k estimate with 25% tokenizer headroom; provider usage is authoritative"}


@contextmanager
def model_call_context(**metadata: Any):
    token = _CALL.set({**_CALL.get(), **metadata})
    try:
        yield
    finally:
        _CALL.reset(token)


def execution_effort(default: str = "") -> str:
    return str(_CALL.get().get("inference_effort") or default)


def preserves_literal_output() -> bool:
    return bool(_CALL.get().get("literal_output"))


def current_research_access_scope() -> str:
    """A producer declares ownership; private API/research workers default off."""
    return os.getenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE", "").strip()


@contextmanager
def research_run_context(run_id: str):
    parent = _CALL.get()
    with model_call_context(root_run_id=parent.get("root_run_id") or os.getenv("PIPELINE_RUN_ID") or run_id,
                            parent_run_id=parent.get("run_id"), run_id=run_id):
        yield


@lru_cache(maxsize=1)
def _price_snapshot() -> dict[str, Any]:
    path = Path(os.getenv("LLM_PRICE_SNAPSHOT_PATH") or Path(__file__).with_name("llm_prices.json"))
    return json.loads(path.read_text())


def _pricing_model(config: Any, payload: dict[str, Any], operation: str) -> str:
    # Azure's wire model can be an arbitrary deployment name. Billing follows
    # the configured underlying model, not that resource alias.
    if operation == "embedding":
        return str(getattr(config, "embedding_model", "") or payload.get("model") or config.model)
    return str(payload.get("model") or payload.get("model_id") or config.model)


def estimated_charge(*, provider: str, model: str, usage: dict[str, Any] | None,
                     started_at: datetime) -> dict[str, Any]:
    """Versioned estimate, not an invoice; unknown usage/model is never zero."""
    if not isinstance(usage, dict) or not usage:
        return {"status": "usage_unavailable"}
    usage = normalize_provider_usage(usage)
    try:
        snapshot = _price_snapshot()
        price = snapshot["models"].get(provider + ":" + model)
        if price is None:
            return {"status": "price_unavailable"}
        if "usd_per_1000_characters" in price:
            characters = usage.get("input_characters")
            if characters is None:
                return {"status": "usage_unavailable"}
            amount = characters * price["usd_per_1000_characters"] / 1000
            return {"status": "estimated", "price_version": snapshot["version"], "source": price["source"],
                    "usd_min": amount, "usd_max": amount, "rate_band": "published_character_price"}
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        if input_tokens is None:
            return {"status": "usage_unavailable"}
        cached = usage.get("prompt_cache_hit_tokens", (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        output = usage.get("completion_tokens", usage.get("output_tokens", 0))
        rates = price["offpeak"]
        base = ((input_tokens-cached)*rates["input"] + cached*rates["cached_input"] + output*rates["output"]) / 1e6
        utc = started_at.astimezone(timezone.utc)
        peak = utc.weekday() < 5 and any(start <= utc.hour < end for start, end in price.get("peak_utc_hours", []))
        return {"status": "estimated", "price_version": snapshot["version"], "source": price["source"],
                "usd_min": base, "usd_max": base * price.get("peak_multiplier", 1) if peak else base,
                "rate_band": "peak_or_holiday" if peak else "offpeak"}
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "price_unavailable"}


def _account_key(config: Any) -> str:
    # Only the digest is retained. It identifies the billing account across
    # development/production workers without persisting credentials.
    endpoint = config.base_url
    if config.provider == "deepseek" and urlparse(endpoint).hostname == "api.deepseek.com":
        endpoint = "https://api.deepseek.com"
    return hashlib.sha256(f"{config.provider}:{endpoint}:{config.api_key}".encode()).hexdigest()


def _read_state(namespace: str, key: str) -> dict[str, Any] | None:
    from .pipeline_store import read_execution_state
    try:
        return read_execution_state(namespace, key)
    except Exception:
        log.warning("LLM execution checkpoint read unavailable", exc_info=False)
        return None


def _write_state(namespace: str, key: str, value: dict[str, Any]) -> None:
    from .pipeline_store import write_execution_state
    try:
        if not write_execution_state(namespace, key, value):
            log.warning("LLM execution checkpoint was not persisted")
    except Exception:
        log.warning("LLM execution checkpoint write unavailable", exc_info=False)


def ensure_provider_available(config: Any, *, timeout: float) -> None:
    if config.provider != "deepseek":
        return
    from .llm import LLMProviderUnavailable
    key = _account_key(config)
    with _ACCOUNT_LOCK:
        now = time.monotonic()
        local = _ACCOUNTS.get(key)
        if local and now - local[0] < 30:
            blocked = local[1]
        else:
            state = _read_state("provider_account", key)
            blocked = bool((state or {}).get("blocked")) or bool(local and local[1])
            if blocked:
                try:
                    response = requests.get(
                        config.base_url.rstrip("/") + "/user/balance",
                        headers={"Authorization": "Bearer " + config.api_key},
                        timeout=min(timeout, 15),
                    )
                    if response.ok and response.json().get("is_available") is True:
                        blocked = False
                        _write_state("provider_account", key, {"blocked": False})
                except (requests.RequestException, ValueError):
                    pass
            _ACCOUNTS[key] = (time.monotonic(), blocked)
    if blocked:
        raise LLMProviderUnavailable("DeepSeek account has insufficient funds; provider execution is paused until credit is restored.")


def _provider_budget_state(config: Any, amount: Any, deadline: float) -> dict[str, Any]:
    """Refresh authoritative credit without confusing future work with paid usage."""
    from . import pipeline_store
    from .llm import LLMProviderUnavailable
    namespace, key = "provider_budget", _account_key(config)
    amount = _microusd(amount)
    def needs_refresh(state):
        if time.time() - float(state.get("observed_at", 0)) >= 30:
            return True
        free = _provider_unreserved_microusd(state)
        if free >= amount:
            return False
        return any(
            value.get("finished_at", 0) > state.get("observed_at", 0)
            or (value.get("status") == "pending"
                and value.get("deadline_at", value.get("expires_at")) is not None
                and value.get("deadline_at", value.get("expires_at")) < time.time())
            for value in state.get("reservations", {}).values())
    state = pipeline_store.read_execution_state(namespace, key, strict=True) or {}
    while needs_refresh(state):
        check_execution_ownership()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LLMProviderUnavailable("Provider balance refresh exceeded the request deadline. Progress is retained.")
        with execution_ownership(namespace + ":balance", key,
                                 wait_seconds=min(remaining, 15), lease_seconds=60) as owned:
            state = pipeline_store.read_execution_state(namespace, key, strict=True) or {}
            if owned and needs_refresh(state):
                probe_started = time.time()
                response = requests.get(config.base_url.rstrip("/") + "/user/balance",
                    headers={"Authorization": "Bearer " + config.api_key},
                    timeout=min(max(deadline - time.monotonic(), 0.001), 15))
                response.raise_for_status()
                data = response.json()
                balances = [value for value in data["balance_infos"] if value["currency"] == "USD"]
                if len(balances) != 1:
                    raise LLMProviderUnavailable("Provider USD balance could not be established.")
                balance = float(balances[0]["total_balance"])
                if not Decimal(str(balance)).is_finite():
                    raise LLMProviderUnavailable("Provider USD balance is invalid.")
                def refresh(current):
                    reservations = {}
                    for identity, original in current.get("reservations", {}).items():
                        value = dict(original)
                        if value.get("status") == "settled" and value.get("finished_at", 0) < probe_started:
                            continue
                        ended = value.get("deadline_at", value.get("expires_at"))
                        if value.get("status") == "pending" and ended is not None and ended < probe_started:
                            value.update(status="unknown", finished_at=ended,
                                         uncertainty_reason="local_request_deadline_elapsed")
                        if value.get("status") == "unknown" and value.get("finished_at") is not None and value["finished_at"] < probe_started:
                            value["liquidity_observed_at"] = probe_started
                        reservations[identity] = value
                    current.update(observed_at=probe_started, balance_usd=balance,
                        available=data.get("is_available") is True, reservations=reservations,
                        liquidity_policy="current_provider_balance_and_active_requests",
                        unreconciled_at_balance_check={
                            "attempt_count": sum(v.get("status") == "unknown" for v in reservations.values()),
                            "maximum_usd": sum(v["amount_usd"] for v in reservations.values() if v.get("status") == "unknown"),
                            "cost_settlement": "Unknown usage is retained; this balance check does not establish individual charges."})
                    future = _future_core_microusd(current)
                    current["future_core_shortfall_microusd"] = max(0, future - _provider_unreserved_microusd(current))
                    return current
                state = pipeline_store.mutate_execution_state(namespace, key, refresh)
    return state


def reserve_provider_budget(config: Any, payload: dict[str, Any], *, attempt_id: str,
                            timeout: float, operation: str = "chat") -> bool:
    """Fund actual requests atomically, leaving future completion ahead of exploration.

    Future completion is a priority floor, not a fictional paid request. If the
    account cannot fund all declared future work, optional work stops; current
    required completion may still use genuine credit for its actual request.
    """
    official = config.provider == "deepseek" and urlparse(config.base_url).hostname == "api.deepseek.com"
    if not official:
        if _run_budget_key() is None:
            return False
        return reserve_research_cost(attempt_id=attempt_id,
            maximum_usd=_request_maximum_charge(config, payload, operation), category="inference", wait_seconds=timeout)
    from . import pipeline_store
    from .llm import LLMProviderUnavailable
    key = ("provider_budget", _account_key(config))
    deadline = time.monotonic() + timeout
    amount = _request_maximum_charge(config, payload, operation)
    maximum = _microusd(amount)
    completion = (_run_budget_key() is None or _CALL.get().get("completion_priority")
                  or (_CALL.get().get("routing") or {}).get("role") == "core")
    free = 0
    def reserve(current):
        nonlocal free
        if time.monotonic() >= deadline:
            raise LLMProviderUnavailable("Provider admission exceeded the request deadline. Progress is retained.")
        free = _provider_unreserved_microusd(current)
        future = _future_core_microusd(current)
        current["future_core_shortfall_microusd"] = max(0, future - free)
        if not current["available"] or free < maximum:
            raise LLMProviderUnavailable(
                f"Provider credit cannot cover the next request and active work (${free / 1e6:.2f} unreserved). Progress is retained.")
        if not completion and free - future < maximum:
            error = ResearchBudgetExceeded("Shared provider credit is protected for current core completion; stop optional spending.")
            error.required_microusd = maximum
            raise error
        current["reservations"][attempt_id] = {"amount_usd": maximum / 1e6, "status": "pending",
            "deadline_at": time.time() + max(0, deadline - time.monotonic()),
            "expires_at": time.time() + timeout + 300}
        current["future_core_shortfall_microusd"] = max(0, future - _provider_unreserved_microusd(current))
        return current
    while True:
        check_execution_ownership()
        if time.monotonic() >= deadline:
            raise LLMProviderUnavailable("Provider admission exceeded the request deadline. Progress is retained.")
        state = _provider_budget_state(config, amount, deadline)
        try:
            reserve_research_cost(attempt_id=attempt_id, maximum_usd=amount, category="inference",
                account_key=key, account_transform=reserve, wait_seconds=max(0, deadline - time.monotonic()))
        except LLMProviderUnavailable:
            fresh = pipeline_store.read_execution_state(*key, strict=True) or {}
            if fresh == state:
                raise
            continue
        break
    if free / 1e6 < float(os.getenv("LLM_PROVIDER_BALANCE_ALERT_USD", "10")):
        log.warning("provider_balance_low available_usd=%.2f reserved_request_usd=%.2f", free / 1e6, amount)
    return True


def _request_maximum_charge(config: Any, payload: dict[str, Any], operation: str | None = None) -> Decimal:
    from .llm import LLMProviderUnavailable
    if operation is None:
        operation = "embedding" if config.provider == "azure_openai" and "input" in payload and "messages" not in payload else "chat"
    price = _price_snapshot()["models"].get(config.provider + ":" + _pricing_model(config, payload, operation))
    if price is None:
        raise LLMProviderUnavailable("Budget admission requires a published price for the configured model.")
    if "usd_per_1000_characters" in price:
        return Decimal(len(str(payload.get("text") or ""))) * Decimal(str(price["usd_per_1000_characters"])) / 1000
    if operation == "embedding":
        import tiktoken
        inputs = payload["input"]
        if isinstance(inputs, str):
            inputs = [inputs]
        if inputs and all(type(value) is int for value in inputs):
            inputs = [inputs]
        encoding = tiktoken.get_encoding("cl100k_base")
        input_tokens = sum(len(encoding.encode_ordinary(value)) if isinstance(value, str) else len(value)
                           for value in inputs)
        return Decimal(input_tokens) * Decimal(str(price["offpeak"]["input"])) / 1_000_000
    context = request_context_budget(config, payload)
    if context:
        input_tokens, output_tokens = context["estimated_input_tokens"], context["reserved_output_tokens"]
    else:
        import tiktoken
        # Price all input, including schemas; leave tokenizer headroom. Models
        # without a known default must declare their maximum output explicitly.
        input_tokens = (len(tiktoken.get_encoding("cl100k_base").encode_ordinary(
            json.dumps(payload, ensure_ascii=False, default=str))) * 5 + 3) // 4
        output_tokens = payload.get("max_completion_tokens") or payload.get("max_output_tokens") or payload.get("max_tokens")
        if output_tokens is None and Decimal(str(price["offpeak"]["output"])) != 0:
            raise LLMProviderUnavailable("A capped run requires an explicit maximum output for this model.")
        output_tokens = int(output_tokens or 0)
    if output_tokens < 0:
        raise ValueError("Maximum output must be nonnegative.")
    return (Decimal(input_tokens) * Decimal(str(price["offpeak"]["input"]))
            + Decimal(output_tokens) * Decimal(str(price["offpeak"]["output"]))) * Decimal(str(price.get("peak_multiplier", 1))) / 1_000_000


def settle_provider_budget(config: Any, *, attempt_id: str, charge: dict[str, Any],
                           definitely_unbilled: bool, reservation: dict[str, Any] | None = None) -> None:
    def settle(state):
        value = state.get("reservations", {}).get(attempt_id)
        if value is None:
            # A fresh authoritative balance can already include this settled
            # request. Replaying a receipt must not recreate its wallet debit.
            return state
        if charge.get("status") == "estimated" or definitely_unbilled:
            amount = 0 if definitely_unbilled else float(charge["usd_max"])
            amount = max(amount, value["amount_usd"] if value["status"] == "settled" else 0)
            if value["status"] != "settled" or amount != value["amount_usd"]:
                # A real late receipt replaces the unknown estimate and protects
                # its debit until the next provider balance observation. Replay
                # neither recreates a debit nor moves its original finish time.
                value.update(status="settled", amount_usd=amount, finished_at=time.time())
                value.pop("liquidity_observed_at", None)
        elif value["status"] != "settled":
            # A disconnected response is unknown usage, never a free request.
            value.setdefault("finished_at", time.time())
            value["status"] = "unknown"
        return state
    actual = 0 if definitely_unbilled else charge.get("usd_max") if charge.get("status") == "estimated" else None
    if reservation is None:
        official_balance = config.provider == "deepseek" and urlparse(config.base_url).hostname == "api.deepseek.com"
        account_key = ("provider_budget", _account_key(config)) if official_balance else None
    else:
        account_key = tuple(reservation["account"]) if reservation.get("account") else None
    settle_research_cost(attempt_id=attempt_id, actual_usd=actual,
        account_key=account_key, account_transform=settle if account_key else None,
        reservation=reservation)


def reconcile_provider_reservation(event: dict[str, Any]) -> None:
    """Apply a retained final receipt to its original run and shared account."""
    reservation = (event.get("metadata") or {}).get("budget_reservation")
    if event.get("status") == "started" or not reservation:
        return
    run, account = reservation.get("run"), reservation.get("account")
    if run is not None and (len(run) != 2 or run[0] != "research_run_budget" or run[1] != event.get("root_run_id")):
        raise ValueError("Receipt run reservation does not match its execution.")
    if account is not None and (len(account) != 2 or account[0] != "provider_budget" or not re.fullmatch(r"[a-f0-9]{64}", account[1])):
        raise ValueError("Invalid provider account reservation identity.")
    settle_provider_budget(None, attempt_id=event["attempt_id"],
        charge=event["metadata"].get("estimated_charge") or {},
        definitely_unbilled=event.get("status") == "not_sent" or event.get("http_status") == 402,
        reservation=reservation)


def request_composition(payload: dict[str, Any], *, operation: str = "chat") -> dict[str, Any]:
    """Measure the final transport payload without retaining any prompt text."""
    encoder = json.JSONEncoder(ensure_ascii=True, default=str)
    def size(value: Any) -> int:
        return sum(len(part) for part in encoder.iterencode(value))
    roles: dict[str, int] = {}
    messages = payload.get("messages") or []
    if not messages and (operation == "responses" or payload.get("instructions")):
        supplied = payload.get("input") or []
        messages = ([{"role": "user", "content": supplied}] if isinstance(supplied, str) else list(supplied))
        if payload.get("instructions"):
            messages = [{"role": "system", "content": payload["instructions"]}, *messages]
    for message in messages:
        role = str(message.get("role") or "unknown") if isinstance(message, dict) else "unknown"
        roles[role] = roles.get(role, 0) + size(message)
    return {"serialized_characters": size(payload), "message_count": len(messages),
            "message_characters_by_role": roles, "tool_count": len(payload.get("tools") or []),
            "tool_schema_characters": size(payload.get("tools") or []),
            "response_schema_characters": size(payload.get("response_format") or (
                payload["text"].get("format") if isinstance(payload.get("text"), dict) else {}) or {}),
            "measurement": "serialized JSON characters; provider usage supplies actual tokens"}


def provider_post(client: Any = None, *, config: Any = None, session: Any = None,
                  operation: str = "chat", **kwargs: Any) -> requests.Response:
    """Meter each transport attempt, including failures and nested JSON repairs."""
    from .llm import llm_settings_scope, _remaining_request_timeout
    from .pipeline_store import record_provider_attempt
    config = config or client.config
    session = session or client.session
    check_execution_ownership()
    timeout = float(kwargs.get("timeout") or config.timeout_seconds)
    deadline = time.monotonic() + timeout
    context = dict(_CALL.get())
    payload = dict(kwargs.get("json") or {})
    if config.provider == "deepseek" and (operation == "json_repair" or context.get("literal_output")):
        # Non-thinking defaults to 8K while thinking defaults to 64K. A
        # mechanical conversion must not silently shorten an existing artifact.
        output_key = "max_output_tokens" if operation == "responses" else "max_tokens"
        payload.setdefault(output_key, int(os.getenv("DEEPSEEK_LITERAL_MAX_OUTPUT_TOKENS") or "65536"))
        kwargs["json"] = payload
    root = context.get("root_run_id") or os.getenv("PIPELINE_RUN_ID")
    response_identity = None
    if root and operation in {"chat", "json_repair"}:
        from .pipeline_store import read_execution_state
        digest = hashlib.sha256(json.dumps({"root": root, "account": _account_key(config),
            "operation": operation, "payload": payload}, sort_keys=True, default=str).encode()).hexdigest()
        response_identity = ("provider_response:" + llm_settings_scope(), digest)
        try:
            pointer = read_execution_state(response_identity[0] + ":inputs", response_identity[1])
            retained = read_execution_state(response_identity[0], pointer["artifact_id"]) if pointer else None
        except Exception:
            retained = None
            log.warning("Paid response checkpoint read unavailable")
        if retained and retained.get("replayable_after_cancellation"):
            # Exact request, model, account and original logical run only.
            # Adoption does not buy inference or record a second billed attempt.
            adopted = requests.Response()
            adopted.status_code = 200
            adopted._content = json.dumps(retained["data"], ensure_ascii=False).encode()
            adopted.headers["x-spectral-retained-attempt"] = retained["attempt_id"]
            return adopted
    ensure_provider_available(config, timeout=timeout)
    kwargs["timeout"] = _remaining_request_timeout(deadline)
    composition = request_composition(payload, operation=operation)
    context_budget = request_context_budget(config, payload) if operation not in {"embedding", "audio"} else {}
    if context_budget:
        composition["context_budget"] = context_budget
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    attempt_id = "llmhttp_" + uuid.uuid4().hex
    reservation_identity = {"run": _run_budget_key(), "account":
        ("provider_budget", _account_key(config)) if config.provider == "deepseek" and
        urlparse(config.base_url).hostname == "api.deepseek.com" else None}
    initial_event = {
        "attempt_id": attempt_id, "model_call_id": context.get("model_call_id"),
        "root_run_id": context.get("root_run_id") or os.getenv("PIPELINE_RUN_ID") or context.get("run_id"),
        "parent_run_id": context.get("run_id") or context.get("parent_run_id"),
        "settings_scope": llm_settings_scope(),
        "surface": context.get("surface") or getattr(client, "_zopedia_surface", "") or "provider.direct",
        "purpose": context.get("purpose") or "direct." + operation,
        "provider": config.provider, "requested_model": payload.get("model") or config.model,
        "operation": operation, "status": "started", "started_at_utc": started, "duration_ms": 0,
        "metadata": {"request_composition": composition, "job_name": os.getenv("PIPELINE_JOB_NAME"),
                     "budget_reservation": reservation_identity,
                     "routing": context.get("routing")},
    }
    try:
        if not record_provider_attempt(initial_event):
            log.warning("Provider attempt start could not be persisted: %s", attempt_id)
    except Exception:
        log.warning("Provider attempt start accounting unavailable: %s", attempt_id)
    response = None
    error_type = ""
    transport_attempted = False
    budget_reserved = False
    try:
        if context_budget and not context_budget["fits"]:
            raise LLMContextWindowExceeded(json.dumps(context_budget))
        budget_reserved = reserve_provider_budget(config, payload, attempt_id=attempt_id,
                                                  timeout=_remaining_request_timeout(deadline), operation=operation)
        check_execution_ownership()
        kwargs["timeout"] = _remaining_request_timeout(deadline)
        transport_attempted = True
        response = session.post(**kwargs)
        if response.status_code == 400 and re.search(
            r"maximum context length|context[_ ](?:length|window).*exceed|too many tokens",
            response.text, re.IGNORECASE,
        ):
            raise LLMContextWindowExceeded(response.text[:2000])
        if config.provider == "deepseek" and response.status_code == 402:
            key = _account_key(config)
            with _ACCOUNT_LOCK:
                _ACCOUNTS[key] = (time.monotonic(), True)
            _write_state("provider_account", key, {"blocked": True, "http_status": 402})
        return response
    except BaseException as exc:
        error_type = type(exc).__name__
        from .llm import LLMProviderUnavailable
        if isinstance(exc, LLMProviderUnavailable):
            record_execution_issue("provider_admission:" + config.provider, exc)
        raise
    finally:
        data: dict[str, Any] = {}
        if response is not None:
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    data = parsed
            except ValueError:
                pass
        usage = data.get("usage")
        if operation == "audio" and response is not None and 200 <= response.status_code < 300:
            usage = {"input_characters": len(str(payload.get("text") or ""))}
        event = {
            "attempt_id": attempt_id,
            "model_call_id": context.get("model_call_id"),
            "root_run_id": context.get("root_run_id") or os.getenv("PIPELINE_RUN_ID") or context.get("run_id"),
            "parent_run_id": context.get("run_id") or context.get("parent_run_id"),
            "settings_scope": llm_settings_scope(),
            "surface": context.get("surface") or getattr(client, "_zopedia_surface", "") or "provider.direct",
            "purpose": context.get("purpose") or "direct." + operation,
            "provider": config.provider, "requested_model": payload.get("model") or config.model,
            "reported_model": data.get("model"), "operation": operation,
            "status": "success" if response is not None and 200 <= response.status_code < 300 else "failure" if transport_attempted else "not_sent",
            "http_status": response.status_code if response is not None else None,
            "started_at_utc": started, "duration_ms": (time.monotonic() - t0) * 1000,
            "usage": normalize_provider_usage(usage) if isinstance(usage, dict) else usage,
            "metadata": {
                "transport_attempted": transport_attempted,
                "budget_reservation": reservation_identity,
                "request_composition": composition,
                "error_type": error_type, "provider_request_id": data.get("id"),
                "response_status": data.get("status"), "native_usage": data.get("usage"),
                "response_error": data.get("error"),
                "response_finish_reasons": [choice.get("finish_reason") for choice in data.get("choices") or []],
                "sampling_parameters": {key: payload[key] for key in ("temperature", "top_p", "max_tokens", "max_output_tokens") if key in payload},
                "inference_effort": payload.get("reasoning_effort") or (payload.get("reasoning") or {}).get("effort"), "thinking": payload.get("thinking"),
                "input_hash": hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(),
                "job_name": os.getenv("PIPELINE_JOB_NAME"),
                "routing": context.get("routing"),
                "research_subject": context.get("research_subject"),
                "artifact_input_id": context.get("artifact_input_id"),
                "literal_output": bool(context.get("literal_output")),
                "max_output_tokens": payload.get("max_tokens") or payload.get("max_output_tokens"),
                "input_characters": len(str(payload.get("text") or "")) if operation == "audio" else None,
                "pricing_model": _pricing_model(config, payload, operation),
                "estimated_charge": estimated_charge(provider=config.provider,
                    model=_pricing_model(config, payload, operation), usage=usage, started_at=started),
            },
        }
        from .work_scope import work_expired
        late_response = work_expired() or time.monotonic() >= deadline
        def retain():
            if response_identity and response is not None and response.status_code == 200 and data.get("choices"):
                from .pipeline_store import retain_provider_response
                try:
                    event["metadata"]["retained_response"] = retain_provider_response(
                        *response_identity, value={"attempt_id": attempt_id, "data": data,
                            "root_run_id": root, "replayable_after_cancellation": late_response,
                            "created_at_utc": datetime.now(timezone.utc).isoformat()})
                except Exception:
                    log.warning("Complete provider response retention unavailable: %s", attempt_id)
            if budget_reserved:
                try:
                    settle_provider_budget(config, attempt_id=attempt_id,
                        charge=event["metadata"]["estimated_charge"],
                        definitely_unbilled=not transport_attempted or (response is not None and response.status_code == 402),
                        reservation=reservation_identity)
                    event["metadata"]["reservation_settled"] = True
                except Exception:
                    log.warning("Provider budget settlement unavailable; reservation retained: %s", attempt_id)
            if not record_provider_attempt(event):
                raise RuntimeError("The provider receipt could not be retained")
        try:
            retain_paid_result(retain)
        except Exception:
            # No request text or credentials are included. A bounded receipt
            # write may outlive child cancellation; paid work cannot restart.
            log.warning("Provider attempt accounting unavailable; receipt=%s",
                        json.dumps(event, sort_keys=True, default=str), exc_info=False)


def research_artifact_key(*, access_scope: str, subject: str, query: str,
                          schema: dict[str, Any], config: Any) -> tuple[str, str]:
    from .llm import get_active_narrative_style_rule, llm_settings_scope
    namespace = f"research_artifact:{llm_settings_scope()}:{access_scope}"
    from .aql_zopedia_engine import research_engine_revision
    from .inference_policy import policy
    routing = policy()
    budget_key = _run_budget_key()
    state = _read_state(*budget_key) if budget_key else None
    contract = {"engine_revision": research_engine_revision(), "query": query, "schema": schema, "provider": config.provider,
                "inference_policy": {key: routing.get(key) for key in ("models", "routes", "best_effort_model", "core_tasks")},
                "research_target_microusd": (state or {}).get("base_microusd", (state or {}).get("cap_microusd", _microusd(os.getenv("RESEARCH_RUN_BUDGET_USD") or "0"))) if budget_key else None,
                "event_ceiling_microusd": (state or {}).get("event_ceiling_microusd") if budget_key else None,
                "economical_completion": best_effort_required() and not funded_core_research(),
                "endpoint": config.base_url, "model": config.model,
                "thinking": config.thinking_mode, "effort": execution_effort(config.reasoning_effort),
                "temperature": config.temperature, "api_style": getattr(config, "api_style", "chat"), "style": get_active_narrative_style_rule(),
                "literal_output": preserves_literal_output(),
                "subject": subject, "representation_version": 1}
    key = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return namespace, key


def read_research_artifact(namespace: str, key: str, *, strict: bool = False) -> dict[str, Any] | None:
    from .pipeline_store import read_execution_state
    read = (lambda ns, state_key: read_execution_state(ns, state_key, strict=True)) if strict else _read_state
    pointer = read(namespace + ":inputs", key)
    return read(namespace, pointer["artifact_id"]) if pointer else None


def retain_research_artifact(namespace: str, key: str, *, subject: str, task: str,
                             result: dict[str, Any]) -> dict[str, Any] | None:
    from .pipeline_store import retain_research_checkpoint
    from .json_utils import to_jsonable
    result = to_jsonable(result)
    artifact_id = hashlib.sha256((key + json.dumps(result, sort_keys=True, allow_nan=False, default=str)).encode()).hexdigest()
    value = {"artifact_id": artifact_id, "input_id": key, "subject": subject, "task": task,
             "created_at_utc": datetime.now(timezone.utc).isoformat(),
             "root_run_id": os.getenv("PIPELINE_RUN_ID"), "result": result}
    try:
        stored = retain_research_checkpoint(namespace=namespace, input_key=key, artifact=value,
                                            subject=subject, task=task)
        if stored:
            return {"artifact_id": artifact_id, "input_id": key, "namespace": namespace,
                    "created_at_utc": stored["created_at_utc"]}
    except Exception:
        log.warning("Completed research artifact was not checkpointed", exc_info=False)


def latest_research_artifact(*, access_scope: str, subject: str, task: str) -> dict[str, Any] | None:
    from .llm import llm_settings_scope
    namespace = f"research_artifact:{llm_settings_scope()}:{access_scope}"
    frozen = _CALL.get().get("prior_research") or {}
    identity = subject + ":" + task
    if identity in frozen:
        return frozen[identity]
    pointer = _read_state(namespace + ":latest", subject + ":" + task)
    artifact = _read_state(namespace, pointer["artifact_id"]) if pointer else None
    if not artifact:
        return None
    # Prior findings are evidence, not recursive copies of old agent prompts.
    return {key: artifact.get(key) for key in ("artifact_id", "input_id", "subject", "task", "created_at_utc")} | {
        "payload": (artifact.get("result") or {}).get("payload") or {},
        "artifact_context": (artifact.get("result") or {}).get("artifact_context") or {},
    }


def checkpoint_structured_research(function):
    """Opt-in, environment-owned replay of completed supplied-evidence stages.

    Live acquisition is never treated as timeless. Tool-using stages remain
    fresh research; their dated findings can be supplied to later planning.
    """
    @wraps(function)
    def run(**kwargs):
        from jsonschema import Draft202012Validator
        from .aql_zopedia_engine import load_aql_zopedia_llm_client

        access = kwargs.get("reuse_scope") or ""
        effort = kwargs.get("inference_effort") or ""
        subject = kwargs.get("research_subject") or ""
        def execute():
            result = function(**kwargs)
            if kwargs.get("artifact_context") is not None:
                result = {**result, "artifact_context": kwargs["artifact_context"]}
            return result
        with model_call_context(**{"research_subject": subject,
                                  **({"inference_effort": effort} if effort else {})}):
            if (not access or kwargs.get("persist_findings") or kwargs.get("conversation_history")
                    or kwargs.get("review_answer") or kwargs.get("max_tool_calls") is None):
                return execute()
            live_acquisition = kwargs["max_tool_calls"] > 0
            run_instance = _CALL.get().get("work_instance") or os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME") or os.getenv("PIPELINE_RUN_ID")
            if live_acquisition and not run_instance:
                return execute()
            client = kwargs.get("llm_client") or load_aql_zopedia_llm_client(
                surface=kwargs["surface"]
            )
            if client is None:
                return execute()
            kwargs["llm_client"] = client
            query = (kwargs.get("reuse_identity") or kwargs["query"]) + json.dumps(kwargs.get("initial_evidence"), sort_keys=True, default=str)
            query += json.dumps({key: kwargs.get(key) for key in (
                "instructions", "schema_name", "task", "surface", "write_policy", "request_context",
                "answer_review_owner", "run_timeout_seconds", "llm_step_timeout_seconds",
            )}, sort_keys=True, default=str)
            if kwargs.get("artifact_context") is not None:
                query += json.dumps(kwargs["artifact_context"], sort_keys=True, default=str)
            if live_acquisition:
                # Acquisition resumes only within this execution. A future
                # scheduled investigation must reassess fresh external inputs.
                query += json.dumps({"run_instance": run_instance, "max_tool_calls": kwargs["max_tool_calls"]})
            namespace, key = research_artifact_key(access_scope=access, subject=subject,
                                                  query=query, schema=kwargs["schema"], config=client.config)
            artifact = None if kwargs.get("force_refresh") else read_research_artifact(namespace, key)
            saved = (artifact or {}).get("result") or {}
            payload = saved.get("payload")
            if isinstance(payload, dict) and not list(Draft202012Validator(kwargs["schema"]).iter_errors(payload)):
                result = {**saved, "artifact_reuse": {"artifact_id": artifact["artifact_id"],
                                                     "created_at_utc": artifact["created_at_utc"],
                                                     "namespace": namespace, "input_id": key}}
                callback = kwargs.get("progress_callback")
                if callback:
                    callback({"stage": "research_artifact_reused", "message": "Reusing completed research for unchanged evidence.",
                              "artifact_id": artifact["artifact_id"], "progress": .9})
                return result
            incomplete = None if kwargs.get("force_refresh") else _read_state(namespace + ":incomplete", key)
            retained_agent = ((incomplete or {}).get("result") or {}).get("agent_result")
            if live_acquisition and isinstance(retained_agent, dict):
                kwargs["retained_agent_result"] = retained_agent
            partial_payload = ((incomplete or {}).get("result") or {}).get("partial_payload")
            if isinstance(partial_payload, dict):
                kwargs["retained_partial_payload"] = partial_payload
            partial_text = ((incomplete or {}).get("result") or {}).get("partial_text")
            if partial_text:
                kwargs["retained_partial_text"] = partial_text
            with model_call_context(artifact_input_id=key):
                result = execute()
            payload = result.get("payload")
            if (result.get("status") == "completed" and isinstance(payload, dict)
                    and not list(Draft202012Validator(kwargs["schema"]).iter_errors(payload))):
                reference = retain_research_artifact(namespace, key, subject=subject, task=kwargs["task"], result=result)
                if reference:
                    result = {**result, "research_artifact": reference}
            elif result.get("status") == "failed":
                # Keep paid work for bounded schema repair; never publish it as
                # a completed checkpoint or let it authorize knowledge writes.
                _write_state(namespace + ":incomplete", key, {
                    "input_id": key, "subject": subject, "task": kwargs["task"],
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "query": kwargs["query"], "schema": kwargs["schema"], "result": result,
                })
            return result
    return run


def checkpoint_agent_research(function):
    """Resume owned acquisition within one platform execution, never a new day.

    Knowledge write permission is separate from saving paid operational work.
    Private conversations remain with their existing user-owned run repository.
    """
    @wraps(function)
    def run(**kwargs):
        access = current_research_access_scope()
        execution = _CALL.get().get("work_instance") or os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME") or os.getenv("PIPELINE_RUN_ID")
        request_context = kwargs.get("request_context") or {}
        if (not access or not execution or kwargs.get("persist_findings", True)
                or kwargs.get("conversation_history") or kwargs.get("force_refresh")
                or kwargs.get("service") is not None
                or kwargs.get("write_policy") not in {None, "none"}
                or request_context.get("owner_id") or request_context.get("conversation_id")):
            return function(**kwargs)
        from . import llm, pipeline_store
        from .aql_zopedia_engine import (
            load_aql_zopedia_llm_client, research_engine_revision, resolve_research_budget,
        )
        # Import the shared acquisition owner before capturing its prompt defaults.
        from . import zopedia_agent, zopedia_research  # noqa: F401

        client = kwargs.get("llm_client") or load_aql_zopedia_llm_client(surface=kwargs.get("surface", "zopedia.chat"))
        if client is None:
            return function(**kwargs)
        kwargs["llm_client"] = client
        budget = resolve_research_budget(**{
            key: kwargs.get(key) for key in (
                "max_tool_calls", "run_timeout_seconds", "tool_call_timeout_seconds",
                "llm_step_timeout_seconds", "prefetch_timeout_seconds",
            )
        })
        llm.load_prompt_overrides()
        contract = {
            "execution": execution, "engine": research_engine_revision(),
            "request": {key: value for key, value in kwargs.items() if key not in {
                "llm_client", "service", "progress_callback", "checkpoint_callback", "resume_state",
            }},
            "budget": budget,
            "prompts": {item["key"]: item["prompt"] for item in llm.list_narrative_prompts()},
            "parameters": {item["key"]: item["value"] for item in llm.list_config_params()},
        }
        namespace, key = research_artifact_key(
            access_scope=access, subject=kwargs.get("task", "agent_answer"),
            query=json.dumps(contract, sort_keys=True, ensure_ascii=False, default=str),
            schema={}, config=client.config,
        )
        # Source capture survives changes to model, dollar allowance, worker
        # count and phase capacity. Completed answers retain their stricter
        # objective/model/prompt identity and must not become deeper by reuse.
        capture_key = hashlib.sha256(json.dumps({"execution": _CALL.get().get("root_run_id") or os.getenv("PIPELINE_RUN_ID") or execution,
            "request": {field: kwargs.get(field) for field in (
                "query", "task", "surface", "initial_evidence", "request_context", "write_policy")}},
            sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        # A replica retry retains the research identity as well as its evidence.
        kwargs.setdefault("run_id", "agrun_" + key[:40])
        if kwargs["run_id"] is None:
            kwargs["run_id"] = "agrun_" + key[:40]
        owner = uuid.uuid4().hex
        lease_namespace = namespace + ":leases"
        deadline = time.monotonic() + float(budget["run_timeout_seconds"])
        claimed = False
        try:
            while True:
                artifact = read_research_artifact(namespace, key, strict=True)
                if artifact:
                    return {**artifact["result"], "artifact_reuse": {
                        "artifact_id": artifact["artifact_id"], "namespace": namespace,
                        "input_id": key, "created_at_utc": artifact["created_at_utc"],
                    }}
                claimed = pipeline_store.claim_execution_state(
                    lease_namespace, key, owner=owner,
                    seconds=float(budget["run_timeout_seconds"]) + 120,
                )
                if claimed:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Compatible research is still owned by another worker.")
                time.sleep(5)
            # Close the race between the preceding read and acquiring ownership.
            artifact = read_research_artifact(namespace, key, strict=True)
            if artifact:
                return {**artifact["result"], "artifact_reuse": {"artifact_id": artifact["artifact_id"], "input_id": key}}
            incomplete = pipeline_store.read_execution_state(namespace + ":captures", capture_key, strict=True) or {}
            if not incomplete:
                incomplete = pipeline_store.read_execution_state(namespace + ":incomplete", key, strict=True) or {}
            if incomplete.get("checkpoint") and not kwargs.get("resume_state"):
                state = incomplete["checkpoint"]
                if incomplete.get("engine") not in {None, research_engine_revision()}:
                    # Read complete source captures under today's semantics;
                    # an old plan, draft or working-context selection does not
                    # acquire acceptance under a changed engine.
                    state = {field: state.get(field) for field in ("tool_calls", "request_context", "write_policy")}
                kwargs["resume_state"] = state
                if not state.get("continued_request"):
                    # Restore defaults resolved on the first attempt, including
                    # its clock. Explicit inputs in this same contract still win.
                    kwargs["request_context"] = {
                        **(state.get("request_context") or {}),
                        **(kwargs.get("request_context") or {}),
                    }
            original_checkpoint = kwargs.get("checkpoint_callback")
            checkpoint_saved = bool(incomplete.get("checkpoint"))

            def checkpoint(state):
                nonlocal checkpoint_saved
                if original_checkpoint:
                    original_checkpoint(state)
                checkpoint_saved = pipeline_store.write_execution_state(namespace + ":captures", capture_key, {
                    "checkpoint": state, "execution": execution, "input_id": key, "engine": research_engine_revision(),
                })
                if not checkpoint_saved:
                    log.warning("Research acquisition checkpoint unavailable: %s", key)

            kwargs["checkpoint_callback"] = checkpoint
            with model_call_context(artifact_input_id=key):
                result = function(**kwargs)
            result.setdefault("runtime", {})["operational_checkpoint"] = checkpoint_saved
            result["runtime"]["resumable"] = checkpoint_saved or result["runtime"].get("resumable", False)
            if result.get("status") == "completed" and result.get("answer_markdown"):
                reference = retain_research_artifact(
                    namespace, key, subject="", task=kwargs.get("task", "agent_answer"), result=result,
                )
                if reference:
                    result = {**result, "research_artifact": reference}
            return result
        finally:
            if claimed:
                try:
                    pipeline_store.release_execution_state(lease_namespace, key, owner=owner)
                except Exception:
                    log.warning("Research execution lease release unavailable: %s", key)
    return run
