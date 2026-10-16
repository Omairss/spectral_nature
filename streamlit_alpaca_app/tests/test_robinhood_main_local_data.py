from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from services import robinhood_main, robinhood_main_analytics


PORTFOLIO_ID = "33333333-3333-3333-3333-333333333333"
CONNECTION_ID = "44444444-4444-4444-4444-444444444444"


def test_every_robinhood_lookback_is_cut_from_local_history():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2021-01-01", "2025-01-01", "2026-09-01", "2026-10-01"],
                utc=True,
            ),
            "portfolio": [100.0, 130.0, 150.0, 160.0],
        }
    )

    assert robinhood_main_analytics.LOOKBACK_OPTIONS == (
        "1D", "1W", "1M", "3M", "YTD", "1Y", "5Y", "All"
    )
    assert len(robinhood_main_analytics.filter_lookback(frame, "5Y")) == 3
    assert len(robinhood_main_analytics.filter_lookback(frame, "1M")) == 2
    assert len(robinhood_main_analytics.filter_lookback(frame, "All")) == 4


@pytest.mark.parametrize("refresh_status", ["queued", "running"])
def test_portfolio_data_status_maps_active_queue_states_to_in_progress(
    monkeypatch,
    refresh_status,
):
    monkeypatch.setattr(robinhood_main, "_ensure_store", lambda: True)
    monkeypatch.setattr(robinhood_main, "_ensure_data_store", lambda: True)
    monkeypatch.setattr(
        robinhood_main.auth_store,
        "user_has_active_portfolio_membership",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        robinhood_main.brokerage_mcp_store,
        "get_portfolio_brokerage",
        lambda **kwargs: {"brokerage_source": robinhood_main.PROVIDER},
    )
    monkeypatch.setattr(robinhood_main, "_portfolio_read_connection", lambda **kwargs: {})
    monkeypatch.setattr(robinhood_main.robinhood_main_store, "get_snapshot", lambda **kwargs: None)
    monkeypatch.setattr(
        robinhood_main.robinhood_main_store,
        "latest_refresh",
        lambda **kwargs: {"status": refresh_status, "requested_at": "2026-10-02T00:00:00Z"},
    )

    status = robinhood_main.portfolio_data_status(
        user_id="member",
        portfolio_id=PORTFOLIO_ID,
    )

    assert status["state"] == "in_progress"
    assert status["refresh_status"] == "in_progress"
    assert status["snapshot_ready"] is False


def test_opportunity_history_excludes_non_equity_contract_symbols():
    assert robinhood_main._is_equity_history_symbol("BRK.B")
    assert robinhood_main._is_equity_history_symbol("BTCZ")
    assert not robinhood_main._is_equity_history_symbol("/6AZ26")
    assert not robinhood_main._is_equity_history_symbol("BTC-USD")


def test_group_analytics_uses_one_local_symbol_history_for_multiple_groups(monkeypatch):
    resources = [
        {
            "resource_key": "group-a",
            "payload": {
                "name": "Growth",
                "kind": "watchlist",
                "status": "ready",
                "analytics_status": "ready",
                "members": [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
            },
        },
        {
            "resource_key": "group-b",
            "payload": {
                "name": "Mega Cap",
                "kind": "curated list",
                "status": "ready",
                "analytics_status": "ready",
                "members": [{"symbol": "AAPL"}, {"symbol": "GOOG"}],
            },
        },
    ]
    points = []
    for symbol, values in {"AAPL": [100, 120], "MSFT": [100, 90], "GOOG": [100, 140]}.items():
        for timestamp, close in zip(
            pd.to_datetime(["2026-09-01", "2026-10-01"], utc=True), values
        ):
            points.append(
                {
                    "series_key": symbol,
                    "observed_at": timestamp,
                    "values": {"close": close},
                }
            )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        robinhood_main_analytics.robinhood_main_store,
        "list_resources",
        lambda **kwargs: resources,
    )

    def _series(**kwargs):
        captured.update(kwargs)
        return points

    monkeypatch.setattr(
        robinhood_main_analytics.robinhood_main_store,
        "get_series_points",
        _series,
    )

    result = robinhood_main_analytics.opportunity_analytics(
        portfolio_id=PORTFOLIO_ID,
        environment="local",
        lookback="1Y",
        group_keys=["group-a", "group-b"],
    )

    assert captured["series_keys"] == ["AAPL", "GOOG", "MSFT"]
    rankings = result["rankings"].set_index("group")
    assert rankings.loc["Growth", "return"] == pytest.approx(0.05)
    assert rankings.loc["Mega Cap", "return"] == pytest.approx(0.30)
    assert set(result["contributions"]["direction"]) == {"Winners", "Detractors"}


