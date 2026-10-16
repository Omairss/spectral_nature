# Hierarchical evidence experiments — October 15, 2026

[Master research index](../../../research/README.md) · [Research brief and indexed cases](../../../research/hierarchical-summarization/README.md) · [Durable final-batch evidence](../../../research/hierarchical-summarization/evidence/2026-10-15/README.md)

Status: final cheap batch complete — 14 matched pairs, four guidance trials and six adaptive-visibility trials; 38 personally reviewed final-batch answers. The candidate is not accepted for application defaults or larger workloads. Operational Admin/reliability release is separate.

## Experiment design

The existing shared AQL/Zopedia engine produced all summaries and answers. Every model route in the isolated experiment used DeepSeek Flash with thinking disabled and a 4,096-token output allowance. Review cycles were disabled equally in both arms; this is a comparison at one cheap operating point, not a comparison with the application’s configured full research flow. No additional premium routes, durable knowledge writes, web acquisition or application setting changes were made.

Six corpora cover a supplied manufacturer table/memo, DeepSeek/HTTPX transport documentation, PostgreSQL/Psycopg/Azure pooling documentation, a dated Microsoft earnings release, a dated FOMC statement and the full RAPTOR paper. The longest original is approximately 79,000 characters. Two questions per corpus cover broad interpretation and competing explanations; two additional questions probe detailed pool behavior and benchmark/ablation distinctions. These small trials do not establish behavior on million-token production inputs.

The comparison supplies either full retained originals or derived per-source summaries and a corpus overview where there are multiple sources. Originals and intermediate summaries remain addressable through read/search tools. Summaries are cached by complete content, level, model and frozen source identity, and reused across questions. This prototype does not implement RAPTOR clustering or prove an optimal hierarchy depth. It tests whether a small navigable summary representation is useful enough to justify a larger adaptive experiment.

The final matched comparison freezes 159 source files under source digest `3018e160f1e017c92690e2dd653173441d842753f32f637612760bbf85b385d9`. Planning, synthesis, optional analysis/monitor work, tool reads, all actual HTTP attempts and summary construction are counted. Started/final ledger events are deduplicated by attempt ID. Cached and reasoning tokens are breakdowns of usage, not extra tokens to add again. Complete answers are personally reviewed against originals; completion and numerical/string assertions are not quality acceptance.

## Protocol corrections

Early batches ran against concurrently changing engine code; their observations and cost are retained as exploratory. A snapshot script initially copied no source because of a duplicated path; the corrected snapshot checks before/after/copy hashes and the actual imported module path.

The first frozen comparison omitted the shared `research.read_context` affordance from its restricted catalog despite the planner instructions referring to it. It is excluded from the final matched comparison. The final catalog restores that exact shared tool contract.

The original summary-first packet could put catalog entries and appended navigation guidance beyond the planner’s 4,000-character preview. A separate adaptive variant puts the catalog first and passes optional navigation guidance in request context, where planning can read it. This changes the representation available at the decision point; it does not require original reads or a fixed sequence.

Some experiment attempts emitted database/archive accounting warnings. The independent local observer retained started and terminal provider events before attempting those persistence operations. Reported experimental cost uses those local receipts, preserving unknown usage for interrupted attempts. These warnings do not establish a database-capacity outage; a separate live Admin SQL probe completed in 5.4 seconds.

## Personal review findings

| Case | Observation and practical implication |
|---|---|
| Manufacturer change | Full-source answers derived operating profit falling from 12 to 6 and free cash flow rising from 2 to 6. Hierarchical and initial guidance answers retained the raw values but repeatedly omitted those central calculations and substituted gross-profit/net-income discussion. The candidate loses useful analysis even though the source values survive. |
| Manufacturer competing interpretations | Both approaches supplied useful pricing, mix and working-capital hypotheses. Full-source answers still over-ranked a one-time working-capital explanation; summaries preserved exploration but lost the operating-profit contrast. Hypothesis breadth is useful; unsupported certainty remains a shared engine issue. |
| Transport close | Both arms over-interpreted keep-alive lines as proof of pre-inference queueing and overstated the documented ten-minute close as an exhaustive server failure rule. Summary access does not repair this reasoning failure. |
| Total latency | Both correctly separated end-to-end duration from inference duration, then made overly strong claims about what first-byte/first-token timestamps isolate. Useful next investigations survived, but the complete explanation was not reliable enough to call solved. |
| Current-tier database reliability | A restricted-summary answer suggested built-in PgBouncer despite the supplied Burstable limitation. The corrected-catalog hierarchy answer retained the restriction and total-process pool sizing; a guided answer read the Azure original. This shows an improvement, with remaining inconsistent pool-lifecycle advice. |
| Four independent pools | Both arms recognized that four per-process pools can exhaust the stated non-reserved budget and leave no headroom. Hierarchy preserved the main useful capacity argument. Some recommendations drifted toward a paid tier despite the question’s current-tier objective. |
| Null versus zero-minimum pool | Original reads in the corrected setup preserved connection handoff, idle retention and per-pool scope. The practical distinction was useful. The max-size-zero/unbounded exception and idle expiry need care; do not generalize a positive-size bound to every setting. |
| Microsoft growth and currency | Both retained many reported figures. The repeated full-source answer incorrectly called 24% reported versus 22% constant-currency growth a currency drag; the hierarchy answer’s opening description of the gap was also inconsistent. These are material reasoning issues shared across representations. |
| Microsoft cash generation | Full-source answers overlooked the supplied cash-flow statement and claimed it was absent. Summaries surfaced annual cash from operations and capital additions, improving coverage. Hierarchical answers then overstated AI attribution and self-funding, and compared quarterly returns with annual cash generation too loosely. Compression can help salience while introducing different errors. |
| FOMC policy and future path | Both generally preserved the dated cut, dual-mandate risks and conditional future decisions. The hierarchy included unnecessary source-process caveats. These are broadly useful answers; small editorial weaknesses do not justify suppressing them. |
| RAPTOR overview | Both described the basic method and need for a financial-domain evaluation. Full-source output overlooked absolute build-time/cost information in an appendix; hierarchy repeatedly changed contiguous/adjacency grouping into a recency-based comparator. |
| RAPTOR layer choice | Full-source output read the original and preserved counterexamples where all-layer search underperformed. Summary answers smoothed this into a broad full-tree advantage. This is precisely the disagreement/exception loss that navigable summaries must avoid. |
| RAPTOR detailed comparisons | Basic hierarchy confused CoLISA overall accuracy with its hard-subset score, omitted the requested DPR value and overstated attribution of gains. Guidance triggered an original read and recovered the main exact comparison, but the final answer still carried the mistaken recency-based ablation and broad claims from its earlier view. Original access helps; it does not guarantee revision of an anchored answer. |

