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
        execution.reserve_provider_budget(model().config, {}, attempt_id="expired", timeout=5)


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


def test_replica_retry_uses_baseline_without_changing_the_next_execution(durable_store, monkeypatch):
    from services.inference_policy import execute_with_fallback
    import os
    ctx = SimpleNamespace(name="news-ingest-and-features", run_id="attempt", started_at_monotonic=time.monotonic(),
                          degraded_components={})
    monkeypatch.setenv("CONTAINER_APP_JOB_EXECUTION_NAME", "scheduled-execution")
    calls = []
    def handler(context, connection):
        calls.append(os.environ["LLM_POLICY_PROFILE"])
        if calls[-1] == "balanced":
            raise RuntimeError("candidate failed")
        return "retained baseline result"
    with pytest.raises(RuntimeError, match="candidate failed"):
        execute_with_fallback(handler, ctx, None)
    assert calls == ["balanced"]
    assert execute_with_fallback(handler, ctx, None) == "retained baseline result"
    assert calls == ["balanced", "baseline"]
    monkeypatch.setenv("LLM_POLICY_PROFILE", "balanced")
    monkeypatch.setenv("CONTAINER_APP_JOB_EXECUTION_NAME", "next-scheduled-execution")
    next_run = SimpleNamespace(name=ctx.name, run_id="next-run", started_at_monotonic=time.monotonic(), degraded_components={})
    assert execute_with_fallback(lambda *args: os.environ["LLM_POLICY_PROFILE"], next_run, None) == "balanced"


def test_interrupted_candidate_uses_baseline_on_next_replica(durable_store, monkeypatch):
    from services.inference_policy import execute_with_fallback, policy_identity
    import os
    ctx = SimpleNamespace(name="news-ingest-and-features", run_id="attempt", started_at_monotonic=time.monotonic(),
                          degraded_components={})
    monkeypatch.setenv("CONTAINER_APP_JOB_EXECUTION_NAME", "same-execution")
    ns = "inference_rollout:" + pipeline_store.dataset_namespace()
    durable_store[(ns, ctx.name + ":same-execution:" + policy_identity())] = {"status": "started", "execution": "same-execution"}
    assert execute_with_fallback(lambda *args: os.environ["LLM_POLICY_PROFILE"], ctx, None) == "baseline"


def test_old_release_rollback_does_not_override_a_new_executions_selected_profile(durable_store, monkeypatch):
    from services.inference_policy import execute_with_fallback, policy_identity
    import os
    ctx = SimpleNamespace(name="news-ingest-and-features", run_id="new-run", started_at_monotonic=time.monotonic(), degraded_components={})
    monkeypatch.setenv("CONTAINER_APP_JOB_EXECUTION_NAME", "new-execution")
    ns = "inference_rollout:" + pipeline_store.dataset_namespace()
    durable_store[(ns, ctx.name + ":" + policy_identity())] = {"status": "failed", "rollback": True, "execution": "old-execution"}
    assert execute_with_fallback(lambda *args: os.environ["LLM_POLICY_PROFILE"], ctx, None) == "balanced"
    monkeypatch.setenv("LLM_POLICY_PROFILE", "baseline")
    assert execute_with_fallback(lambda *args: os.environ["LLM_POLICY_PROFILE"], ctx, None) == "baseline"


def test_failed_rollout_audit_preserves_the_original_error(durable_store, monkeypatch):
    from services.inference_policy import execute_with_fallback
    from services.work_scope import WorkCancelled
    ctx = SimpleNamespace(name="news-ingest-and-features", run_id="attempt", started_at_monotonic=time.monotonic(), degraded_components={})
    original = ValueError("original checkpoint failure")
    write = pipeline_store.write_execution_state
    def failed_audit(namespace, key, value, **kwargs):
        if value.get("status") == "failed":
            raise WorkCancelled("audit owner cancelled")
        return write(namespace, key, value, **kwargs)
    monkeypatch.setattr(pipeline_store, "write_execution_state", failed_audit)
    def handler(*args):
        raise original
    with pytest.raises(ValueError) as caught:
        execute_with_fallback(handler, ctx, None)
    assert caught.value is original


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
    from datetime import datetime, timezone
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
    monkeypatch.setattr(jobs, "read_execution_state", lambda *args, **kwargs: {
        "run_id": "original-replica", "asof": "2026-10-26T14:05:00+00:00", "universe_version": "original"})
    monkeypatch.setattr(jobs, "write_execution_state", lambda *args, **kwargs: True)
    monkeypatch.setattr(jobs, "execution_ownership", ownership)
    monkeypatch.setattr(jobs, "run_news", lambda ctx, conn: resumed.append(ctx))
    monkeypatch.setattr(pipeline_store, "retain_job_execution_receipt", lambda *args: None)
    jobs.main()
    assert waited == [315]
    assert len(resumed) == 1 and resumed[0].run_id == "original-replica"
    original = datetime.fromisoformat("2026-10-26T14:05:00+00:00")
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
