from __future__ import annotations

from data_access.query_service import QueryService
from services import agent_tools


def test_build_tool_catalog_uses_typed_param_schemas():
    tools = agent_tools.build_tool_catalog(QueryService(data_access=object()))
    tools_by_name = {tool["name"]: tool for tool in tools}

    assert "query.execute" not in tools_by_name
    assert "research.retained_context" in tools_by_name
    assert "research.live_event_evidence" in tools_by_name
    assert "research.open_page" in tools_by_name
    fred_tool = tools_by_name["dataset.fred_dashboard"]
    price_tool = tools_by_name["dataset.price_history"]

    assert fred_tool["inputSchema"]["additionalProperties"] is False
    assert fred_tool["inputSchema"]["properties"]["years"]["type"] == "integer"
    assert price_tool["inputSchema"]["required"] == ["ticker"]
    assert price_tool["inputSchema"]["properties"]["ticker"]["type"] == "string"


def test_invoke_research_tool_dispatches_to_research_module(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_retained_context(**kwargs):
        captured.update(kwargs)
        return {"summary": [{"label": "Oil lower today"}], "llm_context_text": "matched retained context"}

    monkeypatch.setattr(agent_tools.omnibar_research, "retained_context", _fake_retained_context)

    service = QueryService(data_access=object())
    result = agent_tools.invoke_tool(
        service=service,
        tool_name="research.retained_context",
        arguments={"query": "iran talks", "max_items": 3},
    )

    assert captured["query"] == "iran talks"
    assert captured["max_items"] == 3
    assert result["result_type"] == "research"
    assert result["payload"]["summary"][0]["label"] == "Oil lower today"
