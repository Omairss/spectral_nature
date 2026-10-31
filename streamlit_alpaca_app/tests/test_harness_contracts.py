from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
import requests

from services import llm, zopedia_agent
from services.agents import research_runs


def _review():
    return {"verdict": "accept", "critique_summary": "", "answer_markdown": "answer",
            "answer_points": [], "confidence": "medium", "research_requests": []}


@pytest.mark.parametrize("patch", [{"verdict": None}, {"verdict": "unknown"}, {"confidence": "certain"}, {"research_requests": "none"}, {"answer_markdown": ""}])
def test_invalid_review_never_defaults_to_accept(monkeypatch, patch):
    payload = {**_review(), **patch}
    monkeypatch.setattr(zopedia_agent, "get_prompt", lambda key: "")
    monkeypatch.setattr(zopedia_agent, "generate_json_via_aql_zopedia_gateway", lambda **kwargs: payload)
    review = zopedia_agent._run_answer_judge(
        llm=SimpleNamespace(), run_id="agrun_contract", query="question", tool_calls=[],
        draft_answer="draft", draft_confidence="high", limitations=[], conversation_history=None,
        prefetched_context="", timeout_seconds=2, progress_callback=None,
    )
    assert review["status"] == "failed"
    assert "verdict" not in review


@pytest.mark.parametrize("review", [{"status": "failed"}, {"status": "timeout"}, {"status": "skipped"}, {"status": "completed", "verdict": "insufficient"}, {"status": "completed"}])
def test_unapproved_answer_cannot_start_memory_or_retained_write(monkeypatch, review):
    from services import agents
    writes = []
    monkeypatch.setattr(agents, "log_chat_session", lambda **kwargs: True)
    monkeypatch.setattr(agents, "write_entry", lambda **kwargs: writes.append(kwargs))
    monkeypatch.setattr(zopedia_agent, "_write_back_agent_evidence", lambda **kwargs: pytest.fail("unapproved answer retained"))
    monkeypatch.setattr(zopedia_agent, "generate_json_via_aql_zopedia_gateway", lambda **kwargs: pytest.fail("memory inference started"))
    args = dict(run_id="agrun_contract", query="question", tool_calls=[], limitations=[], service=SimpleNamespace())
    result = zopedia_agent._run_post_answer_memory_agent(
        **args, llm=SimpleNamespace(), answer_markdown="draft", confidence="high", quality_review=review,
        conversation_history=None, prefetched_context="source", timeout_seconds=2, progress_callback=None,
    )
    assert result["reason"] == "answer_not_approved"
    zopedia_agent._persist_agent_findings(**args, status="completed", model="test", answer="draft", confidence="high", quality_review=review)
    assert writes[0]["content"]["answer"] == ""


def test_full_passages_and_source_refs_reach_writer_and_judge():
    passage = "source passage " * 800 + "source tail"
    summary = zopedia_agent._summarize_tool_result({"result_type": "research", "payload": {
        "llm_context_text": passage, "summary": [{"chunk_id": "chunk-1", "title": "Source", "url": "https://example.com/source"}],
    }})
    calls = [{"tool_call_id": "t1", "tool_name": "research.open_page", "status": "completed", "result_summary": summary}]
    assert summary["evidence_context_text"] == passage
    assert passage in zopedia_agent._tool_history_prompt(calls)
    for prompt in [zopedia_agent._final_user_prompt(query="question", tool_calls=calls),
                   zopedia_agent._judge_user_prompt(query="question", tool_calls=calls, draft_answer="draft", draft_confidence="low", limitations=[])]:
        assert passage in prompt
        assert "chunk-1" in prompt
        assert "https://example.com/source" in prompt


@pytest.mark.parametrize("artifact,schema", [
    (_review(), {**zopedia_agent._JUDGE_SCHEMA,
                 "properties": {**zopedia_agent._JUDGE_SCHEMA["properties"], "answer_markdown": {"type": "string"}},
                 "required": [*zopedia_agent._JUDGE_SCHEMA["required"], "answer_markdown"]}),
    ({"decision": "hold"}, {"type": "object", "properties": {"decision": {"type": "string"}},
                            "required": ["decision"], "additionalProperties": False}),
])
def test_evidence_read_then_complete_artifact_needs_no_format_repurchase(monkeypatch, artifact, schema):
    from data_access.query_service import QueryService
    from jsonschema import Draft202012Validator

    service = QueryService.from_environment().with_supplied_evidence({
        "original": {"title": "Original", "metadata": {}, "content": "complete-source-tail"}})
    replies = iter([
        {"decision": {"action": "read", "tool_name": "research.read_supplied_evidence",
                      "tool_arguments": json.dumps({"source_id": "original"})}},
        {"decision": artifact},
    ])
    prompts = []

    def generate(**kwargs):
        reply = next(replies)  # A third purchase is an execution failure.
        assert kwargs["schema"].get("type") == "object"
        assert not list(Draft202012Validator(kwargs["schema"]).iter_errors(reply))
        prompts.append(kwargs["user_prompt"])
        return reply

    monkeypatch.setattr(zopedia_agent, "generate_json_via_aql_zopedia_gateway", generate)
    result = zopedia_agent._run_evidence_model_step(
        llm_client=SimpleNamespace(), service=service, tool_calls=[], context_state={},
        prompt=lambda calls: zopedia_agent._tool_history_prompt(calls, full_evidence=True),
        system_prompt="", schema_name="artifact", schema=schema, surface="test", purpose="test",
        deadline_monotonic=time.monotonic() + 3, metadata={}, progress_callback=None)
    assert result == artifact
    assert len(prompts) == 2
    assert "complete-source-tail" in prompts[1]


