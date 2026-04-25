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
  - Dashboard: Home, Daily Market Overview, attention drilldowns, attention graph views

- Taxonomy
  - Job: `entity-taxonomy-refresh`
  - Datasets: `us_equity_listings`, `entity_taxonomy_labels`
  - Dashboard: Attention entity labeling, sector/industry/peer-group lookup
  - Detailed flow: `documents/architecture/data_pipelines/TAXONOMY_PIPELINE_FLOW.md`

- Fundamentals
  - Job: `fundamentals-quarterly-refresh` (standalone; previously embedded in `equities-intraday-preload`)
  - Datasets: `quarterly_fundamentals`
  - Source: SimFin API (requires `SIMFIN_API_KEY` in Key Vault as secret `SimFinAPI`; falls back to bundled CSV files when unavailable)
  - Schedule: weekdays at 12:00 UTC (configurable via `FUNDAMENTALS_QUARTERLY_REFRESH_CRON`)
  - Dashboard: Fundamental Strategizer, Market Opportunity fundamentals block
  - Staleness: UI shows a warning above fundamentals charts when the most recent report date is >150 days old

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

## Historical Replay

Every dataset version is retained in Postgres (`dataset_versions`) and Blob Storage.
The `dataset_versions` table records `asof_time_utc` per version, so any past day's
snapshot can be retrieved without re-running the pipeline or calling the LLM.

### How it works

- `pipeline_store.dataset_metadata_asof(dataset_name, target_date)` — returns the
  newest `ready` version whose `asof_time_utc` falls on or before `target_date`.
- `pipeline_store.load_dataset_frame_asof(dataset_name, target_date)` — loads the
  blob parquet for that version (with local cache).
- `data_access.layer.resolve_homepage_asof(target_date)` — loads a complete homepage
  from stored outputs.  No LLM calls, no live API calls.

### Homepage replay dependencies

A full historical homepage requires four datasets, all produced in the same
`attention-home-build` job run:

| Dataset | Contains |
|---|---|
| `attention_home_1d` | Summary text, audio text, hypothesis, graph figure JSON, top events, must-read movers, unresolved moves, taxonomy trends, entity master, coverage summary |
| `attention_ticker_snapshots_1d` | Per-symbol sparkline chart (data URI), company name, market cap label |
| `attention_research_bundles` | Detailed per-event research (loaded on click in the UI) |
| `attention_ticker_background_snapshots` | Deeper ticker profiles with news context |

All narrative content, charts, and the relationship graph are **precomputed and
stored** — the replay reads them directly.  The only thing missing from a
historical view vs. the live view is that ticker snapshot profiles won't fall back
to the live Alpaca API for symbols not in the stored snapshot.

### UI access

The homepage includes a date picker (top right). Selecting a past date loads
that day's stored snapshot instead of the latest.

### API access

```
POST /v1/dataset/homepage_replay
{"target_date": "2026-04-20"}
```

Returns `home_payload`, `ticker_snapshots`, and `dataset_metadata` with provenance
(version IDs and asof timestamps for each loaded dataset).

## Notes

- Option snapshot datasets now back expiration discovery, quote tables, and scenario surface inputs.
- Account remains live-backed.
- Portfolio positions, portfolio history, and holding momentum are snapshot-first via `positions_snapshot`, `portfolio_timeseries_snapshot`, and `momentum_profiles`.
