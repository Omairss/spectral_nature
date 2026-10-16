# Native workbench refinement — 2026-10-15

The owner adds meaningful scatter shape/size without slowing the phone, softer dark
text, the Attention network on Brief, a fully expanded Brief, restoration of Home
highlight datapoints, five-second research updates with completion notification,
collapsed Trading Agent cards, investigation of all-no-call output, and a single
research entry at top right with Trading Agent in the bottom navigation.

This continues the pending request-thread and peripheral-chart release. Build 7 is
still the accepted installed version. The host has recovered (load about 2 and one
booted iPhone16); the previous simulator failure is no longer assumed blocking.

## Design and acceptance

- Trace Home datapoints and Trading Agent artifacts to their producing jobs before
  changing their rendering or model instructions. Preserve actual model decisions;
  do not manufacture trades or hide valid no-call outcomes.
- Scatter shape denotes an actual categorical source dimension and area an actual
  quantitative dimension, with explicit legends and inspectable values. Prepare
  geometry/domains off the main actor, recomputing only when inputs change; never
  recompute hulls during point selection or ordinary view updates. Keep outer K.
- Use one semantic text palette across native screens. Preserve contrast and do not
  dim accent/status colors or user controls through a blanket opacity.
- Brief always shows its full narrative/audio/highlights and a network entry. Put
  research conversations, composer and knowledge behind the top-right entry;
  Trading Agent replaces the duplicate bottom Research tab.
- Keep research tracking at the session level, independent of the open sheet/tab.
  Poll actual durable state every five seconds while foregrounded, expose current
  phase/latest public finding/next step plus timestamp, and notify completion once.
  The owner requested locked/closed delivery, but confirmed the free Personal Team.
  APNs is blocked on enrollment; do not claim background delivery from polling.
- Trading candidates begin collapsed; show the concise identity/setup/stance first,
  then lazy details and decision controls on expansion. Existing review authority
  stays unchanged.

Review real Home/Markets/Trading artifacts and simulated request/research lifecycle
layouts, then the normal dev API path. Do not broadly deploy the dirty workspace.
Auto-deploy scoped changes to dev after local acceptance; production promotion
still requires the owner's explicit permission. Device install follows a ready
production API and signed candidate, preserving bundle identity/session.

Status: signed build8 and normal native dev flows accepted. Trading source repair passed local recovery/data-equivalence checks and complete five-horizon review. Updated dev API, queued Research and Trading images share engine b96611be709270d5a95d; image-only configuration checks passed. Full2GiB Trading generation trading-agent-build-dev-9h67txj is running. No production promotion or phone installation.


## Findings and local implementation

Production Home has eight groups and a graph but returns zero highlights. The
latest retained highlight dataset contains five charts selected October 14 at
16:50 UTC; the October 15 Home run has a different ID and an empty embedded set.
The API previously rejected every independent selection with a different Home ID.
Live Home now picks the newer valid independent or embedded selection and exposes
its selection timestamp. Replay remains tied to the historical Home bundle. The
native and web headings display that actual selection date; no old data is relabeled
as today. The native Brief is fully expanded and links to the same network as Attention.

The shared scatter prepares points, hull layers, full-universe domains and color/
volume scales in a cancellable detached task. Point selection reuses prepared data.
Markets defaults to 40 outer points with a 200-mark maximum; shape is 1D direction,
color is 1D magnitude, and log-scaled area is shares traded. Numeric legends and
selected volume are visible. Real-data simulator review confirmed directional
triangles, differentiated areas, stable axes and selecting KOD with its actual volume.

Research conversations/knowledge/sources now open from the top-right entry. Trading
replaces the bottom Research tab for admins, preserving server authority. Session-
owned research tracking follows active IDs across navigation, scoped to gateway/user,
and polls at five-second intervals. Current phase, public finding/next step, source
count and checked time are visible outside the optional journal. An in-app completion
notice links to the result. The scripted loopback lifecycle is UI QA only, not a live
LLM acceptance claim.

