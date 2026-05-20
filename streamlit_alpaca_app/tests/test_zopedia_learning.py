from __future__ import annotations

import json

from services import zopedia_learning


def _thread_payload() -> dict[str, object]:
    return {
        "thread_id": "zthread_test",
        "user_key": "u",
        "title": "What is the impact of the bond market today?",
        "messages": [
            {"role": "user", "content": "What is the impact of the bond market today?"},
            {
                "role": "assistant",
                "content": "I cannot query actual stocks, so stock data is unavailable.",
                "payload": {
                    "agent_result": {
                        "tool_calls": [
                            {
                                "tool_name": "dataset.daily_movers",
                                "status": "completed",
                                "arguments": {"symbols": ["SPY", "TLT"]},
                                "result_summary": {"row_count": 0},
                            },
                            {
                                "tool_name": "dataset.price_history",
                                "status": "completed",
                                "arguments": {"symbol": "SPY", "days": 5},
                                "result_summary": {"row_count": 3},
                            },
                        ]
                    }
                },
            },
        ],
    }


def test_detect_learning_events_from_payload_captures_rescued_tool_path():
    events = zopedia_learning.detect_learning_events_from_payload(_thread_payload(), user_key="u")

    assert len(events) == 1
    event = events[0]
    assert event["root_cause"] == "premature_unavailable_claim"
    assert event["trigger_type"] == "tool_path_later_succeeded"
    assert event["successful_path"][0]["tool"] == "dataset.price_history"
    assert "primitive evidence paths" in event["evidence_plan"]["cannot_claim"][0]


def test_learning_event_builds_persisted_regression_eval(tmp_path):
    event = zopedia_learning.detect_learning_events_from_payload(_thread_payload(), user_key="u")[0]
    critique = zopedia_learning.critique_learning_event(event)
    case = zopedia_learning.build_regression_eval_case(event, critique)
    path = zopedia_learning.persist_regression_eval_case(case, base_dir=tmp_path)

    payload = json.loads((tmp_path / path.split("/")[-1]).read_text())
    assert payload["learning_event_id"] == event["event_id"]
    assert "dataset.price_history" in payload["required_tool_patterns"]
    assert "stock data is unavailable" in payload["forbidden_answer_patterns"]


def test_build_tool_affordance_update_is_safe_upsert():
    event = zopedia_learning.detect_learning_events_from_payload(_thread_payload(), user_key="u")[0]
    critique = zopedia_learning.critique_learning_event(event)

    update = zopedia_learning.build_tool_affordance_update(event, critique)

    assert update["mutation_type"] == "upsert_pages"
    assert update["pages"][0]["page_type"] == "concept"
    assert event["event_id"] in update["pages"][0]["body_markdown"]


def test_replay_learning_eval_marks_tool_and_answer_contract(monkeypatch, tmp_path):
    from services import aql_zopedia_engine

    def _fake_run_aql_zopedia_agent(**kwargs):
        return {
            "status": "completed",
            "answer_markdown": "Yields bear-steepened; SPY and TLT moves were observed.",
            "tool_calls": [
                {"tool_name": "dataset.yield_curve_facts_1d", "status": "completed"},
                {"tool_name": "dataset.daily_movers", "status": "completed"},
            ],
        }

    monkeypatch.setattr(aql_zopedia_engine, "run_aql_zopedia_agent", _fake_run_aql_zopedia_agent)
    path = tmp_path / "eval.json"
    path.write_text(
        json.dumps(
            {
                "question": "What is the impact of the bond market today?",
                "required_tool_patterns": ["dataset.yield_curve_facts_1d", "dataset.daily_movers"],
                "forbidden_answer_patterns": ["stock data is unavailable", "tool_call_id"],
            }
        )
    )

    result = zopedia_learning.replay_learning_eval(str(path), run_agent=True)

    assert result["status"] == "passed"
    assert result["forbidden_hits"] == []
    assert "dataset.daily_movers" in result["required_tool_hits"]
