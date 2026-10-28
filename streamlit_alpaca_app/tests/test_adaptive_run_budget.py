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
    monkeypatch.setenv("AQL_ZOPEDIA_MODEL_TELEMETRY_ENABLED", "1")
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


def test_unaffordable_work_returns_control_immediately_for_economical_completion(store):
    execution.reserve_research_cost(attempt_id="active", maximum_usd=9, category="inference")
    started = time.monotonic()
    with pytest.raises(execution.ResearchBudgetExceeded):
        execution.reserve_research_cost(attempt_id="waiter", maximum_usd=9, category="inference", wait_seconds=2)
    assert time.monotonic() - started < 1
    assert execution.research_spending_context()["available_usd"] == 1


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


@pytest.mark.parametrize("allowance", ["", "0", "0.000001"])
def test_gateway_finishes_current_request_with_economy_and_accounts_for_overrun(store, monkeypatch, allowance):
    from services import llm, aql_zopedia_gateway as gateway
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", allowance)
    monkeypatch.setenv("LLM_POLICY_PROFILE", "baseline")
    config = llm.LLMConfig(provider="deepseek", api_key="contract-key", model="deepseek-v4-pro",
                           base_url="https://api.deepseek.com", thinking_mode="enabled")
    execution._ACCOUNTS.clear()
    pipeline_store.write_execution_state("provider_budget", execution._account_key(config),
        {"available": True, "observed_at": time.time(), "balance_usd": 100, "reservations": {}})
    sent, recorded = [], []
    def post(**kwargs):
        sent.append(kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"model": kwargs["json"]["model"], "usage": {
            "prompt_tokens": 100, "completion_tokens": 100},
            "choices": [{"message": {"content": '{"ok": true}'}}]}).encode()
        return response
    factory = llm.client_from_config
    def make_client(value):
        client = factory(value)
        client.session = SimpleNamespace(post=post)
        return client
    monkeypatch.setattr(llm, "client_from_config", make_client)
    monkeypatch.setattr(pipeline_store, "record_model_call", lambda **kw: recorded.append(kw))
    monkeypatch.setattr(execution.requests, "get", lambda *a, **kw: pytest.fail("No external provider calls in qualification"))
    client = make_client(config)
    deadline = time.monotonic() + 30
    for _ in range(2):
        result = gateway.generate_json_via_aql_zopedia_gateway(llm_client=client, surface="test.current_report",
            purpose="agent.final_answer", call_type="research_grade", system_prompt="Contract input",
            user_prompt="Current supplied input", schema_name="contract",
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
            deadline_monotonic=deadline)
        assert result == {"ok": True}
    assert len(sent) == 2 and all(value["json"]["model"] == "deepseek-flash" for value in sent)
    assert all(0 < value["timeout"] <= 30 for value in sent)
    assert execution.best_effort_required()
    with pytest.raises(execution.ResearchBudgetExceeded, match="Optional paid acquisition"):
        execution.metered_tool_request(post, cost_key="serper.search", timeout=30)
    assert len(sent) == 2
    successes = [event for event in recorded if event["status"] == "success"]
    assert all(event["metadata"]["routing"]["role"] == "best_effort" for event in successes)
    if allowance:
        context = execution.research_spending_context()
        assert context["settled_usd"] > float(allowance)
        assert context["overrun_usd"] == pytest.approx(context["settled_usd"] - float(allowance))
        state = pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True)
        assert len(state["attempts"]) == 2 and all(value["best_effort"] for value in state["attempts"].values())


