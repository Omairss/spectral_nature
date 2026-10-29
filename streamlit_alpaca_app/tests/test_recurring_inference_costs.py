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


def test_reusable_source_prefix_precedes_volatile_run_metadata():
    from services.zopedia_agent import _judge_user_prompt
    source = "Complete supplied document. " * 200
    def prompt(identity):
        return _judge_user_prompt(query="Current question " + identity, draft_answer="Draft " + identity,
            draft_confidence="medium", limitations=[], tool_calls=[{
                "tool_call_id": identity, "origin_run_id": identity, "tool_name": "research.supplied_context",
                "status": "completed", "result_summary": {"evidence_context_text": source}}])
    left, right = prompt("first-owner"), prompt("second-owner")
    assert left[:left.index("Evidence record metadata")] == right[:right.index("Evidence record metadata")]
    assert source in left and "first-owner" in left and "Current question first-owner" in left


@pytest.fixture
def durable_store(monkeypatch):
    values = {}
    lock = threading.RLock()
    monkeypatch.setenv("APP_TRACK", "development")
    monkeypatch.setenv("LLM_POLICY_PROFILE", "balanced")
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


def test_balance_waiter_uses_a_competing_workers_fresh_result(durable_store, monkeypatch):
    from contextlib import contextmanager
    config = model().config
    key = execution._account_key(config)
    @contextmanager
    def competing_refresh(namespace, identity, **kwargs):
        durable_store[("provider_budget", key)] = {
            "observed_at": time.time(), "available": True, "balance_usd": 100,
            "reservations": {},
        }
        yield False
    monkeypatch.setattr(execution, "execution_ownership", competing_refresh)
    monkeypatch.setattr(execution.requests, "get", lambda *args, **kwargs:
        pytest.fail("A fresh shared balance must not be fetched again"))
    assert execution.reserve_provider_budget(config, {"model": config.model,
        "messages": [{"role": "user", "content": "Coordination check"}]},
        attempt_id="waiter", timeout=60)
    assert "waiter" in durable_store[("provider_budget", key)]["reservations"]


def test_balance_contention_stays_inside_the_request_deadline(durable_store, monkeypatch):
    from contextlib import contextmanager
    clock = [0.0]
    monkeypatch.setattr(execution.time, "monotonic", lambda: clock[0])
    @contextmanager
    def unavailable_refresh(*args, **kwargs):
        clock[0] += 10
        yield False
    monkeypatch.setattr(execution, "execution_ownership", unavailable_refresh)
    with pytest.raises(llm.LLMProviderUnavailable, match="request deadline"):
        execution.reserve_provider_budget(model().config, {"messages": [{"role": "user", "content": "Current input"}]},
                                          attempt_id="expired", timeout=5)


def test_bounded_batch_rotates_older_and_missing_profiles_before_fresh_ones(monkeypatch):
    captured = []
    monkeypatch.setattr(company, "build_ticker_business_model_stack_results", lambda **kw: captured.extend(kw["symbols"]) or [])
    previous = pd.DataFrame([
        dict(symbol="FRESH", asof_time_utc="2026-10-26T14:05Z", business_story_markdown="Fresh analysis"),
        dict(symbol="OLDER", asof_time_utc="2026-10-20T14:05Z", business_story_markdown="Older analysis")])
    company.build_ticker_business_model_stack_frames(symbols=["FRESH", "OLDER", "MISSING", "OUTSIDE"],
        existing_stacks_frame=previous, limit=3)
    assert captured == ["MISSING", "OLDER", "FRESH"]


def test_policy_routes_evidence_work_but_preserves_analysis_and_review(monkeypatch):
    from services.inference_policy import client_for_task, policy_identity
    monkeypatch.setenv("LLM_POLICY_PROFILE", "balanced")
    full = model()
    fast, metadata = client_for_task(full, surface="pipeline.ticker_business_model_stack.research_plan",
                                     purpose="agent.research_step")
    assert fast.config.model == "deepseek-flash" and metadata["role"] == "economy"
    for surface, purpose in [("pipeline.ticker_business_model_stack.research_dossier", "ticker_business_model_research_dossier.direct"),
                             ("pipeline.ticker_business_model_stack", "ticker_business_model_stack.direct"),
                             ("pipeline.ticker_business_model_stack.research_followup", "agent.answer_judge"),
                             ("trading_agent.synthesis", "candidate_synthesis.direct")]:
        assert client_for_task(full, surface=surface, purpose=purpose)[0] is full
    candidate = policy_identity()
    monkeypatch.setenv("LLM_POLICY_PROFILE", "baseline")
    assert policy_identity() != candidate
    assert client_for_task(full, surface="pipeline.ticker_business_model_stack.research_dossier", purpose="anything")[0] is full


