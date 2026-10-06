from __future__ import annotations

import json

import pandas as pd

from services.all_signal_ranker import RANKING_METHOD, rank_trading_agent_opportunities
from services.trading_agent import build_trading_agent_context


def _feed(symbols: list[str], *, horizon_key: str = "1m", horizon_col: str = "return_1m_pct") -> pd.DataFrame:
    size = len(symbols)
    return pd.DataFrame(
        {
            "business_filter": ["All Market"] * size,
            "horizon_key": [horizon_key] * size,
            "selected_horizon_col": [horizon_col] * size,
            "selected_horizon_label": ["1 Month"] * size,
            "symbol": symbols,
            "rank": range(1, size + 1),
            "opportunity_score": list(range(90, 90 - size, -1)),
            "daily_change_pct": [float(value) for value in range(size, 0, -1)],
            "return_1w_pct": [float(value) for value in range(size, 0, -1)],
            "return_1m_pct": [float(value) for value in range(size, 0, -1)],
            "return_3m_pct": [float(value) for value in range(size, 0, -1)],
            "return_1y_pct": [float(value) for value in range(size, 0, -1)],
            "return_5y_pct": [float(value) for value in range(size, 0, -1)],
            "momentum_roc_score": [float(value) / 10.0 for value in range(size, 0, -1)],
            "trend_fit_gap": [float(value) / 10.0 for value in range(1, size + 1)],
            "horizon_metric_available": [True] * size,
            "retrieval_method": ["reciprocal_rank_fusion_v1"] * size,
            "horizon_retrieval_rank": range(1, size + 1),
        }
    )


def test_all_signal_ranker_uses_multiple_families_and_preserves_scanner_trace() -> None:
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    feed = _feed(symbols)
    frames = {
        "technical_signals_latest": pd.DataFrame(
            {
                "symbol": symbols,
                "ret_21_pct": [1.0, 25.0, 2.0, 3.0, 4.0, 5.0],
                "rsi_14": [50.0, 99.0, 51.0, 52.0, 53.0, 54.0],
            }
        ),
        "price_expectations": pd.DataFrame(
            {
                "symbol": symbols,
                "horizon": ["1mo"] * len(symbols),
                "residual_zscore": [0.0, 0.0, 8.0, 0.0, 0.0, 0.0],
                "trend_zscore": [0.0, 0.0, 7.0, 0.0, 0.0, 0.0],
            }
        ),
    }

    ranked = rank_trading_agent_opportunities(feed, frames)

    assert ranked["retrieval_method"].eq(RANKING_METHOD).all()
    assert ranked["all_signal_rank"].tolist() == list(range(1, len(symbols) + 1))
    assert ranked["scanner_opportunity_score"].notna().all()
    assert ranked["all_signal_family_count"].min() >= 4
    assert ranked["all_signal_signal_count"].min() >= ranked["all_signal_family_count"].min()
    assert set(json.loads(ranked.iloc[0]["all_signal_family_scores_json"])) >= {
        "momentum",
        "relative_expectation",
        "technical",
        "trend_quality",
    }
    for row in ranked.itertuples():
        families = json.loads(row.all_signal_family_scores_json)
        domains = json.loads(row.all_signal_domain_scores_json)
        price_families = [
            families[name]
            for name in ("price_liquidity", "momentum", "trend_quality", "technical")
            if name in families
        ]
        assert abs(domains["price_behavior"] - sum(price_families) / len(price_families)) <= 0.1


def test_all_signal_ranker_matches_horizon_specific_rows() -> None:
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    frames = {
        "price_expectations": pd.DataFrame(
            {
                "symbol": symbols * 2,
                "horizon": ["1w"] * len(symbols) + ["1mo"] * len(symbols),
                "residual_zscore": [20.0, 0.0, 0.0, 0.0, 0.0, 0.0] + [0.0, 20.0, 0.0, 0.0, 0.0, 0.0],
            }
        )
    }
    one_week = rank_trading_agent_opportunities(
        _feed(symbols, horizon_key="1w", horizon_col="return_7d_pct").assign(return_7d_pct=range(6, 0, -1)),
        frames,
    )
    one_month = rank_trading_agent_opportunities(_feed(symbols), frames)

    week_scores = one_week.set_index("symbol")["all_signal_family_scores_json"].map(json.loads)
    month_scores = one_month.set_index("symbol")["all_signal_family_scores_json"].map(json.loads)
    assert week_scores["AAA"]["relative_expectation"] > week_scores["BBB"]["relative_expectation"]
    assert month_scores["BBB"]["relative_expectation"] > month_scores["AAA"]["relative_expectation"]


def test_sparse_family_missing_rows_receive_neutral_rank() -> None:
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    feed = _feed(symbols)
    for column in (
        "daily_change_pct",
        "return_1w_pct",
        "return_1m_pct",
        "return_3m_pct",
        "return_1y_pct",
        "return_5y_pct",
        "momentum_roc_score",
        "trend_fit_gap",
    ):
        feed[column] = 1.0
    frames = {
        "price_expectations": pd.DataFrame(
            {
                "symbol": ["AAA", "BBB", "CCC"],
                "horizon": ["1mo", "1mo", "1mo"],
                "residual_zscore": [10.0, 1.0, 5.0],
            }
        )
    }

    ranked = rank_trading_agent_opportunities(feed, frames).set_index("symbol")

    missing_score = ranked.loc["DDD", "all_signal_score"]
    assert ranked.loc["AAA", "all_signal_score"] > missing_score
    assert missing_score > ranked.loc["CCC", "all_signal_score"]


def test_fundamental_history_becomes_lag_safe_growth_features() -> None:
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    rows: list[dict[str, object]] = []
    dates = pd.date_range("2024-01-01", periods=5, freq="QE")
    for index, symbol in enumerate(symbols, start=1):
        values = [100.0, 102.0, 104.0, 106.0, 108.0 + index * 10.0]
        rows.extend(
            {"ticker": symbol, "metric": "Revenue", "value": value, "report_date": date}
            for date, value in zip(dates, values)
        )

    ranked = rank_trading_agent_opportunities(
        _feed(symbols),
        {"quarterly_fundamentals": pd.DataFrame(rows)},
    )

    families = ranked.set_index("symbol")["all_signal_family_scores_json"].map(json.loads)
    assert all("fundamentals" in value for value in families)
    assert ranked["all_signal_datasets_json"].map(json.loads).map(set).map(
        lambda value: "quarterly_fundamentals" in value
    ).all()


def test_trading_agent_context_uses_all_signal_rank_for_retrieval_depth() -> None:
    symbols = [f"S{index:02d}" for index in range(30)]
    feed = _feed(symbols)
    feed["all_signal_rank"] = list(range(30, 0, -1))
    feed["all_signal_score"] = list(range(1, 31))
    feed["opportunity_score"] = feed["all_signal_score"]
    feed["retrieval_method"] = RANKING_METHOD
    feed["rank"] = feed["all_signal_rank"]

    context, selected = build_trading_agent_context(
        opportunity_feed=feed,
        page_summaries=pd.DataFrame(),
        horizon_key="1m",
        horizon_col="return_1m_pct",
        horizon_label="1 Month",
        max_candidates=4,
        research_candidate_limit=6,
    )

    assert context["controls"]["candidate_retrieval_method"] == "all_signal_rank"
    assert selected["all_signal_rank"].max() <= 24
    assert context["market_opportunity_feed"][0]["symbol"] == "S29"
