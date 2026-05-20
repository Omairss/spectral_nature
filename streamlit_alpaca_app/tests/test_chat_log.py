from __future__ import annotations

from services.agents import chat_log


def test_json_list_accepts_postgres_jsonb_decoded_lists():
    assert chat_log._json_list(["research.live_event_evidence"]) == ["research.live_event_evidence"]


def test_json_list_accepts_text_json_lists():
    assert chat_log._json_list('["AAL", "DAL"]') == ["AAL", "DAL"]


def test_json_list_rejects_non_lists():
    assert chat_log._json_list('{"tool": "research"}') == []


def test_json_dict_accepts_postgres_jsonb_decoded_dicts():
    assert chat_log._json_dict({"source": "zopedia"}) == {"source": "zopedia"}


def test_thread_title_compacts_long_titles():
    title = chat_log._thread_title("  " + ("NVDA " * 40), limit=32)

    assert title.endswith("...")
    assert len(title) <= 32
    assert "  " not in title


def test_chat_thread_helpers_noop_without_database(monkeypatch):
    monkeypatch.setattr(chat_log, "_db_connection", lambda: None)

    assert chat_log.create_chat_thread(user_key="u", title="hello") is None
    assert chat_log.append_chat_message(thread_id=None, user_key="u", role="user", content="hello") is None
    assert chat_log.list_chat_threads(user_key="u") == []
    assert chat_log.load_chat_thread(thread_id="missing", user_key="u") is None
