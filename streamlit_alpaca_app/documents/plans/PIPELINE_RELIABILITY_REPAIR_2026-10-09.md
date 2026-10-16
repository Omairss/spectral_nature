# Pipeline reliability repair — October 9, 2026

Status: operational recovery verified. Full-workload acceptance, production rollout and ordinary final-image monitoring are complete. DeepSeek is restored.

Both full r18 news runs succeeded, taking 106.78 minutes in production and 117.57 minutes in development. All fifty company narratives and twenty-four event interpretations are saved and nonempty. Every event interpretation and seven representative company narratives were personally read; known factual/editorial limitations remain recorded as research-quality issues, not hidden or labeled a semantic pass. The one partial development company investigation retains its complete narrative.

Full r15 Home completed in both namespaces: production Succeeded; development Warning for partial research. Both public feeds have complete stories, five populated charts and playable audio. All ten optional page narratives were personally read. Production maintenance and learning also completed. Each of these six complete workloads has exactly one database attempt under its exact Azure execution and namespace.

All eleven r19 worker variants passed guarded source checks, and all nine development templates are promoted. The first ordinary final-image evidence index succeeded. The metadata-only overlay preserves acquired research URLs in company memory. Actual-image validation preserved nine affected development pages without durable writes; production's eight rejected pages are now committed through the existing audited mutation owner, with all recovered references verified by durable readback. Original full-run reports remain unchanged as historical records.

All fifteen production and nine development worker templates use the verified r19 images, with protected configuration checks passed. All 27 cloud jobs are provisioned. Ordinary final-image health execution `system-health-29862570` succeeded in Azure and the database at 21:30 UTC and reported zero open incidents. Final public reads verified both accepted Home runs, five populated charts per feed and playable audio; all eight application revisions are ready. Future production news and the manual development validation host use a three-hour ceiling; Home retains two hours. Production frontend/API images are unchanged by this repair. The durable evidence receipt has status `operational_recovery_verified`.

Updated: 2026-10-11T21:33:55.067013+00:00

User authorized fixing
all current pipeline errors end to end, including production restoration. Preserve
unrelated workspace changes and each worker's existing provider, identity, schedule
and dataset namespace.

The follow-up diagnosis identified remaining idle-transaction failures in shared
maintenance/learning and news acquisition exceeding its memory allocation. Later
20:05 news and 20:50 maintenance runs repeated those failures. Trading and equity
continued to succeed.

## Owning contracts

- Maintenance and learning own short database phases on an initially idle
  connection. Commit materialized reads before graph construction, model calls or
  replay. Persist coherent results before the next external operation; retain the
  protective transaction limits and atomic knowledge/audit writes.
- Pipeline status uses an independent autocommit connection. Work transactions
  cannot disconnect status recording or have their changes committed by a progress
  update. The work connection is separately owned and closed by the runner.
- The canonical page reader queues browser-backed acquisition at a process-wide
  capacity, independently of high search/LLM parallelism. A persistent authenticated
  profile has one owner. No source, question or symbol is removed to fit memory.
  Profile real browser processes and provision news with sufficient headroom.
- Monitoring reads the latest completed job state, so starting another execution
  cannot declare a failed producer recovered. It ends its read transaction before
  Azure requests and uses bounded standard transport retries for transient errors.
  Audit historical platform executions and reconcile only exact identities.

## Acceptance

Inspect actual deployed sources before creating minimal guarded overlays. Exercise
real PostgreSQL boundaries with accelerated timeouts, real concurrent page reads,
and full affected development jobs. Promote only accepted images/configuration.
Run all affected production paths, inspect their persisted results and public Home,
and confirm recovery in the ordinary scheduled monitor. Recheck the complete job
inventory and new errors before finishing. Record actual limitations, source
hashes, allocations, run IDs and outcomes here and in the deployment tracker.

## Implementation and measured acceptance — 23:17 UTC

The source fixes are implemented. Eleven guarded image variants passed their actual
entrypoint failure/progress/cleanup checks; two additional Home overlays contain
only the sector-label handoff functions. Full Azure snapshots and source hashes
are retained under `tmp/pipeline-reliability-20261009` pending the final receipt.

- Real PostgreSQL temporary-table probes use a one-second idle-transaction limit.
  Maintenance computation, two critic calls and two replay calls run outside
  transactions; actual result/audit writes persist. Separately, an actual work
  connection timeout still produces a durable Failed receipt on independent
  tracking. Temporary monitor rows verify that starting a retry cannot clear a
  failure, later completed recovery clears provider incidents, and dev is excluded.
- Maintenance also had quadratic source matching: every page rebuilt/scanned the
  source catalog. A batch index preserves exact reference identity and order for
  all 1,676 retained pages. Measured reference matching fell from 4.268 seconds to
  0.000762 seconds; the isolated full maintenance probe took 5.6 seconds including
  an intentional 1.3-second delay.
- Real Chromium with 24 concurrent callers completed every source, with at most
  two browsers. The missing description-tag locator wait added 30 seconds to each
  page: removing that wait reduced the local batch from 371 to 10.7 seconds.
  The accepted local parent/child peak is 744,390,656 bytes. The Azure Linux batch
  also completed all 24 pages in 14.8 seconds; a second cloud probe obtains cgroup
  memory because the production image does not contain `ps`.
