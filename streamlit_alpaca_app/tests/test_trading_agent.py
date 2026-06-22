from __future__ import annotations

from http.client import RemoteDisconnected

import numpy as np
import pandas as pd
import requests

from services.llm import LLMAPIError
from services.trading_agent import (
    TRADING_AGENT_HORIZON_SPECS,
    build_trading_agent_context,
    build_trading_agent_materialized_frames,
    build_trading_agent_suggestions,
)


class FakeTradingLLM:
    def __init__(self) -> None:
        self.user_prompt = ""

    def generate_json(self, **kwargs):
        self.user_prompt = kwargs["user_prompt"]
        return {
            "regime_read": "Momentum is positive but macro confirmation is incomplete.",
            "portfolio_posture": "Stay selective and require confirmation.",
            "candidates": [
                {
                    "ticker": "aapl",
                    "direction": "long",
                    "setup": "Breakout continuation",
                    "hypothesis": "Momentum can continue if the market stays supportive.",
                    "evidence": ["Opportunity score is high.", "Technical trend is positive."],
                    "invalidation": "Break below support.",
                    "tail_risks": ["Macro shock", "Earnings reset"],
                    "suggested_horizon": "1-3 weeks",
                    "confidence": "medium",
                }
            ],
            "data_gaps": ["No options positioning evidence."],
        }


class FakeAQLTradingAgent:
    def __init__(self) -> None:
        self.kwargs = {}

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return {
            "run_id": "agrun_trade",
            "status": "completed",
            "answer_markdown": "AQL read: AAPL has momentum, but macro confirmation is incomplete.",
            "confidence": "medium",
            "limitations": [],
            "tool_calls": [
                {
                    "tool_name": "hypothesis.verify",
                    "status": "completed",
                    "arguments": {"hypothesis": "AAPL momentum can continue"},
                    "result_summary": {"preview_text": "verification weak but directionally consistent"},
                }
            ],
        }


def test_trading_agent_stock_summary_lookup_does_not_cross_tickers():
    opportunity_feed = pd.DataFrame(
        [
            {
                "symbol": "AGX",
                "company_name": "Argan, Inc.",
                "opportunity": "Upside momentum",
                "direction": "Up / accelerating",
                "opportunity_score": 91.0,
                "daily_change_pct": 3.0,
                "return_1y_pct": 120.0,
                "selected_horizon_col": "return_1y_pct",
                "selected_horizon_label": "1 Year",
                "asof_time_utc": "2026-05-05T12:00:00+00:00",
                "run_id": "attention-run",
            }
        ]
    )
    page_summaries = pd.DataFrame(
        [
            {
                "surface": "Stock Investigator",
                "ticker": "MXL",
                "headline": "MXL summary must not leak",
                "confidence": "medium",
                "summary_json": '{"status":"ok","headline":"MXL summary must not leak","watch_items":["MXL item"],"data_gaps":[],"confidence":"medium"}',
                "generated_at_utc": "2026-05-05T12:00:00+00:00",
                "run_id": "summary-run",
            }
        ]
    )

    context, _ = build_trading_agent_context(
        opportunity_feed=opportunity_feed,
        page_summaries=page_summaries,
        horizon_key="1y",
        horizon_col="return_1y_pct",
        horizon_label="1 Year",
        max_candidates=1,
    )

    assert context["ticker_evidence"][0]["ticker"] == "AGX"
    assert context["ticker_evidence"][0]["company_name"] == "Argan, Inc."
    assert context["ticker_evidence"][0]["stock_summary"] == {}
    assert context["source_summaries"]["stock_investigator"] == []