These observations are judgments of the complete outputs, not universal deterministic publication gates. Useful partial findings and indirect hypotheses remain valid research contributions. The reason to hold this candidate is the repeated loss or distortion of consequential relationships, not the existence of any recoverable subtask error.

## Decision and next experiment

Keep hierarchy as an optional navigational capability over retained originals. Do not summarize every small request, require one depth or force every answer through a summary/review pipeline. Reuse a built view when it adds information value; retain source identity, dates, disagreements and direct access to the complete text.

The next design question is how the shared planner and final analyst revise a summary-derived view when originals contain a missing calculation, restriction or counterexample. Improve that shared evidence/analysis handoff before paying for larger trees or more expensive models. A future comparison should include the configured normal research flow, larger unfamiliar corpora, update/contradiction cases and incremental summary reuse. More calls or premium reasoning are not established fixes by this batch.

## Retained evidence

Originals, complete answers, summary artifacts, harnesses, source manifest and attempt accounting are retained privately under `tmp/hierarchy-experiments-20261015/`. Runtime originals are also in `/tmp/sn-reliability-20261015/experiments/`. Final accounting and adaptive-variant findings follow. No hierarchy policy is deployed.

## Final accounting and adaptive-visibility result

All 38 final-batch answers completed, which establishes execution only. Across them, 127 actual Flash HTTP attempts used 707,758 reported input tokens and 115,865 output tokens; 281,600 input tokens were reported cached. Known estimated cost was **$0.13429**. The retained eleven summary-build calls cost a further **$0.00874** at their observed provider cache/rate conditions.

| Work | Answers | Actual HTTP attempts | Known estimated USD |
|---|---:|---:|---:|
| Full-source reference | 14 | 62 | 0.06308 |
| Basic navigable hierarchy, existing summaries | 14 | 34 | 0.03638 |
| Initial navigation-guidance ablation | 4 | 14 | 0.01642 |
| Catalog-first, planner-visible guidance | 6 | 17 | 0.01842 |
| Retained summary construction, charged once | — | 11 | 0.00874 |

These are measured experimental costs, not a production saving. The smaller answering bill cannot be accepted independently of the quality loss. Repeated original reads and planner repair loops are included. Summary construction was reused, and provider-prefix caches were already warm in places; a newly acquired corpus can have different economics. The short manufacturer source also expanded into a longer summary packet, showing why summarization should not be a mandatory stage.

The entire investigation, including exploratory, invalid-protocol and interrupted work, recorded **315 distinct attempts: 314 successful and one interrupted/unfinished**. Known reported usage is 1,835,375 input and 310,524 output tokens, including 588,672 cached input tokens. Known estimated cost is **$0.37509 plus one unpriced interrupted attempt**. No active experimental process remains. These figures cover this hierarchy investigation only, not another agent’s reliability or model-routing work.

Making catalog/navigation visible prompted two original-source reads and two context-continuation reads in the current-tier database question. That answer retained the Burstable restriction and per-process sizing, but still attributed timeout guidance to a supplied page where it was absent. The manufacturer answers again omitted operating-profit/free-cash-flow analysis. The Microsoft answer retained cash-flow data and useful hypotheses, with continued loose AI attribution. The detailed RAPTOR answer did not read the original and again omitted a requested baseline while overstating attribution and misdescribing the clustering comparator. The pool-behavior answer was broadly useful but still needed care around limits and exceptions.

**No candidate is promoted or scaled.** Keep the usable findings and original artifacts. The next iteration belongs at the shared evidence-to-analysis handoff, with optional summary navigation and meaningful revision against originals. Adding a mandatory read, fixed depth, source quota or deterministic quality gate is not supported by these results.
