# Deep cleanup development trial

The owner requested reconnaissance, development testing and a two-day dev/production comparison before any production promotion. This record covers the prior local retirement/storage cleanup plus HTTP boundary consolidation. Models, prompts, confidence thresholds, research decisions, tool budgets and broker authority remain fixed.

## Reconnaissance and scope

| Area | Source/consumers reviewed | Decision and prerequisite |
|---|---|---|
| Retired code and duplicated helpers | Complete source inventory; AQL/Home producers, API/DAL views, SwiftUI contracts, imports, notebooks and scripts | Include verified unused-graph retirement and direct implementation imports. Retain active artifact compatibility. See the linked cleanup audit. |
| Connection/transaction ownership | Shared session, auth/broker/chat/knowledge/SAA stores; all API/worker/broker consumers | Include session-owned discard, exactly-once lease return, explicit operational failures and direct public factories. Real PostgreSQL cancellation, rollback/savepoint and cross-process admission checks pass. |
| JSON/data normalization | DAL/query/chart contracts, view services and Home producer | Include the shared recursive serializer. Replay of 3,000 retained fundamental rows is identical; missing/nested-value checks cover intentional fixes. Broker Decimal/UUID conversion remains a distinct typed boundary. |
| HTTP transport | All Next.js API adapters, credential exchange/refresh, CSRF, Home cache/audio and JSON views | Include transport-owned outage/timeout responses and HTTP-owned body parsing. Remove 27 repeated route catches. Keep mutation validation, refresh rotation, credential allowlists, binary audio and public-cache handling. Compare 26 JSON adapter call structures and actual signed-in flows. |
| Research orchestration | AQL timeout wrapper, engine loop, ResearchSession/Budget/WorkScope, retained checkpoints and continuation | Recon only in this trial. Nested timeouts interact with provider deadlines and retained-result handoff. An ownership redesign needs frozen complete investigations and cancellation/continuation comparisons; removing timeout layers by appearance would change accepted behavior. |
| Active semantic heuristics | AQL collector/assembler/shared market helpers and subject construction | Recon only. Their removal changes candidate recall and public explanations, requiring model-owned replacement and personal review of matched full outputs. Software assertions cannot authorize that change. |
| Persistence responsibilities | pipeline_store publication/cache/indexing/job control/approvals/provider accounting/checkpoints, ResearchSessionStore | Teardown is simplified. Separating these owners further needs transactional/publication and recovery replay, with optional SQL/telemetry failure preserving completed work. No universal fallback/retry manager. |
| Account email and identity | auth_service invitation library/assets/previews vs auth sessions/account operations; auth_store admin aggregation | Templates and migrations still have live consumers. Separation requires actual preview/delivery/access review; no email is sent by cleanup verification. |
| Clients and provider conversions | Next.js route adapters, unchanged web rendering/SwiftUI DTOs, dedicated broker service/store | Keep broker isolation and read/review/execute permissions. Native state/CSS and provider conversion consolidation need rendered and numerical comparisons before further removal. |

The accepted reasoning constraint keeps this first comparison attributable to structural changes. Deeper semantic and execution redesign remains recorded work, not an unreviewed addition to this trial.

## Current environment baseline

[Live consumer inventory](consumers.before.json) captures all 34 consumers, following ARM pagination. Fourteen are development consumers: frontend, API and twelve jobs. Most development producers are manual; the broker development cron runs only on January 1. Evidence indexing runs every five minutes. Production has twenty consumers. Their immutable image/configuration baselines are retained privately. Production's existing October 16 Zopedia maintenance failure is a baseline incident, not a newly introduced cleanup failure.

[Pre-release HTTP observations](http.before.json) show both frontends/Home responding 200, signed-out session reads 200, and protected Health/Trading adapters 401. Development and production Home artifacts already differ; byte equality between those separate datasets is not the acceptance criterion. Replaying the same retained input must preserve its content.

## Local verification before packaging

- 57 database lifecycle cases pass with no skips against an isolated PostgreSQL 17 cluster bound only to loopback. This includes the four previously skipped real SQL checks.
- 39 feature-request, publication, serializer, auth-store and settings cases pass; real feature-request persistence/ownership checks use the isolated database.
- 13 frontend auth/HTTP boundary checks pass: origin/CSRF enforcement, refresh rotation and outage retention, credential projection, body validation/limits, timeout/outage classification, safe error messages, upstream status/cache forwarding and mutation idempotency.
- Frontend TypeScript unused-local/parameter checking passes. Source comparison preserves outbound calls and validation calls across 26 JSON adapters; only their repeated exception scopes and parser ownership change.

Previous broad historical fixture failures remain documented in the [cleanup audit](../../architecture/cleanup-2026-10-18/README.md). They cannot justify restoring retired prose suppression or changing accepted Trading approvals.

## Two-day comparison

The 48-hour clock begins after all fourteen development consumers run the same verified candidate and development acceptance is complete. Record exact UTC start/end and digests in `trial.json`. No timestamp is assigned before acceptance.

Use existing Azure metrics/job execution history and application publication/research/provider receipts, plus the bounded [cloud observer](observe.py). The observer is a manual one-replica development verification job, running the accepted worker image with a reviewed operator command. It collects every fifteen minutes for an explicit maximum of 48 hours, writes only to a dedicated development verification Blob prefix, and exits at the end. It never deploys, starts research, sends notifications or modifies product records. Its short qualification run must produce an actual stored sample before the live window starts. The job definition remains idle after completion and is retired when the trial is reviewed. Missing samples or unavailable observations remain explicit coverage gaps. Record actual dev workload coverage alongside production activity; an idle development job or a missing collection is inconclusive. Exercise representative producers and explicit research runs in development, with matched inputs/settings for output comparisons. Compare complete research outputs personally, including retained sources and successful findings under source/provider failures. Ordinary model wording variability is separate from lost evidence, missing results or failed continuation.

Review HTTP availability/status/latency, sign-in/session refresh/permissions, Home replay/audio and charts, portfolio/broker read boundaries, dataset timestamps/publication completion, research progress/checkpoint/continuation, provider failures/tokens/cost, failed/queued executions, database wait/transaction lifetime and resource use. New authorization failures, lost work, corrupt/missing publications, duplicate side effects or unexpected production changes fail the trial immediately. Performance comparisons need comparable workload and distinguish cold starts from sustained changes; absence of error logs alone cannot pass the trial.

Any material repair produces new candidate digests and restarts the complete observation window. Production promotion requires completed observation coverage and review of the exact development images under the owner's conditional instruction. Until then, production release authorization is false.

## Status

Preparing source acceptance and development deployment. No cleanup image has been built or deployed yet. The observation window has not started.

The additional `cleanup-trial-observer-dev` verification consumer is a manual job, using the completely verified candidate worker and existing pipeline identities, registry and secret resolution. It has no recurring schedule, new role grants, product writes or notifications. Its 176,400-second execution limit bounds the 48-hour observation; fixed start/end times survive a restart. The default command refuses to run without the reviewed explicit observation override. HTTP, database aggregates and actual Azure job execution statuses/images are retained in development Blob storage. Its canonical configuration and preservation review are recorded in [observer-definition.json](observer-definition.json). It becomes idle after the run and is retired when the comparison is reviewed.
