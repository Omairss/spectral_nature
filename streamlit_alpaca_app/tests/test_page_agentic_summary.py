from __future__ import annotations

import pandas as pd

from services import page_agentic_summary


class FakeLLM:
    def __init__(self) -> None:
        self.user_prompt = ""

    def generate_json(self, **kwargs):
        self.user_prompt = kwargs["user_prompt"]
        if kwargs.get("schema_name") == "page_agentic_summary_review":
            return {
                "accepted": True,
                "issues": [],
                "revision_instruction": "",
                "confidence": "medium",
            }
        return {
            "headline": "Momentum is concentrated in AAPL.",
            "summary_markdown": "**AAPL** has the cleaner setup in the supplied feed.",
            "watch_items": ["Check whether the move holds into the close."],
            "data_gaps": ["No live news evidence included."],
            "confidence": "medium",
        }


class FakeAQLAgent:
    def __init__(self) -> None:
        self.kwargs = {}

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return {
            "run_id": "agrun_test",
            "status": "completed",
            "answer_markdown": "**AAPL** has a retained-evidence-backed momentum setup.",
            "confidence": "medium",
            "limitations": [],
            "tool_calls": [
                {
                    "tool_name": "research.search_evidence",
                    "status": "completed",
                    "arguments": {"query": "AAPL momentum"},
                    "result_summary": {"preview_text": "matched retained evidence"},
                }
            ],
        }


def test_market_summary_context_limits_records_and_keeps_horizon():
    feed = pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "company_name": "Apple Inc.",
                "opportunity": "Upside momentum",
                "direction": "Up / accelerating",
                "opportunity_score": 91.0,
                "return_1m_pct": 8.2,
                "details": "1 Month +8.2%",
            }
        ]
    )

    context = page_agentic_summary.market_summary_context(
        business_filter="All Market",
        selected_horizon_label="1 Month",
        opportunity_feed=feed,
        movers=pd.DataFrame([{"symbol": "AAPL"}]),
        momentum=pd.DataFrame([{"symbol": "AAPL"}]),
    )

    assert context["surface"] == "Market Explorer"
    assert context["selected_horizon"] == "1 Month"
    assert context["opportunities"][0]["symbol"] == "AAPL"


def test_build_page_agentic_summary_returns_llm_payload(monkeypatch):
    monkeypatch.delenv("PAGE_AGENTIC_SUMMARY_WRITE_POLICY", raising=False)
    fake = FakeLLM()
    agent = FakeAQLAgent()

    result = page_agentic_summary.build_page_agentic_summary(
        surface="Market Explorer",
        context={"surface": "Market Explorer", "opportunities": [{"symbol": "AAPL"}]},
        llm_client=fake,
        aql_agent_runner=agent,
    )

    assert result["status"] == "ok"
    assert result["headline"] == "Momentum is concentrated in AAPL."
    assert result["watch_items"] == ["Check whether the move holds into the close."]
    assert "AAPL" in fake.user_prompt
    assert "AQL agent result JSON" in fake.user_prompt
    assert "AQL / Zopedia" in agent.kwargs["query"]
    assert agent.kwargs["persist_findings"] is False
    assert agent.kwargs["write_policy"] == "safe_auto"
    assert result["aql_agent"]["tool_calls"][0]["tool_name"] == "research.search_evidence"


def test_build_page_agentic_summary_fails_closed_without_llm():
    result = page_agentic_summary.build_page_agentic_summary(
        surface="Stock Investigator",
        context={"ticker": "AAPL"},
        llm_client=None,
    )

    assert result["status"] == "unavailable"
    assert result["summary_markdown"] == ""
    assert result["confidence"] == "low"


def test_build_page_agentic_summary_fails_closed_when_aql_fails():
    def _failed_agent(**kwargs):
        return {
            "status": "failed",
            "answer_markdown": "",
            "error": "planner failed",
            "limitations": ["No evidence"],
        }

    result = page_agentic_summary.build_page_agentic_summary(
        surface="Market Explorer",
        context={"opportunities": [{"symbol": "AAPL"}]},
        llm_client=FakeLLM(),
        aql_agent_runner=_failed_agent,
    )

    assert result["status"] == "unavailable"
    assert result["summary_markdown"] == ""
    assert result["data_gaps"][0] == "AQL did not return enough grounded evidence for a page summary."


def test_build_page_agentic_summary_fails_closed_when_aql_raises():
    def _raising_agent(**kwargs):
        raise ConnectionError("remote disconnected")

    result = page_agentic_summary.build_page_agentic_summary(
        surface="Market Explorer",
        context={
            "opportunities": [
                {
                    "symbol": "AAPL",
                    "opportunity": "Upside momentum",
                    "direction": "Up / accelerating",
                    "details": "1D +2.1% | 1 Month +8.2%",
                }
            ]
        },
        llm_client=FakeLLM(),
        aql_agent_runner=_raising_agent,
    )

    assert result["status"] == "unavailable"
    assert result["summary_markdown"] == ""
    assert result["data_gaps"][0] == "AQL page summary failed."


