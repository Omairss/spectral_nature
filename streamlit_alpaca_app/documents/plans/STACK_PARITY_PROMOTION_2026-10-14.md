# Development-to-production stack promotion

Status: owner-authorized production code promotion is complete across eighteen targets. Infrastructure, configuration, HTTP and retained-data checks passed. Live research acceptance is blocked by DeepSeek timeouts and HTTP 503; a provider decision was requested. No successful generated answer is claimed.

## Scope

Promote the currently deployed and accepted modern development frontend, private API, queued research worker, shared Zopedia conversation/engine consolidation and LLM cost restructuring. Preserve the accepted Attention cards. Include every active non-broker production pipeline consumer of the shared research code rather than leaving older scheduled consumers behind.

The research-depth and DeepSeek model-split proposal in the conversation repair plan is not implemented and is outside this promotion. Native device installation is outside this server release. Production data, identity, credentials, broker connections, schedules and execution limits remain production-owned.

## Release method

1. Snapshot live application and job definitions privately and record exact rollback image digests.
2. Read source and image defaults from the actual deployed development and production images. Reuse accepted immutable development images where runtime and source compatibility permit; preserve production-specific accepted dependencies when a source-guarded image is necessary.
3. Verify shared source identity, imports, production namespace and private/public artifact ownership before changing production.
4. Promote queued workers and the private API before exposing the conversation frontend. Update all applicable pipeline templates, preserving protected configuration.
5. Verify ready revisions, traffic, exact custom domains, authenticated Home/portfolio/conversation reads, a controlled queued conversation and its follow-up, and the shared engine revision used by the worker. Personally inspect the complete generated answers and conversation rendering.
6. Record the release, remaining limitations and rollback instructions; update deployment and learning records.

Evidence directory: `tmp/stack-parity-prod-20261014/`. Full resource snapshots and private conversation artifacts are local files with restricted permissions. A concise non-secret release receipt will be retained under `documents/verification/`.

## Compatibility review and prepared images

- The frontend and private API can use the exact deployed development digests, retaining their production API URLs and environment settings. The queued research, equity, macro, Home and Trading Agent counterparts can also reuse their accepted development images.
- Eight older general workers and the quarterly fundamentals worker retain their original production baselines. Three successful builds (`cj14c`, `cj14a`, `cj14b`) apply 41 accepted shared research/cost/conversation files to the general, fundamentals and health image groups respectively. Every overlaid file is guarded against its actual deployed source before replacement.
- Production's health worker has newer execution reconciliation than the modern development worker baseline. Its monitoring source is explicitly preserved, including exact Azure execution matching and namespace isolation. Copying the whole dev worker image would have removed that accepted repair.
- Local projections import the real pipeline entrypoint, preserve the existing unprefixed production storage contract and report shared engine revision `7242a828ab189e15faa4`. The image builds independently verify these imports and source guards.
- Five public producers (news, macro, equities, Home and Trading Agent) explicitly opt into `public_company_research` artifact reuse, matching the accepted ownership contract. Private API/research workers and knowledge maintenance/learning do not receive that opt-in. Other configuration remains unchanged.

## Deployed and verified

- Both production frontends use the exact accepted development revision 20 image, `attention-fundamentals-dev@sha256:8380f82bf07cc249ee8a6761569c0a344fc30aed162d64ab3c45618292fd2c1d`. Their ready revisions are `sn-streamlit-ui--0000092` and `sn-next-ui--0000008`.
- The private API uses the exact development revision 30 image, `zopedia-conversation-dev@sha256:2e9085fdfb623d13594712d9a879b4e90ab10382e952e5536e700908385f27db`, as `sn-api-next--0000007`. All three applications are healthy, provisioned and serve 100% traffic on their latest ready revisions.
- All fifteen non-broker production worker templates are updated. Five matching workers reuse exact development images; ten workers use the three source-guarded compatibility builds. The ten shared engine source files match the API's fingerprint `7242a828ab189e15faa4`.
- Protected configuration comparisons pass across all eighteen targets. Seventeen excluded resources, including every development resource, both broker jobs and the legacy APIs, compare unchanged.
- The four custom hostnames and the production Azure alias return health and Home HTTP 200. Private conversation and Admin routes return 401 without a session. The signed-in production alias loads the private portfolio and the new conversation UI, including grouped legacy chains.
- Home retains run `3fa5fba2-b392-479e-9404-1760b22c6345`, seven stories and audio. The browser shows twenty ticker cards, seventy financial-history charts and no card selectors or horizontal overflow. The existing zero daily-datapoint count is unchanged; no fresh Home publication or large paid research batch was forced for this deployment.

## Provider-blocked research acceptance

Production conversation `zthread_0b92845c1279420faea7d0cb` exercised a normal public-source comparison through the authenticated browser and actual queued worker. The first turn reached research planning but timed out at the existing ninety-second step allowance. A follow-up remained in the same conversation, retained the failed turn and returned DeepSeek HTTP 503 “Service is too busy.” Both worker results identify the accepted engine revision.

Production and development resolve the same model and budgets: `deepseek-reasoner`, thinking enabled, ten tools, 300-second operation and ninety-second model step. These failures do not establish a deployment-specific regression or successful research quality. The HTTP 503 attempt is present in the new production provider ledger with usage unavailable. The first timeout has no completed provider receipt; its usage is unknown, not zero. Provider/model settings were not changed. The owner was asked whether to retain DeepSeek and retry after recovery or specify another provider/model.

Exact targets, immutable images, previous images, configuration exceptions and live checks are in the [release receipt](../verification/stack-parity-production-2026-10-14/release.json). Rollback restores both old frontends before the old API, then restores each worker's recorded previous image and removes only its newly added public-artifact opt-in. Additive persistence schema and existing conversations/attempt receipts remain intact.
