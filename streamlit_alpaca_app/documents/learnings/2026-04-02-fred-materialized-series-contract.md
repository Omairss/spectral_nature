# FRED Materialized Series Contract

## Problem

The FRED page assumed `dashboard["series_index"]` had the same shape regardless of source. That was true for live API loads, but false for the materialized pipeline path.

- Live `load_fred_dashboard()` returned `series_index` rows with `release_name`, `frequency`, `units`, and `notes`.
- The `macro-fred-daily` job only persisted `fred_summary` and `fred_observations`.
- `build_fred_dashboard_from_pipeline()` reconstructed a thinner `series_index` from `fred_summary`, so `release_name` was missing.
- The Streamlit explorer then indexed `series_index["release_name"]` directly and raised `KeyError`.

## Fix

We aligned the materialized contract with the live contract instead of masking the symptom.

1. `macro-fred-daily` now also persists `fred_series_index` and `fred_release_index`.
2. `DataAccessLayer.resolve_fred_dashboard()` loads those richer indexes when they exist.
3. `build_fred_dashboard_from_pipeline()` accepts optional materialized indexes and synthesizes the missing explorer columns when older snapshots are still in use.
4. The Streamlit FRED explorer now normalizes required columns before filtering so pre-fix snapshots do not crash the page.

## Operational Note

Old materialized snapshots can still be read safely after the UI patch, but the next FRED pipeline refresh should populate `fred_series_index` and `fred_release_index` so release filtering and metadata parity are restored without fallback synthesis.
