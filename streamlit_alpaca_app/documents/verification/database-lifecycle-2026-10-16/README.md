# Database lifecycle and refresh coordination — October 16

Status: historical October 16 evidence. The interrupted repair was reconciled with newer accepted Git source and verified in development on October 18. Use the [current continuation record](../database-lifecycle-2026-10-18/README.md) for current images and results. Production is outside this repair authorization.

[Implementation plan](../../plans/DATABASE_WORK_LIFECYCLE_2026-10-16.md) · [Research index](../../research/README.md) · [Preceding production failures](../../research/system-reliability/PRODUCTION_FAILURES_2026-10-16.md)

## Change

Expired threaded work now cancels its SQL and cannot start later database operations. The work connection keeps its global advisory permit until rollback/release finishes. The shared admission domain begins with one background transaction and three total work slots, protecting interactive/progress access. Background queue waits can use up to 300 seconds within the actual caller budget; interactive waits remain at 30 seconds. Independent research/provider waits hold no database permit.

A stable Blob publication serves latest reads without querying its optional SQL index. Metadata misses coalesce, caches follow version identity, and conditional latest-pointer writes keep older generations from replacing newer ones. The index persists/checkpoints changed knowledge pages in batches, skips unchanged news versions and releases physical database connections during embedding requests. Recovery runs independently with a total budget covering continuation IO and drain loops; Health and usage reads no longer start backlog recovery.

The accepted deployed baseline plus eleven scoped lifecycle modules is frozen in [source.json](source.json). Concurrent trading changes and the unaccepted reasoning/cost candidate are excluded. Model routes, prompts and research breadth remain at the accepted baseline. The engine source fingerprint changes to `52d929f1cad3d4aa3e08` because the execution wrappers changed. Hierarchical summarization remains off.

## Local evidence

- [Checks](local-checks.json): 52 focused offline checks; six real PostgreSQL lifecycle checks; four projection checks against owned temporary relations. These verify execution/storage contracts, not LLM answer quality.
- [Real Blob probe](blob-probe.json): concurrent publication ordering, eight reads with SQL forbidden, and cleanup of six owned probe manifests.
- [Retained-evidence reads](retained-query-probe.json): five-result MRNA catalog/lexical reads completed in 10.641/8.880 seconds from the local machine. Connection setup dominates much of that wall time. No paid model was called by the controlled checks.

## Limits and rollout

The allowance protects query concurrency, not a guaranteed CPU partition or strict FIFO ordering. Configure the same slot limits on all participating clients. Existing production clients do not obey the new admission contract until separately rolled out. Python threads are not forcibly killed; expired scopes prevent later IO, active SQL receives cancellation, and statement deadlines provide an independent bound when cancellation transport is unavailable.

Development shares the physical database with production. Small component/overlap checks cannot certify a complete production refresh cycle or a reduction in daily CPU/cost. Cached datasets retain their publication/as-of identity; this repair does not regenerate stale development research. Keep those distinctions visible when assessing the release.

For rollback, use the previous per-target immutable image recorded in the final release receipt. The projection-state table is additive and need not be deleted; reverting to the old index simply ignores it. Recovery is an independent development job. No normal production execution, schedule, resource allocation, identity or credential is changed by this release.
