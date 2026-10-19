# Complete reliability restoration — October 18–19

The four omissions identified in the [completed comparison](../rollback-consumer-comparison-2026-10-18/README.md) are restored in production. The owner authorized this release with “Restore it then and don't you fucking make a mistake again.” The release preserves the current Home/research, paper Trading, learning, authentication and Admin/Health behavior. It does not restore an old application tree or incorporate the concurrent cleanup.

## Released behavior

| Production job | Restored contract |
| --- | --- |
| `universe-builder` | Managed transaction/admission/cancellation, conditional Blob publication, recoverable SQL indexing and durable completion receipts |
| `fundamentals-quarterly-refresh` | The same lifecycle/publication/completion handling, preserving its existing producer and upstream/bundled data paths |
| `system-health` | Managed connections/completion and Blob-first metadata reads, preserving monitoring rules, incident state and alert delivery |
| `robinhood-main-sync-prod` | Managed shared-store connections and explicit background classification, preserving queue transactions and account cadence |
| New `system-recovery` | Production-scoped maintenance every fifteen minutes at minutes 7, 22, 37 and 52 UTC; its own 60-second budget and durable cursor, separate from health observation |

The worker image is `accepted-worker@sha256:3ab50680bb7ce47ca01dbc029900f98d1a2bbaa15a0a380f56a034f15d5ed2cd`; broker is `accepted-broker@sha256:bcf893e2b0d8d5677dc0177fb1098bfa80104d7f70392666390d08436a379ef2`, both built from Git `00560e7c49e00b82e33be7226df1eb58503cb4a3`. Registry: `snpipelineacr03130136.azurecr.io`. They are the exact [development-accepted images](DEVELOPMENT.md). Every one of their 199 packaged application files matches Git; dependency metadata, imports, manifests, image configurations and entrypoints were verified. [Package proof](packaged-images.json).

The new recovery job uses the production worker identity/environment and the tested development recovery allocation of 0.5 CPU / 1 GiB. Azure added only an empty `ephemeralStorage` readback field. The release gate stopped before any existing production image changed; that sole representational addition was reviewed and recorded before promotion. [Canonical definition](production-recovery-definition.json).

## Actual execution and preservation

All four production job identities passed their execution probes. Universe and fundamentals read their existing production publications and acquired real managed background database connections. Health read the current production Home, five Trading rows and jobs/provider state at its unchanged allocation. The broker CLI performed six real background acquisitions against the control database using an empty QA account environment; it did not sync an account or place an order.

Normal production recovery execution `system-recovery-s2dl6ao` completed a 3.122-second batch: four manifests checked, seven pending provider receipts reconciled, no manifest failures. A separate read under that production identity verified the durable cursor at four, production provider scope and the durable completion receipt. Its application status is correctly **Warning** because the full manifest sweep continues in later batches; Azure execution success does not mean the whole sweep is complete. [Execution reports](production-results.json).

[All-resource verification](production-configuration-audit.json) covers all 34 live resources. Across the 33 existing resources there were six intended image changes (two development, four production), **zero configuration changes**, and 27 preserved images. Managed environments, workload profiles, tags, identities, credentials, domains, schedules, allocations and scaling remain equal. The new recovery job is the only added resource. [Production image receipts](production-deployment.json).

Production Home and `/health` return 200. Complete Home JSON and its raw response hash exactly match the pre-release capture (`a5b63b86161adaa8b6e4bf58973a8f6448ddcb88c71bc6320ba4dfbdc456d9b8`). Admin/Health and Trading return the expected anonymous 401. The frontend/API images were preserved. [HTTP verification](production-http.json).

The first automatic scheduled recovery execution, `system-recovery-29873152` at 05:52 UTC, also succeeded. It checked the next four manifests, advanced continuation to eight and reported no manifest failures. This confirms the scheduled production binding as well as manual execution. [Scheduled batch](production-scheduled-recovery.json).

Release validation now reviews the complete consumer inventory. Retained Python images need complete packaged-source proof and matching shared connection/publication contracts; all consumers need an explicit behavior review. A required production recovery binding and all deployment roles are checked before mutation. The release no longer treats an unchanged image as evidence that a shared reliability feature reached it.

## Remaining observations

The monitor still reports the pre-existing Zopedia maintenance failure from October 16: `IdleInTransactionSessionTimeout`. Azure's latest ordinary maintenance execution also failed then; no subsequent maintenance slot occurred over the weekend. The ordinary health and broker executions at 06:00 UTC both subsequently succeeded. Health observed one existing incident, opened/recovered none and sent zero emails; broker enqueued and processed one actual scheduled refresh. [Normal cycles](production-normal-cycles.json). Its next maintenance slot and the next ordinary universe/fundamentals refreshes remain observations to collect. This restoration does not declare that historical incident resolved or claim that production database CPU is already below a particular percentage.

Development published 20 universe rows and 1,053 fundamentals rows in an isolated namespace, verified successful durable receipts and exercised recovery continuation. Real PostgreSQL checks covered active cancellation, transaction rollback, cross-process admission and interactive headroom. No new model calls, model/prompt changes, broker orders, manual email delivery or production data regeneration were needed. Hierarchical summarization remains deliberately off under its existing research findings.

Accepted source was prepared in a clean isolated Git worktree and integrated by fast-forward. None of its changed paths overlap the 121 pre-existing dirty paths. One unrelated test continued changing during observation; its current version was retained rather than reset to force the earlier hash. [Shared-workspace preservation](shared-workspace-preservation.json). Concurrent cleanup/research edits remain outside these deployed images.

The 109 private operator files were content-verified into the external 30-day archive and their temporary directory removed. The existing daily cleanup service is loaded and its installed script matches Git. [Retention record](evidence-retention.json). Application source remains in Git; the archive contains operator receipts and probes, not an application source tree.
