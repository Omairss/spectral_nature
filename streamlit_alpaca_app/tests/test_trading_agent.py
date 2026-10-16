from __future__ import annotations

import json
from http.client import RemoteDisconnected

import numpy as np
import pandas as pd
import requests

from services.llm import LLMAPIError
from services.trading_agent import (
    TRADING_AGENT_HORIZON_SPECS,
    TRADING_AGENT_NARRATIVE_CONTRACT_VERSION,
    _balanced_shared_research_pool,
    build_trading_agent_context,
    build_trading_agent_outcomes,
    build_trading_agent_research_reviews,
    build_trading_agent_shared_research_frames,
)


class FakeTradingLLM:
    def __init__(self) -> None:
        self.user_prompt = ""
        self.kwargs = {}

    def generate_json(self, **kwargs):
        self.kwargs = kwargs
        self.user_prompt = kwargs["user_prompt"]
        return {
            "regime_read": "Momentum is positive but macro confirmation is incomplete.",
            "portfolio_posture": "Stay selective and require confirmation.",
            "candidates": [
                {
                    "ticker": "aapl",
                    "direction": "long",
                    "stance": "long",
                    "research_status": "supported",
                    "setup": "Breakout continuation",
                    "hypothesis": "Momentum can continue if the market stays supportive.",
                    "evidence": ["Opportunity score is high.", "Technical trend is positive."],
                    "invalidation": "Break below support.",
                    "tail_risks": ["Macro shock", "Earnings reset"],
                    "evidence_limits": ["No options positioning evidence."],
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


def test_trading_agent_outcomes_score_matured_and_mark_to_market_calls():
    candidates = pd.DataFrame(
        [
            {
                "candidate_id": "cand_aapl_1w",
                "trading_agent_run_id": "run:1w",
                "run_id": "run",
                "horizon_key": "1w",
                "horizon_label": "1 Week",
                "rank": 1,
                "ticker": "AAPL",
                "direction": "long",
                "setup": "Breakout continuation",
                "hypothesis": "Momentum can persist.",
                "evidence_json": '["Opportunity score is high."]',
                "tail_risks_json": '["Macro reversal"]',
                "confidence": "medium",
                "asof_time_utc": "2026-06-01T13:00:00Z",
                "generated_at_utc": "2026-06-01T13:00:00Z",
            },
            {
                "candidate_id": "cand_msft_1m",
                "trading_agent_run_id": "run:1m",
                "run_id": "run",
                "horizon_key": "1m",
                "horizon_label": "1 Month",
                "rank": 1,
                "ticker": "MSFT",
                "direction": "short",
                "setup": "Risk control",
                "hypothesis": "Weak momentum can continue.",
                "evidence_json": '["Momentum is fading."]',
                "tail_risks_json": '["Relief rally"]',
                "confidence": "low",
                "asof_time_utc": "2026-06-20T13:00:00Z",
                "generated_at_utc": "2026-06-20T13:00:00Z",
            },
        ]
    )
    price_history = pd.DataFrame(
        [
            {"symbol": "AAPL", "timestamp": "2026-06-01T00:00:00Z", "close": 100.0},
            {"symbol": "AAPL", "timestamp": "2026-06-08T00:00:00Z", "close": 112.0},
            {"symbol": "MSFT", "timestamp": "2026-06-22T00:00:00Z", "close": 200.0},
            {"symbol": "MSFT", "timestamp": "2026-06-24T00:00:00Z", "close": 190.0},
            {"symbol": "SPY", "timestamp": "2026-06-01T00:00:00Z", "close": 100.0},
            {"symbol": "SPY", "timestamp": "2026-06-08T00:00:00Z", "close": 103.0},
            {"symbol": "SPY", "timestamp": "2026-06-22T00:00:00Z", "close": 104.0},
            {"symbol": "SPY", "timestamp": "2026-06-24T00:00:00Z", "close": 106.0},
        ]
    )

    outcomes = build_trading_agent_outcomes(
        candidates=candidates,
        price_history=price_history,
        evaluation_time_utc="2026-06-24T15:00:00Z",
    )
    rows = {row["candidate_id"]: row for row in outcomes.to_dict("records")}

    assert rows["cand_aapl_1w"]["outcome_status"] == "matured"
    assert rows["cand_aapl_1w"]["is_matured"] is True
    assert rows["cand_aapl_1w"]["is_win"] is True
    assert np.isclose(rows["cand_aapl_1w"]["signed_return_pct"], 12.0)
    assert np.isclose(rows["cand_aapl_1w"]["benchmark_return_pct"], 3.0)
    assert np.isclose(rows["cand_aapl_1w"]["excess_signed_return_pct"], 9.0)

    assert rows["cand_msft_1m"]["outcome_status"] == "mark_to_market"
    assert rows["cand_msft_1m"]["is_matured"] is False
    assert rows["cand_msft_1m"]["is_win"] is True
    assert np.isclose(rows["cand_msft_1m"]["raw_return_pct"], -5.0)
    assert np.isclose(rows["cand_msft_1m"]["signed_return_pct"], 5.0)
    assert np.isclose(rows["cand_msft_1m"]["benchmark_return_pct"], (106.0 / 104.0 - 1.0) * 100.0)
    assert np.isclose(
        rows["cand_msft_1m"]["excess_signed_return_pct"],
        (-5.0 - (106.0 / 104.0 - 1.0) * 100.0) * -1.0,
    )


def test_trading_agent_outcomes_do_not_score_watch_or_avoid_research_rows():
    candidates = pd.DataFrame(
        [
            {
                "candidate_id": "cand_watch",
                "run_id": "run",
                "horizon_key": "1w",
                "ticker": "AAPL",
                "direction": "watch",
                "asof_time_utc": "2026-06-01T13:00:00Z",
            },
            {
                "candidate_id": "cand_avoid",
                "run_id": "run",
                "horizon_key": "1w",
                "ticker": "MSFT",
                "direction": "avoid",
                "asof_time_utc": "2026-06-01T13:00:00Z",
            },
            {
                "candidate_id": "cand_unsupported_long",
                "run_id": "run",
                "horizon_key": "1w",
                "ticker": "NVDA",
                "direction": "long",
                "stance": "long",
                "research_status": "insufficient_evidence",
                "asof_time_utc": "2026-06-01T13:00:00Z",
            },
        ]
    )
    price_history = pd.DataFrame(
        [
            {"symbol": "AAPL", "timestamp": "2026-06-01T00:00:00Z", "close": 100.0},
            {"symbol": "AAPL", "timestamp": "2026-06-08T00:00:00Z", "close": 110.0},
            {"symbol": "MSFT", "timestamp": "2026-06-01T00:00:00Z", "close": 200.0},
            {"symbol": "MSFT", "timestamp": "2026-06-08T00:00:00Z", "close": 180.0},
        ]
    )

    outcomes = build_trading_agent_outcomes(
        candidates=candidates,
        price_history=price_history,
        evaluation_time_utc="2026-06-08T15:00:00Z",
    ).set_index("candidate_id")

    assert set(outcomes["outcome_status"]) == {"not_scoreable"}
    assert set(outcomes["scoring_direction"]) == {"no_call"}
    assert outcomes["signed_return_pct"].isna().all()
    assert outcomes["is_win"].isna().all()
    assert outcomes.loc["cand_unsupported_long", "stance"] == "no_call"
    assert outcomes.loc["cand_unsupported_long", "research_status"] == "insufficient_evidence"


def test_trading_agent_research_reviews_use_bounded_llm_payload():
    class ReviewLLM:
        def __init__(self) -> None:
            self.user_prompt = ""

        def generate_json(self, **kwargs):
            self.user_prompt = kwargs["user_prompt"]
            return {
                "review_status": "completed",
                "outcome_summary": "The long thesis worked over the measured horizon.",
                "thesis_verdict": "validated",
                "direction_verdict": "right_direction",
                "horizon_verdict": "appropriate",
                "evidence_verdict": "mixed",
                "primary_failure_mode": "",
                "primary_success_factor": "Momentum evidence matched the price path.",
                "missing_evidence_slots": ["Demand quality"],
                "recommended_contract_change": "Require demand quality when available.",
                "recommended_prompt_change": "Ask for the expected mechanism explicitly.",
                "recommended_retrieval_change": "Retrieve recent company evidence before synthesis.",
                "recommended_candidate_selection_change": "Keep watchlist selection unchanged until more outcomes mature.",
                "source_refs": ["candidate:evidence_json"],
            }

    candidates = pd.DataFrame(
        [
            {
                "candidate_id": "cand_aapl_1w",
                "trading_agent_run_id": "run:1w",
                "ticker": "AAPL",
                "setup": "Breakout continuation",
                "hypothesis": "Momentum can persist.",
                "evidence_json": '["Opportunity score is high."]',
                "tail_risks_json": '["Macro reversal"]',
                "confidence": "medium",
            }
        ]
    )
    runs = pd.DataFrame(
        [
            {
                "trading_agent_run_id": "run:1w",
                "status": "ok",
                "regime_read": "Momentum regime.",
                "portfolio_posture": "Selective.",
                "data_gaps_json": '["No options evidence."]',
                "aql_agent_json": '{"status":"completed"}',
            }
        ]
    )
    outcomes = pd.DataFrame(
        [
            {
                "outcome_id": "tao_aapl",
                "candidate_id": "cand_aapl_1w",
                "trading_agent_run_id": "run:1w",
                "run_id": "run",
                "horizon_key": "1w",
                "ticker": "AAPL",
                "direction": "long",
                "scoring_direction": "long",
                "outcome_status": "matured",
                "is_win": True,
                "is_matured": True,
                "raw_return_pct": 12.0,
                "signed_return_pct": 12.0,
                "entry_ts": "2026-06-01T00:00:00Z",
                "entry_close": 100.0,
                "target_exit_ts": "2026-06-08T00:00:00Z",
                "actual_exit_ts": "2026-06-08T00:00:00Z",
                "exit_close": 112.0,
            }
        ]
    )
    llm = ReviewLLM()

    reviews = build_trading_agent_research_reviews(
        outcomes=outcomes,
        candidates=candidates,
        runs=runs,
        llm_client=llm,
        max_reviews=1,
        review_time_utc="2026-06-24T15:00:00Z",
    )

    assert len(reviews) == 1
    row = reviews.iloc[0]
    assert row["review_status"] == "completed"
    assert row["thesis_verdict"] == "validated"
    assert json.loads(row["missing_evidence_slots_json"]) == ["Demand quality"]
    review_input = json.loads(row["review_input_json"])
    assert review_input["review_contract"]["may_mutate_prompt_or_memory"] is False
    assert review_input["candidate"]["hypothesis"] == "Momentum can persist."
    assert review_input["outcome"]["signed_return_pct"] == 12.0
    assert "Review input JSON" in llm.user_prompt


def test_trading_agent_context_retrieves_by_horizon_before_rrf_reranking():
    opportunity_feed = pd.DataFrame(
        [
            {
                "business_filter": "All Market",
                "selected_horizon_col": "return_1m_pct",
                "rank": 1,
                "symbol": "DAILY",
                "return_1m_pct": 1.0,
                "horizon_retrieval_rank": 50,
                "opportunity_score": 99.0,
                "retrieval_method": "reciprocal_rank_fusion_v1",
            },
            {
                "business_filter": "All Market",
                "selected_horizon_col": "return_1m_pct",
                "rank": 2,
                "symbol": "BROAD",
                "return_1m_pct": 8.0,
                "horizon_retrieval_rank": 2,
                "opportunity_score": 70.0,
                "retrieval_method": "reciprocal_rank_fusion_v1",
            },
            {
                "business_filter": "All Market",
                "selected_horizon_col": "return_1m_pct",
                "rank": 3,
                "symbol": "FOCUSED",
                "return_1m_pct": 9.0,
                "horizon_retrieval_rank": 1,
                "opportunity_score": 65.0,
                "retrieval_method": "reciprocal_rank_fusion_v1",
            },
        ]
    )

    context, _ = build_trading_agent_context(
        opportunity_feed=opportunity_feed,
        page_summaries=pd.DataFrame(),
        horizon_key="1m",
        horizon_col="return_1m_pct",
        horizon_label="1 Month",
        max_candidates=2,
        research_candidate_limit=2,
    )

    assert [item["ticker"] for item in context["ticker_evidence"]] == ["BROAD", "FOCUSED"]
    assert context["controls"]["candidate_retrieval_method"] == "horizon_then_rrf_v1"
    assert context["controls"]["candidate_retrieval_depth"] == 8


def test_shared_research_reuses_one_company_package_across_horizons():
    opportunity_feed = pd.DataFrame(
        [
            {
                "business_filter": "All Market",
                "selected_horizon_col": spec["column"],
                "selected_horizon_label": spec["label"],
                "rank": 1,
                "symbol": "AAPL",
                "company_name": "Apple Inc.",
                "opportunity": "Business momentum",
                "opportunity_score": 90.0,
                spec["column"]: 5.0,
            }
            for spec in TRADING_AGENT_HORIZON_SPECS
        ]
    )
    research_calls = []

    def _agent(**kwargs):
        research_calls.append(kwargs)
        return {
            "run_id": "agrun_aapl",
            "status": "completed",
            "answer_markdown": "Apple demand and margins support a selective watch thesis.",
            "confidence": "medium",
            "limitations": [],
            "tool_calls": [],
        }

    runs, candidates, packages = build_trading_agent_shared_research_frames(
        opportunity_feed=opportunity_feed,
        page_summaries=pd.DataFrame(),
        llm_client=FakeTradingLLM(),
        run_id="shared-run",
        asof_time_utc="2026-08-10T20:00:00Z",
        max_candidates=1,
        research_candidate_limit=1,
        research_pool_limit=4,
        aql_agent_runner=_agent,
    )

    assert len(research_calls) == 1
    assert research_calls[0]["persist_findings"] is False
    assert len(packages) == 1
    assert json.loads(packages.iloc[0]["horizon_keys_json"]) == ["1w", "1m", "3m", "1y", "5y"]
    assert runs["status"].tolist() == ["ok"] * 5
    assert candidates["horizon_key"].tolist() == ["1w", "1m", "3m", "1y", "5y"]


def test_shared_research_pool_limit_controls_recall_without_fabricating_candidates():
    horizons = (
        {"key": "1w", "column": "return_7d_pct", "label": "1 Week"},
        {"key": "1m", "column": "return_1m_pct", "label": "1 Month"},
    )
    opportunity_feed = pd.DataFrame(
        [
            {
                "business_filter": "All Market",
                "selected_horizon_col": "return_7d_pct",
                "selected_horizon_label": "1 Week",
                "rank": 1,
                "symbol": "AAPL",
                "company_name": "Apple Inc.",
                "opportunity": "Weekly change",
                "opportunity_score": 91.0,
                "return_7d_pct": 6.0,
            },
            {
                "business_filter": "All Market",
                "selected_horizon_col": "return_1m_pct",
                "selected_horizon_label": "1 Month",
                "rank": 1,
                "symbol": "MSFT",
                "company_name": "Microsoft Corporation",
                "opportunity": "Monthly change",
                "opportunity_score": 92.0,
                "return_1m_pct": 12.0,
            },
        ]
    )

    class TwoTickerLLM:
        def generate_json(self, **kwargs):
            return {
                "regime_read": "Selective company moves.",
                "portfolio_posture": "Research the supported names.",
                "candidates": [
                    {
                        "ticker": ticker,
                        "direction": "watch",
                        "setup": f"{ticker} setup",
                        "hypothesis": f"{ticker} evidence may support continuation.",
                        "evidence": [f"{ticker} package completed."],
                        "invalidation": "Business evidence weakens.",
                        "tail_risks": ["Market reversal"],
                        "suggested_horizon": "Selected horizon",
                        "confidence": "medium",
                    }
                    for ticker in ["AAPL", "MSFT"]
                ],
                "data_gaps": [],
            }

    research_calls = []

    def _agent(**kwargs):
        ticker = "AAPL" if "Focus ticker: AAPL" in kwargs["query"] else "MSFT"
        research_calls.append(ticker)
        return {
            "run_id": f"agrun_{ticker.lower()}",
            "status": "completed",
            "answer_markdown": f"{ticker} package completed.",
            "confidence": "medium",
            "limitations": [],
            "tool_calls": [],
        }

    runs, candidates, packages = build_trading_agent_shared_research_frames(
        opportunity_feed=opportunity_feed,
        page_summaries=pd.DataFrame(),
        llm_client=TwoTickerLLM(),
        run_id="bounded-run",
        asof_time_utc="2026-08-10T20:00:00Z",
        max_candidates=1,
        research_candidate_limit=1,
        research_pool_limit=1,
        horizon_specs=horizons,
        aql_agent_runner=_agent,
    )

    assert research_calls == ["MSFT"]
    assert packages["ticker"].tolist() == ["MSFT"]
    assert runs["status"].tolist() == ["error", "ok"]
    assert candidates["ticker"].tolist() == ["MSFT"]
    assert candidates["stance"].tolist() == ["no_call"]
    assert candidates["decision_status"].tolist() == ["research_only"]


def test_shared_research_pool_ranks_a_complete_horizon_tier_before_cutoff():
    horizon_contexts = []
    for key, ticker, score in [
        ("1w", "AAPL", 91.0),
        ("1m", "MSFT", 92.0),
        ("3m", "NVDA", 93.0),
    ]:
        opportunity = {"symbol": ticker, "opportunity_score": score}
        horizon_contexts.append(
            (
                {"key": key, "column": f"return_{key}_pct", "label": key},
                {
                    "controls": {"research_candidate_limit": 1},
                    "market_opportunity_feed": [opportunity],
                    "ticker_evidence": [
                        {
                            "ticker": ticker,
                            "company_name": ticker,
                            "market_opportunity": opportunity,
                        }
                    ],
                },
            )
        )

    pool = _balanced_shared_research_pool(horizon_contexts, research_pool_limit=2)

    assert [item["ticker"] for item in pool] == ["NVDA", "MSFT"]
    assert pool[0]["selection_trace"] == {
        "method": "horizon_tier_rrf_v1",
        "pool_rank": 1,
        "rrf_score": round(1.0 / 61.0, 8),
        "horizon_ranks": {"3m": 1},
    }


def test_shared_research_timeout_fails_closed_without_price_only_candidates():
    opportunity_feed = pd.DataFrame(
        [
            {
                "business_filter": "All Market",
                "selected_horizon_col": "return_7d_pct",
                "selected_horizon_label": "1 Week",
                "rank": 1,
                "symbol": "AAPL",
                "company_name": "Apple Inc.",
                "opportunity": "Weekly change",
                "opportunity_score": 91.0,
                "return_7d_pct": 6.0,
            }
        ]
    )

    def _timeout_agent(**kwargs):
        raise TimeoutError("AQL research exceeded its bounded step budget.")

    runs, candidates, packages = build_trading_agent_shared_research_frames(
        opportunity_feed=opportunity_feed,
        page_summaries=pd.DataFrame(),
        llm_client=FakeTradingLLM(),
        run_id="failed-run",
        asof_time_utc="2026-08-10T20:00:00Z",
        max_candidates=1,
        research_candidate_limit=1,
        research_pool_limit=1,
        horizon_specs=({"key": "1w", "column": "return_7d_pct", "label": "1 Week"},),
        aql_agent_runner=_timeout_agent,
    )

    assert packages.iloc[0]["status"] == "failed"
    assert runs.iloc[0]["status"] == "error"
    assert candidates.empty


def test_shared_research_excludes_failed_company_from_mixed_horizon_synthesis():
    opportunity_feed = pd.DataFrame(
        [
            {
                "business_filter": "All Market",
                "selected_horizon_col": "return_7d_pct",
                "selected_horizon_label": "1 Week",
                "rank": rank,
                "symbol": ticker,
                "company_name": company,
                "opportunity": "Weekly change",
                "opportunity_score": score,
                "return_7d_pct": move,
            }
            for rank, ticker, company, score, move in [
                (1, "AAPL", "Apple Inc.", 91.0, 6.0),
                (2, "MSFT", "Microsoft Corporation", 89.0, -5.0),
            ]
        ]
    )

    class InspectingLLM:
        def __init__(self):
            self.prompts = []

        def generate_json(self, **kwargs):
            self.prompts.append(kwargs["user_prompt"])
            return {
                "regime_read": "Selective company moves.",
                "portfolio_posture": "Research the supported name.",
                "candidates": [
                    {
                        "ticker": "AAPL",
                        "direction": "watch",
                        "setup": "AAPL setup",
                        "hypothesis": "Completed evidence supports monitoring.",
                        "evidence": ["AAPL package completed."],
                        "invalidation": "Business evidence weakens.",
                        "tail_risks": ["Market reversal"],
                        "suggested_horizon": "1 Week",
                        "confidence": "medium",
                    }
                ],
                "data_gaps": [],
            }

    def _agent(**kwargs):
        if "Focus ticker: MSFT" in kwargs["query"]:
            raise RuntimeError("provider returned empty content")
        return {
            "run_id": "agrun_aapl",
            "status": "completed",
            "answer_markdown": "AAPL package completed.",
            "confidence": "medium",
            "limitations": [],
            "tool_calls": [],
        }

    llm = InspectingLLM()
    runs, candidates, packages = build_trading_agent_shared_research_frames(
        opportunity_feed=opportunity_feed,
        page_summaries=pd.DataFrame(),
        llm_client=llm,
        run_id="mixed-run",
        asof_time_utc="2026-08-10T20:00:00Z",
        max_candidates=2,
        research_candidate_limit=2,
        research_pool_limit=2,
        horizon_specs=({"key": "1w", "column": "return_7d_pct", "label": "1 Week"},),
        aql_agent_runner=_agent,
    )

    assert packages.set_index("ticker")["status"].to_dict() == {
        "AAPL": "completed",
        "MSFT": "failed",
    }
    assert runs.iloc[0]["status"] == "ok"
    assert candidates["ticker"].tolist() == ["AAPL"]
    assert len(llm.prompts) == 1
    assert '"ticker":"MSFT"' not in llm.prompts[0]
