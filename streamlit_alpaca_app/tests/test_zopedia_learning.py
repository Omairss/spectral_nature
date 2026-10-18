from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from data_access.query_service import QueryService
from services import aql_zopedia_engine, llm, zopedia_learning


def _thread():
    return {"thread_id": "thread", "user_key": "owner", "messages": [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Answer"},
    ]}


def test_episode_identity_tracks_owner_messages_and_original_execution():
    thread = _thread()
    original = deepcopy(thread)
    episode = zopedia_learning.detect_learning_events_from_payload(thread)[0]
    assert thread == original
    assert episode == zopedia_learning.detect_learning_events_from_payload(thread)[0]
    assert episode["event_id"] != zopedia_learning.detect_learning_events_from_payload(thread, user_key="another-owner")[0]["event_id"]
    thread["messages"].append({"role": "user", "content": "Correction"})
    assert episode["event_id"] != zopedia_learning.detect_learning_events_from_payload(thread)[0]["event_id"]
    assert episode["event_id"] != zopedia_learning.detect_learning_events_from_payload(original, run_payloads=[{"run_id": "attempt", "status": "failed"}])[0]["event_id"]
    assert zopedia_learning.detect_learning_events_from_payload({"messages": [{"role": "user", "content": "Question"}]}) == []


def _critic_dependencies(monkeypatch, operation):
    monkeypatch.setattr(QueryService, "from_environment", classmethod(lambda cls: cls(data_access=None)))
    monkeypatch.setattr(aql_zopedia_engine, "research_engine_revision", lambda: "execution-contract")
    monkeypatch.setattr(aql_zopedia_engine, "run_aql_zopedia_structured_agent", operation)
    monkeypatch.setattr(llm, "get_active_narrative_style_rule", lambda: "frozen-style")
    return SimpleNamespace(config=SimpleNamespace(provider="transport", model="model"))


def test_completed_critique_reuses_exact_inputs_and_invalidates_changed_evidence(monkeypatch):
    calls = []
    payload = {"proposed_change_type": "no_change", "root_cause": ""}
    def operation(**kwargs):
        calls.append(kwargs)
        return {"status": "completed", "payload": payload}
    client = _critic_dependencies(monkeypatch, operation)
    thread = _thread()
    event = zopedia_learning.detect_learning_events_from_payload(thread)[0]
    pending = []
    result = zopedia_learning.critique_learning_event(event, thread, [], client, on_pending=lambda: pending.append(True))
    assert result["_execution"]["status"] == "completed"
    assert result["root_cause"] == payload["root_cause"]
    repeated = zopedia_learning.critique_learning_event(event, thread, [], client, previous=result, on_pending=lambda: pending.append(True))
    assert repeated["_reused"] is True
    assert len(calls) == len(pending) == 1
    thread["messages"].append({"role": "user", "content": "Additional evidence"})
    changed_event = zopedia_learning.detect_learning_events_from_payload(thread)[0]
    zopedia_learning.critique_learning_event(changed_event, thread, [], client, previous=result)
    assert len(calls) == 2
    assert calls[-1]["write_policy"] == "none"
    assert calls[-1]["request_context"]["allowed_source_access"] == ["supplied"]


def test_transport_failure_remains_pending_and_is_retried(monkeypatch):
    calls = []
    def operation(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError("transport timeout")
        return {"status": "completed", "payload": {"proposed_change_type": "no_change"}}
    client = _critic_dependencies(monkeypatch, operation)
    thread = _thread()
    event = zopedia_learning.detect_learning_events_from_payload(thread)[0]
    pending = []
    failed = zopedia_learning.critique_learning_event(event, thread, [], client, on_pending=lambda: pending.append(True))
    assert failed["_execution"]["status"] == "pending"
    assert "error" in failed["_execution"]
    assert "proposed_change_type" not in failed
    recovered = zopedia_learning.critique_learning_event(event, thread, [], client, previous=failed, on_pending=lambda: pending.append(True))
    assert recovered["_execution"]["status"] == "completed"
    assert len(calls) == len(pending) == 2
