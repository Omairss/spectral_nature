from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd

from pipeline.jobs.attention_home_build import (
    AttentionHomeBuildError,
    _build_page_agentic_summary_frame,
    _build_zopedia_enrichment_frame,
    _news_payloads_from_articles_frame,
    build_attention_home_output_frames,
)
from pipeline.jobs.main import (
    JobContext,
    _build_quarterly_fundamentals_snapshot,
    _build_treasury_yield_snapshots,
    _build_equity_price_history_snapshot,
    _build_portfolio_timeseries_snapshot,
    _company_baselines_with_listing_fallback,
    _db_mark_job_start,
    _persist_dataset,
    _resolve_equity_symbols,
    _upload_frame,
    _upload_manifest,
    run_attention_home,
    run_commodities,
    run_company_baselines,
    run_entity_taxonomy,
    run_fred,
    run_news,
    run_trading_agent,
    run_zopedia_learning_job,
    run_zopedia_maintenance_job,
)
from services.pipeline_store import SOURCE_DATASETS, SOURCE_JOB_MAP


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


def test_upload_manifest_writes_stable_latest_pointer(monkeypatch):
    uploaded: dict[str, dict[str, object]] = {}

    def _capture(path: str, payload: bytes, content_type: str) -> None:
        uploaded[path] = {
            "payload": json.loads(payload.decode("utf-8")),
            "content_type": content_type,
        }

    monkeypatch.setattr("pipeline.jobs.main._upload_bytes", _capture)

    ctx = JobContext(
        name="attention-home-build",
        run_id="abcdef12-test-run",
        asof=datetime(2026, 5, 27, 19, 55, tzinfo=timezone.utc),
        universe_version="20260527",
    )

    manifest = _upload_manifest("attention_home_snapshots_1d", "datasets/home.parquet", pd.DataFrame([{"x": 1}]), ctx)

    assert manifest is not None
    version_path = f"manifests/attention_home_snapshots_1d/{manifest['dataset_version_id']}.json"
    latest_path = "manifests/attention_home_snapshots_1d/latest.json"
    assert version_path in uploaded
    assert latest_path in uploaded
    assert uploaded[latest_path]["payload"]["blob_path"] == "datasets/home.parquet"
    assert uploaded[latest_path]["content_type"] == "application/json"


def test_attention_step_timeout_returns_before_slow_step_finishes():
    import pipeline.jobs.attention_home_build as attention_home_build_module

    started = time.monotonic()
    try:
        attention_home_build_module._call_with_timeout("slow AQL step", 1, lambda: time.sleep(3))
    except attention_home_build_module._AttentionStepTimeout as exc:
        assert "slow AQL step" in str(exc)
    else:
        raise AssertionError("Expected slow step to time out")

    assert time.monotonic() - started < 2


