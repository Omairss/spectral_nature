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
    assert "zopedia.search_pages" in tools_by_name
    assert "zopedia.read_source" in tools_by_name
    assert "zopedia.sources_for_page" in tools_by_name
    assert "zopedia.trace_to_evidence" in tools_by_name
    assert "zopedia.ingest_youtube" in tools_by_name
    assert "zopedia.list_mutations" in tools_by_name
    assert "zopedia.list_maintenance_reports" in tools_by_name
    assert "zopedia.apply_mutation" in tools_by_name
    assert "zopedia.rollback_mutation" in tools_by_name
    assert "analysis.run_python" in tools_by_name
    fred_tool = tools_by_name["dataset.fred_dashboard"]
    price_tool = tools_by_name["dataset.price_history"]
    analysis_tool = tools_by_name["analysis.run_python"]

    assert fred_tool["inputSchema"]["additionalProperties"] is False
    assert fred_tool["inputSchema"]["properties"]["years"]["type"] == "integer"
    assert price_tool["inputSchema"]["required"] == ["ticker"]
    assert price_tool["inputSchema"]["properties"]["ticker"]["type"] == "string"
    assert analysis_tool["inputSchema"]["required"] == ["objective", "code"]


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


def test_invoke_zopedia_tool_dispatches_to_research_module(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_search_pages(**kwargs):
        captured.update(kwargs)
        return {"summary": [{"title": "AI Power", "page_id": "zopedia::theme::ai-power"}]}

    monkeypatch.setattr(agent_tools.omnibar_research, "zopedia_search_pages", _fake_search_pages)

    service = QueryService(data_access=object())
    result = agent_tools.invoke_tool(
        service=service,
        tool_name="zopedia.search_pages",
        arguments={"query": "ai power", "max_results": 4},
    )

    assert captured["query"] == "ai power"
    assert captured["max_results"] == 4
    assert result["request"]["operation"] == "zopedia"
    assert result["payload"]["summary"][0]["title"] == "AI Power"


def test_invoke_zopedia_source_tool_dispatches_to_research_module(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_sources_for_page(**kwargs):
        captured.update(kwargs)
        return {"summary": [{"title": "AI power memo", "page_id": "zopedia::source::ai-power"}]}

    monkeypatch.setattr(agent_tools.omnibar_research, "zopedia_sources_for_page", _fake_sources_for_page)

    service = QueryService(data_access=object())
    result = agent_tools.invoke_tool(
        service=service,
        tool_name="zopedia.sources_for_page",
        arguments={"page_id": "zopedia::theme::ai-power"},
    )

    assert captured["page_id"] == "zopedia::theme::ai-power"
    assert result["request"]["operation"] == "zopedia"
    assert result["payload"]["summary"][0]["title"] == "AI power memo"


def test_invoke_zopedia_read_source_tool_dispatches_to_research_module(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_read_source(**kwargs):
        captured.update(kwargs)
        return {"summary": [{"title": "AI power memo", "ref": "saa_chunk::ai-power"}]}

    monkeypatch.setattr(agent_tools.omnibar_research, "zopedia_read_source", _fake_read_source)

    service = QueryService(data_access=object())
    result = agent_tools.invoke_tool(
        service=service,
        tool_name="zopedia.read_source",
        arguments={"ref": "saa_chunk::ai-power", "kind": "retained_evidence_chunk"},
    )

    assert captured["ref"] == "saa_chunk::ai-power"
    assert captured["kind"] == "retained_evidence_chunk"
    assert result["request"]["operation"] == "zopedia"
    assert result["payload"]["summary"][0]["title"] == "AI power memo"


def test_invoke_zopedia_list_mutations_tool_dispatches_to_research_module(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_list_mutations(**kwargs):
        captured.update(kwargs)
        return {"summary": [{"title": "ingest", "mutation_id": "zopedia_mutation::ingest_source::abc"}]}

    monkeypatch.setattr(agent_tools.omnibar_research, "zopedia_list_mutations", _fake_list_mutations)

    service = QueryService(data_access=object())
    result = agent_tools.invoke_tool(
        service=service,
        tool_name="zopedia.list_mutations",
        arguments={"status": "committed", "mutation_type": "ingest_source", "max_results": 3},
    )

    assert captured["status"] == "committed"
    assert captured["mutation_type"] == "ingest_source"
    assert captured["max_results"] == 3
    assert result["request"]["operation"] == "zopedia"
    assert result["payload"]["summary"][0]["mutation_id"] == "zopedia_mutation::ingest_source::abc"


def test_invoke_zopedia_rollback_mutation_tool_dispatches_to_research_module(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_rollback_mutation(**kwargs):
        captured.update(kwargs)
        return {"summary": [{"mutation_id": "zopedia_mutation::rollback::abc", "status": "rolled_back"}]}

    monkeypatch.setattr(agent_tools.omnibar_research, "zopedia_rollback_mutation", _fake_rollback_mutation)

    service = QueryService(data_access=object())
    result = agent_tools.invoke_tool(
        service=service,
        tool_name="zopedia.rollback_mutation",
        arguments={"mutation_id": "zopedia_mutation::ingest_source::abc"},
    )

    assert captured["mutation_id"] == "zopedia_mutation::ingest_source::abc"
    assert result["request"]["operation"] == "zopedia"
    assert result["payload"]["summary"][0]["status"] == "rolled_back"


def test_invoke_zopedia_maintenance_reports_tool_dispatches_to_research_module(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_reports(**kwargs):
        captured.update(kwargs)
        return {"summary": [{"run_id": "maintenance-run", "issue_count": 2}]}

    monkeypatch.setattr(agent_tools.omnibar_research, "zopedia_list_maintenance_reports", _fake_reports)

    service = QueryService(data_access=object())
    result = agent_tools.invoke_tool(
        service=service,
        tool_name="zopedia.list_maintenance_reports",
        arguments={"status": "ready", "max_results": 2},
    )

    assert captured["status"] == "ready"
    assert captured["max_results"] == 2
    assert result["request"]["operation"] == "zopedia"
    assert result["payload"]["summary"][0]["run_id"] == "maintenance-run"


def test_invoke_zopedia_apply_mutation_tool_dispatches_to_research_module(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_apply_mutation(**kwargs):
        captured.update(kwargs)
        return {"summary": [{"mutation_id": "zopedia_mutation::metadata_patch::abc", "status": "committed"}]}

    monkeypatch.setattr(agent_tools.omnibar_research, "zopedia_apply_mutation", _fake_apply_mutation)

    service = QueryService(data_access=object())
    result = agent_tools.invoke_tool(
        service=service,
        tool_name="zopedia.apply_mutation",
        arguments={
            "mutation_type": "metadata_patch",
            "page_id": "zopedia::theme::ai-power",
            "metadata_patch": {"reviewed": True},
            "rationale": "source-backed correction",
        },
    )

    assert captured["mutation_type"] == "metadata_patch"
    assert captured["page_id"] == "zopedia::theme::ai-power"
    assert captured["metadata_patch"] == {"reviewed": True}
    assert result["request"]["operation"] == "zopedia"
    assert result["payload"]["summary"][0]["status"] == "committed"


def test_invoke_analysis_tool_dispatches_to_analysis_runner(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run_analysis_python(**kwargs):
        captured.update(kwargs)
        return {
            "analysis_run_id": "zopedia_analysis::test",
            "status": "succeeded",
            "metrics": [{"name": "rows", "value": 2}],
            "tables": [],
            "charts": [],
            "artifacts": [],
            "messages": [],
            "llm_context_text": "Zopedia analysis run succeeded.",
        }

    monkeypatch.setattr(agent_tools.zopedia_analysis, "run_analysis_python", _fake_run_analysis_python)

    service = QueryService(data_access=object())
    result = agent_tools.invoke_tool(
        service=service,
        tool_name="analysis.run_python",
        arguments={
            "objective": "count rows",
            "code": "add_metric('rows', len(datasets['prices']))",
            "inline_datasets": [{"name": "prices", "rows": [{"close": 10}, {"close": 11}]}],
        },
    )

    assert captured["service"] is service
    assert captured["objective"] == "count rows"
    assert captured["inline_datasets"] == [{"name": "prices", "rows": [{"close": 10}, {"close": 11}]}]
    assert result["request"]["operation"] == "analysis"
    assert result["result_type"] == "analysis_result"
    assert result["payload"]["metrics"][0]["name"] == "rows"


def test_invoke_analysis_tool_repairs_flattened_code_once(monkeypatch):
    calls: list[dict[str, object]] = []

    def _fake_run_analysis_python(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "analysis_run_id": "zopedia_analysis::bad",
                "status": "rejected",
                "objective": kwargs["objective"],
                "error": "Syntax error: invalid syntax (<unknown>, line 1)",
                "metadata": {"failure_category": "analysis_code_error"},
                "messages": [],
                "llm_context_text": "Failure category: analysis_code_error",
            }
        return {
            "analysis_run_id": "zopedia_analysis::fixed",
            "status": "succeeded",
            "objective": kwargs["objective"],
            "metrics": [{"name": "rows", "value": 2}],
            "tables": [],
            "charts": [],
            "artifacts": [],
            "messages": [],
            "metadata": {"runner": "zopedia_analysis.v1"},
            "llm_context_text": "Zopedia analysis run succeeded.",
        }

    def _fake_repair(**kwargs):
        return {
            "objective": "count rows",
            "code": "prices = datasets['prices']\nadd_metric('rows', len(prices))",
            "dataset_refs": [],
            "inline_datasets": [{"name": "prices", "rows": [{"close": 10}, {"close": 11}]}],
            "notes": "restored multiline Python and explicit inputs",
        }

    monkeypatch.setattr(agent_tools.zopedia_analysis, "run_analysis_python", _fake_run_analysis_python)
    monkeypatch.setattr(agent_tools, "repair_aql_zopedia_analysis_arguments", _fake_repair)

    service = QueryService(data_access=object())
    result = agent_tools.invoke_tool(
        service=service,
        tool_name="analysis.run_python",
        arguments={
            "objective": "count rows",
            "code": "import pandas as pd add_metric('rows', 2)",
            "dataset_refs": ["{\"name\": \"price_history\", \"params\": {\"ticker\": \"SPY\", \"days\": 60}}"],
        },
    )

    assert len(calls) == 2
    assert calls[0]["dataset_refs"] == [{"name": "price_history", "params": {"ticker": "SPY", "days": 60}}]
    assert calls[1]["code"] == "prices = datasets['prices']\nadd_metric('rows', len(prices))"
    assert result["payload"]["status"] == "succeeded"
    assert result["payload"]["metadata"]["analysis_repaired"] is True
    assert result["payload"]["metadata"]["repaired_from_run_id"] == "zopedia_analysis::bad"


def test_invoke_analysis_tool_retries_when_first_repair_still_fails(monkeypatch):
    calls: list[dict[str, object]] = []
    repair_failures: list[dict[str, object]] = []

    def _fake_run_analysis_python(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "analysis_run_id": "zopedia_analysis::bad_original",
                "status": "rejected",
                "objective": kwargs["objective"],
                "error": "Syntax error: invalid syntax (<unknown>, line 1)",
                "metadata": {"failure_category": "analysis_code_error"},
                "messages": [],
                "llm_context_text": "Failure category: analysis_code_error",
            }
        if len(calls) == 2:
            return {
                "analysis_run_id": "zopedia_analysis::bad_repair",
                "status": "rejected",
                "objective": kwargs["objective"],
                "error": "Syntax error: expected an indented block",
                "metadata": {"failure_category": "analysis_code_error"},
                "messages": [],
                "llm_context_text": "Failure category: analysis_code_error",
            }
        return {
            "analysis_run_id": "zopedia_analysis::fixed",
            "status": "succeeded",
            "objective": kwargs["objective"],
            "metrics": [{"name": "rows", "value": 2}],
            "tables": [],
            "charts": [],
            "artifacts": [],
            "messages": [],
            "metadata": {"runner": "zopedia_analysis.v1"},
            "llm_context_text": "Zopedia analysis run succeeded.",
        }

    def _fake_repair(**kwargs):
        repair_failures.append(kwargs["failure_payload"])
        if len(repair_failures) == 1:
            return {
                "objective": "count rows",
                "code": "if True:\nadd_metric('rows', 2)",
                "dataset_refs": [],
                "inline_datasets": [{"name": "prices", "rows": [{"close": 10}, {"close": 11}]}],
                "notes": "first repair still has bad indentation",
            }
        return {
            "objective": "count rows",
            "code": "prices = datasets['prices']\nadd_metric('rows', len(prices))",
            "dataset_refs": [],
            "inline_datasets": [{"name": "prices", "rows": [{"close": 10}, {"close": 11}]}],
            "notes": "fixed indentation",
        }

    monkeypatch.setattr(agent_tools.zopedia_analysis, "run_analysis_python", _fake_run_analysis_python)
    monkeypatch.setattr(agent_tools, "repair_aql_zopedia_analysis_arguments", _fake_repair)

    result = agent_tools.invoke_tool(
        service=QueryService(data_access=object()),
        tool_name="analysis.run_python",
        arguments={
            "objective": "count rows",
            "code": "import pandas as pd add_metric('rows', 2)",
        },
    )

    assert len(calls) == 3
    assert [failure["analysis_run_id"] for failure in repair_failures] == [
        "zopedia_analysis::bad_original",
        "zopedia_analysis::bad_repair",
    ]
    assert result["payload"]["status"] == "succeeded"
    assert result["payload"]["metadata"]["analysis_repair_attempt"] == 2


def test_invoke_analysis_tool_accepts_json_object_inline_datasets(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run_analysis_python(**kwargs):
        captured.update(kwargs)
        return {
            "analysis_run_id": "zopedia_analysis::inline",
            "status": "succeeded",
            "metrics": [],
            "tables": [],
            "charts": [],
            "artifacts": [],
            "messages": [],
            "metadata": {"runner": "zopedia_analysis.v1"},
            "llm_context_text": "ok",
        }

    monkeypatch.setattr(agent_tools.zopedia_analysis, "run_analysis_python", _fake_run_analysis_python)

    agent_tools.invoke_tool(
        service=QueryService(data_access=object()),
        tool_name="analysis.run_python",
        arguments={
            "objective": "inline",
            "code": "add_metric('rows', len(price_jpm))",
            "inline_datasets": "{\"price_jpm\": [{\"close\": 10}, {\"close\": 11}]}",
        },
    )

    assert captured["inline_datasets"] == [{"name": "price_jpm", "rows": [{"close": 10}, {"close": 11}]}]
