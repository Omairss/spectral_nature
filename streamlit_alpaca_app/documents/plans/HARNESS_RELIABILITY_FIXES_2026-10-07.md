# Shared harness reliability fixes

Requested October 7, 2026, after the model/harness audit. Status: locally accepted
and deployed to development and the modern production workbench.

## Scope and design

1. Validate the answer-review JSON against its existing schema. A missing verdict,
   wrong type, or incomplete correction is a failed review, never implicit approval.
   Keep the draft answer and actual review status. Allow automatic memory reflection
   and retained-answer write-back only after a completed accept/revise review.
   Keep conversation history available; do not convert an unreviewed draft into a
   retained research claim. The API preserves review status and the research view
   identifies a draft without adding a disclaimer.
2. Separate collected evidence from planner/UI previews at the shared tool-result
   boundary. Preserve complete tool-supplied passages and all summary items with
   source references. Synthesis, review, and memory receive that evidence without
   the planner's second character cut. Retained prefetch preserves full selected
   chunks for these phases. Approved retained write-back also keeps the complete
   collected text and references. Acquisition limits still apply; this is not a semantic
   compaction or large-document retrieval rewrite.
3. Propagate one monotonic step deadline through the gateway into provider adapters.
   Every HTTP attempt uses the remaining budget, including transport retries,
   unsupported-setting retries, and JSON repair. Reject late results and do not
   begin new work after expiration. Keep existing client signatures compatible by
   using a call-scoped context rather than mutating a shared client configuration.
   Requests socket timeouts are not hard wall-clock cancellation; full tool/process
   cancellation and checkpoints remain separate architecture work.

## Acceptance and release

Protect schema, persistence, evidence preservation, and deadline contracts with
focused plumbing checks. Personally read live final/review output from controlled
long source passages and conflicting evidence. Disable persistence and knowledge
writes for probes; do not insert an owner-account research conversation. Inspect
the draft state locally before building. Deploy isolated development images only,
preserve all protected runtime settings, and record exact rollback receipts.
Production promotion was separately authorized; see the release record below.

## Local findings

- Personally read three live final answers and four live reviews. Each source set
  contained 13 items and 5.5–5.7k characters. The decisive correction was in item
  13, beyond the old planner preview. All three answers used it: 30% orders versus
  8% revenue, a $120m order due next year versus established business context, and
  correlation versus causation. The reviewer also corrected an intentionally false
  30% revenue draft. This validates these context paths, not general model quality.
- The configured `deepseek-reasoner` again reported `deepseek-flash`. No model
  migration was made. Outputs still show repetition and excessive scope caveats;
  improved concision remains separate from these execution/context fixes.
- Evidence and complete outputs: [controlled live review](../architecture/AQL/audits/HARNESS_REVIEW_2026-10-07.json).
  No owner-account research run, conversation, or knowledge entry was created.
- All 107 focused plumbing cases passed across agent, provider, gateway, harness,
  API and chat-log suites. The final harness/agent run passed 71 cases. The 26
  harness contracts also pass on the older job baseline with the same patch
  applied. TypeScript and targeted lint passed. These checks cover schema,
  persistence and time budgets; the live output review covers prose.
- Inspected the real Next view through a loopback API fixture using the actual
  typed response model. The draft remains readable with one review-state label:
  [local screenshot](../architecture/AQL/audits/HARNESS_DRAFT_LOCAL_2026-10-07.jpg).
- Remaining boundary: Requests timeouts are socket timeouts, so an active response
  can outlive the outer deadline. Late results are rejected and retries/repair
  cannot reset the budget; hard cancellation still needs a different execution
  owner. Source-acquisition limits and planner previews remain bounded.

## Development release

- API `sn-api-next-dev--0000015` and frontend `sn-next-ui-dev--0000012` are ready
  and each receives 100% of latest-revision traffic. Updated all eight non-broker
  development job templates. No jobs were started for acceptance.
- Base-source checks and imports passed for every image. Trading and macro images
  use an older agent baseline and lack the research-run API module. Inspected the
  actual baseline, applied the exact shared source patch with zero fuzz, checked
  its 26 contracts, and excluded the unused API module from those two bundles.
  This preserves their unrelated baseline behavior and dependencies.