def test_persist_dataset_retains_attention_source_documents_before_upload(monkeypatch):
    captured: dict[str, object] = {}
    ctx = JobContext(
        name="attention-home-build",
        run_id="attention-retention-run",
        asof=datetime(2026, 4, 14, 18, 0, tzinfo=timezone.utc),
        universe_version="20260414",
    )
    frame = pd.DataFrame(
        [
            {
                "document_id": "doc::1",
                "title": "Copper outlook improves on AI demand",
                "raw_text": "Copper demand is rising because AI data-center buildouts are expanding.",
            }
        ]
    )

    def _fake_retention(*args, **kwargs):
        captured["retention_called"] = True
        original = args[1].copy()
        original["raw_text_blob_path"] = ["saa/raw_documents/test.json"]
        return original

    def _fake_upload_frame(dataset_name, uploaded_frame, local_ctx):
        captured["uploaded_dataset_name"] = dataset_name
        captured["uploaded_frame"] = uploaded_frame.copy()
        return "datasets/attention_source_documents/test.parquet"

    def _fake_upload_manifest(dataset_name, path, uploaded_frame, local_ctx):
        return {
            "dataset_version_id": "attention_source_documents__20260414T180000Z__attentio",
            "dataset_name": dataset_name,
            "run_id": local_ctx.run_id,
            "asof_time_utc": local_ctx.asof.isoformat(),
            "ingested_at_utc": local_ctx.asof.isoformat(),
            "universe_version": local_ctx.universe_version,
            "blob_path": path,
            "row_count": int(len(uploaded_frame)),
            "schema_columns": list(uploaded_frame.columns),
        }

    monkeypatch.setattr("pipeline.jobs.main.persist_retained_source_documents", _fake_retention)
    monkeypatch.setattr("pipeline.jobs.main._upload_frame", _fake_upload_frame)
    monkeypatch.setattr("pipeline.jobs.main._upload_manifest", _fake_upload_manifest)
    monkeypatch.setattr("pipeline.jobs.main._db_upsert_dataset_version", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.jobs.main._job_progress", lambda *args, **kwargs: None)

    _persist_dataset("attention_source_documents", frame, ctx, conn=None)

    assert captured["retention_called"] is True
    uploaded_frame = captured["uploaded_frame"]
    assert isinstance(uploaded_frame, pd.DataFrame)
    assert uploaded_frame.loc[0, "raw_text_blob_path"] == "saa/raw_documents/test.json"


def test_persist_dataset_retains_attention_evidence_chunks_before_upload(monkeypatch):
    captured: dict[str, object] = {}
    ctx = JobContext(
        name="attention-home-build",
        run_id="attention-chunk-run",
        asof=datetime(2026, 4, 14, 18, 0, tzinfo=timezone.utc),
        universe_version="20260414",
    )
    frame = pd.DataFrame(
        [
            {
                "chunk_id": "chunk::1",
                "chunk_text": "USO and BNO fell on March 24, 2026 as oil eased after ceasefire headlines.",
                "canonical_document_id": "saa_doc::uso",
            }
        ]
    )

    def _fake_retention(*args, **kwargs):
        captured["retention_called"] = True
        original = args[1].copy()
        original["chunk_record_id"] = ["saa_chunk::uso"]
        return original

    def _fake_upload_frame(dataset_name, uploaded_frame, local_ctx):
        captured["uploaded_dataset_name"] = dataset_name
        captured["uploaded_frame"] = uploaded_frame.copy()
        return "datasets/attention_evidence_chunks/test.parquet"

    def _fake_upload_manifest(dataset_name, path, uploaded_frame, local_ctx):
        return {
            "dataset_version_id": "attention_evidence_chunks__20260414T180000Z__attentio",
            "dataset_name": dataset_name,
            "run_id": local_ctx.run_id,
            "asof_time_utc": local_ctx.asof.isoformat(),
            "ingested_at_utc": local_ctx.asof.isoformat(),
            "universe_version": local_ctx.universe_version,
            "blob_path": path,
            "row_count": int(len(uploaded_frame)),
            "schema_columns": list(uploaded_frame.columns),
        }

    monkeypatch.setattr("pipeline.jobs.main.persist_retained_evidence_chunks", _fake_retention)
    monkeypatch.setattr("pipeline.jobs.main._upload_frame", _fake_upload_frame)
    monkeypatch.setattr("pipeline.jobs.main._upload_manifest", _fake_upload_manifest)
    monkeypatch.setattr("pipeline.jobs.main._db_upsert_dataset_version", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.jobs.main._job_progress", lambda *args, **kwargs: None)

    _persist_dataset("attention_evidence_chunks", frame, ctx, conn=None)

    assert captured["retention_called"] is True
    uploaded_frame = captured["uploaded_frame"]
    assert isinstance(uploaded_frame, pd.DataFrame)
    assert uploaded_frame.loc[0, "chunk_record_id"] == "saa_chunk::uso"


def test_run_zopedia_maintenance_job_calls_service(monkeypatch):
    captured: dict[str, object] = {}
    progress: list[dict[str, object]] = []

    def _fake_run_zopedia_maintenance(**kwargs):
        captured.update(kwargs)
        return {
            "status": "ready",
            "summary": {
                "page_count": 4,
                "edge_count": 6,
                "community_count": 2,
                "issue_count": 1,
            },
        }

    monkeypatch.setenv("ZOPEDIA_MAINTENANCE_PAGE_LIMIT", "321")
    monkeypatch.setenv("ZOPEDIA_MAINTENANCE_STALE_AFTER_DAYS", "90")
    monkeypatch.setenv("ZOPEDIA_MAINTENANCE_BLOAT_CHAR_LIMIT", "12000")
    monkeypatch.setattr("pipeline.jobs.main.run_zopedia_maintenance_service", _fake_run_zopedia_maintenance)
    monkeypatch.setattr(
        "pipeline.jobs.main._job_progress",
        lambda ctx, conn, **kwargs: progress.append({"ctx": ctx, "conn": conn, **kwargs}),
    )
    ctx = JobContext(
        name="zopedia-maintenance",
        run_id="zopedia-maintenance-test",
        asof=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
        universe_version="20260517",
    )
    conn = object()

    run_zopedia_maintenance_job(ctx, conn=conn)

    assert captured["run_id"] == "zopedia-maintenance-test"
    assert captured["page_limit"] == 321
    assert captured["stale_after_days"] == 90
    assert captured["bloat_char_limit"] == 12000
    assert captured["conn"] is conn
    assert progress[-1]["stage"] == "done"
    assert "pages=4" in str(progress[-1]["message"])


def test_run_zopedia_learning_job_calls_service(monkeypatch):
    captured: dict[str, object] = {}
    progress: list[dict[str, object]] = []

    def _fake_run_zopedia_learning(**kwargs):
        captured.update(kwargs)
        return {
            "threads_scanned": 3,
            "events_detected": 2,
            "evals_generated": 2,
            "safe_updates_applied": 1,
            "verified": 1,
            "regressed": 0,
        }

    monkeypatch.setenv("ZOPEDIA_LEARNING_THREAD_LIMIT", "17")
    monkeypatch.setattr("pipeline.jobs.main.run_zopedia_learning_service", _fake_run_zopedia_learning)
    monkeypatch.setattr(
        "pipeline.jobs.main._job_progress",
        lambda ctx, conn, **kwargs: progress.append({"ctx": ctx, "conn": conn, **kwargs}),
    )
    ctx = JobContext(
        name="zopedia-learning",
        run_id="zopedia-learning-test",
        asof=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        universe_version="20260519",
    )
    conn = object()

    run_zopedia_learning_job(ctx, conn=conn)

    assert captured["limit"] == 17
    assert captured["conn"] is conn
    assert progress[-1]["stage"] == "done"
    assert "events=2" in str(progress[-1]["message"])
    assert "verified=1" in str(progress[-1]["message"])


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
    assert "portfolio_timeseries_snapshot" in equity_datasets


def test_pipeline_store_lists_yield_datasets_under_fred():
    fred_datasets = set(SOURCE_DATASETS["fred"])

    assert {
        "fred_series_index",
        "fred_release_index",
        "yield_curve_observations",
        "yield_curve_summary",
        "yield_curve_facts_1d",
    }.issubset(fred_datasets)


def test_run_fred_persists_series_and_release_indexes(monkeypatch):
    persisted: list[str] = []
    dashboard = {
        "summary": pd.DataFrame({"series_id": ["CPIAUCSL"]}),
        "observations": pd.DataFrame(
            {
                "series_id": ["CPIAUCSL"],
                "date": [pd.Timestamp("2026-03-01")],
                "value": [316.2],
                "release_id": [10],
            }
        ),
        "series_index": pd.DataFrame({"series_id": ["CPIAUCSL"], "release_id": [10], "release_name": ["Consumer Price Index Release"]}),
        "release_index": pd.DataFrame({"release_id": [10], "release_name": ["Consumer Price Index Release"]}),
    }
    ctx = JobContext(
        name="macro-fred-daily",
        run_id="fred-test-run",
        asof=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
        universe_version="20260320",
    )

    monkeypatch.setattr("pipeline.jobs.main.load_fred_api_key", lambda: "fred-key")
    monkeypatch.setattr("pipeline.jobs.main.load_fred_dashboard", lambda api_key, years: dashboard)
    monkeypatch.setattr(
        "pipeline.jobs.main._build_treasury_yield_snapshots",
        lambda asof_time_utc: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()),
    )
    monkeypatch.setattr(
        "pipeline.jobs.main._build_broad_economy_page_summary_frame",
        lambda **kwargs: pd.DataFrame([{"surface": "Broad Economy", "run_id": ctx.run_id}]),
    )
    monkeypatch.setattr("pipeline.jobs.main._persist_dataset", lambda dataset_name, frame, ctx, conn: persisted.append(dataset_name))
    monkeypatch.setattr("pipeline.jobs.main._job_progress", lambda *args, **kwargs: None)

    run_fred(ctx, conn=object())

    assert {
        "fred_summary",
        "fred_observations",
        "fred_series_index",
        "fred_release_index",
        "page_agentic_summaries",
    }.issubset(set(persisted))


def test_run_fred_raises_if_fred_step_fails(monkeypatch):
    persisted: list[str] = []
    ctx = JobContext(
        name="macro-fred-daily",
        run_id="fred-fail-test-run",
        asof=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
        universe_version="20260320",
    )

    monkeypatch.setattr("pipeline.jobs.main.load_fred_api_key", lambda: "fred-key")
    monkeypatch.setattr(
        "pipeline.jobs.main.load_fred_dashboard",
        lambda api_key, years: (_ for _ in ()).throw(RuntimeError("simulated fred failure")),
    )
    monkeypatch.setattr(
        "pipeline.jobs.main._build_treasury_yield_snapshots",
        lambda asof_time_utc: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()),
    )
    monkeypatch.setattr("pipeline.jobs.main._persist_dataset", lambda dataset_name, frame, ctx, conn: persisted.append(dataset_name))
    monkeypatch.setattr("pipeline.jobs.main._job_progress", lambda *args, **kwargs: None)

    try:
        run_fred(ctx, conn=object())
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "FRED preload failed" in str(exc)

    assert raised
    assert {"yield_curve_observations", "yield_curve_summary", "yield_curve_facts_1d"}.issubset(set(persisted))
    assert "fred_summary" not in persisted