@pytest.mark.parametrize("provider", ["deepseek", "openai", "azure_openai", "local"])
@pytest.mark.parametrize("first_attempt", ["repair", "retry", "expired"])
def test_provider_attempts_share_one_remaining_budget(monkeypatch, provider, first_attempt):
    clock = [100.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(llm, "apply_narrative_style", lambda prompt: prompt)
    timeouts = []

    class Session:
        def post(self, url, *, timeout, **kwargs):
            timeouts.append(timeout)
            clock[0] += 11 if first_attempt == "expired" else 6 if len(timeouts) == 1 else 1
            if first_attempt == "retry" and len(timeouts) == 1:
                raise requests.exceptions.ReadTimeout()
            raw = "not JSON" if len(timeouts) == 1 else json.dumps({"ok": True})
            return SimpleNamespace(status_code=200, text="", json=lambda: {"choices": [{"message": {"content": raw}}]})

    config = llm.LLMConfig(provider=provider, api_key="test", model="test", deployment="test", base_url="https://example.com", timeout_seconds=480)
    cls = {"deepseek": llm.DeepSeekChatJSONClient, "openai": llm.OpenAIChatJSONClient,
           "azure_openai": llm.AzureOpenAIChatJSONClient, "local": llm.LocalChatJSONClient}[provider]
    client = cls(config, session=Session())
    if first_attempt == "expired":
        with pytest.raises(llm.LLMDeadlineExceeded):
            with llm.llm_request_deadline(110):
                client.generate_json(system_prompt="", user_prompt="", schema_name="test", schema={})
    else:
        with llm.llm_request_deadline(110):
            assert client.generate_json(system_prompt="", user_prompt="", schema_name="test", schema={}) == {"ok": True}
    assert timeouts == ([10] if first_attempt == "expired" else [10, 4])
    assert llm._REQUEST_DEADLINE.get() is None


def test_public_run_retains_review_state_without_review_prose():
    from api.main import ResearchRunResultResponse
    safe = research_runs._safe_result({"status": "completed", "answer_markdown": "draft", "answer_status": "draft",
                                      "quality_review": {"status": "failed", "reason": "private internal detail"}})
    assert safe["answer_status"] == "draft"
    assert safe["quality_review"] == {"status": "failed", "verdict": ""}
    assert "private internal detail" not in str(safe)
    response = ResearchRunResultResponse(**safe).model_dump()
    assert response["answer_status"] == "draft"
    assert response["quality_review"] == {"status": "failed", "verdict": ""}


def test_tool_model_work_inherits_its_call_deadline(monkeypatch):
    deadlines = []
    def invoke(**kwargs):
        deadlines.append(llm._REQUEST_DEADLINE.get())
        return {"payload": {}}
    monkeypatch.setattr(zopedia_agent, "invoke_tool", invoke)
    start = time.monotonic()
    zopedia_agent._invoke_tool_with_heartbeat(
        service=SimpleNamespace(), tool_name="research.example", arguments={}, run_id="probe",
        progress_callback=None, progress=0, tool_call_id="t1", tool_call_count=0, timeout_seconds=2,
    )
    assert start + 2 <= deadlines[0] <= time.monotonic() + 2
    assert llm._REQUEST_DEADLINE.get() is None


def test_approved_retention_keeps_full_collected_source(monkeypatch):
    from services import agents
    writes = []
    monkeypatch.setattr(agents, "log_chat_session", lambda **kwargs: True)
    monkeypatch.setattr(agents, "write_entry", lambda **kwargs: True)
    monkeypatch.setattr(zopedia_agent, "_write_back_agent_evidence", lambda **kwargs: writes.append(kwargs))
    call = {"tool_call_id":"t1", "tool_name":"research.open_page", "status":"completed", "result_summary":{
        "llm_context_text":"short preview", "evidence_context_text":"complete source including the correction",
        "source_links":[{"url":"https://example.com/source","label":"Source"}],
    }}
    zopedia_agent._persist_agent_findings(
        run_id="probe",query="question",status="completed",model="test",answer="reviewed answer",confidence="medium",
        tool_calls=[call],service=SimpleNamespace(),quality_review={"status":"completed","verdict":"accept"},
    )
    assert "complete source including the correction" in writes[0]["claims"][0]
    assert "https://example.com/source" in writes[0]["claims"][0]


def test_structured_review_preserves_artifact_contract(monkeypatch):
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"], "additionalProperties": False}
    payload = _review()
    payload.pop("answer_markdown")
    payload["answer_payload"] = {"value": 7}
    monkeypatch.setattr(zopedia_agent, "get_prompt", lambda key: "")
    monkeypatch.setattr(zopedia_agent, "generate_json_via_aql_zopedia_gateway", lambda **kwargs: payload)
    review = zopedia_agent._run_answer_judge(
        llm=SimpleNamespace(), run_id="agrun_contract", query="question", tool_calls=[],
        draft_answer='{"value": 6}', draft_confidence="medium", limitations=[], conversation_history=None,
        prefetched_context="", timeout_seconds=2, progress_callback=None, response_schema=schema,
    )
    assert review["status"] == "completed"
    assert json.loads(review["answer_markdown"]) == {"value": 7}
    payload["answer_payload"] = {"value": "wrong type"}
    review = zopedia_agent._run_answer_judge(
        llm=SimpleNamespace(), run_id="agrun_contract", query="question", tool_calls=[],
        draft_answer='{"value": 6}', draft_confidence="medium", limitations=[], conversation_history=None,
        prefetched_context="", timeout_seconds=2, progress_callback=None, response_schema=schema,
    )
    assert review["status"] == "failed"


@pytest.mark.parametrize("review", [
    {"status": "timeout"}, {"status": "failed"}, {"status": "skipped"},
    {"status": "completed", "verdict": "insufficient"},
])
def test_advisory_review_does_not_discard_valid_structured_answer(monkeypatch, review):
    from services import aql_zopedia_engine as engine
    schema = {"type": "object", "properties": {"value": {"type": "integer"}},
              "required": ["value"], "additionalProperties": False}
    agent = {"status": "completed", "answer_markdown": '{"value": 7}', "quality_review": review}
    monkeypatch.setattr(engine, "run_aql_zopedia_agent", lambda **kwargs: agent)
    result = engine.run_aql_zopedia_structured_agent(
        query="question", schema_name="contract", schema=schema,
        task="contract", surface="contract", max_tool_calls=0, review_answer=True, write_policy="none",
    )
    assert result["status"] == "completed"
    assert result["payload"] == {"value": 7}
    assert result["agent_result"]["quality_review"] == review


def test_run_budget_reserves_completion_and_never_restarts(monkeypatch):
    from services.agents import research_session
    clock = [100.0]
    monkeypatch.setattr(research_session.time, "monotonic", lambda: clock[0])
    budget = research_session.ResearchBudget.start(90, 60)
    clock[0] = 146.0
    assert budget.remaining("research") == 0
    assert budget.remaining("synthesis") > 0
    clock[0] = 191.0
    for phase in ("research", "synthesis", "completion"):
        assert budget.remaining(phase) == 0


def test_required_answer_can_use_unused_review_time_within_the_original_window(monkeypatch):
    from services.agents import research_session
    clock = [100.0]
    monkeypatch.setattr(research_session.time, "monotonic", lambda: clock[0])
    budget = research_session.ResearchBudget.start(600, 600)
    clock[0] = 501.0
    assert budget.remaining("research") == 0
    assert budget.remaining("synthesis") == budget.remaining("completion") == 199
    assert budget.step_deadline("synthesis", 600) == 700
    clock[0] = 700.0
    assert budget.remaining("synthesis") == budget.remaining("completion") == 0
    from services.work_scope import WorkScope, WorkCancelled
    clock[0] = 1000.0
    with pytest.raises(WorkCancelled):
        with WorkScope(1100).activate():
            child = research_session.ResearchBudget.start(600, 600, research_required=False)
            assert child.deadline == 1100
            clock[0] = 1100.0
            assert child.remaining("synthesis") == child.remaining("completion") == 0


def test_expired_aql_and_home_steps_never_dispatch():
    from services.aql.pipeline import _call_with_timeout as aql_step, AQLStepTimeout
    from pipeline.jobs.attention_home_build import _call_with_timeout as home_step
    for step, error in ((aql_step, AQLStepTimeout), (home_step, TimeoutError)):
        with pytest.raises(error):
            step("expired", 0, lambda: pytest.fail("expired step dispatched"))
