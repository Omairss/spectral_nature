from __future__ import annotations

import json

import pandas as pd
from streamlit.testing.v1 import AppTest

from views import trading_agent as trading_agent_view


def test_load_latest_trading_agent_output_pairs_matching_recent_versions(monkeypatch):
    class Metadata:
        dataset_version_id = "trading_agent_runs__new"
        asof_time_utc = "2026-06-22T20:35:00Z"

    runs = pd.DataFrame(
        [
            {
                "run_id": "run-new",
                "horizon_key": "1w",
                "generated_at_utc": "2026-06-22T20:35:00Z",
            }
        ]
    )
    stale_candidates = pd.DataFrame(
        [
            {
                "run_id": "run-old",
                "horizon_key": "1w",
                "ticker": "OLD",
            }
        ]
    )
    matching_candidates = pd.DataFrame(
        [
            {
                "run_id": "run-new",
                "horizon_key": "1w",
                "ticker": "AAPL",
            }
        ]
    )

    def _fake_recent(dataset_name: str, *, limit: int = 8):
        if dataset_name == "trading_agent_runs":
            return [(runs.copy(), Metadata())]
        if dataset_name == "trading_agent_candidates":
            return [
                (stale_candidates.copy(), object()),
                (matching_candidates.copy(), object()),
            ]
        return []

    monkeypatch.setattr(trading_agent_view, "load_recent_dataset_frames", _fake_recent)
    monkeypatch.setattr(
        trading_agent_view,
        "load_latest_dataset_frame",
        lambda dataset_name: (_ for _ in ()).throw(AssertionError("recent frames should satisfy the view")),
    )
    monkeypatch.setattr(trading_agent_view, "trading_agent_actions_table", lambda limit=500: pd.DataFrame())

    loaded_runs, loaded_candidates, actions, metadata = trading_agent_view._load_latest_trading_agent_output()

    assert loaded_runs["run_id"].tolist() == ["run-new"]
    assert loaded_candidates["ticker"].tolist() == ["AAPL"]
    assert actions.empty
    assert getattr(metadata, "dataset_version_id") == "trading_agent_runs__new"


def test_legacy_watch_and_avoid_rows_render_as_research_only():
    watch = trading_agent_view._trading_agent_candidate_from_row(
        pd.Series(
            {
                "candidate_id": "watch-1",
                "ticker": "AAPL",
                "direction": "watch",
                "candidate_json": '{"ticker":"AAPL","direction":"watch"}',
            }
        )
    )
    avoid = trading_agent_view._trading_agent_candidate_from_row(
        pd.Series(
            {
                "candidate_id": "avoid-1",
                "ticker": "MSFT",
                "direction": "avoid",
                "candidate_json": '{"ticker":"MSFT","direction":"avoid"}',
            }
        )
    )

    assert watch["stance"] == "no_call"
    assert watch["research_status"] == "watch"
    assert watch["decision_status"] == "research_only"
    assert watch["narrative_contract_version"] == ""
    assert avoid["stance"] == "no_call"
    assert avoid["research_status"] == "avoid"
    assert avoid["decision_status"] == "research_only"
    assert avoid["narrative_contract_version"] == ""


def test_current_candidate_keeps_plain_language_contract_and_evidence_limits():
    candidate = trading_agent_view._trading_agent_candidate_from_row(
        pd.Series(
            {
                "candidate_id": "current-1",
                "ticker": "AAPL",
                "candidate_json": json.dumps(
                    {
                        "ticker": "AAPL",
                        "direction": "watch",
                        "narrative_contract_version": trading_agent_view.TRADING_AGENT_NARRATIVE_CONTRACT_VERSION,
                        "evidence_limits": ["A current revenue forecast is still needed."],
                    }
                ),
            }
        )
    )

    assert (
        candidate["narrative_contract_version"]
        == trading_agent_view.TRADING_AGENT_NARRATIVE_CONTRACT_VERSION
    )
    assert candidate["evidence_limits"] == ["A current revenue forecast is still needed."]


def test_signal_model_exposes_investor_metrics_without_scanner_diagnostics():
    model = trading_agent_view._trading_agent_signal_model(
        {
            "daily_change_pct": 7.38,
            "return_7d_pct": 31.61,
            "direction": "Up / cooling",
            "opportunity_score": 96,
            "momentum_roc_score": -11.44,
            "trend_fit_gap": 0.2,
            "asof_time_utc": "2026-09-20T00:55:38Z",
        },
        {
            "selected_horizon_col": "return_7d_pct",
            "momentum_horizon": "1 Week",
        },
    )

    assert model["daily_move"] == 7.38
    assert model["horizon_return"] == 31.61
    assert model["market_pattern"] == "Up / cooling"
    assert model["freshness"] == "Price data as of 2026-09-20 00:55 UTC"
    assert "rank" not in model
    assert "score_components" not in model
    assert "momentum_roc" not in model


def test_time_label_formats_dataset_timestamp_for_people():
    assert (
        trading_agent_view._trading_agent_time_label("2026-09-21T01:28:23.331526+00:00")
        == "2026-09-21 01:28 UTC"
    )


def test_robinhood_snapshot_helpers_find_nested_values_without_account_identifiers():
    payload = {
        "account": {
            "account_number": "123456789",
            "portfolio": {"portfolio_value": "12500.25", "buying_power": "450.50"},
        }
    }

    assert trading_agent_view._robinhood_first_value(payload, "portfolio_value") == "12500.25"
    assert trading_agent_view._robinhood_first_value(payload, "buying_power") == "450.50"
    assert trading_agent_view._robinhood_money("12500.25") == "$12,500.25"


def test_robinhood_equity_order_renders_one_review_and_one_place_action():
    app = AppTest.from_string(
        """
from views import trading_agent as view

view.robinhood_mcp.execution_enabled = lambda: True
view._render_robinhood_equity_order(
    user_id="22222222-2222-2222-2222-222222222222",
    snapshot={
        "agentic_accounts": [
            {"account_number": "123456789", "label": "individual ••••6789"}
        ]
    },
)
"""
    )

    app.run()

    assert not app.exception
    assert [button.label for button in app.button] == ["Review order"]
    assert [expander.label for expander in app.expander] == ["Equity order"]


def test_unsupported_directional_row_cannot_render_as_an_open_call():
    candidate = trading_agent_view._trading_agent_candidate_from_row(
        pd.Series(
            {
                "candidate_id": "unsupported-long",
                "ticker": "NVDA",
                "direction": "long",
                "stance": "long",
                "research_status": "insufficient_evidence",
                "decision_status": "open",
                "candidate_json": "{}",
            }
        )
    )

    assert candidate["stance"] == "no_call"
    assert candidate["research_status"] == "insufficient_evidence"
    assert candidate["direction"] == "watch"
    assert candidate["decision_status"] == "research_only"
