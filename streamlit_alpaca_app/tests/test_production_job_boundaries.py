"""Execution/storage boundaries only; no assertions grade model prose."""
from types import SimpleNamespace
import subprocess
import sys
import threading
import time

import pandas as pd
import pytest

from services import page_browsing
from services.aql import business_model_stack as company, news_business_resolution as news
from services.work_scope import WorkScope, WorkCancelled


def test_baseline_company_work_respects_the_batch_deadline(monkeypatch):
    monkeypatch.setenv("LLM_POLICY_PROFILE", "baseline")
    monkeypatch.setenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE", "")
    monkeypatch.setattr(company, "_business_research_budget_seconds", lambda: 60)
    calls = []
    from services.llm_execution import _CALL
    bounded = company._checkpoint_company_research(lambda **kwargs:
        calls.append(_CALL.get()["completion_only"]) or SimpleNamespace(business_story_markdown=""))
    bounded(symbol="SUBJECT", llm_client=object(), research_deadline_monotonic=time.monotonic() + 1)
    bounded(symbol="SUBJECT", llm_client=object(), research_deadline_monotonic=time.monotonic() + 120)
    assert calls == [True, False]
    with pytest.raises(WorkCancelled):
        bounded(symbol="SUBJECT", llm_client=object(), research_deadline_monotonic=time.monotonic() - 1)


