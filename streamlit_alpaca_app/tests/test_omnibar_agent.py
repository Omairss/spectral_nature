from __future__ import annotations

from types import SimpleNamespace

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
    monkeypatch.setattr(omnibar_agent, "load_llm_client", lambda: None)

    result = omnibar_agent.run_omnibar_agent(
        query="What changed in payrolls?",
        service=_StubQueryService(),
        llm_client=None,
    )

    assert result["status"] == "unavailable"
    assert "cannot run tool-based analysis" in result["answer_markdown"]


def test_final_prompt_lightly_prefers_supporting_sources():
    prompt = omnibar_agent._final_user_prompt(
        query="How are things going to pan out now that there's no agreement in Iran US talks",
        tool_calls=[],
    )

    assert "one or two supporting sources or links" in prompt
    assert "Do not turn the answer into a citation list." in prompt
