# Pipeline Redesign (Blob + Postgres Index) with Backtesting Support

## Goals

1. Eliminate dashboard cold-load stalls by precomputing and preloading datasets.
2. Maximize market coverage (top-by-market-cap universe) while respecting provider/API politeness.
3. Support reproducible backtesting with time-versioned data and deterministic replay.
4. Keep operating cost reasonable by separating heavy analytical payloads from metadata/query indexing.

## Recommended Architecture

### Storage Pattern (Hybrid)

- **Primary analytical store**: Azure Blob Storage (Parquet).
- **Metadata/index store**: Azure Database for PostgreSQL Flexible Server.

Why this split:
- Blob is best for large historical bars/options/derived frames.
- Postgres is best for dataset discovery, freshness checks, job status, and backtest run metadata.

## Data Domains and Refresh Policy

### 1) Equities (Top-by-market-cap universe)

- Universe source: daily top-N market-cap snapshot.
- Intraday refresh cadence: **4 times during trading hours** (example ET):
  - 09:35
  - 11:30
  - 13:30
  - 15:45
- Tiering for scale:
  - **Tier 1** (largest symbols): full refresh each intraday run.
  - **Tier 2** (next tranche): daily refresh + selective intraday deltas.

Datasets refreshed:
- Daily movers
- Momentum profiles
- Price history increments
- Asset metadata deltas
- News deltas

### 2) FRED Macro

- Refresh: **once daily**, early morning ET.
- Update mode: release/series incremental where possible; avoid full re-download when unchanged.

### 3) Commodities / Regime Scans

- Minimum: once daily.
- Preferred: 2-4 times/day depending on API budget and observed latency.

### 4) Options

- Refresh a liquid subset only (e.g., top symbols by liquidity/open interest) to control cost.
- Expiration chain metadata can run more often than full snapshot/surface calculations.

### 5) News (First-class dataset)

- Refresh cadence: at least daily; preferred aligned with equities intraday runs (**4x/day**) with strict rate limits.
- Storage split:
   - **Blob/Parquet** for article snapshots and symbol-news joins (append-only, partitioned by publish date and as-of).
   - **Postgres index** for fast dedupe and lookup (url hash/article id, symbol, source, published time, sentiment tags).
- Backtesting rule: use only articles with `published_at <= simulation_time` and (optionally stricter) `ingested_at_utc <= simulation_time`.
- Keep raw article text immutable; downstream NLP/sentiment features are separate versioned datasets referencing source article ids.

## Backtesting-Critical Design

Backtesting requires more than cache prewarming; it requires immutable, queryable historical states.

### Dataset Versioning Rules

Every produced dataset artifact must include:
- `dataset_name`
- `universe_version`
- `parameter_hash`
- `asof_time_utc` (market knowledge time)
- `ingested_at_utc`
- `source_window_start_utc`
- `source_window_end_utc`
- `code_version` (git SHA or build id)
- `schema_version`

### Time Semantics

Use two-time semantics where relevant:
- **Valid time**: when market fact is true (`asof_time_utc`).
- **System time**: when pipeline recorded it (`ingested_at_utc`).

Backtests should read data using a strict rule:
- `asof_time_utc <= simulation_time`
- optionally also enforce `ingested_at_utc <= simulation_time` to prevent accidental lookahead from delayed ingestion.

### Immutability Policy

- Raw and curated backtest artifacts are **append-only**.
- Corrections create new versions; never overwrite a historical artifact in place.

### Reproducibility Contract

A backtest run stores:
- universe version
- strategy parameters
- dataset manifests used (exact artifact IDs/paths)
- code version
- execution timestamp and environment metadata

Any run can be replayed from this manifest without ambiguity.

## Blob Layout

Recommended prefix structure:

`blob://<container>/datasets/<dataset_name>/dt=<YYYY-MM-DD>/asof=<YYYY-MM-DDTHH-mm-ssZ>/universe=<universe_version>/part-*.parquet`

Companion manifest object per dataset version:

`blob://<container>/manifests/<dataset_name>/<dataset_version_id>.json`

Manifest includes row count, min/max timestamps, checksum, schema version, and upstream dependency versions.

