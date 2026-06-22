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


def _arg(name: str, value: object) -> dict[str, object]:
    if isinstance(value, bool):
        return {
            "name": name,
            "value_kind": "boolean",
            "string_value": None,
            "number_value": None,
            "boolean_value": value,
            "string_list_value": None,
            "json_value": None,
            "object_value": None,
            "object_list_value": None,
        }
    if isinstance(value, int | float):
        return {
            "name": name,
            "value_kind": "number",
            "string_value": None,
            "number_value": value,
            "boolean_value": None,
            "string_list_value": None,
            "json_value": None,
            "object_value": None,
            "object_list_value": None,
        }
    if isinstance(value, list):
        return {
            "name": name,
            "value_kind": "string_list",
            "string_value": None,
            "number_value": None,
            "boolean_value": None,
            "string_list_value": value,
            "json_value": None,
            "object_value": None,
            "object_list_value": None,
        }
    return {
        "name": name,
        "value_kind": "string",
        "string_value": str(value),
        "number_value": None,
        "boolean_value": None,
        "string_list_value": None,
        "json_value": None,
        "object_value": None,
        "object_list_value": None,
    }


def test_coerce_tool_arguments_supports_object_list_values():
    args, error = omnibar_agent._coerce_tool_arguments(
        [
            {
                "name": "dataset_refs",
                "value_kind": "object_list",
                "string_value": None,
                "number_value": None,
                "boolean_value": None,
                "string_list_value": None,
                "json_value": None,
                "object_value": None,
                "object_list_value": [
                    {"name": "price_history", "alias": "price_jpm", "params": {"ticker": "JPM", "days": 60}}
                ],
            }
        ]
    )

    assert error == ""
    assert args == {
        "dataset_refs": [{"name": "price_history", "alias": "price_jpm", "params": {"ticker": "JPM", "days": 60}}]
    }


def test_coerce_tool_arguments_accepts_object_kind_with_json_list():
    args, error = omnibar_agent._coerce_tool_arguments(
        [
            {
                "name": "focus_symbols",
                "value_kind": "object",
                "string_value": None,
                "number_value": None,
                "boolean_value": None,
                "string_list_value": None,
                "json_value": ["UMC", "REMX"],
                "object_value": None,
                "object_list_value": None,
            }
        ]
    )

    assert error == ""
    assert args == {"focus_symbols": ["UMC", "REMX"]}


def _judge_accept(answer: str, confidence: str = "medium") -> dict[str, object]:
    return {
        "verdict": "accept",
        "critique_summary": "The draft answer is supported by the collected evidence.",
        "answer_markdown": answer,
        "confidence": confidence,
        "limitations": [],
        "unsupported_claims": [],
        "evidence_gaps": [],
    }


class _StubLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="gpt-test")
        self.step_calls = 0

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, schema
        if schema_name == "zopedia_agent_step":
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
        if schema_name == "zopedia_agent_final":
            return {
                "answer_markdown": "Latest CPI reading is 319.8 with YoY inflation at 2.9%.",
                "confidence": "medium",
                "limitations": [],
                "used_tool_call_ids": [],
            }
        if schema_name == "zopedia_agent_judge":
            return _judge_accept("Latest CPI reading is 319.8 with YoY inflation at 2.9%.", "medium")
        if schema_name == "zopedia_memory_reflection":
            return {
                "action": "no_action",
                "rationale": "The answer used a volatile datapoint and does not require a durable wiki update.",
                "mutation_type": "",
                "proposal_type": "",
                "page_id": "",
                "target_page_id": "",
                "title": "",
                "pages": [],
                "metadata_patch": {},
                "evidence_refs": [],
                "payload": {},
                "allow_risky": False,
            }
        raise AssertionError(f"Unexpected schema_name: {schema_name}")


class _FinalSynthesisTransportDropLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="gpt-test")

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, user_prompt, schema
        if schema_name == "zopedia_agent_step":
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
        if schema_name == "zopedia_agent_final":
            raise requests.ConnectionError(
                "Connection aborted.",
                RemoteDisconnected("Remote end closed connection without response"),
            )
        if schema_name == "zopedia_agent_judge":
            return _judge_accept("Evidence collected before the model connection dropped.", "low")
        raise AssertionError(f"Unexpected schema_name: {schema_name}")


class _FollowupLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="gpt-test")
        self.prompts: list[str] = []

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, schema
        self.prompts.append(user_prompt)
        if schema_name == "zopedia_agent_step":
            return {
                "action": "final",
                "reasoning": "The prior turn gives the target context.",
                "tool_name": "",
                "tool_arguments": [],
                "answer_markdown": "Continuing the airline thread with more evidence.",
                "confidence": "medium",
                "needs_more_tools": False,
            }
        if schema_name == "zopedia_agent_final":
            return {
                "answer_markdown": "Continuing the airline thread with more evidence.",
                "confidence": "medium",
                "limitations": [],
                "used_tool_call_ids": [],
            }
        if schema_name == "zopedia_agent_judge":
            return _judge_accept("Continuing the airline thread with more evidence.", "medium")
        raise AssertionError(f"Unexpected schema_name: {schema_name}")


class _CompanyEvidencePlannerLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="gpt-test")
        self.final_prompts: list[str] = []
        self.step_calls = 0

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, schema
        if schema_name == "zopedia_agent_step":
            self.step_calls += 1
            assert "Evidence contract" in user_prompt or "Evidence contract" in omnibar_agent._planner_system_prompt()
            plan = [
                ("investigator.company_context", [_arg("ticker", "VRT")]),
                ("investigator.fundamentals", [_arg("ticker", "VRT")]),
                ("investigator.recent_news", [_arg("ticker", "VRT"), _arg("days", 30), _arg("limit", 8)]),
                ("research.search_evidence", [_arg("query", "VRT AI data-center power catalysts"), _arg("tickers", ["VRT"]), _arg("max_results", 10)]),
            ]
            if self.step_calls <= len(plan):
                tool_name, tool_arguments = plan[self.step_calls - 1]
                return {
                    "action": "tool_call",
                    "reasoning": "Satisfy the company evidence contract before synthesis.",
                    "tool_name": tool_name,
                    "tool_arguments": tool_arguments,
                    "answer_markdown": "",
                    "confidence": "low",
                    "needs_more_tools": True,
                }
            return {
                "action": "final",
                "reasoning": "Company evidence contract is satisfied.",
                "tool_name": "",
                "tool_arguments": [],
                "answer_markdown": "",
                "confidence": "medium",
                "needs_more_tools": False,
            }
        if schema_name == "zopedia_agent_final":
            self.final_prompts.append(user_prompt)
            return {
                "answer_markdown": "Vertiv is a data-center infrastructure supplier with current AI power catalysts.",
                "confidence": "high",
                "limitations": [],
                "used_tool_call_ids": [],
            }
        if schema_name == "zopedia_agent_judge":
            return _judge_accept(
                "Vertiv is a data-center infrastructure supplier with current AI power catalysts.",
                "high",
            )
        raise AssertionError(f"Unexpected schema_name: {schema_name}")


class _TimeoutToolPlannerLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="gpt-test")
        self.step_calls = 0

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, user_prompt, schema
        if schema_name == "zopedia_agent_step":
            self.step_calls += 1
            if self.step_calls == 1:
                return {
                    "action": "tool_call",
                    "reasoning": "Need live evidence, but the tool may be slow.",
                    "tool_name": "research.live_event_evidence",
                    "tool_arguments": [_arg("query", "AI datacenter buildout"), _arg("max_results", 6)],
                    "answer_markdown": "",
                    "confidence": "low",
                    "needs_more_tools": True,
                }
            return {
                "action": "final",
                "reasoning": "Use collected evidence and limitations.",
                "tool_name": "",
                "tool_arguments": [],
                "answer_markdown": "Evidence collection timed out, but the agent remained bounded.",
                "confidence": "low",
                "needs_more_tools": False,
            }
        if schema_name == "zopedia_agent_final":
            return {
                "answer_markdown": "Evidence collection timed out, but the agent remained bounded.",
                "confidence": "low",
                "limitations": [],
                "used_tool_call_ids": [],
            }
        if schema_name == "zopedia_agent_judge":
            return _judge_accept("Evidence collection timed out, but the agent remained bounded.", "low")
        raise AssertionError(f"Unexpected schema_name: {schema_name}")


class _SingleEvidenceHighConfidenceLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="gpt-test")
        self.step_calls = 0

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, user_prompt, schema
        if schema_name == "zopedia_agent_step":
            self.step_calls += 1
            if self.step_calls == 1:
                return {
                    "action": "tool_call",
                    "reasoning": "Need one dataset.",
                    "tool_name": "dataset.fred_dashboard",
                    "tool_arguments": [_arg("years", 1)],
                    "answer_markdown": "",
                    "confidence": "low",
                    "needs_more_tools": True,
                }
            return {
                "action": "final",
                "reasoning": "Enough for a draft.",
                "tool_name": "",
                "tool_arguments": [],
                "answer_markdown": "A single source supports the direction.",
                "confidence": "high",
                "needs_more_tools": False,
            }
        if schema_name == "zopedia_agent_final":
            return {
                "answer_markdown": "A single source supports the direction.",
                "confidence": "high",
                "limitations": [],
                "used_tool_call_ids": [],
            }
        if schema_name == "zopedia_agent_judge":
            return _judge_accept("A single source supports the direction.", "high")
        raise AssertionError(f"Unexpected schema_name: {schema_name}")


class _JudgeRevisionLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="deepseek-test")
        self.judge_prompts: list[str] = []

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, schema
        if schema_name == "zopedia_agent_step":
            return {
                "action": "final",
                "reasoning": "Drafting before enough evidence.",
                "tool_name": "",
                "tool_arguments": [],
                "answer_markdown": "Yes. This is guaranteed to double next week.",
                "confidence": "high",
                "needs_more_tools": False,
            }
        if schema_name == "zopedia_agent_judge":
            self.judge_prompts.append(user_prompt)
            return {
                "verdict": "insufficient",
                "critique_summary": "The draft makes an unsupported guarantee.",
                "answer_markdown": "I do not have enough collected evidence to support that claim.",
                "confidence": "low",
                "limitations": ["No supporting tool evidence was collected."],
                "unsupported_claims": ["Guaranteed to double next week."],
                "evidence_gaps": ["No fundamentals, price, or current news evidence."],
            }
        raise AssertionError(f"Unexpected schema_name: {schema_name}")


class _MemoryApplyLLM:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="deepseek-test")

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict[str, object]) -> dict[str, object]:
        del system_prompt, schema
        assert schema_name == "zopedia_memory_reflection"
        assert "Evidence references available" in user_prompt
        return {
            "action": "apply_mutation",
            "rationale": "The collected evidence supports a durable page link.",
            "mutation_type": "link_pages",
            "proposal_type": "",
            "page_id": "zpage::nvda",
            "target_page_id": "zpage::ai-capex",
            "title": "",
            "pages": [],
            "metadata_patch": {},
            "evidence_refs": [{"kind": "source_link", "url": "https://example.com/nvda"}],
            "payload": {},
            "allow_risky": False,
        }