The owner wants notifications while locked/closed and confirmed use of the free
Personal Team. The installed profile expires after seven days and has no APNs
entitlement. Neither Key Vault has an Apple push key. Background alerts are blocked
on Developer Program enrollment and APNs setup; no foreground timer or scheduled
local alert is claimed to solve this. No push entitlement or unverified delivery
service is added to the free-team build. Official requirements:
https://developer.apple.com/help/account/reference/supported-capabilities-ios

Production Trading Agent has 16 no_call candidates (11 watch, 4 avoid, 1 insufficient
evidence); the 3M synthesis failed with an LLM deadline. Company packages repeatedly
use watch-only wording. The shared trading task instructions now request competing
forward-looking bullish/bearish mechanisms, assumptions and invalidation, and make
clear that an unresolved past move or missing historical return alone does not veto
a forecast. No call quota or coded ticker rule is added.

The deeper source gap was numerical financial history: scoring had access to it,
but company research received no quarterly values. The existing Attention snapshot
transformation now lives in compute/fundamentals.py and serves both consumers. Both
Trading generation routes receive the already-loaded quarterly frame, batch the
company snapshots once, and retain values, actual periods and histories through the
shared company-research pool and horizon synthesis. Eight real company snapshots
match the previous Attention contract exactly. In the actual algorithm-selected
8-company probe, 6 have history and 2 have explicit absent coverage.

Personally read the live research and typed candidates. Local current-engine trials
retained a substantive mildly bearish JBLU answer, an IOVA 1–3M bullish research
thesis and a reasonable 1Y Watch; several other calls timed out. These trials do not
validate unrelated in-progress engine edits. A separate review used the exact
accepted Trading worker source plus only the scoped Trading/financial changes and
its existing dev runtime configuration. It completed a supported IOVA 3M long,
including the $170.7M first-half versus $410–420M guide calculation, the required
$120–125M second-half quarterly pace, operating losses, repricing risk and observable
invalidation. The raw typed payload and normalized candidate agree. No forced call
quota and no change to the stance normalizer. This is a bounded live qualitative
review, not a full scheduled-job completion claim.

Optimized Mac selector benchmark (999 real instruments, 100 iterations): outer40
median 0.452 ms / p95 0.675 ms; outer200 median 0.920 / p95 1.096 ms. This measures the
selector, not iPhone rendering FPS. Preparation is off-main and selection reuses it.

The final Trading header is leading-aligned and limited to two setup lines. A real
native request thread was created and followed up through the actual persistence
API in the isolated QA namespace; both messages were visible on reopening. Research
Close controls belong to the navigated screens. The completion notice must occupy
its own layout region rather than overlap UIKit navigation bars; visual acceptance
is required in addition to an accessibility label.

Scoped release contexts: /tmp/sn-native-polish-release-20261015. API layers the Home
selection repair on the already-built request API candidate. Web starts from the
accepted request gateway source, changing only the Highlights heading/date contract.
Trading layers only compute/fundamentals.py, services/trading_agent.py and
pipeline/jobs/main.py on worker digest 474ea722; it preserves that worker's existing
engine. The local Attention caller uses the shared computation, while the older
worker's unused Attention implementation is unchanged. Source hashes and base
receipts are retained in the manifest. No unrelated dirty source is copied.

UI preview source/loopback fixture server: `/tmp/sn-native-polish-preview`.
Derived build: `/tmp/sn-native-polish-preview-derived`. Simulator installed and
launched preview PID 26797. Data captures and live LLM outputs are in
`/tmp/sn-native-polish-20261015`; the production-like API payload uses the actual
Home/trading adapters over retained captures. Feedback writes use synthetic actor
70c83d37-50c5-4adc-9059-8b6ab3a74758 in namespace
`feedback-qa-native-polish-20261015` only. The server runs on loopback port 8768.
Native shipping version is still 7 and production is unchanged.

## Deployment and final handoff

