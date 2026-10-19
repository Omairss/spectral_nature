# Codebase cleanup audit — October 18, 2026

The repository has substantial abandoned code, duplicated helpers and inconsistent failure handling. Confirmed dead paths are removed locally. The continuation also removes repeated storage teardown and consolidates JSON transport. The development trial also consolidates Next.js HTTP transport. Broader storage responsibilities and research execution remain unfinished.

## Coverage

The baseline is Git revision `ec086715302dd97b737c95f961f4d1c04380143d`. The inventory covers all 376 baseline tracked Python, TypeScript, TSX, Swift, shell, MJS and CSS files, plus the continuation's new JSON contract test file. The initial Python inventory contains 257 files and 4,970 function definitions. Packaging, requirements, architecture, notebooks, release consumers and dynamic import mechanisms were also checked for references. Other sessions' source integration, uncommitted research documents and evidence were preserved.

This is a structural repository audit with source and dependency review of the cleanup candidates. It is **not** a claim that every retained line has received human review. [The complete file inventory](source-inventory.json) records every source file, before/after size, Python function and exception counts, removals, and changed runtime definitions. The [working plan](../../plans/CODEBASE_DEEP_CLEAN_2026-10-18.md) records the implementation sequence.

## Completed locally

Twelve runtime modules have been retired:

| Removed path under `services/` | Reason and current owner |
|---|---|
| `analytics.py`, `technicals.py` | Unused chart implementations; current clients consume shared data/chart contracts |
| `aql/_agentic.py` | Unused compatibility facade; research entrypoints live in `aql/pipeline.py` |
| `aql/chat_log.py`, `aql/scratchpad.py` | Unused compatibility imports; state belongs to `agents/` |
| `aql/entities.py` | Unused compatibility import; extraction belongs to `entity_extraction.py` |
| `attention/__init__.py` | An unused second namespace over the existing attention modules |
| `attention_agentic.py` | Compatibility facade and `sys.modules` replacement for test patches; callers now use implementation owners |
| `attention_home_summary.py` | Unused summary facade |
| `aql/critique.py` | Abandoned summary review/rewrite path; no product callers |
| `market_activity_shared.py` | Unused wildcard import shim; common helpers own the implementation |
| `pipeline_slices.py` | Fallback for mixing old/new application files; the current partition-aware store already implements the same live path |

The cleanup also removes:

- Uncalled Home enrichment reviewers/revisers, the alternate market summary, group synthesis, their schemas and configuration settings.
- The unused claim-extraction/ranking graph, including fixed lexical overlap, numeric matching and freshness filtering. Current research continues through the shared engine.
- Summary-specific page acquisition and retained-evidence retrieval that no caller used.
- The old Home summary/critique stack and its April evaluation script. This path had undefined names and no product consumers. The historical evaluation results remain documentation, with a retirement note.
- The obsolete event-prompt notebook, which imported already-removed planners/fallback writers and recommended preserving old prose filters. The active graph notebook now imports AQL directly; its historical executed copy remains frozen evidence.
- Tests for retired implementations, including the patch-propagating facade and old fallback narratives. Retained tests import their actual owners.
- Fake optional PostgreSQL dependency guards across the stores. The shared database module already imports PostgreSQL unconditionally. Required Azure SDK import guards and a blanket import guard around local signal computation were removed too. Missing configuration and unavailable external services retain their existing behavior.
- Unused identity lookup methods in the DAL, leftover schemas, duplicate imports, inert settings and old formatting helpers.

Sixteen copied helper implementations were initially consolidated into `services/common/market_activity.py`, with callers redirected to that owner. One subsequently proved unused everywhere and was removed from the common module too. The comparison covered function bodies, transitive helper dependencies, module identities and referenced constant values. Identical causal/rate/bucket constants were also consolidated or removed when unused.

The remaining apparently identical taxonomy/graph functions were **not** merged: their surrounding dependencies differ. The two JSON serializers also initially differed; their scalar and container contracts were reviewed explicitly before the consolidation below. Textual similarity is insufficient to establish equal behavior.

The Home story projection remains verbatim, including reviewed explanations and older stored-artifact compatibility. Audio remains in `attach_aql_zopedia_summary_audio`. Active research prompts, review behavior, confidence thresholds, broker/account permissions and execution budgets were not redesigned.

## Active storage and serialization continuation

This pass removes another 482 runtime lines and six runtime function definitions, including nested functions. Runtime handlers naming `Exception` or `BaseException` directly fall from 747 to 677. The reduction includes both removed handlers and narrower, typed failure boundaries. Indentation changes account for much of the displayed diff; SQL strings retain their original bytes.

