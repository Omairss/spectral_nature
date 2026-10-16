"""Transport accounting checks without provider requests or narrative assertions."""
import json
from types import SimpleNamespace

import pytest
import requests

from services import llm, llm_execution, pipeline_store


def test_transport_start_and_completion_share_identity_without_prompt_text(monkeypatch):
    events = []
    monkeypatch.setattr(pipeline_store, "record_provider_attempt", lambda event: events.append(event) or True)
    monkeypatch.setattr(llm_execution, "ensure_provider_available", lambda *a, **kw: None)
    monkeypatch.setattr(llm, "_remaining_request_timeout", lambda _: 1)
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"model":"fixture","usage":{"prompt_tokens":7,"completion_tokens":3}}'
    session = SimpleNamespace(post=lambda **_: response)
    config = SimpleNamespace(provider="fixture", model="fixture", timeout_seconds=1)
    llm_execution.provider_post(config=config, session=session, json={"messages": [{"role": "user", "content": "PRIVATE_PROMPT_MARKER"}]}, timeout=1)
    assert [event["status"] for event in events] == ["started", "success"]
    assert events[0]["attempt_id"] == events[1]["attempt_id"]
    assert events[1]["metadata"]["transport_attempted"] is True
    assert "PRIVATE_PROMPT_MARKER" not in json.dumps(events, default=str)
    assert events[1]["metadata"]["request_composition"]["message_count"] == 1


def test_local_deadline_failure_is_not_reported_as_a_sent_http_request(monkeypatch):
    events, checks = [], []
    monkeypatch.setattr(pipeline_store, "record_provider_attempt", lambda event: events.append(event) or True)
    monkeypatch.setattr(llm_execution, "ensure_provider_available", lambda *a, **kw: None)
    def remaining(_):
        checks.append(1)
        if len(checks) > 1:
            raise TimeoutError("Local deadline expired")
        return 1
    monkeypatch.setattr(llm, "_remaining_request_timeout", remaining)
    session = SimpleNamespace(post=lambda **_: pytest.fail("Transport must not run after its deadline"))
    config = SimpleNamespace(provider="fixture", model="fixture", timeout_seconds=1)
    with pytest.raises(TimeoutError):
        llm_execution.provider_post(config=config, session=session, json={}, timeout=1)
    assert [event["status"] for event in events] == ["started", "not_sent"]
    assert events[1]["metadata"]["transport_attempted"] is False
