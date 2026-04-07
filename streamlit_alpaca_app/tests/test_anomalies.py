from __future__ import annotations

import json

import pandas as pd
import pytest

from compute.anomalies import (
    AttentionConfig,
    ExpectationConfig,
    build_attention_candidates,
    build_attention_feed,
    build_attention_rollups,
    build_commodity_peer_group_membership,
    build_price_expectations,
    detect_anomaly_events,
    filter_attention_events,
)


def _price_frame(symbol: str, closes: list[float], dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [symbol] * len(dates),
            "timestamp": dates,
            "close": closes,
        }
    )


def test_build_price_expectations_blends_trend_peer_and_benchmark_components():
    dates = pd.bdate_range("2026-02-16", periods=22, tz="UTC")
    price_history = pd.concat(
        [
            _price_frame("AAA", [100.0] * 17 + [101.0, 102.0, 104.0, 106.0, 107.0], dates),
            _price_frame("BBB", [100.0] * 17 + [100.2, 100.4, 100.6, 100.8, 101.0], dates),
            _price_frame("SPY", [100.0] * 17 + [100.2, 100.4, 100.6, 100.8, 101.0], dates),
        ],
        ignore_index=True,
    )
    momentum_profiles = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "return_1w_pct": [5.0, 1.0],
            "return_1m_pct": [10.0, 2.0],
            "return_3m_pct": [15.0, 3.0],
            "momentum_score": [1.2, 0.2],
            "momentum_roc_score": [0.4, 0.1],
        }
    )
    phase_summary = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "benchmark": ["SPY", "SPY"],
            "correlation_now": [1.0, 1.0],
            "correlation_roc": [-0.05, 0.01],
        }
    )
    peer_group_membership = pd.DataFrame(
        {
            "entity_id": ["AAA", "BBB"],
            "peer_group_id": ["business_lens:test", "business_lens:test"],
            "peer_group_name": ["Test Lens", "Test Lens"],
            "benchmark": ["SPY", "SPY"],
        }
    )

    out = build_price_expectations(
        price_history,
        momentum_profiles,
        phase_summary,
        peer_group_membership,
        config=ExpectationConfig(),
    )

    aaa_1w = out[(out["symbol"] == "AAA") & (out["horizon"] == "1w")].iloc[0]
    bbb_1w = out[(out["symbol"] == "BBB") & (out["horizon"] == "1w")].iloc[0]

    assert aaa_1w["observed_return_pct"] == pytest.approx(7.0)
    assert aaa_1w["trend_expected_return_pct"] == pytest.approx(3.642857, abs=1e-6)
    assert aaa_1w["peer_expected_return_pct"] == pytest.approx(1.0)
    assert aaa_1w["benchmark_expected_return_pct"] == pytest.approx(1.0)
    assert aaa_1w["blended_expected_return_pct"] == pytest.approx(2.057143, abs=1e-6)
    assert aaa_1w["residual_return_pct"] > bbb_1w["residual_return_pct"]
    assert aaa_1w["residual_zscore"] > 0
    assert bbb_1w["residual_zscore"] < 0


def test_build_commodity_peer_group_membership_assigns_focus_groups_and_benchmarks(monkeypatch):
    taxonomy = pd.DataFrame(
        [
            {
                "symbol": "DBC",
                "commodity_role": "",
                "rates_role": "",
                "defensive_role": "",
                "macro_role_tags": [],
            },
            {
                "symbol": "GLD",
                "commodity_role": "gold",
                "rates_role": "",
                "defensive_role": "",
                "macro_role_tags": [],
            },
            {
                "symbol": "CPER",
                "commodity_role": "copper",
                "rates_role": "",
                "defensive_role": "",
                "macro_role_tags": ["industrial_metals"],
            },
        ]
    )
    monkeypatch.setattr("compute.anomalies.load_entity_taxonomy_frame", lambda symbols=None: taxonomy)

    out = build_commodity_peer_group_membership(
        asof_time_utc=pd.Timestamp("2026-03-20T00:00:00Z"),
        symbols=["DBC", "GLD", "CPER"],
    )

    assert not out.empty
    assert set(out["entity_type"]) == {"commodity_symbol"}
    assert set(out["peer_group_type"]) == {"commodity_focus", "commodity_role"}

    dbc_row = out[out["entity_id"] == "DBC"].iloc[0]
    assert dbc_row["peer_group_name"] == "Broad Commodity Market"
    assert dbc_row["benchmark"] == "PDBC"

    gld_row = out[out["entity_id"] == "GLD"].iloc[0]
    assert gld_row["peer_group_name"] != "Broad Commodity Market"
    assert gld_row["benchmark"] == "DBC"