def test_materialized_page_agentic_summary_round_trips_exact_context():
    context = {"surface": "Market Explorer", "opportunities": [{"symbol": "AAPL"}]}
    summary = {
        "status": "ok",
        "surface": "Market Explorer",
        "headline": "AAPL is leading.",
        "summary_markdown": "AAPL has the strongest setup.",
        "watch_items": [],
        "data_gaps": [],
        "confidence": "medium",
    }
    row = page_agentic_summary.build_materialized_page_agentic_summary_row(
        surface="Market Explorer",
        context=context,
        summary=summary,
        generated_at_utc="2026-04-27T17:00:00Z",
        run_id="run_1",
    )
    frame = pd.DataFrame([row])

    resolved = page_agentic_summary.materialized_page_agentic_summary(
        frame,
        surface="Market Explorer",
        context_signature=page_agentic_summary.page_summary_context_signature(context),
    )

    assert resolved["headline"] == "AAPL is leading."
    assert resolved["materialized"]["context_match"] == "exact"
    assert resolved["materialized"]["run_id"] == "run_1"


def test_materialized_page_agentic_summary_allows_ticker_fallback():
    context = {"surface": "Stock Investigator", "ticker": "AAPL", "background": {"llm_summary_text": "Apple context"}}
    row = page_agentic_summary.build_materialized_page_agentic_summary_row(
        surface="Stock Investigator",
        context=context,
        summary={
            "status": "ok",
            "surface": "Stock Investigator",
            "headline": "AAPL context is materialized.",
            "summary_markdown": "AAPL has a precomputed summary.",
            "watch_items": [],
            "data_gaps": [],
            "confidence": "medium",
        },
        generated_at_utc="2026-04-27T17:00:00Z",
        run_id="run_2",
        ticker="AAPL",
    )

    resolved = page_agentic_summary.materialized_page_agentic_summary(
        pd.DataFrame([row]),
        surface="Stock Investigator",
        context_signature="different-context",
        ticker="AAPL",
    )

    assert resolved["headline"] == "AAPL context is materialized."
    assert resolved["materialized"]["context_match"] == "ticker"


def test_materialized_page_agentic_summary_allows_latest_surface_lookup():
    older_row = page_agentic_summary.build_materialized_page_agentic_summary_row(
        surface="Broad Economy",
        context={"surface": "Broad Economy", "macro_indicators": [{"series_id": "CPIAUCSL"}]},
        summary={
            "status": "ok",
            "surface": "Broad Economy",
            "headline": "Older macro read.",
            "summary_markdown": "Older macro read.",
            "watch_items": [],
            "data_gaps": [],
            "confidence": "low",
        },
        generated_at_utc="2026-04-26T17:00:00Z",
        run_id="run_old",
    )
    newer_row = page_agentic_summary.build_materialized_page_agentic_summary_row(
        surface="Broad Economy",
        context={"surface": "Broad Economy", "macro_indicators": [{"series_id": "PAYEMS"}]},
        summary={
            "status": "ok",
            "surface": "Broad Economy",
            "headline": "Latest macro read.",
            "summary_markdown": "Latest macro read.",
            "watch_items": [],
            "data_gaps": [],
            "confidence": "medium",
        },
        generated_at_utc="2026-04-27T17:00:00Z",
        run_id="run_new",
    )

    resolved = page_agentic_summary.materialized_page_agentic_summary(
        pd.DataFrame([older_row, newer_row]),
        surface="Broad Economy",
        context_signature="",
    )

    assert resolved["headline"] == "Latest macro read."
    assert resolved["materialized"]["context_match"] == "surface"
    assert resolved["materialized"]["run_id"] == "run_new"


def test_materialized_page_agentic_summary_falls_back_to_latest_surface_on_signature_mismatch():
    row = page_agentic_summary.build_materialized_page_agentic_summary_row(
        surface="Broad Economy",
        context={"surface": "Broad Economy", "macro_indicators": [{"series_id": "CPIAUCSL"}]},
        summary={
            "status": "ok",
            "surface": "Broad Economy",
            "headline": "Latest scheduled macro read.",
            "summary_markdown": "The scheduled Broad Economy summary is available.",
            "watch_items": [],
            "data_gaps": [],
            "confidence": "medium",
        },
        generated_at_utc="2026-04-27T17:00:00Z",
        run_id="run_broad",
    )

    resolved = page_agentic_summary.materialized_page_agentic_summary(
        pd.DataFrame([row]),
        surface="Broad Economy",
        context_signature="different-context-signature",
    )

    assert resolved["headline"] == "Latest scheduled macro read."
    assert resolved["materialized"]["context_match"] == "surface_fallback"
