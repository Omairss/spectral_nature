from __future__ import annotations

import pandas as pd

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