- The existing model-produced taxonomy contains 6,735 symbols. Joining it at the
  Home numerical coverage owner classifies 998 of 999 current movers. Missing
  labels stay missing; no ticker/sector inference rules were added.
- Development maintenance succeeded (`trading-agent-build-dev-2pxrkv0`). Production
  maintenance succeeded (`zopedia-maintenance-h9di7hu`, run
  `25f3d1cd-2463-4dfd-8d3e-9deee4274b48`) in about 2.4 seconds inside the worker.
- Development learning completed its normal 25-thread scan: four events/four
  generated evaluations/four memory updates/three replay regressions. This proves
  runtime restoration, not research quality. Replay judgments remain recorded;
  they were not changed into successful semantic evaluations. Production learning
  is now running (`zopedia-learning-aun9vhc`).
- Exact Azure execution reconciliation passed under the deployed managed identity,
  with temporary rows and no notifications (`trading-agent-build-dev-a6m2l3x`).
- Ninety historical orphan rows were reconciled only after matching startup UUID,
  logical job/namespace and exact execution inventory. Eighty-five Failed, three
  Stopped and two platform-success-without-receipt Warning records are retained as
  history. Azure omitted end times for 88 entries; those rows explicitly record
  reconciliation time. The 210 unidentified older rows remain untouched.
- Nine development templates are promoted with fresh protected-configuration
  comparisons. The manual ticker producer lacked Key Vault/DB settings; restoring
  that shared runner contract yielded its normal successful 31-row materialization
  (`attention-ticker-next-dev-rbirxn3`). Its earlier failed manual probe is history.
- Full Home development (`attention-home-next-dev-fu5ca7n`), full news development
  (`trading-agent-build-dev-gcuv9yf`) and production news
  (`news-ingest-and-features-f71tiv4`) remain under observation. The first news dev
  donor had default 80 context symbols versus production's 20; it was intentionally
  stopped and restarted with the actual production workload, development dataset
  namespace and no knowledge writes. No production scope was reduced.

Production promotion is proceeding within the authorized incident scope. The
frontend and API images are unchanged. Public Home remains HTTP 200 / available;
this does not establish producer recovery. Do not mark the repair complete until
fresh affected producer results and the ordinary scheduled monitor confirm it.

## News throughput correction — 23:28 UTC

The repaired production reader passed its previously failing acquisition phase:
23 questions, 92 result rows, 42 opened pages and zero acquisition error rows for
the first company. That observation also exposed a separate lifecycle problem:
the 25-company business-research batch runs serially. The first company spends
several minutes in planning/acquisition/dossier/fact resolution before finishing;
a one-hour job cannot safely assume that cost repeats serially 25 times.

The canonical batch now runs four company investigations concurrently, retaining
caller order and every selected company/query. Existing provider throttles and the
shared two-browser capacity still own their separate limits. AQL timeout defaults
now use request-local ContextVar scope rather than temporarily mutating process
variables. The thread pool copies request context. The concurrency probe retains
all 25 inputs and demonstrates four isolated simultaneous timeout values with an
unchanged process environment. Actual-image checks repeat this contract for every
worker variant; no semantic verdict/string expectations are involved.

News receives a two-hour execution allowance to leave latency headroom for its
unchanged work within the existing two-hour schedule. This accompanies the
throughput/capacity fixes; no source, company or research question is removed.
The earlier serial repair executions will be intentionally superseded after the
new variants pass development acceptance. Monitoring now also excludes intentional
Stopped retries from recovery selection, so cancelling a diagnostic execution does
not erase the preceding Failed producer state.

Production learning has completed successfully at 23:24:20 UTC (run
`3563cbaf-4eb6-4af7-9139-242bc0914713`). The Home development candidate stage has
completed all twelve candidates; downstream publication/review remains running.

## Home context assembly correction — 23:42 UTC

The full development Home run completed twelve ticker investigations, four source
investigations and numerical market coverage (`ok`, zero coverage gaps), then its
editor failed before publication. DeepSeek rejected 1,378,458 requested tokens
against its 1,048,576 context window. Its failed input is retained for exact replay;
production Home still serves its previous available snapshot. The automatic retry
of this same development execution was intentionally stopped rather than repeat
an immutable oversized request.

The input contained 17 candidates and 49 intake reports. Collected research reused
782 JSON records (402 distinct) and 1,233 reference records (669 distinct), and
request metadata copied the entire inline request a second time. This is a shared
context ownership error, not a reason to remove candidates or enforce source gates.

Home now supplies typed evidence separately from editorial instructions. Shared
agent prompt metadata excludes the already-present query while durable session and
checkpoint records retain it. Shared model-input serialization decodes nested JSON
captures and represents exact repeated values through a context catalog, retaining
all distinct records/text and their original relationships. Storage serialization
is unchanged. An expansion probe verifies semantic round-trip equality for the
complete failed input (after the existing JSON-safe conversion); its context falls
from 1,924,183 to 1,068,778 characters, with 505 shared values. Real model replay and
qualitative inspection are in progress; this is not yet publication acceptance.

The real editor replay completed both calls: 357,841 synthesis input tokens and
369,056 review input tokens, versus the prior 1,378,458-token rejection. Synthesis
took 156.95 seconds and review 176.26 seconds. The returned artifact preserves all
17 candidate identities, publishes eleven, merges six overlapping interpretations,
and discovers eleven additional events. Personal inspection confirms useful source
synthesis, indirect connections, chronology corrections in review and the OpenAI
financing story ranked near the top. This establishes the repaired representation's
usefulness; it is not a claim that every generated statement/ticker is correct.
The unchanged fallible research model still needs its normal correction/learning.

