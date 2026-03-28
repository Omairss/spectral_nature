from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from pipeline.jobs.main import (
    JobContext,
    _build_quarterly_fundamentals_snapshot,
    _build_treasury_yield_snapshots,
    _build_equity_price_history_snapshot,
    _resolve_equity_symbols,
    _upload_frame,
    run_news,
)
from services.pipeline_store import SOURCE_DATASETS


def test_upload_frame_persists_empty_frames(monkeypatch):
    uploaded: list[tuple[str, int, str]] = []

    def _capture(path: str, payload: bytes, content_type: str) -> None:
        uploaded.append((path, len(payload), content_type))

    monkeypatch.setattr("pipeline.jobs.main._upload_bytes", _capture)

    ctx = JobContext(
        name="equities-intraday-preload",
        run_id="12345678-test-run",
        asof=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
        universe_version="20260320",
    )

    path = _upload_frame("attention_feed", pd.DataFrame(columns=["event_id", "attention_score"]), ctx)

    assert path.endswith("/part-12345678.parquet")
    assert uploaded
    assert uploaded[0][0] == path
    assert uploaded[0][1] > 0
    assert uploaded[0][2] == "application/octet-stream"


def test_pipeline_store_lists_attention_datasets_under_derivatives():
    derivative_datasets = set(SOURCE_DATASETS["derivatives"])

    assert {
        "peer_group_membership",
        "price_expectations",
        "attention_candidates",
        "anomaly_events",
        "attention_rollups",
        "attention_feed",
    }.issubset(derivative_datasets)


def test_pipeline_store_lists_universe_snapshot_under_equities():
    equity_datasets = set(SOURCE_DATASETS["equities"])

    assert "universe_snapshot" in equity_datasets


def test_pipeline_store_lists_yield_datasets_under_fred():
    fred_datasets = set(SOURCE_DATASETS["fred"])

    assert {"yield_curve_observations", "yield_curve_summary", "yield_curve_facts_1d"}.issubset(fred_datasets)


def test_pipeline_store_lists_attention_datasets_under_commodities():
    commodity_datasets = set(SOURCE_DATASETS["commodities"])

    assert {
        "commodity_regime_summary",
        "commodity_regime_history",
        "commodity_peer_group_membership",
        "commodity_price_expectations",
        "commodity_attention_candidates",
        "commodity_anomaly_events",
        "commodity_attention_rollups",
        "commodity_attention_feed",
    }.issubset(commodity_datasets)


def test_pipeline_store_lists_attention_context_datasets_under_news():
    news_datasets = set(SOURCE_DATASETS["news"])

    assert {
        "news_articles",
        "news_symbol_map",
        "edgar_filings",
        "edgar_evidence",
        "attention_context_llm",
        "attention_context_bundle",
        "attention_ticker_snapshots_1d",
        "attention_ticker_background_snapshots",
    }.issubset(news_datasets)


def test_resolve_equity_symbols_prefers_large_snapshot(monkeypatch):
    ctx = JobContext(
        name="equities-intraday-preload",
        run_id="12345678-test-run",
        asof=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
        universe_version="20260320",
    )
    snapshot = pd.DataFrame({"symbol": [f"S{idx:04d}" for idx in range(600)], "rank": list(range(1, 601))})
    monkeypatch.delenv("UNIVERSE_SYMBOLS", raising=False)
    monkeypatch.setenv("EQUITY_UNIVERSE_TARGET_SIZE", "500")
    monkeypatch.setattr("pipeline.jobs.main._load_latest_equity_universe_snapshot", lambda target_size: snapshot)

    symbols = _resolve_equity_symbols(object(), ctx, None)

    assert len(symbols) == 500
    assert symbols[:3] == ["S0000", "S0001", "S0002"]


