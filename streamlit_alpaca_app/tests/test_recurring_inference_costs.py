"""Operational ownership, freshness and accounting; no model-quality verdicts."""
from dataclasses import fields
import json
import threading
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from services import llm, llm_execution as execution, pipeline_store
from services.aql import business_model_stack as company


@pytest.fixture
def durable_store(monkeypatch):
    values = {}
    lock = threading.RLock()
    monkeypatch.setenv("APP_TRACK", "development")
    monkeypatch.setenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE", "cost-contracts")
    monkeypatch.setattr(llm, "load_prompt_overrides", lambda: None)
    monkeypatch.setattr(llm, "list_narrative_prompts", lambda: [])
    monkeypatch.setattr(llm, "list_config_params", lambda: [])
    monkeypatch.setattr(execution, "latest_research_artifact", lambda **kwargs: None)
    monkeypatch.setattr(company, "latest_research_artifact", lambda **kwargs: None)
    monkeypatch.setattr(pipeline_store, "read_execution_state", lambda ns, key, **kw: values.get((ns, key)))
    def write(ns, key, value, immutable=False):
        with lock:
            if not immutable or (ns, key) not in values:
                values[ns, key] = json.loads(json.dumps(value, default=str))
        return True
    def mutate(ns, key, transform):
        with lock:
            value = transform(json.loads(json.dumps(values.get((ns, key), {}))))
            write(ns, key, value)
            return value
    def claim(ns, key, *, owner, seconds):
        with lock:
            if values.get((ns, key), {}).get("expires_at", 0) > time.time():
                return False
            write(ns, key, {"owner": owner, "expires_at": time.time()+seconds})
            return True
    def renew(ns, key, *, owner, seconds):
        with lock:
            value = values.get((ns, key), {})
            if value.get("owner") != owner or value.get("expires_at", 0) <= time.time():
                return False
            value["expires_at"] = time.time()+seconds
            return True
    def release(ns, key, *, owner):
        with lock:
            if values.get((ns, key), {}).get("owner") == owner:
                values.pop((ns, key))
    monkeypatch.setattr(pipeline_store, "write_execution_state", write)
    monkeypatch.setattr(pipeline_store, "mutate_execution_state", mutate)
    monkeypatch.setattr(pipeline_store, "claim_execution_state", claim)
    monkeypatch.setattr(pipeline_store, "renew_execution_state", renew)
    monkeypatch.setattr(pipeline_store, "release_execution_state", release)
    return values


def result_for(kwargs):
    values = {field.name: [] if str(field.type).startswith("list") else
              {} if str(field.type).startswith("dict") else "" for field in fields(company.TickerBusinessModelStackResult)}
    values.update(symbol=kwargs["symbol"], company_name="Retained company", status="ready",
                  confidence="medium", business_story_markdown="Retained original analysis.",
                  run_id=kwargs["run_id"], asof_time_utc=kwargs["asof_time_utc"])
    return company.TickerBusinessModelStackResult(**values)


def model():
    return SimpleNamespace(config=llm.LLMConfig(provider="deepseek", api_key="not-a-real-key",
        model="deepseek-v4-pro", base_url="https://api.deepseek.com", thinking_mode="enabled"))


def test_company_refresh_reuses_across_runs_but_reacts_to_evidence_and_deadline(durable_store, monkeypatch):
    calls = []
    @company._checkpoint_company_research
    def work(**kwargs):
        calls.append(kwargs)
        return result_for(kwargs)
    kwargs = dict(symbol="EXAMPLE", llm_client=model(), run_id="first",
                  asof_time_utc="2026-10-26T14:00:00+00:00")
    original = work(**kwargs)
    reused = work(**{**kwargs, "run_id": "second", "asof_time_utc": "2026-10-26T16:00:00+00:00"})
    assert reused.run_id == original.run_id and reused.asof_time_utc == original.asof_time_utc
    assert reused.business_story_markdown == original.business_story_markdown and len(calls) == 1
    news = pd.DataFrame([dict(symbols=["EXAMPLE"], headline="New evidence", published_at="2026-10-26T16:00Z")])
    work(**{**kwargs, "news_frame": news})
    assert len(calls) == 2
    state = next(value for (ns, key), value in durable_store.items() if ns.endswith(":company_workflow"))
    state["started_at"] -= 86401
    work(**{**kwargs, "news_frame": news})
    assert len(calls) == 3
    work(**{**kwargs, "news_frame": news, "write_policy": "none"})
    assert len(calls) == 4