The measured 333-second two-call path also exceeds the existing 300-second editor
allocation. Its source default, deployment default and Home templates now allow
600 seconds inside the unchanged two-hour job boundary. A fresh full development
Home run uses the accepted image and this allocation:
`attention-home-next-dev-vo0zaor`. Production Home promotion remains pending full
publication acceptance. Shared context overlays for the other workers are rolling
out with fresh protected-configuration verification; active news executions retain
their immutable bounded-batch images and remain under observation.

## Full-run findings and additional owner repairs — October 10, 08:04 UTC

The next observation resumed at 07:47 UTC. The jobs had reached their platform
limits; they were not accepted. Production news completed all 25 researched
profiles at 00:51, then remained in its serial 12-event interpretation phase until
Azure killed it at 01:30. Development news completed sixteen profiles; its last
logs show Patchright/Node assertion failure during a page crash, followed by no
progress until its platform limit. This separates a remaining serial phase from a
browser-driver lifecycle failure. No result is relabelled successful.

Development Home completed editorial synthesis/review and published its core
snapshot, bundles and review artifacts. Its legacy 1,800-second platform limit
then interrupted optional page summaries at 00:18. Production Home's limit was
7,200 seconds. Correct the development configuration and validate the complete
worker; core publication alone does not establish completed job acceptance.

The shared browser owner now gives each browser attempt an owned child process
and process group. Normal navigation settings and fallbacks are retained; a
separate 90-second worker deadline covers multi-step login/navigation/cleanup.
The parent kills and reaps the driver and its descendants even when Node or
browser shutdown fails. Source requests still queue at bounded capacity. An
actual hung parent/descendant probe verifies termination and subsequent recovery;
24 real Chromium requests then all complete with four admitted browser attempts,
a 1,706,164,224-byte local parent/children peak and 9.114-second batch duration.
Linux/cgroup validation completed in execution `trading-agent-build-dev-t2bhbd1`: all 24 pages completed in 26.454 seconds after forced hung-process recovery, with a 911,605,760-byte cgroup peak. News receives four browser slots; Home retains two browser slots and receives 2 CPU/4 GiB for
worker headroom. Both Home environments receive the existing two-hour job limit.

The remaining shared news-interpretation batch now runs four independent events
concurrently, preserves all twelve selected rows and caller order, and copies
request context. A scheduling probe verifies those boundaries without semantic
verdict expectations. The canonical news producer yields its coherent completed
company-research phase for persistence before starting interpretation. It retains
the final merged knowledge commit as an atomic owner operation. Unexpected phase
and provider failures propagate to independent tracking; a failed memory commit
retains its report and then fails the job. Useful research artifacts are retained
without calling a failed workflow successful. An injected later-phase failure
confirms delivery of the completed first phase and exception propagation.


## Capacity headroom and retrieval cancellation — October 10, 08:27 UTC

Azure's whole-worker peak for the previous development news run was
4,378,050,560 bytes, against its 4 GiB allocation. Production reached
2,887,794,688 bytes. The development driver crash occurred near exhausted memory;
the kernel's precise child termination reason is unavailable. Provision news at
4 CPU/8 GiB with four bounded browser attempts. Do not trim the company/source
workload. The early new 4 GiB diagnostic run was intentionally stopped and
superseded by `trading-agent-build-dev-0t62etq` at the verified 8 GiB allocation.

Inspection of the earlier Home summary logs also found repeated semantic retrieval
failures after a tool's caller deadline expired. Retained search caught that
cancellation as provider degradation and scheduled more queries. Shared retained
search now checks the caller clock at entry, before queued searches and when
handling planner/provider/search exceptions. A provider's shorter private timeout
still permits lexical fallback while the caller remains active; caller cancellation
propagates. Reader-owned SQL connections use autocommit so embedding latency cannot
hold an implicit transaction open. Connections supplied by callers retain their
existing transaction ownership. Neither retrieval ranking nor evidence selection
changes. Per-variant guarded overlays preserve the legacy and modern search APIs.

Full development Home (`attention-home-next-dev-0fbffb2`), full development news
(`trading-agent-build-dev-0t62etq`) and an actual production-image read-only replay
of all twelve news interpretations (`trading-agent-build-dev-k2rae2n`) are under
observation. Production promotion and complete-run acceptance remain pending.


## Remaining model-input path and external billing stop — October 10, 08:38 UTC

The new full Home run exposed another model-input ownership path. A source-event
agent failed after retained search with a 1,222,614-token planning request. Its
retained result contains 155 candidate sources and repeated source references;
the planner history was 3,281,031 characters. The full-evidence synthesis path
already used shared context, but the planner's candidate catalog and retrieval
metadata still used raw JSON. The shared tool-history owner now represents both
paths with typed, factored context. The actual failed history becomes 1,026,175
characters with 417 shared values. Expansion preserves all 155 candidate records,
their complete contents and relationships. Including the original query gives
1,262,589 characters before small surrounding instructions. This is measured
representation acceptance; real model replay is pending.

