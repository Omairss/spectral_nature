# Broad Economy FRED Source + UI Fix

## Goal

Fix the Broad Economy page at the source, then clean up the page order so the most useful macro view leads the section.

## Problems

- `YoY` showed `n/a` across the Broad Economy snapshot even when enough history existed.
- High-frequency series such as `WALCL`, `DGS10`, and `MORTGAGE30US` were stale in the materialized snapshot because the job kept trusting the bulk path.
- The page order buried the category tabs below the snapshot and left the explorer chart floating between major sections.
- Stationarized change was optional behind a checkbox even though it is the more useful default view for macro regime work.

## Source Fix

### FRED summary consistency

- Normalize FRED frequency labels such as `Monthly` and `Weekly` into canonical short codes such as `M` and `W`.
- Rebuild derived summary fields from the raw observation frame when loading materialized pipeline datasets instead of trusting stale precomputed `fred_summary` rows.
- This restores:
  - correct `YoY`
  - correct latest observation date
  - consistent frequency handling across materialized and on-demand paths

### FRED fetch-path default

- Default the runtime to `FRED_BULK_MODE=v1_only`.
- Keep the bulk path available only as an explicit opt-in instead of the default.
- Also push the same default through the Azure pipeline deploy script so the jobs stay aligned with the source code.

## UI Changes

- Remove the stationarized overlay checkbox.
- Turn stationarized change on by default for Broad Economy charts.
- Lead the page with `M2` as the top chart.
- Move the category tabs near the top of the page.
- Move the free-floating explorer graph into a dedicated `Series Explorer` tab.
- Push the `Indicator Snapshot` table below the category tabs.

## Expected Outcome

- Broad Economy starts with liquidity first, then category tabs, then the summary table.
- `YoY` is no longer `n/a` just because the stored frequency label used full words.
- Fresh pipeline runs use the stable per-series FRED path by default, so high-frequency series stop lagging behind live FRED observations.

## Validation

- Local live FRED load after the source fix returned current high-frequency dates:
  - `WALCL` `2026-04-15`
  - `DGS10` `2026-04-16`
  - `T10YIE` `2026-04-17`
- The rebuilt materialized summary after rerunning `macro-fred-daily` landed as:
  - `fred_summary__20260419T073945Z__cf735ce9`
  - `fred_observations__20260419T073945Z__cf735ce9`
- The refreshed stored summary now has normalized frequency codes and non-null `YoY` for the curated set again.
- Dev pipeline deploy also picked up a deploy-script reliability fix:
  - optional donor env lookups no longer abort the script under `set -e`
