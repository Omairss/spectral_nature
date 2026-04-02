# Portfolio Live Mode Fix

## Problem

Portfolio and account surfaces could render synthetic zero values even when the live brokerage connection was healthy.

Two source issues caused that behavior:

1. `APP_PRESENTATION_LAYER_ONLY` defaulted to enabled when unset, so deployed environments silently ran in snapshot mode.
2. The section gate treated snapshot availability as sufficient even for pages that explicitly require live account data.

## Fix

1. Centralize runtime mode decisions in `services/runtime_policy.py`.
2. Make presentation-only mode an explicit opt-in instead of the default.
3. Require live data for portfolio/account pages unless a section explicitly allows pipeline fallback.
4. Prevent the sidebar from attempting to load account metrics while the app is in snapshot mode.
5. Restore the intended hybrid contract:
   - account stays live
   - positions read `positions_snapshot` first
   - portfolio history reads `portfolio_timeseries_snapshot` first
   - holdings momentum reads `momentum_profiles` first
6. Materialize `portfolio_timeseries_snapshot` in the shared equities job so the performance pages stop rebuilding history on request.
7. Treat empty account-linked snapshots as missing and fall back to the cached live resolver instead of blanking the UI.
8. Cache pipeline metadata and versioned snapshot payloads locally so snapshot-first pages do not re-query manifests and re-download parquet blobs on every rerun.
9. Keep Home narrative ticker cards snapshot-only so a missing supporting-symbol mini-card never triggers slow live price/metadata fallback during homepage render.

## Expected Outcome

- Account status and buying power remain live.
- Portfolio and performance pages avoid repeated live positions/history calls in normal operation.
- Snapshot-first pages also avoid repeated remote blob fetches in normal operation after the container warms its local pipeline cache.
- Home v2 remains snapshot-driven even when supporting ticker cards are missing from the precomputed ticker snapshot map.
- Snapshot-capable market pages still work from materialized datasets when requested.
- Snapshot-only environments fail clearly for live-only account surfaces instead of showing fabricated zero balances.
