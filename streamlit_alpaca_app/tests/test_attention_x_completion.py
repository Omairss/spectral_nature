"""Research completion and external publication ordering; no model grading."""
import pytest

from pipeline.jobs import main as jobs
from services import attention_x, llm_execution, pipeline_store


@pytest.mark.parametrize("outcome", ["Succeeded", "Warning", "Failed", "no_publication"])
def test_attention_delivery_follows_finished_run_receipt(monkeypatch, outcome):
    for key in ("CONTAINER_APP_JOB_EXECUTION_NAME", "PIPELINE_JOB_MAX_DURATION_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PIPELINE_JOB_NAME", "attention-home-build")
    monkeypatch.setattr(jobs, "_db_connection", lambda **kwargs: None)
    monkeypatch.setattr(llm_execution, "interrupt_core_research", lambda *args: None)
    monkeypatch.setattr(llm_execution, "research_spending_context", lambda: {})
    order = []

    def research(ctx, connection):
        if outcome == "Failed":
            raise RuntimeError("Research failed")
        if outcome != "no_publication":
            ctx.published_datasets["attention_home_snapshots_1d"] = "exact-finished-version"
        return {"job_status": outcome}

    def publish(**kwargs):
        assert order[-1][0] == "completion"
        assert kwargs["run_id"] == order[-1][1]["run_id"]
        assert kwargs["source_version"] == "exact-finished-version"
        order.append(("publication", kwargs))
        raise RuntimeError("X is unavailable")

    monkeypatch.setattr(jobs, "run_attention_home", research)
    monkeypatch.setattr(pipeline_store, "retain_job_execution_receipt",
                        lambda receipt: order.append(("completion", receipt)))
    monkeypatch.setattr(attention_x, "publish_completed_attention", publish)
    if outcome == "Failed":
        with pytest.raises(RuntimeError, match="Research failed"):
            jobs.main()
    else:
        jobs.main()
    assert [kind for kind, _ in order] == (
        ["completion", "publication"] if outcome in {"Succeeded", "Warning"} else ["completion"])
