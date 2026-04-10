# Commodity Preload Root Cause Fix

Date: 2026-04-09

## Problem

The `Commodity Section` can fall back to materialized datasets. Those datasets are built by `run_commodities(...)`.

The preload job was not using the same symbol model as the UI:

- UI commodity view uses curated commodity proxy ETFs/ETNs from `COMMODITY_FOCUS_UNIVERSES`
- pipeline preload used `default_commodity_universe_symbols()`, which is taxonomy-derived
- the preload then reused that taxonomy-derived list as both the study universe and the commodity reference basket

That mismatch can leave `commodity_regime_summary` / `commodity_regime_history` empty or misaligned with the UI, which then triggers:

`Not enough commodity history was returned to build this section.`

## Fix

Align the preload job with the UI contract:

1. Add a shared helper for the default broad commodity proxy universe.
2. Use the broad commodity proxy universe for the preload symbol set.
3. Keep the curated commodity reference basket separate from the broader proxy universe.
4. Add regression tests that assert the preload job calls `scan_commodity_regimes(...)` with the UI-aligned symbol sets.

Also fix the materialized-first momentum resolver:

1. If explicit symbols are requested and the filtered materialized `momentum_profiles` slice is empty, fall back to on-demand fetches when live reads are allowed.
2. Keep materialized-only / snapshot mode behavior unchanged.

## Expected outcome

- `commodity_regime_summary` and `commodity_regime_history` are generated from the same proxy universe the UI expects.
- Explicit commodity proxy requests no longer get stuck behind an empty filtered `momentum_profiles` snapshot when live fallback is available.
- Snapshot mode and materialized-first reads can render the commodity section consistently.
- The dependency graph/Sankey can appear once the commodity datasets are repopulated.
