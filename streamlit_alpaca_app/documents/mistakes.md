# Mistakes Log

## 2026-04-06 - Attention Scoring Doc Drift From Runtime

### What went wrong
- The historical attention layer plan still described scoring with shorthand placeholders (`liquidity proxy`, `peer confirmation`) that no longer matched the implemented formulas in `compute/anomalies.py`.
- Horizon/config defaults shown in the plan also lagged behind runtime values.

### Impact
- Confusion during review about how `severity`, `impact`, `relevance`, and `confidence` are actually computed.
- Higher risk of design decisions being made from stale guidance instead of current contracts.

### Never repeat checklist (mandatory)
1. When explaining scoring behavior, verify formulas directly from code before citing plan docs.
2. If a plan is historical, clearly mark current-runtime sections and keep formulas/versioned defaults updated.
3. When adding a new architecture plan, immediately update the plans index so future sessions find the right spec first.

## 2026-04-06 - Noisy Framework Search During API Discovery

### What went wrong
- I used a broad keyword search for web frameworks and accidentally traversed generated/debug HTML artifacts, producing noisy output.

### Impact
- Slower discovery loop before implementing the actual API scaffold.

### Never repeat checklist (mandatory)
1. Scope framework checks to source directories (`api/`, `scripts/`, `requirements.txt`, `services/`) first.
2. Exclude heavy artifacts (`-g '!documents/debug/**' -g '!cache/**' -g '!*.html'`) on first pass.
3. Stop and narrow immediately when output includes generated bundles.

## 2026-04-05 - Overbroad Search During Domain Ops Discovery

### What went wrong
- I used a broad repo search pattern while looking for domain/DNS automation hooks and pulled notebook-heavy output that was not needed.

### Impact
- Slower discovery loop and unnecessary output review before the actual cutover.

### Never repeat checklist (mandatory)
1. Start domain-operation discovery from `documents/operations/PROJECT_SETUP_AND_OPERATIONS.md` and `scripts/` only.
2. Exclude notebooks and caches in initial searches (`-g '!notebooks/**' -g '!cache/**'`).
3. Expand search scope only after targeted docs/scripts are exhausted.

## 2026-04-05 - Partial Refactor Left UI/Service Contract Mismatch

### What went wrong
- I partially migrated invite preview APIs from single-theme (`theme_override`) to template-based (`template_override`) but did not complete all UI call sites in the same pass.
- This created a temporary runtime mismatch in the invite designer page.

### Impact
- Invite designer preview would fail until UI and service signatures were fully aligned.

### Never repeat checklist (mandatory)
1. When changing public function signatures, run immediate cross-file search for old call patterns before pausing work.
2. Treat service/UI contract migrations as one atomic change set (service, UI, tests) rather than incremental partial edits.
3. Add at least one regression test for the new API surface (`build_invite_email_preview(..., template_override=...)`) before handoff.

## 2026-04-05 - Overscoped Search During Planning

### What went wrong
- I ran a broad `rg` query across the app tree during strategy discovery and pulled far more output than needed.

### Impact
- Wasted review time and tokens during planning work.

### Never repeat checklist (mandatory)
1. Start with targeted files (`documents/README.md`, `data_access/query_service.py`, `services/auth_*`) before any broad scan.
2. If grep is required, constrain with specific dirs and `--max-count`/file filters.
3. Stop and narrow immediately when output volume spikes.

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

## 2026-04-03 - Silent Partial Success in `macro-fred-daily`

### What went wrong
- The FRED phase in `macro-fred-daily` logged errors but did not fail the job.
- Treasury yield persistence in the same run made the execution appear successful while FRED datasets stayed stale.

### Impact
- Monitoring and operators saw `Succeeded` executions despite no fresh FRED snapshots.
- FRED dashboard data could lag for multiple days without a job-level failure signal.

### Never repeat checklist (mandatory)
1. For composite jobs, define mandatory source steps and fail the run when they fail.
2. Do not downgrade mandatory ingest failures to warning-only logs.
3. Add tests that simulate source failure and assert job failure status propagation.