Dev API sn-api-next-dev--0000031 uses native-workbench-api-dev digest
2751bcf177c88601470b601f8f4520ee26a353a71a7c51063d87886a1be79a4e (ACR cj14f).
Dev gateway sn-next-ui-dev--0000021 uses native-workbench-web-dev digest
ba0aa2bd6912eae732b95f122ac02b1d2cd7a6133b798c58c5691c33176ef0b6 (cj14g).
Dev trading-agent-build-dev uses native-workbench-trading-dev digest
b33398acef3c660162d5b2d61c0432e9f97524d9407235edc9d8cc467f20b0a7 (cj14h).
All three before/after comparisons preserve the complete environment, resources,
commands, identity and app configuration; only images change.

The deployed public Home returns five valid dated highlights, eight groups and a
graph. An unauthenticated request-inbox call correctly returns 401. Existing browser
Home renders Highlights with the UTC selection date. A direct browser API navigation
was blocked by the browser client and is not counted as authenticated acceptance.
The actual shipping bundle is running against dev in the iPhone16 simulator; its
separate origin-scoped session required a normal dev login. The owner signed in normally. The final same-bundle simulator update preserved
that session. A dev request was created, followed up, and reopened with both messages. Do not copy browser or production cookies, impersonate a user or
broaden authorization to complete that check.

Final native layout review confirmed a visible completion notice, title and Close
control simultaneously; after closing, exactly one notice remains across tabs and
tapping it opens the completed result. The compact scatter includes all three
legends without clipping; explicit toolbar dimensions keep Research visible when
navigation collapses. Highlight calendar dates use UTC in both clients. Zero volume
uses the logarithmic scale's valid zero; absent direction and volume are identified.

Signed build 8 keeps com.torrescapital.spectralnature and team 44J3R74SJ9. The
profile expires October22 06:23:30 UTC and contains no aps-environment. Signature
verification passed. The iPhone is paired/available on the latest device query;
no install has been attempted. Receipt and source manifest:
[verification](../verification/native-workbench-2026-10-15/release.json).

The owner's session-provided AGENTS.md rule is:
> Auto deploy changes to dev, but not to prod without explicit permission.

After authenticated native dev acceptance (or an explicit owner decision to skip
it), present these exact candidates for production approval. Then promote the
scoped API and both serving web apps, update the production Trading worker, verify
protected configuration and the normal gateway, and install build8 without an
uninstall or Keychain reset. Do not replace the research worker with the Trading or
API image. Locked/closed notifications remain a separate explicit enrollment/APNs
blocker; no purchase or enrollment has been performed.

The full dev Trading generation was started as execution
trading-agent-build-dev-i5bn40g / run 5b290fa6-b205-4de0-b8e3-03f86c3de6de.
It has loaded the actual 15-dataset ranking context (including quarterly
fundamentals) and begun shared company research. The normal native research test is
agrun_80e586049ccb4864acfd81a4, worker execution sn-research-run-dev-cmq2pzv.
Its actual public summary and next step appeared in the app. Research continues
while Markets is open; the normal gateway loaded all999 instruments with outer40.
Completion and full Trading output remain under observation.

Normal native dev acceptance is now complete: a real request with a follow-up was
saved and reopened; sign-in survived the final same-bundle simulator installation;
all999 Markets instruments loaded with the outer40 plot and legends. The real
research run completed with63 events and4 public updates. Its notice appeared while
Markets was open, and tapping it opened the full answer. Personally read that
answer and citation; it accurately distinguishes free Personal Team remote push
from local notifications. One planning timeout remained in the retained limitations,
but the useful, correct final answer was preserved. This is actual queue/worker/
gateway/native acceptance, separate from the earlier scripted UI lifecycle.
The full Trading generation has completed the first of8 company investigations.

## Full Trading generation failure and decision — 21:39 PDT

The full job did not finish. Its first replica qj4tw exited137 with reason
ProcessExited after LITE, CRGY, PBR and MRVL completed and MRNA failed. Three company
investigations had not returned. Memory samples rose from0.50 to1.68GiB against the
existing2GiB allocation. This supports memory-pressure suspicion, but no captured
kernel OOM event identifies the precise cause or allocation. Do not report2GiB as
an observed peak. Unlike the earlier health audit's outcome-stage failures, this
failure happened during company research.