def test__run_zopedia_agent_loop_uses_shared_tool_registry(monkeypatch):
    import services.saa as saa_module
    import services.omnibar_research as omnibar_research

    monkeypatch.setattr(saa_module, "search_retained_evidence_chunks", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(omnibar_research, "market_impact_map", lambda *args, **kwargs: {"search_keywords": []})
    service = _StubQueryService()
    llm = _StubLLM()

    result = omnibar_agent._run_zopedia_agent_loop(
        query="What was the latest CPI reading?",
        service=service,
        llm_client=llm,
        persist_findings=False,
    )

    assert result["status"] == "completed"
    assert "319.8" in result["answer_markdown"]
    assert result["tool_calls"][0]["tool_name"] == "dataset.fred_dashboard"
    assert result["tool_calls"][0]["status"] == "completed"
    assert result["aql_evidence_pack_id"].startswith("aqlpack::")
    assert result["aql_evidence_pack"]["schema_version"] == "aql_evidence_pack_v1"
    assert result["aql_evidence_pack"]["trace"][0]["tool_name"] == "dataset.fred_dashboard"
    assert len(service.calls) == 1
    assert service.calls[0].operation == "dataset"
    assert service.calls[0].name == "fred_dashboard"


def test__run_zopedia_agent_loop_caps_high_confidence_with_single_evidence_source(monkeypatch):
    import services.saa as saa_module
    import services.omnibar_research as omnibar_research

    monkeypatch.setattr(saa_module, "search_retained_evidence_chunks", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(omnibar_research, "market_impact_map", lambda *args, **kwargs: {"search_keywords": []})
    result = omnibar_agent._run_zopedia_agent_loop(
        query="What is moving oil today?",
        service=_StubQueryService(),
        llm_client=_SingleEvidenceHighConfidenceLLM(),
        max_tool_calls=1,
        persist_findings=False,
    )

    assert result["status"] == "completed"
    assert result["confidence"] == "medium"
    assert "only one evidence source" in " ".join(result["limitations"]).lower()


def test__run_zopedia_agent_loop_answer_judge_can_revise_unsupported_draft():
    events: list[dict[str, object]] = []
    llm = _JudgeRevisionLLM()

    result = omnibar_agent._run_zopedia_agent_loop(
        query="Will this company double next week?",
        service=_StubQueryService(),
        llm_client=llm,
        max_tool_calls=1,
        progress_callback=events.append,
        persist_findings=False,
    )

    assert result["status"] == "completed"
    assert "not have enough collected evidence" in result["answer_markdown"]
    assert "guaranteed to double" not in result["answer_markdown"].lower()
    assert result["confidence"] == "low"
    assert result["quality_review"]["verdict"] == "insufficient"
    assert result["quality_review"]["revised_answer_applied"] is True
    assert "No supporting tool evidence was collected." in result["limitations"]
    assert any(event.get("stage") == "answer_judge_complete" for event in events)
    assert any("Draft answer" in prompt for prompt in llm.judge_prompts)


def test__run_zopedia_agent_loop_reports_unavailable_llm(monkeypatch):
    captured_kwargs = {}

    def _fake_load(**kwargs):
        captured_kwargs.update(kwargs)
        return None

    monkeypatch.setattr(omnibar_agent, "load_aql_zopedia_llm_client", _fake_load)

    result = omnibar_agent._run_zopedia_agent_loop(
        query="What changed in payrolls?",
        service=_StubQueryService(),
        llm_client=None,
    )

    assert result["status"] == "unavailable"
    assert "cannot run tool-based analysis" in result["answer_markdown"]
    assert captured_kwargs == {"surface": "zopedia.agent"}


def test__run_zopedia_agent_loop_can_skip_persistence(monkeypatch):
    service = _StubQueryService()
    llm = _StubLLM()
    persisted = {"called": False}

    def _fake_persist(**kwargs):
        persisted["called"] = True

    monkeypatch.setattr(omnibar_agent, "_persist_agent_findings", _fake_persist)

    result = omnibar_agent._run_zopedia_agent_loop(
        query="What was the latest CPI reading?",
        service=service,
        llm_client=llm,
        persist_findings=False,
    )

    assert result["status"] == "completed"
    assert persisted["called"] is False


def test_post_answer_memory_agent_applies_safe_mutation(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    def _fake_invoke_tool(*, service, tool_name, arguments=None, run_id=""):
        del service, run_id
        args = dict(arguments or {})
        calls.append((tool_name, args))
        return {
            "result_type": "research",
            "payload": {
                "status": "committed",
                "summary": [
                    {
                        "kind": "zopedia_mutation",
                        "mutation_id": "zmut_1",
                        "summary_text": "link_pages committed.",
                    }
                ],
                "llm_context_text": "Zopedia apply mutation type=link_pages status=committed.",
            },
            "provenance": None,
        }

    events: list[dict[str, object]] = []
    monkeypatch.setattr(omnibar_agent, "invoke_tool", _fake_invoke_tool)

    result = omnibar_agent._run_post_answer_memory_agent(
        llm=_MemoryApplyLLM(),
        service=_StubQueryService(),
        run_id="agrun_test",
        query="How does NVDA connect to AI capex?",
        answer_markdown="NVDA is tied to AI capex through accelerator demand.",
        confidence="high",
        limitations=[],
        quality_review={"status": "completed", "verdict": "accept"},
        tool_calls=[
            {
                "tool_call_id": "agtc_1",
                "tool_name": "research.live_event_evidence",
                "arguments": {"query": "NVDA AI capex"},
                "status": "completed",
                "result_summary": {
                    "llm_context_text": "Source-backed NVDA AI capex evidence.",
                    "source_links": [{"url": "https://example.com/nvda", "label": "NVDA evidence"}],
                },
            }
        ],
        conversation_history=None,
        prefetched_context="",
        timeout_seconds=5,
        progress_callback=events.append,
    )

    assert result["status"] == "completed"
    assert result["action"] == "apply_mutation"
    assert result["tool_call"]["tool_name"] == "zopedia.apply_mutation"
    assert calls == [
        (
            "zopedia.apply_mutation",
            {
                "mutation_type": "link_pages",
                "page_id": "zpage::nvda",
                "target_page_id": "zpage::ai-capex",
                "pages": [],
                "metadata_patch": {},
                "evidence_refs": [{"kind": "source_link", "url": "https://example.com/nvda"}],
                "rationale": "The collected evidence supports a durable page link.",
                "payload": {},
                "allow_risky": False,
            },
        )
    ]
    assert any(event.get("stage") == "memory_reflection_start" for event in events)
    assert any(event.get("stage") == "memory_mutation_complete" for event in events)


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


def test_resolve_conversation_followup_query_turns_contextual_question_into_actionable_context():
    resolved, did_resolve = omnibar_agent.resolve_conversation_followup_query(
        "What about geopolitics?",
        [
            {"role": "user", "content": "What is happening with oil today? Why did it fall?"},
            {
                "role": "assistant",
                "answer": "Oil fell today, but the live news search timed out and no catalyst was confirmed.",
            },
        ],
    )

    assert did_resolve is True
    assert "contextual follow-up" in resolved
    assert "What is happening with oil today" in resolved
    assert "What about geopolitics?" in resolved


def test__run_zopedia_agent_loop_resolves_bare_yes_before_planning():
    llm = _FollowupLLM()

    result = omnibar_agent._run_zopedia_agent_loop(
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


def test__run_zopedia_agent_loop_planner_uses_company_evidence_contract(monkeypatch):
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
    llm = _CompanyEvidencePlannerLLM()

    result = omnibar_agent._run_zopedia_agent_loop(
        query="VRT: what is the company context and latest catalysts?",
        service=_StubQueryService(),
        llm_client=llm,
        max_tool_calls=5,
        persist_findings=False,
    )

    assert result["status"] == "completed"
    assert calls[0][0] == "investigator.company_context"
    assert any(name == "investigator.fundamentals" for name, _ in calls)
    assert any(name == "investigator.recent_news" for name, _ in calls)
    assert any(name == "research.search_evidence" for name, _ in calls)
    assert any("Vertiv supplies power" in prompt for prompt in llm.final_prompts)


def test_bootstrap_plan_no_longer_uses_hardcoded_query_routing():
    catalog = omnibar_agent.build_tool_catalog(_StubQueryService())

    plan = omnibar_agent._bootstrap_tool_plan(
        query="Compare NVDA, AVGO, and TSM around AI capex risks and current market narrative.",
        tool_catalog=catalog,
        force_refresh=False,
        max_calls=4,
    )

    assert plan == []


def test_planner_prompt_contains_generic_evidence_contract():
    prompt = omnibar_agent._planner_system_prompt()

    assert "Evidence contract" in prompt
    assert "For public company or ticker questions" in prompt
    assert "For macroeconomic questions" in prompt
    assert "For market-impact questions" in prompt
    assert "observed market data" in prompt
    assert "analysis.run_python fails" in prompt
    assert "Treat user-supplied claims as hypotheses" in prompt


def test_analysis_failure_requires_one_repair_pass_before_final():
    tool_calls = [
        {
            "tool_name": "analysis.run_python",
            "status": "completed",
            "result_summary": {
                "llm_context_text": "Analysis status: rejected\nFailure category: analysis_code_error",
                "preview": {"kind": "object"},
            },
        }
    ]

    assert omnibar_agent._analysis_failure_needs_repair(tool_calls) is True
    tool_calls.append(
        {
            "tool_name": "analysis.run_python",
            "status": "completed",
            "result_summary": {
                "llm_context_text": "Analysis status: rejected\nFailure category: analysis_code_error",
                "preview": {"kind": "object"},
            },
        }
    )
    assert omnibar_agent._analysis_failure_needs_repair(tool_calls) is False


def test_bootstrap_skip_never_bypasses_llm_planner():
    tool_calls = [
        {
            "tool_name": "investigator.company_context",
            "status": "completed",
            "result_summary": {"preview_text": "NVDA designs accelerators."},
        },
        {
            "tool_name": "investigator.fundamentals",
            "status": "completed",
            "result_summary": {"preview_text": "Quarterly fundamentals for NVDA: income, balance, cashflow."},
        },
    ]

    assert (
        omnibar_agent._should_skip_planner_after_bootstrap(
            "NVDA: tell me about the company.",
            tool_calls,
            {"investigator.company_context"},
        )
        is False
    )


def test_final_prompt_treats_user_premise_as_unverified():
    prompt = omnibar_agent._final_user_prompt(
        query="NVDA revenue collapsed 90% last quarter. Explain why.",
        tool_calls=[],
    )

    assert "Treat the user's premise as unverified" in prompt
    assert "if evidence contradicts the premise" in prompt


def test_attention_home_top_events_become_llm_context():
    summary = omnibar_agent._summarize_tool_result(
        {
            "result_type": "dataset",
            "payload": {
                "generated_at_utc": "2026-05-05T12:00:00+00:00",
                "coverage_summary": {"candidate_count": 12, "event_count": 2},
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

    assert "USO and BNO rose" in summary["llm_context_text"]


def test_tool_summary_preserves_evidence_refs_for_aql_pack():
    summary = omnibar_agent._summarize_tool_result(
        {
            "result_type": "research",
            "payload": {
                "summary": [
                    {
                        "chunk_record_id": "saa_chunk::abc",
                        "canonical_document_id": "saa_doc::abc",
                        "document_id": "doc::abc",
                        "chunk_id": "chunk::abc",
                        "title": "Diesel pinch point",
                        "source": "Energy Aspects",
                        "published_date": "2026-05-15",
                        "url": "https://example.com/diesel",
                        "chunk_text": "Diesel inventories are tight.",
                    }
                ],
            },
            "provenance": None,
        }
    )

    assert summary["evidence_refs"][0]["chunk_record_id"] == "saa_chunk::abc"
    assert summary["evidence_refs"][0]["canonical_document_id"] == "saa_doc::abc"
    assert summary["source_links"][0]["url"] == "https://example.com/diesel"


def test_tool_summary_does_not_promote_internal_eval_urls_to_citations():
    summary = omnibar_agent._summarize_tool_result(
        {
            "result_type": "research",
            "payload": {
                "summary": [
                    {
                        "title": "Supply Pressure",
                        "url": "https://eval.local/zopedia/run/supply-pressure",
                    },
                    {
                        "title": "Public evidence",
                        "url": "https://example.com/public-evidence",
                    },
                ],
            },
            "provenance": None,
        }
    )

    assert summary["evidence_refs"][0]["url"] == "https://eval.local/zopedia/run/supply-pressure"
    assert [item["url"] for item in summary["source_links"]] == ["https://example.com/public-evidence"]


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


def test__run_zopedia_agent_loop_bounds_prefetch_and_tool_calls(monkeypatch):
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
    result = omnibar_agent._run_zopedia_agent_loop(
        query="Are STRL, ECG, and PRIM all related to the AI datacenter buildout?",
        service=_StubQueryService(),
        llm_client=_TimeoutToolPlannerLLM(),
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


def test__run_zopedia_agent_loop_sanitizes_transient_transport_drop_after_tools():
    result = omnibar_agent._run_zopedia_agent_loop(
        query="What changed in macro data?",
        service=_StubQueryService(),
        llm_client=_FinalSynthesisTransportDropLLM(),
        max_tool_calls=1,
        persist_findings=False,
    )

    assert result["status"] == "failed"
    assert result["answer_markdown"] == ""
    assert result["tool_calls"]
    assert "research or model connection dropped" in result["error"]
    assert "RemoteDisconnected" not in result["error"]
    assert "Connection aborted" not in result["error"]


def test_summarize_tool_result_exposes_analysis_render_payload():
    summary = omnibar_agent._summarize_tool_result(
        {
            "result_type": "analysis_result",
            "payload": {
                "analysis_run_id": "zopedia_analysis::test",
                "status": "succeeded",
                "objective": "row count",
                "metrics": [{"name": "rows", "value": 3}],
                "tables": [{"name": "preview", "rows": [{"x": 1}], "row_count": 1, "columns": ["x"]}],
                "charts": [],
                "artifacts": [],
                "llm_context_text": "Zopedia analysis run zopedia_analysis::test succeeded.",
            },
            "provenance": {"mode": "computed", "datasets": ["saa_zopedia_analysis_runs"]},
        }
    )

    assert summary["render_payload"]["kind"] == "analysis_result"
    assert summary["render_payload"]["analysis"]["analysis_run_id"] == "zopedia_analysis::test"
    assert "Zopedia analysis run" in summary["llm_context_text"]


def test_tool_summary_omits_raw_text_from_generic_object_preview():
    raw_text = "\n".join(f"raw line {idx}" for idx in range(100))
    summary = omnibar_agent._summarize_tool_result(
        {
            "result_type": "dataset",
            "payload": {"title": "Retained document", "raw_text": raw_text},
            "provenance": {"mode": "computed", "datasets": ["saa_documents"]},
        }
    )

    assert "Retained document" in summary["preview_text"]
    assert "raw line 99" not in summary["preview_text"]
    assert "raw text omitted" in summary["preview_text"]


def test_tool_summary_includes_empty_result_messages_for_planner():
    summary = omnibar_agent._summarize_tool_result(
        {
            "result_type": "dataset",
            "payload": [],
            "provenance": {
                "mode": "on_demand",
                "datasets": ["event_significance"],
                "details": {"empty_reason": "insufficient_observations"},
            },
            "messages": [
                "The event study did not have enough observations after the event date for a significance test.",
                "Next tool hint: Use dataset.price_history or event-window returns when significance windows are too short.",
            ],
        }
    )

    assert "Tool messages:" in summary["llm_context_text"]
    assert "not have enough observations" in summary["llm_context_text"]
    assert "dataset.price_history" in summary["llm_context_text"]


def test_tool_summary_keeps_small_market_baskets_visible():
    rows = [{"symbol": f"SYM{i}", "change_pct": i} for i in range(1, 7)]
    summary = omnibar_agent._summarize_tool_result(
        {
            "result_type": "dataset",
            "payload": rows,
            "provenance": {"mode": "on_demand", "datasets": ["daily_movers"], "details": {}},
            "messages": [],
        }
    )

    assert "SYM1" in summary["preview_text"]
    assert "SYM6" in summary["preview_text"]


def test_tool_summary_builds_long_term_context_from_momentum_profiles():
    summary = omnibar_agent._summarize_tool_result(
        {
            "request": {"operation": "dataset", "name": "momentum_profiles", "params": {}},
            "result_type": "dataset",
            "payload": [
                {
                    "symbol": "APLD",
                    "close": 46.88,
                    "return_1w_pct": 10.2,
                    "return_1m_pct": 31.0,
                    "return_3m_pct": 49.7,
                    "return_1y_pct": 514.2,
                    "sparkline_3m": [1.0, 2.0, 3.0],
                }
            ],
            "provenance": {"mode": "materialized", "datasets": ["momentum_profiles"], "details": {}},
            "messages": [],
        }
    )

    assert "Momentum profiles returned 1 row" in summary["llm_context_text"]
    assert "APLD" in summary["llm_context_text"]
    assert "1Y=514.2%" in summary["llm_context_text"]


def test_tool_summary_builds_price_history_window_context():
    summary = omnibar_agent._summarize_tool_result(
        {
            "request": {"operation": "dataset", "name": "price_history", "params": {"ticker": "APLD"}},
            "result_type": "dataset",
            "payload": [
                {"symbol": "APLD", "timestamp": "2025-05-22T04:00:00+00:00", "close": 7.47},
                {"symbol": "APLD", "timestamp": "2025-05-23T04:00:00+00:00", "close": 7.35},
                {"symbol": "APLD", "timestamp": "2026-05-21T04:00:00+00:00", "close": 46.88},
            ],
            "provenance": {"mode": "materialized", "datasets": ["price_history"], "details": {}},
            "messages": [],
        }
    )

    assert "Price history returned 3 row" in summary["llm_context_text"]
    assert "from 2025-05-22 to 2026-05-21" in summary["llm_context_text"]
    assert "return=527.6%" in summary["llm_context_text"]


def test_market_impact_recovery_requires_observed_market_data_before_final():
    tool_catalog = [
        {"name": "research.market_impact_map"},
        {"name": "dataset.daily_movers"},
    ]

    recovery = omnibar_agent._market_impact_recovery_tool(
        query="What is the impact of the bond market today?",
        answer="Long rates are rising and equities may be pressured.",
        tool_calls=[],
        tool_catalog=tool_catalog,
    )

    assert recovery is not None
    assert recovery[0] == "research.market_impact_map"


def test_market_impact_recovery_uses_impact_map_symbols_for_daily_movers():
    tool_catalog = [
        {"name": "research.market_impact_map"},
        {"name": "dataset.daily_movers"},
    ]
    tool_calls = [
        {
            "tool_call_id": "agtc_1",
            "tool_name": "research.market_impact_map",
            "arguments": {"query": "bond market impact"},
            "status": "completed",
            "result_summary": {
                "preview": {
                    "sample": [
                        {"symbol": "SPY", "role": "broad equity benchmark"},
                        {"symbol": "QQQ", "role": "growth benchmark"},
                    ]
                },
                "llm_context_text": "Likely impacted symbols to check next: SPY, QQQ.",
            },
        }
    ]

    recovery = omnibar_agent._market_impact_recovery_tool(
        query="What is the impact of the bond market today?",
        answer="Equities may be pressured by higher long rates.",
        tool_calls=tool_calls,
        tool_catalog=tool_catalog,
    )

    assert recovery is not None
    assert recovery[0] == "dataset.daily_movers"
    assert recovery[1]["symbols"] == ["SPY", "QQQ"]