DeepSeek began returning HTTP 402 insufficient funds at approximately 08:28 UTC.
It failed the last two Home ticker enrichments and both publication attempts.
An actual configured-cloud search still returned retained results through its
fallback, but its planner recorded the same billing failure. The previous
successful twelve-event production-image interpretation replay completed before
this outage: 12 nonempty stories in 653.99 seconds, inspected personally.

The full development news, development Home and newly started production news
executions were intentionally stopped after confirming the billing failure:
`trading-agent-build-dev-0t62etq`, `attention-home-next-dev-0fbffb2` and
`news-ingest-and-features-o5ltlgp`. These are not successful full-run acceptance.
The provider's credit restoration or a user-selected replacement is required;
a request is pending. Maintenance and learning retain successful production
receipts. Production Home is still on its previous available publication.

Release r7 is verified on all nine development and fourteen non-Home production
worker templates. It includes process ownership, completed-phase persistence,
remaining phase parallelism, context factoring for full evidence and read/caller
cancellation ownership. Production Home promotion remains held. Release r8 adds
the repaired shared planner representation; build/representation verification
and development rollout are proceeding while model-backed acceptance is blocked.


At 08:44 UTC, all eleven r8 compatibility builds passed their actual-image
representation checks, and all nine development templates were freshly verified
on the pinned r8 images. Fourteen non-Home production templates remain verified
on r7; production Home remains held on its earlier image. The production-configured
read-only provider probe `trading-agent-build-dev-nlrkaxf` completed its diagnostic
process and reported HTTP 402 insufficient funds at 08:41:44; its Azure Succeeded
status is not a successful research/producer receipt. All 27 job resources are
provisioned and all eight applications have running, ready current revisions.
Public Home returns HTTP 200/available from run
`783d11e2-603d-492b-9543-c6931c1bced9`. The previous news failure incident remains
open. No complete pipeline recovery is claimed.


## Credit restored; complete-run acceptance resumed — October 10, 11:20 PDT

The user restored DeepSeek credit. Production-configured diagnostic execution
`trading-agent-build-dev-25f9uua` returned a completed model response with
`ok: true` at 18:19:28 UTC. This establishes provider connectivity, not producer
acceptance. Complete r8 development news (`trading-agent-build-dev-53tco9z`,
run `f7d76374-d187-4960-a87d-3bdc7286925a`) and Home
(`attention-home-next-dev-4uxnz6n`, run
`77fd1408-1a05-4abb-9941-efca331624ae`) are now running. Their full workloads,
namespace isolation, knowledge-write policy and resource headroom are preserved.
Production still retains r7 non-Home workers and the earlier Home image pending
completed execution and qualitative review.

At 11:23 PDT, a model replay using the exact r8 worker prompt owner and the
original failed query/tool results completed in 37.658 seconds within its
60-second allowance. The full input was 1,270,939 characters. Its complete answer
was inspected: it uses retained program details, connected companies and an
independent index event while identifying unknown funding terms. The original
155 candidate sources remain preserved. Existing verbosity and causal-strength
limitations are not presented as repaired. This accepts the failed planning
boundary; complete producers are still under observation.

The full production news acceptance execution is `news-ingest-and-features-3jgeain`
(run `a16c035d-1bf1-4fed-beda-ddb7104f8c0f`). It uses the pinned r8 execution
image with the unchanged production workload and settings; the scheduled template
stays on r7 until acceptance. The first resumed development news memory sample
peaks at 5,933,084,672 bytes against 8 GiB. All four source-event syntheses completed
in development Home; two reviews reached their existing deadline and retained
their valid draft. Neither a draft nor an active execution is treated as completed
producer acceptance.


At 11:48 PDT, the r8 development Home core datasets persisted. All twelve candidate
investigations, four source events and twelve ticker enrichments completed. The
shared editor and final review completed (23 entries reviewed), taking approximately
186 and 89 seconds. The persisted six-story feed and market summary were personally
read: substantive source-event explanations, related companies and business context.
Four individual ticker answers retain incomplete review state; the final Home
review completed. This remains an honest research warning, not an execution failure
or a reason to discard valid research. Optional page summaries continue under the
full two-hour worker allowance. Production Home validation uses an execution-only
r8 image/resource override; scheduled template promotion remains pending.


At 11:54 PDT, development Home completed its full worker execution: Azure Succeeded,
database Warning for the four incomplete individual ticker reviews. All five optional
page-summary rows persisted; four are usable and one (NVAX) is unavailable. The public
summary contract intentionally omits internal failure details, so an exact persisted-
context replay on the pinned worker is investigating that outcome without publishing
datasets or writing knowledge (`trading-agent-build-dev-z04svca`). Production Home
validation is `attention-home-build-8prcyou`, run
`dfd02f22-fc40-4ce6-af5f-bc94027e25bd`.

The optional NVAX replay completed in 188.52 seconds without provider/runtime error.
Its body largely repeats trailing returns, so this is not accepted as evidence of
strong page-summary quality. The original unavailable public row remains unchanged;
its internal reason was not retained in that display contract. This repair does
not claim to resolve every existing editorial or mapping weakness.