Azure started replica wp8cb automatically, while the execution still appeared
Running. Stopped only this test execution with its explicit name; Azure now reports
Stopped. Schedules, resources and identities were unchanged. No successful complete
Trading report from this trial is claimed. The single-horizon qualitative review
remains valid but cannot stand in for full-job reliability.

The owner has been asked whether to finish Trading reliability before installation
or explicitly promote the reviewed native/API/web changes now while leaving the
production Trading worker unchanged. Native requests, the full Markets view,
research progress, completion notice and saved sign-in all passed normal dev.
Production and the iPhone are unchanged. Push while locked/closed remains blocked
by the confirmed free Personal Team.

A deeper Trading repair must profile evidence/frame lifetimes through company
research and outcomes under the existing paid2GiB allocation, identify retained
copies/caches at their actual owner, and preserve retrievable originals and paid
partial company results across retries. Do not hide the problem with smaller
fact/ticker quotas, arbitrary evidence truncation, a larger instance or another
unobserved retry. Then rerun the complete company/horizon job and personally review
the published report before promoting the Trading worker. Coordinate this finding
with the existing System Reliability Repair plan; this investigation did not
implement or deploy that broader repair.

## Owner decision: finish Trading before installation

The owner chose **Finish Trading repair first**. Keep production and the phone
unchanged. Continue the source repair within the existing2GiB worker allocation.

Profiling uses the exact stopped worker source and its actual dev runtime/input
configuration, outside the live job: /tmp/sn-trading-memory-20261015/profile_worker.py.
It records RSS, Python allocation traces/overhead, Arrow active bytes, dataset sizes,
and research-stage boundaries. Completed company packages and latest acquisition
checkpoints are written locally as they arrive. No dataset publication or durable
knowledge mutation occurs in this profile; provider usage remains real. Record
profiling overhead separately rather than interpreting its RSS as deployment headroom.
The first setup attempt was interrupted before inference to correct the ranker's
import owner; its observations are retained separately.

Read Python tracemalloc, Arrow MemoryPool and Azure built-in execution identity
documentation before implementation. Tracemalloc does not measure all native
allocations. Arrow release_unused is diagnostic/benchmarking support, not a proposed
production cleanup patch. Azure CONTAINER_APP_JOB_EXECUTION_NAME provides a stable
execution identity across its replicas, already used by job tracking. It can scope
recovery without replaying stale scheduled observations.

Source findings to investigate, not yet asserted causes: the pipeline retains all
ranking frames across research/outcomes; nested evidence serialization materializes
complete subtrees repeatedly; company packages retain tool-call evidence; and
persist_findings=False selects NullResearchSessionStore, which also discards
operational checkpoints. Separate knowledge mutation permission from durable paid
execution state in any recovery repair. Do not broaden memory-write authorization.

The first research profile remains active after a Ctrl-C: executor shutdown waits
for submitted tasks. A mistakenly overlapping replacement reached two planning
calls before its exact process was terminated. Retain those attempts as profiling
cost, not accepted output. Do not start another inference batch. The active runner
saves complete results independently of the interrupted main collector. Private
checkpoints are consumed by ResearchSession and never reach the public callback.

Allocation snapshots identify retained-evidence candidate queries materializing
tens of megabytes of full chunk bodies/metadata per search, before rank fusion and
document diversification discard most rows. Nested retrieval runs four such
searches per company with two companies in parallel. Repair the shared store to
rank the same full candidate identities first and fetch complete bodies only for
the existing selected result set. Keep search breadth, ranking, originals and
retrieval tools unchanged. This is a measured transient allocation source, not
proof of the Azure kernel's kill reason.

The snapshot comparison now passes for complete returned content and scores.
The prior SQL ordering left equal-ranked candidates unspecified; added record ID
as a final tie breaker in both comparison paths. With that ordering fixed, the
same real query returns identical results and uses1.93MB rather than26.59MB of
temporary tracked Python allocation. This is one search, not a complete-job claim.
All8 full company results from the original profile have been preserved.

