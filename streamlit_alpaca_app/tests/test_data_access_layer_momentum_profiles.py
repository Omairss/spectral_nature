from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data_access.layer import DataAccessLayer


def test_resolve_momentum_profiles_falls_back_when_materialized_slice_is_empty(monkeypatch):
    import data_access.layer as layer_module

    materialized = pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT"],
            "momentum_score": [1.0, 0.8],
        }
    )
    live = pd.DataFrame(
        {
            "symbol": ["USO", "GLD"],
            "momentum_score": [0.5, 0.4],
        }
    )
    cfg = SimpleNamespace(alpaca_api_key="key")

    monkeypatch.setattr(
        DataAccessLayer,
        "_try_pipeline_frame",
        lambda self, dataset_name, force_refresh: (materialized.copy(), {"dataset_name": dataset_name}),
    )
    monkeypatch.setattr(layer_module, "cached_frame", lambda *args, **kwargs: live.copy())

    resolved = DataAccessLayer(cfg=cfg).resolve_momentum_profiles(days=252, symbols=["USO", "GLD"])

    assert resolved.provenance.mode == "on_demand"
    assert resolved.provenance.datasets == ("momentum_profiles",)
    assert resolved.provenance.details["symbols"] == ["GLD", "USO"]
    assert resolved.payload["symbol"].tolist() == ["USO", "GLD"]


def test_resolve_momentum_profiles_materialized_only_keeps_empty_filtered_slice(monkeypatch):
    import data_access.layer as layer_module

    materialized = pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT"],
            "momentum_score": [1.0, 0.8],
        }
    )

    monkeypatch.setattr(
        DataAccessLayer,
        "_try_pipeline_frame",
        lambda self, dataset_name, force_refresh: (materialized.copy(), {"dataset_name": dataset_name}),
    )
    monkeypatch.setattr(
        layer_module,
        "cached_frame",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live fallback should not run")),
    )

    resolved = DataAccessLayer(materialized_only=True).resolve_momentum_profiles(days=252, symbols=["USO"])

    assert resolved.provenance.mode == "materialized"
    assert resolved.payload.empty
    assert resolved.provenance.details["symbols"] == ["USO"]