def test_resolve_equity_symbols_rebuilds_and_persists_when_snapshot_missing(monkeypatch):
    ctx = JobContext(
        name="equities-intraday-preload",
        run_id="12345678-test-run",
        asof=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
        universe_version="20260320",
    )
    persisted: list[tuple[str, int]] = []

    monkeypatch.delenv("UNIVERSE_SYMBOLS", raising=False)
    monkeypatch.setenv("EQUITY_UNIVERSE_TARGET_SIZE", "4")
    monkeypatch.setattr("pipeline.jobs.main._load_latest_equity_universe_snapshot", lambda target_size: pd.DataFrame())
    monkeypatch.setattr(
        "pipeline.jobs.main._build_equity_universe_snapshot",
        lambda api, *, target_size: pd.DataFrame({"symbol": ["AAA", "BBB", "CCC", "DDD"], "rank": [1, 2, 3, 4]}),
    )
    monkeypatch.setattr(
        "pipeline.jobs.main._persist_dataset",
        lambda dataset_name, frame, ctx, conn: persisted.append((dataset_name, len(frame))),
    )

    symbols = _resolve_equity_symbols(object(), ctx, None)

    assert symbols == ["AAA", "BBB", "CCC", "DDD"]
    assert persisted == [("universe_snapshot", 4)]


def test_build_equity_price_history_snapshot_fetches_incremental_updates_and_full_history_for_missing_symbols(monkeypatch):
    now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)
    existing = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "SPY", "SPY"],
            "timestamp": [
                pd.Timestamp("2025-11-22T00:00:00Z"),
                pd.Timestamp("2026-03-18T00:00:00Z"),
                pd.Timestamp("2025-11-21T00:00:00Z"),
                pd.Timestamp("2026-03-18T00:00:00Z"),
            ],
            "open": [10.0, 11.0, 20.0, 21.0],
            "high": [10.5, 11.5, 20.5, 21.5],
            "low": [9.5, 10.5, 19.5, 20.5],
            "close": [10.2, 11.2, 20.2, 21.2],
            "volume": [100, 110, 200, 210],
        }
    )

    class FakeBarsAPI:
        def __init__(self):
            self.calls: list[dict[str, object]] = []

        def get_stock_bars(self, symbols, start=None, end=None, timeframe="1Day", feed="iex"):
            normalized = list(symbols)
            self.calls.append({"symbols": normalized, "start": start, "end": end, "timeframe": timeframe, "feed": feed})
            frames: dict[str, pd.DataFrame] = {}
            for symbol in normalized:
                if symbol == "BBB" and start <= datetime(2025, 11, 21, tzinfo=timezone.utc):
                    timestamps = pd.to_datetime(["2025-11-21T00:00:00Z", "2026-03-18T00:00:00Z"])
                    closes = [30.2, 31.2]
                else:
                    timestamps = pd.to_datetime(["2026-03-18T00:00:00Z", "2026-03-19T00:00:00Z"])
                    base = {"AAA": 11.4, "BBB": 31.4, "SPY": 21.4}[symbol]
                    closes = [base, base + 1.0]
                frames[symbol] = pd.DataFrame(
                    {
                        "timestamp": timestamps,
                        "open": closes,
                        "high": [value + 0.2 for value in closes],
                        "low": [value - 0.2 for value in closes],
                        "close": closes,
                        "volume": [1000 + idx for idx in range(len(closes))],
                    }
                )
            return frames

    monkeypatch.setattr("pipeline.jobs.main._utc_now", lambda: now)
    monkeypatch.setattr(
        "pipeline.jobs.main.load_latest_dataset_frame",
        lambda dataset_name: (existing, SimpleNamespace(asof_time_utc="2026-03-19T12:00:00+00:00")),
    )

    api = FakeBarsAPI()

    bars, frame = _build_equity_price_history_snapshot(
        api,
        ["AAA", "BBB"],
        benchmark="SPY",
        history_days=120,
        incremental_lookback_days=10,
        full_refresh_hours=168.0,
    )

    assert len(api.calls) == 2
    assert api.calls[0]["symbols"] == ["BBB"]
    assert api.calls[1]["symbols"] == ["AAA", "BBB", "SPY"]
    assert set(bars) == {"AAA", "BBB", "SPY"}
    assert set(frame["symbol"]) == {"AAA", "BBB", "SPY"}
    assert frame.groupby("symbol").size().to_dict() == {"AAA": 3, "BBB": 3, "SPY": 3}
    earliest = frame.groupby("symbol")["timestamp"].min().astype(str).to_dict()
    assert earliest["AAA"].startswith("2025-11-22")
    assert earliest["BBB"].startswith("2025-11-21")
    assert earliest["SPY"].startswith("2025-11-21")
    latest_aaa = frame[frame["symbol"] == "AAA"].sort_values("timestamp").iloc[-1]
    assert latest_aaa["close"] == 12.4