def test_detect_anomaly_events_boosts_portfolio_relevance_and_news_confirmation():
    price_expectations = pd.DataFrame(
        {
            "asof_time_utc": pd.to_datetime(["2026-03-20T00:00:00Z", "2026-03-20T00:00:00Z"], utc=True),
            "symbol": ["AAA", "BBB"],
            "horizon": ["1w", "1w"],
            "close": [120.0, 80.0],
            "observed_return_pct": [8.0, 8.0],
            "trend_expected_return_pct": [3.0, 3.0],
            "peer_expected_return_pct": [2.0, 2.0],
            "benchmark_expected_return_pct": [1.0, 1.0],
            "blended_expected_return_pct": [2.0, 2.0],
            "residual_return_pct": [6.0, 6.0],
            "residual_zscore": [3.0, 3.0],
            "trend_zscore": [1.0, 1.0],
            "peer_zscore": [0.0, 0.0],
            "benchmark_zscore": [0.0, 0.0],
            "vol_20_ann_pct": [25.0, 25.0],
            "momentum_score": [1.1, 1.1],
            "momentum_roc_score": [0.0, 0.0],
            "correlation_now": [0.8, 0.8],
            "correlation_roc": [0.0, 0.0],
            "peer_group_id": ["business_lens:test", "business_lens:test"],
            "peer_group_name": ["Test Lens", "Test Lens"],
            "benchmark": ["SPY", "SPY"],
            "trajectory_model_version": ["trend_blend_v1", "trend_blend_v1"],
            "peer_model_version": ["business_lens_peer_v1", "business_lens_peer_v1"],
            "schema_version": ["v1", "v1"],
        }
    )
    technical_signals_latest = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "regime": ["Trend breakout", "Range / consolidation"],
        }
    )
    news_symbol_map = pd.DataFrame(
        {
            "headline": ["AAA wins new contract"],
            "published_at": pd.to_datetime(["2026-03-19T14:00:00Z"], utc=True),
            "source": ["Newswire"],
            "url": ["https://example.com/aaa"],
            "symbols": ["AAA"],
        }
    )
    positions = pd.DataFrame(
        {
            "symbol": ["AAA", "ZZZ"],
            "market_value": [800.0, 200.0],
        }
    )

    events = detect_anomaly_events(
        price_expectations,
        technical_signals_latest=technical_signals_latest,
        news_symbol_map=news_symbol_map,
        positions=positions,
        config=AttentionConfig(),
    )

    assert events["entity_id"].tolist() == ["AAA", "BBB"]
    assert events.iloc[0]["attention_score"] > events.iloc[1]["attention_score"]
    assert events.iloc[0]["portfolio_exposure_weight"] == pytest.approx(0.8)
    assert events.iloc[0]["linked_news_count"] == 1
    assert "news_symbol_map" in events.iloc[0]["supporting_datasets"]
    assert events.iloc[0]["why_now_text"].startswith("AAA moved 8.00%")


