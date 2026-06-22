"""Tests for the AQL critique + judge layer."""
from __future__ import annotations

import pytest


def _summary_fixture() -> dict:
    return {
        "headline": "Market Summary",
        "summary_text": (
            "BNO surged nearly 70% in extreme trading tied to the Hormuz narrative, "
            "with no clear catalyst confirmed."
        ),
        "audio_text": "BNO surged nearly 70% on Hormuz, no catalyst confirmed.",
        "event_count": 0,
        "must_read_count": 1,
        "unresolved_count": 0,
        "featured_symbols": ["BNO"],
        "stories": [],
    }


def _install_runtime_helper_stub(monkeypatch, *, tool_results=None, catalog_names=None):
    """Patch ``critique._load_runtime_helpers`` with a controllable stub bundle."""
    from services.aql import critique as critique_module

    catalog = [
        {"name": name, "description": f"stub {name}", "inputSchema": {"properties": {}}}
        for name in (catalog_names or [
            "research.search_evidence",
            "research.retained_context",
            "investigator.technical_signals",
            "investigator.recent_news",
        ])
    ]
    invocations: list[tuple[str, dict]] = []

    def fake_invoke_tool(*, service, tool_name, arguments, run_id):
        invocations.append((tool_name, dict(arguments or {})))
        if tool_results and tool_name in tool_results:
            return tool_results[tool_name]
        return {
            "result_type": "object",
            "payload": {"summary": [{"label": tool_name, "summary": "stub result"}]},
        }

    helpers = {
        "build_tool_catalog": lambda service: catalog,
        "invoke_tool": fake_invoke_tool,
        "coerce_arguments": lambda raw: (
            (_coerce_list(raw), "") if isinstance(raw, list)
            else (dict(raw or {}), "")
        ),
        "summarize_result": lambda result: {
            "preview_text": str(result.get("payload") or "")[:200],
            "llm_context_text": str(result.get("payload") or "")[:200],
        },
        "tool_entry_by_name": lambda cat, name: next((t for t in cat if t.get("name") == name), {}),
        "tool_history_prompt": lambda calls: f"{len(calls)} prior calls" if calls else "no prior calls",
    }
    monkeypatch.setattr(critique_module, "_load_runtime_helpers", lambda: helpers)
    monkeypatch.setattr(
        critique_module,
        "_filtered_tool_catalog",
        lambda service, build: [t for t in build(service) if t["name"] in critique_module._CRITIQUE_TOOL_NAMES],
    )

    class FakeQueryService:
        @classmethod
        def from_environment(cls):
            return cls()

    monkeypatch.setattr(
        "data_access.query_service.QueryService", FakeQueryService, raising=True
    )
    return invocations


def _coerce_list(raw):
    out = {}
    for item in list(raw or []):
        if isinstance(item, dict) and item.get("name"):
            kind = str(item.get("value_kind") or "")
            if kind == "string":
                out[item["name"]] = item.get("string_value")
            elif kind == "number":
                out[item["name"]] = item.get("number_value")
            elif kind == "string_list":
                out[item["name"]] = list(item.get("string_list_value") or [])
    return out


class _FakeLLM:
    def __init__(self, scripted: list[dict]):
        self.scripted = list(scripted)
        self.calls: list[str] = []

    def generate_json(self, *, system_prompt, user_prompt, schema_name, schema):
        self.calls.append(schema_name)
        if not self.scripted:
            raise AssertionError(f"No scripted response for schema {schema_name}")
        return self.scripted.pop(0)


def test_critique_skipped_when_disabled(monkeypatch):
    from services.aql import critique as critique_module
    from services.aql.critique import critique_home_summary

    monkeypatch.setattr(critique_module, "get_config_param", lambda key: 0)
    result = critique_home_summary(
        summary=_summary_fixture(),
        home_payload={},
        llm_client=_FakeLLM([]),
    )
    assert result["skipped"] is True
    assert result["issues"] == []
    assert result["tool_calls"] == []


def test_critique_returns_issues_after_tool_use(monkeypatch):
    from services.aql.critique import critique_home_summary

    invocations = _install_runtime_helper_stub(
        monkeypatch,
        tool_results={
            "investigator.technical_signals": {
                "result_type": "object",
                "payload": {"ticker": "BNO", "current_price": 30.1, "pct_change_today": 0.032},
            }
        },
    )
    llm = _FakeLLM([
        {
            "action": "tool_call",
            "reasoning": "Need to check the actual move on BNO.",
            "tool_name": "investigator.technical_signals",
            "tool_arguments": [
                {"name": "ticker", "value_kind": "string", "string_value": "BNO",
                 "number_value": None, "boolean_value": None, "string_list_value": None}
            ],
            "issues": [],
        },
        {
            "action": "final",
            "reasoning": "BNO moved ~3.2%, not 70%.",
            "tool_name": "",
            "tool_arguments": [],
            "issues": [
                {
                    "type": "numeric",
                    "location": "overview",
                    "claim": "BNO surged nearly 70%",
                    "severity": "high",
                    "evidence": "investigator.technical_signals returned pct_change_today=0.032",
                },
                {
                    "type": "contradiction",
                    "location": "overview",
                    "claim": "tied to the Hormuz narrative, with no clear catalyst confirmed",
                    "severity": "high",
                    "evidence": "Sentence names Hormuz as the catalyst then says no catalyst was confirmed.",
                },
            ],
        },
    ])

    result = critique_home_summary(
        summary=_summary_fixture(),
        home_payload={},
        llm_client=llm,
    )

    assert result["skipped"] is False
    assert len(result["issues"]) == 2
    types = sorted(issue["type"] for issue in result["issues"])
    assert types == ["contradiction", "numeric"]
    assert invocations and invocations[0][0] == "investigator.technical_signals"
    assert invocations[0][1] == {"ticker": "BNO"}
    assert llm.calls == ["aql_critique_step", "aql_critique_step"]