At 12:13 PDT, the final r8 production learning execution completed
(`zopedia-learning-mqaoe3n`, run `acae8a55-639d-4022-8477-57c7c31ee66b`):
25 threads, four events, four evaluations and four updates. The same three existing
replay regressions remain recorded; runtime success is not semantic acceptance.
Final r8 production maintenance also completed (`zopedia-maintenance-9w72qhm`,
run `3acbf2c5-a157-4e83-9235-76e3eb0f125c`). An actual database activity sample
during the concurrent jobs showed eight idle connections and no open transactions.


At 12:22 PDT, production Home published run
`dfd02f22-fc40-4ce6-af5f-bc94027e25bd`; the public `/api/home` returns that
exact run, HTTP 200/available, audio, six stories and five daily charts. All four
source events, twelve ticker enrichments and individual/final reviews completed.
Market coverage is ok: 999 symbols and zero gaps. The shared editor reviewed 21
entries, changed 15 and dropped six. The complete summary, six story explanations
and five chart captions were personally read. Substantive event-led connections
are present; repetitive absence/provenance prose in the Palantir item, strong
causal phrasing and inconsistent sector-lead descriptions remain editorial
limitations. This runtime repair does not certify every generated market claim.
Optional page summaries continue before full-worker acceptance.


## Optional page-summary budget ownership — October 10, 12:34 PDT

Full production Home r8 completed at 19:29:15 UTC and its core remains available.
Two of five optional summaries returned unavailable at their identical five-minute
outer deadline. The shared research agent itself may consume that entire five-
minute allowance, leaving no time for formatting and iterative semantic review.
The unavailable constructor also erased its own diagnostic reason before the public
serializer, making execution failure and editorial rejection indistinguishable.

The shared page-summary service now owns a 900-second whole-operation clock.
Research receives at most its explicit 300-second allowance and half of the remaining
caller clock, retaining time for formatting/review under shorter explicit callers.
Home delegates its deadline to this owner; economy summaries inherit the same
contract. Failure details remain in internal results and bounded operational logs;
the public serializer keeps its clean unavailable contract. Research prompts,
source selection and review semantics are preserved in each deployed generation.

Local execution-contract probes verify 300 seconds for normal research, at most 20
seconds under a 40-second caller, unchanged caller state, private diagnostics and
clean public output. Eleven guarded r9 overlays repeat those probes against actual
worker sources. Full production/development news have both persisted all 25 company
profiles and are resolving their twelve events; their news code is unchanged by r9.
Full acceptance of the new optional-summary contract remains pending.

## Review retention and JSON repair — October 10, 12:54 PDT

The two complete r8 news executions succeeded: production
`news-ingest-and-features-3jgeain` / `a16c035d-1bf1-4fed-beda-ddb7104f8c0f`
at 19:39:12 UTC; development `trading-agent-build-dev-53tco9z` /
`f7d76374-d187-4960-a87d-3bdc7286925a` at about 19:45 UTC. Both persisted all
25 company stories and twelve event rows. Each contains eleven nonempty event
narratives and one `needs_synthesis` row; these are not twelve complete narratives.
Production committed 17 eligible memory pages atomically; development retained its
read-only knowledge policy. The normal health monitor has now cleared every open
incident. This establishes execution recovery, not perfect semantic output.

The production empty narrative retained detailed resolved changes, including a
sentence cut off mid-thought. The shared provider JSON-repair prompt independently
cuts raw model output at 12,000 characters. That destructive input contract is
confirmed at source; historic telemetry cannot prove which response entered repair
because repair usage was not recorded. All provider JSON-repair adapters now pass
the complete raw response and record the actual repair response model, usage and
finish reason separately. The news schema requires a nonempty narrative, with an
instruction to explain available observations and leads under uncertainty. Shared
schema validation already preserves the preceding draft if review violates its
contract. No company, sector, phrase, citation-count or finance-pattern rule was
added. A fresh twelve-event phase replay must establish the changed contract;
existing incomplete rows remain honestly recorded in the original run receipts.

The r9 exact optional-page replay establishes the budget repair: IOVA completed
with business context and accepted revisions in 367 seconds. Market Explorer
completed research and two revisions in 326 seconds, then the page service erased
its complete draft because the final reviewer still objected. That is a second,
independent recall defect. Its formatter also cut page and research JSON to character
prefixes before review. The shared page service now presents complete context using
the existing lossless reference representation. Review remains iterative and
advisory: a valid latest draft survives outstanding objections, a failed revision,
or an incomplete review. Actual research/formatting failures remain unavailable;
review details stay private and public serializers continue emitting clean text.
Prompts encourage business connections and explicit research leads without making
complete business evidence a publication admission rule.

All eleven r10 compatibility images passed the shared page budget and retention
execution-contract probes. The final r11 overlays change only the shared JSON
repair transport and news narrative schema on those exact images; each verifies
its actual deployed file hashes and preserves unrelated source generations.
Real page replays, the twelve-event phase, and complete Home acceptance remain
required before final promotion. Source contract probes are not semantic-quality
acceptance. No frontend or API deployment is part of this incident repair.

## Complete narrative transport — October 10, 13:06 PDT

The real configured DeepSeek JSON-repair probe recovered the complete narrative
from a 50,323-character malformed response. The narrative begins at character
44,432, beyond the old repair prefix. The actual repair model completed in 41.2
seconds and its separate model/usage/finish metadata was recorded. Its complete
narrative was inspected. This proves the lost-tail mechanism on real source
content; it does not retrospectively prove the unrecorded original repair call.