- `DatabaseSession.close()` closes an unfinished physical connection and returns its lease exactly once. Closing the driver connection discards its transaction and session advisory locks without another rollback request. Successful context exit still commits; failed commits are never retried. Cancellation and teardown share the existing lock so an old operation cannot target a later borrower.
- Removed 51 close-suppression blocks and 10 redundant rollback blocks. Converted 74 pure finalizers to standard resource contexts, then removed 56 of those outer contexts because the existing session context already owned teardown. Removed five more duplicate close finalizers around session contexts. Explicit commit points and ownership guards on caller-supplied transactions remain intact.
- Removed the private forwarding aliases in auth and pipeline storage. Callers and test patches address the actual public owners. Removed the ignored per-call `connect_timeout` parameter and all seven callers; the pool and work scope already own transport and execution timing.
- Seven connection factories now convert operational connection failures to unavailable results while allowing cancellation, invalid configuration and programming errors to propagate. Admission exhaustion uses the pool's existing `PoolTimeout` type. This adds no retry mechanism. Three broker reads catch `UndefinedTable` directly instead of inspecting every exception's SQLSTATE.
- `services/json_utils.py` now owns normalization for DAL, API views, charts and Home production. Removed four duplicate/obsolete serializer definitions. Nested Series, Index, arrays, dataclasses and frames normalize recursively; missing dates and nullable values become JSON null. Unsupported objects still fail strict JSON encoding. Finite broker Decimal/UUID conversion remains separate because it has a different contract.

The installed psycopg driver and null-pool return/close implementation were inspected before changing teardown. Local checks cover successful commit, failed commit without retry, exception preservation, discard without rollback RPC, repeated close, pool ownership errors, cancellation propagation, and broker missing-table behavior. These are deterministic resource/data contracts, not evaluations of LLM prose.

The structural comparison against the verified pre-continuation archive covers 3,286 retained definitions. After accounting for the documented ownership, import, serializer and error-type changes, no unexpected body changes remain. Replaying 1,000 rows from each of the three checked-in fundamental datasets produces identical strict JSON through both former serializers and the shared implementation. Separate nested/missing-value checks cover the intentional fixes.

## Remaining work, in order

### 1. Give storage failures and transactions clear owners

`pipeline_store.py` still spans publication manifests, local cache eviction, database indexing, Azure job control, Trading approvals, connector telemetry, provider accounting and research checkpoints. It contains 54 broad handlers, including 45 with mechanically simple `pass`/`return`/`continue` bodies. The same shape appears in chat logging, SAA/Zopedia and knowledge-graph persistence.

Connection acquisition now distinguishes operational outages from cancellation and unexpected faults. Query-level catches still frequently collapse unavailable storage, bad SQL/schema assumptions and programming errors into empty results. Removing repeated close/rollback suppression resolves teardown ownership; it does not resolve those business-level failure contracts.

The source fix is to give dataset publication, research persistence and job administration coherent owners; use the existing session's transaction lifetime; and distinguish unconfigured/unavailable stores from unexpected defects. Preserve valid Blob publications when optional SQL indexing fails, preserve completed research when optional tracking fails, and preserve authorization failure semantics. Do not replace all catches with a universal retry/fallback helper.

### 2. Remove active semantic rules through the shared research contract

Dead semantic filters are gone, but active ones remain in AQL shared helpers, collector, assembler, common market helpers and candidate construction. Examples include `_passes_symbol_search_gate`, `_is_low_signal_claim_text`, `_judge_cause_status`, `_looks_like_stat_dump` and title/label construction. These paths use word patterns, overlap scores and fixed labels to decide meaning.

The global fix belongs in model-owned evidence selection and interpretation through the existing shared engine. Code should retain execution/schema/permission rules and numerical calculations. Before changing active behavior, compare complete frozen investigations and personally review their usefulness, recall, explanation and failure retention. Do not turn the audit's examples into new keyword lists. Existing owner decisions about confidence thresholds remain a separate behavior change.

### 3. Simplify orchestration around existing execution ownership

The agent execution loop is 1,059 lines; bottom-up research orchestration is 726; the Home builder is 562; the access-admin dashboard is 604. File size alone is not the defect. The problem is how planning, acquisition, review, checkpointing, presentation and failure outcomes interact inside those functions.

The existing research session, work scope and budget already provide execution ownership. Make those contracts authoritative and remove overlapping orchestration; keep feature adapters focused on tasks and result formatting. Preserve the accepted deadline behavior that returns retained research before optional review/persistence. Creating more feature controllers or splitting a large function into dozens of forwarding helpers would preserve the complexity.

### 4. Consolidate normalization at real data boundaries

After consolidation, the source scan still finds 22 `_clean` definitions, 19 `_coerce_text`, 13 `_normalize_symbol`, 10 `_json_dumps` and 10 `_trim` definitions. Several are exact copies; others differ on NaN, containers, arrays, missing values, truncation or dates. Some differences are meaningful.

The DAL/shared JSON duplication is resolved. Continue from typed provider and stored-artifact boundaries, then remove repeated repair inside business logic. Do not replace all coercion with a permissive utility that silently erases contract errors.

### 5. Put HTTP transport policy at the existing gateway boundary

Many Next.js routes repeat backend fetch, catch and JSON forwarding. Several return HTTP 400 for connection/timeouts/configuration failures and expose the thrown exception text. Python routes also repeat error translation. Consolidate the policy in the existing transport boundary while preserving validation, refresh-cookie rotation, CSRF, status codes, binary audio and streaming. The subsequent development trial implements this Next.js boundary consolidation, removes 27 repeated catches and verifies 26 adapter call structures plus thirteen boundary cases. Python route error translation remains future work. See [installed development verification](../../verification/deep-cleanup-dev-trial-2026-10-18/DEVELOPMENT.md).

