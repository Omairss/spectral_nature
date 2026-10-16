# Zopedia conversation failures on October 14 2026

The latest production conversation confirms four connected failures: the product
uses execution runs as conversations, the worker does not retain usable research
state across turns, answer review destroys Markdown, and research spends too much
of its budget directing and reviewing shallow acquisition. Attention improvements
have not established equivalent behavior in the interactive research worker.

This is a diagnosis and repair design. Application code and deployments were not
changed. The [repair plan](../../../plans/ZOPEDIA_CONVERSATION_REPAIR_2026-10-14.md)
defines implementation and acceptance.

## Conversations reviewed

Read the complete three latest production answers, their full retained tool
records, all 135 durable events, session metadata and checkpoints. Also read the
October 8 development answer and follow-up for comparison. Times below are PDT
on October 14; persisted evidence uses UTC.

| Request | Run | Local start | Result |
|---|---|---|---|
| Explain the CoreWeave event and the AI selloff | `agrun_2d63e452cdbc46fdb1527c5b` | 09:40:51 | Completed; 5,762 characters, zero newlines |
| Explain insurer financing and the indirect ASML connection | `agrun_68b405c1cb744d58929f10ee` | 09:50:37 | Failed; zero tools, no answer |
| Explain why the follow-up failed | `agrun_208cf0490b9444079e85c654` | 10:02:27 | Completed; 2,876 characters, zero newlines; investigated unrelated records |

All three have empty `checkpoint_json`. The second and third have the correct
immediate `parent_run_id`. A parent link alone does not provide a conversation.

## Deployed code was verified separately from the working tree

Azure execution history confirms that all three jobs used production worker
`sn-research-run`, image `pipeline-reliability@sha256:5dfb67be690d8b28de96f8ef304ec2092ccc64039b651f65f0498e5b8e9cdf74`.
The private production API is `sn-api-next--0000006`, image
`ios-analytics-api-prod@sha256:c5ebfdb1e439fead4868ae3e57e0db1f3c77408cb023bdd073e521c319cc7219`.
Both image names use registry `snpipelineacr03130136.azurecr.io`.

Extracted six relevant source files from each pinned image and verified downloaded
layer digests. The worker and API have identical deployed `research_runs.py` and
`research_session.py`, and different `zopedia_agent.py` versions. Both retain the
old continuation contract. The worker retains the Markdown defect, fixed planner
loop, mandatory trajectory monitoring and lexical-only retained search.

The October 8 repair is documented as a development release. The current dev
research worker uses a later `llm-cost` image. The modern web image is shared
across dev and production, but the research executors are different. Updating a
frontend or API cannot update a separately deployed queued executor.

## 1. Follow-ups are new runs with incomplete context

Web `ResearchRunView.submitFollowUp` posts to `/api/research-runs` and navigates to
the returned child run. Native `ResearchComposer` uses the same API; a follow-up
opens another research sheet. The list endpoint returns individual runs, and
both clients render those runs as conversation entries. Neither presents a
continuous transcript with one stable conversation identity.

Deployed `services/agents/research_runs.py:741` reads only the immediate parent's
answer. If that answer exists, it concatenates the previous question and answer
into a new query. It passes no typed `conversation_history`, evidence state,
checkpoint or parent execution details to the engine. If the parent has no
answer, it passes only the new question. It does not walk to earlier successful
turns. The latest meta-question therefore lost both the original CoreWeave
research and the failed parent's execution context.

The full blob confirms this: the substantive follow-up has a 6,357-character
expanded query; the later failure question has only its 66-character new query.
No `conversation.prior_answers` tool was available in those runs. The scratchpad
read does not establish persistent conversation memory.

Current local code already carries ancestor messages and checkpoints. It still
uses runs as the product's conversation model, compacts older answer text, and
does not provide the agent with typed failed-attempt details. Promoting it alone
does not complete this repair.

## 2. Execution events do not explain the research

The journal records generic lifecycle text, tool names and repeating heartbeats.
The clients display `event.message`; they do not render useful structured tool
payloads such as selected queries or source links. The most recent event replaces
the current status, so a heartbeat readily displaces a meaningful action.

`ResearchSession.progress_callback` replaces planner messages with generic text.
Private model reasoning is removed, appropriately, but there is no separate public
research update containing the current question, chosen action, concise rationale,
finding and next step. Current local checkpoints contain `research_state`, but
checkpoint callbacks return before becoming public events. Thus even the newer
state contract does not by itself create a useful progress display.