def test_detect_anomaly_events_adds_business_lens_drilldown_context():
    price_expectations = pd.DataFrame(
        {
            "asof_time_utc": pd.to_datetime(["2026-03-20T00:00:00Z"], utc=True),
            "symbol": ["BX"],
            "horizon": ["1d"],
            "close": [150.0],
            "observed_return_pct": [4.0],
            "trend_expected_return_pct": [1.0],
            "peer_expected_return_pct": [1.2],
            "benchmark_expected_return_pct": [0.5],
            "blended_expected_return_pct": [1.0],
            "residual_return_pct": [3.0],
            "residual_zscore": [2.5],
            "trend_zscore": [0.0],
            "peer_zscore": [0.0],
            "benchmark_zscore": [0.0],
            "vol_20_ann_pct": [22.0],
            "momentum_score": [0.8],
            "momentum_roc_score": [0.2],
            "correlation_now": [0.7],
            "correlation_roc": [0.1],
            "peer_group_id": ["business_lens:alternative_asset_managers"],
            "peer_group_name": ["Alternative Asset Managers"],
            "benchmark": ["SPY"],
            "trajectory_model_version": ["trend_blend_v1"],
            "peer_model_version": ["business_lens_peer_v1"],
            "schema_version": ["v1"],
        }
    )

    events = detect_anomaly_events(price_expectations, config=AttentionConfig())

    params = json.loads(events.iloc[0]["drilldown_params_json"])
    assert params["ticker"] == "BX"
    assert params["horizon"] == "1d"
    assert params["market_view"] == "Markets"
    assert params["business_filter"] == "Alternative Asset Managers"


def test_build_price_expectations_degrades_cleanly_when_benchmark_rows_are_missing():
    dates = pd.bdate_range("2026-02-16", periods=22, tz="UTC")
    price_history = pd.concat(
        [
            _price_frame("AAA", [100.0] * 17 + [101.0, 102.0, 104.0, 106.0, 107.0], dates),
            _price_frame("BBB", [100.0] * 17 + [100.2, 100.4, 100.6, 100.8, 101.0], dates),
        ],
        ignore_index=True,
    )
    momentum_profiles = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "return_1w_pct": [5.0, 1.0],
            "return_1m_pct": [10.0, 2.0],
            "return_3m_pct": [15.0, 3.0],
            "momentum_score": [1.2, 0.2],
            "momentum_roc_score": [0.4, 0.1],
        }
    )
    phase_summary = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "benchmark": ["SPY", "SPY"],
            "correlation_now": [1.0, 1.0],
            "correlation_roc": [-0.05, 0.01],
        }
    )
    peer_group_membership = pd.DataFrame(
        {
            "entity_id": ["AAA", "BBB"],
            "peer_group_id": ["business_lens:test", "business_lens:test"],
            "peer_group_name": ["Test Lens", "Test Lens"],
            "benchmark": ["SPY", "SPY"],
        }
    )

    out = build_price_expectations(
        price_history,
        momentum_profiles,
        phase_summary,
        peer_group_membership,
        config=ExpectationConfig(),
    )

    assert not out.empty
    assert out["benchmark_expected_return_pct"].isna().all()


