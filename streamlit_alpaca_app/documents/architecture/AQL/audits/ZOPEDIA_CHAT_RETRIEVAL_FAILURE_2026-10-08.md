# Zopedia chat retrieval and research failure — October 8, 2026

Status: read-only production audit and proposed shared redesign. No runtime,
model, knowledge or deployment change made for this audit.

## Actual questions and results

| Production question | Run | Start UTC | Observed failure |
|---|---|---|---|
| What is the leading cause of today's downturn? | `agrun_dd4bcea4254c4ff19d101436` | October 8, 20:02:42 | Read one midday market article, discussed oil and yields, and declined to rank a leading cause without a source-linked causal comparison. |
| What are some causes of today’s downturn in AI stocks? | `agrun_f5c7d54a9ff449a884cf307e` | October 9, 02:32:22 / October 8 local | Reused the preceding answer and its retained article capture; ranked yields first and omitted the OpenAI revenue report despite its presence in retained evidence and the newly published Home. |

Both completed with a completed `revise` review. These were substantive failures,
not provider exceptions. Read both persisted final answers, their full blob tool
records, 123 durable progress events and 28 run-linked model-call events. All 28
calls succeeded and reported `deepseek-flash` for the requested reasoner alias.
Full records are retained privately in `/tmp/zopedia-two-prompt-audit-20261008`.
No new model run was needed to establish the recorded failure.

The first question is broader than AI and does not by itself establish the
OpenAI story as the leading cause of the entire market. Its failure is premature
narrowing and failure to deliver a useful comparative judgment. The second
question supplies the AI scope explicitly, so omission of that available report
is a direct recall failure.

## Findings at the shared source

### Retrieval matches the question's wording more readily than its subject

Both agent prefetch attempts and `research.search_evidence` explicitly disable
semantic retrieval. The retained SQL path uses `plainto_tsquery`, which joins
content words with AND, plus a whole-query substring fallback. The latest query
becomes `'caus' & 'today' & 'downturn' & 'ai' & 'stock'`. Current matches are
exclusively `agent_research` answers and evidence wrappers. The prior answer
repeats that language; the OpenAI report does not.

The latest run's actual two prefetch hits are the preceding generated answer
and the preceding opened-article capture. Its next searches narrow to a familiar
public-ticker list. It neither searches the wiki nor reads the new Home source
bundle. The untagged private-company report is lost again through a different
path from Home's repaired intake.

At audit time all 11,340 retained chunks have a null `embedding_model`. Enabling
the existing semantic flag alone cannot fix this. Its implementation also scans a
bounded recent set before embedding reranking; it is not corpus-wide nearest-
neighbor retrieval. The two OpenAI news chunks were retained by 23:07 UTC and
again at 01:25 UTC, before the newer question. The first question predates these
specific retained versions, although the original news reports were already
available in intake. Do not backdate index availability from publication dates.

Relevant source: `services/zopedia_agent.py:3444`,
`services/zopedia_research.py:814`, `services/saa/storage.py:1871`.

### Generated interpretation is easy to mistake for source observation

`_persist_agent_findings` writes completed reviewed answers and tool-history text
back as `agent_research` chunks. The newer run then receives the previous answer
as generic “Internal evidence.” It also receives the original article capture,
so the yield discussion is not wholly unsupported. The problem is anchoring and
lost distinctions: generated conclusions and source observations share one
flattened presentation, with weak typed dependency links. Prior answers are
useful research leads, but cannot independently corroborate themselves.

`research.live_event_evidence` combines an LLM impact classifier with retrieved
news. It describes the classifier's `expected_direction=up` as a live evidence
direction. The monitor treats that as an observed contradiction to falling
prices, spending research effort resolving a conflict manufactured by the
context contract. A model expectation must remain distinct from an observation.

Source: `services/zopedia_agent.py:4532`, `services/saa/storage.py:1079`,
`services/zopedia_research.py:328` and `:601`.

### Control calls crowd out acquisition and corrections can be abandoned

Each run makes six planner calls and six trajectory-monitor calls but executes
only four acquisition/data tools. Monitoring takes 58.08 seconds in the older
run and 44.81 seconds in the newer run. The latest first live tool obtains no
news rows; the second spends twenty seconds on a hidden impact-classifier
timeout and returns two weak web leads. No page is opened in that run; the
article body is inherited from the earlier capture.

Both sixth monitor calls request a correction, immediately followed by final
synthesis. A restart uses `continue` in a fixed `range(planner_steps)`, so the
last correction gets no next iteration. Non-acquisition monitor calls are already
excluded from the tool counter; that does not solve the separate iteration/time
problem. The newer run also approaches its research-phase deadline.

The durable request records eight tool calls, while model telemetry records an
effective six. The caller's explicit eight equals `DEFAULT_MAX_TOOL_CALLS` and is
silently replaced with the registered default of six. No saved prompt override
exists for the planner, monitor or answer judge, and no saved max-tools override
explains the mismatch. Optional/default resolution must occur once at entry;
passing an explicit number must preserve it.