The repair needs explicit public research summaries and source activity. Private
model reasoning must remain separate from that product contract.

## 3. Review destroys report structure

In the production worker, `services/zopedia_agent.py:2504` reads reviewed prose
with `_clean(payload.get("answer_markdown"))`. `_clean` uses
`" ".join(str(value or "").split())`, collapsing all whitespace. A later
Markdown-preserving function cannot restore the original boundaries.

Both successful October 14 answers have zero newline characters in the full blob
and product result. Their inline `###` headings, table separators and lists
therefore reach web and native already broken. A direct probe of the extracted
deployed function turned a six-newline heading/paragraph/table sample into zero
newlines. This establishes an upstream formatting defect independent of CSS.

The current local reviewer uses `_clean_model_output` and preserves Markdown.
The recorded development answers also retain paragraphs. That correction has not
reached this production executor. Citation presentation still needs attention:
the recorded two-sentence development follow-up exposes 24 collected citations,
although its prose links only three. The source list is not selected for the answer.

## 4. The research is shallow and recovery loses the question

### The first answer contained the insurance fact but did not develop the mechanism

It explicitly says CoreWeave is seeking insurance-industry capital. This is not
total failure to find that fact. It then spends much of its answer on price tables,
causal caveats, internal IDs and missing data. It classifies ASML as a different
kind of company without developing the indirect demand relationship the user
wanted explored. The reviewer corrected a draft classification of ASML as a
self-funded mega-cap, but did not deepen the economic explanation.

No original article body was opened. The agent read one generated Zopedia event
page and provider headlines/snippets. A hidden `live_event_impact_map` model call
timed out after 20 seconds; the tool then skipped remaining symbol-news searches.
The source URL in the page was available, but the six planner iterations ended
before a substantive source read or relationship investigation.

Monitoring took approximately 50.61 seconds and answer review another 50.06
seconds, within roughly 196 seconds from agent start to answer. One proposed
action was replaced by a trajectory restart, which consumed an iteration. The
request stored eight tools, while events show six planning steps: the deployed
agent replaces an explicit value equal to its module default with a tuning value.
Control activity and hidden model calls crowd out acquisition.

The numerical context is also incomplete. `daily_movers` returned a ten-row
result, but the agent's generic preview/context showed only the first eight rows.
CRWV and ORCL were outside that sample. The answer treated missing preview rows
as missing data. The supplied context also omitted the observation timestamp.
Complete numerical results and their dates belong in the research contract;
display previews cannot stand in for them.

### The substantive follow-up exhausted its research phase during preparation

The worker started at 16:51:07 UTC. The first preparation event appeared at
16:53:21, about 134 seconds later. Keyword prefetch followed, then the first
planner ran for approximately five seconds before timeout. No tool completed.
The turn finished at 16:53:37: 180.53 seconds after submission and approximately
150 seconds after execution started.

The deployed clock reserves half of a 300-second operation for synthesis/review
when the model-step limit is 90 seconds. Preparation consumes that same initial
150-second research window. With no collected sources or draft, the planner
timeout becomes a terminal empty answer despite the reserved completion time.
The error text describes a dropped connection, obscuring the observed phase
timeout. Blob duration reports only 4.95 seconds because its timer starts late.

The exact slow preparation dependency is not recorded. The source initializes
the query/data service and configuration before the first preparation event;
that is a concrete place to instrument, not proof of which dependency stalled.
The execution pod has been removed, so its console logs were unavailable.

### The failure explanation investigated the wrong system

Without parent context, the agent searched the scratchpad, generic retained market
context, an attention trace with an empty ID, job status and wiki maintenance.
It did not read the known parent research-run events. Its reviewer called the
draft relevant and coherent, corrected some overclaims, and preserved speculative
causes involving unrelated running jobs. The final response asked the user for a
question and time already present in application state.

The platform possesses the failure record. The model did not receive it or have
an appropriate owner-scoped research-run read capability. A broader search or
another prose review cannot recover a missing application context contract.

## Retained evidence and limits

Private database exports, complete blob payloads, event histories and digest-checked
source snapshots are under ignored `tmp/zopedia-audit-20261014/`. Database reads
used read-only transactions. No new research, memory mutation, build or deployment
was triggered. Market statements above describe the agent's output and source
handling; this audit does not independently validate their financial accuracy.
