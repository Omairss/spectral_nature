"""Structured protocol recovery and review ordering; no language grading."""
import json
from types import SimpleNamespace

import pytest

from services import aql_zopedia_engine as engine, zopedia_agent


SCHEMA = {"type": "object", "properties": {"body": {"type": "string"},
          "annotation": {"type": "string"}}, "required": ["body", "annotation"]}


@pytest.mark.parametrize("continued", [False, True])
def test_fresh_and_continued_structured_drafts_are_repaired_before_review(monkeypatch, continued):
    client = SimpleNamespace(config=SimpleNamespace(model="protocol-check", provider="local"))
    service = SimpleNamespace(list_capabilities=lambda: {"datasets": {}, "charts": {}})
    calls = []
    repaired = {"body": "retained value", "annotation": "metadata"}
    monkeypatch.setattr(zopedia_agent, "generate_json_via_aql_zopedia_gateway", lambda **kwargs: {"body": "retained value"})

    def repair(**kwargs):
        assert kwargs["llm_client"] is client
        assert json.loads(kwargs["answer_markdown"]) == {"body": "retained value"}
        calls.append("repair")
        return repaired

    def review(**kwargs):
        assert json.loads(kwargs["draft_answer"]) == repaired
        calls.append("review")
        return {"status": "completed", "verdict": "accept",
                "answer_markdown": json.dumps(repaired), "confidence": "medium"}

    monkeypatch.setattr(engine, "_repair_structured_payload_from_agent_answer", repair)
    monkeypatch.setattr(zopedia_agent, "_run_answer_judge", review)
    result = zopedia_agent._run_zopedia_agent_loop(
        query="Return the supplied structured record.", service=service, llm_client=client,
        max_tool_calls=0, persist_findings=False, write_policy="none", response_schema=SCHEMA,
        resume_state={"draft_answer": json.dumps({"body": "retained value"})} if continued else None,
    )
    assert calls == ["repair", "review"]
    assert result["status"] == "completed"
    assert json.loads(result["answer_markdown"]) == repaired


def test_implicit_client_is_preserved_for_shared_structured_recovery(monkeypatch):
    client = object()
    monkeypatch.setattr(engine, "load_aql_zopedia_llm_client", lambda **kwargs: client)

    def agent(**kwargs):
        assert kwargs["llm_client"] is client
        return {"status": "failed", "answer_markdown": json.dumps({"body": "retained value"})}

    def repair(**kwargs):
        assert kwargs["llm_client"] is client
        return {"body": "retained value", "annotation": "metadata"}

    monkeypatch.setattr(engine, "run_aql_zopedia_agent", agent)
    monkeypatch.setattr(engine, "_repair_structured_payload_from_agent_answer", repair)
    result = engine.run_aql_zopedia_structured_agent(
        query="Return the supplied structured record.", schema_name="protocol", schema=SCHEMA,
        task="protocol", surface="test.protocol", max_tool_calls=0,
        persist_findings=False, write_policy="none", review_answer=True,
    )
    assert result["status"] == "completed"
    assert result["agent_result"]["engine"]["structured_repair_used"] is True


@pytest.mark.parametrize("continued", [False, True])
@pytest.mark.parametrize("review_owner", ["agent", "downstream"])
def test_valid_draft_keeps_content_and_review_ownership_without_repair(monkeypatch, continued, review_owner):
    client = SimpleNamespace(config=SimpleNamespace(model="protocol-check", provider="local"))
    service = SimpleNamespace(list_capabilities=lambda: {"datasets": {}, "charts": {}})
    payload = {"body": "retained value", "annotation": "metadata"}
    calls = []

    def synthesize(**kwargs):
        calls.append("synthesize")
        return dict(payload)

    def review(**kwargs):
        assert json.loads(kwargs["draft_answer"]) == payload
        calls.append("review")
        return {"status": "completed", "verdict": "accept", "answer_markdown": kwargs["draft_answer"]}

    def unexpected(**kwargs):
        pytest.fail("Valid structured output must not invoke format repair")

    monkeypatch.setattr(zopedia_agent, "generate_json_via_aql_zopedia_gateway", synthesize)
    monkeypatch.setattr(zopedia_agent, "_run_answer_judge", review)
    monkeypatch.setattr(engine, "_repair_structured_payload_from_agent_answer", unexpected)
    result = zopedia_agent._run_zopedia_agent_loop(
        query="Return the supplied structured record.", service=service, llm_client=client,
        max_tool_calls=0, persist_findings=False, write_policy="none", response_schema=SCHEMA,
        answer_review_owner=review_owner,
        resume_state={"draft_answer": json.dumps(payload)} if continued else None,
    )
    assert calls == ([] if continued else ["synthesize"]) + (["review"] if review_owner == "agent" else [])
    assert result["status"] == "completed"
    assert json.loads(result["answer_markdown"]) == payload


