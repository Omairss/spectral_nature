# Zopedia/AQL LLM call counts — October 14, 2026

Scope: the pre-repair engine and retained October 14 evaluation records.
The baseline audit below is historical. The approved
[repair plan](../../../plans/ZOPEDIA_CONVERSATION_REPAIR_2026-10-14.md)
now records a local implementation and its acceptance evidence; that follow-up
has not been deployed.

## Local implementation after the audit

For a simple ad hoc question that needs no tools, correction, repair or knowledge
update, the default flow is **four calls**: researcher → synthesis → one reviewer
→ answer presentation. A substantive correction adds a synthesis call. Tool-using
research repeats researcher decisions and acquisition as useful; it finishes
through the same synthesis/review path. These are not fixed total call counts.

| Work | Default routing and call behavior |
| --- | --- |
| Research decisions and substantive synthesis/corrections | Pro/max; one call per executed decision or synthesis |
| Monitor | Flash/low; configured cadence or researcher request |
| Review | Flash/low; configured reviewers per executed cycle, one cycle by default |
| Retained search | Flash/low query planning, then the independently configured relevance model if candidates exist |
| Source reading | Capture/read is deterministic; first-pass summary uses Flash without thinking; source-cleaning model work is conditional |
| Answer presentation and JSON repair | Flash without thinking; repair only when required |
| Knowledge maintenance | Pro/max, subject to the saved authenticated policy and actual run state |

Typed operations with supplied evidence can call their declared stage directly.
Page summaries request a typed artifact from the shared research/review engine;
they no longer add a second feature-local review. Daily datapoints review an
existing typed draft through that same configurable cycle. Independent domain
critique operations use the editable default review route.

Admin → LLM projects the actual backend routes, workload, prompts and measured
HTTP attempts. Reviewer count and cycle allowance are separate. A saved
configuration applies to new runs; queued/in-flight attempts retain their snapshot.
Provider retries, JSON repairs and embedding requests are individually metered;
embedding requests are distinct from the chat calls described here.

## Historical baseline

The subsequent [handoff review](ZOPEDIA_HANDOFF_REVIEW_2026-10-14.md) records the
owner's acceptance of one configurable review cycle, the current missing numeric
cycle setting, explicit knowledge-write policies and the complete hybrid retrieval
method, including reciprocal rank fusion.

## Architecture clarification against the owner's diagram

The owner's diagram remains broadly accurate: Attention and user requests enter
the shared Zopedia/AQL engine; its researcher chooses tools and receives results,
with a separate monitor available; evidence lookup draws on live search and
retained evidence; synthesis and review prepare the result; authorized memory
updates improve retained knowledge.

These are responsibilities, not a fixed list of model calls. The researcher can
make many decisions, an evidence-lookup specialist can plan several retrieval
queries, and the monitor need not run on every iteration. A retrieval-planning
LLM call can legitimately belong to the evidence-lookup role. Its existence alone
does not establish duplicated responsibility or justify removing that role.

Current implementation qualifications:

- The shared engine owns the lifecycle, with distinct model prompts/functions for
  research, monitoring, synthesis, review and memory. These need not be separate
  deployed services or separate models.
- The monitor is called when the researcher requests an extra check, after some
  tool history exists. It is not continuously invoked beside the researcher.
- Structured research commonly follows research, synthesis, review. Prose chat
  can instead follow researcher draft, review, final synthesis/rewrite. That final
  rewrite has no second review, so the drawn synthesis-to-review order is not
  consistently implemented.
- Serper supplies live search. Retained retrieval uses PostgreSQL full-text
  ranking and substring candidates plus independently retrieved corpus embedding
  candidates, combined with reciprocal rank fusion and source diversification;
  the diagram's BM25 label differs from the literal current lexical ranker.
- Memory updates are available but conditional. Queued chat and Attention
  candidate research use write policy `none`; saving a run or retained source is
  separate from mutating Zopedia knowledge. Other authorized workflows can ingest
  sources and update memory.
