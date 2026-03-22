from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from pipeline.jobs.main import (
    JobContext,
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

    assert {"news_articles", "news_symbol_map", "edgar_filings", "edgar_evidence", "attention_context_llm", "attention_context_bundle"}.issubset(news_datasets)


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
