from __future__ import annotations

from typing import Any

from services.agents.research_session import ResearchSession, ResearchSessionEvent


class _MemoryStore:
    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.events: list[ResearchSessionEvent] = []
        self.finishes: list[dict[str, Any]] = []
        self.closed = False

    def start(self, **kwargs: Any) -> None:
        self.starts.append(kwargs)

    def append(self, event: ResearchSessionEvent) -> None:
        self.events.append(event)

    def finish(self, **kwargs: Any) -> None:
        self.finishes.append(kwargs)

    def close(self) -> None:
        self.closed = True


class _FailingStore(_MemoryStore):
    def append(self, event: ResearchSessionEvent) -> None:
        del event
        raise RuntimeError("database unavailable")


def test_research_session_records_ordered_sanitized_progress_events():
    store = _MemoryStore()
    visible_events: list[dict[str, Any]] = []
    session = ResearchSession(
        run_id="agrun_test",
        surface="zopedia.chat",
        task="agent_answer",
        query="What changed?",
        provider="deepseek",
        model="deepseek-reasoner",
        store=store,
    )

    callback = session.progress_callback(visible_events.append)
    callback(
        {
            "stage": "planner_reasoning",
            "message": "Checking current evidence.",
            "progress": 0.25,
            "tool_name": "research.search_evidence",
            "reasoning_trace": "private model reasoning",
            "authorization": "Bearer secret",
        }
    )
    session.finish(
        result={
            "status": "completed",
            "model": "deepseek-reasoner",
            "confidence": "medium",
            "answer_markdown": "Grounded answer.",
            "tool_calls": [{"tool_name": "research.search_evidence"}],
        }
    )

    assert [event.sequence for event in store.events] == [1, 2, 3]
    progress_event = store.events[1]
    assert progress_event.stage == "planner_reasoning"
    assert progress_event.payload["tool_name"] == "research.search_evidence"
    assert "reasoning_trace" not in progress_event.payload
    assert "authorization" not in progress_event.payload
    assert "reasoning_trace" not in visible_events[0]
    assert visible_events[0]["message"] == "Planner selected the next research step."
    assert store.finishes[0]["status"] == "completed"
    assert session.runtime_metadata() == {
        "run_id": "agrun_test",
        "event_count": 3,
        "durable_event_log": True,
        "resumable": False,
    }


def test_research_session_persistence_failure_does_not_break_progress():
    store = _FailingStore()
    session = ResearchSession(
        run_id="agrun_failure",
        surface="zopedia.chat",
        task="agent_answer",
        query="Continue despite telemetry failure",
        store=store,
    )

    callback = session.progress_callback(None)
    callback({"stage": "start", "message": "Starting.", "progress": 0.1})
    session.finish(result={"status": "completed", "answer_markdown": "Done."})

    assert session.runtime_metadata()["durable_event_log"] is False
    assert session.runtime_metadata()["event_count"] == 3