Recovery design extends the existing execution-state/artifact owner. Shared
tool-using public research can save private acquisition checkpoints independently
of knowledge writes, scoped by declared access, environment, platform execution,
input content, runtime/prompt settings and engine source revision. Completed
results are reused; unfinished evidence resumes through the existing agent.
A short SQL lease prevents overlapping attempts and closes its connection before
inference. Fresh scheduled executions have different acquisition identities;
private conversations and knowledge-writing calls keep their existing ownership.
Structured Trading horizons/reviews opt into the existing supplied-evidence cache.

Preserve the logical pipeline run/as-of and input dataset versions across replicas
of one platform execution. This avoids both accidental input drift and duplicate
candidate IDs after a retry. Outcomes read complete candidate history one version
at a time and only necessary run fields. The shared Parquet owner downloads to
disk, projects before Pandas allocation, and scans all price bars for historical
candidate symbols plus SPY in bounded batches. No history/ticker/fact quota is
reduced. Full original research and public candidate contracts remain retained.

## Local Trading repair acceptance — 22:20 PDT

The shared recovery checks passed against isolated dev execution-state keys with
zero model calls: private checkpoints stay out of public progress, full evidence
resumes under the same research identity, completed results are reused, fresh
executions are separate, and SQL ownership prevents overlapping work. Required
reads stop before inference if persistence is unavailable. Changed instructions
invalidate supplied-evidence reuse. Input dataset versions and logical run/as-of
remain identical across replica attempts. Knowledge-write policy remains none.

The outcome comparison requested12 historical versions and found6. All96 retained
candidates and every output/provenance field matched exactly. The source contains
1,467,928 price bars;31,021 matching bars cover the candidate symbols plus SPY.
Isolated old/new process peaks were743MB and219MB (223MB after accepted-baseline
storage consolidation). Two missing-price outcomes remain unchanged. These local
figures do not establish complete Linux-container headroom.

All8 original company investigations were saved and personally read. Recovered
their exact unchanged briefs to evaluate every horizon without buying company
research again. Four horizons completed; the5Y synthesis hit the shared direct
formatter's implicit60-second deadline. The configured operation budget is300
seconds and provider timeout480 seconds. Removed that hidden cap at the shared
owner while retaining explicit caller budgets. The5Y-only retry completed; its
result was saved before a collector constant typo, then materialized from that
saved result without another request. The original failed attempt remains in the
record. All5 complete horizon outputs were personally read; directional calls and
watch classifications differ by horizon without a coded call quota. These are
research outputs, not evidence of profitable forecasts or a completed cloud run.

Shared-source consolidation preserves accepted safeguards that had already
drifted between the API and workers: API database timeouts/partition readers and
retained-document SQL filtering, plus worker acquisition deadlines and owned browser
process cleanup. The same7 shared source files will be layered onto each role's
own accepted base. Keep the research worker's existing entry point and queue code;
only Trading receives the Trading adapter/job changes. No unaccepted reasoning,
cost or flow implementation is included.

Frozen contexts: /tmp/sn-trading-reliability-release-20261015.
ACR builds: API cj14m, queued Research cj14j, Trading cj14k. Development image-only
updates and a full observed2GiB Trading run remain pending. Before-config captures
confirm the expected current image per role and unchanged existing allocations.
Production and the iPhone remain unchanged; build8 is still ready.

Development rollout is complete: API revision32, queued Research and Trading use
the scoped images in the verification receipt and report the same shared engine
revision b96611be709270d5a95d. All role-specific environment, identity, commands,
resources and schedules compare unchanged. Trading's first layered build cj14k
hit Docker's layer-depth limit before compilation; rebuilt cj14n from the earlier
accepted474ea base with one owned source layer, including the unchanged shared
fundamentals file. API cj14m and Research cj14j passed directly.

Home still returns5 Highlights and8 groups with the original selection date;
anonymous request access remains401. The signed-in native app completed a real
follow-up through the updated queued worker (agrun_f8a19308a94441aca42cdf71),
retaining its conversation,27 events,3 public updates and one delivered completion.
Read the complete answer and verified stable conversation dismissal. No native
source changes or new build were needed.

