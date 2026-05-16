from __future__ import annotations

from http.client import RemoteDisconnected
import time
from types import SimpleNamespace

import requests
import pandas as pd

from data_access.contracts import QueryRequest, QueryResponse
from services import omnibar_agent


class _StubQueryService:
    def __init__(self) -> None:
        self.calls: list[QueryRequest] = []

    def list_capabilities(self) -> dict[str, dict[str, dict[str, object]]]:
        return {
            "datasets": {
                "fred_dashboard": {
                    "params": ["years", "force_refresh"],
                    "resolution": "materialized_first",
                }
            },
            "charts": {},
        }

    def execute(self, request):
        query = request if isinstance(request, QueryRequest) else QueryRequest.from_dict(request)
        self.calls.append(query)
        return QueryResponse(
            request=query,
            result_type="dataset",
            payload=[
                {
                    "series_id": "CPIAUCSL",
                    "latest_date": "2026-03-01",
                    "latest_value": 319.8,
                    "yoy_pct": 2.9,
                }
            ],
            provenance=None,
        )


class _StubLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="gpt-test")
        self.step_calls = 0

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, schema
        if schema_name == "omnibar_agent_step":
            self.step_calls += 1
            if self.step_calls == 1:
                assert "dataset.fred_dashboard" in user_prompt
                return {
                    "action": "tool_call",
                    "reasoning": "Need the latest inflation dataset.",
                    "tool_name": "dataset.fred_dashboard",
                    "tool_arguments": [
                        {
                            "name": "years",
                            "value_kind": "number",
                            "string_value": None,
                            "number_value": 3,
                            "boolean_value": None,
                            "string_list_value": None,
                        }
                    ],
                    "answer_markdown": "",
                    "confidence": "low",
                    "needs_more_tools": True,
                }
            return {
                "action": "final",
                "reasoning": "Enough evidence collected.",
                "tool_name": "",
                "tool_arguments": [],
                "answer_markdown": "Latest CPI reading is 319.8 with YoY inflation at 2.9%.",
                "confidence": "high",
                "needs_more_tools": False,
            }
        if schema_name == "omnibar_agent_final":
            return {
                "answer_markdown": "Fallback answer.",
                "confidence": "medium",
                "limitations": [],
                "used_tool_call_ids": [],
            }
        raise AssertionError(f"Unexpected schema_name: {schema_name}")


class _FinalSynthesisTransportDropLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="gpt-test")

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, user_prompt, schema
        if schema_name == "omnibar_agent_step":
            return {
                "action": "tool_call",
                "reasoning": "Need one data source before answering.",
                "tool_name": "dataset.fred_dashboard",
                "tool_arguments": [
                    {
                        "name": "years",
                        "value_kind": "number",
                        "string_value": None,
                        "number_value": 1,
                        "boolean_value": None,
                        "string_list_value": None,
                    }
                ],
                "answer_markdown": "",
                "confidence": "low",
                "needs_more_tools": True,
            }
        if schema_name == "omnibar_agent_final":
            raise requests.ConnectionError(
                "Connection aborted.",
                RemoteDisconnected("Remote end closed connection without response"),
            )
        raise AssertionError(f"Unexpected schema_name: {schema_name}")


class _FollowupLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="gpt-test")
        self.prompts: list[str] = []

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, schema
        self.prompts.append(user_prompt)
        if schema_name == "omnibar_agent_step":
            return {
                "action": "final",
                "reasoning": "The prior turn gives the target context.",
                "tool_name": "",
                "tool_arguments": [],
                "answer_markdown": "Continuing the airline thread with more evidence.",
                "confidence": "medium",
                "needs_more_tools": False,
            }
        if schema_name == "omnibar_agent_final":
            return {
                "answer_markdown": "Continuing the airline thread with more evidence.",
                "confidence": "medium",
                "limitations": [],
                "used_tool_call_ids": [],
            }
        raise AssertionError(f"Unexpected schema_name: {schema_name}")


class _SynthesisOnlyLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="gpt-test")
        self.final_prompts: list[str] = []

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, schema
        if schema_name == "omnibar_agent_step":
            raise AssertionError("Planner should be skipped when deterministic bootstrap evidence is enough.")
        if schema_name == "omnibar_agent_final":
            self.final_prompts.append(user_prompt)
            return {
                "answer_markdown": "Vertiv is a data-center infrastructure supplier with current AI power catalysts.",
                "confidence": "high",
                "limitations": [],
                "used_tool_call_ids": [],
            }
        raise AssertionError(f"Unexpected schema_name: {schema_name}")


def test_run_omnibar_agent_uses_shared_tool_registry():
    service = _StubQueryService()
    llm = _StubLLM()

    result = omnibar_agent.run_omnibar_agent(
        query="What was the latest CPI reading?",
        service=service,
        llm_client=llm,
    )

    assert result["status"] == "completed"
    assert "319.8" in result["answer_markdown"]
    assert result["tool_calls"][0]["tool_name"] == "dataset.fred_dashboard"
    assert result["tool_calls"][0]["status"] == "completed"
    assert len(service.calls) == 1
    assert service.calls[0].operation == "dataset"
    assert service.calls[0].name == "fred_dashboard"