Source: `services/zopedia_agent.py:3335`, `:3723`, `:4121`;
`services/agents/research_session.py:40`.

### Review improves the searched answer, without recovering the missing subject

The reviews correct discrepancies and add limitations inside the existing
source set. Neither can see the omitted report. Both perpetuate the idea that
ranking plausible causes needs a source-provided ranking or a “ranked causal
test.” A useful reasoned assessment can compare timing, exposure, specificity and
alternatives without claiming causal identification. An LLM verifier is not a
causal experiment. Source reading should deepen understanding, not certify
whether a useful hypothesis may be discussed.

The public answers also expose internal tool IDs, dataset names and long process
limitations. The newer answer has 6,240 characters and zero newline characters
in both the full blob and product result. `_run_answer_judge` passes Markdown
through `_clean`, which collapses whitespace, before `_clean_model_output` can
preserve it. Fix the shared prose contract instead of reconstructing Markdown in
one renderer. The latest response's separate citation list omits the Benzinga
article used in its prose because prefetched text does not carry the same typed
source-link collection as direct tool results.

Source: `services/zopedia_agent.py:463`, `:2240`, `:2485` and `:4401`;
`services/agents/research_runs.py:117`.

## Proposed general repair

Change the existing shared engine and retrieval contracts. Apply the design to
chat, Home, page summaries and background research. Preserve the current high-
recall objective, useful noisy leads and user control over browsing and writes.

1. **Build real retrieval across the corpus.** Index original documents,
   knowledge pages and derived research with explicit types and dependencies.
   Backfill embeddings and provide corpus-wide semantic candidate retrieval
   alongside lexical search. Let the LLM formulate varied searches from the
   user's goal, combine candidate sets and rank meaning. Titles, private entities,
   concepts and indirect relationships can all contribute. Exact ticker tags,
   one conjunction of question words and fixed score thresholds cannot define
   the relevance boundary. Dates and source diversity inform ranking; hard
   filtering follows explicit user scope. Index new sources at ingestion so
   discovery does not depend on a prior successful Home run.
2. **Give every run explicit context and evolving research state.** Carry the
   original request, prior turns, visible artifact references, as-of time,
   timezone, requested time period, source permissions and write policy. The LLM
   maintains what it is trying to answer, useful findings, competing explanations
   and the next most valuable information request. This is generic task state,
   not a market-intent classifier or mandatory semantic checklist.
3. **Use an adaptive acquisition loop under one real budget.** Resolve effective
   limits once and record them accurately. Separate acquisition actions from
   planning/repair iterations. Integrate reflection into planning and use an
   additional monitor when it adds value rather than before every tool. A chosen
   correction must have an executable next action and remaining budget; otherwise
   retain it as pending research without pretending it ran. Allow independent
   searches to run together. Replace hidden nested market classifiers in source
   fetch tools with explicit planner decisions and simple acquisition contracts.
   Preserve a useful draft and enough state to resume when the budget ends.
4. **Preserve epistemic type and original source access.** Keep original reporting,
   dated numerical observations, retained business knowledge, earlier generated
   answers and new hypotheses distinguishable. Resolve source dependencies when
   reusing a finding, without demanding a citation for every reasonable inference.
   Classifier expectations cannot masquerade as market observations. Keep source
   and retrieval dates separate, including date precision. A repeated generated
   answer is context, not a second source.
5. **Let synthesis answer the actual question.** Produce a direct, useful judgment
   and explain the strongest plausible connections, alternatives and meaningful
   uncertainty. Review can request acquisition when a missing angle matters and
   time permits, or improve the draft; it does not impose proof gates. Preserve
   the best valid answer on review failure. Carry citations through typed source
   references and preserve Markdown bytes through review, persistence and DTOs.
   Keep internal process failures in diagnostics; mention a limitation publicly
   only when it changes the answer's practical meaning.

Implement shared retrieval/provenance and effective-limit/Markdown corrections
first, then replace the fixed planner/monitor choreography with the shared
adaptive loop. Do not add another feature-specific agent or another verifier
layer. Raising timeouts or changing models alone leaves the observed failure
paths intact.

## Qualitative evaluation

Replay these two questions as diagnostic regressions, then personally evaluate
unseen requests and paraphrases across business explanations, current events,
definitions, historical comparisons, retained-only questions, numerical analysis
and follow-ups. Inspect which original sources were available, found, read and
used; whether the answer satisfies the request; and whether a misleading earlier
answer can be corrected when newer evidence arrives. Include an unfamiliar
private entity and a concept with sparse ticker tags. Check browsing/write scope
and recovery mechanically, but do not use fixed phrases, required tickers or
required conclusions as semantic acceptance tests.

“Any prompt” sets the scope of the shared mechanism. Unseen-question accuracy
must be established through actual output, beyond this known-example audit.
