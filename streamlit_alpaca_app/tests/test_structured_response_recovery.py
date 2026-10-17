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
