# Daily highlight context repair

Updated October 17, 2026. **Current state: fresh development execution succeeded; complete subjective quality failed, so new builds and production promotion are held.** Read the [full review](../verification/daily-highlight-context-2026-10-17/DEVELOPMENT_QUALITY_REVIEW.md) and [complete output](../verification/daily-highlight-context-2026-10-17/development-home-publication.json).

## Goal and cause

Restore current-data highlights after the exact per-consumer production rollback. Production has a fresh October 17 brief, audio and intraday histories; its retained five highlights are October 14. The historical chart generator embedded its complete catalog into direct inference with `max_tool_calls=0`. The current production request reached 1,887,304 tokens against a 1,048,576-token limit. Both the actual historical Home image and the earlier successful producer use that same selector: an immutable rollback cannot change this input-size limitation.

Use the existing shared Zopedia supplied-evidence contract. Retain the complete catalog and observations, let the LLM navigate and choose, and carry its chosen originals into synthesis, review, checkpoints and provenance. Preserve exact plotted observations, the original dedicated chart review/revision, valid-draft retention, configured model and 600-second operation budget. No feature-local model, financial classifier, ticker ranking, phrase filter, added reviewer or semantic admission gate.

## Committed implementation

Source `6c428b3b` changes four runtime files: chart adapter, shared inference syntax recovery, structured-engine request/recovery ownership and shared agent evidence handoff. Syntax/schema recovery carries the original task/evidence. Planner and final-artifact schemas remain separate. The LLM's selected source references resolve through invocation-owned permitted evidence before synthesis. The accepted no-tool Home editorial request and original planner prompt are unchanged. The chart task removes accumulated preferences and prohibitions, retaining purpose and output shape.

Both normal Home and the independent daily-datapoints job call the corrected selector. The complete source also explicitly reconciles the previously accepted database lifecycle/cancellation repair. Web, modern API/iPhone gateway, Trading, broker and other workers remain outside this rollout.

## Evaluation and adverse evidence

Preserve frozen original inputs, complete provider requests/results and failed experiments privately. The first navigation attempts produced invalid IDs or repeatedly read metadata without originals. A provider-level schema-validation prototype changed partial-draft/call ownership and was reverted. An added planner paragraph did not establish original reads and was removed before packaging. A temporary direct-prefetch prototype was replaced by the existing evidence-history handoff. Carrying the original task to chart review alone did not restore good prose. These results do not authorize publication or more prompt rules.

The final current-data local replays completed in 201.41/269.01 seconds. Maximum individual requests were 140,297/150,906 tokens. Personally read both complete five-chart outputs and original histories. The production replay improved substantially; the held-out development replay retained an overly categorical Micron interpretation. Local source acceptance permitted narrowly scoped DEV verification, with full fresh-output acceptance still required before production.

Focused mechanical checks passed for transport/context ownership; one outdated fake-Blob fixture was excluded. They do not grade research. Full originals were retained beyond 1,200 observations; the unchanged modern API separately displays at most 1,200 points per highlight. Current caption comparisons lie inside that window.

## Actual development release

Build revision `18322b68`, ACR run `cj179`, image `accepted-worker@sha256:b334fa9aa367a4a6c1f927bf9ad4a1eaccfba7f6359f241401db9c9fafcad1fe`. In-registry verification matched all 197 runtime files and all 93 installed dependency versions/metadata hashes. The complete application inventory has 312 files. No inherited application source survives the dependency image.

Only `attention-home-next-dev` and `daily-datapoints-next-dev` received that digest. All 33 resource configurations and the other 31 images remain unchanged, including production. The independent chart job succeeded and published five fresh highlights against the earlier DEV brief.

The normal Home execution `attention-home-next-dev-4js1f7n`, root `035fc67f-1315-439f-a72b-a045a4f853a3`, published its own new Home and five charts at 23:35:23 UTC. Its catalog has 516 candidates and 107,971 observations. All 12 ticker enrichments and the public editor completed; two focused investigations timed out, and ticker answer review remains partial. Core publication, visible browser data, audio, actual intraday histories and exact chart data are verified. All five optional page summaries completed; the full job succeeded at 23:42:39 UTC after 40 minutes 18 seconds.

## Failed quality condition and continuation

Read the complete seven-story brief, overview, all five captions and both chart-review feedback rounds. The overview still exposes CPI bookkeeping, IREN excludes rates too broadly, and the Micron caption retains unsupported forecast-cut language despite final feedback. Chart generation success and exact data do not satisfy the owner's full subjective condition. `infra/release_acceptance.json` records development as not accepted and holds new builds/promotions. This candidate changed no production image, Home publication or highlight selection. Existing macro jobs had refreshed upstream provider observations in both namespaces before image validation; that data refresh used their unchanged runtimes and is separate from promoting this candidate.

Preserve the failed output. Next investigate the complete first-draft/critic request, retained evidence and resulting revision at the shared editorial boundary through paired full-output review. Keep interpretation probabilistic and LLM-owned. Do not stack instructions, hardcode a correction for these examples or manually edit published prose.

Production authorization remains conditional: only after a fresh complete development publication matches or exceeds the pre-incident quality bar may the exact DEV digest be promoted to the single Home producer and its existing daily-chart job be executed without changing the permanent template. That condition has not passed. The saved historical rollback remains live.