A separate qualitative inspection of the r11 optional-page replay found that its
review described business paragraphs missing from the returned IOVA summary. The
shared `clean_attention_text` removed sentences using fixed phrase tests and kept
only two sentences. `_clean_page_narrative` also collapsed markdown whitespace.
This affected both model context and final public fields, so a complete business
investigation could become a price-only summary after review. The shared cleaner
now performs boundary whitespace normalization only, and page narratives preserve
all content and paragraph boundaries. Its obsolete private sentence filters were
removed. Content selection belongs to the LLM synthesis/review, not phrase code.
The actual 4,695-character repair narrative survives both boundaries intact.

The r12 overlays change only those functions on each verified r11 worker image.
The development Streamlit consumer requires the same function changes; its
compatibility build patches only the owned functions and verifies that unrelated
AST nodes remain identical. Production's Streamlit-named resource runs Node. Next.js frontend and API code do not use this cleaner
and need no change. Final source-context page replays and full Home publication
must be inspected before promotion. This is content-preservation acceptance, not
a new financial ranking rule or a claim that all generated facts are correct.

## Shared model-context serializer audit — October 10, 13:15 PDT

A source-wide inventory found five `_compact_json` model-context adapters. Page
summaries had been repaired, while Trading Agent, structured AQL/Zopedia, ticker
business research and news interpretation still cut JSON by character prefixes.
All five now delegate to the same lossless context representation. Exact repeated
values share references; records, string tails and schema context remain complete.
The compatibility overlays change only these adapter functions and imports,
verify each actual image's file hashes, and prove that unrelated functions remain
identical. All eleven r13 worker images passed the complete-input transport check.
This fixes the fault family rather than a particular company or failed article.

The r12 exact page replays both completed and were personally inspected in full:
Market Explorer 258.1 seconds, 3,509 characters and 14 markdown paragraphs; IOVA
336.5 seconds, 1,100 characters and three paragraphs. Both retain business context
and follow-ups. The source does not erase drafts on reviewer objections, truncate
context JSON, or filter whole narratives by phrases/two-sentence limits. Remaining
causal overstatement, technical interpretation and evidence-gap prose are model
quality limitations; none is claimed solved by execution-contract checks.

The r11 production-input news phase completed all twelve narratives in 640 seconds,
including cold-start entities. The final context changes additionally require real
company and complete producer validation. The company probe selects the largest
persisted search-evidence payload numerically, reuses that actual evidence, makes
normal shared AQL/Zopedia calls, and performs no dataset or knowledge writes.
Production Home r13 started as `attention-home-build-lu3lrns`; its complete optional
phase and public API output remain acceptance requirements.

Consumer inspection corrected an operational assumption: production's
`sn-streamlit-ui` resource runs Node, so it does not execute the Python cleaner.
Next.js and production API/frontend images need no deployment. The development
Streamlit consumer does execute the Python text functions. Its image overlay is
limited to those functions, with byte-hash guards and unrelated AST preservation.
Build-time source comparisons must not compare AST hashes between Python versions;
import probes need `/app` on PYTHONPATH when scripts execute from `/tmp`. Initial
consumer compatibility builds failed safely before any deployment.

## Final complete acceptance — October 10, 13:25 PDT

The r13 largest-input company probe completed in 425.9 seconds, ready, with a
complete 8,466-character business narrative. Its source was selected by
persisted payload size (345,345 characters), not ticker identity. All 27
model-context measurements preserve complete parseable evidence; the largest
encoded context is 185,737 characters. Normal DeepSeek calls succeeded. The full
narrative was read; transport and usefulness are accepted, without certifying all
inherited numerical claims or causal interpretation.

Production news r13 now runs the normal complete workload, including acquisition,
25 company investigations, 12 event interpretations and normal atomic memory
persistence. Previous r8 receipts remain preserved. Development Home r13 is
`attention-home-next-dev-b8v94uk`; production Home r13 is
`attention-home-build-lu3lrns`. Scheduled development workers are being promoted
within verified image lineage. Production template promotion remains held until
complete accepted producers and their materialized/public output are checked.

Development Streamlit revision `sn-streamlit-ui-dev--0000457` is ready with HTTP
200 at `/_stcore/health`. Fresh before/after comparison verifies that identity,
secrets, environment, ingress and template fields are unchanged except the image.
Production app resources are unchanged.

All nine development templates now match their r13 digests and passed fresh
protected-configuration comparisons. All 27 jobs are provisioned; no latest
execution is Failed. Development news r13 also starts as
`trading-agent-build-dev-j26hqrt`, retaining the equivalent production workload,
development dataset namespace and no knowledge writes. Its prior r8 result
remains preserved rather than being mislabeled final acceptance.

## Complete embedding inputs — October 10, 13:40 PDT

The wider error scan found a real development news retrieval failure at 20:31 UTC:
OpenAI rejected an input over 8,192 tokens. Lexical fallback kept the producer
running, but semantic retrieval was lost. The five repaired chat serializers do
not establish the different embedding input contract. This is a shared provider
transport fault, not a ticker or news-rule failure.

Both OpenAI and Azure embedding clients now share token-aware complete-input
batching. `cl100k_base` token arrays preserve every token; chunks fit the
per-input limit, and batches fit both aggregate token and item limits. A long
input produces a token-weighted mean of its chunk vectors, then unit normalization.
Short inputs retain the actual provider vector unchanged, so existing short-text
index vectors need no migration. Response index/dimension/finite-value checks and
one caller deadline apply to the whole operation. Legacy worker configurations
without an explicit dimension retain the provider default. The tokenizer cache is
loaded during image construction, avoiding a tokenizer download during research.