def test_policy_activation_uses_pacific_date(monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from services.inference_policy import policy
    monkeypatch.setenv("LLM_POLICY_PROFILE", "balanced")
    tomorrow = datetime.now(ZoneInfo("America/Los_Angeles")).date() + timedelta(days=1)
    monkeypatch.setenv("LLM_POLICY_NOT_BEFORE", tomorrow.isoformat())
    assert policy()["profile"] == "baseline"
    monkeypatch.setenv("LLM_POLICY_NOT_BEFORE", (tomorrow - timedelta(days=1)).isoformat())
    assert policy()["profile"] == "balanced"


@pytest.fixture
def news_execution(durable_store, monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime, timezone
    from pipeline.jobs import main as jobs
    clock = [datetime.now(timezone.utc)]
    receipts = []
    @contextmanager
    def ownership(*args, **kwargs):
        yield True
    monkeypatch.setenv("PIPELINE_JOB_NAME", "news-ingest-and-features")
    monkeypatch.setenv("PIPELINE_RUN_ID", "controller-contract")
    monkeypatch.setenv("CONTAINER_APP_JOB_EXECUTION_NAME", "scheduled-execution")
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "10")
    monkeypatch.setenv("PIPELINE_JOB_MAX_DURATION_SECONDS", "")
    monkeypatch.setenv("PIPELINE_JOB_WORK_TIMEOUT_SECONDS", "6900")
    monkeypatch.setattr(jobs, "_utc_now", lambda: clock[0])
    monkeypatch.setattr(jobs, "_db_connection", lambda **kwargs: None)
    monkeypatch.setattr(jobs, "read_execution_state", pipeline_store.read_execution_state)
    monkeypatch.setattr(jobs, "write_execution_state", pipeline_store.write_execution_state)
    monkeypatch.setattr(jobs, "execution_ownership", ownership)
    monkeypatch.setattr(pipeline_store, "retain_job_execution_receipt", receipts.append)
    return SimpleNamespace(jobs=jobs, clock=clock, receipts=receipts, state=durable_store)


def test_failed_execution_retries_with_unchanged_routing_budget_and_clock(news_execution, monkeypatch):
    from datetime import timedelta
    import os
    from services.work_scope import remaining_work_seconds
    run = news_execution
    calls = []
    original = ValueError("original checkpoint failure")
    def handler(ctx, conn):
        calls.append((ctx, os.environ["LLM_POLICY_PROFILE"], os.environ["RESEARCH_RUN_BUDGET_USD"],
                      remaining_work_seconds(10000)))
        if len(calls) == 1:
            raise original
    monkeypatch.setattr(run.jobs, "run_news", handler)
    with pytest.raises(ValueError) as caught:
        run.jobs.main()
    assert caught.value is original
    run.clock[0] += timedelta(seconds=600)
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "1000")
    run.jobs.main()
    assert [value[1:3] for value in calls] == [("balanced", "10"), ("balanced", "10")]
    assert calls[0][0].run_id == calls[1][0].run_id
    assert calls[0][0].asof == calls[1][0].asof
    assert 7199 < calls[0][3] <= 7200 and 6599 < calls[1][3] <= 6600
    assert not any(ns.startswith("inference_rollout:") for ns, key in run.state)
    assert run.receipts[0]["status"] == "Failed"


def test_historical_rollback_state_cannot_change_selected_routing(news_execution, monkeypatch):
    from services.inference_policy import policy, policy_identity
    run = news_execution
    identity = ("inference_rollout:" + pipeline_store.dataset_namespace(), "news-ingest-and-features:" + policy_identity())
    historical = {"status": "failed", "rollback": True, "execution": "old-execution"}
    run.state[identity] = historical.copy()
    observed = []
    monkeypatch.setattr(run.jobs, "run_news", lambda *args: observed.append(policy()["profile"]))
    run.jobs.main()
    assert observed == ["balanced"] and run.state[identity] == historical
    monkeypatch.setenv("LLM_POLICY_PROFILE", "baseline")
    monkeypatch.setenv("CONTAINER_APP_JOB_EXECUTION_NAME", "explicit-baseline-execution")
    run.jobs.main()
    assert observed == ["balanced", "baseline"]


