import json

import pandas as pd
import pytest

from services import attention_ticker_snapshots as snapshots


def test_intraday_change_uses_prior_regular_session_and_respects_asof(monkeypatch):
    from services import alpaca_api, config

    class MarketData:
        def __init__(self, _config):
            pass

        def get_stock_bars(self, _symbols, **_kwargs):
            return {"EXAMPLE": pd.DataFrame([
                {"timestamp": "2026-10-12T19:55:00Z", "close": 100},
                {"timestamp": "2026-10-13T19:55:00Z", "close": 110},
                {"timestamp": "2026-10-13T20:00:00Z", "close": 900},
                {"timestamp": "2026-10-14T14:00:00Z", "close": 900},
            ])}

    monkeypatch.setattr(config, "load_config", lambda: object())
    monkeypatch.setattr(alpaca_api, "AlpacaAPI", MarketData)
    result = snapshots.prefetch_attention_intraday_prices(
        ["EXAMPLE", "UNAVAILABLE"], asof_time_utc="2026-10-13T19:59:00Z")
    chart = result["EXAMPLE"]
    assert chart["session"] == "2026-10-13"
    assert chart["previous_close_at"] == "2026-10-12T19:55:00+00:00"
    assert chart["change_pct"] == pytest.approx(10)
    assert len(chart["points"]) == 1
    assert result["UNAVAILABLE"]["points"] == []
    assert result["UNAVAILABLE"]["change_pct"] is None


def test_stale_daily_cache_cannot_overwrite_prefetched_session_change(monkeypatch):
    monkeypatch.setattr(snapshots, "share_counts_asof", lambda *_args, **_kwargs: {})
    intraday = {"session": "2026-10-13", "change_pct": 10,
                "points": [{"date": "2026-10-13T19:55:00+00:00", "value": 110}]}
    frame = snapshots.build_attention_ticker_snapshot_frame(
        ["EXAMPLE"],
        price_history_frame=pd.DataFrame([
            {"symbol": "EXAMPLE", "timestamp": "2026-10-09T00:00:00Z", "close": 500}]),
        universe_snapshot_frame=pd.DataFrame(), quarterly_fundamentals_frame=pd.DataFrame(),
        intraday_prices={"EXAMPLE": intraday},
        asof_time_utc="2026-10-13T19:59:00Z", run_id="session-proof")
    retained = json.loads(frame.iloc[0]["intraday_price_history_json"])
    assert retained["change_pct"] == 10
