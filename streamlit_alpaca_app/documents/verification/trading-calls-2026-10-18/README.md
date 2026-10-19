# Trading resumption verification — October 18

**Current assessment: accepted locally for the owner-approved paper pilot; development and production deployment pending.** The owner requires a strong general framework for iteration, not perfect current forecasts. The [reasoning findings](../../research/trading-calls/experiments/2026-10-18/ASSESSMENT.md) remain improvement evidence, not an automatic pilot veto. The owner explicitly authorized production with “Push to prod.” Development verification of the exact images remains required. The newer database recovery and Admin/Health frontend releases are preserved.

Owned source and complete experiment evidence are saved in unaccepted checkpoint `27e9bd1c` on `held/trading-resume-20261019T025845Z`. An isolated Git index created that checkpoint without moving the active branch, changing the active index or staging other sessions' dirty work. Shared dirty notes were excluded from that commit and remain in the checkout. [Checkpoint receipt](checkpoint.json). This is preservation, not release acceptance.

## Preservation

Started at Git `98663cd4` and accepted application source `9f9e3b18`. Other sessions then committed documentation-only `725caebd` and integrated database continuation `c090d774`, merged as `0a7c9f2a`. The current acceptance source is `cc14d46c`. The recovery-budget repair in `run_system_recovery`, release record and all other sessions' dirty documentation are preserved. [Initial inventory](resume-baseline.json), [comparison against the current accepted source and Git](preservation-review.json).

Only Trading definitions/endpoints, approval persistence, the optional equities tracking call and Trading web/native views changed in this task. The new tracking service/BFF route are listed separately. No other file in the current accepted source inventory differs in the checkout. Native Trading/calendar support already existed; the native change only labels the new paper chart by its stored session dates rather than allowing timezone-shifted dates. Home, auth, research deadlines, learning, database lifecycle repairs, workload settings and release tools were preserved. Hashes are scope evidence, not qualitative acceptance.

## Objective checks

| Boundary | Result |
| --- | --- |
| Real PostgreSQL | Passed atomic approval, three concurrent taps producing one action/ledger row, immutable thesis and entry, reject behavior, user/environment isolation, genuine legacy approval backfill, metadata fallback and idempotent migration. Isolated schema `qa_call_tracking_0598a9b5f1bd` was dropped in `finally`. |
| Daily valuation | Passed post-approval/weekend entry, future/partial-day exclusion, DST, consistent split adjustment, exact SPY dates, short return/excess, maturity and missing expected session handling. |
| Generation diagnostics | Uses actual publication time rather than the older market snapshot; completed-bar cutoff and new directional semantics checked. Kept separate from the approval-time ledger. |
| Publication | Failed outcome publication does not mark a call complete; retry binds the exact published price version. Summary/detail projection and materialized ownership checks passed. |
| Existing focused tests | `pytest tests/test_trading_agent.py -k 'stock_summary_lookup or outcomes' -q`: 3 passed, 7 deselected. The arithmetic fixture now uses actual New York daily bucket labels and post-publication entry dates. No fixed prose grade was added. |
| Web | TypeScript `--noEmit` and scoped ESLint passed for the Trading page and owned detail route. |
| Native | `TradingView.swift`, including explicit session-date axis labels, passed Swift parse. No new native application build/install or authenticated UI verification yet. |

Reproducible arithmetic/publication/isolated-database checks are adjacent. The database script needs the private current development worker configuration at its documented `/tmp` path; it creates a disposable schema and removes it even on failure. Never run its fixtures in application-owned tables.

## Earlier integration assessment

The paper path is implemented and its objective contracts pass. Normal authenticated UI/native build checks and release integration remain open. Approved-call outcomes are retained but do not yet feed the existing generation-outcome review or shared learning workflow; manual iteration is possible, automatic improvement is not demonstrated. Close that feedback connection through the existing framework and retain the observed reasoning failures for future comparisons. Follow the revised [acceptance plan](../../plans/TRADING_CALLS_AND_APPROVAL_TRACKING_2026-10-16.md); perfect forecasts are not a pilot prerequisite. Before release, reconcile accepted source in Git, preserve other sessions' work, update the committed inventory/consumer review and verify in dev. Production requires new explicit owner approval. Historical trees/image overlays are not build sources.

## Production release preparation — October 19 UTC

Current source includes the latest Admin/Health integration and database recovery continuation. The paper-call foundation, rather than perfect individual forecasts, is the acceptance scope. All original reasoning experiments remain evidence; no model/effort switch or experimental typed-packet path is promoted.

Completed approved-call publications now enqueue immutable owner/environment-bound episodes into the existing Zopedia learning worker. The original thesis and dated outcome remain separate supplied evidence. Enqueue retries cannot reset completed reviews or rewrite original evidence. A failed delivery retries from retained completed outcomes even when no call remains active. The price job performs no inference; the shared learning worker retains its total 25-input budget, pending/retry behavior and exact-input critique reuse. Outcome reviews distinguish reasoning from luck and benchmark moves. Retrospective replays use retained supplied evidence and are not forecast-accuracy validation. Learning pages remain excluded from public/shared page reads under the existing visibility policy. This connects evidence to review; automatic improvement in future Trading returns has not been demonstrated.

Real PostgreSQL checks cover immutable feedback enqueue, terminal-state retries, the valid empty production namespace, distinct owner/environment identity and the shared worker limit. Isolated schema `qa_feedback_afede6081be6` was dropped. Existing publication checks now assert enqueue only after successful outcome publication/completion. Full focused contract checks: **27 passed**; no deterministic prose grade. The older fake provider responses were updated to the v3 DTO for contract checks; legacy stored watch/no-call handling remains covered separately. Web TypeScript/scoped ESLint pass. A full iPhone simulator build succeeds with Xcode explicitly selected for the command; system developer-directory settings were not changed.

Personally inspected the local normal web page through the current Next.js adapter and an isolated loopback fixture: calls start collapsed, opening a call exposes the dated reference, pricing assessment, assumptions, opposing case and approval controls; Paper performance lazily loads a selected call and renders dated call/SPY returns, separate metrics and retained history. The rendered paper outcome was a labeled QA fixture, never an application-owned approval or observation. Local rendering checks establish presentation, not live authentication or forecast accuracy.
