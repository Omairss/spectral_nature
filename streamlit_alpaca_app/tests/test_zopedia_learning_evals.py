from __future__ import annotations

import json
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
EVAL_CASE = (
    APP_ROOT
    / "documents"
    / "architecture"
    / "new_features"
    / "zopedia"
    / "eval_cases"
    / "generated"
    / "bond_yields_market_impact_price_history_recovery.json"
)


def test_bond_yield_recovery_eval_fixture_matches_benchmark_contract():
    payload = json.loads(EVAL_CASE.read_text())

    assert payload["thread_source"] == "zthread_61ef63346b654add"
    assert "bond market" in payload["question"].lower()
    assert any("dataset.price_history" in item or "dataset.daily_movers" in item for item in payload["required_tool_patterns"])
    assert any("broad equity benchmark" in item for item in payload["required_tool_patterns"])
    assert any("duration/growth" in item for item in payload["required_tool_patterns"])
    assert any("financial/credit" in item for item in payload["required_tool_patterns"])
    assert "stock data is unavailable" in payload["forbidden_answer_patterns"]
    assert any("causal proof" in item for item in payload["required_answer_claims"])
    assert "bear-steepening" in payload["benchmark_answer_summary"]


def test_generated_learning_evals_include_executable_forbidden_patterns():
    generated = sorted(EVAL_CASE.parent.glob("what_is_the_impact_of_the_bond_market_today_*.json"))
    assert generated

    for path in generated:
        payload = json.loads(path.read_text())
        patterns = [str(item) for item in payload.get("forbidden_answer_patterns") or []]
        assert patterns
        assert any(
            literal in patterns
            for literal in [
                "stock data is unavailable",
                "cannot query actual stocks",
                "daily movers returned empty, so no market data",
                "tool_call_id",
                "run_id",
            ]
        )