def test_trading_agent_runs_single_ticker_aql_packages():
    class PackageLLM(FakeTradingLLM):
        def generate_json(self, **kwargs):
            self.user_prompt = kwargs["user_prompt"]
            return {
                "regime_read": "Single-ticker packages are ready.",
                "portfolio_posture": "Use package evidence only.",
                "candidates": [
                    {
                        "ticker": "MXL",
                        "direction": "watch",
                        "setup": "MaxLinear package setup",
                        "hypothesis": "MaxLinear can continue if company evidence confirms the move.",
                        "evidence": ["MXL package was completed."],
                        "invalidation": "Package evidence turns stale.",
                        "tail_risks": ["Semiconductor reversal"],
                        "suggested_horizon": "1 Month",
                        "confidence": "medium",
                    }
                ],
                "data_gaps": [],
            }

    calls = []

    def _package_agent(**kwargs):
        calls.append(kwargs)
        query = kwargs["query"]
        if "Focus ticker: MXL" in query:
            ticker = "MXL"
        elif "Focus ticker: POET" in query:
            ticker = "POET"
        else:
            ticker = "UNKNOWN"
        return {
            "run_id": f"agrun_{ticker.lower()}",
            "status": "completed",
            "answer_markdown": f"{ticker} package completed.",
            "confidence": "medium",
            "limitations": [],
            "tool_calls": [
                {
                    "tool_name": "investigator.company_context",
                    "status": "completed",
                    "arguments": {"ticker": ticker},
                    "result_summary": {"preview_text": f"{ticker} context"},
                }
            ],
        }

    llm = PackageLLM()
    result = build_trading_agent_suggestions(
        context={
            "controls": {
                "selected_horizon_label": "1 Month",
                "selected_horizon_col": "return_1m_pct",
                "max_candidates": 2,
            },
            "market_opportunity_feed": [
                {
                    "symbol": "MXL",
                    "company_name": "MaxLinear, Inc.",
                    "return_1m_pct": 91.8,
                    "details": "1 Month +91.8%.",
                },
                {
                    "symbol": "POET",
                    "company_name": "POET Technologies Inc.",
                    "return_1m_pct": 67.3,
                    "details": "1 Month +67.3%.",
                },
            ],
        },
        llm_client=llm,
        aql_agent_runner=_package_agent,
    )

    queries = [call["query"] for call in calls]
    assert result["status"] == "ok"
    assert len(calls) == 2
    assert all(call["max_tool_calls"] == 8 for call in calls)
    assert all(call["persist_findings"] is False for call in calls)
    assert any("Focus ticker: MXL = MaxLinear, Inc." in query and "POET" not in query for query in queries)
    assert any("Focus ticker: POET = POET Technologies Inc." in query and "MXL" not in query for query in queries)
    assert "ticker_packages" in llm.user_prompt
    assert result["aql_agent"]["ticker_packages"][0]["ticker"] == "MXL"
    assert result["aql_agent"]["ticker_packages"][1]["ticker"] == "POET"


def test_trading_agent_deduplicates_similar_gap_wording():
    class GapLLM(FakeTradingLLM):
        def generate_json(self, **kwargs):
            return {
                "regime_read": "Research package completed with gaps.",
                "portfolio_posture": "Wait for confirmation.",
                "candidates": [],
                "data_gaps": [
                    "No recent news for MXL in last 30 days",
                    "No Zopedia pages to verify institutional knowledge",
                ],
            }

    def _package_agent(**kwargs):
        return {
            "run_id": "agrun_mxl",
            "status": "completed",
            "answer_markdown": "MXL package completed for MaxLinear.",
            "confidence": "medium",
            "limitations": [
                "No recent news found for MXL in the last 30 days.",
                "No Zopedia pages for MXL or MaxLinear.",
            ],
            "tool_calls": [],
        }

    result = build_trading_agent_suggestions(
        context={
            "controls": {
                "selected_horizon_label": "1 Month",
                "selected_horizon_col": "return_1m_pct",
                "max_candidates": 1,
            },
            "market_opportunity_feed": [
                {
                    "symbol": "MXL",
                    "company_name": "MaxLinear, Inc.",
                    "return_1m_pct": 81.8,
                    "details": "1 Month +81.8%.",
                }
            ],
        },
        llm_client=GapLLM(),
        aql_agent_runner=_package_agent,
    )

    assert result["data_gaps"] == [
        "No recent news for MXL in last 30 days",
        "No Zopedia pages to verify institutional knowledge",
        "No Zopedia pages for MXL or MaxLinear.",
    ]