### 6. Separate account email presentation from identity operations

`auth_service.py` includes invitation template libraries, image assets, HTML rendering, theme migration and previews alongside authentication, sessions, lockout, password reset and account operations. `auth_store.py` also contains a 604-line admin aggregation. Separate those responsibilities at their existing service boundaries; preserve delivery, access checks and database-outage behavior. Old-looking invitation settings are active behavior, not automatically removable history.

### 7. Keep broker isolation; simplify repeated provider conversions

`robinhood_main.py` has 113 functions and 41 exception handlers. Its provider pacing, last-good retention, read/review/execute separation and single-use confirmations are current requirements. Review repeated catalog/history conversion and error mapping inside those boundaries. Do not collapse the broker into generic Alpaca/data-access code to make the module smaller.

### 8. Retire stale evaluators and fixtures according to the current contract

The broader checks expose tests for discontinued prose suppression, the old evidence dataset/source projection, pre-lifecycle SQL fixtures and pre-paper-tracking approval behavior. These should be reconciled against current accepted behavior. They are not a reason to restore deleted admission rules or change current Trading approvals. Existing historical research evidence remains distinct from runnable current evaluators.

### 9. Review client duplication with rendered behavior in view

The web unused-local/parameter check passes. The Swift scan finds no private function name occurring only at its definition. Swift JSON decoding, cancellation, session restoration and resource loading have purposeful error boundaries. Neither observation proves that all client code is necessary. Audit duplicated session/resource state and CSS against actual routes, desktop/phone rendering and native interactions before deleting them. The copy scan found no generic disclaimer/marketing block to remove; input placeholders and meaningful unavailable states were retained.

## Validation and limits

- Every remaining Python file parses. All 149 runtime Python modules import successfully with network access and external service subprocesses blocked; dependency CPU/platform probes were allowed. There were zero external attempts in the successful run.
- Before/after AST comparison records retained and removed definitions. The initial retirement pass changed dependency-availability guards and direct-owner imports, plus the DAL class losing unused methods. The continuation's additional changes are detailed above. Consolidated helpers have equivalent dependency graphs; the live story projection, agent loop, research prompts and review logic remain unchanged.
- Twenty-four focused existing checks pass: module boundaries, retained story projection, audio metadata, price-session provenance, supplied-evidence contracts, native/shared-engine boundaries and the SAA connection factory.
- The web TypeScript unused-local/parameter check passes. Focused AQL/pipeline static checks and `git diff --check` pass.
- A broader 89-check run passed 84 and failed five in unchanged behavior: two old prose-suppression expectations, an old evidence dataset expectation and two older SAA SQL fixtures. The complete pipeline-store run passed 24 and failed one older Trading approval fixture. A broader job run was stopped after 52 passes and two failures (the same approval fixture and an old learning-status message expectation); it was not a completed suite. No claim of a passing full suite or LLM quality acceptance follows from these checks.
- Before the dev trial, the continuation's combined focused run passed 178 checks and skipped four explicitly enabled live PostgreSQL checks, and deselects the already documented historical Trading fixture. Nine additional broker error-contract cases pass; the final lifecycle-only run passes 53 and skips the same four live checks. At that initial stage no local PostgreSQL server or test DSN was available. The subsequent development trial runs these checks against real PostgreSQL: 57 lifecycle cases pass with no skips, and 217 focused combined Python cases pass. See the current trial record for the completed source and cloud verification.
- A separate wider read/view run passed 104 and reproduced the same three documented stale DAL failures (old prose admission and evidence-dataset expectations). Those expectations were not used to restore retired behavior. All 149 runtime imports pass with external access blocked, and all 244 current Python files parse.

The code still has hundreds of broad exception handlers. They were inventoried, not mechanically deleted. Complete active-path remediation and release acceptance remain outstanding.

## Retention and release state

Original source and the affected notebooks were archived and hash-verified before mutation in the existing private archive directory:

- `~/Library/Application Support/SpectralNature/retired-release-sources/20261019T040740Z`
- `~/Library/Application Support/SpectralNature/retired-release-sources/20261019T040840Z`
- `~/Library/Application Support/SpectralNature/retired-release-sources/20261019T054059Z` (verified pre-continuation source)

Those UTC batch names correspond to October 18 locally. Their manifests enforce 30-day retention through the existing cleanup scheduler. They are evidence only and cannot supply a release.

At the end of the initial local passes, changes were uncommitted and no cleanup release had been built. Subsequent source reconciliation uses the real Git branch `cleanup/dev-trial-20261019`, based on `b948d6f8`, preserving concurrent root-workspace changes. The [development trial](../../verification/deep-cleanup-dev-trial-2026-10-18/README.md) records accepted application source, complete package proofs, all-consumer review, actual dev deployment and the two-day comparison. Production remains unchanged. Deeper active-path work above remains open; development verification does not accept deferred semantic redesign.
