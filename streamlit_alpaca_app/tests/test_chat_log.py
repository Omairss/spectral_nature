from __future__ import annotations

from services.agents import chat_log


def test_json_list_accepts_postgres_jsonb_decoded_lists():
    assert chat_log._json_list(["research.live_event_evidence"]) == ["research.live_event_evidence"]


def test_json_list_accepts_text_json_lists():
    assert chat_log._json_list('["AAL", "DAL"]') == ["AAL", "DAL"]


def test_json_list_rejects_non_lists():
    assert chat_log._json_list('{"tool": "research"}') == []