def test_build_attention_rollups_and_feed_surface_top_anomalies():
    anomaly_events = pd.DataFrame(
        {
            "event_id": ["evt_aaa", "evt_bbb"],
            "asof_time_utc": pd.to_datetime(["2026-03-20T00:00:00Z", "2026-03-20T00:00:00Z"], utc=True),
            "entity_type": ["symbol", "symbol"],
            "entity_id": ["AAA", "BBB"],
            "parent_entity_type": ["peer_group", "peer_group"],
            "parent_entity_id": ["business_lens:test", "business_lens:test"],
            "horizon": ["1w", "1w"],
            "anomaly_type": ["price_residual", "price_residual"],
            "direction": ["up", "down"],
            "observed_value": [8.0, -5.0],
            "expected_value": [2.0, -1.0],
            "residual_value": [6.0, -4.0],
            "residual_zscore": [3.5, 2.2],
            "severity_score": [87.5, 55.0],
            "impact_score": [70.0, 45.0],
            "relevance_score": [100.0, 40.0],
            "confidence_score": [85.0, 55.0],
            "attention_score": [84.0, 50.0],
            "persistence_score": [75.0, 60.0],
            "novelty_score": [87.5, 55.0],
            "portfolio_exposure_weight": [0.8, 0.0],
            "peer_group_id": ["business_lens:test", "business_lens:test"],
            "peer_group_name": ["Test Lens", "Test Lens"],
            "benchmark": ["SPY", "SPY"],
            "regime_label": ["Trend breakout", ""],
            "why_now_code": ["price_residual", "price_residual"],
            "why_now_text": ["AAA moved well above expectation.", "BBB moved below expectation."],
            "supporting_datasets": ["price_expectations,positions", "price_expectations"],
            "linked_news_count": [1, 0],
            "linked_news_ids": ["n1", ""],
            "drilldown_section": ["Market Opportunity", "Market Opportunity"],
            "drilldown_params_json": ['{"ticker":"AAA"}', '{"ticker":"BBB"}'],
            "status": ["active", "cooling"],
            "schema_version": ["v1", "v1"],
        }
    )

    rollups = build_attention_rollups(anomaly_events, peer_group_membership=pd.DataFrame())
    feed = build_attention_feed(anomaly_events, rollups, top_n=5)

    assert set(rollups["rollup_type"]) == {"market", "portfolio", "business_lens"}
    assert feed.iloc[0]["entity_id"] == "AAA"
    assert feed.iloc[0]["title"].startswith("Portfolio attention")
    assert feed.iloc[0]["horizon"] == "1w"
    assert feed.iloc[0]["residual_value"] == pytest.approx(6.0)
    assert feed.iloc[0]["residual_zscore"] == pytest.approx(3.5)
    assert "chart shows how the realized move separated from the model baseline over 1w" in feed.iloc[0]["expected_vs_observed_text"]
    assert "AAA is trading stronger than its Test Lens peers implied." in feed.iloc[0]["story_text"]
    assert "Price action still looks like a trend breakout setup" in feed.iloc[0]["story_text"]
    assert "Residual over" not in feed.iloc[0]["story_text"]
    assert "AAA" in feed.iloc[0]["next_best_action"]
    assert json.loads(feed.iloc[0]["drilldown_params_json"]) == {
        "horizon": "1w",
        "market_view": "Markets",
        "ticker": "AAA",
        "business_filter": "Test Lens",
    }


def test_build_attention_feed_preserves_commodity_drilldown_context():
    anomaly_events = pd.DataFrame(
        {
            "event_id": ["evt_bno"],
            "asof_time_utc": pd.to_datetime(["2026-03-20T00:00:00Z"], utc=True),
            "entity_type": ["commodity_symbol"],
            "entity_id": ["BNO"],
            "parent_entity_type": ["commodity_focus"],
            "parent_entity_id": ["commodity_focus:energy_and_oil"],
            "horizon": ["1mo"],
            "anomaly_type": ["price_residual"],
            "direction": ["up"],
            "observed_value": [9.0],
            "expected_value": [3.0],
            "residual_value": [6.0],
            "residual_zscore": [2.8],
            "severity_score": [70.0],
            "impact_score": [65.0],
            "relevance_score": [70.0],
            "confidence_score": [72.0],
            "attention_score": [69.0],
            "persistence_score": [80.0],
            "novelty_score": [70.0],
            "portfolio_exposure_weight": [0.0],
            "peer_group_id": ["commodity_focus:energy_and_oil"],
            "peer_group_name": ["Energy & Oil"],
            "benchmark": ["PDBC"],
            "regime_label": ["Trend breakout"],
            "why_now_code": ["price_residual"],
            "why_now_text": ["BNO moved above expectation."],
            "supporting_datasets": ["commodity_price_expectations"],
            "linked_news_count": [0],
            "linked_news_ids": [""],
            "drilldown_section": ["Market Opportunity"],
            "drilldown_params_json": ['{"ticker":"BNO"}'],
            "status": ["active"],
            "schema_version": ["v1"],
        }
    )

    feed = build_attention_feed(anomaly_events, pd.DataFrame(), top_n=5)

    assert "Energy & Oil peers" in feed.iloc[0]["story_text"]
    assert json.loads(feed.iloc[0]["drilldown_params_json"]) == {
        "commodity_focus": "Energy & Oil",
        "horizon": "1mo",
        "market_view": "Commodity Section",
        "ticker": "BNO",
    }


