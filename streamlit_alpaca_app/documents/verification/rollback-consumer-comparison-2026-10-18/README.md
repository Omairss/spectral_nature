# Comparison of the four retained production jobs

The comparison is complete. The four jobs preserve their accepted core functions, but their shared reliability implementation is behind the current production release. All four bypass the shared database admission and cancellation layer. Universe and fundamentals also retain older publication/completion handling. Production has no configured executor for the new bounded recovery entrypoint.

These are confirmed implementation gaps, rather than an inference from image age. They leave the requested reliability restoration incomplete. They do not establish lost universe selection, financial statements, monitoring coverage or Robinhood pacing.

## Deployed code and preserved behavior

The reference is the committed source used by the current production release, Git `aa43e3d2ef617acfa67616f586149f1c60b4d0e3`, rather than the concurrently changing working tree. Four isolated registry executions checked all **560 application Python file instances** in the exact retained images against the historical source evidence, with zero mismatches. The registry manifest/config digests and actual launch commands were verified separately. The broker image additionally contains 61 historical test files, which are outside its scheduled launch path.

[Complete comparison and source hashes](comparison.json) · [Current production release](../trading-calls-2026-10-18/PRODUCTION.md) · [Original regression audit](../release-source-regressions-2026-10-16/README.md).

| Production job | Retained image digest prefix | Preserved behavior | Missing reliability changes |
| --- | --- | --- | --- |
| `universe-builder` | `8578f2287052` | Identical job runner, universe construction/fallback helpers and complete universe-selection module. | Shared database admission/cancellation; conditional publication; recovery after SQL indexing failure; durable completion receipt. |
| `fundamentals-quarterly-refresh` | `4e63f790e7d4` | Identical runner, symbol resolution, quarterly builders and existing SimFin/statement reader definitions, including bank and insurance families. | The same database, publication and completion changes as universe. |
| `system-health` | `358c72d7a195` | Entire health-monitoring and email modules match current accepted source, including Azure execution reconciliation, environment scope and broader failure checks. | Managed database connections/cancellation; the newer metadata cache and Blob-first reads; durable completion receipt. |
| `robinhood-main-sync-prod` | `2a8e36cdb6d9` | Identical sync script, queue/store and cadence modules; all 84 existing definitions in the broker service match. Hourly/two-hour boundaries and duplicate exclusion remain. | The store still obtains raw database connections through the older `auth_store._db_connect`. |

Thirty-five selected whole-file/function preservation checks match. The dependency review separates additions used by the current API or downstream consumers from the scheduled jobs themselves: portfolio readiness presentation, downstream financial-history projection and new news/calendar helpers are not missing scheduled-job features merely because they are absent from an older worker image.

## Confirmed execution gaps

**Database headroom is not enforced across all four jobs.** The three pipeline images call `psycopg.connect` directly. Their timeout wrapper can raise while its thread continues; it lacks the accepted work scope and cancellation call. The broker reaches raw connections through its shared authentication-store owner. None of these four images contains `services/database.py` or `services/work_scope.py`. Their existing SQL timeouts are separate from the new admission/cancellation contract. This comparison does not measure their contribution to CPU saturation.

**Universe and fundamentals retain the old publication failure modes.** Their `_upload_manifest` writes `latest.json` without the conditional generation check. Their `_persist_dataset` can propagate a SQL-index failure after the Blob data was committed, instead of recording a recoverable degraded component. Their entrypoint also lacks the new durable job-completion receipt. Dataset selection and financial calculations remain preserved; publication ordering and failure recovery are the differences.

**Health coverage is preserved, but its read path is older.** Its imported metadata loader still queries SQL on a cold cache miss even when a stable Blob manifest exists. Updating only the health-rule module would accomplish nothing: that module already matches. The shared connection, read and completion boundaries require reconciliation.

**Production recovery is not wired.** The accepted runtime calls the dataset-manifest and provider-receipt reconcilers only through `run_system_recovery`. The complete live job inventory binds that entrypoint only to `system-recovery-dev`, a manual job in the `development` namespace. No production job invokes it. Therefore the repaired recovery function being packaged in a production image does not establish periodic production backlog draining. This finding does not quantify pending receipts or imply committed Blob output was deleted.

**A broker image replacement alone is insufficient.** The current broker script has no explicit background database scope; neither its current deployment environment nor the accepted broker Docker stage sets `PIPELINE_JOB_NAME`. The new database owner consequently defaults that process to interactive. Its entrypoint must identify sync as background work when adopting the managed database owner, preserving the existing cadence and duplicate exclusion.

## When the differences arose

The exact rollback receipt records the four image-template replacements on **October 16 Pacific time**: universe at **21:41:19**, fundamentals at **21:42:29**, scheduled health at **21:43:12**, and Robinhood sync at **21:43:21**. The UTC date is October 17. These are recorded deployment update times, rather than independently measured transaction cutovers. [Original receipt](../exact-production-recovery-2026-10-16/production-deployment.json).

The pooled database connection owner was in the original reliability images and was removed by those replacements. Durable job-completion receipts were also present in the three pipeline workers; recoverable SQL-index failure handling was present in the universe/fundamentals publication path. Those capabilities were removed from the templates by the rollback and remain absent in these four retained images.

The later global admission/work-scope cancellation, conditional latest-pointer publication and Blob-first metadata reads were **not in the original reliability images**. They belong to the follow-up lifecycle repair and never reached these four consumers through the subsequent production releases. They are deployment omissions, rather than capabilities that disappeared in that rollback. The earlier health worker performed bulk recovery inline; the replacement independent bounded recovery entrypoint never acquired a configured production job. [Historical development repair](../database-lifecycle-2026-10-16/README.md). The machine-readable comparison records the original-image proof and this classification separately.

## Required reconciliation

1. Move universe, fundamentals and scheduled health to the reviewed shared worker implementation after checking their actual commands, dependencies, dataset inputs and failure paths in development. Keep the core behavior established above.
2. Give the broker sync entrypoint an explicit background database scope, verify its shared store transactions, then package it through the supported broker role. Preserve its cadence and queue contracts.
3. Configure production-scoped bounded recovery between refreshes, with its own deadline and durable cursor, separate from the health observation budget. Keep manifest/provider receipts recoverable across batches.
4. Require a behavior review for every consumer affected by a shared connection, publication or execution contract. Recording an unchanged image as an untouched consumer must not silently stand in for that review.

## Runtime observations and scope

The latest four recorded executions of each job succeeded. Universe and fundamentals last ran on Friday October 16; their schedules are weekdays only. Scheduled health continues every fifteen minutes and broker sync every hour. Successful executions do not validate the failure paths above.

This audit changed no application code, production image, job schedule, identity, data, broker state or model setting. The four registry probes read files without importing the application or receiving production credentials. No model calls or normal refreshes were initiated. Database saturation and repository-wide feature equivalence are outside this completed four-job code/wiring comparison. The application restoration remains open for the specific reliability changes above.

Ten private registry/operator receipts were hash-verified into the existing external archive and their owned temporary directory removed. [Retention record](evidence-retention.json) preserves the archive digest and at least 30 days of retention through the existing automatic cleanup. Current findings and comparison hashes remain in this repository.