The full dev Trading execution is9h67txj, logical run
1d1e0d6e-19c0-4340-93af-fc7e624a717b, replica m4xnm. It retained all15 ranking
datasets and5994 ranked rows. Four company investigations have completed; the
kernel-reported cgroup peak so far is1,153,236,992 bytes against2,147,483,648.
Continue observing through all companies, synthesis, publication and outcomes.
This was an intermediate observation, not complete-run acceptance.

### Trading memory acceptance remains open — October 16, 06:00 UTC

The same replica subsequently reached the kernel-reported limit of
2,147,483,648 bytes during company research. Seven investigations completed and
were checkpointed; MRNA returned failed at05:56:29 UTC. The worker continued into
horizon analysis. Reaching the limit does not establish an OOM kill, but it does
fail the requirement for usable memory headroom. Production and the phone remain
unchanged.

Read-only checkpoint metadata identifies one unusually large acquisition state:
56,379,968 compressed SQL bytes, compared with the other states below1MB. It
contains completed-investigation, live-evidence and retained-evidence tools.
Its actual field ownership and serialization must be inspected before attributing
the pressure to a specific allocation. Short bounded SQL shape/read probes
timed out; no checkpoint was deleted or overwritten. Avoid repeating broad
namespace scans or buying another full company pool while these paid results
are available.

The v2 contexts contain only the already checked restoration of the original
request clock on replica recovery. Their builds are held, not deployed: this
correction does not resolve the full-job memory result. Continue from the scoped
source and preserve role-specific configuration. Do not include unrelated dirty
reasoning/cost experiments in this release.

### Source provenance repair and recorded-work review — October 16

Execution9h67txj finished successfully at06:01:31 UTC:7 of8 companies completed,
all5 horizons completed,17 candidates,113 outcomes and1 historical review.
Personally read the complete17 candidate narratives and all5 regime/posture
outputs. The prior all-no-call behavior is no longer present. This does not
establish forecast performance or resolve the broader reasoning-quality work.
The kernel peak still reached2GiB, so this run alone cannot accept headroom.

The large checkpoint's third tool, research.search_evidence, occupies
197,939,859 raw JSONB bytes. It contains160 candidates. One retained article
carries8,034 top-level source references and60,458 recursively copied records,
but only689 distinct metadata records, with ancestry depth4. The original SQL
metadata and captured acquisition were read without mutation and retained
privately for review. Wide JSONB decomposition/text conversion repeatedly timed
out; small JSON paths and one materialized candidate projection succeeded.

The repair belongs to the existing SAA/provenance and agent-tool owners:

- Retained metadata reads/writes normalize ancestry into complete records with
  source_reference_id links. Every distinct metadata record and ancestry edge
  is preserved; duplicate links do not imply corroboration. The operation is
  idempotent. The largest legacy reference list shrinks from21,859,318 to819,017
  encoded bytes, with all689 records and all links resolvable.
- Raw SAA readers retain their full-reference default. Shared agent search
  explicitly projects inherited references out of SQL and supplies a callable
  research.read_evidence resource. Content reads return the complete passage;
  include_source_references=true retrieves the complete linked provenance.
  No text, candidate breadth, dates, source types or ranking rules are reduced.
- Source summaries and evidence packs preserve linked definitions. Materialized
  captures retain their original-source read resource even when ancestry has
  not been expanded. Future capture writes therefore retain provenance without
  copying its entire tree into every new result.
- The engine revision now also includes aql/evidence_pack.py. Job memory logs
  include cgroup anonymous/file/kernel components and OOM/limit counters.

Exact recorded160-source review, without model calls: default search checkpoint
196,904,579→732,636 bytes; prompt6,382,784→376,502 bytes; temporary Python peak
841,363,640→91,546,771 bytes. Source-ID, passage and date hashes match. Live
explicit reads of the largest source return the same complete972-character
passage/date; default response1,618 bytes, full provenance820,991 bytes.
Materialized-capture source-resource preservation and all graph links passed.
These Mac allocations are not Linux container-headroom measurements.

