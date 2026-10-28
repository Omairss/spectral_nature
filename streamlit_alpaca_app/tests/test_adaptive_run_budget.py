"""Financial and ownership contracts on real PostgreSQL; no prose grading."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timezone
import os
import json
import threading
import time
import uuid
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import requests

from services import database, llm_execution as execution, pipeline_store
from services.work_scope import check_work


@pytest.fixture
def store(monkeypatch):
    dsn = os.getenv("SN_DB_TEST_DSN")
    if not dsn:
        pytest.skip("Set SN_DB_TEST_DSN to an isolated PostgreSQL database")
    monkeypatch.setattr(pipeline_store, "_postgres_connection_string", lambda: dsn)
    monkeypatch.setenv("PIPELINE_RUN_ID", "budget-contract-" + uuid.uuid4().hex)
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "10")
    monkeypatch.setenv("POSTGRES_GLOBAL_ACTIVE_LIMIT", "3")
    monkeypatch.setenv("POSTGRES_GLOBAL_BACKGROUND_LIMIT", "1")
    with database.database_workload("interactive"):
        conn = pipeline_store.open_database_connection()
        with conn:
            pipeline_store._ensure_execution_store(conn)
    return dsn


def test_checkpoint_normalizes_nested_missing_values_at_the_shared_boundary(store):
    namespace = "json-contract-" + uuid.uuid4().hex
    payload = {"lens": float("nan"), "nested": [np.float64("inf"), pd.NA, pd.NaT],
               "when": datetime(2026, 10, 27, tzinfo=timezone.utc), "count": np.int64(3)}
    assert pipeline_store.write_execution_state(namespace, "one", payload)
    assert pipeline_store.write_execution_states(namespace, {"many": payload})
    mutated = pipeline_store.mutate_execution_state(namespace, "mutate", lambda _: payload)
    expected = {"lens": None, "nested": [None, None, None], "when": "2026-10-27T00:00:00+00:00", "count": 3}
    assert mutated == pipeline_store.read_execution_state(namespace, "one", strict=True) == expected
    assert pipeline_store.read_execution_state(namespace, "many", strict=True) == expected
    retained = pipeline_store.retain_research_checkpoint(namespace=namespace, input_key="input", subject="subject",
        task="task", artifact={**payload, "artifact_id": "artifact", "created_at_utc": "2026-10-27T00:00:00Z"})
    assert retained["lens"] is None and retained["nested"] == [None, None, None]


def test_run_and_wallet_reservation_rollback_together(store):
    wallet = ("wallet-contract", uuid.uuid4().hex)
    pipeline_store.write_execution_state(*wallet, {"credit": 100, "attempts": {}})
    def cannot_admit(state):
        state["credit"] -= 2
        raise RuntimeError("account admission failed")
    with pytest.raises(RuntimeError, match="account admission failed"):
        execution.reserve_research_cost(attempt_id="rejected", maximum_usd=2, category="inference",
            account_key=wallet, account_transform=cannot_admit)
    assert pipeline_store.read_execution_state(*wallet, strict=True)["credit"] == 100
    assert pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True) is None


def test_sixteen_concurrent_workers_share_ten_dollars_and_settlement_refunds_headroom(store):
    def admit(index):
        try:
            execution.reserve_research_cost(attempt_id=str(index), maximum_usd=2, category="inference")
            return str(index)
        except execution.ResearchBudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=16) as pool:
        admitted = [value for value in pool.map(admit, range(16)) if value is not None]
    assert len(admitted) == 5
    assert execution.research_spending_context()["available_usd"] == 0
    for identity in admitted[:4]:
        execution.settle_research_cost(attempt_id=identity, actual_usd=.5)
        execution.settle_research_cost(attempt_id=identity, actual_usd=.5)
    context = execution.research_spending_context()
    assert context["settled_usd"] == 2 and context["reserved_usd"] == 2 and context["available_usd"] == 6
    with ThreadPoolExecutor(max_workers=8) as pool:
        new = [value for value in pool.map(admit, range(16, 24)) if value is not None]
    assert len(new) == 3 and execution.research_spending_context()["available_usd"] == 0


def test_unknown_charge_stays_reserved_and_late_receipt_never_regresses_a_settlement(store):
    execution.reserve_research_cost(attempt_id="disconnected", maximum_usd=8, category="inference")
    execution.settle_research_cost(attempt_id="disconnected", actual_usd=None)
    assert execution.research_spending_context()["available_usd"] == 2
    with pytest.raises(execution.ResearchBudgetExceeded):
        execution.reserve_research_cost(attempt_id="retry", maximum_usd=3, category="inference")
    execution.settle_research_cost(attempt_id="disconnected", actual_usd=1)
    execution.settle_research_cost(attempt_id="disconnected", actual_usd=None)
    assert execution.research_spending_context()["available_usd"] == 9


@pytest.mark.parametrize("cap", ["10", "1000", "10000.75"])
def test_continuous_cap_nested_work_and_retry_do_not_create_extra_allowances(store, monkeypatch, cap):
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", cap)
    with execution.research_run_context("parent"):
        execution.reserve_research_cost(attempt_id="parent", maximum_usd="1.0000001", category="inference")
        with execution.research_run_context("child"):
            execution.reserve_research_cost(attempt_id="child", maximum_usd=2, category="acquisition")
    context = execution.research_spending_context()
    assert context["reserved_usd"] == 3.000001
    assert context["available_usd"] == float(cap) - 3.000001
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "20")
    execution.reserve_research_cost(attempt_id="retry", maximum_usd=1, category="inference")
    assert execution.research_spending_context()["cap_usd"] == float(cap)


def test_explicit_budget_amendment_retains_spending_and_requires_distinct_authorization(store):
    execution.reserve_research_cost(attempt_id="existing", maximum_usd=8, category="inference")
    args = {"root_run_id": execution._run_budget_key()[1], "cap_usd": 1000, "approved_by": "owner",
            "authorization": "test-only-owner-approval", "amendment_id": "approval-1"}
    execution.amend_research_budget(**args)
    execution.amend_research_budget(**args)
    context = execution.research_spending_context()
    assert context["cap_usd"] == 1000 and context["available_usd"] == 992 and context["reserved_usd"] == 8
    with pytest.raises(ValueError, match="different changes"):
        execution.amend_research_budget(**{**args, "cap_usd": 2000})
    with pytest.raises(ValueError, match="obligations"):
        execution.amend_research_budget(**{**args, "cap_usd": 7, "amendment_id": "approval-2"})


def test_waiting_worker_uses_returned_reservation_inside_its_original_clock(store):
    execution.reserve_research_cost(attempt_id="active", maximum_usd=9, category="inference")
    context = copy_context()
    timer = threading.Timer(.2, context.run, args=(lambda: execution.settle_research_cost(attempt_id="active", actual_usd=.2),))
    timer.start()
    try:
        assert execution.reserve_research_cost(attempt_id="waiter", maximum_usd=9, category="inference", wait_seconds=2)
    finally:
        timer.join()
    assert execution.research_spending_context()["available_usd"] == .8


def test_metadata_renews_while_background_capacity_is_occupied(store):
    ready = threading.Event()
    done = threading.Event()
    errors = []
    def background():
        try:
            with database.open_database_session(store, workload="background") as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    ready.set()
                    cur.execute("SELECT pg_sleep(1)")
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()
    thread = threading.Thread(target=background)
    thread.start()
    assert ready.wait(5)
    try:
        with database.database_workload("background"), execution.execution_ownership("lease-contract", uuid.uuid4().hex, lease_seconds=.6) as owned:
            assert owned
            time.sleep(.8)
            check_work()
    finally:
        thread.join(5)
    assert done.is_set() and not errors


def test_transient_renewal_error_retries_before_confirmed_expiry(store, monkeypatch):
    renew = pipeline_store.renew_execution_state
    calls = []
    def intermittent(*args, **kwargs):
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise RuntimeError("temporary database failure")
        return renew(*args, **kwargs)
    monkeypatch.setattr(pipeline_store, "renew_execution_state", intermittent)
    with execution.execution_ownership("lease-contract", uuid.uuid4().hex, lease_seconds=.6):
        time.sleep(.8)
        check_work()
    assert len(calls) >= 2


def test_real_provider_boundary_waits_for_shared_headroom_and_meters_each_attempt(store, monkeypatch):
    from services.llm import LLMConfig
    config = LLMConfig(provider="deepseek", api_key="test-key", model="deepseek-v4-pro", base_url="https://api.deepseek.com")
    execution._ACCOUNTS.clear()
    key = execution._account_key(config)
    pipeline_store.write_execution_state("provider_budget", key,
        {"available": True, "observed_at": time.time(), "balance_usd": 100, "reservations": {}})
    monkeypatch.setattr(execution.requests, "get", lambda *a, **kw: pytest.fail("Fresh balance requires no remote API"))
    def post(**kwargs):
        time.sleep(.1)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"model": config.model, "usage": {"prompt_tokens": 10, "completion_tokens": 20}}).encode()
        return response
    def call(index):
        return execution.provider_post(config=config, session=SimpleNamespace(post=post), timeout=5,
            json={"model": config.model, "messages": [{"role": "user", "content": "Contract input"}]})
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(response.ok for response in pool.map(call, range(8)))
    state = pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True)
    assert len(state["attempts"]) == 8 and all(v["status"] == "settled" for v in state["attempts"].values())
    context = execution.research_spending_context()
    assert context["reserved_usd"] == 0 and 0 < context["settled_usd"] < .01


def test_paid_acquisition_requires_quote_and_unknown_transport_stays_reserved(store, monkeypatch):
    sent = []
    def post(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(status_code=200)
    with pytest.raises(execution.ResearchWorkDeferred, match="maximum charge quote"):
        execution.metered_tool_request(post, cost_key="serper.search", timeout=1)
    assert not sent
    monkeypatch.setenv("RESEARCH_TOOL_MAX_COST_USD_JSON", '{"serper.search": 0.25}')
    execution.metered_tool_request(post, cost_key="serper.search", timeout=1)
    def disconnect(**kwargs):
        raise requests.ConnectionError("ambiguous transport")
    with pytest.raises(requests.ConnectionError):
        execution.metered_tool_request(disconnect, cost_key="serper.search", timeout=1)
    context = execution.research_spending_context()
    assert context["settled_usd"] == .25 and context["reserved_usd"] == .25 and context["available_usd"] == 9.5