def test_build_quarterly_fundamentals_snapshot_uses_local_when_simfin_not_configured(monkeypatch):
    monkeypatch.setenv("SIMFIN_REFRESH_ENABLED", "true")
    monkeypatch.setattr("pipeline.jobs.main.simfin_refresh_configured", lambda: False)

    captured: list[tuple[list[str], bool, int]] = []

    def _fake_build(symbols, *, prefer_upstream, refresh_days):
        captured.append((list(symbols), prefer_upstream, refresh_days))
        return (
            pd.DataFrame(
                {
                    "ticker": ["RDDT"],
                    "statement": ["income"],
                    "metric": ["Total Revenue"],
                    "report_date": [pd.Timestamp("2024-09-30")],
                    "value": [348351000.0],
                }
            ),
            {"provider": "local", "data_dir": "", "refresh_days": refresh_days},
        )

    monkeypatch.setattr("pipeline.jobs.main.build_quarterly_fundamentals_frame", _fake_build)

    frame, details = _build_quarterly_fundamentals_snapshot(["RDDT"])

    assert len(frame) == 1
    assert details["provider"] == "local"
    assert captured == [(["RDDT"], True, 1)]


def test_build_quarterly_fundamentals_snapshot_prefers_upstream_when_configured(monkeypatch):
    monkeypatch.setenv("SIMFIN_REFRESH_ENABLED", "true")
    monkeypatch.setenv("SIMFIN_REFRESH_DAYS", "3")
    monkeypatch.setattr("pipeline.jobs.main.simfin_refresh_configured", lambda: True)

    captured: list[tuple[list[str], bool, int]] = []

    def _fake_build(symbols, *, prefer_upstream, refresh_days):
        captured.append((list(symbols), prefer_upstream, refresh_days))
        return (
            pd.DataFrame(
                {
                    "ticker": ["RDDT"],
                    "statement": ["income"],
                    "metric": ["Total Revenue"],
                    "report_date": [pd.Timestamp("2024-12-31")],
                    "value": [400000000.0],
                }
            ),
            {"provider": "simfin", "data_dir": "/tmp/simfin_refresh", "refresh_days": refresh_days},
        )

    monkeypatch.setattr("pipeline.jobs.main.build_quarterly_fundamentals_frame", _fake_build)

    frame, details = _build_quarterly_fundamentals_snapshot(["RDDT"])

    assert len(frame) == 1
    assert details["provider"] == "simfin"
    assert details["data_dir"] == "/tmp/simfin_refresh"
    assert captured == [(["RDDT"], True, 3)]


def test_build_treasury_yield_snapshots_uses_service_payload(monkeypatch):
    observations = pd.DataFrame({"date": pd.to_datetime(["2026-03-26"]), "series_id": ["UST_10Y"], "yield_pct": [4.13]})
    summary = pd.DataFrame({"series_id": ["UST_10Y"], "latest_value": [4.13]})
    facts = pd.DataFrame({"ust_10y": [4.13], "ust_10y_1d_bps": [-11.0]})

    monkeypatch.setattr(
        "pipeline.jobs.main.load_treasury_yield_datasets",
        lambda years, end_date=None: {
            "yield_curve_observations": observations,
            "yield_curve_summary": summary,
            "yield_curve_facts_1d": facts,
        },
    )
    monkeypatch.setenv("TREASURY_YIELD_LOOKBACK_YEARS", "2")

    obs_out, summary_out, facts_out = _build_treasury_yield_snapshots(asof_time_utc=datetime(2026, 3, 26, 16, 0, tzinfo=timezone.utc))

    assert obs_out.equals(observations)
    assert summary_out.equals(summary)
    assert facts_out.equals(facts)