Official OpenAI documentation confirms the [embedding request limits](https://developers.openai.com/api/reference/resources/embeddings/methods/create)
and [third-generation token encoding](https://developers.openai.com/api/docs/guides/embeddings).
The aggregation is an application representation choice, not a provider claim or
proof of retrieval quality. Real long-input retrieval and model-path output remain
required. Transport probes cover both providers with 426,500 preserved tokens,
54 chunks, two token-bounded requests, a separate 2,100-input batch, stable order,
unchanged short vectors and normalized combined vectors.

The r13 full news and Home producers continue; their complete workload and output
are still required. The narrowly changed r14 adapter additionally needs actual
configured-provider/retrieval acceptance and compatibility checks across all
eleven worker image variants. No full job is being declared recovered from
transport probes alone.

The r14 actual Azure OpenAI probe `trading-agent-build-dev-6whaedy` succeeded.
The largest persisted business-stack representation selected CCI: 122,723
characters and 29,991 tokens. All tokens reached the provider in four chunks
through one successful request; reported usage is exactly 29,991 tokens. The
combined vector has 1,536 dimensions and unit norm. The full-corpus query returned
twelve sources and normal DeepSeek synthesis completed; total time was 52.2
seconds. All returned source texts and the complete answer were personally read.
The output connects company operations, a peer and indirect spectrum/tower leads,
identifies chronology/value conflicts and recognizes an unrelated Ciena hit as
noise. This is useful retrieval acceptance, not a perfect-recall or financial
fact certification. No dataset or knowledge writes occurred.

All eleven r14 image compatibility builds succeeded. Development promotion is
complete within the verified lineage. The complete r13 news/Home workloads
validate the unchanged remaining pipeline; the r14 adapter has separate actual
provider, retrieval and model-output evidence. Azure split the diagnostic JSON
across two log records; the complete original fragments were reassembled and
parsed, preserving the full private inspection artifact.

## Daily chart selection retention — October 10, 13:55 PDT

Production Home r13 completed its four source-event investigations, twelve ticker
investigations and all reviews, with 999-symbol breadth and no coverage gaps. Its
public API returned HTTP 200, eight stories and audio. Personal inspection found
zero daily charts where the accepted r8 publication had five. The actual log
identified the shared chart service: selection succeeded, but the generic short
review call failed, and the service discarded its valid selected charts.

`services/daily_market_datapoints.py`, shared by Home and the standalone producer,
now owns a 600-second operation clock, with selection calls bounded at 180 seconds
and reviews at 120 seconds under the caller clock. It keeps the latest valid
five-entry selection on review failure, remaining objections or an invalid
revision. Catalog references and actual plotted observations remain machine
contracts. Review guides improvement; it no longer serves as an all-or-nothing
publication gate. Review state stays in private dataset metadata, excluded by the
existing public DTO. Complete evidence uses the shared lossless representation.

Retention probes verify valid data survives all three interruption cases. They
do not establish qualitative chart value. Actual production-context selection,
full updated Home builds and five charts in the public API remain required. Only
deployed image variants containing the chart service need this r15 overlay;
other worker variants retain their accepted r14 embedding repair.

## Full-worker headroom and startup transport — 21:12 UTC

Azure system logs show the first `attention-home-next-dev-b8v94uk` replica ended
with exit 137 at 20:41:05 and automatically retried under the same execution name.
The one-minute memory maximum reached 3,841,888,256 bytes against 4 GiB; sampling
cannot establish its precise termination peak. Both Home deployment profiles now
use 4 CPU/8 GiB, retaining two browser slots and the complete research workload.
The incomplete development execution and early low-capacity production r15
execution were intentionally stopped and preserved as superseded, never accepted.
Final full Home r15 runs are `attention-home-build-m7qazrj` and
`attention-home-next-dev-colarak`.

The 21:00 scheduled development evidence index had a connection timeout on its
first replica; its automatic retry indexed all 17,121 chunks. The shared runner
previously made one five-second connection attempt inside a twenty-second startup
allowance. The r16 overlay changes only that connection owner: up to three attempts
for transient operational failures under the same startup deadline, known terminal
SQL states do not retry, connection options remain protective, and a connection
that arrives after expiry closes itself. Local transport checks cover recovery,
terminal authentication state, exhaustion and late connection cleanup. This is an
operational transport change; full model workloads remain on news r13/Home r15,
with a separate actual Azure/database startup acceptance required for r16.

The r16 actual Azure/database probe succeeded in execution
`trading-agent-build-dev-adqg9y0`: two injected connection interruptions, then the
real PostgreSQL connection/read on attempt three, completed in 2.431 seconds.
Protective SQL timeouts remained intact and the connection closed. All eleven
actual-image contract checks passed. Development template promotion is underway;
production awaits complete model-workload and public-output acceptance.

Ordinary scheduled r16 execution `evidence-index-dev-29861125` independently
encountered two real connection timeouts and recovered on attempt three without
restarting the worker. Azure and database both report Succeeded; the normal
indexing path completed all 17,125 chunks. The startup receipt records this natural
recovery alongside the injected-failure probe.

## Company narrative contract — r17

Full r13 production company inspection found 25 persisted rows but only 24
nonempty narratives. The empty row was marked ready because readiness checked
durable-memory facts, not the required narrative. Its final structured model
response had valid JSON but violated the schema. Direct structured synthesis
failed immediately, while the tool-backed path already had bounded model-based
conversion. No ticker-specific repair is appropriate.

The shared structured owner now applies the same complete-answer schema conversion
to direct synthesis, within the original operation deadline. Company narratives
are required to be nonempty. A valid narrative is retained independently of
whether synthesis adds source-backed durable-memory facts. Profile readiness
requires a narrative; durable fact-write policy stays independent. The news
producer checkpoints all useful company/event outputs before propagating missing
required narratives as an execution failure. This is a transport/materialization
contract, not a semantic publication gate. Actual image probes and complete
workload reruns remain required.

Full r15 development Home completed (Azure Succeeded, logical Warning for partial
research). All five optional page bodies were personally read and retained.
Useful event/company links survive; inherited claims, numerical inconsistencies
and some absence/provenance prose remain quality limitations.

## Separate resolved facts from writing; allocate event completion — r18

Development r13 also had an empty company narrative (unrecovered JSON parse
failure after about 71 seconds) and one empty event interpretation. Provider
telemetry attributes the event failure to final synthesis read timeout after
about 70 seconds. The 300-second whole-agent clock left roughly 70 seconds of
synthesis reserve even though a much larger model step timeout was configured.
The news interpretation owner now declares a 900-second whole operation and
300-second per-step limit; the shared phase budget still reserves final writing
and review time. Both knobs are configurable. This changes execution capacity,
not source relevance or research admission.

Company final synthesis no longer reconstructs the complete resolved fact
inventory. The existing research/fact-resolution phase owns those facts; the
final model returns the narrative, recovered company name, confidence and open
questions. All input evidence and persisted fact groups remain available. The
unused final-fact extraction/admission path is removed. This reduces redundant
large JSON generation at source without truncating evidence or losing research.
R17 shared schema conversion and checkpointed failure propagation remain.
Actual provider recovery, complete r18 news runs in both namespaces and personal
output inspection are still pending; no final recovery claim is made.

### Required-narrative failure propagation checked, 19:25 UTC October 11

The actual producer generator was exercised with missing company narrative,
missing event narrative, and both present. Both research checkpoints were yielded
before either missing-body error propagated. With both bodies present it ended
normally. No external data or knowledge writes occurred. This proves job/phase
contracts only; final model output quality still requires complete live inspection.
Production r18 execution: `news-ingest-and-features-owge2wy`, run
`eca74e45-d8a9-4606-aba4-316a1923a560`. Development r18 execution:
`trading-agent-build-dev-xwm9vha`, run
`bb2e3614-85db-4bcc-b4c4-3b4debfb1198`. Both started normally and are in company
research. The 19:23 ordinary runtime snapshot has zero open incidents.

### Final company writer accepted; development rollout complete, 19:36 UTC October 11

R18 actual Azure preview `trading-agent-build-dev-z4dyxwd` succeeded. Both complete
outputs were personally read: the previously empty profile (14 groups/66 supplied
facts) and the numerically largest dossier (15 groups/73 supplied facts; 95,397
dossier characters). Each final writing call completed in about 40 seconds. Both
retained useful company and indirect context without emitting a duplicate fact
inventory. Source/date inconsistencies and absence/provenance prose remain quality
limitations; the result does not certify financial accuracy. No dataset/knowledge
writes occurred. Both public Home audio endpoints also returned HTTP 200 with
nonempty MP3 data.

All nine development templates now pin r18 images; each fresh protected-config
comparison passed. Existing identities, provider/secrets, commands, schedules and
namespaces were preserved. Production promotion and final full news acceptance
remain pending.


## October 11 — workload deadline headroom

The actual r18 company phases took roughly 90–95 minutes before twelve event
interpretations. Four event workers execute three groups with a 900-second
operation allowance each. The previous two-hour platform ceiling leaves too little
headroom for this full workload. Future production news executions and the manual
development validation host use a three-hour ceiling; full research, parallelism,
provider settings and the Home two-hour ceiling remain unchanged. Active r18
executions retain their original two-hour ceiling and are not restarted merely
for this configuration update.


## October 11 — acquired references retained in durable memory

The company builder previously supplied only recent-news URLs to its memory-page
owner. The same investigations retained dozens of successful search-source URLs,
but eight production drafts were rejected as missing sources. The r19 change
unions the actual acquired URLs with recent-news URLs, deduplicates them, and keeps
research text, memory eligibility and write authorization intact. Every worker
variant received only that source assembly change under before/after byte guards.

Current full-run saved-data validation preserved nine development page bodies
without durable writes. Production recovery committed all eight affected pages
through the existing typed mutation owner, with zero rejections and durable source
readback. The mutation is `zopedia_mutation::upsert_pages::b6c6ea44d637a4f4`.
Original r18 commit reports retain their eight rejections; the supplemental recovery
is recorded separately. No per-company rules or replacement narratives were added.

The first development preview used the legacy production reader and failed its
exact run/version guard before writing. It was superseded by the actual development
image, which passed. That rejected preview remains in the evidence; it is separate
from the six accepted full workloads, each of which has exactly one database attempt.
