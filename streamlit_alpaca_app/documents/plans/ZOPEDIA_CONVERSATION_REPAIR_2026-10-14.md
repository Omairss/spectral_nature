# Zopedia conversation and research repair

Status: shared engine consolidation, conversation continuity and public progress implemented. Development API revision 30 and frontend revision 20 are live; the owner authorized their [production promotion](STACK_PARITY_PROMOTION_2026-10-14.md), now running as API revision 7 and frontends 92/8. All fifteen non-broker production workers include the shared changes. Production infrastructure and conversation rendering checks passed; live research acceptance is blocked by DeepSeek timeouts and HTTP 503. Both failed attempts remain in one conversation. Broader research-quality work remains open. The [production audit](../architecture/AQL/audits/ZOPEDIA_CONVERSATION_AUDIT_2026-10-14.md) preserves the starting behavior. Earlier development-only statements below describe that initial release.

Current follow-up scope: the owner approved the Admin → LLM mockup and authorized
implementation. Shared role routing, immutable run settings, configurable parallel
reviewers, evidence handoffs, retained captures and the real admin editor are now
implemented locally, including the page-summary and daily-datapoint adapters.
Financial examples and infrastructure checks passed; source-reasoning acceptance
remains open. These changes have not been released. The accepted release above is
unchanged. Continue from the
[admin design](#admin-llm-flow-editor--mockup-and-implementation-design) and
[implementation handoff](#implementation-handoff-for-the-next-agent), preserving
the separate research roles.

## Implementation record

### Admin flow and model routing — local implementation, acceptance pending

- Actual daily spend audit: the owner reported $37, and the provider page
  confirmed $37.21 for October 15 local time. Known usage plus archive receipts
  attributes $35.64: $27.03 news/company work, $5.92 Home and the remaining worker/
  experiment calls. Four news schedules and a $4.19 repeated application attempt
  explain why the earlier illustrative $3–$4 mix was not a daily baseline. The
  [audit](../architecture/AQL/audits/ZOPEDIA_DAILY_SPEND_2026-10-15.md) leaves $1.57
  unassigned. Production still reports Flash; actual daily volumes and repeated
  work must be addressed before expanding premium routing.
- October 15 follow-up: the owner accepts up to +20% overall workload LLM cost to
  improve reasoning and rejects +50% overall. Individual difficult runs may cost
  more; there is no per-run or per-category +20% cap. The live comparisons and
  proposed next handoff are maintained
  in the [reasoning/cost plan](ZOPEDIA_REASONING_QUALITY_COST_2026-10-15.md).
  Its small-query estimate excludes full Home generation. A retained full Home
  run used 16.77 million generation tokens; the combined budget needs that
  workload's actual frequency and per-role model costs before any overall claim.
  Actual Trading Agent batch samples used 0.67–0.88 million tokens on reported
  Flash. Repricing one batch's core roles to Pro at unchanged tokens gives
  $0.6485 versus $0.2431 with the same hypothetical cache pattern (+166.8%).
  That routing cost is separate from the marginal handoff improvement; the
  complete rollout budget remains unresolved. News/dossier builds executed in
  the same Azure worker are a different workload.
  Investigation used isolated overrides; application defaults remain unchanged.
  No candidate has passed both quality and cost acceptance. Preparing explicit
  assertion/basis pairs helped Flash catch the original errors, but the correction
  introduced doubt about an explicit user premise. The direct-analysis replay
  cost rose 45.7%; that alone does not breach the clarified overall allowance.
  Its quality failure and unmeasured workload impact still prevent adoption.
  The next candidate improves
  premise provenance and review feedback inside the existing calls, with matched
  direct-query and research comparisons before release.
- Current acceptance and precise continuation work are recorded in the
  [October 15 acceptance review](../architecture/AQL/audits/ZOPEDIA_FLOW_ACCEPTANCE_2026-10-15.md).
- October 15 handoff refinement: acquisition returns and checkpoints original
  sources before optional relevance ranking or first-pass summarization. The
  shared executor then runs those model stages under their own configured limits.
  A failed enrichment keeps the acquired result and its provenance; it cannot
  turn a successful read into a failed acquisition. Ranking retains every
  candidate, and summaries remain alongside original text. This is the existing
  Search → Relevance and Read → Summarize flow, without another research loop.
- `services/llm_flow.py` owns the stage graph, model profiles, review cohort and
  per-run settings snapshot. Admin GET/PATCH projects and saves this contract.
  New queued/direct runs capture the complete configuration; continuations of the
  same attempt retain it. Historical turns supply evidence, not stale settings.
- Analysis, research and durable knowledge use Pro/max by default. Monitor,
  relevance, query planning and reviewers use Flash/low. Source summaries,
  presentation and schema repair use Flash without thinking. Every route remains
  editable. Parallel reviewers have independent model settings and focus; reviewer
  count does not change the configured cycle count, whose default is one.
- The shared cycle now synthesizes original evidence before review. Reviewers
  return corrections or acquisition requests; the researcher acquires evidence
  and synthesis resolves changes. Cheap review never directly replaces premium
  analysis. Monitor freedom and cadence are independent controls.
- Source identity resolution is shared by synthesis, review and memory. Captured
  text remains available in the existing raw-document archive and chunk corpus;
  read windows report their coverage. Relevance ranks the complete candidate
  catalog rather than dropping indirect leads.
- The real Next.js editor has the flow, step settings/instructions/data, model
  profiles, reviewer controls, a change review and measured attempt data. Actual
  GET/PATCH save and full reload passed in an isolated settings scope. Desktop and
  390-pixel phone controls were inspected. A queued run retained its original
  settings after the save; a later run received 12 retained queries, two reviewers,
  Pro/max relevance and exploratory monitoring. Request policy narrowing and
  rejection of invalid policy passed through the real queue repository.
- Transport checks exercised two reviewers, follow-up acquisition and premium
  correction inside one cycle. These checks establish protocol behavior only.
  DeepSeek initially returned HTTP 200 with keepalive whitespace but no completion,
  then HTTP 503 "Service is too busy."
  Provider requests now have a total cancellable HTTP deadline, including those
  keepalives, and attempts record their start before awaiting a response.
- Live Pro arithmetic and a two-Flash-reviewer numerical correction passed personal
  inspection. A real queued numerical answer also passed: six HTTP calls included
  premium planning/synthesis/correction, two parallel Flash reviewers and Flash
  presentation. A typed background page summary passed in 77.6 seconds with one
  shared review cycle and no feature-local formatter/review loop.
  The source investigation read the requested pages but timed out in
  planning and synthesis; a continuation retained those sources and completed.
  That answer still overstated what byte arrival/silence establishes about the
  cause. It is not accepted as a quality pass. The shared synthesis/review prompts
  now explicitly distinguish observations from competing causal explanations.
  A later 90-second synthesis timed out. An evaluation-only 180-second model limit
  completed in 258 seconds but still overstated what timing observations prove.
  A review replay completed but also failed personal acceptance. Longer time alone
  did not solve the reasoning issue. The 90-second model/300-second whole-run
  defaults remain unchanged while the owner's timeout preference is unanswered.
- Source inspection also found the browser reader discarded short values, code
  and table cells and flattened layout. The reader now preserves visible main text
  and structure across browser, HTTP and crawler paths. Page summaries now request
  their typed artifact directly from the shared engine; the second formatter and
  feature-local review sequence were removed. Daily datapoints use the shared
  configurable artifact-review cycle. Other typed critique tasks retain their
  domain schemas and use the editable default review route.
- The review prompt still contained instructions to rewrite the answer despite
  the new responsibility split. It now requests specific corrections and their
  basis; premium synthesis owns the revised analysis. This correction is global,
  and no technical-question rules or answer-specific assertions were added.
  An unseen conflicting financial-records correction passed personal inspection
  in 61 seconds with one Flash reviewer and premium correction; the technical
  source case remains unresolved.
- Real PostgreSQL capture/window/version checks passed with an injected archive
  transport. Archive upload and readback also passed in an execution of the
  existing development research worker, under its pipeline identity and unchanged
  image/settings/job template. The local reader identity remains read-only.
  Full source text survives in run context when capture retention is unavailable.
- Do not deploy this follow-up until real outputs and original evidence have
  been personally reviewed. Local evidence and transport probes are under
  `tmp/llm-flow-20261014/`; no follow-up deployment has occurred.

- The consolidation had covered a provider gateway and newer agent callers,
  while leaving independent Attention candidate planning/search/extraction,
  a separate homepage research/critique sequence, and an additional summary
  evidence pass. Those runtime sequences now delegate to the existing shared
  agent; the duplicated candidate/summary planners and summary research sequence
  have been removed. Domain adapters retain their typed feed output contracts.
- Research and structured model operations enter `aql_zopedia_engine.py`.
  Only its agent implementation and the engine call the provider gateway.
  The gateway alone invokes `generate_json`. The architecture check no longer
  exempts every AQL module and selected feature modules from this boundary.
- Queued research now resolves the same engine budget as direct/background
  callers. Engine results carry a source revision fingerprint so API/worker
  generation differences can be identified and checked during release.
- Conversations reuse `saa_zopedia_chat_threads/messages`; sessions have typed
  conversation/turn links. Legacy parent chains migrate within owner/environment.
  Appends are serialized per conversation, idempotency precedes allocation, and
  a failed turn preserves older messages, evidence and execution outcomes.
- Web and native render conversation transcripts with one composer. Planner
  decisions emit separate public research updates. Private reasoning is omitted;
  meaningful activity survives heartbeat updates. The BFF now carries timezone,
  source scope and visible references instead of discarding those fields.
- Isolated database checks pass for continuity through failure, evidence and
  Markdown preservation, owner checks, source scope, concurrent append handling,
  idempotency and the actual typed API response.
- The first live example retained its numbers across a failed middle turn, but
  attributed an earlier search timeout to that middle turn and added an unrelated
  domain analogy. That first output was rejected; the later continuity acceptance
  is recorded below. The shared correction preserves
  origin run/turn identity on carried acquisition and exposes that identity in
  synthesis context; prompts require relevant examples and concise public updates.
- Task source baseline and private evaluation evidence are retained under
  `tmp/zopedia-continuity-20261014/`. No production application was changed.

## Ownership and design

Keep the shared AQL/Zopedia engine. The required change is its application-owned
conversation, evidence and progress contracts, together with the thin clients.
Use LLM planning and semantic review for research decisions. Code owns identity,
permissions, persistence, deadlines and artifact integrity.

### One conversation contains messages and execution attempts

Evolve the existing shared conversation persistence rather than adding a third
transcript store beside `saa_zopedia_chat_threads/messages` and research sessions.
Give each conversation stable identity, owner and environment; each user message
has a turn identity, and each queued research attempt points to that turn.
Backfill existing parent-run chains within their authenticated owner/environment.

A follow-up appends to this conversation. Web and native retain one transcript
and composer. A separate run per execution remains valid internally. Retries
belong to the existing turn and cannot duplicate the message or fragment history.

Build context from the ordered conversation, linked sources, current findings,
user corrections and execution outcomes. A failed turn cannot cut off earlier
successful research. Give the agent the known parent attempt ID, failure stage,
timestamps and concise recorded error. Provide an owner-scoped read of research
attempts/events when more detail is needed; attention-job traces are a separate
capability. Keep raw infrastructure logs behind diagnostic access.

Continuing after a completed answer and retrying an interrupted attempt have
different semantics. A new message inherits useful evidence and gets a fresh
acquisition budget; the model revises the research goal before executing inherited
pending work. A retry resumes its checkpoint. Preserve source permissions and
write policy explicitly, with no accidental widening across turns.

### Public research updates explain current work

At the existing planner/tool boundary, emit a small typed public update: current
research question, selected action, brief reason, source identity/query, useful
finding and intended next action. The LLM supplies concise summaries of research
decisions; execution facts come from actual tool state. Do not publish private
model reasoning or relabel heartbeats as insights.

Persist the updates with the attempt and render them inside the conversation in
both clients. Keep meaningful current activity visible while heartbeats update
elapsed time. Sources being opened, completed findings and acquisition failures
should be distinguishable. Avoid raw tool names and operational filler in the
default reading path. Private checkpoints remain private.

### Preserve complete evidence and answer structure

Use prose-preserving normalization through synthesis, review, repair, storage,
API and all clients. Carry selected source IDs with the answer so inline links
and its bibliography identify the sources actually used. Keep the larger acquired
catalog available for inspection without attaching every entry to a short reply.

Tool payloads retain complete numerical rows, timestamps, units and source IDs.
The planner may receive a compact preview plus a reference to the complete result;
it must know that a preview is partial and have a direct way to read the rest.
Do not infer missing observations from a display sample. Keep original reporting,
prior generated analysis and new hypotheses distinguishable.

### Spend research time acquiring useful information

Apply the already-developed shared retrieval/adaptive-loop improvements to the
actual executor after re-evaluation. Remove unconditional market classification
from generic intake and hidden source-fetch helpers. Let the model interpret the
question and choose current reports, retained research, conceptual explanation,
numerical work or a combination. Reuse relevant Attention investigations and
their original sources rather than only paraphrasing their generated event page.

Make initialization visible and bounded. Acquire provider-specific dependencies
when their tools need them. Resolve and persist one effective operation budget at
entry, including preparation, model calls, tools and completion. Retain sensible
completion time without allowing a static reserve plus slow setup to leave no
useful research opportunity. Preserve drafts and sources on interruption; report
actual timeout stages and full execution duration.

Use adaptive acquisition limits, not a fixed number of planner/monitor cycles.
Reflection should improve the selected next step. The owner accepts one review
cycle by default and requires its count to be configurable. Keep that control
separate from monitor restarts, tool limits and transport retries. A correction
must have time to execute. Known sources and user corrections
remain available across failure. A transient failure may trigger a bounded retry
or durable continuation of that attempt, without creating a new conversation.

Research and review should explain mechanisms and indirect connections around
the user's question. The model chooses useful customers, suppliers, financing
relationships and competing explanations. Do not encode this incident's firms,
supply-chain path or desired conclusion as rules. Preserve reasonable inference;
use source reading to deepen understanding rather than impose proof gates.

## Implementation sequence

1. Restore the shared Markdown contract and accurate phase/error reporting in the
   existing engine. Instrument the unobserved preparation boundary before choosing
   a fix for its slow dependency. Preserve the deployed-source audit.
2. Implement one conversation owner and message/attempt lineage, including context
   across failed turns and typed execution outcomes. Add the shared attempt-read
   capability with owner/environment checks. Update both clients together.
3. Complete evidence/result access, adaptive budget ownership and public research
   updates. Retain the shared retrieval improvements; correct their remaining
   continuation and presentation gaps rather than treating promotion as completion.
4. Personally evaluate full local outputs and their research paths. Deploy isolated
   development API, web and research-worker images only after those results satisfy
   the request. Verify a queued conversation on the actual dev executor. Production
   promotion requires explicit permission and a release manifest covering that worker.

## Acceptance

Personally read the repaired conversation, including a user correction, a failed
intermediate turn and recovery. Judge whether it understands the changing question,
retains earlier evidence, explains the relevant indirect mechanism, and gives a
readable useful answer without a wall of process limitations. Inspect the sources
it actually found and read. Repeat with unseen freeform questions, paraphrases,
concepts, numerical work, changing current events and retained-only requests.

Use mechanical probes for message/run identity, authorization, retry idempotency,
complete result access, Markdown preservation and effective deadline reporting.
These establish plumbing only. No fixed company, phrase, story or automated
accept/reject verdict establishes LLM quality.

## October 14 implementation acceptance

The live astronomy conversation retained its first explanation across a failed
middle turn, answered the unanswered question, and correctly identified the
injected planning timeout. The next message, submitted through the real browser
composer and worker, reused the same conversation and produced a readable Markdown
table. Both desktop and 390-pixel layouts were personally inspected. Wide tables
scroll inside the answer; the app frame owns the viewport and only its canvas
scrolls. The extra global composer is removed from Zopedia routes.

The first numerical evaluation retained its input but introduced an unrelated
analogy and confused an inherited source failure with the failed turn. That output
was rejected. Carried acquisition now retains origin run/turn IDs, and execution
outcomes distinguish submission, claim, completion and actual recorded phase. The
model receives complete ordered messages and complete numerical tool rows. This
establishes continuity; it does not establish universal numerical-research quality.

A real Attention candidate investigation completed in 219 seconds through the
same engine and produced the complete typed feed narrative and claims. Its source
acquisition remained snippet-heavy and one retained-source search timed out.
Research quality remains open for deeper evaluation. The source adapter now
retains every acquired snippet with its own URL, date and stable source identity,
and identifies it as a provider snippet rather than an opened article. Serialization
context IDs are explicitly distinct from source identifiers. A live acquisition
and materialization check verified the corrected source contract.

Mechanical checks establish architecture and plumbing only: 14 engine-boundary
checks, isolated PostgreSQL ownership/environment/idempotency/concurrent-append
checks, complete inherited evidence, Markdown preservation and API projection.
Web types/lint, the isolated frontend production build and native simulator build
passed. Native source is updated; no physical-device or production installation
is part of this release.

### Development release ownership

All ten shared engine source files are identical across eight Python images,
covering the API and nine AI jobs. Each image starts from that consumer's immutable
accepted image, checks its previous source hashes, applies this task's adapter
delta and validates imports. The release revision is `7242a828ab189e15faa4`.
The frontend preserves the accepted Attention card release. Broker jobs,
production images, resource identities, schedules, commands and runtime settings
are outside the mutation scope. Exact images, rollback images and protected
configuration receipts are retained under `tmp/zopedia-continuity-20261014/`.

Earlier consolidation stopped at transport: broad module exemptions in the
boundary checks allowed feature-local planning/search/extraction/writing loops to
remain. There is now one owner for research planning, tool execution, synthesis,
review, deadlines, progress and continuation. Feature functions define typed tasks
or project results into existing product contracts. The provider adapter only
transports inference; a new feature must not implement another research lifecycle.

### Deployed verification

Both real Azure worker turns completed with revision `7242a828ab189e15faa4` in
conversation `zthread_cad7e99b4f7142e6a2fa54ef`, preserving the kettle example into
the combined mass/power follow-up. The model correctly derived a fourfold ideal
heating time and supplied assumptions. Public updates described that work; the
16- and 17-event journals retained the actual execution phases. These were new
queued worker executions, not local substituted results.

The final frontend build is `cj147`, image
`zopedia-conversation-web-dev@sha256:de37b42da9995b58f207b7b9570476f844f77851051818964eeabb0bd79a663e`.
All eleven dev targets preserved their configuration. Read-only comparison found
all production and other resource images unchanged. New conversation routes serve
successfully and unauthenticated conversation API reads return 401 with no-store.
See the [release receipt](../verification/zopedia-conversation-2026-10-14/release.json).


The final deployed browser path also completed two turns in
`zthread_b103141c39e1458b9416a4d0`: a new freeform evaporation/boiling question,
then a reference-only follow-up requesting a table and a wet-towel explanation.
The URL stayed unchanged; the prior answer remained visible; the table rendered
correctly. Public progress showed the active phase and a useful finding. Existing
legacy run links migrated to six conversations from seven attempts for the dev
review account. The [progress screenshot](../verification/zopedia-conversation-2026-10-14/deployed-progress.jpg)
and [completed transcript](../verification/zopedia-conversation-2026-10-14/deployed-conversation.jpg)
record the actual deployed UI. API revision 30 and web revision 19 are both ready.


## Proposed research-depth repair — October 14 follow-up

Status: rechecked design and implementation handoff, expanded with the owner's
role diagram, configurable single-review default and requested DeepSeek model
split. Implementation is now authorized, beginning with the admin mockup below.
No engine code, runtime configuration or deployment has changed in this follow-up.
The source findings and their limits are recorded in the
[handoff review](../architecture/AQL/audits/ZOPEDIA_HANDOFF_REVIEW_2026-10-14.md)
and [call-count audit](../architecture/AQL/audits/ZOPEDIA_LLM_CALL_COUNTS_2026-10-14.md).

### What the retained evaluation establishes

The 219-second Attention investigation made four `research.live_event_evidence`
calls, one `research.search_evidence` call that timed out at 45 seconds, and no
`research.open_page` or retained full-source reads. The repeated searches added
headlines and snippets; they did not establish article-body acquisition. The final
review identified the offering terms, the specific bond, comparable market moves
and the underlying analyst note as useful missing research, but those requests
were retained as pending work after final synthesis. The absence of page reads
means this evaluation does not establish that the page reader failed.

Current code also exposes separate handoff weaknesses. The prose writer's
outline-based branch supplies answer points and citation metadata rather than
selected original passages. Source selection can contain tool-call IDs, while
filtering in the structured evidence history accepts only chunk/document IDs and
the prose citation filter accepts chunk/page IDs. Those valid references can
therefore omit evidence in later phases. The reader defaults to a 5,000-character
response and caps it at 12,000 without a complete-document range contract;
main-content acquisition is optional. These are source-contract risks independent
of the observed decision never to invoke the reader.

### Change the shared research lifecycle

1. Give the existing engine one source catalog containing canonical document and
   capture identities, dates, complete extracted content or its existing corpus
   reference, actual acquisition state, and which portions have been returned.
   Keep search snippets, article text and generated prior analysis distinguishable.
   Resolve tool-call, document and chunk references centrally, before selecting
   evidence for any consumer. Retain all captures; do not turn duplicated coverage
   into independent corroboration.
2. Make each research decision address the most useful unresolved question against
   that catalog: discover another lead, read an existing source, retrieve a filing
   or dataset, calculate, explore an alternative, or finish. The LLM chooses by
   expected information gain. More search wording is not automatically more depth.
   No fixed article quota, ticker rules, publisher whitelist or mandatory primary
   source for every claim. Primary documents are preferred when they settle the
   material question; commentary and indirect leads remain useful discovery.
3. Strengthen the existing shared reader rather than building an Attention-only
   crawler. For an article read, acquire its substantive body and preserve the
   complete capture through existing corpus/archive ownership. Return meaningful
   passages with stable locations and explicit partial-read metadata; allow further
   reads without losing the rest. An access failure stays a failure, allowing the
   agent to choose a filing, original release or other source. Do not label a
   snippet or a page's navigation as a successfully read article.
4. Preserve the evidence-lookup specialist. It may expand queries and interpret
   reporting dates within the researcher's objective; its own LLM call is not
   inherently redundant. Carry the original request, explicit constraints and
   resulting retrieval plan through the shared clock and progress contract.
   Return usable partial results
   on slow-source failure. Protect actual source reading within the remaining
   budget and retain a small completion allowance rather than spending the entire
   investigation portion searching before review can redirect it.
5. Feed selected original passages, observations and calculations to both writer
   and reviewer. Keep the outline as an organizational aid. The model explains
   mechanisms, compares alternatives and separates observations from inferences.
   Review substantive gaps while acquisition can still act on them; send worthwhile
   questions back into this same loop. At the whole deadline, return useful work
   and retain the actual open questions without an automatic publication gate.

### Acceptance

Replay the retained investigation and then evaluate unseen subjects and freeform
questions, including corrections, conflicting reports, difficult page access and
requests that need no external reading. Personally compare complete answers and
original source captures: did reading change the explanation, resolve an important
number, reveal a contradiction, or redirect the investigation? Do not accept extra
page calls, a source-count target, longer prose or a passing schema as quality.
Record elapsed time, acquisition failures and actual provider attempts alongside
that judgment. All changes belong to the existing engine and reader and must reach
chat and every background consumer together after local acceptance.

### Findings rechecked before planning

The following statements were checked again against current local source. They
describe code behavior, not a new measurement of production model routing or
research quality.

| Finding | Rechecked evidence and qualification |
| --- | --- |
| Source selection can omit valid evidence | `zopedia_agent._tool_history_prompt` filters candidate rows by chunk ID or a document fallback. `_final_user_prompt` uses chunk/page IDs. Neither expands selected tool-call IDs. The retained local Attention run uses tool-call selections; it predates the newer candidate projection, so it does not prove that this filtering defect occurred in that execution. |
| Prose writing loses selected source content | The outline branch of `_final_user_prompt` sends answer points and citation metadata but omits original passages and the supplied draft. The reviewer receives fuller tool history. |
| Review count is not numerically configurable | `Agent answer judge enabled` is an on/off parameter. `reviewed_draft` and completed-review status implement an effective single completed review; failed attempts and provider retries are separate. |
| Late review feedback is pending work | Early requests may resume acquisition. Completion-time requests are stored without further acquisition in that attempt. The retained investigation contains four such requests. |
| Knowledge writes are disabled by particular callers | Queued chat fixes `none` at creation and worker execution; Attention candidate research also passes `none`. The separate news/business job defaults `ZOPEDIA_NEWS_BUSINESS_WRITE_POLICY` to `safe_auto` and has a real typed commit path. This is not global write unavailability. |
| Hybrid retrieval is already implemented | PostgreSQL full-text and substring candidates are combined with independently retrieved pgvector candidates through reciprocal rank fusion, then source diversification. It is not BM25 or substring-only retrieval. |
| Cheap thinking does not select a cheap model | `_synthesis_llm_client` returns the primary client, including for review and monitoring. Monitor effort and schema-repair thinking have overrides, but no shared role-to-model selector exists. |

Preserve the diagram's researcher, monitor, evidence lookup, synthesis, review and
memory responsibilities. One shared lifecycle can contain these separate roles.
A prose writer after reviewed analysis is compatible with one review cycle when
the handoff preserves the analysis and evidence.

### Implementation handoff for the next agent

This is the authorized source-fix sequence. The admin interaction design is the
first deliverable; use its reviewed direction when binding the actual settings.

1. **Establish the task delta.** Read `documents/philosophy.md`, this plan and both
   audits. Record current source hashes and the accepted development images before
   editing. The workspace contains substantial unrelated changes; isolate this
   work and preserve the accepted conversation, Attention-card and native releases.
2. **Resolve source references once.** Extend the existing shared evidence
   representation in `services/zopedia_agent.py` and
   `services/aql/evidence_pack.py`, using existing document/chunk/capture ownership
   in `services/saa`. Expand a tool-call selection into that call's evidence;
   resolve document, page and chunk references consistently even when a candidate
   contains several identities. Include supplied evidence and ordinary numeric
   tool results. Preserve origin run/turn, dates, capture identity and source kind.
   Distinguish an intentionally empty selection from an unresolved selection;
   never silently turn an unresolved valid reference into an empty evidence set.
   Pass the same resolved selection to synthesis, citations, review and memory.
3. **Repair the writer/reviewer packet.** Update `_final_user_prompt`,
   `_judge_user_prompt` and their shared context assembly. Supply selected original
   passages, observations, calculations and relevant draft alongside reviewed
   points and citation metadata. Keep originals, prior generated research and
   hypotheses identifiable. The complete source catalog stays available for
   further research. Avoid separate writer-only selection rules and shortened
   source copies that sever qualifications or numerical rows.
4. **Make review cycles explicit.** Add a validated shared-engine count with
   default `1`; `0` disables review. Define a cycle as draft/analysis review,
   optional acquisition or analytical correction, and application of that
   correction. A further review consumes another cycle. Persist the effective
   count, completed cycles and what draft/evidence the review covered in existing
   run/checkpoint state. Retries do not consume a substantive cycle. Preserve the
   legacy enable switch as an explicit compatibility constraint rather than two
   contradictory settings. Give the accepted cycle useful acquisition time when
   possible; at deadline retain a valid answer and label requests as pending.
   Do not create another conversation or imply that pending work executed.
5. **Carry knowledge policy end to end.** Resolve the authenticated task's server
   policy once, persist it in the session and use that value in the worker,
   registry and memory stage. Remove the worker's unconditional replacement with
   `none`. Support `none`, `propose` and `safe_auto` through existing audited typed
   mutations; invalid policies must be rejected. Request/model instructions may
   narrow the server policy. Preserve current defaults until explicitly configured;
   do not broaden private chat into shared knowledge automatically. Keep the
   separate news/business writer and its policy intact. A global memory-enable
   toggle must not be mistaken for a grant of write authority.
6. **Expose acquisition state.** In `services/zopedia_research.py`,
   `services/saa/storage.py`, `services/saa/semantic_index.py` and the shared reader,
   carry requested/actual retrieval modes, semantic unavailability, capture/read
   status and partial-range information. A retained search preview is currently
   400 characters; `read_evidence` can return the full chunk. Page reads default
   to 5,000 characters and cap at 12,000. Preserve complete captures through the
   existing corpus/archive and support further reading of the same capture.
   Keep lexical fallback useful while exposing its actual mode. Preserve the
   current hybrid ranking; a BM25 replacement is not part of this fix.

Relevant entry points beyond those above are `services/aql_zopedia_engine.py`,
`services/agents/research_runs.py`, `services/agents/research_session.py`,
`services/agent_tools.py`, `services/aql/collector.py` and `pipeline/jobs/main.py`.
Update public API/client projections only where the shared result contract needs
them; raw diagnostics and model reasoning remain private.

### Proposed DeepSeek routing

Status: implemented locally; release acceptance is in progress. The owner wants
the premium model for analysis/reasoning and the cheapest DeepSeek tier for
peripheral work. These are editable defaults, not restrictions: an admin may
select a different model and thinking level for any step, including relevance.
Configure responsibilities centrally rather than changing the model globally
for every caller.

| Responsibility | Proposed model | Thinking |
| --- | --- | --- |
| Research planning, choosing useful investigations and interpreting tool results | `deepseek-v4-pro` | Enabled, `max` |
| Substantive synthesis, causal/financial/numerical reasoning, resolving conflicting evidence | `deepseek-v4-pro` | Enabled, `max` |
| Interpreting durable knowledge changes and resolving fact conflicts | `deepseek-v4-pro` | Enabled, `max` |
| Trajectory monitor | `deepseek-flash` | Enabled, `low` |
| Review and consistency checks, default one cycle | `deepseek-flash` | Enabled, `low` |
| First-pass source summaries and faithful extraction | `deepseek-flash` | Disabled |
| Retrieval query expansion and date/metadata interpretation within an existing objective | `deepseek-flash` | Enabled, `low` |
| JSON/schema repair, source-body block selection and faithful presentation of completed analysis | `deepseek-flash` | Disabled |

First-pass summaries help navigate evidence; originals must still reach the
premium analysis. A cheap review may identify errors and suggest corrections.
Substantive new reasoning or disputed conclusions return to the premium analysis
role for resolution within the same review cycle. Do not let a cheap reviewer or
presentation writer silently become the final author of the analytical thesis.
A cheap writer may improve wording and format while preserving reviewed content.
Existing mixed-purpose `agent.final_synthesis` calls must declare whether they
perform analysis or only presentation before routing them.

Do not route by `formatter_over_aql`, feature name, output schema or a keyword
classifier: several structured operations perform substantive reasoning. Each
engine operation declares its responsibility as a typed execution contract; the
configuration maps that responsibility to model and thinking settings.

**Implementation locations and controls.** Extend the existing engine/gateway,
`services/zopedia_runtime.py`, `services/llm.py` and `services/llm_execution.py`;
keep provider transport and the shared inference boundary. Use two model profiles
with explicit per-role thinking settings. Expose them through the existing
environment-scoped LLM configuration rather than feature-specific clients.
Resolve the role profile after shared provider defaults, so the current global
`runtime:{provider}:model` override cannot collapse both profiles to one model.
Nested operations must explicitly resolve their own role instead of inheriting
the parent's `max` effort. Preserve dependency injection for transport checks.

Mechanical JSON repair inside the provider adapter currently reuses the same
model with thinking disabled. Include those hidden requests in the routing work;
lowering effort alone does not meet the cheap-model requirement. Keep the
original paid draft and full schema context, and record every repair attempt.
Do not silently downgrade analytical work to Flash on a provider failure.

Reuse existing attempt metering to record role, routing-policy version, requested
and reported model, thinking/effort, input/cached/output tokens, repairs, failures
and estimated charge. Include effective role/profile settings in artifact reuse
identity and continuation metadata. Reusing an older artifact must not make it
appear to have been generated by a newly selected model. Stable evidence prefixes
and full context remain intact.

**Published models and prices checked October 14, 2026.** The current official
pricing table lists the following USD rates per million tokens:

| Model | Off-peak uncached input | Off-peak cached input | Off-peak output |
| --- | ---: | ---: | ---: |
| `deepseek-v4-pro` | $0.66 | $0.022 | $1.98 |
| `deepseek-flash` | $0.15 | $0.003 | $0.60 |

Peak rates are twice these rates. Use the provider's current time/holiday rules
when measuring spend; do not change job schedules as part of this proposal.
The source is [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/),
corroborated by the repository's October 12 `services/llm_prices.json` snapshot.
The latest [official change log](https://api-docs.deepseek.com/updates/) confirms
continued V4 Pro API service after September 14; an older September launch page
still describes a planned redirect to Flash and must not override that update.
`deepseek-flash` currently identifies V4.1 Flash. Pro is the higher-priced listed
tier; price alone does not establish that it beats the newer Flash on our tasks.
Honor the requested premium-analysis policy and compare complete outputs before
claiming a quality advantage or deployment readiness.

DeepSeek supports explicit thinking on/off and `low`, `high`, `max` effort;
see [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/).
The retained October 14 dev API/worker environment receipts request the legacy
`deepseek-reasoner` name, also seen in a retained local run. That does not establish
Pro routing, and database overrides can alter effective configuration. The next
implementer must inspect actual requested/reported identities. Replace legacy
examples/default assumptions with supported model names during implementation,
without claiming that a new live model invocation occurred in this planning turn.

### Acceptance and release handoff

Use transport checks for source-ID resolution, complete numerical rows, policy
propagation, review counts/checkpoints and requested model/effort on every actual
attempt, including nested repair. Those checks establish plumbing only.

For quality acceptance, replay retained company/event inputs and use unseen
questions, corrections, conflicting sources, retained-only work and a request
needing no outside sources. Personally read the original captures, premium
analysis, cheap review, any premium correction and final answer. Reject loss of
facts, numbers, source qualifications, alternative explanations or continuity.
Inspect actual acquisition and pending feedback; extra calls or longer answers
do not establish better research. Compare complete baseline and proposed outputs,
latency and total measured spend including recovery. No workload savings percentage
or superior-model claim is established by the pricing table alone.

When implementation and local acceptance are complete, release
the same engine revision to the development API, queued worker and every affected
background consumer using their accepted image baselines. Preserve rollback
images and record actual model routing on a queued dev conversation and a
background investigation. Production promotion requires explicit permission.

## Admin LLM flow editor — mockup and implementation design

Status: interactive mockup approved and implemented in the actual Next.js admin
editor. The prototype below remains a design reference; its controls do not save
application settings. The actual editor does save through the existing admin API.
The editable source is
[`output/mockups/admin-llm-flow.html`](../../output/mockups/admin-llm-flow.html).
It contains two layouts, **Flow canvas** and **Step list**, sharing the same
local configuration. Flow canvas is the recommended direction because the
research, evidence, review and memory loops stay visible while a step is edited.

### Interaction and organization

- Open Admin → LLM to the actual shared research flow. Select a step to inspect
  **Settings**, **Instructions**, or **Data**. Data shows what the step receives,
  produces and hands onward. Models is a separate compact tab for shared model
  definitions. Do not retain a global undifferentiated tuning-parameter list.
- Keep the visible graph tied to the existing engine: researcher; monitor;
  search, source reading, relevance and first-pass summary inside evidence lookup;
  synthesis; reviewers; answer; knowledge updates. Show sources returning to the
  researcher, review corrections returning to synthesis, review research requests
  returning to the researcher, and knowledge feeding later evidence lookup.
  This is a configuration map; it does not imply every request executes every box.
- Put model, thinking and the step's principal workload controls first. Keep
  deeper parameters attached to the tool/step that actually uses them. Instructions
  belong with their step rather than a separate wall of unrelated prompt editors.
- Search exposes queries per lookup, results per query and parallel queries.
  These differ from the researcher's whole-run tool allowance and time budget.
  Increasing query volume must not silently raise every other budget.
- Relevance has its own model and thinking choice. An admin can spend more here
  while keeping search planning, summaries and monitoring cheap. Semantic ranking
  organizes useful sources and indirect leads; it does not discard the full
  candidate catalog or become a rigid publication gate.
- Monitor frequency and research freedom are independent. A more exploratory
  instruction lets the researcher pursue adjacent leads; frequency controls when
  the monitor gets a chance to advise. Implement the semantic instruction in the
  monitor's prompt, not a made-up relevance score or phrase rule.
- **Reviewer count and review cycles are separate.** Add independent reviewers
  within one cycle; each has its own model, thinking and review focus. Reviewers
  receive the same draft and original evidence and can run in parallel. Synthesis
  resolves disagreements and substantive corrections. Do not implement majority
  voting or automatically require an extra cycle because another reviewer exists.
- Knowledge policy stays with the knowledge stage. Saving an admin configuration
  uses the existing authenticated, environment-scoped authority; an individual
  research request may still narrow that policy.
- Review changes as one readable diff before saving the configuration. Apply a
  saved revision to new runs. Existing attempts retain the settings they began
  with. Keep operational diagnostics and credentials out of the ordinary flow.

### Backend contract and implementation order

1. Extend the existing shared configuration/parameter and prompt registration
   metadata with stage ownership and control bindings. The engine owns the stage
   graph, input/output descriptions, values and effective model route; the admin
   endpoint projects that same contract. Do not maintain a separate frontend map
   that claims a knob exists while the executor ignores it.
2. Use the existing admin GET/PATCH boundary and audited environment-scoped
   settings persistence. Validate the complete draft and write it atomically.
   Preserve and deliberately migrate existing overrides. Keep provider connection
   settings separate from per-step model and thinking choices. This is a focused
   editor for the real research engine, not an arbitrary workflow builder.
   Retain the global language-rule editor and expose its shared scope alongside
   stage instructions. Keep corpus embedding configuration distinct from the
   relevance LLM; changing a chat model must not imply changing embedding space.
3. Resolve a run's complete effective configuration once at submission/start,
   including prompts, stage models, thinking, workload limits, reviewer list,
   cycle allowance and knowledge policy. Store a snapshot or immutable revision
   reference in the existing session/run metadata. Carry it through the queued
   worker, nested helpers, retry and continuation. Do not reload changed global
   prompts or model overrides halfway through a run.
4. Implement the source/reference and writer/reviewer handoff repairs before
   judging the model split. Then bind typed step responsibilities to the configured
   model route, including nested output repair. Keep clients/configuration
   immutable per call so parallel reviewers cannot change one another's model.
5. Execute the configured reviewers in the same engine and whole-operation clock.
   Retain each actual review result, the draft/evidence it considered, and pending
   work on timeout. The synthesis role receives corrections and resolves analytical
   changes using its configured model. Preserve useful answers if an optional
   review fails; extra reviewers are useful perspectives, not admission gates.
6. Replace `web/components/admin/llm-settings.tsx` with the reviewed flow editor,
   remove the misleading single-global-model summary from `web/app/admin/page.tsx`,
   and project the shared contract through `services/admin_view.py` and the existing
   API/BFF. Read `web/AGENTS.md` and the installed Next.js documentation before
   implementation. Include prompt, token allowance, timeout and provider-specific
   controls under the stage that owns them; explicitly account for legacy settings.
7. Expose observed run data through the same step identities and existing journals
   and attempt ledger. Show actual model, input/output references, calls, time and
   measured spend when available. Configuration maxima are ceilings, not forecasts
   of executed calls. Do not populate the interface with invented run metrics.

### Mockup acceptance and next implementation checks

The prototype uses local state only and proposed defaults; it is not a view of
deployed settings. It was inspected in the real browser at desktop and phone
widths. The following interactions were personally exercised: increasing search
queries from 5 to 12 on Flash, adding a second Flash reviewer while retaining one
cycle, changing relevance to Pro/max, making the monitor exploratory and changing
its cadence, switching layouts and inspecting a stage's data handoff.
The model definitions, complete change-review page and local save were also
exercised. JavaScript syntax and narrow-screen control overflow were checked.
These checks validate the prototype, not the pending executor bindings.

Before release of the real implementation, repeat those actions through the
actual admin API, reload the page, and verify a fresh queued chat and background
investigation use exactly those settings. Inspect requested/reported models and
every reviewer result rather than trusting the UI labels. Personally read the
complete outputs and original evidence. Verify an in-flight run keeps its earlier
configuration after an admin save, and a later run receives the new revision.
Only then release the accepted shared engine and admin frontend to development.