- Runtime check on the new API confirmed the agent source hash, typed review
  state, failed-review memory guard, health 200 and unauthenticated Admin 401.
  Home returned 200/available, the signed-in Admin reloaded, and the delivered
  frontend bundle contains the draft label. The draft edge state was personally
  inspected through the local fixture before building.
- All ten targets preserved protected configuration, including secrets,
  identities, commands, schedules and resources. Compared production API/frontend
  configuration and images against the pre-release snapshots; unchanged.
- Exact new and rollback images, successful builds, source hashes and acceptance:
  [development release receipt](../architecture/AQL/audits/DEV_HARNESS_RELEASE_2026-10-07.json).

## Production promotion — authorized October 7

The user explicitly requested production deployment. Promote the accepted language,
settings and harness changes together. Reuse the exact accepted frontend and worker
digests where their original production bases match. For older production API and
worker bases, verify the source hashes before overlaying only these accepted changes;
preserve unrelated production data-layer behavior. Do not publish concurrent native,
discovery or Home changes from the working tree.

Update both production frontend entry points, the private production API and
the fifteen non-broker production worker templates. Preserve credentials, provider
selection, commands, schedules, resources and routing. Add `APP_TRACK=production`
only to production workers missing that label so their shared LLM settings use the
same scope as production Admin. Check the active production language rule; reset
only its scoped override if needed to activate the accepted 80% default. Do not
copy development settings or start acceptance research/jobs in the owner's account.
Capture exact previous images/configuration, check readiness and live authenticated
UI behavior, and record the final release and rollback receipt.

Preflight confirmed that `sn-api` is a separate older public stack, with no
research-run API module and substantial differences from the accepted application.
It is excluded from this promotion. Both modern production frontends route to
`sn-api-next`. Their exact older base passed all accepted overlay source checks.
The legacy worker's actual agent also accepts the shared patch with zero fuzz;
its older style and DeepSeek response handling are superseded by the exact accepted
shared provider adapter. All other worker modules remain on their original base.

## Production release

- Ready private API: `sn-api-next--0000003`. Ready frontend revisions:
  `sn-streamlit-ui--0000088` at https://spectral-nature.com and
  `sn-next-ui--0000004`. All three receive 100% latest-revision traffic.
- Updated fifteen non-broker production worker templates. Thirteen previously
  unlabeled workers now explicitly use `APP_TRACK=production`; their existing
  production data namespace is unchanged. All other configuration is preserved.
- The running API matches the accepted agent hash, uses the 80% default language
  rule in production scope, preserves typed draft/review status and rejects failed
  review for memory promotion. Health returns 200; unauthenticated Admin returns
  401. No settings reset was needed and no production settings were written.
- The authenticated production Admin shows editable model/language controls and
  the active default. The custom-domain root, health, Home and Zopedia return 200;
  Home is available. The older worker projection passes all 26 harness contracts.
  No acceptance research conversation, knowledge entry or job execution was made.
- Eighteen targets updated; sixteen excluded resources, including development,
  both broker jobs and the separate legacy public API, retain their original image
  and configuration. Model selection remains `deepseek-reasoner`; this promotion
  does not resolve the previously observed Flash alias behavior or hard cancellation.
- [Exact release and rollback receipt](../architecture/AQL/audits/PROD_HARNESS_RELEASE_2026-10-07.json).
  Roll back each listed target to its exact previous image and remove only the
  `APP_TRACK` entries recorded as added. No credentials or schedules need rollback.
- [Production Admin proof](../architecture/AQL/audits/PROD_LLM_ADMIN_2026-10-07.png).

## Home research acceptance gap — October 8

The next-day [production Home audit](../architecture/overall/audits/HOME_EXPLANATION_FAILURE_2026-10-08.md)
found twelve of twelve ticker enrichments timing out, a failed market summary,
and skipped group synthesis while Home remained `available` and its job reported
`Succeeded`. The same twelve-ticker failure exists in pre-release artifacts.
The release checks above establish the stated shared changes and service health;
they do not establish successful scheduled Home research. Shared run budgets,
cancellation/continuation and event-level intake remain open. Future acceptance
must personally inspect scheduled product output and its retained sources.
