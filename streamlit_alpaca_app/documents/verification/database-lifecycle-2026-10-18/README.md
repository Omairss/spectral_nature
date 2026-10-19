# Database lifecycle continuation — October 18

Status: accepted locally and verified in development. No production promotion is authorized for this continuation.

[Plan](../../plans/DATABASE_WORK_LIFECYCLE_2026-10-16.md) · [Current production Home/research behavior](../home-presentation-cleanup-2026-10-18/PRODUCTION.md) · [Source policy](../../infra/RELEASE_SOURCE_POLICY.md)

The October 16 database implementation is already reconciled into current accepted Git source. Its SQL cancellation, global database admission, publication caching, incremental projection and independent recovery remain intact. The newer research checkpoint handoff, learning changes and Home cleanup are preserved. [Source reconciliation](reconciliation.json) verifies every definition in the changed runtime file and nine shared/hotfix files. Historical archives were read as evidence; none supplies application code for this release.

## Remaining repair

The interrupted overlap check left the manifest cursor at four. Recovery allocated only 40% of its 60-second budget to manifests, so waiting for database capacity could consume that phase while the caller still had useful time available. Recovery now uses its remaining total execution budget, retaining the existing four-manifest/25-receipt batch sizes, deadline cancellation, durable cursor and idempotent receipt writes. Actual batch progress and elapsed time are recorded in its operational log.

The only runtime change is `run_system_recovery` in `pipeline/jobs/main.py`. All other definitions in that module are unchanged from the accepted October 18 hotfix. No models, prompts, research breadth, source permissions, job schedules or resource allocations change. The supported application roles are modern API and workers; retired Streamlit/legacy API roles are excluded.

Local checks cover a 30-second capacity wait followed by cursor advancement, expired continuation reads/writes, cancellation propagation and the newer retained-research checkpoint behavior. These are execution/storage checks, not LLM-quality acceptance. Completed model experiments are reused; this continuation adds no paid model evaluation.

The development release also updates `evidence-index-dev` and `system-recovery-dev` from their older reviewed worker image to the current accepted Git application. Their launch commands, namespace, identity, resources and triggers are retained. The current production images/configuration remain separate from this development acceptance.

## Development evidence

- [Packaged source](packaged-images.json): worker `58f5eaa6` and API `1cba5594`, built from Git `46e604ca`. All 197 application files in each immutable image match the accepted runtime inventory; no inherited application files or retired Streamlit package remain.
- [Deployment](development-deployment.json) and [all-resource audit](configuration-audit.json): eight worker targets and the modern API updated. All 33 resource configurations are preserved; untargeted images, including every production image, are unchanged. API revision `sn-api-next-dev--0000047` is Healthy, ready and running at its existing one-replica maximum.
- [Real overlap/cancellation probe](recovery-overlap.json): a 30-second background SQL hold forced recovery past its former 24-second cutoff. The durable cursor advanced **4 → 8**, checking four manifests without failures and draining 25 provider receipts in **36.613 seconds**, within the existing 60-second deadline. Interactive SQL completed in **0.121 seconds** while background capacity was occupied. Explicit scope cancellation stopped a separate active SQL worker in **0.055 seconds**; both test workers ended and the pool had no outstanding work connections.
- [Normal job continuation](normal-recovery.json): saved job entrypoint `system-recovery-dev-ot8wi1l` succeeded, resumed from eight and advanced to **12** in **4.409 seconds**. The four-manifest and 25-receipt limits are retained. `scan_complete: false` records remaining durable work rather than pretending the whole backlog is empty.
- [Home overlap reads](home-overlap.json): twelve HTTP 200 reads, including the actual capacity hold, retained the exact publication, summaries, graph, six beats and daily datapoints. Twenty additional rollout reads also returned HTTP 200. The initial worker/API rollout left the older DEV frontend serving 404 for `/api/admin/health`. That omitted consumer is now corrected and verified through the [matching frontend release](../admin-health-frontend-2026-10-18/README.md), including a fresh authenticated Health screen with live database/container charts.
- Scheduled `evidence-index-dev-29872960` succeeded alongside the controlled probe and logged **27,843/27,843 chunks indexed**. [Index observation](index-observation.json).

The controlled checks made no model calls and did not regenerate research, change source permissions, send notifications or alter schedules. The source policy remains enforced. This proves scoped DEV execution, continuation and serving behavior; it does not measure a full production refresh cycle, daily CPU reduction or LLM answer quality. Global permits protect participating database clients' concurrency rather than a guaranteed CPU percentage. Hierarchical summarization remains disabled.

## Source integration

The reviewed branch is merged into the main checkout. [Integration proof](source-integration.json) records exact preservation of all other pipeline text, ten unrelated dirty files and the complete reviewed committed tree. Other sessions' changes in shared documents were retained through exact patch application. No active work was stashed, reset or included in this development image. The newer learning-provenance commit is also retained in the branch history.

The completed isolated Git worktree was removed after integration. Seventy private operator files, including before/after resource definitions and execution logs, were hash-verified and archived outside the checkout in `~/Library/Application Support/SpectralNature/retired-release-sources/20261019T024651Z/database-lifecycle-20261018-operator-receipts.tar.gz`. The existing automatic cleanup retains this completed batch until at least November 18, 2026 UTC. It contains no extracted application source tree; current application source and public evidence remain in Git.
