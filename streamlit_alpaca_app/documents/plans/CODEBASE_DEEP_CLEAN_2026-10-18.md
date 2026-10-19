# Codebase deep cleanup — October 18, 2026

## Objective and scope

Remove abandoned implementations, compatibility layers without consumers, redundant orchestration, and exception handling that hides broken contracts. Preserve the current Next.js, FastAPI, SwiftUI, scheduled producers, broker isolation, research retention, and account boundaries.

The starting checkout is `ec086715`. Other sessions have modified research plans, verification records, learnings and mistakes; preserve their work. No production deployment is authorized by this cleanup. Do not build or deploy before the local cleanup has been reviewed.

## Repository inventory

The initial tracked inventory contains 1,102 files. The structural scan covers all 371 Python, TypeScript, TSX, Swift and shell source files, totaling 153,025 lines. The 257 Python files contain 4,970 functions, 1,022 exception handlers, and 799 broad handlers. Of those broad handlers, 365 have bodies consisting only of `pass`, `return`, or `continue`; that classification identifies review candidates, not proof that each handler is wrong.

Review uses the active architecture map, source imports, function references, exports, dynamic string references, notebooks, scripts, clients, configuration and release consumers. Tests can expose a contract but cannot establish that an abandoned research implementation is still a product dependency. Static reachability is followed by source review before removal.

## Local execution plan

1. Inventory every source boundary and trace candidate ownership. Read the existing philosophy, active map and release policy first.
2. Archive original affected files outside the checkout, verify their contents, and retain them through the existing 30-day archive maintenance.
3. Remove unused namespaces and complete dead helper graphs, including their imports, schemas and configuration registrations. Avoid leaving inert prompts or alternate implementations behind.
4. Move callers from the attention compatibility facade to implementation owners. Remove test-only module replacement machinery; tests patch the actual owner.
5. Review active error handling by responsibility. Transaction cleanup, provider isolation, cancellation and retention of completed research remain explicit requirements. Swallowed programming errors and repeated storage ownership need deeper redesign rather than cosmetic replacement of `except` clauses.
6. Verify retained contracts with focused existing checks, source/import review and before/after function comparison. No new inference is needed for removal of unreachable code; any change to active model inputs or control flow requires complete qualitative comparison.
7. Record removals, remaining opportunities and actual validation. Keep source acceptance tied to the existing committed release record; do not rewrite that record merely to permit packaging.

## Findings under review

| Area | Evidence | Direction |
|---|---|---|
| Unused namespaces | No source importers for old AQL shims, the alternate attention namespace, old Home-summary facade, or retired chart-service modules; scripts/notebooks also searched | Retire after verified archive |
| Dead Home orchestration | Uncalled enrichment reviewers/revisers, alternative market summary, group synthesis and their limits | Remove entire abandoned helper graphs |
| Dead evidence interpretation | Uncalled claim extractor, lexical/numeric claim admission, fixed freshness ranking, and summary-specific page retrieval | Remove abandoned paths; shared research owns interpretation |
| Compatibility test machinery | `attention_agentic.py` replaces itself in `sys.modules` to propagate monkeypatches | Import owners directly and remove facade |
| Storage | `pipeline_store.py`: 87 handlers, 77 broad, 60 mechanically silent; similar repetition in chat logs and SAA/Zopedia stores | Trace connection and transaction ownership before changing active fallback behavior |
| Main orchestration | A 1,059-line agent loop, 726-line research pipeline, 604-line admin dashboard and 562-line Home builder | Identify cohesive ownership boundaries; splitting files alone is not simplification |
| Obsolete summary/evaluation path | Static checks find undefined `prompt_beats` and `_prepend_hypothesis_section`; old critique harness still calls this path | Determine retained research use before retiring or replacing it |
| Web transport | Repeated route-local backend fetch/catch/forward blocks; several map transport failures to HTTP 400 and expose exception messages | Consolidate transport policy at the existing HTTP boundary, preserving auth/CSRF and streaming |
| Native client | JSON decoding, cancellation, authentication restoration, audio and error presentation contain purposeful catches | Do not remove failure boundaries mechanically; inspect actual ownership and duplicated state |

## Status

### Active storage simplification — completed locally

The continuation removes another 482 runtime lines and six function definitions. `DatabaseSession` owns discard and lease return; stores lose duplicate close/rollback suppression and redundant transaction wrappers. Seven factories retain operational outage behavior while propagating cancellation and unexpected faults. Public connection owners replace two forwarding aliases; the ignored per-call timeout parameter is removed. The existing shared JSON owner replaces the DAL serializer, with explicit nested and missing-value handling. Broker missing-table handling uses the driver type directly.

The installed driver/pool implementation was reviewed, 3,286 retained definitions were structurally compared, 3,000 fundamental rows serialize identically, and the focused combined run passes 178 checks. Nine additional broker failure cases pass. Four live PostgreSQL checks remain skipped without a dedicated test DSN; no real SQL-server verification is claimed for this continuation. The [audit](../architecture/cleanup-2026-10-18/README.md) records the exact changes and historical fixture failures. Other-session Git commits and release records remain preserved; this cleanup has no build or deployment.

The repository audit and confirmed dead-code cleanup are complete locally. Twelve runtime modules, the obsolete summary/critique harness and notebook, abandoned helper graphs and their stale tests are retired. Matching shared helpers and constants are consolidated at their existing common owner; required dependencies no longer pretend to be optional. The current story projection and active research behavior remain preserved.

The [audit and complete source inventory](../architecture/cleanup-2026-10-18/README.md) record the changes, verification, historical fixture failures and ranked next work. The deeper active storage, semantic-rule, orchestration and transport redesign remains open. No application build, deployment or release-acceptance update was performed. This local cleanup does not establish completion of every architectural issue in the repository.

## Development trial authorized October 19 UTC

The owner requested reconnaissance and development testing before a two-day comparison with production. Production promotion is conditional on that observation; the prior production authorization does not authorize immediate cleanup promotion. Hold model/provider/prompt settings fixed for an interpretable structural trial unless the owner requests semantic changes. The clock starts only after all intended development consumers run the verified candidate and acceptance checks finish.

Work sequence: capture current immutable images/configuration and known incidents; reconcile the local cleanup into an isolated Git worktree; review active storage, execution and HTTP ownership against current consumers; implement coherent simplifications with retained behavior; run real PostgreSQL and authenticated-route checks; package accepted Git source through the supported release gate; deploy all reviewed development roles; exercise installed producers, account reads, research continuation and deadlines; then collect comparable dev/prod observations for 48 hours. A failed or missing sample cannot count as no regression. Keep production images, settings, data and schedules preserved during the trial.

Compare HTTP availability/latency, auth refresh and permissions, dataset freshness/publication receipts, research retention and continuation, provider outcomes/tokens/cost, failed/queued jobs, database admission/transactions and resource use. Review complete matched research outputs personally; normal model variability is separate from missing evidence or lost work. Record exact digests, observation coverage, baseline incidents and rollback targets. A material regression restarts the full window after a reviewed repair.

### Installed development trial

The structural candidate is committed on `cleanup/dev-trial-20261019` in the isolated Git worktree `spectral_nature_cleanup_trial_20261019`. Four complete verified role images now run on all fourteen original dev consumers. Active reasoning remains fixed by owner choice. See [installed development evidence](../verification/deep-cleanup-dev-trial-2026-10-18/DEVELOPMENT.md) and `trial.json` for the exact observation state; production is unchanged and remains gated on the completed comparison.
