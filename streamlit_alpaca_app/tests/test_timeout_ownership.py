"""Thread/IO ownership checks; no inference or answer-quality assertions."""
import threading

import pytest

from services.work_scope import register_cancellation, check_work, remaining_work_seconds, WorkCancelled


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
