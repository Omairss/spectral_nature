"""Provider liquidity contracts reused from 4f78e927; isolated PostgreSQL, no inference."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import os
import time
import uuid
import pytest
import requests
from services import database, llm_execution as execution, pipeline_store


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
    with database.database_workload("interactive"):
        conn = pipeline_store.open_database_connection()
        with conn:
            pipeline_store._ensure_execution_store(conn)
    return dsn


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