def test_valid_retained_answer_needs_no_model_client(monkeypatch):
    def unexpected(**kwargs):
        pytest.fail("Returning a valid retained answer must not load a client or run inference")

    monkeypatch.setattr(engine, "load_aql_zopedia_llm_client", unexpected)
    monkeypatch.setattr(engine, "run_aql_zopedia_agent", unexpected)
    retained = {"status": "completed", "answer_markdown": json.dumps({"body": "retained value", "annotation": "metadata"})}
    result = engine.run_aql_zopedia_structured_agent(
        query="Return the supplied structured record.", schema_name="protocol", schema=SCHEMA,
        task="protocol", surface="test.protocol", max_tool_calls=0,
        persist_findings=False, write_policy="none", review_answer=True, retained_agent_result=retained,
    )
    assert result["agent_result"] is retained
    assert result["retained_draft_recovered"] is True


@pytest.mark.parametrize("continued", [False, True])
def test_failed_repair_preserves_complete_draft_and_does_not_review(monkeypatch, continued):
    client = SimpleNamespace(config=SimpleNamespace(model="protocol-check", provider="local"))
    service = SimpleNamespace(list_capabilities=lambda: {"datasets": {}, "charts": {}})
    draft = {"body": "retained value"}
    monkeypatch.setattr(zopedia_agent, "generate_json_via_aql_zopedia_gateway", lambda **kwargs: dict(draft))
    monkeypatch.setattr(engine, "_repair_structured_payload_from_agent_answer", lambda **kwargs: None)

    def unexpected(**kwargs):
        pytest.fail("Unrepairable structured output must remain a failed execution")

    monkeypatch.setattr(zopedia_agent, "_run_answer_judge", unexpected)
    result = zopedia_agent._run_zopedia_agent_loop(
        query="Return the supplied structured record.", service=service, llm_client=client,
        max_tool_calls=0, persist_findings=False, write_policy="none", response_schema=SCHEMA,
        resume_state={"draft_answer": json.dumps(draft)} if continued else None,
    )
    assert result["status"] == "failed"
    assert json.loads(result["answer_markdown"]) == draft
    assert json.loads(result["continuation_state"]["draft_answer"]) == draft
    assert result["structured_repair_attempted"] is True


def test_failed_agent_repair_is_not_repeated_by_structured_owner(monkeypatch):
    draft = json.dumps({"body": "retained value"})
    monkeypatch.setattr(engine, "run_aql_zopedia_agent", lambda **kwargs: {
        "status": "failed", "answer_markdown": draft, "structured_repair_attempted": True,
    })

    def unexpected(**kwargs):
        pytest.fail("A failed pre-review format repair must not be repeated after review was skipped")

    monkeypatch.setattr(engine, "_repair_structured_payload_from_agent_answer", unexpected)
    result = engine.run_aql_zopedia_structured_agent(
        query="Return the supplied structured record.", schema_name="protocol", schema=SCHEMA,
        task="protocol", surface="test.protocol", llm_client=object(),
        max_tool_calls=0, persist_findings=False, write_policy="none", review_answer=True,
    )
    assert result["status"] == "failed"
    assert result["payload"] is None
    assert result["agent_result"]["answer_markdown"] == draft
