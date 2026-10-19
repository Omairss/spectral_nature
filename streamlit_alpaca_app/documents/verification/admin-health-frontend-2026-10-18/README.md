# Admin/Health frontend release — October 18

Status: deployed and verified in development. Production is outside this release.

The database continuation updated API/workers but omitted the matching Next.js frontend. The deployed frontend image `native-workbench-web-dev@sha256:ba0aa2bd6912eae732b95f122ac02b1d2cd7a6133b798c58c5691c33176ef0b6` serves `/admin` but its compiled route manifest has no `/api/admin/health`. A fresh authenticated browser reload also has no Health tab. An older open tab retained the October 16 Health UI/data in browser memory; that cached view was not proof of current deployment.

Current committed Git already contains the Health UI and authenticated API adapter. Compared with the deployed compiled route manifest, it adds exactly the Health API route and removes no route. No frontend application source, API, database, research/model code or unaccepted Trading work is changed for this repair. The current Git frontend retains its session refresh/outage behavior and all existing routes; the additional Health section reads telemetry through the shared API. Eight existing authentication checks and TypeScript checking pass.

The supported release command now packages the web role through `Dockerfile.web`. Its pinned Node dependency supplies no historical application code. Every frontend input is verified against the committed Git manifest before compilation, and every compiled runtime file/symlink is inventoried for subsequent immutable-image verification. Source acceptance uses the web input inventory for this role and preserves the independent Python runtime inventory. Thirteen release provenance/consumer checks pass, including the web build boundary and complete web input inventory.

Only `sn-next-ui-dev` is eligible for this frontend promotion. Preserve its backend binding, identity, cookies/origins, ingress, resource allocation and scaling. The already verified database/API/worker images and all production consumers remain untouched. The [database continuation](../database-lifecycle-2026-10-18/README.md) remains the authority for its completed execution checks; this release closes its frontend gap.

## Local behavior acceptance

The unchanged frontend ran from the accepted Git worktree at `127.0.0.1:3128`, using an isolated loopback backend fixture with no live credentials, database or model calls. The Health adapter returned HTTP 200. Personal browser inspection verified the Health tab, token/attempt totals, publication table, provider table, connection table and pending recovery state. Switching resources rendered container memory in MiB, CPU in cores and replica history, with allocation shown separately. Existing Admin sections and navigation remain available. The fixture establishes rendering and adapter compatibility; actual authorization, telemetry and freshness require the deployed authenticated check.

## Development acceptance

Frontend `accepted-web@sha256:91730986547ca814a857528f7ca6d536dcf75b03db694ed9ed4a3f8b0679033d`, built from Git `21717503`, is running Healthy as `sn-next-ui-dev--0000024`. [Packaged-image verification](packaged-image.json) covers 85 Git frontend inputs, 1,694 compiled runtime files, zero symlinks and all 48 generated routes. Compared with the [previous image](previous-frontend-routes.json), the only added route is `/api/admin/health`; none is removed. Node remains v22.23.3. [Deployment](development-deployment.json).

A fresh reload in the existing authenticated admin session exposes Health and retrieves current database-backed observations. The 24-hour view displayed 2,710 attempts, 13 failures, 69,967,506 input tokens and 3,839,762 output tokens, together with separately reported cached/unknown/unpriced usage. These are existing account observations, not inference initiated by this frontend repair. Database CPU, database memory, server connections and rejected-connection charts render actual Azure data. Selecting `sn-next-ui-dev` renders its actual container CPU, memory and replica charts with the unchanged 1 CPU / 2 GiB allocation. No collection errors were displayed. [Authenticated browser observation](authenticated-ui.json), [Health screen](development-health.png), [database charts](development-database-metrics.png), [container charts](development-container-metrics.png).

Anonymous `/api/admin/health` now returns **401 instead of 404**, preserving access control. `/admin` and `/health` return 200; Home returns 200 with the exact prior publication, graph, summaries, six beats and daily datapoints. [HTTP checks](http-checks.json).

The [33-resource audit](configuration-audit.json) verifies **one image change**, on `sn-next-ui-dev`, and **zero configuration changes**. Existing API/worker images, all production images, domains, identities, origins, backend binding, allocation and scaling are retained. No model calls, new infrastructure, credential changes or notification sends were initiated. This acceptance covers the missing frontend consumer; it does not certify lower production database utilization or a new research-quality result.

## Integration and cleanup

The reviewed release branch is merged into the main checkout. [Integration proof](source-integration.json) verifies the complete reviewed committed tree, fourteen unrelated dirty files and exact preservation of other sessions' edits in both shared notes files. No active work was stashed, reset or added to the image. The local preview tab and its two owned loopback servers were closed after verification; the actual DEV Admin tab remains open on Health.