def test_wide_price_frame_preserves_the_time_axis_for_large_catalogs():
    timestamps = pd.date_range("2026-09-28", periods=3, freq="B", tz="UTC")
    points = [
        {
            "series_key": f"S{symbol_index:03d}",
            "observed_at": timestamp,
            "values": {"close": 100.0 + symbol_index + point_index},
        }
        for symbol_index in range(300)
        for point_index, timestamp in enumerate(timestamps)
    ]

    prices = robinhood_main_analytics._price_frame(points)

    assert prices.shape == (3, 300)
    assert prices.index.is_unique
    assert prices.index.min() == timestamps.min()
    assert prices.index.max() == timestamps.max()


def test_equity_analytics_calculates_risk_structure_from_local_history(monkeypatch):
    timestamps = pd.date_range("2026-05-01", periods=90, freq="B", tz="UTC")
    paths = {
        "AAA": [100.0 * (1.002 ** index) * (1.0 + 0.01 * ((index % 7) - 3)) for index in range(90)],
        "BBB": [80.0 * (1.001 ** index) * (1.0 - 0.008 * ((index % 5) - 2)) for index in range(90)],
        "SPY": [500.0 * (1.0008 ** index) * (1.0 + 0.003 * ((index % 6) - 2)) for index in range(90)],
    }
    points = [
        {"series_key": symbol, "observed_at": timestamp, "values": {"close": close}}
        for symbol, closes in paths.items()
        for timestamp, close in zip(timestamps, closes)
    ]
    monkeypatch.setattr(
        robinhood_main_analytics.robinhood_main_store,
        "get_series_points",
        lambda **kwargs: points,
    )

    result = robinhood_main_analytics.equity_position_analytics(
        portfolio_id=PORTFOLIO_ID,
        environment="local",
        holdings=[
            {"asset_class": "equity", "symbol": "AAA", "direction": "long", "market_value": 7000.0},
            {"asset_class": "equity", "symbol": "BBB", "direction": "long", "market_value": 3000.0},
        ],
        lookback="3M",
    )

    assert set(result["positions"]["history_status"]) == {"Ready"}
    assert result["correlation"].shape == (2, 2)
    assert result["components"].shape == (2, 3)
    assert result["stats"]["history_coverage"] == pytest.approx(1.0)
    assert result["stats"]["portfolio_volatility"] > 0
    assert result["positions"]["volatility_contribution"].notna().all()


def test_fresh_catalog_history_only_backfills_new_portfolio_holdings(monkeypatch):
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    requested_batches: list[list[str]] = []
    resources: list[dict[str, object]] = []

    class _Client:
        def quotes(self, symbols):
            requested_batches.append(list(symbols))
            return {symbol: {"last_trade_price": "100", "previous_close": "99"} for symbol in symbols}

        def stock_history(self, symbols, *, span):
            assert span == "5year"
            return [
                {
                    "symbol": symbol,
                    "historicals": [
                        {
                            "begins_at": "2026-10-01T00:00:00Z",
                            "open_price": "99",
                            "high_price": "101",
                            "low_price": "98",
                            "close_price": "100",
                            "volume": "1000",
                        }
                    ],
                }
                for symbol in symbols
            ]

    monkeypatch.setattr(
        robinhood_main.robinhood_main_store,
        "get_resource",
        lambda **kwargs: {
            "status": "ready",
            "payload": {},
            "refresh_after": now + timedelta(days=1),
        },
    )
    monkeypatch.setattr(robinhood_main.robinhood_main_store, "list_resources", lambda **kwargs: [])
    monkeypatch.setattr(
        robinhood_main.robinhood_main_store,
        "get_series_coverage",
        lambda **kwargs: {"AAPL": {"point_count": 100}},
    )
    monkeypatch.setattr(
        robinhood_main.robinhood_main_store,
        "upsert_series_batch",
        lambda **kwargs: len(list(kwargs["points"])),
    )
    monkeypatch.setattr(
        robinhood_main.robinhood_main_store,
        "upsert_resource",
        lambda **kwargs: resources.append(kwargs) or kwargs,
    )

    result = robinhood_main._persist_equity_histories(
        client=_Client(),
        portfolio_id=PORTFOLIO_ID,
        connection_id=CONNECTION_ID,
        environment="local",
        required_symbols=["AAPL", "CRDO"],
        source_updated_at=now,
        collector_errors={},
    )

    assert requested_batches == [["CRDO"]]
    assert result["opportunity_history_points"] == 1
    assert resources[-1]["payload"]["required_symbol_count"] == 2