def test_partial_results_are_recorded_without_a_whole_run_model_retry(news_execution, monkeypatch):
    run = news_execution
    calls = []
    def handler(ctx, conn):
        calls.append(ctx.run_id)
        ctx.degraded_components["news_narratives"] = "One investigation remains pending."
    monkeypatch.setattr(run.jobs, "run_news", handler)
    run.jobs.main()
    assert len(calls) == 1
    assert run.receipts[-1]["status"] == "Warning"
    assert "news_narratives" in run.receipts[-1]["components"]


def test_expired_retry_cannot_start_more_work(news_execution, monkeypatch):
    from datetime import timedelta
    from services.work_scope import WorkCancelled
    run = news_execution
    calls = []
    monkeypatch.setattr(run.jobs, "run_news", lambda *args: calls.append(True))
    run.jobs.main()
    run.clock[0] += timedelta(seconds=7201)
    with pytest.raises(WorkCancelled):
        run.jobs.main()
    assert calls == [True] and run.receipts[-1]["status"] == "Failed"


def test_producer_deadline_returns_already_published_current_subset(news_execution, monkeypatch):
    from services.work_scope import check_work
    import time
    run = news_execution
    monkeypatch.setenv("PIPELINE_JOB_MAX_DURATION_SECONDS", "1")
    def handler(ctx, conn):
        ctx.published_datasets["zopedia_news_business_resolutions"] = "current-publication"
        time.sleep(1.02)
        check_work()
    monkeypatch.setattr(run.jobs, "run_news", handler)
    run.jobs.main()
    assert run.receipts[-1]["status"] == "Warning"
    assert run.receipts[-1]["datasets"] == {"zopedia_news_business_resolutions": "current-publication"}


def test_explicit_job_deadline_also_applies_to_baseline(news_execution, monkeypatch):
    from datetime import timedelta
    from services.work_scope import WorkCancelled, remaining_work_seconds
    run = news_execution
    monkeypatch.setenv("LLM_POLICY_PROFILE", "baseline")
    monkeypatch.setenv("PIPELINE_JOB_MAX_DURATION_SECONDS", "60")
    calls = []
    monkeypatch.setattr(run.jobs, "run_news", lambda *args: calls.append(remaining_work_seconds(10000)))
    run.jobs.main()
    assert 59 < calls[0] <= 60
    run.clock[0] += timedelta(seconds=61)
    with pytest.raises(WorkCancelled):
        run.jobs.main()
    assert len(calls) == 1


def test_independent_company_failure_does_not_discard_completed_companies(monkeypatch):
    events = []
    monkeypatch.setenv("ZOPEDIA_TICKER_BUSINESS_MAX_WORKERS", "3")
    def build(**kwargs):
        if kwargs["symbol"] == "FAILED":
            raise ValueError("checkpoint rejected")
        return result_for(kwargs)
    monkeypatch.setattr(company, "build_ticker_business_model_stack", build)
    results = company.build_ticker_business_model_stack_results(symbols=["FIRST", "FAILED", "LAST"],
        run_id="batch", asof_time_utc="2026-10-27T00:00:00Z", progress_callback=events.append)
    assert [result.symbol for result in results] == ["FIRST", "LAST"]
    assert any(event.get("stage") == "business_research_failed" and event["symbol"] == "FAILED" for event in events)