Recovered MRNA's original clock, research state and three completed acquisitions
through bounded scalar JSON paths. Resumed only that investigation against the
new source view: one new search, completed in122 seconds, process peak314MB.
Its complete answer was personally read. The answer contains useful business
facts but still mishandles seasonal comparisons and contradicts itself between
the opening and horizon conclusions. Keep this QA result unpublished; it is an
execution-recovery observation, not narrative-quality acceptance. No completed
company pool or earlier horizons were repurchased.

Frozen v3 role contexts: /tmp/sn-trading-reliability-release-v3-20261015.
The ten shared research files derive from the accepted role sources and the
owned changes only; unrelated dirty reasoning/cost experiments remain excluded.
The queued worker keeps its own original entrypoint. A read-only Linux replay
will hold the original ranking/paid outputs and four copies of the worst
recorded acquisition, then recompute every published outcome under the existing
1CPU/2GiB allocation, with model transport disabled and no publication. This
checks the identified allocation without repurchasing completed research; it
does not claim a new full inference generation. Production/phone are unchanged.

### Runtime correction accepted in dev — October 16, 07:00 UTC

The corrected recorded-work executionuikbgp5 succeeded on the exact original
1CPU/2GiB template, including all84 environment entries. It retained the5,994
ranked rows and paid outputs, read all160 original sources through the projected
SQL owner, held4 copies of the worst acquisition together, and recomputed every
value/provenance field for all113 outcomes using35,349 bars across30 symbols.
Kernel peak823,480,320 bytes; memory limit2,147,483,648; no limit events or OOM
kills. The final snapshot separates390.6MB anonymous RSS from176.5MB file cache.
There were no model calls or publications. This is the measured allocation
acceptance for the identified defect, not a new full paid inference generation.

The first isolated executionrenls7r failed before loading data: CLI image/command
flags built a replacement container with no inherited environment and default
0.5CPU/1Gi. Used the documented complete-template input and checked the observed
execution before accepting the repeated measurement. The probe also now reads
cgroup v1 as well as v2; the application already reads current/peak/limit on both.

Dev API revision33 and the Research/Trading job templates now use the v3 images
in the verification receipt. All report enginebe03bbe361a775b358ec. Fresh GETs
prove full protected templates, configuration/schedules, identities and existing
allocations match their captured baselines. Update-command responses omit env
values and were not used as configuration receipts. The web image remains21.
Home returns5 Highlights/8 groups with the actual14October selection date;
anonymous feature-request access is401.

Normal signed-in build8 verification after rollout: Trading displays Long and
Watch calls, with cards initially collapsed and working tap expansion/collapse.
The retained-source research request9f4a5c2c5bc14bbcb0863da7 completed through
the new queued worker with36 events,4 public updates and enginebe03bbe361a775b358ec.
Personally read its full answer: the passage/missing-date handling is faithful,
though verbose for the requested two short sentences. This does not accept the
broader reasoning-quality experiments. Completion was displayed once, its active
ID cleared and delivered ID persisted; conversation Close remained accessible.
The signed shipping build8 binary hash is unchanged from its accepted build.

Read-only production inventory still matches the captured old API, both web
aliases and both worker roles. No production resource or iPhone installation
has changed. The next release step requires the explicit production approval
specified in the user-provided AGENTS.md, followed by a production Trading
refresh and installation of the existing signed build8. Do not publish the
unaccepted recovered MRNA QA draft as a recommendation.

### Production promotion approved — October 16, 07:13 UTC

The owner explicitly approved the verified API/web/worker promotion, a fresh
production Trading generation, and installation of the already signed build8.
The recovered MRNA QA draft remains unpublished. Fresh production GETs matched
the expected image baselines. Private before/after configuration captures are in
`/tmp/sn-production-build8-20261016`; release outcomes are recorded in the
verification receipt. Promotion changes images only and checks protected
templates, configuration/schedules and identities against fresh GETs. The phone
is paired/reachable and Developer Mode is enabled. Do not uninstall the app or
clear its saved sign-in.

