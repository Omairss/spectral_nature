# FRED v1 Fallback Fix

## Goal

Keep the macro dashboard and `macro-fred-daily` usable even when the FRED v2 bulk release endpoint is unavailable or the key is not registered for v2.

## Root Cause

- The curated FRED pipeline depended on `fred/v2/release/observations` as the only path for fresh observations.
- The live `Fred` key in `spectral-nature-kvault` works on stable v1 series endpoints but is rejected by the v2 bulk endpoint with `401` and `The credentials are not registered.`
- The fallback path did not exist, so a v2-only failure left `fred_summary` stale even though the same series could still be loaded through v1.

## Source Fix

### Dual-path loader

- `services/fred.py` now separates:
  - v2 bulk release loading
  - v1 curated per-series loading
- Default mode is `prefer`:
  1. try v2 bulk
  2. if bulk fails, fall back to v1 per-series requests
- Optional env override:
  - `FRED_BULK_MODE=prefer` default
  - `FRED_BULK_MODE=v1_only`
  - `FRED_BULK_MODE=require`

### Shared payload contract

- Both paths now produce the same payload shape:
  - `summary`
  - `series_data`
  - `metadata`
  - `series_index`
  - `observations`
  - `release_index`
- That keeps the pipeline job, UI dashboard, and downstream data-access layer unchanged.

### Curated series expansion

- Added high-signal series so the v1 fallback remains worth using on its own:
  - `JTSJOL`
  - `CIVPART`
  - `ICSA`
  - `INDPRO`
  - `RSAFS`
  - `BAMLC0A4CBBB`
  - `FEDFUNDS`
  - `WALCL`

## Validation

- Added regression coverage for:
  - v2 bulk failure falling back to v1
  - expanded curated series coverage
- Validation should confirm that:
  - `fred_summary` refreshes even when v2 returns `401`
  - daily and weekly series such as `BAMLH0A0HYM2`, `MORTGAGE30US`, `ICSA`, and `WALCL` carry fresh dates into the materialized snapshot

## Rollout

1. Deploy pipeline jobs to dev.
2. Trigger `macro-fred-daily`.
3. Verify:
   - fresh `fred_summary`, `fred_observations`, `fred_series_index`, `fred_release_index`
   - max `latest_date` in `fred_summary` moves past `2026-03-01`
   - the job succeeds even while direct v2 bulk auth still fails
