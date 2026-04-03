# Mistakes Log

## 2026-04-03 - Dashboard End-State Mismatch

### What went wrong
- I treated passing service-level tests as sufficient, but the actual dashboard end state still showed fallback text (`No relevant catalyst found ...`) for real symbols.
- I removed overview-panel fundamental charts during cleanup when the request was to simplify narrative sections, not remove analytical visuals.
- I validated providers in one vault path but did not first guarantee the running dev app vault had all required secrets (`serpapi-api-key`, `tavily-api-key`, `azure-openai-api-key`).

### Impact
- User saw unchanged/noisy outcomes in the real UI despite backend changes.
- Confidence dropped because test results did not reflect production-like behavior.

### Never repeat checklist (mandatory)
1. Validate through the same query path the dashboard uses (`attention_ticker_background`, `attention_research_bundle`) with `force_refresh=true`.
2. Confirm target app environment secrets in the exact Key Vault used by that app before declaring search/LLM fixes done.
3. Add/maintain end-state assertions in tests for final payload fields consumed by UI:
   - `description_text`
   - `news_summary_lines`
   - `recent_headlines`
4. Do not remove unrelated UI sections (e.g., fundamental charts) unless explicitly requested.
5. After deploy, verify one known problematic ticker end-to-end and record the output in deploy notes.

## 2026-04-03 - Missed `force_refresh=false` Materialized Path

### What went wrong
- I validated mostly with `force_refresh=true`, which hit on-demand symbol bundle logic.
- The default dashboard path (`force_refresh=false`) still preferred materialized bundle snapshots and surfaced stale SEC-only/no-catalyst copy.

### Impact
- User kept seeing fallback text in the actual dashboard despite successful forced-refresh checks.

### Never repeat checklist (mandatory)
1. For ticker-background fixes, always verify both paths: `force_refresh=true` and `force_refresh=false`.
2. In container validation, assert provenance datasets for default path include live search datasets when materialized bundle lacks web signal.
3. Treat stale materialized-vs-on-demand precedence as a first-class regression risk and add explicit tests for it.