def test_run_fred_raises_if_key_missing(monkeypatch):
    persisted: list[str] = []
    ctx = JobContext(
        name="macro-fred-daily",
        run_id="fred-missing-key-test-run",
        asof=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
        universe_version="20260320",
    )

    monkeypatch.setattr("pipeline.jobs.main.load_fred_api_key", lambda: "")
    monkeypatch.setattr(
        "pipeline.jobs.main._build_treasury_yield_snapshots",
        lambda asof_time_utc: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()),
    )
    monkeypatch.setattr("pipeline.jobs.main._persist_dataset", lambda dataset_name, frame, ctx, conn: persisted.append(dataset_name))
    monkeypatch.setattr("pipeline.jobs.main._job_progress", lambda *args, **kwargs: None)

    try:
        run_fred(ctx, conn=object())
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "FRED key unavailable" in str(exc)

    assert raised
    assert {"yield_curve_observations", "yield_curve_summary", "yield_curve_facts_1d"}.issubset(set(persisted))
    assert "fred_summary" not in persisted


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


def test_run_commodities_uses_proxy_universe_and_reference_basket(monkeypatch):
    ctx = JobContext(
        name="commodities-regime",
        run_id="commodity-test-run",
        asof=datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc),
        universe_version="20260409",
    )
    captured: dict[str, object] = {}
    persisted: list[str] = []

    monkeypatch.setattr("pipeline.jobs.main._alpaca_config", lambda: object())
    monkeypatch.setattr("pipeline.jobs.main.AlpacaAPI", lambda cfg: SimpleNamespace())
    monkeypatch.setattr("pipeline.jobs.main.default_commodity_proxy_symbols", lambda: ["USO", "GLD", "CPER"])
    monkeypatch.setattr("pipeline.jobs.main.commodity_reference_universe", lambda: ["PDBC", "USO", "GLD", "CPER"])
    monkeypatch.setattr("pipeline.jobs.main._job_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.jobs.main._persist_dataset", lambda dataset_name, frame, ctx, conn: persisted.append(dataset_name))

    def _fake_scan(api, *, symbols, commodity_symbols, days):
        captured["symbols"] = list(symbols)
        captured["commodity_symbols"] = list(commodity_symbols)
        captured["days"] = days
        return {"summary": pd.DataFrame(), "history": pd.DataFrame()}

    monkeypatch.setattr("pipeline.jobs.main.scan_commodity_regimes", _fake_scan)
    monkeypatch.setattr(
        "pipeline.jobs.main.build_commodity_peer_group_membership",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("stop after preload assertion")),
    )

    run_commodities(ctx, conn=object())

    assert captured == {
        "symbols": ["USO", "GLD", "CPER"],
        "commodity_symbols": ["PDBC", "USO", "GLD", "CPER"],
        "days": 252,
    }
    assert {"commodity_regime_summary", "commodity_regime_history"}.issubset(set(persisted))


def test_pipeline_store_lists_attention_context_datasets_under_news():
    news_datasets = set(SOURCE_DATASETS["news"])

    assert {
        "news_articles",
        "news_symbol_map",
        "edgar_filings",
        "edgar_evidence",
        "attention_context_llm",
        "attention_context_bundle",
        "zopedia_business_model_research_plans",
        "zopedia_business_model_search_requests",
        "zopedia_business_model_search_results",
        "zopedia_ticker_business_model_stacks",
        "zopedia_news_business_resolutions",
        "zopedia_company_business_memory_pages",
    }.issubset(news_datasets)


def test_company_baselines_use_listing_names_without_inventing_business_text():
    baselines = pd.DataFrame(
        [
            {
                "symbol": "BX",
                "company_background_text": "",
            }
        ]
    )
    listings = pd.DataFrame(
        [
            {
                "symbol": "BX",
                "security_name": "Blackstone Inc. Common Stock",
            },
            {
                "symbol": "OBDC",
                "security_name": "Blue Owl Capital Corporation Common Stock",
            },
        ]
    )

    frame = _company_baselines_with_listing_fallback(baselines, listings)
    rows = {row["symbol"]: row for row in frame.to_dict("records")}

    assert rows["BX"]["company_name"] == "Blackstone Inc. Common Stock"
    assert rows["BX"].get("company_background_text") == ""
    assert rows["OBDC"]["company_name"] == "Blue Owl Capital Corporation Common Stock"


def test_pipeline_store_lists_attention_job_and_datasets():
    assert SOURCE_JOB_MAP["attention"] == "attention-home-build"

    attention_datasets = set(SOURCE_DATASETS["attention"])
    assert {
        "attention_home_1d",
        "attention_research_bundles",
        "attention_ticker_snapshots_1d",
        "attention_ticker_background_snapshots",
    }.issubset(attention_datasets)


def test_pipeline_store_lists_trading_agent_job_and_datasets():
    assert SOURCE_JOB_MAP["trading_agent"] == "trading-agent-build"
    assert set(SOURCE_DATASETS["trading_agent"]) == {"trading_agent_runs", "trading_agent_candidates"}


