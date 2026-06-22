# Homepage Runtime Contract Learning

Date: 2026-04-02

## Summary

The homepage slowdown was not primarily an optimization problem. It was a runtime-contract regression.

We changed the app from implicit presentation-only behavior to hybrid behavior, and dev was also configured to force refresh by default. That combination caused Home to bypass the precomputed attention snapshots and compute the homepage path again.

## What Changed

1. `APP_PRESENTATION_LAYER_ONLY` stopped defaulting to enabled when unset.
2. `APP_FORCE_DATA_REFRESH_DEFAULT` was `true` in the dev container app.
3. The Home renderer passed `force_data_refresh` into `_load_attention_home_1d_cached(...)`.
4. When `force_refresh=True`, the data-access layer skips the pipeline snapshot path and falls back to the on-demand attention build path.

## Why Home Became Slow

Home was expected to read from the materialized attention datasets:

- `attention_home_snapshots_1d`
- `attention_bundle_snapshots`
- `attention_ticker_snapshots_1d`

Instead, dev was effectively doing this:

1. force refresh enabled by default
2. pipeline reads bypassed
3. `resolve_attention_home_1d(force_refresh=True)` executed
4. `_resolve_live_attention_artifacts(...)` rebuilt the narrative inputs on demand

That is a fundamentally different behavior than "read the latest precomputed view."

## Key Evidence

- `APP_PRESENTATION_LAYER_ONLY` was unset in both dev and prod.
- `APP_FORCE_DATA_REFRESH_DEFAULT=true` in dev.
- `APP_FORCE_DATA_REFRESH_DEFAULT=false` in prod.
- `presentation_layer_only_enabled(...)` now returns `False` when the env var is unset.
- `resolve_attention_home_1d(...)` only uses the materialized path when `force_refresh=False`.

## Correct Mental Model

The intended contract is:

- account/account status can be live
- homepage, attention, market context, research bundles, and ticker snapshots should read precomputed/materialized views by default
- refresh buttons should trigger upstream jobs, not silently force the UI onto expensive on-demand rebuild paths

If we need hybrid behavior for some surfaces, it must be explicit and scoped. It should not happen because an env var is unset or because a global force-refresh default is enabled.

## Operational Guardrails

1. Treat homepage regressions as contract regressions first, performance regressions second.
2. Always inspect deployed env vars before changing code paths:
   - `APP_PRESENTATION_LAYER_ONLY`
   - `APP_FORCE_DATA_REFRESH_DEFAULT`
3. For snapshot-first pages, `force_refresh=True` should be used carefully because it changes semantics, not just speed.
4. Keep Home symbol cards snapshot-only unless there is an explicit user action to fetch richer live detail.
5. When diagnosing slowness, first answer:
   - Are we reading a materialized dataset?
   - Or did we accidentally trigger the on-demand builder?
6. Do not let routine dev deploys rewrite this contract. `scripts/deploy_ui_azure.sh` must keep `APP_FORCE_DATA_REFRESH_DEFAULT=false` by default, with overrides reserved for targeted debugging only.

## Immediate Fix Applied

Dev was corrected by setting:

- `APP_FORCE_DATA_REFRESH_DEFAULT=false`

That restored the expected default behavior: Home reads the precomputed attention views instead of recomputing them during page load.

## Follow-up Guardrail

The recurrence mechanism was operational, not just conceptual: `scripts/deploy_ui_azure.sh` was writing `APP_FORCE_DATA_REFRESH_DEFAULT=true` on every dev deploy.

That script now pins `APP_FORCE_DATA_REFRESH_DEFAULT=false` for normal rollouts in both environments. If a future task needs to test the on-demand path explicitly, it must do so with a one-off override:

- `APP_FORCE_DATA_REFRESH_DEFAULT_OVERRIDE=true ./scripts/deploy_ui_azure.sh --target dev ...`

That override should be treated as temporary debugging state, not the default runtime contract.