def test_news_batch_uses_remaining_window_for_current_best_effort(monkeypatch):
    monkeypatch.setenv("ZOPEDIA_NEWS_BUSINESS_MAX_WORKERS", "1")
    monkeypatch.setenv("ZOPEDIA_NEWS_BUSINESS_TIMEOUT_SECONDS", "900")
    clock = [0.0]
    monkeypatch.setattr(news.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(news, "_news_symbol_rows", lambda *args, **kwargs: [(pd.Series(), "FIRST"), (pd.Series(), "SECOND")])
    def resolve(*args, **kwargs):
        clock[0] += 100
        return SimpleNamespace(status="ready", symbol=kwargs["symbol"], coherent_story_markdown="")
    monkeypatch.setattr(news, "resolve_news_business_event", resolve)
    deferred = []
    results = news.build_news_business_resolution_results(
        news_frame=pd.DataFrame(), research_deadline_monotonic=950, progress_callback=deferred.append)
    assert [result.symbol for result in results] == ["FIRST", "SECOND"]
    assert not deferred


def test_browser_queue_expires_without_launching_a_process():
    capacity = threading.BoundedSemaphore(1)
    capacity.acquire()
    try:
        with WorkScope(time.monotonic() + 0.02).activate():
            with pytest.raises(WorkCancelled):
                with page_browsing._owned_browser_slot(capacity):
                    pytest.fail("Expired browser work acquired capacity")
    except WorkCancelled:
        pass
    finally:
        capacity.release()


def test_browser_deadline_terminates_its_actual_child_process(monkeypatch):
    original = subprocess.Popen
    children = []
    def process(*args, **kwargs):
        child = original([sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(30)"], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(page_browsing.subprocess, "Popen", process)
    with pytest.raises(page_browsing.PageBrowsingError):
        with WorkScope(time.monotonic() + 0.08).activate():
            page_browsing._run_browser_process("playwright", "https://example.com", timeout_seconds=30, max_text_chars=500)
    assert children and children[0].poll() is not None


def test_eight_company_workers_carry_the_parent_execution_scope(monkeypatch):
    monkeypatch.setenv("ZOPEDIA_TICKER_BUSINESS_MAX_WORKERS", "8")
    barrier = threading.Barrier(8)
    work = WorkScope(time.monotonic() + 5)
    inherited = []
    def investigate(*, symbol, **kwargs):
        from services.work_scope import remaining_work_seconds
        inherited.append(remaining_work_seconds(1000))
        barrier.wait(timeout=3)
        return symbol
    monkeypatch.setattr(company, "build_ticker_business_model_stack", investigate)
    with work.activate():
        results = company.build_ticker_business_model_stack_results(symbols=[str(index) for index in range(8)])
    assert len(results) == len(inherited) == 8
    assert all(0 < remaining <= 5 for remaining in inherited)


def test_company_batch_returns_completed_results_at_deadline_and_cancels_straggler(monkeypatch):
    from services.work_scope import check_work
    release = threading.Event()
    stopped = threading.Event()
    def investigate(*, symbol, **kwargs):
        if symbol == "FAST":
            return symbol
        try:
            release.wait(timeout=1)
            check_work()
            pytest.fail("Cancelled company continued work")
        except WorkCancelled:
            stopped.set()
            raise
    monkeypatch.setattr(company, "build_ticker_business_model_stack", investigate)
    events = []
    started = time.monotonic()
    try:
        results = company.build_ticker_business_model_stack_results(symbols=["FAST", "SLOW"],
            research_deadline_monotonic=started + .05, progress_callback=events.append)
        assert results == ["FAST"] and time.monotonic() - started < .5
    finally:
        release.set()
    assert stopped.wait(timeout=1)
    assert any(event.get("symbol") == "SLOW" for event in events)


def test_recovery_distinguishes_scan_progress_from_actual_failed_repairs(monkeypatch):
    from pipeline.jobs import main
    from services import pipeline_store
    manifest = {"checked": 4, "failed": {}, "next_offset": 8, "scan_complete": False}
    monkeypatch.setattr(pipeline_store, "read_execution_state", lambda *args, **kwargs: {"offset": 4})
    monkeypatch.setattr(pipeline_store, "write_execution_state", lambda *args, **kwargs: True)
    monkeypatch.setattr(pipeline_store, "reconcile_dataset_manifests", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(pipeline_store, "reconcile_provider_attempt_receipts", lambda **kwargs: {"reconciled": 3, "scan_complete": True})
    monkeypatch.setattr(pipeline_store, "reconcile_provider_budget_reservations", lambda **kwargs: {"settled": 2, "scan_complete": True})
    result = main.run_system_recovery(SimpleNamespace(), SimpleNamespace())
    assert result["job_status"] == "Succeeded" and result["scan_complete"] is False
    manifest["failed"] = {"dataset": "OperationalError"}
    assert main.run_system_recovery(SimpleNamespace(), SimpleNamespace())["job_status"] == "Warning"


def test_deployed_budget_admits_a_full_investigation_after_observed_preparation(monkeypatch):
    import json
    from pathlib import Path
    from services.work_scope import remaining_work_seconds
    config = json.loads((Path(__file__).parents[1] /
        "documents/verification/production-recovery-2026-10-27/configuration.json").read_text())
    env = config["changes"]["news-ingest-and-features"]["environment"]
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LLM_POLICY_PROFILE", "baseline")
    monkeypatch.setenv("ZOPEDIA_TICKER_BUSINESS_RESEARCH_BUDGET_SECONDS", "3600")
    prepared_at = time.monotonic()
    deadline = (prepared_at - config["admission_feasibility"]["observed_preparation_seconds"]
        + int(env["PIPELINE_JOB_WORK_TIMEOUT_SECONDS"])
        - int(env["PIPELINE_JOB_FINALIZATION_RESERVE_SECONDS"]))
    admitted = company._checkpoint_company_research(
        lambda **kwargs: SimpleNamespace(remaining=remaining_work_seconds(10000), business_story_markdown=""))
    assert admitted(symbol="SUBJECT", llm_client=None,
                    research_deadline_monotonic=deadline).remaining >= 3600


def test_news_phases_publish_current_outputs_without_merging_old_interpretations(monkeypatch):
    from datetime import datetime, timezone
    from pipeline.jobs import main
    ctx = main.JobContext(name="news-ingest-and-features", run_id="current",
                          asof=datetime.now(timezone.utc), universe_version="qualification")
    previous_date = "2026-10-26T14:05:00Z"
    previous = pd.DataFrame([dict(source_event_id="retained-source", symbol="RETAINED",
        run_id="earlier", asof_time_utc=previous_date, coherent_story_markdown="Retained output.")])
    monkeypatch.setenv("ZOPEDIA_TICKER_BUSINESS_RESEARCH_ENABLED", "false")
    monkeypatch.setattr(main, "_configure_news_business_research_budget_env", lambda: None)
    monkeypatch.setattr(main, "_load_latest_materialized_frame", lambda name:
        previous.copy() if name == "zopedia_news_business_resolutions" else pd.DataFrame())
    monkeypatch.setattr(main, "_zopedia_pages_for_news_resolution", lambda conn: pd.DataFrame())
    monkeypatch.setattr(main, "_job_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_make_stdout_zopedia_progress_callback", lambda **kwargs: lambda event: None)
    calls = []
    def company_phase(**kwargs):
        calls.append("company")
        kwargs["progress_callback"]({"stage": "business_research_incomplete", "symbol": "LATER"})
        return {"zopedia_ticker_business_model_stacks": pd.DataFrame([dict(
            symbol="COMPLETED", run_id="current", business_story_markdown="Completed output.")])}
    def interpretation_phase(**kwargs):
        kwargs["progress_callback"]({"stage": "news_interpretation_incomplete", "symbol": "LATER"})
        return {"zopedia_news_business_resolutions": pd.DataFrame([dict(
            source_event_id="current-source", symbol="COMPLETED", run_id="current",
            coherent_story_markdown="Completed output.")])}
    monkeypatch.setattr(main, "build_ticker_business_model_stack_frames", company_phase)
    monkeypatch.setattr(main, "build_news_business_resolution_frames", interpretation_phase)
    monkeypatch.setattr(main, "_commit_zopedia_memory_pages", lambda **kwargs: {"status": "skipped"})
    monkeypatch.setattr(main, "_zopedia_memory_commit_report_frame", lambda **kwargs: pd.DataFrame())
    phases = main._iter_news_business_research_output_phases(news=pd.DataFrame(),
        edgar_evidence=pd.DataFrame(), symbols=["COMPLETED", "LATER"], llm_client=None, ctx=ctx, conn=None)
    published = next(phases)["zopedia_news_business_resolutions"].set_index("symbol")
    assert set(published.index) == {"COMPLETED"}
    assert not calls
    assert next(phases)["zopedia_ticker_business_model_stacks"].iloc[0]["symbol"] == "COMPLETED"
    assert calls == ["company"]
    remaining = list(phases)
    assert len(remaining) == 1
    assert set(remaining[0]) == {"zopedia_company_business_memory_pages", "zopedia_memory_commit_report"}
    assert not ctx.continuations
    assert set(ctx.degraded_components) == {"company_research", "news_interpretation"}


def test_home_composition_does_not_start_another_knowledge_write(monkeypatch):
    from pipeline.jobs import attention_home_build as home
    requests = []
    monkeypatch.setattr(home, "_zopedia_agent_llm_timeout_seconds", lambda total: 480)
    monkeypatch.setattr(home, "run_aql_zopedia_agent", lambda **kwargs:
        requests.append(kwargs) or {"status": "completed", "answer_markdown": "Retained composition."})
    supplied = [{"text": "Previously acquired input", "published_at": "2026-10-26T14:05:00Z"}]
    home._run_single_zopedia_enrichment("SUBJECT", timeout_seconds=600,
        asof_time_utc="2026-10-27T14:05:00Z", initial_evidence=supplied)
    assert requests[0]["initial_evidence"] is supplied
    assert requests[0]["max_tool_calls"] == 0
    assert requests[0]["write_policy"] == "none"