def test_run_trading_agent_persists_runs_and_candidates(monkeypatch):
    ctx = JobContext(
        name="trading-agent-build",
        run_id="trading-agent-run",
        asof=datetime(2026, 5, 5, 13, 0, tzinfo=timezone.utc),
        universe_version="20260505",
    )
    opportunity_rows = []
    horizon_specs = [
        ("1w", "return_7d_pct", "1 Week"),
        ("1m", "return_1m_pct", "1 Month"),
        ("3m", "return_3m_pct", "3 Month"),
        ("1y", "return_1y_pct", "1 Year"),
        ("5y", "return_5y_pct", "5 Year"),
    ]
    for horizon_key, horizon_col, horizon_label in horizon_specs:
        opportunity_rows.append(
            {
                "business_filter": "All Market",
                "horizon_key": horizon_key,
                "selected_horizon_col": horizon_col,
                "selected_horizon_label": horizon_label,
                "rank": 1,
                "symbol": "AAPL",
                "company_name": "Apple Inc.",
                "opportunity": "Upside momentum",
                "direction": "Up / accelerating",
                "opportunity_score": 91.0,
                "daily_change_pct": 1.1,
                horizon_col: 7.5,
                "momentum_roc_score": 0.7,
                "trend_fit_gap": 0.12,
                "details": "Momentum setup.",
            }
        )

    def _fake_load(dataset_name):
        if dataset_name == "market_opportunity_feed":
            return pd.DataFrame(opportunity_rows)
        if dataset_name == "page_agentic_summaries":
            return pd.DataFrame()
        return pd.DataFrame()

    persisted: dict[str, pd.DataFrame] = {}

    def _fake_persist(dataset_name, frame, local_ctx, conn):
        persisted[dataset_name] = frame.copy()

    def _fake_suggestions(*, context, llm_client, aql_agent_runner=None):
        controls = context["controls"]
        return {
            "status": "ok",
            "regime_read": f"Regime {controls['horizon_key']}",
            "portfolio_posture": "Selective.",
            "candidates": [
                {
                    "ticker": "AAPL",
                    "direction": "long",
                    "setup": "Momentum continuation",
                    "hypothesis": "Momentum can persist.",
                    "evidence": ["Ranked feed row."],
                    "invalidation": "Break below support.",
                    "tail_risks": ["Macro shock"],
                    "suggested_horizon": controls["selected_horizon_label"],
                    "confidence": "medium",
                }
            ],
            "data_gaps": [],
            "error": "",
            "aql_agent": {"status": "completed", "answer_markdown": "Grounded."},
        }

    monkeypatch.setattr("pipeline.jobs.main._load_latest_materialized_frame", _fake_load)
    monkeypatch.setattr("pipeline.jobs.main._persist_dataset", _fake_persist)
    monkeypatch.setattr("pipeline.jobs.main.load_aql_zopedia_llm_client", lambda **kwargs: object())
    monkeypatch.setattr("pipeline.jobs.main.build_trading_agent_suggestions", _fake_suggestions)
    monkeypatch.setenv("TRADING_AGENT_HORIZON_TIMEOUT_SECONDS", "30")

    run_trading_agent(ctx, conn=None)

    assert set(persisted) == {"trading_agent_runs", "trading_agent_candidates"}
    assert persisted["trading_agent_runs"]["horizon_key"].tolist() == ["1w", "1m", "3m", "1y", "5y"]
    assert persisted["trading_agent_candidates"]["horizon_key"].tolist() == ["1w", "1m", "3m", "1y", "5y"]


def test_attention_home_news_payloads_accept_array_symbols():
    news_frame = pd.DataFrame(
        {
            "headline": ["Array-backed symbols work"],
            "summary": ["Regression coverage for materialized attention inputs."],
            "source": ["UnitTest"],
            "symbols": [np.array(["AAA", "BBB"])],
            "published_at": [pd.Timestamp("2026-03-30T18:00:00Z")],
            "url": ["https://example.com/array-symbols"],
        }
    )

    payloads = _news_payloads_from_articles_frame(news_frame, symbols=["AAA", "BBB"], limit=8)

    assert payloads["AAA"]["source"] == "pipeline"
    assert payloads["BBB"]["source"] == "pipeline"
    assert payloads["AAA"]["articles"]["headline"].tolist() == ["Array-backed symbols work"]
    assert payloads["BBB"]["articles"]["headline"].tolist() == ["Array-backed symbols work"]