def test_run_news_persists_attention_context(monkeypatch):
    class FakeAPI:
        def get_news(self, symbols, limit=50):
            return pd.DataFrame(
                {
                    "headline": ["Apple files current report"],
                    "published_at": [pd.Timestamp("2026-03-20T12:00:00Z")],
                    "source": ["ExampleWire"],
                    "url": ["https://example.com/aapl"],
                    "symbols": [["AAPL"]],
                }
            )

    class FakeEdgarClient:
        def load_recent_filings(self, symbols, *, days, forms, max_filings_per_symbol, fetch_document_text, max_document_fetches_per_symbol, existing_frame):
            assert symbols == ["AAPL"]
            assert days == 120
            assert max_filings_per_symbol == 4
            assert fetch_document_text is True
            assert max_document_fetches_per_symbol == 2
            assert isinstance(existing_frame, pd.DataFrame)
            return pd.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "company_name": ["Apple Inc."],
                    "cik": [320193],
                    "filing_date": [pd.Timestamp("2026-03-20T00:00:00Z")],
                    "form": ["8-K"],
                    "items": ["2.02, 9.01"],
                    "primary_doc_description": ["Current report"],
                    "filing_url": ["https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/a8k.htm"],
                    "filing_excerpt": ["Apple reported strong services growth and accelerated buybacks."],
                    "document_text": ["Apple discussed services growth and repurchases."],
                    "document_text_hash": ["hash-aapl-8k"],
                }
            )

    persisted: list[tuple[str, int]] = []
    ctx = JobContext(
        name="news-ingest-and-features",
        run_id="12345678-test-run",
        asof=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
        universe_version="20260321",
    )

    monkeypatch.setattr("pipeline.jobs.main._alpaca_config", lambda: object())
    monkeypatch.setattr("pipeline.jobs.main.AlpacaAPI", lambda cfg: FakeAPI())
    monkeypatch.setattr(
        "pipeline.jobs.main._load_latest_attention_seed",
        lambda limit: pd.DataFrame({"entity_id": ["AAPL"], "attention_score": [80.0]}),
    )
    monkeypatch.setattr("pipeline.jobs.main._load_latest_materialized_frame", lambda dataset_name: pd.DataFrame())
    monkeypatch.setattr("pipeline.jobs.main.EdgarClient", lambda: FakeEdgarClient())
    monkeypatch.setattr("pipeline.jobs.main.load_llm_client", lambda: None)
    monkeypatch.setattr(
        "pipeline.jobs.main._persist_dataset",
        lambda dataset_name, frame, ctx, conn: persisted.append((dataset_name, len(frame))),
    )

    run_news(ctx, None)

    assert ("news_articles", 1) in persisted
    assert ("news_symbol_map", 1) in persisted
    assert ("edgar_filings", 1) in persisted
    assert ("edgar_evidence", 0) in persisted
    assert ("attention_context_llm", 0) in persisted
    assert ("attention_context_bundle", 1) in persisted


