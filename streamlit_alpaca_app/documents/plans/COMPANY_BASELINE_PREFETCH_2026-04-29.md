# Company Baseline Prefetch

Date: 2026-04-29

## Problem

Stock Investigator context could show generic coverage metadata like "6 recent articles over roughly 4 days; tone is mixed" in both Background and What Happened. That text explains article count, but it does not explain what the company does or why it matters.

## Design

- Add a slow-changing `company_baselines` materialized dataset.
- Build it from the ranked equity universe, company display names, taxonomy labels, existing role hints, and bounded Wikipedia summaries.
- Keep the daily attention/news jobs focused on current catalysts.
- Let `resolve_attention_ticker_background` merge the baseline into ticker background results when attention text is missing or low signal.
- Schedule `company-baseline-prefetch` monthly, beside `entity-taxonomy-refresh`, with a small default cap of 50 tickers until cost/runtime is measured.
- Send an identifiable Wikipedia User-Agent through the company helper and wait `COMPANY_BASELINE_REQUEST_DELAY_SECONDS` between baseline lookups. Default delay is 0.5 seconds, configurable upward if the ticker cap grows.
- Resolve company names conservatively: try the official display name first, then a cleaned legal-suffix fallback such as `Vertiv Holdings` when `Vertiv Holdings, LLC` does not resolve.

## Implementation Notes

- Service: `services/company_baseline.py`
- Pipeline job: `PIPELINE_JOB_NAME=company-baseline-prefetch`
- Dataset: `company_baselines`
- UI/data access: Stock Investigator reads the dataset through the existing attention ticker background resolver.

## Validation

- Unit tests cover baseline frame creation, deserialization, data-access fallback, low-signal replacement, and job persistence.
- Local smoke sample for `VRT`, `NVDA`, and `CR` returned concrete baseline company descriptions.