def test_build_attention_home_output_frames_backfills_missing_news_with_search(monkeypatch):
    import pipeline.jobs.attention_home_build as attention_home_build_module

    captured: dict[str, pd.DataFrame] = {}
    ctx = SimpleNamespace(asof=pd.Timestamp("2026-03-30T18:00:00Z"), run_id="run-123")

    monkeypatch.setattr(attention_home_build_module, "shortlist_attention_symbols_1d", lambda *args, **kwargs: ["VRDN"])
    monkeypatch.setattr(attention_home_build_module, "load_embedding_client", lambda: None)
    monkeypatch.setattr(attention_home_build_module, "search_symbol_news_payload", lambda *args, **kwargs: {
        "articles": pd.DataFrame(
            [
                {
                    "headline": "Viridian posts trial update",
                    "summary": "Coverage focused on the company's thyroid eye disease program.",
                    "description": "Coverage focused on the company's thyroid eye disease program.",
                    "source": "SerpApi",
                    "published_at": pd.Timestamp("2026-03-30T12:00:00Z"),
                    "url": "https://example.com/vrdn-news",
                }
            ]
        ),
        "fallback_summary": None,
        "source": "serpapi",
    })
    monkeypatch.setattr(
        attention_home_build_module,
        "build_bottom_up_attention_artifacts",
        lambda *args, **kwargs: SimpleNamespace(
            home_payload={
                "run_id": ctx.run_id,
                "top_events": [],
                "must_read_movers": [{"symbol": "VRDN", "bundle_id": "symbol::VRDN"}],
                "unresolved_large_moves": [],
                "coverage_summary": {},
            },
            bundle_map={"symbol::VRDN": {"bundle_id": "symbol::VRDN", "related_symbols": [], "peer_moves": []}},
            frames={"attention_search_results": pd.DataFrame()},
        ),
    )
    monkeypatch.setattr(attention_home_build_module, "build_attention_entity_master", lambda *args, **kwargs: [])
    monkeypatch.setattr(attention_home_build_module, "resolve_macro_anchor_symbols", lambda symbols: [])
    monkeypatch.setattr(attention_home_build_module, "collect_attention_ticker_symbols", lambda *args, **kwargs: ["VRDN"])
    monkeypatch.setattr(
        attention_home_build_module,
        "build_attention_ticker_snapshot_frame",
        lambda *args, **kwargs: pd.DataFrame([{"symbol": "VRDN", "run_id": ctx.run_id}]),
    )
    monkeypatch.setattr(attention_home_build_module, "_market_opportunity_focus_symbol_map", lambda *args, **kwargs: {"All Market": []})
    monkeypatch.setattr(attention_home_build_module, "build_attention_knowledge_graph_proposals", lambda *args, **kwargs: pd.DataFrame())

    def _background_snapshot(*args, news_frame: pd.DataFrame, **kwargs) -> pd.DataFrame:
        captured["news_frame"] = news_frame.copy()
        return pd.DataFrame([{"symbol": "VRDN", "company_name": "Viridian Therapeutics", "run_id": ctx.run_id}])

    monkeypatch.setattr(attention_home_build_module, "build_attention_ticker_background_snapshot_frame", _background_snapshot)
    monkeypatch.setattr(attention_home_build_module, "_build_zopedia_enrichment_frame", lambda *args, **kwargs: pd.DataFrame())

    outputs = build_attention_home_output_frames(
        ctx=ctx,
        daily_movers=pd.DataFrame(
            [{"symbol": "VRDN", "change_pct": 8.4, "close": 21.3, "prev_close": 19.7, "volume": 1000000, "dollar_volume": 21300000.0}]
        ),
        macro_movers=pd.DataFrame(),
        positions_frame=pd.DataFrame(),
        price_history_frame=pd.DataFrame(
            {
                "symbol": ["VRDN"] * 5,
                "timestamp": pd.date_range("2026-03-20", periods=5, freq="B", tz="UTC"),
                "open": [18.0, 18.5, 19.0, 20.0, 20.8],
                "high": [18.5, 19.0, 19.8, 20.6, 21.6],
                "low": [17.8, 18.2, 18.8, 19.7, 20.4],
                "close": [18.3, 18.9, 19.6, 20.4, 21.3],
                "volume": [100000] * 5,
            }
        ),
        attention_feed_frame=pd.DataFrame(),
        commodity_attention_feed_frame=pd.DataFrame(),
        news_frame=pd.DataFrame(),
        attention_context_frame=pd.DataFrame(),
        edgar_filings_frame=pd.DataFrame(),
        llm_client=None,
        load_materialized_frame_fn=lambda dataset_name: pd.DataFrame(
            {"symbol": ["VRDN"], "security_name": ["Viridian Therapeutics"]}
        )
        if dataset_name == "universe_snapshot"
        else pd.DataFrame(),
    )

    assert "attention_web_search_news" in outputs
    assert not outputs["attention_web_search_news"].empty
    assert outputs["attention_web_search_news"]["symbol"].tolist() == ["VRDN"]
    assert outputs["attention_web_search_news"]["headline"].tolist() == ["Viridian posts trial update"]
    assert "news_frame" in captured
    assert captured["news_frame"]["headline"].tolist() == ["Viridian posts trial update"]


def test_build_page_agentic_summary_frame_materializes_page_summaries(monkeypatch):
    import pipeline.jobs.attention_home_build as attention_home_build_module

    monkeypatch.setenv("PAGE_AGENTIC_STOCK_SUMMARY_LIMIT", "1")
    monkeypatch.setattr(
        attention_home_build_module,
        "build_page_agentic_summary",
        lambda *, surface, context, llm_client: {
            "status": "ok",
            "surface": surface,
            "headline": f"{surface} summary",
            "summary_markdown": "Grounded page summary.",
            "watch_items": [],
            "data_gaps": [],
            "confidence": "medium",
        },
    )
    frame = _build_page_agentic_summary_frame(
        ctx=SimpleNamespace(asof=pd.Timestamp("2026-04-27T17:00:00Z"), run_id="run-page"),
        llm_client=object(),
        daily_movers=pd.DataFrame([{"symbol": "AAPL", "change_pct": 3.1, "close": 205.0}]),
        momentum_profiles=pd.DataFrame(
            [
                {
                    "symbol": "AAPL",
                    "return_1m_pct": 8.2,
                    "return_1w_pct": 2.1,
                    "return_3m_pct": 11.0,
                    "momentum_roc_score": 0.8,
                    "trend_fit_gap": 0.1,
                }
            ]
        ),
        ticker_background_frame=pd.DataFrame(
            [
                {
                    "symbol": "AAPL",
                    "company_name": "Apple Inc.",
                    "description_text": "Apple builds consumer devices.",
                    "news_summary_lines_json": json.dumps(["Apple context line."]),
                    "recent_headlines_json": json.dumps([]),
                    "source_trace_json": json.dumps({}),
                }
            ]
        ),
        technical_signals_latest_frame=pd.DataFrame([{"symbol": "AAPL", "signal": "up"}]),
        universe_snapshot_frame=pd.DataFrame([{"symbol": "AAPL", "security_name": "Apple Inc."}]),
    )

    assert set(frame["surface"]) == {"Market Explorer", "Stock Investigator"}
    assert frame["summary_json"].astype(str).str.contains("Grounded page summary").all()
    assert frame["context_signature"].astype(str).str.len().min() == 64


def test_build_page_agentic_summary_frame_fails_closed_on_timeout(monkeypatch):
    import pipeline.jobs.attention_home_build as attention_home_build_module

    def _timeout(label, timeout_seconds, func):
        raise attention_home_build_module._AttentionStepTimeout(f"{label} exceeded {timeout_seconds}s")

    monkeypatch.setenv("PAGE_AGENTIC_STOCK_SUMMARY_LIMIT", "0")
    monkeypatch.setattr(attention_home_build_module, "_call_with_timeout", _timeout)

    frame = _build_page_agentic_summary_frame(
        ctx=SimpleNamespace(asof=pd.Timestamp("2026-04-27T17:00:00Z"), run_id="run-page"),
        llm_client=object(),
        daily_movers=pd.DataFrame([{"symbol": "AAPL", "change_pct": 3.1, "close": 205.0}]),
        momentum_profiles=pd.DataFrame(
            [
                {
                    "symbol": "AAPL",
                    "return_1m_pct": 8.2,
                    "return_1w_pct": 2.1,
                    "return_3m_pct": 11.0,
                    "momentum_roc_score": 0.8,
                    "trend_fit_gap": 0.1,
                }
            ]
        ),
        ticker_background_frame=pd.DataFrame(),
        technical_signals_latest_frame=pd.DataFrame(),
        universe_snapshot_frame=pd.DataFrame(),
    )

    assert not frame.empty
    assert set(frame["status"]) == {"unavailable"}
    summaries = [json.loads(value) for value in frame["summary_json"].astype(str)]
    assert all(not summary["summary_markdown"] for summary in summaries)
    assert all("Page summary failed" in summary["data_gaps"][0] for summary in summaries)


