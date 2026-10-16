"""Transport, ownership and continuation checks; no narrative quality assertions."""
from dataclasses import replace

import pytest

from data_access.query_service import QueryService
from data_access.contracts import QueryValidationError
from services.zopedia_learning import critique_learning_event
from services.agents.research_session import research_request_context
from services.agent_tools import build_tool_registry


def test_original_continuation_and_json_pointer():
    original = "αβγ" * 30000
    service = QueryService(data_access=None).with_supplied_evidence({
        "record": {"title": "Original", "content": {"a/b": {"~key": original}}},
    })
    text, offset = "", 0
    while True:
        page = service.read_supplied_evidence(source_id="record", pointer="/a~1b/~0key", offset=offset, max_characters=10001)
        text += page["text"]
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert text == original


def test_catalog_pagination_reaches_every_source_and_keeps_scope():
    records = {f"id:{n}": {"title": str(n), "metadata": {"category": "odd" if n % 2 else "even"}, "content": [n]} for n in range(217)}
    service = QueryService(data_access=None).with_supplied_evidence(records)
    ids, offset = [], 0
    while True:
        page = service.list_supplied_evidence(offset=offset, limit=100)
        ids.extend(item["source_id"] for item in page["sources"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert ids == list(records)
    assert service.list_supplied_evidence(terms=["odd"])["total_matches"] == 108
    assert replace(service, data_access=None).supplied_evidence is service.supplied_evidence
    other = QueryService(data_access=None)
    with pytest.raises(QueryValidationError):
        other.read_supplied_evidence(source_id="id:216")


def test_fingerprint_includes_originals_beyond_first_read_window():
    common = "x" * 100000
    first = QueryService(data_access=None).with_supplied_evidence({"s": {"content": common + "first"}})
    second = QueryService(data_access=None).with_supplied_evidence({"s": {"content": common + "second"}})
    assert first.supplied_evidence_manifest["sha256"] != second.supplied_evidence_manifest["sha256"]


def test_source_permissions_and_task_context_survive_the_shared_boundary():
    context = research_request_context("Review supplied records", {
        "allow_web": False, "allowed_source_access": ["supplied"], "task_context": {"premise": "original task"},
    })
    assert context["allowed_source_access"] == ["supplied"]
    assert context["task_context"] == {"premise": "original task"}
    with pytest.raises(ValueError):
        research_request_context("x", {"allowed_source_access": ["arbitrary"]})
    service = QueryService(data_access=None).with_supplied_evidence({"s": {"content": "original"}})
    catalog = build_tool_registry(service).catalog()
    assert {tool["name"] for tool in catalog if tool["source_access"] == "supplied"} == {
        "research.list_supplied_evidence", "research.read_supplied_evidence",
    }


def test_unavailable_critic_has_no_fabricated_diagnosis_or_confidence():
    result = critique_learning_event({"event_id": "episode", "original_question": "Inspect the recorded failure"}, llm_client=None)
    assert result["_execution"]["status"] == "pending"
    assert "confidence" not in result and "failed_assumption" not in result


def test_compatible_completed_critic_is_reused_without_model_call():
    event = {"event_id": "episode", "original_question": "Inspect the recorded failure"}
    pending = critique_learning_event(event, llm_client=None)
    previous = {"event_id": "episode", "_execution": {**pending["_execution"], "status": "completed"}}
    # Same unavailable model identity and identical originals must reuse the stored result.
    reused = critique_learning_event(event, llm_client=None, previous=previous)
    assert reused["_reused"] is True
    assert critique_learning_event({**event, "original_question": "Changed premise"}, llm_client=None, previous=previous)["_execution"]["status"] == "pending"