def test_critique_handles_unknown_tool(monkeypatch):
    from services.aql.critique import critique_home_summary

    _install_runtime_helper_stub(monkeypatch)
    llm = _FakeLLM([
        {
            "action": "tool_call",
            "reasoning": "Try a tool that does not exist.",
            "tool_name": "research.totally_made_up",
            "tool_arguments": [],
            "issues": [],
        },
        {
            "action": "final",
            "reasoning": "Nothing found.",
            "tool_name": "",
            "tool_arguments": [],
            "issues": [],
        },
    ])

    result = critique_home_summary(
        summary=_summary_fixture(),
        home_payload={},
        llm_client=llm,
    )

    assert result["issues"] == []
    assert result["tool_calls"]
    assert result["tool_calls"][0]["status"] == "failed"
    assert "not in the critique catalog" in result["tool_calls"][0]["result_summary"]["preview_text"]


def test_critique_dedupes_repeated_tool_calls(monkeypatch):
    from services.aql.critique import critique_home_summary

    _install_runtime_helper_stub(monkeypatch)
    args = [
        {"name": "ticker", "value_kind": "string", "string_value": "BNO",
         "number_value": None, "boolean_value": None, "string_list_value": None}
    ]
    llm = _FakeLLM([
        {
            "action": "tool_call",
            "reasoning": "first try",
            "tool_name": "investigator.technical_signals",
            "tool_arguments": args,
            "issues": [],
        },
        {
            "action": "tool_call",
            "reasoning": "same call again",
            "tool_name": "investigator.technical_signals",
            "tool_arguments": args,
            "issues": [],
        },
        {
            "action": "final",
            "reasoning": "done",
            "tool_name": "",
            "tool_arguments": [],
            "issues": [],
        },
    ])

    result = critique_home_summary(
        summary=_summary_fixture(),
        home_payload={},
        llm_client=llm,
    )

    statuses = [call["status"] for call in result["tool_calls"]]
    assert statuses.count("completed") == 1
    assert statuses.count("skipped") == 1


def test_judge_returns_none_with_no_issues():
    from services.aql.critique import judge_revise_summary

    revised = judge_revise_summary(
        original=_summary_fixture(),
        critique={"issues": []},
        llm_client=_FakeLLM([]),
    )
    assert revised is None


def test_judge_returns_revised_summary():
    from services.aql.critique import judge_revise_summary

    llm = _FakeLLM([
        {
            "overview": "BNO rose around 3% on Strait of Hormuz supply-risk headlines.",
            "sections": [
                {
                    "title": "Energy",
                    "bullets": [
                        "BNO traded higher on geopolitical risk premium tied to Hormuz.",
                    ],
                }
            ],
            "audio_text": "BNO is up about three percent on Hormuz supply-risk headlines.",
            "revisions": [
                {"issue_index": 0, "decision": "rephrase", "rewritten_text": "rose around 3%"},
                {"issue_index": 1, "decision": "rephrase", "rewritten_text": "Hormuz catalyst kept"},
            ],
        }
    ])

    revised = judge_revise_summary(
        original=_summary_fixture(),
        critique={"issues": [
            {"type": "numeric", "location": "overview", "claim": "70%", "severity": "high",
             "evidence": "actual pct_change_today=0.032"},
            {"type": "contradiction", "location": "overview", "claim": "no catalyst",
             "severity": "high", "evidence": "Hormuz IS the catalyst"},
        ]},
        llm_client=llm,
    )

    assert revised is not None
    assert "around 3%" in revised["summary_text"]
    assert "70%" not in revised["summary_text"]
    assert "Energy" in revised["summary_text"]
    assert revised["audio_text"].startswith("BNO is up about three percent")
    assert len(revised["judge_revisions"]) == 2
    assert all(r["decision"] == "rephrase" for r in revised["judge_revisions"])
    # Untouched fields preserved
    assert revised["featured_symbols"] == ["BNO"]
    assert revised["headline"] == "Market Summary"