def test_db_mark_job_start_uses_matching_parameter_count():
    class _Cursor:
        def __init__(self, calls: list[tuple[str, tuple[object, ...]]]):
            self._calls = calls

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query: str, params: tuple[object, ...]) -> None:
            self._calls.append((query, params))

    class _Conn:
        def __init__(self):
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self.commits = 0

        def cursor(self):
            return _Cursor(self.calls)

        def commit(self) -> None:
            self.commits += 1

    ctx = JobContext(
        name="attention-home-build",
        run_id="12345678-test-run",
        asof=datetime(2026, 3, 30, 18, 0, tzinfo=timezone.utc),
        universe_version="20260330",
    )
    conn = _Conn()

    _db_mark_job_start(conn, ctx)

    assert conn.commits == 1
    assert len(conn.calls) == 1
    query, params = conn.calls[0]
    assert query.count("%s") == 12
    assert len(params) == 12


def test_pipeline_store_lists_taxonomy_job_and_datasets():
    assert SOURCE_JOB_MAP["taxonomy"] == "entity-taxonomy-refresh"
    assert SOURCE_JOB_MAP["company_baseline"] == "company-baseline-prefetch"
    assert {"us_equity_listings", "entity_taxonomy_labels", "company_baselines"}.issubset(set(SOURCE_DATASETS["taxonomy"]))
    assert SOURCE_DATASETS["company_baseline"] == ["company_baselines"]


def test_run_company_baselines_persists_capped_rows(monkeypatch):
    ctx = JobContext(
        name="company-baseline-prefetch",
        run_id="12345678-test-run",
        asof=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
        universe_version="20260429",
    )
    universe = pd.DataFrame(
        {
            "symbol": ["VRT", "NVDA", "AAPL"],
            "name": ["Vertiv Holdings Co", "NVIDIA Corporation", "Apple Inc."],
            "rank": [1, 2, 3],
        }
    )
    persisted: dict[str, pd.DataFrame] = {}

    monkeypatch.setenv("COMPANY_BASELINE_LIMIT", "2")
    monkeypatch.setenv("COMPANY_BASELINE_REQUEST_DELAY_SECONDS", "0")
    monkeypatch.delenv("UNIVERSE_SYMBOLS", raising=False)
    monkeypatch.setattr("pipeline.jobs.main._load_latest_equity_universe_snapshot", lambda target_size: universe.copy())
    monkeypatch.setattr("pipeline.jobs.main._persist_dataset", lambda dataset_name, frame, ctx, conn: persisted.setdefault(dataset_name, frame.copy()))
    monkeypatch.setattr(
        "pipeline.jobs.main.build_company_baseline_frame",
        lambda universe_frame, symbols, limit, asof_time_utc, run_id: pd.DataFrame(
            {
                "symbol": universe_frame["symbol"].head(limit).tolist(),
                "company_background_text": ["Vertiv makes power and thermal infrastructure.", "NVIDIA makes accelerated computing chips."],
                "run_id": [run_id] * min(limit, len(universe_frame)),
            }
        ),
    )

    run_company_baselines(ctx, None)

    frame = persisted["company_baselines"]
    assert frame["symbol"].tolist() == ["VRT", "NVDA"]
    assert frame["run_id"].eq("12345678-test-run").all()


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


def test_run_entity_taxonomy_persists_listing_and_taxonomy_datasets(monkeypatch):
    ctx = JobContext(
        name="entity-taxonomy-refresh",
        run_id="12345678-test-run",
        asof=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
        universe_version="20260320",
    )
    listings = pd.DataFrame(
        [
            {"symbol": "AAL", "exchange": "NASDAQ", "security_name": "American Airlines Group Inc.", "is_etf": False, "source_file": "nasdaqlisted"},
        ]
    )
    snapshot = pd.DataFrame(
        [
            {
                "symbol": "AAL",
                "exchange": "NASDAQ",
                "security_name": "American Airlines Group Inc.",
                "listing_source": "nasdaqlisted",
                "is_active": True,
                "is_etf": False,
                "asset_class": "equity",
                "security_type": "common_stock",
                "sector": "Industrials",
                "industry": "Airlines",
                "peer_group_name": "Airlines",
                "peer_group_id": "Airlines",
                "country": "US",
                "commodity_role": "",
                "rates_role": "",
                "defensive_role": "",
                "macro_role_tags": ["travel"],
                "business_role_tags": ["travel_mobility"],
                "source_of_truth": "llm_taxonomy",
                "label_provider": "llm",
                "label_confidence": "medium",
                "is_curated": False,
                "override_reason": "Dynamic taxonomy classification.",
                "classifier_model": "gpt-5-mini",
                "classifier_version": "llm_taxonomy_v2",
                "updated_at_utc": pd.Timestamp("2026-03-20T12:00:00Z"),
            }
        ]
    )
    persisted: list[tuple[str, int]] = []
    db_events: list[object] = []

    monkeypatch.setattr("pipeline.jobs.main._build_us_equity_listings_snapshot", lambda: listings)
    monkeypatch.setattr("pipeline.jobs.main.fetch_entity_taxonomy_frame", lambda: pd.DataFrame())
    monkeypatch.setattr("pipeline.jobs.main.load_aql_zopedia_llm_client", lambda **kwargs: None)
    monkeypatch.setattr("pipeline.jobs.main.build_entity_taxonomy_snapshot", lambda *args, **kwargs: snapshot)
    monkeypatch.setattr(
        "pipeline.jobs.main._persist_dataset",
        lambda dataset_name, frame, ctx, conn: persisted.append((dataset_name, len(frame))),
    )
    monkeypatch.setattr(
        "pipeline.jobs.main.upsert_entity_taxonomy_frame",
        lambda conn, frame: db_events.append(("upsert", len(frame))),
    )
    monkeypatch.setattr(
        "pipeline.jobs.main.deactivate_missing_taxonomy_symbols",
        lambda conn, symbols: db_events.append(("deactivate", list(symbols))),
    )

    run_entity_taxonomy(ctx, conn=object())

    assert persisted == [("us_equity_listings", 1), ("entity_taxonomy_labels", 1)]
    assert db_events == [("upsert", 1), ("deactivate", ["AAL"])]


