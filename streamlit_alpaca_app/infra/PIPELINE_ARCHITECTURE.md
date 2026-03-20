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
  - Datasets: `daily_movers`, `momentum_profiles`, `price_history`
  - Dashboard: Market Opportunity, Technical Strategizer, Option Strategizer (spot reference)

- FRED
  - Job: `macro-fred-daily`
  - Datasets: `fred_summary`, `fred_observations`
  - Dashboard: FRED Macro

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
  - Datasets: `news_articles`, `news_symbol_map`
  - Dashboard: Market Opportunity ticker context

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
- Fundamentals
- Derivatives

These controls are operational triggers, not data readers. Readers still use snapshot-first logic.

## Notes

- Option snapshot datasets now back expiration discovery, quote tables, and scenario surface inputs.
- Portfolio/account/positions/timeseries remain live-backed until dedicated pipeline datasets are introduced.