def test_trading_agent_suggestions_normalize_llm_output():
    fake = FakeTradingLLM()
    agent = FakeAQLTradingAgent()

    result = build_trading_agent_suggestions(
        context={"market_opportunity_feed": [{"symbol": "AAPL"}]},
        llm_client=fake,
        aql_agent_runner=agent,
    )

    assert result["status"] == "ok"
    assert result["candidates"][0]["ticker"] == "AAPL"
    assert result["candidates"][0]["direction"] == "long"
    assert result["data_gaps"] == ["No options positioning evidence."]
    assert "market_opportunity_feed" in fake.user_prompt
    assert "AQL agent result JSON" in fake.user_prompt
    assert "shared AQL research tool harness" in agent.kwargs["query"]
    assert "trade candidates" not in agent.kwargs["query"].lower()
    assert "automated execution" not in agent.kwargs["query"].lower()
    assert agent.kwargs["persist_findings"] is False
    assert result["aql_agent"]["tool_calls"][0]["tool_name"] == "hypothesis.verify"


def test_trading_agent_fails_closed_without_llm():
    result = build_trading_agent_suggestions(context={}, llm_client=None)

    assert result["status"] == "unavailable"
    assert result["candidates"] == []
    assert result["error"] == "LLM runtime is not configured."


def test_trading_agent_fails_closed_when_aql_fails():
    def _failed_agent(**kwargs):
        return {
            "status": "failed",
            "answer_markdown": "",
            "error": "no grounded synthesis",
            "limitations": ["No evidence"],
        }

    result = build_trading_agent_suggestions(
        context={"market_opportunity_feed": [{"symbol": "AAPL"}]},
        llm_client=FakeTradingLLM(),
        aql_agent_runner=_failed_agent,
    )

    assert result["status"] == "error"
    assert result["candidates"] == []
    assert "AQL agent did not produce" in result["data_gaps"][0]


def test_trading_agent_falls_back_to_materialized_candidates_on_aql_timeout():
    def _timeout_agent(**kwargs):
        raise TimeoutError("Planner LLM step timed out after 75s.")

    result = build_trading_agent_suggestions(
        context={
            "controls": {
                "selected_horizon_label": "1 Week",
                "selected_horizon_col": "return_7d_pct",
                "max_candidates": 1,
            },
            "market_opportunity_feed": [
                {
                    "symbol": "AAPL",
                    "opportunity": "Momentum continuation",
                    "direction": "upside",
                    "opportunity_score": 91.0,
                    "return_7d_pct": 2.4,
                    "daily_change_pct": 0.8,
                    "details": "Strong short-term trend.",
                }
            ],
        },
        llm_client=FakeTradingLLM(),
        aql_agent_runner=_timeout_agent,
    )

    assert result["status"] == "fallback"
    assert result["candidates"][0]["ticker"] == "AAPL"
    assert result["candidates"][0]["confidence"] == "low"
    assert "AQL evidence validation did not complete" in result["data_gaps"][1]


def test_trading_agent_sanitizes_provider_policy_errors_from_aql():
    raw_error = (
        "LLMAPIError: LLM request failed status=400: invalid_prompt "
        "potentially violating our usage policy"
    )

    def _failed_agent(**kwargs):
        return {
            "status": "failed",
            "answer_markdown": "",
            "error": raw_error,
            "limitations": [f"LLM error: {raw_error}"],
        }

    result = build_trading_agent_suggestions(
        context={"market_opportunity_feed": [{"symbol": "AAPL"}]},
        llm_client=FakeTradingLLM(),
        aql_agent_runner=_failed_agent,
    )

    rendered = " ".join([str(result.get("error") or ""), *[str(item) for item in result.get("data_gaps", [])]])
    assert result["status"] == "error"
    assert "invalid_prompt" not in rendered
    assert "usage policy" not in rendered
    assert "model rejected the generated research prompt" in rendered


def test_trading_agent_sanitizes_provider_policy_errors_from_final_llm():
    class RejectingLLM:
        def generate_json(self, **kwargs):
            raise LLMAPIError(
                "LLM request failed status=400: invalid_prompt potentially violating our usage policy"
            )

    result = build_trading_agent_suggestions(
        context={"market_opportunity_feed": [{"symbol": "AAPL"}]},
        llm_client=RejectingLLM(),
        aql_agent_runner=FakeAQLTradingAgent(),
    )

    assert result["status"] == "error"
    assert "invalid_prompt" not in result["error"]
    assert "usage policy" not in result["error"]
    assert "model rejected the generated research prompt" in result["error"]