def test_run_omnibar_agent_reports_unavailable_llm(monkeypatch):
    monkeypatch.setattr(omnibar_agent, "load_llm_client", lambda **kwargs: None)

    result = omnibar_agent.run_omnibar_agent(
        query="What changed in payrolls?",
        service=_StubQueryService(),
        llm_client=None,
    )

    assert result["status"] == "unavailable"
    assert "cannot run tool-based analysis" in result["answer_markdown"]


def test_run_omnibar_agent_can_skip_persistence(monkeypatch):
    service = _StubQueryService()
    llm = _StubLLM()
    persisted = {"called": False}

    def _fake_persist(**kwargs):
        persisted["called"] = True

    monkeypatch.setattr(omnibar_agent, "_persist_agent_findings", _fake_persist)

    result = omnibar_agent.run_omnibar_agent(
        query="What was the latest CPI reading?",
        service=service,
        llm_client=llm,
        persist_findings=False,
    )

    assert result["status"] == "completed"
    assert persisted["called"] is False


def test_resolve_conversation_followup_query_turns_yes_into_actionable_context():
    resolved, did_resolve = omnibar_agent.resolve_conversation_followup_query(
        "yes",
        [
            {"role": "user", "content": "Tell me about airlines and the Hormuz crisis."},
            {
                "role": "assistant",
                "answer": "Airlines are under pressure because jet fuel is rising. I can verify which carriers are most exposed next.",
            },
        ],
    )

    assert did_resolve is True
    assert "Current user reply:\nyes" in resolved
    assert "Tell me about airlines" in resolved
    assert "jet fuel is rising" in resolved


def test_run_omnibar_agent_resolves_bare_yes_before_planning():
    llm = _FollowupLLM()

    result = omnibar_agent.run_omnibar_agent(
        query="yes",
        service=_StubQueryService(),
        llm_client=llm,
        conversation_history=[
            {"role": "user", "content": "Tell me about airlines and the Hormuz crisis."},
            {
                "role": "assistant",
                "answer": "Airlines are under pressure because jet fuel is rising. I can verify which carriers are most exposed next.",
            },
        ],
        persist_findings=False,
    )

    assert result["status"] == "completed"
    assert result["followup_resolved"] is True
    assert result["original_query"] == "yes"
    assert "Previous user question" in result["query"]
    assert any("Previous assistant answer" in prompt for prompt in llm.prompts)


