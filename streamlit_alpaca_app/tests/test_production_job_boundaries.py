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
    bounded = company._checkpoint_company_research(lambda **kwargs: calls.append(kwargs) or "completed")
    with pytest.raises(company.ResearchWorkDeferred):
        bounded(symbol="SUBJECT", llm_client=object(), research_deadline_monotonic=time.monotonic() + 1)
    assert not calls
    assert bounded(symbol="SUBJECT", llm_client=object(), research_deadline_monotonic=time.monotonic() + 120) == "completed"


def test_news_batch_returns_completed_work_and_defers_the_next_item(monkeypatch):
    monkeypatch.setenv("ZOPEDIA_NEWS_BUSINESS_MAX_WORKERS", "1")
    monkeypatch.setenv("ZOPEDIA_NEWS_BUSINESS_TIMEOUT_SECONDS", "900")
    clock = [0.0]
    monkeypatch.setattr(news.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(news, "_news_symbol_rows", lambda *args, **kwargs: [(pd.Series(), "FIRST"), (pd.Series(), "SECOND")])
    def resolve(*args, **kwargs):
        clock[0] += 100
        return SimpleNamespace(status="ready", symbol=kwargs["symbol"])
    monkeypatch.setattr(news, "resolve_news_business_event", resolve)
    deferred = []
    results = news.build_news_business_resolution_results(
        news_frame=pd.DataFrame(), research_deadline_monotonic=950, progress_callback=deferred.append)
    assert [result.symbol for result in results] == ["FIRST"]
    assert [event["symbol"] for event in deferred] == ["SECOND"]


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


def test_recovery_distinguishes_scan_progress_from_actual_failed_repairs(monkeypatch):
    from pipeline.jobs import main
    from services import pipeline_store
    manifest = {"checked": 4, "failed": {}, "next_offset": 8, "scan_complete": False}
    monkeypatch.setattr(pipeline_store, "read_execution_state", lambda *args, **kwargs: {"offset": 4})
    monkeypatch.setattr(pipeline_store, "write_execution_state", lambda *args, **kwargs: True)
    monkeypatch.setattr(pipeline_store, "reconcile_dataset_manifests", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(pipeline_store, "reconcile_provider_attempt_receipts", lambda **kwargs: {"reconciled": 3, "scan_complete": True})
    result = main.run_system_recovery(SimpleNamespace(), SimpleNamespace())
    assert result["job_status"] == "Succeeded" and result["scan_complete"] is False
    manifest["failed"] = {"dataset": "OperationalError"}
    assert main.run_system_recovery(SimpleNamespace(), SimpleNamespace())["job_status"] == "Warning"