def test_economical_completion_cannot_spend_nonexistent_provider_credit(store, monkeypatch):
    from services import llm, aql_zopedia_gateway as gateway
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "0")
    config = llm.LLMConfig(provider="deepseek", api_key="empty-test-account", model="deepseek-v4-pro",
                          base_url="https://api.deepseek.com")
    execution._ACCOUNTS.clear()
    pipeline_store.write_execution_state("provider_budget", execution._account_key(config),
        {"available": False, "observed_at": time.time(), "balance_usd": 0, "reservations": {}})
    factory = llm.client_from_config
    def make_client(value):
        client = factory(value)
        client.session = SimpleNamespace(post=lambda **kw: pytest.fail("An empty account must never be charged"))
        return client
    monkeypatch.setattr(llm, "client_from_config", make_client)
    monkeypatch.setattr(pipeline_store, "record_model_call", lambda **kw: None)
    with pytest.raises(llm.LLMProviderUnavailable, match="Provider credit"):
        gateway.generate_json_via_aql_zopedia_gateway(llm_client=factory(config), surface="test.current_report",
            purpose="agent.final_answer", call_type="research_grade", system_prompt="Contract",
            user_prompt="Current input", schema_name="contract", schema={"type": "object"})
    assert execution.research_spending_context()["settled_usd"] == 0


def test_completion_funds_are_protected_before_concurrent_optional_work(store):
    from services.llm import LLMConfig
    config = LLMConfig(provider="deepseek", api_key="test-key", model="deepseek-v4-pro", base_url="https://api.deepseek.com")
    execution.protect_core_research(config=config, work={"company:A": "Current input", "company:B": "Other current input"})
    before = execution.research_spending_context()
    assert before["protected_core_usd"] > 0
    execution.reserve_research_cost(attempt_id="discovery", maximum_usd=before["available_usd"], category="acquisition")
    with pytest.raises(execution.ResearchBudgetExceeded):
        execution.reserve_research_cost(attempt_id="more", maximum_usd=.01, category="acquisition")
    with execution.model_call_context(core_work_id="company:A", routing={"role": "core"}):
        execution.reserve_research_cost(attempt_id="writer", maximum_usd=.1, category="inference")
    during = execution.research_spending_context()
    assert during["available_usd"] == 0
    assert during["protected_core_usd"] == pytest.approx(before["protected_core_usd"] - .1)
    execution.settle_research_cost(attempt_id="writer", actual_usd=.01)
    execution.settle_research_cost(attempt_id="writer", actual_usd=.01)
    after = execution.research_spending_context()
    assert after["available_usd"] == 0
    assert after["protected_core_usd"] == pytest.approx(before["protected_core_usd"] - .01)
    execution.finish_research_economically("Insufficient discretionary headroom", required_microusd=10000)
    assert execution.best_effort_required()
    execution.complete_core_research("company:A")
    assert not execution.best_effort_required()
    released = execution.research_spending_context()
    assert released["available_usd"] > 0
    assert released["protected_core_usd"] > 0
    assert released["reserved_usd"] == before["available_usd"]


