# Zopedia/AQL handoff review — October 14, 2026

Status: historical findings before the repair. Runtime changes are now implemented
locally and undergoing acceptance; this document preserves the original evidence.
The rechecked findings, implementation sequence and current DeepSeek routing are
maintained in the [conversation and research repair plan](../../../plans/ZOPEDIA_CONVERSATION_REPAIR_2026-10-14.md#implementation-handoff-for-the-next-agent).
Scope: current local shared engine, queued chat, Attention candidate research,
retained retrieval and the background news/business memory writer. Existing
October 14 local records inform the review; no new model execution, build or
deployment was performed. This does not establish the configuration or retrieval
quality of every currently deployed consumer.

## Design and review-cycle requirement

Preserve the owner's researcher, monitor, evidence lookup, synthesis, review and
memory responsibilities. The owner accepts one review cycle for now, provided
the cycle count is configurable. A writer may express reviewed analysis after
that review; its existence alone does not require another review call.

The implementation currently exposes `Agent answer judge enabled`, default 1,
in `services/zopedia_agent.py:98`. It is an enable/disable switch. There is no
numeric setting for one, two or more completed review cycles. The loop uses
`reviewed_draft` (`:3175`, `:3323`) and skips completion review once a review has
completed (`:3721`). Failed review attempts may be retried by the structured
completion path. Monitor restart configuration is a separate control.

Conclusion: an effective single successful review is implemented, but the user's
requested configurable cycle count is not. Preserve a default of one when adding
an explicit shared-engine setting. Count substantive review cycles separately
from provider retries and failed review attempts.

## Why direct knowledge writes are disabled in some paths

The backend supports `none`, `propose` and `safe_auto`. Typed mutations include
source-backed page upserts, page links and metadata patches, with mutation audit,
rollback and a proposal path for risky operations. See
`services/saa/zopedia.py:3740` and the existing engine guardrails' Write Policy.

Queued chat fixes policy to `none` at session creation, persists that value, and
passes `none` again in its worker (`services/agents/research_runs.py:430`, `:445`,
`:1168`). The public create request has no write-policy control. The engine then
filters write/proposal tools out and skips post-answer memory maintenance
(`services/zopedia_agent.py:1535`, `:3798`). Toggling the post-answer-memory
setting cannot override the caller's policy.

Attention candidate research separately passes `none`
(`services/aql/collector.py:815`). This does not mean the entire background
platform cannot update knowledge: the news/business job reads
`ZOPEDIA_NEWS_BUSINESS_WRITE_POLICY`, default `safe_auto`
(`pipeline/jobs/main.py:2405`), and commits generated pages through the typed
mutation API (`:984`, `:2468`). These are source defaults, not a claim about the
live job's environment value.

The documented general policy makes research reads the default. There is no
additional per-caller rationale in the examined code explaining why every queued
chat and Attention candidate must forbid even proposals. This is explicit
application policy/wiring, not a missing model or database capability. Any change
should resolve the policy at the authenticated task boundary and carry it through
creation, execution, tools and memory maintenance consistently. A model can narrow
that policy, never grant itself a broader one.

## Is this proper information retrieval?

Yes. The actual retained search has three complementary components:

- PostgreSQL indexed full-text candidates, ordered with `ts_rank`.
- Substring candidates supplementing exact names, ticker strings and acronyms.
- Corpus-wide pgvector embedding candidates, independently retrieved and combined
  with the lexical candidates using reciprocal rank fusion.

The fusion is implemented in `services/saa/storage.py:2170`; semantic retrieval
is in `services/saa/semantic_index.py:102`. Candidates are deduplicated by source
document and diversified among original reporting, derived research and knowledge.
The final order therefore intentionally includes source diversity as well as the
fused relevance score. It is not simply substring matching, nor is its lexical
ranker BM25. PostgreSQL's ranking functions do not use global corpus statistics.

Primary documentation supports this architecture:
[PostgreSQL full-text parsing and ranking](https://www.postgresql.org/docs/current/textsearch-controls.html)
and [pgvector hybrid search and rank fusion](https://github.com/pgvector/pgvector#hybrid-search).
These support the method, not a claim that this corpus's current results are good.
The present source review provides no basis to replace the retrieval stack solely
because it uses PostgreSQL instead of BM25.

## Handoff findings

### High: source selection does not resolve the advertised identities consistently

The planner can select tool-call IDs; the retained Attention run actually selects
five `agtc_...` identities. `_tool_history_prompt` (`zopedia_agent.py:1078`)
instead filters candidate sources by a chunk ID, or document ID only when a chunk
ID is absent. The prose writer's citation filter (`:1755`) accepts chunk/page
IDs. Neither expands a selected tool-call ID into that call's sources. Document
selection can also fail when a candidate contains both document and chunk IDs.
An existing candidate list that filters to empty does not fall back to the full
evidence context. Valid selection can therefore remove passages or citations.

This is established by the current code contracts. The retained 219-second run
predates the newer candidate-source projection, so its successful answer is not
evidence that this exact newer filtering failure occurred in that execution.

Resolve tool-call, document, page and chunk references once through the shared
engine's existing source records, then give every consumer the same selection.

### High: the outline-based prose writer loses the original selected evidence

When answer points exist, `_final_user_prompt` (`:1755`) supplies the outline and
source-title/URL/date metadata. That branch omits source passages and the available
draft, despite receiving the draft as an argument. The reviewer receives full
tool history (`:1862`), but the subsequent prose writer cannot inspect original
passages, numerical rows or qualifications behind those points.

This is a handoff weakness independent of how many review cycles are allowed.
Carry the reviewed points together with selected original passages/observations,
source references and relevant draft context into the writer. Preserve the
complete source catalog separately for further research.

### Medium: review feedback has different execution semantics depending on timing

An early review can place research requests into planning state and continue the
loop while acquisition time remains (`:3348`). Requests from completion review
only become pending actions (`:3779`); they do not cause more acquisition in that
attempt. The retained Attention example has four such pending requests. A future
conversation turn can inherit work, but completion-time feedback is not an
automatically executed follow-up investigation.

Make the distinction explicit in the shared result/progress contract. Schedule
the accepted review cycle while useful acquisition can still act on it when the
task needs that feedback. At deadline, preserve the useful answer and pending
questions. Do not introduce mandatory extra cycles or publication gates.

### Medium: review status is not tied to a particular revised answer/evidence set

A completed early review prevents another completion review even after more
research. Prose writing can then change the output, while the result still reports
`answer_status="reviewed"` (`:3863`) and memory eligibility uses the earlier review
verdict (`:1915`). This is not evidence that a second cycle is always necessary.
The contract should identify whether the review applies to the analysis, draft
or returned artifact, and which evidence revision it considered.

### Medium: retrieval degradation and partial content are weakly exposed downstream

Semantic-search exceptions are logged and lexical results remain usable
(`services/saa/storage.py:2113`). An absent embedding client also returns no
semantic candidates. The caller does not receive a typed indication that a
hybrid request ran with lexical retrieval only. This is a possible failure mode,
not an observed embedding outage in this review.

Retained search previews cut chunks to 400 characters
(`services/zopedia_research.py:1012`); `read_evidence` can return the complete
retained chunk (`:1038`). Live page reading returns 5,000 characters by default
and at most 12,000 (`:1064`), without a document-range continuation contract.
These distinctions should survive lookup, writer and reviewer handoffs as actual
acquisition/read state. An excerpt is useful discovery input; its presence alone
does not establish that the source was read completely.

## Recommended work order

1. Correct shared source-reference resolution and the evidence supplied to the
   final writer. Reuse the existing source/document/chunk ownership.
2. Add the explicit review-cycle setting with default one; clarify what the
   review covered and whether requested research actually ran.
3. Resolve knowledge-write policy at task entry and preserve it end to end;
   distinguish chat, candidate research and the existing background memory job.
4. Expose retrieval mode and partial-read state through the same evidence contract.

Evaluate transport with complete retained records and identity/schema probes.
Judge LLM quality by personally reading complete answers against the sources,
including unseen questions, corrections and late review feedback. This audit
establishes source-level findings, not completed repairs or qualitative acceptance.
