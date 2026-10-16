# Admin Health verification — October 15, 2026

Status: deployed to development on October 16. Live owner authentication, anonymous rejection, PostgreSQL/app/job telemetry and the dedicated read-only managed identity were checked. See the [combined release record](../system-reliability-2026-10-16/README.md) for immutable images and runtime checks.

The new authenticated `/v1/admin/health` read model and `/api/admin/health` BFF feed a separate Admin Health tab. The model reads recorded provider attempts and usage, known estimated charges, hourly trends, shared pipeline-server connections/capacity and the latest environment-scoped executions. A selected Azure resource supplies CPU, memory, connections, restarts and other available standard metrics. Container charts show resource totals; configured CPU/memory is explicitly per replica. PostgreSQL resources are marked shared.

No model calls, DDL, research triggers or automatic polling run from this view. Database reads cache for 60 seconds, inventory for 30 minutes and resource metrics for 10 minutes. Concurrent requests share the same collection. Database and Azure failures remain independent; an allocation lookup cannot discard valid usage. Missing usage, pricing and metrics stay unknown. Provider receipt coverage is explicitly database-indexed, so this view is not a replacement for provider billing reconciliation.

## Completed local verification

- Seven meaningful contract checks pass in the working tree and against the accepted API baseline plus the scoped backend/Admin overlays: admin authorization, no-store responses, resource/environment isolation, cache single-flight and independent partial failures.
- Real read-only Azure probes returned five PostgreSQL series, four application series and two job series, with app/job allocation. The tests used operator credentials locally; the deployed development-only identity subsequently returned the same metric families through the live Admin UI.
- TypeScript checks pass for the task-only release source. The release preserves the accepted Admin LLM editor and existing web features instead of copying unrelated dirty-workspace changes.
- Desktop 1440×1000 and phone 390×844 screenshots were personally inspected. There were no page errors or whole-page overflow. Long table text originally overlapped adjacent cells; the shared DataTable style now wraps it. The local render uses synthetic admin identity and captured real read-only telemetry; live authentication is a separate release check.

## Deployment requirements

Use the final backend API image as the immutable base and guard the inherited API source hash before adding the Health route. Direct extraction from the current immutable API image confirmed the accepted feature-request router addition (`e5381252…`); the Admin overlay now preserves it. The earlier retained API source (`3f4cb697…`) was older and was rejected for release preparation. Rebuild web from `/tmp/sn-native-polish-release-20261015/web` plus the small task diff. Prepared contexts and source manifests are under `/tmp/sn-reliability-20261015/release/`. Never build from the full dirty workspace.

The existing pipeline managed identity is also attached to production. Do not broaden it. Use a development-only telemetry identity with read-only resource/metric access, attach it only to `sn-api-next-dev`, and set `SYSTEM_HEALTH_METRICS_CLIENT_ID` and `SYSTEM_HEALTH_AZURE_RESOURCE_GROUP_ID`. This adds no compute service or larger allocation. Cached standard metric retrieval still uses Azure APIs; no paid alert rules or new log ingestion are introduced.

Runtime verification: all four dev apps reached matching latest/ready revisions; all eleven job templates use the accepted digests. The owner session opened Health; an anonymous BFF request returned 401. The dedicated identity returned database CPU/memory/connections/rejections/storage, API CPU/memory/replicas/restarts and job CPU/memory. Contract checks cover non-admin denial and independent outages; those were not simulated by interrupting live shared infrastructure. Production templates/configuration/identities matched the saved deployment baseline. Production remains outside this release authorization.

Private probes and screenshots: `/tmp/sn-reliability-20261015/`. Shared ownership/handoff: `/tmp/sn-reliability-20261015/OWNERSHIP.md`.