Production image-only promotion is complete: API revision8, serving web revision93
and alias revision9 are Ready/Running; both role-specific worker templates use
the approved digests. Protected settings/schedules/identities and allocations
match their fresh pre-promotion GETs. The normal public gateway returns health200,
Home200 with5 Highlights/8 groups, and anonymous requests401. Production Home
as-of is15October20:50UTC; independent Highlights retain14October16:50UTC.

Production Trading execution `trading-agent-build-uzo29go` started once at
07:15:35UTC with logical run `6b8d2a57-a6a9-48e3-be0e-552898b037b7`. It uses
the unchanged1CPU/2GiB allocation. Completion and report review are pending.

Build8 was installed in place on the iPhone13Pro and launched successfully at
07:16:42UTC. The device's installed-app inventory confirms1.0.0(8). Signature
and binary SHA256 match the previously accepted artifact; no rebuild, uninstall
or Keychain reset occurred.

Production observation boundaries: the local verifier initially inherited a
direct development database URL from `.env`. It now removes that override and
resolves the captured production database secret. The baseline dataset copies
were read from production's unnamespaced blob manifests; final verification will
also check the exact new run/version identifiers. No deployed configuration was
changed for this observation correction.

The preserved production template does not set
`TRADING_AGENT_ZOPEDIA_WRITE_POLICY`; its existing default is `safe_auto`. The
dev QA template explicitly uses `none`. Operational acquisition replay excludes
write-capable policies by design, so the read-only checkpoint/recovery acceptance
is not claimed for that production policy. Structured supplied-evidence horizon
results are retained. Do not silently change production knowledge permissions or
claim all producers/write-capable research have been consolidated.

### Production and phone verification complete — October 16

Production execution `trading-agent-build-uzo29go` **Succeeded** at07:35:56UTC
on its first replica (`ljtmm`), exit0 and restart count0. All8 company reports
and5 horizons completed. Published20 candidates:4 long,7 short,8 watch,1 avoid;
204 outcomes and0 historical review rows. Every dataset version ends in
`__20261016T071604Z__6b8d2a57`, matching the captured logical run. Kernel peak
646,582,272 bytes against2,147,483,648 bytes; allocation remains1CPU/2GiB. This
is a fresh full production inference generation, distinct from the earlier
recorded-work allocation check.

Personally read all8 complete company answers, all5 market/regime/posture
summaries and every field of all20 final cards. Directional calls now reach
publication; MRNA explicitly handles seasonal quarters and differs by horizon,
CORZ separates building expenditure from operating loss, and BAC includes bank
economics. The operational repair is accepted. Broader reasoning is **not**
accepted wholesale: the MRNA brief reverses invalidation labels in one paragraph,
ASST misstates the meaning of a NAV discount, some long-horizon reasoning still
extrapolates short windows/historical returns, and the1M posture miscounts its
watch names. Generic process/disclaimer prose also persists in some internal
company briefs. These belong in the shared synthesis/review work tracked in
`ZOPEDIA_REASONING_QUALITY_COST_2026-10-15.md`; no ticker-specific rewrite or
phrase-based publication rule was added. The rejected recovered MRNA QA draft
remains unpublished. The new production MRNA report is separately generated.

Used an existing authenticated production browser session without extracting or
copying credentials. A fresh production-alias Trading page shows the new
16October00:16:04 local snapshot:1W NVAX Long, JBLU Short, CORZ/CRGY Watch. No
approve/reject or order action was clicked. Existing user tabs were not navigated
or edited; the verification tab is ephemeral.

Build8 remains installed/launched on the phone, and post-launch account,
conversation and portfolio requests succeeded. The accepted binary was installed
in place, preserving app data and Keychain. Free Personal Team profile expiration
is22October06:23:30UTC; background APNs is absent from this signed build.
Foreground completion notices and recovery on return retain their verified scope.

Final receipts: `../verification/native-workbench-2026-10-15/release.json` and
`../verification/native-workbench-2026-10-15/production-trading-review.json`.