- Review can request more research during the acquisition phase while time
  remains. Requests produced by completion-time review remain pending work.

The earlier conversational recommendation to collapse these responsibilities
into one model role was premature. The relevant review is whether each role
receives the right context, performs useful work and hands its result onward
correctly. No engine redesign was implemented by this audit.

## Ad hoc chat

Queued chat enters `run_aql_zopedia_agent` through
`services/agents/research_runs.py:execute_next_queued_run`, with
`write_policy="none"`. Direct and background research use the same engine.

An ordinary successful no-tool prose answer makes three logical model calls
with the default reviewer enabled:

1. `agent.research_step` chooses `final` and supplies a draft.
2. `agent.answer_judge` reviews that draft.
3. `agent.final_synthesis` writes the prose answer.

The last writing call still runs for prose even after the planner supplies a
reviewed final draft. There is no second review of that final prose rewrite.
The owner accepts one review cycle; a separate writer is compatible with that
choice when the evidence and reviewed analysis survive the handoff correctly.
The retained local conversation evaluation records this three-stage path.

Each executed tool normally needs a preceding planner call; stopping before
the tool budget is exhausted normally needs another planner decision. Tool
discovery, invalid decisions, requested trajectory reviews and research requested
by answer review can add calls. A failed or expired run can execute fewer stages.

## Structured research

`run_aql_zopedia_structured_agent(max_tool_calls=0, review_answer=False)`
normally makes one direct call from supplied context. Retained artifact reuse
can avoid that call; malformed output can require additional repair calls.

Tool-using structured research runs the same planner loop. It ordinarily adds
one synthesis and one answer review. A valid structured final draft from the
planner can replace the separate synthesis. A straightforward investigation
with five executed tools therefore has about seven to eight outer logical calls,
before calls inside tools, repairs, monitoring or additional review-directed work.

The engine defaults to ten budgeted tool calls and a 300-second whole-operation
budget. These are configurable defaults, not a ten-model-call limit.
`tools.search` discovery and supplied/carried evidence do not consume that tool
budget. There is no separate fixed total LLM-call cap.

## Calls inside tools and optional completion work

- `research.search_evidence` makes one structured `retrieval.plan` call for a
  nonempty search request, then performs retained retrieval. Semantic embedding
  requests are separate from chat-generation calls.
- `research.live_event_evidence` executes the supplied search query with
  `allow_llm_query=False`; its symbol searches receive `llm_client=None`.
  It no longer adds an unconditional market-intent classifier.
- `research.open_page` and ordinary retained evidence/page reads do not make a
  synthesis call themselves. Other discovered analysis or ingestion tools may.
- A requested trajectory monitor adds one call per actual check; it does not run
  automatically after every tool. The restart limit is not a model-call limit.
- Authorized post-answer memory reflection adds one call and can add one memory
  contract-repair call. Queued chat and Attention candidate research explicitly
  use write policy `none`, so they skip this stage.
- Provider retries and JSON repair can produce multiple HTTP attempts for one
  logical gateway call. Exact provider totals belong to provider-attempt receipts.

## Retained Attention example

`tmp/zopedia-continuity-20261014/qualitative-attention.private.json` and
`attention-evaluation.log` describe the 219-second local run
`agrun_61492a78bb`, engine revision `26db2eef6cab7456b99c`:

- Six planner calls, one final synthesis and one answer review: eight observed
  outer model stages.
- Four live-event searches and one retained-evidence search were executed.
  The additional `research.supplied_context` row is input, not an executed tool
  or a model call.
- The retained search enters an additional retrieval-planning model operation;
  it later times out. This makes nine logical model operations along that path,
  before any provider retries or repairs. The retained stage log does not establish
  the exact number of HTTP attempts.
- No trajectory-monitor or post-answer-memory stage ran.

A whole Attention job sums separate company/event investigations, source-event
nomination, the homepage investigation and any other invoked structured operations.
It has no single fixed per-job call count.