def test_trading_agent_retries_transient_aql_connection_errors(monkeypatch):
    monkeypatch.setattr("services.trading_agent.time.sleep", lambda *_args, **_kwargs: None)
    calls = {"count": 0}

    def _flaky_agent(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.ConnectionError(
                "Connection aborted.",
                RemoteDisconnected("Remote end closed connection without response"),
            )
        return FakeAQLTradingAgent()(**kwargs)

    result = build_trading_agent_suggestions(
        context={"market_opportunity_feed": [{"symbol": "AAPL"}]},
        llm_client=FakeTradingLLM(),
        aql_agent_runner=_flaky_agent,
    )

    assert result["status"] == "ok"
    assert calls["count"] == 2


def test_trading_agent_sanitizes_transient_aql_connection_errors(monkeypatch):
    monkeypatch.setattr("services.trading_agent.time.sleep", lambda *_args, **_kwargs: None)

    def _broken_agent(**kwargs):
        raise requests.ConnectionError(
            "Connection aborted.",
            RemoteDisconnected("Remote end closed connection without response"),
        )

    result = build_trading_agent_suggestions(
        context={"market_opportunity_feed": [{"symbol": "AAPL"}]},
        llm_client=FakeTradingLLM(),
        aql_agent_runner=_broken_agent,
    )

    assert result["status"] == "error"
    assert "RemoteDisconnected" not in result["error"]
    assert "Connection aborted" not in result["error"]
    assert "connection dropped" in result["error"]


def test_trading_agent_handles_numpy_array_fields():
    class ArrayLLM:
        def generate_json(self, **kwargs):
            return {
                "regime_read": "Momentum is concentrated.",
                "portfolio_posture": "Stay selective.",
                "candidates": np.array(
                    [
                        {
                            "ticker": "msft",
                            "direction": "long",
                            "setup": "Continuation",
                            "hypothesis": "Momentum can persist.",
                            "evidence": np.array(["Feed rank is high.", "AQL verified the setup."], dtype=object),
                            "invalidation": "Break below support.",
                            "tail_risks": np.array(["Macro reversal"], dtype=object),
                            "suggested_horizon": "1-3 weeks",
                            "confidence": "medium",
                        }
                    ],
                    dtype=object,
                ),
                "data_gaps": np.array(["Options evidence missing."], dtype=object),
            }

    def _array_agent(**kwargs):
        return {
            "run_id": "agrun_array",
            "status": "completed",
            "answer_markdown": "MSFT momentum setup is grounded enough to watch.",
            "confidence": "medium",
            "limitations": np.array(["No options chain."], dtype=object),
            "tool_calls": np.array(
                [
                    {
                        "tool_name": "hypothesis.verify",
                        "status": "completed",
                        "arguments": {"hypothesis": "MSFT momentum can persist"},
                        "result_summary": {"preview_text": "supported"},
                    }
                ],
                dtype=object,
            ),
        }

    result = build_trading_agent_suggestions(
        context={"market_opportunity_feed": np.array([{"symbol": "MSFT"}], dtype=object)},
        llm_client=ArrayLLM(),
        aql_agent_runner=_array_agent,
    )

    assert result["status"] == "ok"
    assert result["candidates"][0]["ticker"] == "MSFT"
    assert result["candidates"][0]["evidence"] == ["Feed rank is high.", "AQL verified the setup."]
    assert result["data_gaps"] == ["Options evidence missing.", "No options chain."]


def test_trading_agent_materialization_runs_required_horizons():
    opportunity_rows = []
    for spec in TRADING_AGENT_HORIZON_SPECS:
        horizon_col = spec["column"]
        opportunity_rows.append(
            {
                "business_filter": "All Market",
                "horizon_key": spec["key"],
                "selected_horizon_col": horizon_col,
                "selected_horizon_label": spec["label"],
                "rank": 1,
                "symbol": "AAPL",
                "company_name": "Apple Inc.",
                "opportunity": "Upside momentum",
                "direction": "Up / accelerating",
                "opportunity_score": 92.0,
                "daily_change_pct": 1.2,
                horizon_col: 8.5,
                "momentum_roc_score": 0.7,
                "trend_fit_gap": 0.1,
                "details": "Momentum setup.",
                "asof_time_utc": "2026-05-05T12:00:00+00:00",
                "run_id": "attention-run",
            }
        )
    opportunity_feed = pd.DataFrame(opportunity_rows)
    page_summaries = pd.DataFrame(
        [
            {
                "surface": "Market Explorer",
                "ticker": "",
                "headline": "Market setup",
                "confidence": "medium",
                "summary_json": '{"status":"ok","headline":"Market setup","summary_markdown":"Momentum is broad.","watch_items":["AAPL"],"data_gaps":[],"confidence":"medium"}',
                "generated_at_utc": "2026-05-05T12:00:00+00:00",
                "run_id": "summary-run",
            }
        ]
    )

    def _fake_builder(*, context, llm_client, aql_agent_runner=None):
        controls = context["controls"]
        return {
            "status": "ok",
            "regime_read": f"Regime for {controls['horizon_key']}",
            "portfolio_posture": "Stay selective.",
            "candidates": [
                {
                    "ticker": "AAPL",
                    "direction": "long",
                    "setup": "Momentum continuation",
                    "hypothesis": "Momentum can continue.",
                    "evidence": ["Materialized feed rank is high."],
                    "invalidation": "Break below support.",
                    "tail_risks": ["Macro reversal"],
                    "suggested_horizon": controls["selected_horizon_label"],
                    "confidence": "medium",
                }
            ],
            "data_gaps": [],
            "error": "",
            "aql_agent": {"status": "completed", "answer_markdown": "Grounded."},
        }

    runs, candidates = build_trading_agent_materialized_frames(
        opportunity_feed=opportunity_feed,
        page_summaries=page_summaries,
        llm_client=object(),
        run_id="trading-run",
        asof_time_utc="2026-05-05T13:00:00+00:00",
        max_candidates=1,
        suggestion_builder=_fake_builder,
    )

    assert runs["horizon_key"].tolist() == ["1w", "1m", "3m", "1y", "5y"]
    assert candidates["horizon_key"].tolist() == ["1w", "1m", "3m", "1y", "5y"]
    assert set(candidates["decision_status"]) == {"open"}
    assert candidates["candidate_id"].is_unique
    assert "context_json" in candidates.columns


def test_trading_agent_materialization_uses_fallback_on_horizon_timeout():
    opportunity_feed = pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "company_name": "Apple Inc.",
                "opportunity": "Momentum continuation",
                "direction": "upside",
                "opportunity_score": 92.0,
                "daily_change_pct": 1.1,
                "return_7d_pct": 2.2,
                "selected_horizon_col": "return_7d_pct",
                "selected_horizon_label": "1 Week",
                "asof_time_utc": "2026-05-05T13:00:00+00:00",
                "run_id": "run-1",
                "momentum_roc_score": 0.7,
                "trend_fit_gap": 0.1,
                "details": "Momentum setup.",
            }
        ]
    )

    def _timeout_builder(*, context, llm_client, aql_agent_runner=None):
        raise TimeoutError("Planner LLM step timed out after 75s.")

    runs, candidates = build_trading_agent_materialized_frames(
        opportunity_feed=opportunity_feed,
        page_summaries=pd.DataFrame(),
        llm_client=object(),
        run_id="trading-run",
        asof_time_utc="2026-05-05T13:00:00+00:00",
        max_candidates=1,
        horizon_specs=({"key": "1w", "column": "return_7d_pct", "label": "1 Week"},),
        suggestion_builder=_timeout_builder,
    )

    assert runs.iloc[0]["status"] == "fallback"
    assert runs.iloc[0]["candidate_count"] == 1
    assert candidates.iloc[0]["horizon_key"] == "1w"
    assert candidates.iloc[0]["decision_status"] == "open"