def test_run_news_materializes_attention_home_and_research_outputs(monkeypatch):
    class FakeAPI:
        def get_news(self, symbols, limit=50):
            return pd.DataFrame(
                {
                    "headline": ["Apple extends rally on checkout momentum"],
                    "summary": ["Investors are reacting to better same-day commerce commentary."],
                    "published_at": [pd.Timestamp("2026-03-24T15:30:00Z")],
                    "source": ["Reuters"],
                    "url": ["https://example.com/aapl"],
                    "symbols": [["AAPL"]],
                }
            )

    class FakeEdgarClient:
        def load_recent_filings(self, symbols, **kwargs):
            return pd.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "company_name": ["Apple Inc."],
                    "cik": [320193],
                    "filing_date": [pd.Timestamp("2026-03-24T00:00:00Z")],
                    "form": ["8-K"],
                    "items": ["2.02"],
                    "primary_doc_description": ["Current report"],
                    "filing_url": ["https://example.com/aapl-8k"],
                    "filing_excerpt": ["Apple discussed services growth."],
                    "document_text": ["Apple discussed services growth."],
                    "document_text_hash": ["hash-aapl-8k"],
                }
            )

    persisted: dict[str, pd.DataFrame] = {}
    ctx = JobContext(
        name="news-ingest-and-features",
        run_id="87654321-test-run",
        asof=datetime(2026, 3, 24, 18, 0, tzinfo=timezone.utc),
        universe_version="20260324",
    )

    latest_frames = {
        "daily_movers": pd.DataFrame(
            [
                {"symbol": "AAPL", "change_pct": 4.2, "close": 205.0, "prev_close": 196.7, "volume": 10_000_000, "dollar_volume": 2_050_000_000},
                {"symbol": "TLT", "change_pct": 1.3, "close": 96.1, "prev_close": 94.9, "volume": 6_000_000, "dollar_volume": 576_600_000},
            ]
        ),
        "macro_anchor_daily_movers": pd.DataFrame(
            [
                {"symbol": "USO", "change_pct": -7.9, "close": 69.0, "prev_close": 74.9, "volume": 4_000_000, "dollar_volume": 276_000_000},
            ]
        ),
        "positions_snapshot": pd.DataFrame({"symbol": ["AAPL"], "market_value": [100000.0]}),
        "price_history": pd.DataFrame(
            {
                "symbol": ["AAPL"] * 25 + ["TLT"] * 25 + ["USO"] * 25,
                "timestamp": list(pd.date_range("2026-02-20", periods=25, freq="B", tz="UTC")) * 3,
                "open": [100.0] * 75,
                "high": [101.0] * 75,
                "low": [99.0] * 75,
                "close": ([100.0] * 24 + [104.2]) + ([100.0] * 24 + [101.3]) + ([100.0] * 24 + [92.1]),
                "volume": [1000] * 75,
            }
        ),
        "attention_feed": pd.DataFrame({"entity_id": ["AAPL", "USO"], "attention_score": [88.0, 95.0], "observed_value": [4.2, -7.9], "severity_score": [4.0, 5.0], "asof_time_utc": [pd.Timestamp("2026-03-24T18:00:00Z")] * 2}),
        "commodity_attention_feed": pd.DataFrame({"entity_id": ["TLT"], "attention_score": [70.0], "observed_value": [1.3], "severity_score": [2.0], "asof_time_utc": [pd.Timestamp("2026-03-24T18:00:00Z")]}),
        "edgar_filings": pd.DataFrame(),
        "edgar_evidence": pd.DataFrame(),
        "attention_context_llm": pd.DataFrame(),
    }

    monkeypatch.setattr("pipeline.jobs.main._alpaca_config", lambda: object())
    monkeypatch.setattr("pipeline.jobs.main.AlpacaAPI", lambda cfg: FakeAPI())
    monkeypatch.setattr(
        "pipeline.jobs.main._load_latest_attention_seed",
        lambda limit: pd.DataFrame({"entity_id": ["AAPL", "USO", "TLT"], "attention_score": [88.0, 95.0, 70.0]}),
    )
    monkeypatch.setattr(
        "pipeline.jobs.main._load_latest_materialized_frame",
        lambda dataset_name: latest_frames.get(dataset_name, pd.DataFrame()).copy(),
    )
    monkeypatch.setattr("pipeline.jobs.main.EdgarClient", lambda: FakeEdgarClient())
    monkeypatch.setattr("pipeline.jobs.main.load_llm_client", lambda: None)
    monkeypatch.setattr("pipeline.jobs.main.load_embedding_client", lambda: None)
    monkeypatch.setattr(
        "pipeline.jobs.main.search_symbol_news_payload",
        lambda symbol, max_results=8, company_name="": {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": f"{symbol} search result",
                        "summary": f"{symbol} has same-day web confirmation.",
                        "source": "AP",
                        "published_at": pd.Timestamp("2026-03-24T15:45:00Z"),
                        "url": f"https://example.com/{symbol.lower()}-search",
                    }
                ]
            ),
            "fallback_summary": None,
            "source": "search",
        },
    )
    monkeypatch.setattr(
        "pipeline.jobs.main.build_bottom_up_attention_artifacts",
        lambda *args, **kwargs: SimpleNamespace(
            home_payload={
                "run_id": "87654321-test-run",
                "top_events": [
                    {
                        "bundle_id": "event::oil:USO:event",
                        "market_event_id": "oil:USO:event",
                        "event_type": "macro_cluster",
                        "event_title": "Oil and related assets move together today",
                        "what_happened_text": "Oil-linked instruments fell sharply today.",
                        "why_happened_text": "Markets are pricing lower supply-risk.",
                        "affected_assets_summary_text": "Up: AAPL, TLT | Down: USO",
                        "supporting_symbols": ["USO", "AAPL", "TLT"],
                        "cause_status": "supported",
                        "confidence_label": "High",
                        "surface_summary_text": "Oil-linked instruments fell sharply today. Markets are pricing lower supply-risk.",
                    }
                ],
                "must_read_movers": [
                    {
                        "bundle_id": "symbol::AAPL",
                        "symbol": "AAPL",
                        "headline": "Apple moves sharply today",
                        "what_changed_text": "AAPL rose 4.2% today.",
                        "why_now_text": "Investors are reacting to stronger checkout commentary.",
                        "what_else_moved_text": "Related names also moved today.",
                        "cause_status": "supported",
                        "confidence_label": "High",
                        "candidate_score": 95.0,
                        "change_pct": 4.2,
                        "expected_move_pct": 1.0,
                        "surprise_z": 2.4,
                        "sector": "Technology",
                        "industry": "Consumer Electronics",
                        "source_label": "Technology",
                        "top_source": "Reuters",
                        "best_authority_rank": 1,
                        "source_count": 1,
                        "evidence_count": 1,
                        "same_day_evidence_count": 1,
                        "surface_summary_text": "AAPL rose 4.2% today. Investors are reacting to stronger checkout commentary.",
                    }
                ],
                "unresolved_large_moves": [],
                "generated_at_utc": pd.Timestamp("2026-03-24T18:00:00Z").isoformat(),
                "coverage_summary": {"candidate_count": 2, "event_count": 1, "must_read_count": 1, "unresolved_count": 0},
                "event_candidates_1d": [
                    {
                        "candidate_id": "candidate::AAPL",
                        "symbol": "AAPL",
                        "bundle_id": "symbol::AAPL",
                        "headline": "Apple moves sharply today",
                        "what_changed_text": "AAPL rose 4.2% today.",
                        "why_now_text": "Investors are reacting to stronger checkout commentary.",
                        "cause_status": "supported",
                        "confidence_label": "High",
                        "candidate_score": 95.0,
                        "change_pct": 4.2,
                        "expected_move_pct": 1.0,
                        "surprise_z": 2.4,
                        "sector": "Technology",
                        "industry": "Consumer Electronics",
                    }
                ],
                "event_impacts_1d": [],
                "entity_master": [],
            },
            bundle_map={
                "event::oil:USO:event": {
                    "bundle_id": "event::oil:USO:event",
                    "bundle_type": "event",
                    "run_id": "87654321-test-run",
                    "event_title": "Oil and related assets move together today",
                    "what_happened_text": "Oil-linked instruments fell sharply today.",
                    "why_happened_text": "Markets are pricing lower supply-risk.",
                    "affected_assets_summary_text": "Up: AAPL, TLT | Down: USO",
                    "cause_status": "supported",
                    "confidence_label": "High",
                    "evidence_quality": "High",
                    "freshness_quality": "High",
                    "source_summary": "Reuters",
                },
                "symbol::AAPL": {
                    "bundle_id": "symbol::AAPL",
                    "bundle_type": "symbol",
                    "run_id": "87654321-test-run",
                    "symbol": "AAPL",
                    "headline": "Apple moves sharply today",
                    "what_changed_text": "AAPL rose 4.2% today.",
                    "why_now_text": "Investors are reacting to stronger checkout commentary.",
                    "what_else_moved_text": "Related names also moved today.",
                    "cause_status": "supported",
                    "confidence_label": "High",
                    "evidence_quality": "High",
                    "freshness_quality": "High",
                    "source_summary": "Reuters",
                },
            },
            frames={
                "attention_candidates_1d": pd.DataFrame([{"run_id": "87654321-test-run", "candidate_id": "candidate::AAPL", "symbol": "AAPL"}]),
                "attention_research_plans": pd.DataFrame([{"run_id": "87654321-test-run", "candidate_id": "candidate::AAPL"}]),
                "attention_search_requests": pd.DataFrame([{"run_id": "87654321-test-run", "candidate_id": "candidate::AAPL", "query": "AAPL move today"}]),
                "attention_search_results": pd.DataFrame([{"run_id": "87654321-test-run", "candidate_id": "candidate::AAPL", "title": "AAPL search result", "source": "AP", "snippet": "AAPL has same-day web confirmation.", "url": "https://example.com/aapl-search"}]),
                "attention_source_documents": pd.DataFrame([{"run_id": "87654321-test-run", "candidate_id": "candidate::AAPL", "document_id": "doc::aapl"}]),
                "attention_evidence_chunks": pd.DataFrame([{"run_id": "87654321-test-run", "candidate_id": "candidate::AAPL", "chunk_id": "chunk::aapl"}]),
                "attention_claims": pd.DataFrame([{"run_id": "87654321-test-run", "candidate_id": "candidate::AAPL", "claim_id": "claim::aapl"}]),
                "attention_candidate_graph": pd.DataFrame([{"run_id": "87654321-test-run", "left_candidate_id": "candidate::AAPL", "right_candidate_id": "candidate::USO"}]),
                "attention_event_clusters_1d": pd.DataFrame([{"run_id": "87654321-test-run", "event_id": "oil:USO:event"}]),
            },
        ),
    )
    monkeypatch.setattr(
        "pipeline.jobs.main.collect_attention_ticker_symbols",
        lambda *args, **kwargs: ["AAPL"],
    )
    monkeypatch.setattr(
        "pipeline.jobs.main.build_attention_ticker_snapshot_frame",
        lambda *args, **kwargs: pd.DataFrame(
            [{"symbol": "AAPL", "company_name": "Apple Inc.", "market_cap_label": "$1.00T", "run_id": ctx.run_id}]
        ),
    )
    monkeypatch.setattr(
        "pipeline.jobs.main.build_attention_ticker_background_snapshot_frame",
        lambda *args, **kwargs: pd.DataFrame(
            [{"symbol": "AAPL", "company_name": "Apple Inc.", "description_text": "Apple builds consumer devices.", "run_id": ctx.run_id}]
        ),
    )
    monkeypatch.setattr(
        "pipeline.jobs.main._persist_dataset",
        lambda dataset_name, frame, ctx, conn: persisted.setdefault(dataset_name, frame.copy()),
    )

    run_news(ctx, None)

    assert "attention_web_search_news" in persisted
    assert "attention_home_snapshots_1d" in persisted
    assert "attention_bundle_snapshots" in persisted
    assert "attention_candidates_1d" in persisted
    assert "attention_claims" in persisted
    assert "attention_home_1d" in persisted
    assert "attention_research_bundles" in persisted
    assert "attention_ticker_snapshots_1d" in persisted
    assert "attention_ticker_background_snapshots" in persisted
    assert not persisted["attention_home_1d"].empty
    assert not persisted["attention_research_bundles"].empty
    assert not persisted["attention_ticker_snapshots_1d"].empty
    assert not persisted["attention_ticker_background_snapshots"].empty
    assert set(persisted["attention_research_bundles"]["bundle_id"]) == {"event::oil:USO:event", "symbol::AAPL"}
