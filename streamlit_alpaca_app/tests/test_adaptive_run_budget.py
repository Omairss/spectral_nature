"""Financial and ownership contracts on real PostgreSQL; no prose grading."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timezone
from decimal import Decimal
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
    monkeypatch.setenv("PIPELINE_JOB_NAME", "news-ingest-and-features")
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "10")
    monkeypatch.setenv("AQL_ZOPEDIA_MODEL_TELEMETRY_ENABLED", "1")
    monkeypatch.setenv("POSTGRES_GLOBAL_ACTIVE_LIMIT", "3")
    monkeypatch.setenv("POSTGRES_GLOBAL_BACKGROUND_LIMIT", "1")
    monkeypatch.setattr(requests.Session, "send", lambda *args, **kwargs:
        pytest.fail("Financial contracts must not send external transport"))
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


@pytest.fixture
def provider_credit(store, monkeypatch):
    from services.llm import LLMConfig
    config = LLMConfig(provider="deepseek", api_key="credit-contract-" + uuid.uuid4().hex,
                       model="deepseek-v4-pro", base_url="https://api.deepseek.com")
    key = execution._account_key(config)
    quote, balance, probes = [Decimal(8)], [8], []
    pipeline_store.write_execution_state("provider_budget", key, {
        "available": True, "observed_at": time.time(), "balance_usd": 8, "reservations": {}})
    monkeypatch.setattr(execution, "_request_maximum_charge", lambda *args: quote[0])
    def get(*args, **kwargs):
        probes.append(time.time())
        response = requests.Response(); response.status_code = 200
        response._content = json.dumps({"is_available": balance[0] > 0,
            "balance_infos": [{"currency": "USD", "total_balance": str(balance[0])}]}).encode()
        return response
    monkeypatch.setattr(execution.requests, "get", get)
    execution.reserve_provider_budget(config, {}, attempt_id="lost", timeout=30)
    execution.settle_provider_budget(config, attempt_id="lost", charge={}, definitely_unbilled=False)
    original_run = execution._run_budget_key()
    monkeypatch.setenv("PIPELINE_RUN_ID", "new-briefing-" + uuid.uuid4().hex)
    quote[0], balance[0] = Decimal(2), 6
    return config, key, quote, balance, probes, original_run


def test_fresh_provider_credit_admits_new_briefing_without_erasing_unknown_old_cost(provider_credit):
    config, key, quote, balance, probes, original_run = provider_credit
    execution.reserve_provider_budget(config, {}, attempt_id="current", timeout=10)
    wallet = pipeline_store.read_execution_state("provider_budget", key, strict=True)
    lost = wallet["reservations"]["lost"]
    assert len(probes) == 1
    assert lost["status"] == "unknown" and lost["amount_usd"] == 8
    assert lost["liquidity_observed_at"] > lost["finished_at"]
    assert wallet["unreconciled_at_balance_check"]["maximum_usd"] == 8
    old = pipeline_store.read_execution_state(*original_run, strict=True)
    assert old["attempts"]["lost"]["status"] == "unknown"
    assert old["attempts"]["lost"]["maximum_microusd"] == 8_000_000
    assert execution.research_spending_context()["reserved_usd"] == 2


def test_unknown_hold_is_not_released_when_balance_refresh_fails(provider_credit, monkeypatch):
    config, key, quote, balance, probes, original_run = provider_credit
    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("Balance endpoint unavailable")
    monkeypatch.setattr(execution.requests, "get", unavailable)
    with pytest.raises(requests.ConnectionError):
        execution.reserve_provider_budget(config, {}, attempt_id="current", timeout=10)
    wallet = pipeline_store.read_execution_state("provider_budget", key, strict=True)
    assert "liquidity_observed_at" not in wallet["reservations"]["lost"]
    assert "current" not in wallet["reservations"]
    assert pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True) is None


def test_actual_empty_account_still_refuses_new_request_and_retains_unknown_cost(provider_credit):
    from services.llm import LLMProviderUnavailable
    config, key, quote, balance, probes, original_run = provider_credit
    balance[0] = 0
    with pytest.raises(LLMProviderUnavailable, match="Provider credit"):
        execution.reserve_provider_budget(config, {}, attempt_id="current", timeout=10)
    wallet = pipeline_store.read_execution_state("provider_budget", key, strict=True)
    assert wallet["reservations"]["lost"]["status"] == "unknown"
    assert wallet["reservations"]["lost"]["amount_usd"] == 8
    assert "current" not in wallet["reservations"]


def test_expired_worker_hold_becomes_uncertain_but_live_request_keeps_its_credit(provider_credit):
    from services.llm import LLMProviderUnavailable
    config, key, quote, balance, probes, original_run = provider_credit
    def seed(state):
        state["reservations"]["lost"].update(status="pending", expires_at=time.time()-1)
        state["reservations"]["lost"].pop("finished_at")
        state["reservations"]["lost"].pop("deadline_at")
        state["reservations"]["active"] = {"status": "pending", "amount_usd": 3,
                                               "deadline_at": time.time()+100}
        return state
    pipeline_store.mutate_execution_state("provider_budget", key, seed)
    balance[0] = 4
    with pytest.raises(LLMProviderUnavailable, match="Provider credit"):
        execution.reserve_provider_budget(config, {}, attempt_id="current", timeout=10)
    wallet = pipeline_store.read_execution_state("provider_budget", key, strict=True)
    assert wallet["reservations"]["lost"]["status"] == "unknown"
    assert wallet["reservations"]["lost"]["uncertainty_reason"] == "local_request_deadline_elapsed"
    assert wallet["reservations"]["active"]["status"] == "pending"
    assert "liquidity_observed_at" not in wallet["reservations"]["active"]
    execution.settle_provider_budget(config, attempt_id="active", charge={}, definitely_unbilled=True)
    execution.reserve_provider_budget(config, {}, attempt_id="current", timeout=10)


def test_response_lost_during_balance_probe_remains_held_until_a_later_probe(provider_credit, monkeypatch):
    config, key, quote, balance, probes, original_run = provider_credit
    get = execution.requests.get
    def end_during_probe(*args, **kwargs):
        def update(state):
            state["reservations"]["overlapping"] = {"status": "unknown", "amount_usd": 3,
                                                          "finished_at": time.time()}
            return state
        pipeline_store.mutate_execution_state("provider_budget", key, update)
        return get(*args, **kwargs)
    monkeypatch.setattr(execution.requests, "get", end_during_probe)
    execution.reserve_provider_budget(config, {}, attempt_id="current", timeout=10)
    wallet = pipeline_store.read_execution_state("provider_budget", key, strict=True)
    assert "liquidity_observed_at" not in wallet["reservations"]["overlapping"]
    assert wallet["reservations"]["overlapping"]["amount_usd"] == 3


def test_late_usage_after_credit_refresh_updates_original_cost_without_recreating_debits(provider_credit):
    config, key, quote, balance, probes, original_run = provider_credit
    execution.reserve_provider_budget(config, {}, attempt_id="current", timeout=10)
    receipt = {"run": original_run, "account": ("provider_budget", key)}
    for charge in ({"status": "estimated", "usd_max": 1.5}, {}, {"status": "estimated", "usd_max": 1.5}):
        execution.settle_provider_budget(config, attempt_id="lost", charge=charge,
                                        definitely_unbilled=False, reservation=receipt)
    wallet = pipeline_store.read_execution_state("provider_budget", key, strict=True)
    assert wallet["reservations"]["lost"]["status"] == "settled"
    assert wallet["reservations"]["lost"]["amount_usd"] == 1.5
    assert "liquidity_observed_at" not in wallet["reservations"]["lost"]
    finished = wallet["reservations"]["lost"]["finished_at"]
    execution.settle_provider_budget(config, attempt_id="lost", charge={"status": "estimated", "usd_max": 1.5},
                                    definitely_unbilled=False, reservation=receipt)
    wallet = pipeline_store.read_execution_state("provider_budget", key, strict=True)
    assert wallet["reservations"]["lost"]["finished_at"] == finished
    pipeline_store.mutate_execution_state("provider_budget", key, lambda state: {**state, "observed_at": 0})
    quote[0], balance[0] = Decimal(1), 4.5
    execution.reserve_provider_budget(config, {}, attempt_id="next", timeout=10)
    execution.settle_provider_budget(config, attempt_id="lost", charge={"status": "estimated", "usd_max": 1.5},
                                    definitely_unbilled=False, reservation=receipt)
    wallet = pipeline_store.read_execution_state("provider_budget", key, strict=True)
    assert "lost" not in wallet["reservations"]
    old = pipeline_store.read_execution_state(*original_run, strict=True)
    assert old["attempts"]["lost"]["charged_microusd"] == 1_500_000
    assert execution.research_spending_context()["reserved_usd"] == 3


def test_concurrent_new_briefings_cannot_spend_the_same_refreshed_credit(provider_credit, monkeypatch):
    from services.llm import LLMProviderUnavailable
    config, key, quote, balance, probes, original_run = provider_credit
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "100")
    quote[0], balance[0] = Decimal(1), 4
    def reserve(index):
        try:
            execution.reserve_provider_budget(config, {}, attempt_id="new-"+str(index), timeout=60)
            return index
        except LLMProviderUnavailable:
            return None
    with ThreadPoolExecutor(max_workers=16) as pool:
        admitted = [index for index in pool.map(reserve, range(16)) if index is not None]
    assert len(admitted) == 4
    assert len(probes) == 1
    assert execution.research_spending_context()["reserved_usd"] == 4


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


def test_unbudgeted_queued_user_research_preserves_its_explicit_model(store, monkeypatch):
    monkeypatch.delenv("RESEARCH_RUN_BUDGET_USD", raising=False)
    monkeypatch.setenv("PIPELINE_JOB_NAME", "research-run-execute")
    assert not execution.best_effort_required()


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
    with pytest.raises(RuntimeError, match="retention cannot start"):
        execution.retain_paid_result(execution.check_execution_ownership)


def test_optional_full_research_must_fit_original_soft_target(store, monkeypatch):
    from services.llm import LLMConfig
    config = LLMConfig(provider="deepseek", api_key="test-" + uuid.uuid4().hex, model="deepseek-v4-pro", base_url="https://api.deepseek.com")
    pipeline_store.write_execution_state("provider_budget", execution._account_key(config), {
        "available": True, "balance_usd": 1000, "observed_at": time.time(), "reservations": {}})
    monkeypatch.setenv("RESEARCH_RUN_SOFT_OVERRUN_FRACTION", "0.1")
    monkeypatch.setattr(execution, "_request_maximum_charge", lambda *a: 3)
    assert execution.protect_core_research(config=config, work={"current-news": "Current sources"}, stages=1)
    monkeypatch.setattr(execution, "_request_maximum_charge", lambda *a: 8)
    assert execution.protect_core_research(config=config, work={"company:A": "Fresh sources"}, stages=1, required=False)
    # A replica's changed environment must not turn 10% into a new allowance.
    monkeypatch.setenv("RESEARCH_RUN_SOFT_OVERRUN_FRACTION", "10")
    assert not execution.protect_core_research(config=config, work={"company:B": "Fresh sources"}, stages=1, required=False)
    execution.complete_core_research("company:A")
    assert execution.protect_core_research(config=config, work={"company:B": "Fresh sources"}, stages=1, required=False)
    # A released subject cannot restart full research without funding it again.
    assert not execution.protect_core_research(config=config, work={"company:A": "Fresh sources"}, stages=1, required=False)
    with execution.model_call_context(optional_core_unfunded=True):
        assert execution.best_effort_required() and not execution.funded_core_research()
        assert not execution.protect_core_research(config=config, work={"nested": "Fresh sources"})


def test_larger_target_can_fund_more_complete_investigations(store, monkeypatch):
    from services.llm import LLMConfig
    config = LLMConfig(provider="deepseek", api_key="test-" + uuid.uuid4().hex, model="deepseek-v4-pro", base_url="https://api.deepseek.com")
    pipeline_store.write_execution_state("provider_budget", execution._account_key(config), {
        "available": True, "balance_usd": 1000, "observed_at": time.time(), "reservations": {}})
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "1000")
    monkeypatch.setattr(execution, "_request_maximum_charge", lambda *a: 10)
    for index in range(30):
        assert execution.protect_core_research(config=config, work={str(index): "Full supplied context"}, stages=3, required=False)
    state = pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True)
    assert sum(value["remaining_microusd"] for value in state["core_obligations"].values()) == 900_000_000


def test_event_margin_is_atomic_bounded_and_frozen_across_replica_config_changes(store, monkeypatch):
    monkeypatch.setenv('RESEARCH_BUDGET_MODE', 'event')
    monkeypatch.setenv('RESEARCH_EVENT_MARGIN_FRACTION', '0.5')
    execution.reserve_research_cost(attempt_id='base', maximum_usd=10, category='acquisition')
    def admit(index):
        with execution.event_research_context({'work_id':f'event-{index}', 'evidence_ids':[f'source-{index}'],
                                               'reason':'Retained model nomination'}):
            try:
                execution.reserve_research_cost(attempt_id=f'event-{index}', maximum_usd=1, category='acquisition')
                return True
            except execution.ResearchBudgetExceeded:
                return False
    with ThreadPoolExecutor(max_workers=16) as pool:
        assert sum(pool.map(admit, range(16))) == 5
    state = pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True)
    assert state['base_microusd'] == 10_000_000
    assert state['cap_microusd'] == state['event_ceiling_microusd'] == 15_000_000
    assert sum(v['maximum_microusd'] for v in state['attempts'].values()) == 15_000_000
    assert len(state['event_extensions']) == 5
    monkeypatch.setenv('RESEARCH_EVENT_MARGIN_FRACTION', '500')
    monkeypatch.setenv('RESEARCH_RUN_BUDGET_USD', '1000')
    assert not admit('retry')
    assert execution.research_spending_context()['event_ceiling_usd'] == 15
    execution.settle_research_cost(attempt_id='base', actual_usd=4.5)
    # Refunded marginal funds are not permission for unrelated ordinary work.
    with pytest.raises(execution.ResearchBudgetExceeded):
        execution.reserve_research_cost(attempt_id='unrelated', maximum_usd=1, category='acquisition')
    assert admit('useful-current-work')


def test_quiet_or_zero_budget_does_not_create_a_marginal_allowance(store, monkeypatch):
    monkeypatch.setenv('RESEARCH_BUDGET_MODE', 'event')
    monkeypatch.setenv('RESEARCH_EVENT_MARGIN_FRACTION', '0.5')
    execution.reserve_research_cost(attempt_id='quiet-current-report', maximum_usd=.1, category='inference')
    execution.settle_research_cost(attempt_id='quiet-current-report', actual_usd=.02)
    state = pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True)
    assert state['cap_microusd'] == 10_000_000 and state['event_extensions'] == []
    monkeypatch.setenv('PIPELINE_RUN_ID', 'zero-event-budget-' + uuid.uuid4().hex)
    monkeypatch.setenv('RESEARCH_RUN_BUDGET_USD', '0')
    with execution.event_research_context({'work_id':'event', 'evidence_ids':['source'], 'reason':'Useful current question'}):
        with pytest.raises(execution.ResearchBudgetExceeded):
            execution.reserve_research_cost(attempt_id='not-free', maximum_usd=.01, category='acquisition')
        assert execution.best_effort_required()


def test_real_budget_admits_tokenized_azure_deployment_and_settles_the_underlying_model(store):
    from services.llm import LLMConfig, AzureOpenAIEmbeddingClient
    calls = []
    def post(**kwargs):
        calls.append(kwargs)
        token_count = sum(map(len, kwargs['json']['input']))
        response = requests.Response(); response.status_code = 200
        response._content = json.dumps({'model':'text-embedding-3-small',
            'data':[{'index':i,'embedding':[1.,0.,0.]} for i in range(len(kwargs['json']['input']))],
            'usage':{'prompt_tokens':token_count,'total_tokens':token_count}}).encode()
        return response
    config = LLMConfig(provider='azure_openai', api_key='fake', model='arbitrary-deployment-alias',
        base_url='https://example.invalid/openai/v1', embedding_deployment='arbitrary-deployment-alias',
        embedding_model='text-embedding-3-small', embedding_dimensions=3)
    model = AzureOpenAIEmbeddingClient(config, session=SimpleNamespace(post=post))
    assert len(model.generate_embeddings(['Current evidence retained completely.', 'Another source.'])) == 2
    assert calls[0]['json']['model'] == 'arbitrary-deployment-alias'
    state = pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True)
    assert len(state['attempts']) == 1
    assert next(iter(state['attempts'].values()))['status'] == 'settled'
    assert not state.get('invalid_quote')
    payload = {'model':'arbitrary-deployment-alias', 'input':[[11,22,33],[44,55]]}
    assert execution._request_maximum_charge(config,payload) == Decimal('0.00000011')


def test_audio_uses_character_prices_and_a_complete_billing_receipt(store):
    from services.elevenlabs_tts import ElevenLabsTTSConfig
    config = ElevenLabsTTSConfig(api_key='fake',voice_id='voice',model_id='eleven_multilingual_v2',output_format='mp3')
    response=requests.Response();response.status_code=200;response._content=b'complete-audio-bytes'
    execution.provider_post(config=config,session=SimpleNamespace(post=lambda **kw:response),operation='audio',
        json={'model_id':config.model_id,'text':'x'*1000},url='https://example.invalid/audio',timeout=5)
    state = pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True)
    attempt = next(iter(state['attempts'].values()))
    assert attempt['status']=='settled' and attempt['charged_microusd']==80_000


def test_publication_metadata_uses_reserved_capacity_while_background_sql_is_occupied(store, monkeypatch):
    from datetime import datetime, timezone
    from pipeline.jobs import main as jobs
    monkeypatch.setenv('POSTGRES_BACKGROUND_ACQUIRE_TIMEOUT_SECONDS','1')
    execution_name='publication-capacity-'+uuid.uuid4().hex
    monkeypatch.setenv('CONTAINER_APP_JOB_EXECUTION_NAME',execution_name)
    monkeypatch.setenv('LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE','public_company_research')
    ctx=jobs.JobContext(name='news-ingest-and-features',run_id=os.environ['PIPELINE_RUN_ID'],
        asof=datetime.now(timezone.utc),universe_version='qualification')
    namespace='pipeline_execution:'+pipeline_store.dataset_namespace()
    key=ctx.name+':'+execution_name
    with database.database_workload('interactive'):
        assert pipeline_store.write_execution_state(namespace,key,{'run_id':ctx.run_id})
    occupied=database.open_database_session(store,workload='background')
    try:
        with occupied.cursor() as cursor:cursor.execute('SELECT 1')
        with database.database_workload('background'):
            with pytest.raises(RuntimeError,match='accounting store is unavailable'):
                pipeline_store.mutate_execution_state(namespace,key,lambda state:state)
            monkeypatch.setattr(jobs,'_upload_frame',lambda *args:'native.parquet')
            monkeypatch.setattr(jobs,'_upload_manifest',lambda *args,**kwargs:{'dataset_version_id':'native-version'})
            jobs._persist_dataset('edgar_filings',pd.DataFrame([{'form':'10-Q'}]),ctx,None)
        assert ctx.degraded_components=={}
        with database.database_workload('interactive'):
            saved=pipeline_store.read_execution_state(namespace,key,strict=True)
        assert saved['published_datasets']=={'edgar_filings':'native-version'}
    finally:
        occupied.close()


def test_event_capacity_is_available_not_compulsory_and_survives_changed_retry_settings(store, monkeypatch):
    monkeypatch.setenv('RESEARCH_BUDGET_MODE','event')
    monkeypatch.setenv('RESEARCH_EVENT_MARGIN_FRACTION','0.5')
    assert execution.event_investigation_capacity(4)==6
    execution.reserve_research_cost(attempt_id='nomination',maximum_usd=.01,category='inference')
    monkeypatch.setenv('RESEARCH_EVENT_MARGIN_FRACTION','100')
    assert execution.event_investigation_capacity(4)==6
    state=pipeline_store.read_execution_state(*execution._run_budget_key(),strict=True)
    assert state['cap_microusd']==10_000_000 and state['event_extensions']==[]


def test_completed_investigations_survive_a_later_empty_nomination_and_scope_isolation(store):
    namespace = 'research-history-' + uuid.uuid4().hex
    def retain(scope, identity, subject, task, payload):
        return pipeline_store.retain_research_checkpoint(namespace=scope, input_key=identity,
            subject=subject, task=task, artifact={'artifact_id': identity, 'subject': subject,
                'task': task, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'result': {'payload': payload}})
    retain(namespace, 'completed', 'event:one', 'source_event_research', {'analysis': 'Retained full research'})
    retain(namespace, 'quiet', 'attention:source_event_nomination', 'source_event_nomination', {'events': []})
    retain(namespace + ':private', 'other-owner', 'event:two', 'source_event_research', {'analysis': 'Private'})
    rows = pipeline_store.list_research_artifacts(namespace=namespace, task='source_event_research', limit=1)
    assert [r['artifact_id'] for r in rows] == ['completed']
    assert rows[0]['result']['payload'] == {'analysis': 'Retained full research'}
    assert len(pipeline_store.list_research_artifacts(namespace=namespace)) == 2


@pytest.fixture
def core_wallet(store, monkeypatch):
    from services.llm import LLMConfig
    config = LLMConfig(provider="deepseek", api_key="core-" + uuid.uuid4().hex,
                      model="deepseek-v4-pro", base_url="https://api.deepseek.com")
    account = ("provider_budget", execution._account_key(config))
    quote = [Decimal(1)]
    monkeypatch.setattr(execution, "_request_maximum_charge", lambda *args: quote[0])
    pipeline_store.write_execution_state(*account, {
        "available": True, "balance_usd": 10, "observed_at": time.time(), "reservations": {}})
    return config, account, quote


def test_future_core_is_shared_between_runs_and_transfers_without_double_holds(core_wallet, monkeypatch):
    config, account, quote = core_wallet
    first = execution._run_budget_key()
    assert execution.protect_core_research(config=config, work={"first": "input"}, stages=2)
    monkeypatch.setenv("PIPELINE_RUN_ID", first[1] + "-other")
    assert execution.protect_core_research(config=config, work={"other": "input"}, stages=2)
    monkeypatch.setenv("PIPELINE_RUN_ID", first[1] + "-optional")
    quote[0] = Decimal(7)
    with pytest.raises(execution.ResearchBudgetExceeded, match="protected for current core"):
        execution.reserve_provider_budget(config, {}, attempt_id="rejected", timeout=10)
    assert pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True) is None
    wallet = pipeline_store.read_execution_state(*account, strict=True)
    assert execution._future_core_microusd(wallet) == 4_000_000 and wallet["reservations"] == {}

    monkeypatch.setenv("PIPELINE_RUN_ID", first[1])
    quote[0] = Decimal(1)
    with execution.model_call_context(core_work_id="first", routing={"role": "core"}):
        execution.reserve_provider_budget(config, {}, attempt_id="core-call", timeout=10)
    wallet = pipeline_store.read_execution_state(*account, strict=True)
    assert execution._future_core_microusd(wallet) == 3_000_000
    assert execution._provider_unreserved_microusd(wallet) == 9_000_000
    receipt = {"run": first, "account": account}
    for _ in range(2):
        execution.settle_provider_budget(config, attempt_id="core-call", charge={"status": "estimated", "usd_max": .2},
                                         definitely_unbilled=False, reservation=receipt)
    wallet = pipeline_store.read_execution_state(*account, strict=True)
    assert execution._future_core_microusd(wallet) == 3_800_000
    assert execution._provider_unreserved_microusd(wallet) == 9_800_000
    execution.complete_core_research("first")
    assert execution._future_core_microusd(pipeline_store.read_execution_state(*account, strict=True)) == 2_000_000
    monkeypatch.setenv("PIPELINE_RUN_ID", first[1] + "-optional")
    quote[0] = Decimal(7)
    execution.reserve_provider_budget(config, {}, attempt_id="now-affordable", timeout=10)


def test_optional_completion_funding_is_atomic_across_concurrent_runs(core_wallet):
    config, account, quote = core_wallet
    pipeline_store.mutate_execution_state(*account, lambda state: {**state, "balance_usd": 6})
    assert execution.protect_core_research(config=config, work={"primary": "input"}, stages=2)
    root = execution._run_budget_key()[1]
    def protect(index):
        with execution.model_call_context(root_run_id=root + "-" + str(index)):
            return execution.protect_core_research(config=config, work={"optional": "input"}, stages=1, required=False)
    with ThreadPoolExecutor(max_workers=16) as pool:
        admitted = list(pool.map(protect, range(16)))
    assert sum(admitted) == 4
    wallet = pipeline_store.read_execution_state(*account, strict=True)
    assert execution._future_core_microusd(wallet) == 6_000_000
    assert wallet["reservations"] == {}


def test_expired_future_work_releases_only_unspent_funding_and_late_usage_stays_owned(core_wallet, monkeypatch):
    from services.work_scope import WorkCancelled
    config, account, quote = core_wallet
    root = execution._run_budget_key()
    assert execution.protect_core_research(config=config, work={"primary": "input"}, stages=2)
    with execution.model_call_context(core_work_id="primary", routing={"role": "core"}):
        execution.reserve_provider_budget(config, {}, attempt_id="sent", timeout=30)
    def expire(states):
        obligation = states[root]["core_obligations"]["primary"]
        obligation["deadline_at"] = time.time() - 1
        execution._sync_core_account(states, root, account, "primary")
        return states
    pipeline_store.mutate_execution_states([root, account], expire)
    wallet = pipeline_store.read_execution_state(*account, strict=True)
    assert wallet["future_core"] == {} and wallet["reservations"]["sent"]["status"] == "pending"
    with execution.model_call_context(core_work_id="primary", routing={"role": "core"}):
        with pytest.raises(WorkCancelled):
            execution.reserve_provider_budget(config, {}, attempt_id="expired-new-call", timeout=10)
    monkeypatch.setenv("PIPELINE_RUN_ID", root[1] + "-recovery")
    execution.settle_provider_budget(config, attempt_id="sent", charge={"status": "estimated", "usd_max": .2},
        definitely_unbilled=False, reservation={"run": root, "account": account})
    wallet = pipeline_store.read_execution_state(*account, strict=True)
    assert wallet["future_core"] == {} and wallet["reservations"]["sent"]["status"] == "settled"
    assert pipeline_store.read_execution_state(*root, strict=True)["attempts"]["sent"]["charged_microusd"] == 200_000


def test_underfunded_future_quotes_stop_exploration_without_blocking_affordable_core(core_wallet, monkeypatch):
    config, account, quote = core_wallet
    pipeline_store.mutate_execution_state(*account, lambda state: {**state, "balance_usd": 1})
    assert execution.protect_core_research(config=config, work={"primary": "input"}, stages=2)
    assert pipeline_store.read_execution_state(*account, strict=True)["future_core_shortfall_microusd"] == 1_000_000
    quote[0] = Decimal("0.5")
    with pytest.raises(execution.ResearchBudgetExceeded):
        execution.reserve_provider_budget(config, {}, attempt_id="optional", timeout=10)
    with execution.model_call_context(core_work_id="primary", routing={"role": "core"}):
        execution.reserve_provider_budget(config, {}, attempt_id="current-core", timeout=10)
    wallet = pipeline_store.read_execution_state(*account, strict=True)
    assert wallet["future_core_shortfall_microusd"] == 1_000_000
    assert wallet["reservations"]["current-core"]["amount_usd"] == .5
    monkeypatch.setenv("PIPELINE_RUN_ID", execution._run_budget_key()[1] + "-zero")
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "0")
    quote[0] = Decimal("0.1")
    with execution.model_call_context(completion_priority=True, routing={"role": "best_effort"}):
        execution.reserve_provider_budget(config, {}, attempt_id="current-economical", timeout=10)
    assert pipeline_store.read_execution_state(*account, strict=True)["reservations"]["current-economical"]["amount_usd"] == .1


def test_native_response_transport_keeps_its_generation_price_and_usage_units(store):
    from services.llm import LLMConfig
    config = LLMConfig(provider="deepseek", api_key="native-" + uuid.uuid4().hex,
                      model="deepseek-v4-pro", base_url="https://api.deepseek.com")
    payload = {"model": config.model, "instructions": "Serialize the retained result.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Retained result " * 100}]}],
        "max_output_tokens": 2000}
    assert execution._pricing_model(config, payload, "responses") == config.model
    assert execution.request_context_budget(config, payload)["reserved_output_tokens"] == 2000
    assert execution._request_maximum_charge(config, payload, "responses") > 0
    usage = {"input_tokens": 100, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 50}}
    normalized = execution.normalize_provider_usage(usage)
    assert normalized["prompt_tokens"] == 100 and normalized["completion_tokens"] == 20
    assert normalized["input_tokens_details"] == normalized["prompt_tokens_details"] == {"cached_tokens": 50}
    started = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    assert execution.estimated_charge(provider=config.provider, model=config.model, usage=usage, started_at=started) == execution.estimated_charge(
        provider=config.provider, model=config.model, usage={"prompt_tokens": 100, "completion_tokens": 20,
            "prompt_cache_hit_tokens": 50}, started_at=started)


def test_ended_execution_releases_only_future_priority_and_retry_keeps_original_funding(core_wallet):
    config, account, quote = core_wallet
    run = execution._run_budget_key()
    execution.protect_core_research(config=config, work={"primary": "input"}, stages=2)
    original = pipeline_store.read_execution_state(*run, strict=True)["core_obligations"]["primary"]
    with execution.model_call_context(core_work_id="primary", routing={"role": "core"}):
        execution.reserve_provider_budget(config, {}, attempt_id="sent", timeout=30)
    execution.interrupt_core_research("Failed")
    wallet = pipeline_store.read_execution_state(*account, strict=True)
    assert wallet["future_core"] == {} and wallet["reservations"]["sent"]["status"] == "pending"
    execution.settle_provider_budget(config, attempt_id="sent", charge={"status": "estimated", "usd_max": .2},
        definitely_unbilled=False, reservation={"run": run, "account": account})
    assert pipeline_store.read_execution_state(*account, strict=True)["future_core"] == {}
    execution.protect_core_research(config=config, work={"primary": "input"}, stages=2)
    resumed = pipeline_store.read_execution_state(*run, strict=True)["core_obligations"]["primary"]
    assert resumed["remaining_microusd"] == 1_800_000
    assert resumed["deadline_at"] == original["deadline_at"] and resumed["generation"] == original["generation"]
    assert execution._future_core_microusd(pipeline_store.read_execution_state(*account, strict=True)) == 1_800_000
    execution.complete_core_research("primary")
    execution.protect_core_research(config=config, work={"primary": "new input"}, stages=2)
    regenerated = pipeline_store.read_execution_state(*run, strict=True)["core_obligations"]["primary"]
    assert regenerated["generation"] != original["generation"]
    execution.settle_provider_budget(config, attempt_id="sent", charge={"status": "estimated", "usd_max": .5},
        definitely_unbilled=False, reservation={"run": run, "account": account})
    assert pipeline_store.read_execution_state(*run, strict=True)["core_obligations"]["primary"]["remaining_microusd"] == 2_000_000


def test_evidence_navigation_cannot_consume_the_answer_reserve(core_wallet, monkeypatch):
    from services import aql_zopedia_gateway as gateway
    from services.zopedia_agent import _run_evidence_model_step
    from data_access.query_service import QueryService
    from data_access.layer import DataAccessLayer
    config, account, quote = core_wallet
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "2.1")
    execution.protect_core_research(config=config, work={"primary": "input"}, stages=2)
    quote[0] = Decimal(".2")
    calls = []
    class Client:
        def generate_json(self, **kwargs):
            calls.append((dict(execution._CALL.get()), kwargs["schema"]))
            attempt = "protocol-" + str(len(calls))
            execution.reserve_provider_budget(config, {}, attempt_id=attempt, timeout=10)
            execution.settle_provider_budget(config, attempt_id=attempt, charge={"status":"estimated", "usd_max": .1},
                definitely_unbilled=False)
            return {"decision": {"action": "answer", "answer": {"value": "result"}}}
    client = Client(); client.config = config
    monkeypatch.setattr(gateway, "_record_gateway_call", lambda **kwargs: None)
    with execution.model_call_context(core_work_id="primary"):
        result = _run_evidence_model_step(llm_client=client, service=QueryService(DataAccessLayer(materialized_only=True)), tool_calls=[], context_state={},
            prompt=lambda _: "Retained current input", system_prompt="Complete the result", schema_name="protocol",
            schema={"type":"object", "properties":{"value":{"type":"string"}}, "required":["value"]},
            surface="zopedia_agent", purpose="agent.final_synthesis", deadline_monotonic=time.monotonic()+10,
            metadata={}, progress_callback=None)
    assert result == {"value":"result"}
    assert len(calls) == 2 and calls[0][0]["routing"]["role"] == "research"
    assert calls[1][0]["routing"]["role"] == "core"
    assert calls[1][1]["properties"]["decision"]["properties"]["action"]["enum"] == ["answer"]
    state = pipeline_store.read_execution_state(*execution._run_budget_key(), strict=True)
    assert len(state["attempts"]) == 1 and state["core_obligations"]["primary"]["remaining_microusd"] == 1_900_000