def test_build_price_expectations_supports_longer_attention_horizons():
    dates = pd.bdate_range("2025-03-10", periods=260, tz="UTC")
    price_history = pd.concat(
        [
            _price_frame("AAA", list(100.0 + (pd.Series(range(260)) * 0.2)), dates),
            _price_frame("BBB", list(100.0 + (pd.Series(range(260)) * 0.05)), dates),
            _price_frame("SPY", list(100.0 + (pd.Series(range(260)) * 0.08)), dates),
        ],
        ignore_index=True,
    )
    momentum_profiles = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "return_1w_pct": [2.0, 0.5],
            "return_1m_pct": [8.0, 2.0],
            "return_3m_pct": [16.0, 4.0],
            "return_1y_pct": [40.0, 10.0],
            "momentum_score": [1.0, 0.2],
            "momentum_roc_score": [0.2, 0.05],
        }
    )
    phase_summary = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "benchmark": ["SPY", "SPY"],
            "correlation_now": [0.8, 0.8],
            "correlation_roc": [0.0, 0.0],
        }
    )
    peer_group_membership = pd.DataFrame(
        {
            "entity_id": ["AAA", "BBB"],
            "peer_group_id": ["business_lens:test", "business_lens:test"],
            "peer_group_name": ["Test Lens", "Test Lens"],
            "benchmark": ["SPY", "SPY"],
        }
    )

    out = build_price_expectations(
        price_history,
        momentum_profiles,
        phase_summary,
        peer_group_membership,
        config=ExpectationConfig(horizons=("3mo", "1yr"), min_history_rows=30),
    )

    assert {"3mo", "1yr"} == set(out["horizon"])
    assert set(out["symbol"]) == {"AAA", "BBB"}


def test_filter_attention_events_supports_tunable_horizons_and_thresholds():
    candidates = pd.DataFrame(
        {
            "event_id": ["evt_1", "evt_2", "evt_3"],
            "asof_time_utc": pd.to_datetime(["2026-03-20T00:00:00Z"] * 3, utc=True),
            "entity_type": ["symbol", "symbol", "symbol"],
            "entity_id": ["AAA", "AAA", "BBB"],
            "parent_entity_type": ["peer_group"] * 3,
            "parent_entity_id": ["group"] * 3,
            "horizon": ["1d", "1yr", "3mo"],
            "anomaly_type": ["price_residual"] * 3,
            "direction": ["up", "up", "down"],
            "observed_value": [2.0, 12.0, -6.0],
            "expected_value": [0.5, 5.0, -2.0],
            "residual_value": [1.5, 7.0, -4.0],
            "residual_zscore": [1.4, 2.6, 1.9],
            "severity_score": [35.0, 65.0, 47.5],
            "impact_score": [30.0, 70.0, 50.0],
            "relevance_score": [40.0, 70.0, 70.0],
            "confidence_score": [40.0, 75.0, 55.0],
            "attention_score": [28.0, 68.0, 44.0],
            "persistence_score": [35.0, 90.0, 70.0],
            "novelty_score": [35.0, 65.0, 47.5],
            "portfolio_exposure_weight": [0.0, 0.0, 0.0],
            "peer_group_id": ["group"] * 3,
            "peer_group_name": ["Test Lens"] * 3,
            "benchmark": ["SPY"] * 3,
            "regime_label": ["", "", ""],
            "why_now_code": ["price_residual"] * 3,
            "why_now_text": ["", "", ""],
            "supporting_datasets": ["price_expectations"] * 3,
            "linked_news_count": [0, 0, 0],
            "linked_news_ids": ["", "", ""],
            "drilldown_section": ["Market Opportunity"] * 3,
            "drilldown_params_json": ['{"ticker":"AAA"}', '{"ticker":"AAA"}', '{"ticker":"BBB"}'],
            "status": ["cooling", "active", "cooling"],
            "schema_version": ["v1", "v1", "v1"],
        }
    )

    filtered = filter_attention_events(
        candidates,
        config=AttentionConfig(),
        horizons=["1yr", "3mo"],
        residual_zscore_threshold=2.0,
        min_attention_score=45.0,
        statuses=["active", "cooling"],
    )

    assert filtered["horizon"].tolist() == ["1yr"]
    assert filtered["entity_id"].tolist() == ["AAA"]


