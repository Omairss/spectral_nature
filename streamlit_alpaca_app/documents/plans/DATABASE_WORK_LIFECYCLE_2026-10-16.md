# Database work lifecycle and refresh coordination

Status: resumed October 18 against current accepted Git source. Existing lifecycle changes are retained; the remaining recovery-budget fix is locally accepted and proceeds to development verification. Production is outside this continuation authorization. See the [current continuation record](../verification/database-lifecycle-2026-10-18/README.md).

**October 16 source-retirement update:** the owner stopped this task while obsolete release trees were isolated. The complete task directory, eleven-file scoped patch, function ownership, deployment receipts and verification evidence are preserved in the [retirement and resume record](../verification/release-source-retirement-2026-10-16/README.md). Resume the existing implementation by reconciling its scoped changes in Git. Earlier temporary source paths below are historical; do not recreate them or restore a complete old application tree. Existing work and paid research do not need to be restarted for this filesystem change.

[Research index](../research/README.md) · [Past production failures](../research/system-reliability/PRODUCTION_FAILURES_2026-10-16.md)

## Evidence and scope

The captured day averaged 34.2% database CPU: 16.6% outside 14:00–22:00 UTC and 69.5% inside that window. Use this slack to pace database phases while independent research/computation remain parallel. Four daily refreshes do not establish a growing backlog. Whole-job serialization would exceed the two-hour cadence: the last successful news/Home/Trading cycle totals about 168 minutes of wall time.

October 16 snapshots found twelve client evidence reads active for 25–159 minutes. Tool timeouts stop waiting without cancelling their threads/SQL. The development evidence index runs every five minutes against the same physical database binding. Recovery also runs before health observation. Published readers still compare SQL after finding a valid Blob manifest.

This repair covers shared execution cancellation, global database admission, publication serving/cache, incremental indexing and independent bounded recovery. It does not change research breadth, model routes or hierarchy policy. The separate unaccepted reasoning/cost candidate must remain excluded from deployment.

## Owning contracts

- A cancellable work scope owns its database operations. Expiry prevents new operations and requests cancellation of active SQL. Cancellation and connection reuse cannot race: an old callback must never cancel a later borrower's query. Transaction rollback/cleanup retain capacity until the operation actually ends; completed writes are never blindly replayed.
- PostgreSQL session advisory locks on the work connection enforce admission across processes and credentials in the same database. Start with one background transaction and a bounded overall allowance, protecting at least 30% of slots for interactive/essential work. This is a concurrency policy, not a hard CPU partition. Return an unsuccessful admission connection before waiting. Locks disappear with the work session; no separate broker, lease store or polling table is introduced.
- Physical work connections retain their normal explicit transaction/savepoint semantics. Independent progress tracking and health observation use the essential allowance, preventing a work transaction from waiting on its own tracking write. Admission applies only during database phases, not inference/browser/provider waits. Bound queue waits by the caller's remaining execution budget and record them separately. Defaults allow background admission waits up to 300 seconds and interactive waits up to 30 seconds; a shorter tool/index/recovery deadline always wins. Background pipeline connection startup uses the same queue allowance.
- A valid stable publication manifest is sufficient for normal latest reads. A cache miss must not force a SQL comparison. Immutable artifact contents cache by version, while the latest pointer is checked cheaply. Publication advances the pointer conditionally, preventing an older generation from replacing a newer one.
- Evidence projection checkpoints advance only after successful persistence. Skip unchanged knowledge-page versions and news dataset versions without dropping source coverage. Embedding reuse remains keyed by content and embedding space.
- Health observes independently. A separate recovery entrypoint drains manifests/receipts in bounded batches, retaining unfinished durable work across executions.

## Acceptance and deployment

1. Verify real PostgreSQL cancellation, rollback, connection reuse, process-wide admission, protected interactive access and release after disconnect. Use owned temporary records and short sleeps, not paid model runs.
2. Verify manifest-only serving with SQL blocked, concurrent publication ordering, cache reuse, unchanged-source skips, changed/deleted page handling and bounded recovery with retained pending receipts.
3. Measure representative retained-evidence queries under low concurrency, preserving originals and actual filters. A remaining slow query requires source/query investigation; fewer connections alone is not acceptance.
4. Reconcile lifecycle changes into the accepted Git application source, preserving newer accepted consumer behavior. Build APIs and workers from that identified revision under the [release-source policy](../infra/RELEASE_SOURCE_POLICY.md). Preserve allocations, commands, credentials, schedules and namespaces. Do not import application code from an older image or temporary overlay.
5. Exercise the deployed user paths and an isolated background/recovery overlap. Record Home/ad-hoc completion, SQL queue/execution times, unfinished work and known limits. Ordinary scheduled production work is not started or changed by this development release.

Scoped entry source hashes are retained in `/tmp/sn-db-repair-20261016/baseline.json`. Current workspace files include concurrent/unreleased work; deployment must not copy them wholesale.

## Local verification

The frozen accepted source plus eleven scoped lifecycle modules passed 52 focused offline checks, including publication ownership, all four timeout wrappers, recovery continuation budgets and receipt retention. Real PostgreSQL checks passed for cancellation, rollback/savepoints, stale-callback safety and cross-process admission with protected interactive access. Four temporary-relation projection checks passed, covering unchanged/changed/archived pages, interrupted checkpoint retry unchanged news versions and failed-download retry. Real concurrent Azure Blob publication retained the newest generation; eight SQL-disabled reads returned it, and all six owned probe manifests were removed. No model calls were made for these checks.

Two retained-evidence reads with their original MRNA filter and five-result limit completed in 10.641 seconds (catalog) and 8.880 seconds (lexical query). Local connection establishment accounts for much of the measured time. These probes establish completion under their observed load, not full-cycle production capacity or research-answer quality.

The timeout audit covers the shared Zopedia tool/hidden-step wrappers, pipeline startup/helpers, Attention, AQL and live research. Their parallel task submissions preserve context. Existing model routes, prompts, research breadth and accepted consumers remain preserved in the staged release. Root recovery scope includes continuation reads/writes as well as drain loops.
