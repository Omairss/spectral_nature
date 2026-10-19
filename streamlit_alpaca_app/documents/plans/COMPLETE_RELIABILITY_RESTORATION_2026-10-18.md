# Complete reliability restoration

The owner authorized restoration after the [four-consumer comparison](../verification/rollback-consumer-comparison-2026-10-18/README.md) confirmed missing shared reliability contracts. Production promotion is authorized by the instruction “Restore it then and don't you fucking make a mistake again.” Local and development acceptance remain required before promotion.

## Scope and source

Use the committed current production implementation in a real isolated Git worktree based on `93849f4c`. The core universe/fundamentals, monitoring and broker behavior is already compared against the exact deployed images. Preserve the Home/research, paper Trading, learning, Admin/Health and recovery timing changes. Concurrent cleanup and research edits remain outside this release.

The only application behavior change planned is an explicit background database scope around broker sync. The three pipeline workers adopt the accepted shared implementation through their reviewed worker image. No model, prompt, research-depth, permission or existing job schedule/resource change is required.

## Missing contracts to restore

- Universe and fundamentals: managed database admission/cancellation, conditional publication, recoverable SQL indexing after committed Blob output, durable completion receipts.
- Scheduled health: the same managed connection/completion boundary and the accepted cached Blob-first read path, preserving monitoring coverage and alerts.
- Broker sync: managed shared-store connections with explicit background classification, retaining cadence, queue deduplication and transaction semantics.
- Production recovery: a bounded periodic `system-recovery` executor, with a durable cursor and its own 60-second maintenance budget, outside the health observer's budget. Derive its environment/identity from the reviewed production worker configuration; its application image must be the exact development-accepted Git build. Existing resource configurations remain unchanged.

## Release coverage

Require every declared consumer to have an application role. Consumers retained on previous images need source/package evidence and a behavior review; required connection/publication definitions must match the accepted implementation. A consumer scheduled for release must be included through its role in the image record. Preflight the complete live app/job inventory and every preserved configuration before mutation. An unchanged image is not an implicit exemption from a changed shared contract.

## Acceptance and deployment sequence

1. Review the broker change and release checks locally. Run focused execution/transaction, cadence and release-boundary checks; these do not grade LLM output. Reuse the accepted model evidence because inference code is unchanged.
2. Commit accepted source and its complete release inventory. Build worker and broker roles using pinned role dependencies, replacing the inherited application tree. Verify every packaged application file and installed dependency metadata.
3. Deploy the exact images to `system-recovery-dev` and `robinhood-main-sync-dev`. Exercise the actual universe/fundamentals, monitoring read, broker queue and bounded recovery paths using isolated QA data and controlled execution overrides. Suppress notification delivery and avoid broker orders. Verify admission/cancellation and interactive access against the real database.
4. Register development acceptance of the exact digests. Provision the reviewed production recovery job using that image, then promote the three retained workers and broker through the supported release command. Preserve all unrelated images, identities, credentials, domains, schedules and allocations.
5. Verify actual production source/entrypoints, normal monitoring and bounded recovery execution, public Home/Health behavior and all-resource configuration differences. Record unresolved outcomes explicitly; execution success does not certify database CPU reduction or model reasoning quality.

Completion requires the deployed four consumers to satisfy the reviewed contracts, a working production recovery binding, and preservation of the accepted behavior. The comparison alone does not complete this restoration.