def test_build_attention_candidates_scores_all_rows_before_thresholding():
    price_expectations = pd.DataFrame(
        {
            "asof_time_utc": pd.to_datetime(["2026-03-20T00:00:00Z", "2026-03-20T00:00:00Z"], utc=True),
            "symbol": ["AAA", "BBB"],
            "horizon": ["1d", "1yr"],
            "close": [100.0, 120.0],
            "observed_return_pct": [1.5, 12.0],
            "trend_expected_return_pct": [0.2, 5.0],
            "peer_expected_return_pct": [0.5, 4.5],
            "benchmark_expected_return_pct": [0.3, 4.0],
            "blended_expected_return_pct": [0.4, 4.5],
            "residual_return_pct": [1.1, 7.5],
            "residual_zscore": [1.1, 2.7],
            "trend_zscore": [0.0, 0.0],
            "peer_zscore": [0.0, 0.0],
            "benchmark_zscore": [0.0, 0.0],
            "vol_20_ann_pct": [20.0, 25.0],
            "momentum_score": [0.2, 0.8],
            "momentum_roc_score": [0.0, 0.0],
            "correlation_now": [0.6, 0.6],
            "correlation_roc": [0.0, 0.0],
            "peer_group_id": ["business_lens:test", "business_lens:test"],
            "peer_group_name": ["Test Lens", "Test Lens"],
            "benchmark": ["SPY", "SPY"],
            "trajectory_model_version": ["trend_blend_v1", "trend_blend_v1"],
            "peer_model_version": ["business_lens_peer_v1", "business_lens_peer_v1"],
            "schema_version": ["v1", "v1"],
        }
    )

    candidates = build_attention_candidates(price_expectations, config=AttentionConfig())
    anomalies = detect_anomaly_events(price_expectations, config=AttentionConfig())

    assert candidates["entity_id"].tolist() == ["BBB", "AAA"]
    assert anomalies["entity_id"].tolist() == ["BBB"]


def test_build_attention_candidates_applies_macro_context_shadow_and_live_switch():
    price_expectations = pd.DataFrame(
        {
            "asof_time_utc": pd.to_datetime(["2026-03-20T00:00:00Z"], utc=True),
            "symbol": ["AAA"],
            "horizon": ["1d"],
            "close": [100.0],
            "observed_return_pct": [2.5],
            "trend_expected_return_pct": [0.5],
            "peer_expected_return_pct": [0.6],
            "benchmark_expected_return_pct": [0.4],
            "blended_expected_return_pct": [0.5],
            "residual_return_pct": [2.0],
            "residual_zscore": [2.2],
            "trend_zscore": [0.0],
            "peer_zscore": [0.0],
            "benchmark_zscore": [0.0],
            "vol_20_ann_pct": [20.0],
            "momentum_score": [0.4],
            "momentum_roc_score": [0.0],
            "correlation_now": [0.7],
            "correlation_roc": [0.0],
            "peer_group_id": ["business_lens:test"],
            "peer_group_name": ["Test Lens"],
            "benchmark": ["SPY"],
            "trajectory_model_version": ["trend_blend_v1"],
            "peer_model_version": ["business_lens_peer_v1"],
            "schema_version": ["v1"],
        }
    )
    macro_context = pd.DataFrame(
        {
            "symbol": ["AAA"],
            "horizon": ["1d"],
            "macro_alignment_score": [80.0],
            "macro_conflict_score": [10.0],
            "macro_signal_count": [3],
            "macro_staleness_hours": [3.0],
        }
    )

    shadow = build_attention_candidates(
        price_expectations,
        macro_context=macro_context,
        config=AttentionConfig(macro_live_enabled=False, macro_shadow_enabled=True, macro_shadow_weight=0.2),
    ).iloc[0]
    assert bool(shadow["macro_data_fresh"]) is True
    assert shadow["attention_score_v2_shadow"] > shadow["attention_score"]
    assert shadow["attention_score_v2"] == pytest.approx(shadow["attention_score"])

    live = build_attention_candidates(
        price_expectations,
        macro_context=macro_context,
        config=AttentionConfig(macro_live_enabled=True, macro_shadow_enabled=True, macro_shadow_weight=0.2),
    ).iloc[0]
    assert live["attention_score"] == pytest.approx(live["attention_score_v2"])
    assert live["attention_score"] > shadow["attention_score"]
