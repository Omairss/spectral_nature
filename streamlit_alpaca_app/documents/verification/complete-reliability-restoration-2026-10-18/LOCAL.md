# Local acceptance

This restores the contracts identified by the [complete four-consumer comparison](../rollback-consumer-comparison-2026-10-18/README.md). It is not a replacement of product behavior with an older application tree. The release uses an isolated real Git worktree based on `93849f4c`; concurrent cleanup remains untouched.

## Source review

The existing universe and fundamentals producer functions/loaders, all monitoring rules and email delivery, and all 84 broker/store definitions were compared with the exact deployed images in the preceding audit. Their preserved core behavior remains unchanged here. The new worker adopts the current accepted transaction, admission, cancellation, publication and completion implementation already deployed to the other production workers. Current Home, research, paper Trading, learning, authentication and Admin/Health source is unchanged.

- `scripts/sync_robinhood_main.py`: wrap schema readiness, optional migration and scheduled queue work in the shared background database scope. CLI arguments, environment selection, account cadence, queue deduplication, transaction boundaries and output remain unchanged. The context restores the caller's prior classification when it exits. No broker order path is introduced.
- `config/release_contracts.json`: identify required shared runtime definitions for Python roles and a required production recovery execution binding. This is a release requirement; it does not constrain research tools or model decisions.
- `scripts/release_source.py`: review every consumer, including consumers retained on older images; require actual complete image/source evidence and matching shared contracts. Preserve the existing complete Git/runtime inventory and retirement checks.
- `scripts/release_application.py`: preflight the complete live app/job inventory, role coverage, images, configurations and required recovery binding before changing a template. A planned new resource may be absent during development. Normal production deployment still requires it to exist with its reviewed configuration and accepted image.

Retained worker `35fde874…` and API `f1b58d38…` have complete packaged-source proof at `aa43e3d2…`. Retained development worker `5a1e180c…` has complete proof at `e5079b8a…`; its entire database/work-scope modules and required publication/connection definitions match the new accepted implementation. Existing frontend images and their compiled-file evidence remain unchanged.

## Checks completed before builds

- 26 release-boundary and broker CLI checks passed. These cover omitted live consumers, omitted deployment roles, incorrect production namespace, missing production recovery binding, mismatched retained image proof, and background classification without a pipeline environment variable.
- 11 database lifecycle/publication checks passed against real PostgreSQL, with no skips: active SQL cancellation, rollback on deadline, transaction/savepoint ownership, cross-process admission, interactive headroom, conditional publication and durable recovery receipts.
- 10 monitoring, provider receipt and broker checks passed. No notification delivery, broker order, schema migration or model call was performed by these checks.

These are execution and preservation checks, not a model-quality evaluation. Inference, prompts and model selection are unchanged.

## Production recovery definition

One new scheduled `system-recovery` job will use the production health worker's managed identity, managed environment, registry access and production storage/database configuration. It will bind explicitly to the production dataset namespace, a 60-second maintenance budget and `7,22,37,52 * * * *`, with one completion/replica. Its allocation will match the existing development recovery worker (0.5 CPU / 1 GiB). Existing consumers' schedules, resources, credentials, identities and domains remain unchanged. Its application image must be the exact development-accepted worker digest.

Development execution and production verification are pending. This file does not assert that provisioning or runtime restoration is complete, or that database CPU is already reduced.
