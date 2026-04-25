# Data Pipeline Architecture

This folder holds durable architecture for materialized datasets, scheduled jobs, taxonomy, macro sources, cache policy, and pipeline identity.

Boundary rule: pipelines orchestrate ingestion and materialization through public module APIs. They may call producers, but should keep dataset persistence and job status separate from UI and agent behavior.

## Docs

- `PIPELINE_ARCHITECTURE.md`: Azure pipeline job architecture and materialized dataset flow
- `TAXONOMY_PIPELINE_FLOW.md`: taxonomy refresh flow chart and setup notes
- `ATTENTION_FRED_INTEGRATION_PLAN_2026-04-06.md`: phased FRED integration for Attention scoring
- `FRED_MACRO_PM_COVERAGE_2026-04-15.md`: curated FRED dashboard expansion
- `FRED_V1_FALLBACK_2026-04-14.md`: FRED v2/v1 fallback behavior
- `BROAD_ECONOMY_FRED_SOURCE_UI_FIX_2026-04-19.md`: Broad Economy source and layout fixes
- `PIPELINE_CACHE_GUARDRAILS_2026-04-14.md`: local pipeline-store cache bounds and ignore policy
- `PIPELINE_IDENTITY_AND_FRED_RETRY_FIX_2026-04-13.md`: attention job identity and FRED retry hardening
- `COMMODITY_PRELOAD_ROOT_CAUSE_FIX_2026-04-09.md`: commodity preload recovery and alignment plan