def test_judge_dedupes_repeated_sections():
    """Defensive: when the judge LLM returns the same section title twice
    (we saw this in real runs), the rendered summary must contain it once."""
    from services.aql.critique import judge_revise_summary

    repeated_section = {
        "title": "Tech rebound",
        "bullets": ["AI infrastructure rallied on CoreWeave/Anthropic deal."],
    }
    llm = _FakeLLM([
        {
            "overview": "Tech rebounded; utilities slid.",
            "sections": [repeated_section, repeated_section],
            "audio_text": "Tech rebounded today.",
            "featured_symbols": ["NVDA", "ORCL"],
            "revisions": [{"issue_index": 0, "decision": "rephrase", "rewritten_text": "x"}],
        }
    ])

    revised = judge_revise_summary(
        original=_summary_fixture(),
        critique={"issues": [{"type": "numeric", "location": "overview",
                              "claim": "X", "severity": "high", "evidence": "Y"}]},
        llm_client=llm,
    )

    assert revised is not None
    assert revised["summary_text"].count("**Tech rebound**") == 1
    assert revised["featured_symbols"] == ["NVDA", "ORCL"]


def test_agentic_summary_applies_critique_to_summary_text(monkeypatch):
    """End-to-end: critique flags an issue, judge rewrites, and the final
    summary_text uses the revised content prepended with the hypothesis."""
    from services.aql import summarizer as aql_summarizer
    from services.aql.summarizer import build_attention_agentic_summary_with_trace

    payload = {
        "run_id": "summary-critique-run",
        "generated_at_utc": "2026-04-24T18:00:00Z",
        "top_events": [],
        "must_read_movers": [
            {
                "bundle_id": "symbol::BNO",
                "symbol": "BNO",
                "headline": "BNO moved sharply",
                "what_changed_text": "BNO traded sharply higher today.",
                "why_now_text": "Strait of Hormuz supply-risk headlines.",
                "what_else_moved_text": "USO also moved.",
                "cause_status": "supported",
            }
        ],
        "unresolved_large_moves": [],
    }

    monkeypatch.setattr(
        aql_summarizer,
        "critique_home_summary",
        lambda **kw: {
            "issues": [
                {"type": "numeric", "location": "overview", "claim": "fake number",
                 "severity": "high", "evidence": "actual is different"}
            ],
            "tool_calls": [
                {"tool_call_id": "tc_01", "tool_name": "investigator.technical_signals",
                 "arguments": {"ticker": "BNO"}, "status": "completed",
                 "result_summary": {"preview_text": "stub"}},
            ],
            "skipped": False,
        },
    )
    monkeypatch.setattr(
        aql_summarizer,
        "judge_revise_summary",
        lambda *, original, critique, llm_client: {
            **original,
            "summary_text": "**Energy**\n- BNO traded higher on Hormuz supply-risk headlines.",
            "audio_text": "BNO is up on Hormuz supply-risk headlines.",
            "judge_revisions": [
                {"issue_index": 0, "decision": "rephrase", "rewritten_text": "BNO traded higher"}
            ],
        },
    )

    # Stub the rest of the agentic pipeline so we only exercise critique wiring.
    monkeypatch.setattr(
        aql_summarizer,
        "_plan_summary_research",
        lambda payload, llm_client: ["why is BNO moving today"],
    )
    monkeypatch.setattr(
        aql_summarizer,
        "_collect_summary_research_trace",
        lambda payload, *, queries, llm_client, search_clients, embedding_client: {
            "request_rows": [],
            "result_rows": [],
            "documents": [],
            "chunks": None,
            "claims": [{"claim_text": "BNO moved on Hormuz news", "confidence_score": 0.7,
                        "is_same_day": True}],
            "top_sources": [{"source": "Reuters", "url": "https://example.com/x"}],
            "supporting_claims": [{"claim_text": "BNO moved on Hormuz news"}],
        },
    )
    monkeypatch.setattr(
        aql_summarizer,
        "_synthesize_attention_home_hypothesis",
        lambda *, beats, claims, queries, llm_client, signal_context: "BNO is reflecting Hormuz supply-risk fears.",
    )
    monkeypatch.setattr(
        aql_summarizer,
        "verify_hypothesis",
        lambda **kw: {"verdict": "supported", "confidence": "medium",
                      "supporting_claims": ["BNO moved on Hormuz news"],
                      "contradicting_claims": [], "gap_queries": [], "reasoning": "ok"},
    )
    monkeypatch.setattr(aql_summarizer, "_llm_home_summary", lambda *args, **kwargs: None)

    class _NoopLLM:
        def generate_json(self, **kw):
            raise AssertionError("LLM should not be called when all stages are stubbed")

    summary, trace = build_attention_agentic_summary_with_trace(
        payload,
        llm_client=_NoopLLM(),
    )

    assert "BNO traded higher on Hormuz" in summary["summary_text"]
    assert summary["critique_issues"] and summary["critique_issues"][0]["type"] == "numeric"
    assert summary["judge_revisions"] and summary["judge_revisions"][0]["decision"] == "rephrase"
    assert summary["critique_tool_calls"]
    # Hypothesis still gets prepended above the revised body.
    assert "**Market Hypothesis**" in summary["summary_text"]
    assert "BNO is reflecting Hormuz supply-risk fears." in summary["summary_text"]
