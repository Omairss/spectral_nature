from __future__ import annotations

import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from presentation import dashboard_loaders


def test_load_ticker_snapshot_profile_can_skip_live_fallback(monkeypatch):
    monkeypatch.setattr(dashboard_loaders, "_load_attention_ticker_snapshot_map_cached", lambda force_refresh=False: {})
    monkeypatch.setattr(
        dashboard_loaders,
        "_load_universe_security_name_map",
        lambda force_refresh=False: {"CVX": "Chevron Corporation"},
    )
    monkeypatch.setattr(
        dashboard_loaders,
        "_load_attention_ticker_snapshot_cached",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("snapshot fallback should not run")),
    )
    monkeypatch.setattr(
        dashboard_loaders,
        "_load_asset_metadata_cached",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("asset fallback should not run")),
    )
    monkeypatch.setattr(
        dashboard_loaders,
        "_load_price_history_cached",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("price fallback should not run")),
    )
    monkeypatch.setattr(
        dashboard_loaders,
        "_load_public_price_history_cached",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("public price fallback should not run")),
    )

    profile = dashboard_loaders._load_ticker_snapshot_profile(
        object(),
        "CVX",
        allow_live_fallback=False,
    )

    assert profile == {
        "symbol": "CVX",
        "company_name": "Chevron Corporation",
        "market_cap_label": "n/a",
        "sparkline_data_uri": "",
    }