def test_refresh_appends_series_and_saves_current_snapshot(monkeypatch):
    series_calls: list[dict[str, object]] = []
    saved: dict[str, object] = {}
    monkeypatch.setattr(robinhood_main, "_ensure_data_store", lambda: True)
    monkeypatch.setattr(
        robinhood_main,
        "_connection_for_user",
        lambda _user_id: {"id": CONNECTION_ID, "user_id": _user_id},
    )
    monkeypatch.setattr(robinhood_main, "_client_from_connection", lambda _connection: object())
    monkeypatch.setattr(
        robinhood_main,
        "_normalized_snapshot",
        lambda *args, **kwargs: (
            {"portfolio_value": 3000.0, "equity": 3000.0},
            pd.DataFrame([{"symbol": "AAPL", "market_value": 3000.0}]),
            pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(
                        ["2026-09-28", "2026-09-29", "2026-09-30"], utc=True
                    ),
                    "portfolio": [2900.0, 2950.0, 3000.0],
                }
            ),
            {"holdings": [], "collector_errors": {}},
        ),
    )
    monkeypatch.setattr(robinhood_main.robinhood_main_store, "get_snapshot", lambda **kwargs: None)
    monkeypatch.setattr(robinhood_main.robinhood_main_store, "get_resource", lambda **kwargs: None)
    monkeypatch.setattr(robinhood_main.robinhood_main_store, "list_resources", lambda **kwargs: [])
    monkeypatch.setattr(
        robinhood_main.robinhood_main_store,
        "upsert_series_points",
        lambda **kwargs: series_calls.append(kwargs) or len(list(kwargs["points"])),
    )
    monkeypatch.setattr(robinhood_main.robinhood_main_store, "upsert_resource", lambda **kwargs: {})
    monkeypatch.setattr(
        robinhood_main.robinhood_main_store,
        "save_snapshot",
        lambda **kwargs: saved.update(kwargs) or {"source_updated_at": kwargs["source_updated_at"]},
    )
    monkeypatch.setattr(robinhood_main.brokerage_mcp_store, "set_portfolio_brokerage", lambda **kwargs: {})

    result = robinhood_main.sync_portfolio(user_id="owner", portfolio_id=PORTFOLIO_ID)

    assert result["history_points"] == 4
    assert len(series_calls) == 2
    assert series_calls[0]["series_kind"] == robinhood_main.PORTFOLIO_SERIES_KIND
    assert len(series_calls[0]["points"]) == 3
    assert series_calls[1]["points"][0]["values"]["portfolio"] == 3000.0
    assert saved["positions"]
    assert "timeseries" not in saved


def test_cached_detail_reads_still_enforce_portfolio_ownership(monkeypatch):
    checked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        robinhood_main,
        "_portfolio_read_connection",
        lambda **kwargs: checked.append((kwargs["user_id"], kwargs["portfolio_id"])) or {},
    )
    monkeypatch.setattr(
        robinhood_main.robinhood_main_store,
        "get_series_points",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        robinhood_main.robinhood_main_store,
        "get_resource",
        lambda **kwargs: {
            "payload": {"kind": "screener", "name": "Saved", "members": []}
        },
    )

    robinhood_main.option_leg_history(
        user_id="owner",
        portfolio_id=PORTFOLIO_ID,
        leg_key="a" * 64,
    )
    robinhood_main.opportunity_set_snapshot(
        user_id="owner",
        portfolio_id=PORTFOLIO_ID,
        set_key="b" * 64,
    )

    assert checked == [("owner", PORTFOLIO_ID), ("owner", PORTFOLIO_ID)]