def test_exhausted_optional_budget_keeps_core_writing_and_review_on_full_model(store, monkeypatch):
    from services import llm, aql_zopedia_gateway as gateway
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "0.01")
    config = llm.LLMConfig(provider="deepseek", api_key="core-test-account", model="deepseek-v4-pro",
                          base_url="https://api.deepseek.com", thinking_mode="enabled")
    execution._ACCOUNTS.clear()
    pipeline_store.write_execution_state("provider_budget", execution._account_key(config),
        {"available": True, "observed_at": time.time(), "balance_usd": 100, "reservations": {}})
    sent = []
    def post(**kw):
        sent.append(kw["json"]["model"])
        response = requests.Response(); response.status_code = 200
        response._content = json.dumps({"model": config.model,
            "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            "choices": [{"message": {"content": '{"ok": true}'}}]}).encode()
        return response
    client = llm.client_from_config(config); client.session = SimpleNamespace(post=post)
    monkeypatch.setattr(pipeline_store, "record_model_call", lambda **kw: None)
    execution.protect_core_research(config=config, work={"subject": "Retained current inputs"})
    assert execution.best_effort_required()
    with execution.model_call_context(core_work_id="subject"):
        for purpose in ["agent.final_synthesis", "agent.answer_judge"]:
            result = gateway.generate_json_via_aql_zopedia_gateway(llm_client=client, surface="test.research",
                purpose=purpose, call_type="research_grade", system_prompt="Contract", user_prompt="Current input",
                schema_name="contract", schema={"type": "object"})
            assert result == {"ok": True}
    assert sent == [config.model, config.model]
    execution.complete_core_research("subject")
    context = execution.research_spending_context()
    assert context["settled_usd"] > 0 and context["protected_core_usd"] == 0
    assert context["reserved_usd"] == 0


def test_late_usage_receipt_settles_original_run_from_another_recovery_worker(store, monkeypatch):
    from services.llm import LLMConfig
    config = LLMConfig(provider="deepseek", api_key="late-receipt-test", model="deepseek-v4-pro", base_url="https://api.deepseek.com")
    key = execution._account_key(config)
    original_run = execution._run_budget_key()
    pipeline_store.write_execution_state("provider_budget", key,
        {"available": True, "observed_at": time.time(), "balance_usd": 100, "reservations": {}})
    execution._ACCOUNTS.clear()
    def post(**kw):
        response = requests.Response(); response.status_code = 200
        response._content = json.dumps({"model": config.model,
            "usage": {"prompt_tokens": 100, "completion_tokens": 100}}).encode()
        return response
    settle, reconcile = execution.settle_provider_budget, execution.reconcile_provider_reservation
    monkeypatch.setattr(execution, "settle_provider_budget", lambda *a, **kw: None)
    monkeypatch.setattr(execution, "reconcile_provider_reservation", lambda *a, **kw: None)
    assert execution.provider_post(config=config, session=SimpleNamespace(post=post), json={
        "model": config.model, "messages": [{"role": "user", "content": "Current input"}]}, timeout=30).ok
    before = pipeline_store.read_execution_state(*original_run, strict=True)
    assert all(value["status"] == "pending" for value in before["attempts"].values())
    monkeypatch.setattr(execution, "settle_provider_budget", settle)
    monkeypatch.setattr(execution, "reconcile_provider_reservation", reconcile)
    monkeypatch.setenv("PIPELINE_RUN_ID", "different-recovery-owner")
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "0")
    assert pipeline_store.reconcile_provider_budget_reservations()["settled"] == 1
    assert pipeline_store.reconcile_provider_budget_reservations()["settled"] == 0
    after = pipeline_store.read_execution_state(*original_run, strict=True)
    assert all(value["status"] == "settled" for value in after["attempts"].values())
    assert sum(value["charged_microusd"] for value in after["attempts"].values()) > 0
    assert pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True) is None
    wallet = pipeline_store.read_execution_state("provider_budget", key, strict=True)
    assert all(value["status"] == "settled" for value in wallet["reservations"].values())


def test_received_usage_survives_child_cancellation_without_authorizing_new_work(store):
    from services.llm import LLMConfig
    from services.work_scope import WorkScope, WorkCancelled
    config = LLMConfig(provider="deepseek", api_key="cancelled-test", model="deepseek-v4-pro", base_url="https://api.deepseek.com")
    key = execution._account_key(config)
    pipeline_store.write_execution_state("provider_budget", key,
        {"available": True, "observed_at": time.time(), "balance_usd": 100, "reservations": {}})
    execution._ACCOUNTS.clear()
    child = WorkScope(time.monotonic() + 30)
    def post(**kw):
        child.cancel()
        response = requests.Response(); response.status_code = 200
        response._content = json.dumps({"model": config.model,
            "usage": {"prompt_tokens": 100, "completion_tokens": 100}}).encode()
        return response
    root = execution._run_budget_key()
    with pytest.raises(WorkCancelled):
        with child.activate():
            execution.provider_post(config=config, session=SimpleNamespace(post=post), json={
                "model": config.model, "messages": [{"role": "user", "content": "Current input"}]}, timeout=30)
    state = pipeline_store.read_execution_state(*root, strict=True)
    assert all(value["status"] == "settled" for value in state["attempts"].values())
    from services.work_scope import paid_receipt_retention
    with execution.model_call_context(retaining_paid_receipt=True), paid_receipt_retention():
        with pytest.raises(RuntimeError, match="retention cannot start"):
            execution.check_execution_ownership()