def test_run_omnibar_agent_bootstraps_obvious_ticker_context(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    def _fake_invoke_tool(*, service, tool_name, arguments=None, run_id=""):
        del service, run_id
        args = dict(arguments or {})
        calls.append((tool_name, args))
        ticker = str(args.get("ticker") or "VRT")
        if tool_name == "investigator.company_context":
            text = f"{ticker}: Vertiv supplies power, cooling, and rack infrastructure for AI data centers."
        elif tool_name == "investigator.recent_news":
            text = f"{ticker}: Recent coverage focuses on AI data-center demand and power constraints."
        else:
            text = f"{tool_name}: retained evidence for {ticker}."
        return {
            "request": {"operation": "research", "name": tool_name, "params": args},
            "result_type": "research",
            "payload": {"llm_context_text": text},
            "provenance": {"mode": "computed", "datasets": [], "details": {}},
            "messages": [],
        }

    monkeypatch.setattr(omnibar_agent, "invoke_tool", _fake_invoke_tool)
    llm = _SynthesisOnlyLLM()

    result = omnibar_agent.run_omnibar_agent(
        query="VRT: what is the company context and latest catalysts?",
        service=_StubQueryService(),
        llm_client=llm,
        persist_findings=False,
    )

    assert result["status"] == "completed"
    assert calls[0][0] == "investigator.company_context"
    assert any(name == "investigator.recent_news" for name, _ in calls)
    assert any("Vertiv supplies power" in prompt for prompt in llm.final_prompts)


def test_bootstrap_multi_ticker_current_query_gets_all_contexts_before_live_evidence():
    catalog = omnibar_agent.build_tool_catalog(_StubQueryService())

    plan = omnibar_agent._bootstrap_tool_plan(
        query="Compare NVDA, AVGO, and TSM around AI capex risks and current market narrative.",
        tool_catalog=catalog,
        force_refresh=False,
        max_calls=4,
    )

    assert plan[:3] == [
        ("investigator.company_context", {"ticker": "NVDA"}),
        ("investigator.company_context", {"ticker": "AVGO"}),
        ("investigator.company_context", {"ticker": "TSM"}),
    ]
    assert plan[3][0] == "research.live_event_evidence"
    assert plan[3][1]["focus_symbols"] == ["NVDA", "AVGO", "TSM"]


def test_attention_home_summary_payload_becomes_llm_context():
    summary = omnibar_agent._summarize_tool_result(
        {
            "result_type": "dataset",
            "payload": {
                "generated_at_utc": "2026-05-05T12:00:00+00:00",
                "coverage_summary": {"candidate_count": 12, "event_count": 2},
                "homepage_summary": {
                    "headline": "Market Summary",
                    "summary_text": "Energy rose while freight carriers sold off.",
                },
                "top_events": [
                    {
                        "event_title": "Oil pressure lifts energy",
                        "surface_summary_text": "USO and BNO rose on shipping risk.",
                        "supporting_symbols": ["USO", "BNO"],
                    }
                ],
            },
            "provenance": None,
        }
    )

    assert "Energy rose while freight carriers sold off" in summary["llm_context_text"]
    assert "USO and BNO rose" in summary["llm_context_text"]


def test_seeded_tool_timeout_emits_heartbeat_and_timeout(monkeypatch):
    original_get_config_param = omnibar_agent.get_config_param

    def _fake_get_config_param(param):
        if param == omnibar_agent._P_TOOL_CALL_TIMEOUT_SECONDS:
            return 1
        return original_get_config_param(param)

    def _slow_invoke_tool(*, service, tool_name, arguments=None, run_id=""):
        del service, tool_name, arguments, run_id
        time.sleep(2)
        return {"payload": {"llm_context_text": "late result"}}

    events: list[dict[str, object]] = []
    monkeypatch.setattr(omnibar_agent, "get_config_param", _fake_get_config_param)
    monkeypatch.setattr(omnibar_agent, "invoke_tool", _slow_invoke_tool)

    ok = omnibar_agent._execute_seeded_tool_call(
        service=_StubQueryService(),
        run_id="run-test",
        tool_calls=[],
        progress_callback=events.append,
        tool_name="research.live_event_evidence",
        arguments={"query": "AI datacenter buildout"},
        progress=0.2,
    )

    assert ok is False
    assert any(event.get("stage") == "tool_timeout" for event in events)


def test_hidden_prefetch_step_timeout_emits_progress():
    events: list[dict[str, object]] = []

    try:
        omnibar_agent._run_hidden_step_with_timeout(
            label="prefetch keyword extraction",
            timeout_seconds=1,
            progress_callback=events.append,
            progress=0.1,
            func=lambda: time.sleep(2),
        )
    except TimeoutError:
        pass
    else:
        raise AssertionError("Expected hidden step timeout")

    assert any(event.get("stage") == "hidden_step_timeout" for event in events)


def test_run_omnibar_agent_bounds_prefetch_and_tool_calls(monkeypatch):
    original_get_config_param = omnibar_agent.get_config_param

    def _fake_get_config_param(param):
        if param == omnibar_agent._P_TOOL_CALL_TIMEOUT_SECONDS:
            return 1
        return original_get_config_param(param)

    def _slow_retained_search(*args, **kwargs):
        del args, kwargs
        time.sleep(2)
        return pd.DataFrame()

    def _fake_invoke_tool(*, service, tool_name, arguments=None, run_id=""):
        del service, run_id
        args = dict(arguments or {})
        if tool_name == "investigator.company_context":
            ticker = str(args.get("ticker") or "TICKER")
            return {"payload": {"llm_context_text": f"{ticker} has infrastructure exposure."}}
        time.sleep(2)
        return {"payload": {"llm_context_text": "late evidence"}}

    import services.saa as saa_module

    events: list[dict[str, object]] = []
    monkeypatch.setattr(omnibar_agent, "get_config_param", _fake_get_config_param)
    monkeypatch.setattr(saa_module, "search_retained_evidence_chunks", _slow_retained_search)
    monkeypatch.setattr(omnibar_agent, "invoke_tool", _fake_invoke_tool)

    started = time.monotonic()
    result = omnibar_agent.run_omnibar_agent(
        query="Are STRL, ECG, and PRIM all related to the AI datacenter buildout?",
        service=_StubQueryService(),
        llm_client=_SynthesisOnlyLLM(),
        progress_callback=events.append,
        persist_findings=False,
    )

    assert time.monotonic() - started < 8
    assert result["status"] == "completed"
    assert any(event.get("stage") == "hidden_step_timeout" for event in events)
    assert any(event.get("stage") == "tool_timeout" for event in events)


def test_final_prompt_lightly_prefers_supporting_sources():
    prompt = omnibar_agent._final_user_prompt(
        query="How are things going to pan out now that there's no agreement in Iran US talks",
        tool_calls=[],
    )

    assert "one or two supporting sources or links" in prompt
    assert "Do not turn the answer into a citation list." in prompt


def test_run_omnibar_agent_sanitizes_transient_transport_drop_after_tools():
    result = omnibar_agent.run_omnibar_agent(
        query="What changed in macro data?",
        service=_StubQueryService(),
        llm_client=_FinalSynthesisTransportDropLLM(),
        max_tool_calls=1,
        persist_findings=False,
    )

    assert result["status"] == "failed"
    assert "Evidence collected" in result["answer_markdown"]
    assert "research or model connection dropped" in result["error"]
    assert "RemoteDisconnected" not in result["error"]
    assert "Connection aborted" not in result["error"]