def test_completed_pages_do_not_invalidate_their_own_reuse_or_get_republished(durable_store):
    calls = []
    page = dict(page_id="company-memory", page_type="ticker", title="EXAMPLE Business Memory",
                summary="Retained facts", body_markdown="Retained facts", metadata={"symbol": "EXAMPLE"})
    prepared, _ = company.prepare_zopedia_pages([page])
    page = prepared.iloc[0].to_dict()
    @company._checkpoint_company_research
    def work(**kwargs):
        calls.append(kwargs)
        result = result_for(kwargs)
        result.proposed_pages = [page]
        return result
    kwargs = dict(symbol="EXAMPLE", llm_client=model(), run_id="first", asof_time_utc="2026-10-26T14:00Z")
    work(**kwargs)
    reused = work(**{**kwargs, "zopedia_pages_frame": pd.DataFrame([page])})
    assert len(calls) == 1 and reused.proposed_pages == []
    work(**{**kwargs, "zopedia_pages_frame": pd.DataFrame([{**page, "body_markdown": "An external edit"}])})
    assert len(calls) == 2


def test_incomplete_company_resumes_same_work_identity_and_clock(durable_store):
    identities = []
    @company._checkpoint_company_research
    def work(**kwargs):
        identities.append((execution._CALL.get()["work_instance"], kwargs["asof_time_utc"]))
        execution.mark_research_work_incomplete()
        return result_for(kwargs)
    kwargs = dict(symbol="EXAMPLE", llm_client=model(), run_id="first", asof_time_utc="2026-10-26T14:00Z")
    work(**kwargs)
    work(**{**kwargs, "run_id": "next-execution", "asof_time_utc": "2026-10-26T16:00Z"})
    assert len(identities) == 2 and identities[0] == identities[1]


def test_batch_deadline_still_allows_reuse_and_defers_new_paid_work(durable_store):
    calls = []
    @company._checkpoint_company_research
    def work(**kwargs):
        calls.append(kwargs)
        return result_for(kwargs)
    kwargs = dict(symbol="EXAMPLE", llm_client=model(), run_id="first", asof_time_utc="2026-10-26T14:00Z")
    work(**kwargs)
    assert work(**{**kwargs, "research_deadline_monotonic": time.monotonic()-1}).business_story_markdown
    with pytest.raises(execution.ResearchWorkDeferred):
        work(**{**kwargs, "symbol": "ANOTHER", "research_deadline_monotonic": time.monotonic()-1})
    assert len(calls) == 1


def test_ownership_reaches_threads_before_they_send_a_request(durable_store):
    from contextvars import copy_context
    with execution.execution_ownership("producer", "news", lease_seconds=.1):
        contexts = copy_context()
        seen = []
        thread = threading.Thread(target=contexts.run, args=(lambda: seen.append(len(execution._OWNERSHIP.get())),))
        thread.start(); thread.join()
        assert seen == [1]


def test_distributed_ownership_rejects_overlap_and_recovers_after_release(durable_store):
    with execution.execution_ownership("producer", "news") as first:
        with execution.execution_ownership("producer", "news") as overlapping:
            assert first and not overlapping
    with execution.execution_ownership("producer", "news") as next_owner:
        assert next_owner


def test_lost_ownership_stops_further_provider_work(durable_store, monkeypatch):
    monkeypatch.setattr(pipeline_store, "renew_execution_state", lambda *args, **kwargs: False)
    with pytest.raises(RuntimeError, match="ownership was lost"):
        with execution.execution_ownership("producer", "news", lease_seconds=.12):
            time.sleep(.08)
            execution.check_execution_ownership()


def test_wallet_reserves_concurrent_requests_and_reconciles_without_generation(durable_store, monkeypatch):
    probes = []
    balance = {"value": .8}
    def probe(*args, **kwargs):
        probes.append(args)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "is_available": True, "balance_infos": [{"currency": "USD", "total_balance": str(balance["value"])}]})
    monkeypatch.setattr(execution.requests, "get", probe)
    config = model().config
    payload = {"model": config.model, "messages": [{"role": "user", "content": "Supplied evidence"}]}
    assert execution.reserve_provider_budget(config, payload, attempt_id="one", timeout=60)
    assert execution.reserve_provider_budget(config, payload, attempt_id="two", timeout=60)
    assert execution.reserve_provider_budget(config, payload, attempt_id="three", timeout=60)
    with pytest.raises(llm.LLMProviderUnavailable, match="cannot cover"):
        execution.reserve_provider_budget(config, payload, attempt_id="four", timeout=60)
    execution.settle_provider_budget(config, attempt_id="one", charge={"status": "usage_unavailable"}, definitely_unbilled=False)
    execution.settle_provider_budget(config, attempt_id="two", charge={"status": "estimated", "usd_max": .01}, definitely_unbilled=False)
    state = durable_store[("provider_budget", execution._account_key(config))]
    assert state["reservations"]["one"]["status"] == "unknown"
    assert state["reservations"]["two"]["amount_usd"] == .01
    assert len(probes) == 1
    state["observed_at"] -= 31
    balance["value"] = .9
    execution.reserve_provider_budget(config, payload, attempt_id="five", timeout=60)
    refreshed = durable_store[("provider_budget", execution._account_key(config))]
    assert "two" not in refreshed["reservations"] and "one" in refreshed["reservations"]
    assert len(probes) == 2