def test_run_entity_taxonomy_records_progress_updates(monkeypatch):
    ctx = JobContext(
        name="entity-taxonomy-refresh",
        run_id="12345678-test-run",
        asof=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
        universe_version="20260320",
    )
    listings = pd.DataFrame(
        [
            {"symbol": "AAL", "exchange": "NASDAQ", "security_name": "American Airlines Group Inc.", "is_etf": False, "source_file": "nasdaqlisted"},
        ]
    )
    snapshot = pd.DataFrame(
        [
            {
                "symbol": "AAL",
                "exchange": "NASDAQ",
                "security_name": "American Airlines Group Inc.",
                "listing_source": "nasdaqlisted",
                "is_active": True,
                "is_etf": False,
                "asset_class": "equity",
                "security_type": "common_stock",
                "sector": "Industrials",
                "industry": "Airlines",
                "peer_group_name": "Airlines",
                "peer_group_id": "Airlines",
                "country": "US",
                "commodity_role": "",
                "rates_role": "",
                "defensive_role": "",
                "macro_role_tags": [],
                "business_role_tags": ["travel_mobility"],
                "source_of_truth": "llm_taxonomy",
                "label_provider": "llm",
                "label_confidence": "medium",
                "is_curated": False,
                "override_reason": "Dynamic taxonomy classification.",
                "classifier_model": "gpt-5-mini",
                "classifier_version": "llm_taxonomy_v2",
                "updated_at_utc": pd.Timestamp("2026-03-20T12:00:00Z"),
            }
        ]
    )
    progress_updates: list[tuple[str, str, float | None]] = []

    monkeypatch.setattr("pipeline.jobs.main._build_us_equity_listings_snapshot", lambda: listings)
    monkeypatch.setattr("pipeline.jobs.main.fetch_entity_taxonomy_frame", lambda: pd.DataFrame())
    monkeypatch.setattr("pipeline.jobs.main.load_aql_zopedia_llm_client", lambda **kwargs: None)

    def _fake_build_snapshot(*args, **kwargs):
        callback = kwargs.get("progress_callback")
        assert callback is not None
        callback({"event": "snapshot_prepare", "listing_count": 1, "existing_dynamic_count": 0, "unresolved_count": 1})
        callback({"event": "classify_start", "allow_unknown": True, "total_symbols": 1, "total_batches": 1, "batch_size": 1})
        callback({"event": "batch_complete", "allow_unknown": True, "batch_index": 1, "total_batches": 1, "batch_size": 1, "classified_in_batch": 1, "total_symbols": 1, "total_classified": 1, "first_symbol": "AAL", "last_symbol": "AAL"})
        callback({"event": "snapshot_complete", "row_count": 1})
        return snapshot

    monkeypatch.setattr("pipeline.jobs.main.build_entity_taxonomy_snapshot", _fake_build_snapshot)
    monkeypatch.setattr("pipeline.jobs.main._persist_dataset", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.jobs.main.upsert_entity_taxonomy_frame", lambda conn, frame: None)
    monkeypatch.setattr("pipeline.jobs.main.deactivate_missing_taxonomy_symbols", lambda conn, symbols: None)
    monkeypatch.setattr(
        "pipeline.jobs.main._job_progress",
        lambda ctx, conn, *, stage, message, progress_pct=None, status="Running": progress_updates.append((stage, message, progress_pct)),
    )

    run_entity_taxonomy(ctx, conn=object())

    stages = [item[0] for item in progress_updates]
    assert "starting" in stages
    assert "listings_ready" in stages
    assert "classification_setup" in stages
    assert "prepare_snapshot" in stages
    assert "initial_llm_pass" in stages
    assert "snapshot_complete" in stages
    assert "persist_taxonomy_snapshot" in stages


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
    monkeypatch.setattr("pipeline.jobs.main._symbols_from_latest_universe", lambda limit: [])
    monkeypatch.setattr(
        "pipeline.jobs.main._load_latest_attention_seed",
        lambda limit: pd.DataFrame({"entity_id": ["AAPL"], "attention_score": [80.0]}),
    )
    monkeypatch.setattr("pipeline.jobs.main._load_latest_materialized_frame", lambda dataset_name: pd.DataFrame())
    monkeypatch.setattr("pipeline.jobs.main.EdgarClient", lambda: FakeEdgarClient())
    monkeypatch.setattr("pipeline.jobs.main.load_aql_zopedia_llm_client", lambda **kwargs: None)
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
    assert ("zopedia_business_model_research_plans", 1) in persisted
    assert ("zopedia_business_model_search_requests", 0) in persisted
    assert ("zopedia_business_model_search_results", 0) in persisted
    assert ("zopedia_ticker_business_model_stacks", 1) in persisted
    assert ("zopedia_news_business_resolutions", 1) in persisted
    assert ("zopedia_company_business_memory_pages", 0) in persisted


def test_run_attention_home_materializes_attention_home_and_research_outputs(monkeypatch):
    persisted: dict[str, pd.DataFrame] = {}
    ctx = JobContext(
        name="attention-home-build",
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
        "attention_context_bundle": pd.DataFrame(
            [{"symbol": "AAPL", "llm_summary_text": "Apple context summary."}]
        ),
        "universe_snapshot": pd.DataFrame({"symbol": ["AAPL", "USO", "TLT"]}),
    }

    monkeypatch.setattr(
        "pipeline.jobs.main._load_latest_materialized_frame",
        lambda dataset_name: latest_frames.get(dataset_name, pd.DataFrame()).copy(),
    )
    monkeypatch.setattr("pipeline.jobs.attention_home_build.load_aql_zopedia_llm_client", lambda **kwargs: None)
    monkeypatch.setattr("pipeline.jobs.attention_home_build.load_embedding_client", lambda: None)
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build.attach_attention_home_summary_audio",
        lambda summary_payload: {
            **dict(summary_payload),
            "audio_base64": "cHJlYnVpbHQtYXVkaW8=",
            "audio_mime_type": "audio/mpeg",
            "audio_file_extension": "mp3",
            "voice_id": "voice-123",
            "model_id": "eleven_multilingual_v2",
            "output_format": "mp3_44100_128",
        },
    )
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build._build_zopedia_enrichment_frame",
        lambda *args, **kwargs: pd.DataFrame(),
    )
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build._review_home_public_surface",
        lambda payload, bundle_map, **kwargs: (payload, bundle_map, {"status": "accepted"}),
    )
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build._market_opportunity_focus_symbol_map",
        lambda *args, **kwargs: {"All Market": []},
    )
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build.build_attention_entity_master",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build.search_symbol_news_payload",
        lambda *args, **kwargs: {"articles": pd.DataFrame(), "fallback_summary": None, "source": "unit-test"},
    )
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build.shortlist_attention_symbols_1d",
        lambda *args, **kwargs: ["AAPL", "TLT", "USO"],
    )
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build.build_attention_knowledge_graph_proposals",
        lambda *args, **kwargs: pd.DataFrame(),
    )
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build.build_bottom_up_attention_artifacts",
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
                "homepage_graph": {
                    "figure": {"data": [], "layout": {"height": 320, "showlegend": False}},
                    "summary": {"connected_components": 1},
                },
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
        "pipeline.jobs.attention_home_build.collect_attention_ticker_symbols",
        lambda *args, **kwargs: ["AAPL"],
    )
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build.build_attention_ticker_snapshot_frame",
        lambda *args, **kwargs: pd.DataFrame(
            [{"symbol": "AAPL", "company_name": "Apple Inc.", "market_cap_label": "$1.00T", "run_id": ctx.run_id}]
        ),
    )
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build.build_attention_ticker_background_snapshot_frame",
        lambda *args, **kwargs: pd.DataFrame(
            [{"symbol": "AAPL", "company_name": "Apple Inc.", "description_text": "Apple builds consumer devices.", "run_id": ctx.run_id}]
        ),
    )
    monkeypatch.setattr(
        "pipeline.jobs.main._persist_dataset",
        lambda dataset_name, frame, ctx, conn: persisted.setdefault(dataset_name, frame.copy()),
    )

    run_attention_home(ctx, None)

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
    assert json.loads(persisted["attention_home_1d"].iloc[0]["homepage_graph_json"])["figure"]["layout"]["height"] == 320