News-specific parquet examples:
- `datasets/news_articles/dt=<YYYY-MM-DD>/asof=<...>/universe=<...>/part-*.parquet`
- `datasets/news_symbol_map/dt=<YYYY-MM-DD>/asof=<...>/universe=<...>/part-*.parquet`
- `datasets/news_features/dt=<YYYY-MM-DD>/asof=<...>/universe=<...>/part-*.parquet`

## PostgreSQL Metadata Schema (Minimum)

### `dataset_versions`

- `dataset_version_id` (PK)
- `dataset_name`
- `universe_version`
- `parameter_hash`
- `asof_time_utc`
- `ingested_at_utc`
- `blob_path`
- `row_count`
- `checksum`
- `code_version`
- `schema_version`
- `status`

Indexes:
- `(dataset_name, asof_time_utc desc)`
- `(dataset_name, universe_version, parameter_hash, asof_time_utc desc)`

### `job_runs`

- run id, job name, schedule slot, start/end times, status, retries, error summary

### `backtest_runs`

- run id, strategy id, simulation interval, universe version, code version, created_at

### `backtest_run_inputs`

- `backtest_run_id`, `dataset_version_id`, role (features/signals/labels/reference)

### `news_article_index` (recommended)

- `article_uid` (PK; provider id or normalized URL hash)
- `published_at`
- `ingested_at_utc`
- `source`
- `symbols` (array/jsonb)
- `headline_hash`
- `dataset_version_id` (FK to `dataset_versions`)

Indexes:
- `(published_at desc)`
- `(source, published_at desc)`
- `GIN(symbols)`

## Azure Container Apps Jobs Topology

### Jobs

1. **universe-builder** (daily premarket)
   - Builds and publishes market-cap ranked universe versions.

2. **equities-intraday-preload** (4x/day)
   - Refreshes Tier 1 fully, Tier 2 selectively.

3. **macro-fred-daily** (daily)
   - Updates macro datasets and publishes manifests.

4. **commodities-regime** (1-4x/day)
   - Computes commodity/regime derivatives.

5. **options-liquid-universe** (intraday/daily mixed)
   - Refreshes expirations and selective contracts/surfaces.

6. **news-ingest-and-features** (intraday/daily mixed)
   - Pulls provider news deltas, deduplicates, writes immutable article parquet, and publishes optional derived sentiment/topic features.

7. **entity-taxonomy-refresh** (monthly)
   - Refreshes NASDAQ/NYSE listings, classifies the full active universe dynamically, and publishes `entity_taxonomy_labels`.

### Runtime Controls (API Politeness)

- Per-provider request budgets (token bucket).
- Endpoint-specific concurrency limits.
- Jittered starts to avoid bursty fan-out.
- Exponential backoff with max retry cap.
- Circuit-breaker behavior on repeated 429/5xx.
- Incremental windows by default; full rebuild only by explicit backfill job.

## Dashboard Read Path

At runtime, dashboard reads from Postgres manifest first:
1. Resolve latest eligible dataset version for section/params.
2. Load parquet from blob.
3. If missing/stale, degrade gracefully and optionally trigger async refresh.

This keeps UI responsive and removes first-load black-screen behavior caused by on-demand heavy computation.

## Backfill and Historical Reprocessing

Add a dedicated **backfill job** (manual/scheduled) with bounded windows:
- Inputs: dataset name, date range, universe version, parameter set.
- Writes new immutable versions.
- Does not overwrite existing historical artifacts.

## Phased Delivery

### Phase 1 (Immediate)

- Implement hybrid storage contract.
- Add manifests + dataset version indexing.
- Preload current dashboard datasets on schedule.

### Phase 2

- Add full backtest run metadata and replay tooling.
- Add policy-driven data retention and compaction.

### Phase 3

- Add scenario-based selective precomputation (high-demand symbols/params) and adaptive scheduling.

## Success Metrics

- Home/section initial paint latency reduced to target (<2-3s for warmed sections).
- Cache hit rate per section >90% during market hours.
- Zero lookahead violations in backtesting validation checks.
- Reproducible replay success for sampled historical runs.
