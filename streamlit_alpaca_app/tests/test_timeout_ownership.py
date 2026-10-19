"""Thread/IO ownership checks; no inference or answer-quality assertions."""
import threading
import json
from types import SimpleNamespace

import pytest

from services.work_scope import register_cancellation, check_work, remaining_work_seconds, WorkCancelled


@pytest.mark.parametrize("outcome", ["draft", "sources", "normal_return", "no_work"])
def test_aql_deadline_returns_available_shared_research(monkeypatch, outcome):
    from services import aql_zopedia_engine as engine
    from services.aql import pipeline

    cancelled, ended = threading.Event(), threading.Event()
    latest = [None]
    source = {"tool_call_id": "source", "status": "completed",
              "result_summary": {"source_text": "complete captured source"}}
    state = {"run_id": "run", "draft_answer": json.dumps({"value": "draft"}),
             "tool_calls": [source], "quality_review": {"status": "not_run"}}
    if outcome == "sources":
        state["draft_answer"] = "{"
    if outcome == "no_work":
        state.update(draft_answer="", tool_calls=[])

    def agent(**kwargs):
        unregister = register_cancellation(cancelled.set)
        try:
            kwargs["checkpoint_callback"](state)
            source["result_summary"]["source_text"] = "later mutable state"
            if outcome == "normal_return":
                return {"status": "completed", "answer_markdown": json.dumps({"value": "reviewed"})}
            assert cancelled.wait(2)
            check_work()
        finally:
            unregister()
            ended.set()

    def retain(result):
        latest[0] = result

    monkeypatch.setattr(engine, "run_aql_zopedia_agent", agent)
    client = SimpleNamespace(config=SimpleNamespace(model="transport", provider="transport"))
    def research():
        return engine.run_aql_zopedia_structured_agent(
            query="request", task="contract", surface="contract", llm_client=client,
            schema_name="artifact", schema={"type": "object", "required": ["value"],
                                            "properties": {"value": {"type": "string"}}},
            result_callback=retain,
        )

    if outcome == "no_work":
        with pytest.raises(pipeline.AQLStepTimeout):
            pipeline._call_with_timeout("contract", .1, research, on_timeout=lambda: latest[0])
    else:
        result = pipeline._call_with_timeout("contract", .1, research, on_timeout=lambda: latest[0])
        if outcome == "normal_return":
            assert result["payload"] == {"value": "reviewed"}
        else:
            assert result["payload"] == ({"value": "draft"} if outcome == "draft" else None)
            assert result["status"] == ("completed" if outcome == "draft" else "partial")
            assert result["agent_result"]["tool_calls"][0]["result_summary"]["source_text"] == "complete captured source"
    assert ended.wait(2)
    assert cancelled.is_set() == (outcome != "normal_return")


@pytest.mark.parametrize("owner", ["attention", "aql", "live_research", "pipeline"])
def test_timeout_cancels_worker_scope_before_it_can_start_more_io(owner):
    from pipeline.jobs import attention_home_build, main
    from services.aql import pipeline
    from services import zopedia_research
    cancelled, ended = threading.Event(), threading.Event()
    late_io = []

    def work():
        unregister = register_cancellation(cancelled.set)
        try:
            assert cancelled.wait(4)
            check_work()
            late_io.append(True)
        finally:
            unregister()
            ended.set()

    if owner == "live_research":
        value, error = zopedia_research._run_with_timeout("ownership", 1, work, "fallback")
        assert value == "fallback" and error
    else:
        call = {"attention": attention_home_build._call_with_timeout,
                "aql": pipeline._call_with_timeout, "pipeline": main._call_with_timeout}[owner]
        error = {"attention": attention_home_build._AttentionStepTimeout, "aql": pipeline.AQLStepTimeout,
                 "pipeline": main._PipelineStepTimeout}[owner]
        with pytest.raises(error):
            call("ownership", 1, work)
    assert cancelled.is_set() and ended.wait(2)
    assert not late_io


@pytest.mark.parametrize("interrupt_at", ["read", "write"])
def test_recovery_continuation_io_is_inside_its_total_budget(monkeypatch, interrupt_at):
    from pipeline.jobs import main
    from services import pipeline_store as store
    from types import SimpleNamespace
    rolled_back, saved = [], []
    conn = SimpleNamespace(rollback=lambda: rolled_back.append(True))

    def read(*a, **kw):
        assert remaining_work_seconds(1000) <= 60
        if interrupt_at == "read":
            raise WorkCancelled("Interrupted continuation read")
        return {"offset": 0}

    def write(*a, **kw):
        assert remaining_work_seconds(1000) <= 60
        saved.append(True)
        raise WorkCancelled("Interrupted continuation write")

    monkeypatch.setenv("SYSTEM_RECOVERY_BUDGET_SECONDS", "60")
    monkeypatch.setattr(store, "read_execution_state", read)
    monkeypatch.setattr(store, "write_execution_state", write)
    monkeypatch.setattr(store, "reconcile_dataset_manifests", lambda *a, **kw: {"next_offset": 4, "scan_complete": False})
    monkeypatch.setattr(store, "reconcile_provider_attempt_receipts", lambda **kw: pytest.fail("Expired batch started more recovery"))
    result = main.run_system_recovery(SimpleNamespace(), conn)
    assert result["job_status"] == "Warning" and not result["scan_complete"]
    assert rolled_back == [True]
    assert bool(saved) == (interrupt_at == "write")


def test_recovery_uses_remaining_budget_after_database_admission_wait(monkeypatch):
    from pipeline.jobs import main
    from services import pipeline_store as store

    now = [0.0]
    saved = []
    monkeypatch.setattr(main.time, "monotonic", lambda: now[0])
    monkeypatch.setenv("SYSTEM_RECOVERY_BUDGET_SECONDS", "60")
    monkeypatch.setattr(store, "read_execution_state", lambda *a, **kw: {"offset": 4})

    def reconcile(conn, *, offset, limit):
        # Capacity becomes available after the former 24-second phase cutoff,
        # while the caller still has half its total execution budget available.
        now[0] = 30.0
        check_work()
        assert offset == 4 and limit == 4
        return {"next_offset": 8, "scan_complete": False}

    monkeypatch.setattr(store, "reconcile_dataset_manifests", reconcile)
    monkeypatch.setattr(store, "write_execution_state", lambda *args: saved.append(args[2]))
    monkeypatch.setattr(store, "reconcile_provider_attempt_receipts", lambda **kw: {"scan_complete": True, "reconciled": 0})
    result = main.run_system_recovery(SimpleNamespace(), SimpleNamespace(rollback=lambda: None))
    assert saved == [{"offset": 8}]
    assert result["manifests"]["next_offset"] == 8
    assert result["elapsed_seconds"] == 30 and result["job_status"] == "Warning"