def test_zopedia_market_summary_enrichment_carries_its_own_audio(monkeypatch):
    payload = {"top_events": [{"event_title": "Rates connect equities", "supporting_symbols": []}]}
    agent_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build.build_market_stories",
        lambda payload: [{"sentence": "Rates connect equities", "symbols": [], "kind": "event"}],
    )

    def _fake_agent(**kwargs):
        agent_calls.append(kwargs)
        return {
            "status": "completed",
            "answer_markdown": "### Theme\nRates are the dominant cross-asset signal today.",
            "confidence": "medium",
            "limitations": [],
            "tool_calls": [{"tool_name": "dataset.yield_curve_summary", "status": "ok"}],
            "quality_review": {"approved": True},
            "model": "test-model",
        }

    monkeypatch.setattr("pipeline.jobs.attention_home_build.run_aql_zopedia_agent", _fake_agent)

    def _fake_structured_agent(**kwargs):
        agent_calls.append(kwargs)
        schema_name = str(kwargs.get("schema_name") or "")
        if schema_name.endswith("_review"):
            return {
                "status": "completed",
                "payload": {
                    "accepted": True,
                    "issues": [],
                    "revision_instruction": "",
                    "confidence": "medium",
                },
            }
        return {
            "status": "completed",
            "payload": {
                "answer_markdown": "### Theme\nRates are the dominant cross-asset signal today.",
                "confidence": "medium",
                "limitations": [],
            },
            "agent_result": {"status": "completed"},
        }

    monkeypatch.setattr("pipeline.jobs.attention_home_build.run_aql_zopedia_structured_agent", _fake_structured_agent)
    monkeypatch.setattr(
        "pipeline.jobs.attention_home_build.attach_attention_home_summary_audio",
        lambda summary_payload: {
            **dict(summary_payload),
            "audio_base64": "YXVkaW8=",
            "audio_text_hash": "hash-123",
            "audio_mime_type": "audio/mpeg",
            "voice_id": "voice-123",
        },
    )

    frame = _build_zopedia_enrichment_frame(
        payload,
        asof_time_utc=datetime(2026, 5, 23, tzinfo=timezone.utc),
        run_id="home-zopedia-summary-test",
    )

    summary_row = frame[frame["symbol"] == "__MARKET_SUMMARY__"].iloc[0].to_dict()
    assert summary_row["status"] == "completed"
    assert summary_row["audio_base64"] == "YXVkaW8="
    assert summary_row["audio_mime_type"] == "audio/mpeg"
    assert "###" not in summary_row["audio_text"]
    assert agent_calls[0]["surface"] == "attention_home.zopedia_market_summary"


def test_run_attention_home_fails_when_required_mover_inputs_are_missing(monkeypatch):
    progress_calls: list[dict[str, object]] = []
    ctx = JobContext(
        name="attention-home-build",
        run_id="attention-missing-inputs",
        asof=datetime(2026, 4, 13, 16, 20, tzinfo=timezone.utc),
        universe_version="20260413",
    )

    monkeypatch.setattr("pipeline.jobs.main._load_latest_materialized_frame", lambda dataset_name: pd.DataFrame())
    monkeypatch.setattr("pipeline.jobs.attention_home_build.load_aql_zopedia_llm_client", lambda **kwargs: None)
    monkeypatch.setattr(
        "pipeline.jobs.main._job_progress",
        lambda *args, **kwargs: progress_calls.append(
            {
                "stage": kwargs.get("stage"),
                "message": kwargs.get("message"),
                "status": kwargs.get("status"),
            }
        ),
    )
    monkeypatch.setattr(
        "pipeline.jobs.main._persist_dataset",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("attention outputs should not persist")),
    )

    try:
        run_attention_home(ctx, None)
        raised = False
    except AttentionHomeBuildError as exc:
        raised = True
        assert "daily_movers and macro_anchor_daily_movers were unavailable" in str(exc)

    assert raised
    assert progress_calls[-1]["stage"] == "failed"
    assert progress_calls[-1]["status"] == "Failed"


def test_build_portfolio_timeseries_snapshot_uses_shared_portfolio_builder(monkeypatch):
    expected = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-03-30T00:00:00Z"], utc=True),
            "portfolio": [100.0],
            "SPY": [101.0],
        }
    )

    monkeypatch.setattr("pipeline.jobs.main.build_portfolio_timeseries", lambda api, period: expected.copy())

    out = _build_portfolio_timeseries_snapshot(object(), period="5Y")

    assert out.equals(expected)