def test_partial_sync_keeps_each_last_good_section_and_marks_it_stale():
    previous_snapshot = {
        "account_summary": {"equity": 5000.0, "portfolio_value": 5000.0},
        "positions": [{"symbol": "AAPL", "market_value": 1800.0}],
        "details": {
            "holdings": [
                {
                    "asset_class": "equity",
                    "symbol": "AAPL",
                    "market_value": 1800.0,
                    "daily_pl": 20.0,
                    "unrealized_pl": 300.0,
                },
                {
                    "asset_class": "option",
                    "symbol": "AAPL",
                    "market_value": -200.0,
                    "daily_pl": 5.0,
                    "unrealized_pl": 100.0,
                },
            ],
            "performance_accounts": [{"account": "margin ••••7145", "return_pct": 8.0}],
            "opportunity_sets": [{"set_key": "saved", "name": "Saved list"}],
            "capabilities": {"equities": True, "options": True, "performance": True, "screeners": True},
        },
    }
    summary, positions, timeseries, details = robinhood_main._preserve_failed_snapshot_sections(
        summary={"equity": 1000.0, "portfolio_value": 1000.0},
        positions=pd.DataFrame(),
        timeseries=pd.DataFrame(
            {"timestamp": pd.to_datetime(["2026-10-01"], utc=True), "portfolio": [1000.0]}
        ),
        details={
            "holdings": [
                {
                    "asset_class": "future",
                    "symbol": "MESZ6",
                    "market_value": 400.0,
                    "daily_pl": -3.0,
                    "unrealized_pl": 12.0,
                }
            ],
            "performance_accounts": [],
            "opportunity_sets": [],
            "capabilities": {},
            "collector_errors": {
                "equities": "temporary failure",
                "options:margin ••••7145": "temporary failure",
                "performance:margin ••••7145": "temporary failure",
                "watchlists": "temporary failure",
                "portfolio:margin ••••7145": "temporary failure",
            },
        },
        previous_snapshot=previous_snapshot,
        include_performance=True,
        include_opportunities=True,
    )

    assert summary["portfolio_value"] == 5000.0
    assert summary["day_pl"] == 22.0
    assert summary["equity_position_count"] == 1
    assert summary["option_position_count"] == 1
    assert summary["futures_position_count"] == 1
    assert positions.to_dict(orient="records") == previous_snapshot["positions"]
    assert timeseries.empty
    assert {item["asset_class"] for item in details["holdings"]} == {
        "equity",
        "option",
        "future",
    }
    assert details["performance_accounts"] == previous_snapshot["details"]["performance_accounts"]
    assert details["opportunity_sets"] == previous_snapshot["details"]["opportunity_sets"]
    assert details["stale_sections"] == [
        "account_summary",
        "equities",
        "opportunities",
        "options",
        "performance",
    ]


def test_long_group_history_keeps_recent_days_and_compacts_older_points_to_weeks():
    source_updated_at = datetime(2026, 10, 2, tzinfo=timezone.utc)
    points = [
        {
            "begins_at": timestamp.isoformat(),
            "open_price": str(index + 99),
            "high_price": str(index + 101),
            "low_price": str(index + 98),
            "close_price": str(index + 100),
            "volume": "1000",
        }
        for index, timestamp in enumerate(
            pd.to_datetime(
                [
                    "2024-01-01T00:00:00Z",
                    "2024-01-02T00:00:00Z",
                    "2024-01-05T00:00:00Z",
                    "2026-09-29T00:00:00Z",
                    "2026-09-30T00:00:00Z",
                ],
                utc=True,
            )
        )
    ]

    compacted = robinhood_main._compact_equity_history(
        symbol="AAPL",
        points=points,
        source_updated_at=source_updated_at,
    )

    assert len(compacted) == 3
    assert all(point["series_key"] == "AAPL" for point in compacted)
    assert compacted[0]["observed_at"].isoformat().startswith("2024-01-05")
