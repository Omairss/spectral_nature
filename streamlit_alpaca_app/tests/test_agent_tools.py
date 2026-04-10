from __future__ import annotations

from data_access.query_service import QueryService
from services import agent_tools


def test_build_tool_catalog_uses_typed_param_schemas():
    tools = agent_tools.build_tool_catalog(QueryService(data_access=object()))
    tools_by_name = {tool["name"]: tool for tool in tools}

    assert "query.execute" not in tools_by_name
    fred_tool = tools_by_name["dataset.fred_dashboard"]
    price_tool = tools_by_name["dataset.price_history"]

    assert fred_tool["inputSchema"]["additionalProperties"] is False
    assert fred_tool["inputSchema"]["properties"]["years"]["type"] == "integer"
    assert price_tool["inputSchema"]["required"] == ["ticker"]
    assert price_tool["inputSchema"]["properties"]["ticker"]["type"] == "string"
