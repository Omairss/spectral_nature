# Database lifecycle continuation — October 18

Status: accepted locally; development packaging/verification in progress. No production promotion is authorized for this continuation.

[Plan](../../plans/DATABASE_WORK_LIFECYCLE_2026-10-16.md) · [Current production Home/research behavior](../home-presentation-cleanup-2026-10-18/PRODUCTION.md) · [Source policy](../../infra/RELEASE_SOURCE_POLICY.md)

The October 16 database implementation is already reconciled into current accepted Git source. Its SQL cancellation, global database admission, publication caching, incremental projection and independent recovery remain intact. The newer research checkpoint handoff, learning changes and Home cleanup are preserved. [Source reconciliation](reconciliation.json) verifies every definition in the changed runtime file and nine shared/hotfix files. Historical archives were read as evidence; none supplies application code for this release.

## Remaining repair

The interrupted overlap check left the manifest cursor at four. Recovery allocated only 40% of its 60-second budget to manifests, so waiting for database capacity could consume that phase while the caller still had useful time available. Recovery now uses its remaining total execution budget, retaining the existing four-manifest/25-receipt batch sizes, deadline cancellation, durable cursor and idempotent receipt writes. Actual batch progress and elapsed time are recorded in its operational log.

The only runtime change is `run_system_recovery` in `pipeline/jobs/main.py`. All other definitions in that module are unchanged from the accepted October 18 hotfix. No models, prompts, research breadth, source permissions, job schedules or resource allocations change. The supported application roles are modern API and workers; retired Streamlit/legacy API roles are excluded.

Local checks cover a 30-second capacity wait followed by cursor advancement, expired continuation reads/writes, cancellation propagation and the newer retained-research checkpoint behavior. These are execution/storage checks, not LLM-quality acceptance. Completed model experiments are reused; this continuation adds no paid model evaluation.

The development release also updates `evidence-index-dev` and `system-recovery-dev` from their older reviewed worker image to the current accepted Git application. Their launch commands, namespace, identity, resources and triggers are retained. The current production images/configuration remain separate from this development acceptance.
