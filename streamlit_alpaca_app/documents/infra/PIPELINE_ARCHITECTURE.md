# Pipeline Architecture (Data -> Pipelines -> Dashboard)

This document defines the canonical architecture for cached dashboard reads.

## Principles

- Dashboard reads **pipeline snapshots first** (Postgres metadata + Blob parquet).
- Live API calls are **fallback only** when pipeline data is unavailable or force-refresh is requested.
- Ingestion and derivation run in Azure Container App Jobs on schedule.
- `dataset_versions` in Postgres is the source of truth for latest snapshot discovery.

## Flow

1. Source adapters fetch data in `pipeline/jobs/main.py`.
2. Each dataset is written to Blob parquet and registered in Postgres `dataset_versions`.
3. Dashboard loaders in `app.py` call `services/pipeline_store.py` to resolve latest dataset and load parquet.
4. If dataset is missing/unreadable, loader falls back to existing cache/live logic.

## Source-to-Job-to-Dataset Mapping

- Equities
  - Job: `equities-intraday-preload`
  - Datasets: `daily_movers`, `positions_snapshot`, `portfolio_timeseries_snapshot`, `momentum_profiles`, `price_history`
  - Dashboard: Market Opportunity, Portfolio Overview holdings/history blocks, Performance, Technical Strategizer, Option Strategizer (spot reference)

- FRED
  - Job: `macro-fred-daily`
  - Datasets: `fred_summary`, `fred_observations`, `fred_series_index`, `fred_release_index`
  - Dashboard: FRED Macro
  - Failure semantics: the job is marked `Failed` if FRED preload fails (or key is missing), even if Treasury yield datasets persist successfully.

- Commodities
  - Job: `commodities-regime`
  - Datasets: `commodity_regime_summary`, `commodity_regime_history`
  - Dashboard: Market Opportunity (Commodity)

- Options
  - Job: `options-liquid-universe`
  - Datasets: `option_expirations`, `option_contract_snapshots`
  - Dashboard: Option Strategizer expirations, quotes, Greeks surface, scenario selector input surface

- News
  - Job: `news-ingest-and-features`
  - Datasets: `news_articles`, `news_symbol_map`, `edgar_filings`, `edgar_evidence`, `attention_context_llm`, `attention_context_bundle`
  - Dashboard: Market Opportunity ticker context, attention evidence/context inputs

- Attention
  - Job: `attention-home-build`
  - Datasets: `attention_home_1d`, `attention_research_bundles`, `attention_candidates_1d`, `attention_claims`, `attention_event_clusters_1d`, `attention_ticker_snapshots_1d`, `attention_ticker_background_snapshots`, `attention_web_search_news`
  - Dashboard: Home, Daily Tape, attention drilldowns, attention graph views

- Taxonomy
  - Job: `entity-taxonomy-refresh`
  - Datasets: `us_equity_listings`, `entity_taxonomy_labels`
  - Dashboard: Attention entity labeling, sector/industry/peer-group lookup
  - Detailed flow: `documents/infra/TAXONOMY_PIPELINE_FLOW.md`

- Fundamentals
  - Job: `equities-intraday-preload`
  - Datasets: `quarterly_fundamentals`
  - Dashboard: Fundamental Strategizer, Market Opportunity fundamentals block

- Derivatives
  - Job: `equities-intraday-preload`
  - Datasets: `correlation_phase_shift_summary`, `correlation_phase_shift_history`, `technical_signals_latest`, `technical_signal_history`, `peer_group_membership`, `price_expectations`, `anomaly_events`, `attention_rollups`, `attention_feed`
  - Dashboard: Market Opportunity (Broad Markets advanced), Technical Strategizer metrics, future Home attention feed

## Force Refresh Controls

Sidebar source buttons trigger pipeline jobs directly using Azure CLI via `start_source_refresh_job`.

- Equities
- FRED
- Commodities
- Options
- News
- Attention
- Fundamentals
- Derivatives

These controls are operational triggers, not data readers. Readers still use snapshot-first logic.

## Notes

- Option snapshot datasets now back expiration discovery, quote tables, and scenario surface inputs.
- Account remains live-backed.
- Portfolio positions, portfolio history, and holding momentum are snapshot-first via `positions_snapshot`, `portfolio_timeseries_snapshot`, and `momentum_profiles`.