def test_platform_retry_waits_for_a_dead_replicas_producer_lease(durable_store, monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime, timedelta, timezone
    from pipeline.jobs import main as jobs
    waited = []
    resumed = []
    @contextmanager
    def ownership(namespace, key, **options):
        waited.append(options.get("wait_seconds", 0))
        yield True
    monkeypatch.setenv("PIPELINE_JOB_NAME", "news-ingest-and-features")
    monkeypatch.setenv("PIPELINE_RUN_ID", "retry-test")
    monkeypatch.setenv("CONTAINER_APP_JOB_EXECUTION_NAME", "same-platform-execution")
    monkeypatch.setenv("LLM_POLICY_PROFILE", "baseline")
    monkeypatch.setattr(jobs, "_db_connection", lambda **kwargs: None)
    original = datetime.now(timezone.utc) - timedelta(seconds=60)
    monkeypatch.setattr(jobs, "read_execution_state", lambda *args, **kwargs: {
        "run_id": "original-replica", "asof": original.isoformat(), "universe_version": "original"})
    monkeypatch.setattr(jobs, "write_execution_state", lambda *args, **kwargs: True)
    monkeypatch.setattr(jobs, "execution_ownership", ownership)
    monkeypatch.setattr(jobs, "run_news", lambda ctx, conn: resumed.append(ctx))
    monkeypatch.setattr(pipeline_store, "retain_job_execution_receipt", lambda *args: None)
    jobs.main()
    assert waited == [315]
    assert len(resumed) == 1 and resumed[0].run_id == "original-replica"
    assert abs((time.monotonic() - resumed[0].started_at_monotonic)
               - (datetime.now(timezone.utc) - original).total_seconds()) < 1


def test_lost_ownership_cancels_existing_io_scope(durable_store, monkeypatch):
    from services.work_scope import check_work, WorkCancelled
    monkeypatch.setattr(pipeline_store, "renew_execution_state", lambda *args, **kwargs: False)
    with pytest.raises(WorkCancelled):
        with execution.execution_ownership("producer", "lost-owner", lease_seconds=.06):
            time.sleep(.04)
            check_work()


def test_released_owner_cannot_do_io_from_an_inherited_context(durable_store):
    from contextvars import copy_context
    from services.work_scope import check_work, WorkCancelled
    with execution.execution_ownership("producer", "closed-owner"):
        inherited = copy_context()
    with execution.execution_ownership("producer", "closed-owner"):
        with pytest.raises(WorkCancelled):
            inherited.run(check_work)
        with pytest.raises(RuntimeError, match="ownership was lost"):
            inherited.run(execution.check_execution_ownership)


def test_renewable_ownership_preserves_existing_long_operation_budgets(durable_store):
    from services.work_scope import remaining_work_seconds
    with execution.execution_ownership("producer", "long-operation", lease_seconds=.3):
        assert remaining_work_seconds(600) == 600


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


def test_company_retry_keeps_identity_but_a_new_briefing_has_current_identity(durable_store):
    identities = []
    @company._checkpoint_company_research
    def work(**kwargs):
        identities.append((execution._CALL.get()["work_instance"], kwargs["asof_time_utc"]))
        execution.mark_research_work_incomplete()
        return result_for(kwargs)
    kwargs = dict(symbol="EXAMPLE", llm_client=model(), run_id="first", asof_time_utc="2026-10-26T14:00Z")
    work(**kwargs)
    work(**kwargs)
    work(**{**kwargs, "run_id": "next-execution", "asof_time_utc": "2026-10-26T16:00Z"})
    assert len(identities) == 3 and identities[0] == identities[1]
    assert identities[2][0] != identities[0][0]
    assert identities[2][1] == "2026-10-26T16:00:00+00:00"


def test_short_company_window_completes_economically_and_expired_window_stops(durable_store, monkeypatch):
    from services.work_scope import WorkCancelled
    monkeypatch.setenv("RESEARCH_RUN_BUDGET_USD", "10")
    monkeypatch.setenv("PIPELINE_RUN_ID", "deadline-contract")
    calls = []
    @company._checkpoint_company_research
    def work(**kwargs):
        calls.append(execution.best_effort_required())
        return result_for(kwargs)
    kwargs = dict(symbol="EXAMPLE", llm_client=model(), run_id="first", asof_time_utc="2026-10-26T14:00Z")
    work(**kwargs)
    assert work(**{**kwargs, "research_deadline_monotonic": time.monotonic()-1}).business_story_markdown
    result = work(**{**kwargs, "symbol": "CURRENT", "research_deadline_monotonic": time.monotonic()+60})
    assert result.business_story_markdown and calls == [False, True]
    with pytest.raises(WorkCancelled):
        work(**{**kwargs, "symbol": "ANOTHER", "research_deadline_monotonic": time.monotonic()-1})
    assert len(calls) == 2


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
