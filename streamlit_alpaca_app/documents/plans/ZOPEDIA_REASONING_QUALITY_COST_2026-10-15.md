# Improve reasoning within the LLM cost allowance

[Current SOTA position and gap-closure sequence](OCTOBER_APPLIED_RESEARCH_FALL_2026_MASTER_PLAN.md): establish trustworthy measurement while improving reasoning/review; add feedback-driven improvement and allocation; compare retrieval/ranking; calibrate forecasts; then evaluate strategy, sizing and optionality under the same forecasts. This is the ordered proposal, not evidence these capabilities are already validated or permission for unspecified experiment spend.

**Owner clarification, October 16:** cheap open-source models are non-negotiable. Focus on the [complete prediction system around them](../research/frontier-agentic-research/PREDICTION_MACHINE_SCOPE_2026-10-16.md), retaining learning from failures, model allocation, reasoning/correction and evaluation as priorities. [Ling 3.0 Flash Fin and GLM-5.2 are marked candidates](../research/frontier-agentic-research/OPEN_MODEL_CANDIDATES_2026-10-16.md) for a separate frozen comparison. This supersedes any interpretation of earlier premium-model proposals as permission for proprietary escalation. The +20% overall-workload allowance and current workload knobs remain unchanged; no runtime route is changed by this clarification.

[Master research index](../research/README.md): [hierarchy research and failure cases](../research/hierarchical-summarization/README.md), [reasoning/review cases](../research/reasoning-and-review/README.md), and [why production reliability remained degraded](../research/system-reliability/PRODUCTION_FAILURES_2026-10-16.md).

**October 16 research handoff:** [Frontier research TODOs](../research/frontier-agentic-research/README.md) preserve the proposed decision dataset, agentic verification, learned computation allocation, independent evaluator and controlled experiments (points 2–6). The linked [ground-truth inventory](../research/frontier-agentic-research/GROUND_TRUTH_2026-10-16.md) covers the recorded reasoning/hierarchy failures, controls and temporal-label opportunities; the [literature survey](../research/frontier-agentic-research/LITERATURE_SURVEY_2026-10-16.md) distinguishes current benchmark listings from older paper results and defines the next research comparisons. This adds research work only; no new inference, backtest, runtime change or release was performed.

Status: **local implementation continues; reasoning quality and dev release remain unaccepted**.
Coverage, depth, search, output, review and scheduling values remain unchanged.
Admin now exposes workload controls from the shared worker registry. Producer
reuse/coalescing, resolved-search execution, optional context views, review
handoffs, presentation removal and selective Pro routing are implemented.
No images were built or deployed. Production and normal dev settings are unchanged.

This status applies to this reasoning/cost candidate. The separately accepted
[operational reliability and Admin/Health release](../verification/system-reliability-2026-10-16/README.md)
was deployed to dev on October 16 and excludes this candidate. Its separately authorized
[production promotion](../verification/system-reliability-production-2026-10-16/README.md)
is deployed, with operational limitations recorded; this reasoning candidate remains held.

The execution checks and Admin preview pass, but the reasoning repair is not
accepted: Pro and Flash review missed both a source-condition error and a
quantitative error in an unfamiliar delivery-mix case. The overall +20% cost
requirement also remains unverified. Do not treat the earlier +15.2% price
scenario or illustrative savings as measured rollout results. Read the
[implementation review and complete outputs](../verification/llm-flow-2026-10-15/quality-cost/implementation/README.md)
before continuing or deploying this candidate.

Implementation sequence:

1. Unify workload controls and model allocation in the shared flow; keep all
   workload limits unchanged and preserve effective worker values in Admin.
2. Make artifact identity depend on producer inputs/settings, coordinate identical
   work, and preserve existing operational continuation across platform retries.
3. Execute resolved searches directly, keep navigable originals and stable context,
   pass task premises and reviewer corrections once, and generate final prose once.
4. Run paired baseline/candidate evals on frozen representative inputs through the
   shared engine, including unfamiliar held-out cases. Assess complete outputs,
   attempts and workload-weighted cost; use a small live integration check after
   replay acceptance and before the dev rollout.

The scoped pre-change sources are preserved outside the watched workspace at
`/tmp/sn-reasoning-cost-20261015/baseline`. Existing unrelated workspace changes
must remain intact. Later proposal-only statements describe their original audit
phase, not the current authorization.

**Actual daily spend, checked after the owner's correction:** DeepSeek's usage
page shows **$37.21 today for the Spectral Nature key**, in local PDT/MST.
Read-only SQL plus existing archive receipts attribute **$35.64**, principally
**$27.03 news/company research** and **$5.92 Home builds**; $1.57 remains unassigned.
Production records still report Flash. The prior $3–$4 assumed workload mix and
+6–13% small-query scenarios are not daily or overall estimates. The
[daily spend audit](../architecture/AQL/audits/ZOPEDIA_DAILY_SPEND_2026-10-15.md)
records four scheduled news executions, a $4.19 repeated application attempt and
four Home builds. Resolve repeated paid work and actual daily role volumes before
expanding premium routes or claiming that the complete rollout fits +20%.

The owner accepts up to 20% higher overall LLM cost and rejects a 50% overall increase. Compare
against the current local Pro/Flash flow with one reviewer and one cycle, using
the same questions and research limits. Count the complete workflow, including
helpers, corrections, schema repairs and failed provider work. Development
experiments are separate from ongoing application spend.

The owner clarified that this is a **combined-workload allowance**, not a ceiling
for each run or category. Difficult runs may cost more than 20% extra when the
representative workload stays within 1.20 times its baseline cost. Report each
category so the tradeoff is visible, but judge cost acceptance on the combined
total. This is an evaluation constraint, not a proposal to interrupt inference
with a new runtime spending controller.

Use the same representative task mix and frequency weights for both versions:
`sum(frequency × candidate cost) / sum(frequency × baseline cost) <= 1.20`.
Weight by actual workload frequency and resulting spend, not an unweighted mean
of percentage changes or a deliberately easier task mix. The workload frequencies
and matched candidate costs have not yet been established by this investigation.
Full Home generation must be represented explicitly. The small-query arithmetic
below excludes it and cannot establish an overall application cost increase.
Separate two comparisons: the handoff change against the local Pro/Flash flow,
and the complete rollout against recorded runtime model usage. The latter also
includes model upgrades. The actual Trading Agent samples below reported Flash;
their cost must not be treated as already including premium analysis.

This follows the
[flow acceptance review](../architecture/AQL/audits/ZOPEDIA_FLOW_ACCEPTANCE_2026-10-15.md)
and the [conversation repair plan](ZOPEDIA_CONVERSATION_REPAIR_2026-10-14.md).

## Recommendation

First address the dominant actual cost: repeated full news/company work and
continuation across platform retries. Retain the reasoning handoff work below,
but do not treat its small-query overhead as the overall rollout budget. Current
daily spend and the proposed premium model allocation need one combined model.

The barbell proposal below narrows the earlier blanket Pro researcher/synthesis
allocation: Flash handles broad investigation and routine processing; Pro owns
selected consequential analysis and substantive corrections. Keep one default
reviewer and one cycle. Improve the information exchanged by those existing calls
before adding another model stage or increasing reasoning effort.

The next implementation candidate should do three things together:

1. Preserve task premises. User-provided observations, hypothetical assumptions,
   external reports and generated interpretations need distinct origins. A
   reviewer must not demand an external citation for an explicit task premise.
2. Review concrete reasoning. Pass important conclusions with their actual
   supporting basis, and request specific material corrections with a coherent
   counterexample where relevant. Preserve useful hypotheses and recommendations;
   lack of a verbatim source statement does not make an inference wrong.
3. Stop repeating feedback. Send each correction once, with originals available.
   Do not manufacture a replacement answer from feedback and also pass its list
   form back to synthesis. Pro decides substantive changes; presentation handles
   writing. Cosmetic edits should not trigger premium analysis.

These are shared handoff changes, not another review agent, a source-proof gate
or question-specific rules. Their quality and cost benefit still need evidence.

## Barbell model allocation — proposal, October 15

Use cheap breadth and expensive depth. The production bill is already mostly
Flash, so this is a selective quality investment rather than an automatic saving
from changing model names. It replaces existing analytical calls; it does not add
a premium pass after every cheap draft.

`Flash investigation → Pro analysis → Flash review → Pro material correction when needed → render completed answer`

Pro can direct further investigation and return to research when a decision needs
it. Its role is analytical, not just polishing the final prose. Flash review
suggests corrections; Pro resolves changes to the reasoning. Retain the existing
one-cycle default and configurable reviewer count.

| Responsibility | Proposed allocation |
| --- | --- |
| Search expansion, source reading, extraction, first-pass summaries, routine tool choices | Flash; widen useful investigation here |
| Monitor and ordinary relevance ranking | Flash; research freedom remains independently configurable |
| Home analytical synthesis and event interpretation | Pro |
| Trading Agent research synthesis and final candidate thesis | Pro |
| Ad hoc/research final analytical answer | Pro; routine factual lookups can stay Flash |
| Repeated news/company dossier preparation and intermediate summaries | Flash; reuse the same research across consumers |
| Consequential news interpretation, conflicting evidence, a difficult research direction or material correction | Selective Pro reasoning, including further tool use when useful |
| Review, formatting, mechanical schema repair and ordinary memory housekeeping | Flash; new substantive knowledge judgments follow the analytical allocation |

These are shared executor responsibilities, not surface-specific alternate agents.
The current single `researcher` route combines routine tool decisions and deep
analysis; implementing the barbell requires making that distinction explicit in
the shared flow and Admin editor. No extra classifier or reviewer is proposed.
The researcher and synthesis calls must retain navigable original evidence;
summaries are optional views, not the only material Pro is allowed to inspect.
Premium relevance ranking remains an adjustable exception, not the default.

### Repricing the actual recorded day

The reference is the observed **$37.21** provider total, not the earlier $3–$4
illustration. Its +20% reference is **$44.65**, or **$7.44** additional spend.
This single day is a sensitivity baseline, not an established recurring daily
budget. The existing experiment spend, unassigned $1.57 and unknown-usage attempts
are held unchanged; no retry or research-reuse saving is credited.

Replace the following known production Flash calls using their recorded input
and output volumes, actual UTC price bands and **zero Pro cache hits**:

| Premium allocation | Known priced attempts | Existing Flash cost | Pro cost | Added cost |
| --- | ---: | ---: | ---: | ---: |
| Home synthesis and event interpretation | 157 | $1.16 | $4.59 | $3.43 |
| Trading research synthesis and candidate thesis | 80 | $0.27 | $1.01 | $0.74 |
| Queued ad hoc/research final synthesis | 6 | $0.014 | $0.053 | $0.039 |
| Core subtotal | 243 | $1.45 | $5.65 | **$4.21** |

This upgrades 6.11 million recorded input/output tokens, about 2.3% of the known
daily generation volume. These are inference attempts, not counts of reports or
independent investigations. Unknown-usage attempts have no invented price.
The queued-query sample covers final synthesis only; extra premium planning or
research would consume the remaining allowance.

There were also 207 priced news `agent.final_synthesis` attempts, currently
costing $4.94. Upgrading all of them alone adds **$16.12**. A selective allocation
equivalent to 10% of that group's recorded input/output mix adds **$1.61**.
That 10% is a modeling assumption weighted by usage, not a call quota or a claim
that 10% of news cases have been identified as important. Larger difficult cases
can consume more than their share of calls.

| Reference-day scenario | Projected total | Increase |
| --- | ---: | ---: |
| Core Pro allocation; remaining work Flash | $41.42 | +11.3% |
| Core plus selective news equivalent to 5% of news synthesis usage | $42.22 | +13.5% |
| Core plus selective news equivalent to 10% of news synthesis usage | $43.03 | +15.6% |
| Same 10% allocation, with 25% more Pro output including reasoning | $43.68 | +17.4% |
| Same 10% allocation, with 50% more Pro output including reasoning | $44.34 | +19.2% |
| Blanket Pro for all recorded synthesis groups above, including all news, macro and learning | $57.57 | +54.7% |

Arithmetic for the 10% same-volume scenario:
`$37.21 + ($5.6509 − $1.4451) + 0.10 × ($21.0692 − $4.9449) = $43.03`.

The output sensitivity changes completion tokens, which already include reasoning;
it does not also count reasoning tokens separately. Additional investigation,
larger input context or more correction calls are separate costs. At 25% larger
Pro output, the 10% news scenario leaves about **$0.97** before the reference +20%
limit; the 5% version leaves about **$1.80**. Start planning around the latter
allocation rather than spending all the headroom on routine promotion. Let the
shared researcher identify valuable analytical work semantically; do not route
from keyword rules or a made-up confidence threshold. This is a workload design
target, not a new per-run spending stop.

First restore continuation and useful incremental research reuse so completed
work is not bought again. Then assess the barbell on representative days, keeping
model-routing changes separate from those savings and inspecting answer quality.
Model placement alone does not resolve the observed reasoning failures.

The [offline calculation receipt](../verification/llm-flow-2026-10-15/quality-cost/barbell-offline-proposal.json)
retains exact aggregates, selections and sensitivities. Rates were checked against
[DeepSeek's official pricing](https://api-docs.deepseek.com/quick_start/pricing/):
off-peak uncached input/output per million tokens is Flash $0.15/$0.60 and Pro
$0.66/$1.98, with both doubled in the documented weekday peak bands. No inference,
settings change or deployment was made for this proposal.

## Upgrade, downgrade and offload review — proposal, October 15

Prioritize repeated paid work and context assembly alongside the selective Pro
allocation. Of **$35.27 known production spend**, **$27.70 (78.5%) is input** and
$7.56 is output, including $3.75 of reasoning. Cheaper output alone cannot address
the main cost. These production-only role totals exclude isolated experiments;
they are not a second sum to add to the provider's $37.21.

### Model changes

| Responsibility | Proposed treatment |
| --- | --- |
| Final analytical synthesis, competing explanations and Trading Agent theses | Upgrade selectively to Pro; replace existing calls |
| Researcher | Flash for routine acquisition; Pro for difficult analysis and deciding consequential research direction, with tool access |
| Relevance | Indexed lexical/vector retrieval and rank fusion first; Flash for contextual judgments; selective Pro when choosing evidence itself needs deeper analysis |
| Query expansion, first-pass summary and routine extraction | Flash without thinking where the task does not need reasoning; skip generation when an upstream result already supplies the required work |
| Review | Keep Flash/low as the starting proposal; improve concise corrections and evidence access. The earlier non-thinking reviewer trials did not establish equivalent quality |
| Monitor | Flash for semantic research guidance. Code owns timeout, cancellation, tool/schema validity and exact repeated actions. Ordinary guidance can be evaluated without thinking; do not remove semantic guidance through a fixed relevance rule |
| Knowledge maintenance | Code persists approved typed facts, links and provenance; Flash organizes routine updates; Pro resolves new substantive conclusions or conflicting knowledge |
| Presentation | Let the analytical producer write the requested format. Render typed fields with code; reserve Flash rewriting for an actual language/style transformation |
| JSON repair | Parse and validate with code; use Flash without thinking only for conversion that cannot be recovered mechanically. Never invent missing analytical content |

Flash is already the cheaper model in DeepSeek's current published two-model
offering. Turning thinking off reduces generated reasoning; it does not introduce
a cheaper per-token rate. Changing provider or introducing a local model is a
separate quality/operations decision, not necessary for these first improvements.

### Repeated work and handoffs to fix

1. **Resume completed work across attempts.** The daily audit identifies $4.19
   of paid work in the repeated news application attempt. Shared artifact replay
   and incomplete-draft recovery already exist; inspect which stages missed them
   before claiming savings. Preserve stable platform execution identity across
   application retries and resume the missing work. A later scheduled run must
   still reassess changed inputs and external developments.
2. **Make cache invalidation follow the producer.** In local
   `services/llm_execution.py:research_artifact_key`, the key includes the complete
   `capture_research_configuration()["revision"]`. Changing a downstream reviewer
   or monitor setting can therefore invalidate an unchanged supplied-evidence
   dossier. Keep the full configuration snapshot for provenance, but identify
   reuse by the relevant producer's actual inputs, prompt/schema/code version,
   model/effort and dependencies. A changed review can review an existing analysis
   without buying its acquisition or unrelated preparation again. Retain access
   and environment boundaries. Concurrent requests for the same unfinished input
   should share one producer; the current wrapper reads then executes without
   an in-flight claim, although actual duplicate spend from that race is unproven.
3. **Plan a resolved search once.** `search_evidence` currently invokes
   `retrieval.plan` for any nonempty query, including requests with supplied
   queries. Represent resolved queries, dates and retrieval intent explicitly at
   the shared tool boundary; execute those directly. Keep the cheap planner for
   natural-language objectives that still need interpretation or expansion.
   Supplied query hints alone do not prove all interpretation is finished.
4. **Stop automatic rewriting of a finished answer.** The local
   `_final_user_prompt` already asks synthesis for the finished answer in the
   user's requested format; the prose completion path then calls
   `agent.answer_presentation`. Generate final presentation once, retain reviewed
   content, and use a separate rewrite only for a requested transformation.
   Similarly, a completed analytical draft need not be synthesized again merely
   to restate it. Preserve the intended researcher/synthesis/review
   responsibilities rather than collapsing all roles.
5. **Reduce repeated evidence transport.** Local `_tool_history_prompt` always
   resolves the original packet; its `full_evidence` flag does not select a smaller
   planning representation. Routine next-action decisions should be able to use
   an index or existing findings and open originals as useful, rather than
   automatically rereading every passage. Keep all sources accessible. Continue
   stable, append-only evidence and exact-content references across prompts;
   semantic relevance and research scope remain model decisions.
6. **Share completed company research across consumers and scheduled updates.**
   The dossier and final business story have different purposes: organized
   findings versus interpretive narrative. Their separate calls are not proven
   duplicates. Keep one reusable version of each useful result, update changed
   findings, and give Home/Trading those results instead of restarting the same
   company investigation. A different investment horizon can still warrant new
   analysis. Let the model decide what changed evidence merits further work.
7. **Send corrections once.** The existing plan identifies reviewer
   `answer_points` also joined into `answer_markdown`; both representations can
   reach synthesis. Pass one correction object plus the unchanged draft and
   original evidence, not a second answer assembled from reviewer objections.

The earlier per-question dossier reinterpretation, repeated generated source
inventory and extra feature-owned page review were already addressed in prior
local work. Do not count them as new savings. In the recorded production day,
page summary formatting/review still cost about $0.35 together; the dominant
opportunities are elsewhere. Current source behavior and deployed historical
usage must remain separate.

### What code and smaller retrieval models can own

Use normal execution/data processing for fetching, source-version hashes, exact
deduplication, concurrency ownership, date arithmetic, joins, numerical metrics,
schema validation, citation-link resolution and typed rendering. Structured feeds,
tables and document metadata can be parsed directly; interpretation of a passage,
uncertain entity relationships and causal/business relevance remain LLM work.
HTML extraction already uses trafilatura; retain Flash block selection for pages
where a semantic cleanup is useful, and reuse extraction by source version.

Retained retrieval already combines PostgreSQL full-text search with corpus
embeddings and reciprocal rank fusion. Equal-content corpus embeddings are already
shared. Keep that non-generative retrieval path rather than using a chat model
to scan the corpus. Embedding inference still has its own metered cost. A compact
trained reranker is a possible later replacement for bulk chat relevance scoring,
subject to personally inspecting broad and indirect discovery results; it is not
needed to implement the reuse and handoff changes above.

### Relative savings, without another model run

| Opportunity | Recorded cost or offline arithmetic | Interpretation |
| --- | ---: | --- |
| Answer review | $7.39, including $6.02 input and $0.79 reasoning | Focus on context/reuse as well as effort; $0.79 is an upper bound from removing existing reasoning alone, not an equivalent-quality saving |
| Retained retrieval planning | $4.13, including $0.20 reasoning | Bypassing already-resolved planning matters more than turning its thinking off; bypassing half its spend would save $2.07, with eligibility unmeasured |
| News/company work | $27.03 | Reusing 10–20% of this spend would save $2.70–$5.41; unchanged-equivalent work has not yet been quantified |
| Stable Flash input prefixes | 10 million additional off-peak cache hits save $1.47 | Same text remains available; actual cache hits are provider-reported and not guaranteed |
| JSON/schema repairs | About $0.025 in the known production repair-purpose buckets | Sensible execution cleanup, small direct cost opportunity here |

These are overlapping opportunities, not additive promised savings. Exact reuse
avoids a call, provider prefix caching discounts repeated input but still generates
output, and semantic incremental research decides what new investigation is useful.
Address execution continuation and cache ownership first, then resolved-search
execution and unnecessary rewriting, then finer effort/reranker changes. Spend
the recovered capacity on the premium reasoning allocation and useful research
breadth. Read complete outputs before accepting any quality-sensitive downgrade.

The [offline offload receipt](../verification/llm-flow-2026-10-15/quality-cost/offload-offline-review.json)
retains production-only purpose totals and arithmetic. Current
[pricing](https://api-docs.deepseek.com/quick_start/pricing/),
[thinking controls](https://api-docs.deepseek.com/guides/thinking_mode/) and
[prefix-cache rules](https://api-docs.deepseek.com/guides/kv_cache/) were checked
against official documentation. No application code, settings or deployment
changed, and no new inference was performed for this review.

### Combined savings scenarios

A useful planning target is **about $6 saved on a day with the recorded $37.21
workload**, with lower/upper scenarios of approximately **$4–$8 (10–22%)**.
These are conditional sensitivity calculations, not measured or established
achievable savings rates.

Use three mutually exclusive cost pools to avoid adding the overlapping
opportunities above:

- News/company work excluding direct retained-search planning: **$24.1445**.
  Reuse/recovery reduces 10%, 15% or 20% of this spend. Platform retry recovery is
  inside this amount, not another $4.19 added to the saving.
- Direct retained-search planning across production: **$4.1343**. Bypass 25%, 40%
  or 50% of its spend where the request is already resolved.
- Remaining production input, excluding news, search planning and the core Pro
  synthesis allocation: **22.50 million currently uncached tokens**. Converting
  all of that input to cache hits would discount $3.7617 at the recorded time
  bands; assume only 10%, 20% or 30% obtains new hits. Preserve actual existing
  hits. This adds $0.38, $0.75 or $1.13 of savings respectively.

The actual eligible reuse, bypass and cache-hit fractions are not yet measured.
The middle scenario is `$3.62 news reuse + $1.65 planning bypass + $0.75 additional
cache hits = $6.03 saved`. Do not interpret fractions of spend as call fractions.

| Scenario | Saved versus reference | Optimized reference day | With proposed Pro allowance |
| --- | ---: | ---: | ---: |
| Lower | $3.82 / 10.3% | $33.39 | $39.03 |
| Middle | $6.03 / 16.2% | $31.18 | $36.82 |
| Upper | $8.02 / 21.6% | $29.19 | $34.83 |

The last column reserves the full original **$5.64** barbell increment: core Pro
synthesis plus selective news equivalent to 5% of news synthesis usage, zero Pro
cache hits and 25% larger Pro output including reasoning. It credits no savings
on that premium increment. Net change versus $37.21 is +4.9%, −1.0% and −6.4%
respectively. This is a funding scenario, not a matched candidate performance run.

No extra saving is assigned to presentation removal, reviewer thinking changes
or future avoidance of broad cache invalidation. Keep experiment spend, unknown
usage and the provider-attribution gap unchanged. This observed partial-day
workload also does not establish a recurring daily or monthly forecast.
The [combined calculation receipt](../verification/llm-flow-2026-10-15/quality-cost/combined-savings-scenarios.json)
retains the exact disjoint pools and assumptions. No new inference was performed.

## Breadth, depth and refresh controls — proposal

Candidate volume and research depth should be independent controls. Distinguish
subjects considered, subjects actually investigated, subjects receiving deeper
follow-up and results displayed. Lowering a display limit need not reduce the
research performed upstream. A research allowance is a maximum, not a target
the agent must exhaust.

| Control | Cost mechanism | Tradeoff |
| --- | --- | --- |
| Subjects investigated | More separate company/event investigations | Coverage and discovery breadth |
| Subjects receiving deep follow-up | Concentrates additional research and premium reasoning | Breadth versus depth; let the LLM choose useful follow-up |
| Research actions and follow-up rounds per subject | More acquisition and planner calls, with growing context | Ability to resolve difficult questions |
| Search queries and results per lookup | Search-provider work plus relevance and downstream context | Diverse leads and competing explanations |
| Pages opened and evidence read per decision | Acquisition, summaries and repeated input tokens | Source detail; retain originals and continuation rather than truncate captured evidence |
| Refresh cadence | Repeats the workload more or less often | Freshness; cheap monitoring can continue between deeper research updates |
| Premium allocation and reasoning effort | Model prices and generated reasoning | Analytical depth; preserve Pro for consequential reasoning |
| Reviewers, cycles and monitor cadence | Additional checks and any resulting research/corrections | Independent scrutiny and research guidance |
| Research freedom | More or fewer adjacent leads | Exploration; separate this semantic guidance from the numeric research allowance |

### Existing source controls

These are **local code defaults**, not verified live settings. Several producer
controls are environment variables or function arguments, rather than settings
already wired through the Admin flow editor:

- Trading: shortlist **6 per horizon**, shared pool **8 unique companies**, final
  output **up to 4 per horizon**, **4 research tool calls per company**. The shared
  path researches each company once before horizon-specific synthesis; reducing
  horizons does not remove that shared work proportionally.
- News/company production: **25 company stacks**, **12 news resolutions**,
  **24 research queries per company**, **4 search results per query**; the business
  research reader defaults to **2 page opens per query**.
- Home: research candidate allowance **40**, with a separate Zopedia enrichment
  allowance **12**. They are different stages, not counts to add as unique subjects.
- Shared retained lookup: **5 queries**, **40 candidates per query**; the shared
  engine's default research budget is **10 tool calls**, with caller overrides.
- Source reading: **5,000 characters per read** versus **1,000,000 characters
  retained per capture**. A smaller read can require more continuation calls;
  it is not equivalent to deleting source content.
- Review: **one reviewer, one cycle**; monitor cadence defaults to **every two
  research actions**, with independently configurable research freedom.

Source owners are `pipeline/jobs/main.py`,
`pipeline/jobs/attention_home_build.py`, `services/trading_agent.py`,
`services/aql/business_model_stack.py`, `services/aql_zopedia_engine.py`,
`services/zopedia_research.py` and `services/llm_flow.py`.

### Recommended tuning approach

Keep a broad inexpensive first pass, then let the shared researcher allocate
deeper work to promising, conflicting or unresolved questions. For an illustrative
20-subject workload, two initial actions each plus six additional actions for
five selected subjects allows **70 research actions**, compared with **160** if
all 20 receive eight. Deepening ten instead allows **100**. These are action
allowances, not LLM call counts or predicted 56%/38% bill reductions. Helpers,
synthesis, review, actual use, context size and cache hits change the cost.

Tune News/Home first: recorded production spent about **$27.03/$5.92** there,
versus **$0.58** for Trading. Reducing Trading's candidate count alone has a small
overall-dollar effect. Preserve broad discovery while testing how much marginal
deep research adds useful findings. Prefer incremental updates over repeated
complete builds when inputs have not materially changed.

Do not use timeout or worker-count reductions as proxies for this policy.
Concurrency mainly changes elapsed time; a shorter timeout can abandon paid work
and trigger recovery. More tool allowance also does not imply proportional cost:
later calls may reread a larger accumulated evidence context.

In Admin, organize these controls at **Coverage → Research → Analysis → Review**,
with refresh cadence alongside the workload. Show actual subjects/actions/tokens
and spend used next to each allowance, including source of the effective value
when a producer overrides the shared default. Apply workload settings through
the shared executor, preserve each run's configuration, and keep source access
and reviewer count separate from display limits. Numerical limits are user-owned
capacity choices; semantic selection remains with the LLM.

This is a proposal only. No limits, schedules, models or deployments changed.

## What the live comparisons establish

All comparisons used the shared engine with isolated settings. Complete outputs
were personally read against the two original source pages and unchanged user
question. This investigation used **one technical source case**; the financial
and event fixtures in the experiment scripts were not run. It does not establish
general performance or a workload-wide saving.

The original answer wrongly treated a documented sufficient condition as the
only explanation for a connection closing, and treated late JSON arrival as
proof of slow generation rather than allowing queue delay followed by fast
generation. More expensive review repeatedly missed those errors.

| Experiment | Measured cost, USD | Finding |
| --- | ---: | --- |
| Existing Flash/low review of the same saved draft, then presentation | 0.00205 | Accepted the flawed analysis |
| Generic counterexample instructions, Flash/low review, Pro correction and presentation | 0.01980 | Still wrong; introduced poor deadline advice |
| Same focused review with Flash/high | 0.02636 | More effort and premium correction still failed |
| Same focused review with Pro/low | 0.00782 | Accepted the flawed draft |
| Initial Pro analysis and presentation, review disabled for isolation | 0.01390 | Still made an unsupported deadline recommendation |
| Same initial analysis with a self-check instruction | 0.01275 | Still wrong |
| Structured Pro observations, interpretations and alternatives | 0.01743, Pro alone | Still wrong; about +30% versus the matched premium stage |
| Flash source-reading brief, then Pro analysis and presentation | 0.01454 | About +5% for this initial segment; Pro overrode correct source limitations |
| Ordinary full research flow, five calls | 0.03395 | Research, synthesis, review, correction and presentation completed, but answer still wrong |
| Independent Flash checks before final Pro synthesis | 0.02558, partial | Inherited a bad question frame; final analysis still wrong. Presentation was not run |
| Pro resolves a prior research analysis and independent Flash analytical draft | 0.01131, resolution only | Pro restored the unsupported conclusion despite the correct alternative |
| Flash/low checks explicit assertion/basis relationships | 0.00698, review only | Caught both original errors; verbose and made unnecessary or incorrect objections |
| Compact Flash/none material-correction review | 0.00077, review only | Caught the reversed implication, missed the late-JSON error |
| Compact Flash/low material-correction review | 0.00284, review only | Returned no corrections; missed both |
| Flash prepares assertion/basis pairs, Flash checks, Pro/low resolves, Flash presents | 0.01083, post-analysis replay | Fixed the two original errors but questioned an explicit user premise; not accepted |
| Flash/high initial analysis and presentation, diagnostic reference | 0.00295 | Handled the central ambiguity better in this draw; not evidence for changing the requested Pro analysis default |

These are differently scoped segments, **not comparable full-job totals** unless
explicitly marked. The last prepared-review replay made five HTTP attempts,
including a real Flash schema repair. Its correction still used the premium Pro
model, with lower thinking effort for that focused operation. No timeout or
effort override from an experiment became an application default.

The independent-check prototype also hit a presentation-constant typo after
three paid calls. Those outputs and costs were retained, not purchased again.
Its Pro answer already failed personal review; the partial run is neither a
complete-workflow saving nor a successful quality result. Credential-setup
failures made no model requests and are separate from answer-quality outcomes.

## Why the promising prototype still cannot become the default

Preparing assertion/basis pairs let a non-thinking Flash review catch both
original reasoning errors. Pro/low then corrected those errors. This is useful
evidence that the review representation matters more than buying a larger
reviewer for this example.

However, preparation paired the client's explicitly stated 75-second deadline
with external timeout documentation instead of its user-message origin. The
checker claimed the user had not supplied that fact. Pro accepted the objection
and changed a known premise into uncertainty about which component owned the
deadline. It also retained an overstatement about what first-byte telemetry can
separate for a non-streaming response. Fixing two sentences is not sufficient
acceptance for the whole answer.

The cost boundary differs by entry path:

| Comparison | Baseline | Candidate | Change | Status |
| --- | ---: | ---: | ---: | --- |
| Ordinary research: keep measured researcher/first synthesis; substitute measured prepared-review tail | 0.03395 | 0.03282 | −3.4% | Projection across different drafts; not a paired candidate run |
| Saved direct-analysis replay: same retained initial Pro analysis plus each measured review/presentation tail | 0.01922 | 0.02800 | +45.7% | Individual increase allowed in principle; workload impact unmeasured and answer quality rejected |

The direct replay shares a retained initial analysis costing 0.01716924, generated
in an earlier 180-second-step experiment. Its ordinary review accepted the draft
without a premium correction. The new tail adds a premium correction, making the
percentage increase large even with Pro/low. This is a reconstruction from real
paid phases, not a fresh matched whole-run comparison under default timeouts.
It does not establish a workload-wide cost breach or predict the production
bill. Under the owner's clarification, +45.7% on a difficult run is acceptable
in principle if weighted overall spend stays within +20%. This prototype remains
unaccepted because of its answer-quality failure and missing workload comparison.

Do not add the prepared-review sequence to every direct query. Do not remove
needed analytical corrections merely to meet a cost number. The first candidate
must improve the existing calls; a different ordering remains a separate
experiment if that fails.

## Recorded full Home workload

The retained October 14 UTC development Home run
`fe3ca0f5-9e84-4802-9fbe-2f10f2c1ec18` completed twelve ticker investigations,
four source-event investigations, eight stories and five optional page summaries.
Its [saved accounting](../architecture/overall/audits/evidence/LLM_COST_RESTRUCTURE_2026-10-13.json)
records:

| Generation usage | Recorded tokens |
| --- | ---: |
| Input | 15,996,387 |
| Output, including reasoning | 772,106 |
| Total | **16,768,493** |
| Cached input, already included in input above | 6,747,026 |
| Reasoning, already included in output above | 481,866 |

There were 526 chat/schema-repair attempts, 294 embedding attempts and one audio
attempt: 821 provider attempts in total. Including the separately recorded
embedding input gives 16,808,293 input-plus-output tokens. Eight attempt records
lack usable usage, so these are captured totals, not an exhaustive invoice.

Captured generation cost at common off-peak prices was **$1.8709 with the recorded
cache hits**, or **$2.8627 for the same usage with no cache**. An illustrative
+20% on this Home job would add $0.3742 or $0.5725 respectively, producing $2.2451
or $3.4353. The actual allowance still applies to combined workload spend, not a
separate cap on Home. Unknown-price embeddings/audio remain outside those dollars.

This is a historical full-run reference before the unreleased role-routing
changes, not a forecast for today's proposed Pro/Flash configuration. The saved
aggregate lacks per-model/per-purpose token totals; it is insufficient to reprice
the analysis-versus-peripheral split. The common-price cold total numerically
equals Flash-only repricing, which is a reason to check routing rather than
assume the old bill already includes premium analysis. Read the retained attempt
ledger by role/model before claiming a +20% overall result.

The earlier captured complete Home run used about 6.73 million generation tokens;
the later one used 16.77 million. Different selection and richer context prevent
treating either as a fixed number of tokens per homepage. Source/context volume
and repeated planning matter as well as the number of final stories.

## Trading Agent, news and ad hoc workloads

The retained records distinguish the actual Trading Agent candidate builder from
the news/financial-dossier workload. Both were executed in an Azure worker named
`trading-agent-build-dev` during earlier experiments; that infrastructure name
does not identify the workload. Current pipeline dispatch also keeps
`trading-agent-build` and `news-ingest-and-features` separate.

| Recorded example | Generation tokens, input + output | Common off-peak USD with recorded cache | Same usage, cold-cache USD |
| --- | ---: | ---: | ---: |
| Trading Agent candidate batch, October 12 production window | 876,698 | 0.2431 | 0.2659 |
| Full Home build, October 14 development | 16,768,493 | 1.8709 | 2.8627 |
| News/company build plus its retry and recovery | 33,019,985 | 5.1291 | 6.0185 |
| Ad hoc numerical query, local two-reviewer correction example | 23,856 | 0.02075 | 0.02077 |
| Tool-using company research probe | 1,532,902 | 0.1756 | 0.2370 |
| Typed background page summary, local example | 13,218 | 0.01031 | 0.01043 |

These are examples with their recorded model mixtures, not averages or forecasts
under the proposed defaults. Reasoning is included in output. The numerical
query used six model attempts, including two reviewers and a Pro correction; the
page example used three. Deep ad hoc research can process much more context than
the small numerical query. Unknown usage and embedding/audio prices remain
excluded from the known costs.

Four retained production Trading Agent batch windows used 0.67–0.88 million
generation tokens and 95–99 recorded logical gateway calls each. The October 12
window has 577,951 input and 298,747 output tokens, with 97 usage-bearing records;
all reported `deepseek-flash`. Its five horizon-synthesis calls share pipeline
parent `7e8e456e-9874-4f48-b61c-fd3fd8d51e2f`. The window also includes related
research, monitoring, review, relevance and memory calls. The old gateway records
do not establish every HTTP retry or embedding request.

The news/company total instead combines four retained roots: the original failed
full news job, retry, dossier recovery and news recovery. It contains 310 recorded
chat/repair attempts, including four without priced usage, plus 470 embedding
attempts. It must not be presented as a Trading Agent candidate batch or as an
independently successful original job.

### Model-routing cost is larger than the small handoff overhead

Reprice the October 12 Trading Agent window at unchanged token/call counts:

- Pro for research decisions, final analytical synthesis, candidate synthesis and
  memory reflection: 53 recorded calls.
- Flash for monitoring, review and relevance: 44 recorded calls.

At the same hypothetical cache-hit pattern, the total becomes **$0.6485 versus
$0.2431: +166.8%**. With no cache in either version, it is **$0.7277 versus $0.2659:
+173.6%**. This is a price-only sensitivity calculation. Pro may produce different
reasoning/output and the new engine may make different calls. Existing Flash
cache entries do not transfer to Pro; preserving the hit pattern is a warm-cache
assumption, not an immediate model-switch saving.

This does not by itself breach the combined-workload allowance, but it is much
larger than the proposed feedback change. For illustration only, one recorded
Home build, four such Trading Agent batches and ten numerical ad hoc queries cost
about **$3.05** at common prices with recorded cache. Repricing just the Trading
Agent core as above raises that to **$4.67 (+53.1%)**, holding Home/ad hoc unchanged.
Those frequencies are assumptions, not an observed schedule. They show why
neither a small-query average nor one Home job establishes the overall budget.

The [retained workload comparison](../verification/llm-flow-2026-10-15/quality-cost/workload-cost-baselines.json)
contains bounded metering events, purpose totals, exact source attribution and
the arithmetic. No new provider or pipeline execution was made. Resolve the
model-routing budget across real workload frequencies before treating the
previous +10–15% marginal-handoff allowance as room for the whole rollout.

## Small-query back-of-envelope estimate — excludes full background workloads

This estimate uses the recorded cold-cache off-peak rates and example costs;
it does not assume current workload frequencies or measure the proposed changes.
It covers LLM spend, not external search, storage or infrastructure charges.

For an illustrative 1,000-run workload, take 500 single-analysis direct queries
at $0.01922 each and 500 short research runs at $0.03395 each: **$26.59 total**.
The research example already includes one Pro correction; the direct example
does not. These supplied-source examples are unit-cost proxies, not established
averages for all ad hoc and deep-research jobs.

Assume the revised handoff adds the following tokens in total across existing
calls in each run, after replacing repeated representation:

| Additional tokens per run | Rate per million | Added USD/run |
| --- | ---: | ---: |
| 500 Pro input | $0.66 | 0.000330 |
| 500 Pro completion, including reasoning | $1.98 | 0.000990 |
| 500 Flash input | $0.15 | 0.000075 |
| 200 Flash completion, including reasoning | $0.60 | 0.000120 |
| Total | | **0.001515** |

That is **$1.52 per 1,000 runs**, or **+5.7%** on this example mix. The token
counts are assumptions, not a measured consequence of adding concise supporting
basis. Extra internal reasoning belongs in this allowance too. No deduplication
saving is credited separately.

The measured ordinary Pro correction costs $0.009696. An extra correction on
10 percentage points of all runs therefore adds about **$0.97 per 1,000 runs**.
Count increases over the baseline correction rate, not all existing corrections
again. This assumes the extra corrections resemble that recorded call; their
actual cost depends on context and generated reasoning.

| Increase in runs requiring Pro correction | Estimated cost / 1,000 runs, including assumed handoff growth | Increase over $26.59 baseline |
| --- | ---: | ---: |
| No increase | $28.10 | +5.7% |
| +10 percentage points | $29.07 | +9.3% |
| +20 percentage points | $30.04 | +13.0% |
| +40 percentage points | $31.98 | +20.3% |

Formula: `new average cost = baseline average + 0.001515 + added correction rate × 0.009696`.
The +20% limit is $31.91 per 1,000 runs here. With the lean token assumption,
it allows about 39 additional percentage points of corrections, before other
new costs. Double the assumed token growth and the +20-point correction scenario
becomes **+18.7%**, leaving little room for repairs or longer reasoning. This is
why call count alone cannot establish the cost effect.

For context, the earlier extra-step prototype has a different cost profile. If
its two recorded/reconstructed unit costs applied to every run in each category:

| Direct queries / short research by run count | Baseline / 1,000 | Prototype / 1,000 | Increase |
| --- | ---: | ---: | ---: |
| 25% / 75% | $30.27 | $31.61 | +4.4% |
| 50% / 50% | $26.59 | $30.41 | +14.4% |
| 75% / 25% | $22.91 | $29.21 | +27.5% |

That prototype crosses +20% at approximately **62% direct queries by run count**
under these unit-cost assumptions. Its individual +45.7% direct replay therefore
does not imply a +45.7% workload increase. This arithmetic does not resolve its
quality failure or validate the mixed-draft research projection.

The **+10–15% planning allowance applies only to this illustrative small-query
mix**. It is not an overall application estimate. Incorporate Home and other
background research at their actual frequency, context volume and model routes
before allocating the combined +20% allowance. Net token growth, correction
frequency, correction depth and failed work determine the result. Keep extra
review effort focused on useful corrections rather than buying more stages by
default.

## Source changes for the next implementation pass

Keep the existing shared lifecycle and stage configuration. Do not add an intent
classifier, helper agent or parallel feature implementation.

| Owner | Change | Evidence or boundary |
| --- | --- | --- |
| `services/zopedia_agent.py`: researcher state, final and judge input construction | Carry user observations/assumptions separately from external source reports and generated interpretations, using the original message/source identity | The prototype lost the premise's origin despite receiving the full question. Use model interpretation, not keyword rules |
| Same owner: `_final_user_prompt` | Replace the blanket instruction to treat user premises as unverified with a distinction between task assumptions and external assertions open to checking | The blanket instruction currently exists only in the structured-response branch. It was not the direct cause of the ordinary-answer prototype failure; fix the inconsistent shared contract too |
| Same owner: existing analytical findings and judge schema | Attach concise supporting basis to consequential conclusions in already-budgeted analytical output; reviewer returns material correction, basis and counterexample when useful | Do not add the separate extraction call used by the promising prototype. Do not demand an exhaustive assertion inventory or expose private reasoning. This integration is untested |
| Same owner: `_run_answer_judge` and synthesis feedback serialization | Remove the manufactured feedback `answer_markdown` from model input; retain substantive correction fields once and audit identity outside the prompt | It is currently reconstructed from `answer_points` and both are passed back. Full original source access stays intact |
| Same owner: correction handoff | Resolve substantive feedback against original sources and task premises, rather than assuming the reviewer is right | Current experiments show premium resolution can adopt an incorrect review or overrule a correct one |
| `services/llm_flow.py` and existing Admin data contract | Reflect the actual handoff in the existing step details after acceptance | No additional stage or default reviewer/cycle. Existing model/effort controls remain editable |

Removing duplicate compatibility feedback and unused audit bookkeeping reduced
one serialized review packet from 15,217 to 9,771 characters (35.8%). This is a
packet-size measurement, not demonstrated token or full-workflow savings. Keep
all substantive feedback and original material available.

An expanded Pro structure already exceeded its matched premium-stage budget
without improving the answer. Keep the next analytical representation concise
and replace repeated prose rather than adding a second essay. Do not assume a
schema change is cheap simply because call count stays the same.

## Evaluation and release

1. Implement the shared premise/provenance and feedback contract first, with
   unchanged default models, one reviewer and one cycle. No extra premium call or
   Flash extraction stage becomes mandatory. Keep the direct and ordinary
   research entry paths distinct within the same engine.
2. Personally compare complete outputs against originals on this case and
   unrelated financial, dated-event and source-heavy questions. Include sound
   drafts to check that reviewers preserve useful analysis rather than creating
   objections. Use unfamiliar cases before more tuning on the original example;
   inspect correctness, usefulness, assumptions, causal reasoning and clarity.
3. First reprice the retained full Home attempt ledger under the proposed model
   routes, using its actual per-role input/output/cache usage. Include scheduled
   Home, actual Trading Agent candidate batches, news/company ingestion and other
   background jobs at representative frequencies; the short
   source examples do not represent them. Then capture full matched direct-query and research runs with the same settings,
   objectives and input material. Report per-category spend and combine it using
   representative workload frequencies, including actual correction frequency,
   repairs and failures. A reviewer that costs less but triggers more Pro
   revisions can increase the bill substantially.
4. Accept only a candidate with better complete answers and at most +20% measured
   combined-workload cost. Individual runs or categories may increase by more;
   an overall +50% increase or a cheap candidate that retains the error fails.
   Treat model variation and unpriced attempts explicitly; a single replay is
   insufficient to establish the combined increase.
5. If that candidate fails, report the quality/cost tradeoff before expanding the
   architecture. Blind checks and free-form competing analyses were already
   tried here and failed; they are not demonstrated fallback solutions. Preserve
   paid work instead of re-running successful stages after a harness error.
6. Only after local acceptance, reflect the final handoff in Admin and release
   the existing follow-up to dev across all shared-engine consumers. Production
   still requires explicit permission. Do not build/deploy around an unresolved
   analytical-quality failure.

Costs use reported prompt and completion tokens repriced at common cold-cache
off-peak rates: Flash $0.15/$0.60 and Pro $0.66/$1.98 per million input/output
tokens. Reasoning is already included in completion tokens. Actual cache counts
and provider estimates are retained separately. The
[current pricing](https://api-docs.deepseek.com/quick_start/pricing/) agrees with
the recorded price snapshot. DeepSeek's
[cache matching is best effort](https://api-docs.deepseek.com/guides/kv_cache/);
budget compliance cannot depend on guaranteed cache hits.

The [Chain-of-Verification paper](https://arxiv.org/abs/2309.11495) motivated the
independent-check experiment; it did not predict a successful result in this
application. The [self-correction study](https://arxiv.org/abs/2310.01798) likewise
does not establish a universal model limitation. Our actual outputs decide this
candidate's acceptance, not price or a paper's results on other models/tasks.

Selected original inputs, complete model outputs, normalized attempt receipts and
the comparison formulas are retained in
[verification evidence](../verification/llm-flow-2026-10-15/quality-cost/README.md).
Raw configurations, scripts and intermediate artifacts remain under
`tmp/quality-cost-20261015/`. Experimental prompts are not application defaults.


## October 15 implementation checkpoint — release held

All changes are local. Existing producer values remain unchanged and are now
editable beside the relevant Admin flow step. Routine research and preparation
use Flash; Pro is routed to core analysis and selective deeper reasoning. News
and knowledge synthesis keep an economical workload override. Reviewer count and
review cycle remain one. No new spending controller, classifier, fixed escalation
quota or deterministic relevance/intent rule was introduced.

The shared implementation adds leaf-specific reuse identities, simultaneous-work
coordination, preserved operational retry continuation, explicit resolved-search
execution, optional original/index navigation, canonical reviewer corrections and
one final answer generation. Originals are the initial view and reach synthesis
and review. A forced compact-view experiment caused repeat reading and exhausted
the research window; it was removed. The first analytical synthesis does not copy
the routine planner's proposed answer/outline. Premium analysis instructions are
editable in Admin, and changing them invalidates the affected leaf.

Admin was inspected on desktop and at 390×844; no horizontal overflow. Home still
shows 12 research and 12 enrichment candidates; Trading keeps shortlist 6, pool 8
and 4 actions. In an isolated QA scope, saving 8→9 and resetting to 8 succeeded;
an initial 503 is recorded, with its underlying intermittent persistence failure
still unresolved. The worker-default projection now avoids showing a previous
run's override after an administrator resets it. No normal dev setting was saved.
TypeScript checking passed. All 44 Python boundary checks pass; they protect execution and settings, not
qualitative reasoning.

Personal review found correct retailer arithmetic and a sound unfamiliar
reservation explanation, plus two substantive failures. Pro's technical answer
still inferred a cause from a one-way server condition and replaced the user's
deadline objective; Flash accepted it. In an unfamiliar logistics case, Pro said
mix alone could explain 88%→94% performance, although unchanged per-class rates
can produce at most 91.429% at the new mix. Flash accepted that too. Removing a
cheap draft from the first synthesis did not resolve the source case in the
latest direct replay. Stop tuning that one question; do not call this a successful
reasoning repair or deploy around the remaining failure.

The implementation probes made 41 distinct provider attempts: known estimated
cost $0.168808242, plus two attempts with unknown usage. This is development
experiment spend, not a workload forecast. Existing $37.21 reference math puts
one premium scenario at $42.85 (+15.2%) with approximately $1.80 headroom below
+20%, but actual escalation and output growth are unmeasured. No projected reuse
or input savings are credited as achieved.

Next: evaluate the analytical model/effort and review contract on independent
retained tasks at the actual runtime limits, then measure the representative
weighted workload. Keep existing coverage/depth/reviewer values while doing so.
A later release must preserve live source changes, deploy the same engine to all
dev consumers and verify normal authenticated Admin plus queued research. Current
preview uses isolated loopback authentication and does not establish that proof.

Evidence: [implementation review](../verification/llm-flow-2026-10-15/quality-cost/implementation/README.md),
[complete answers](../verification/llm-flow-2026-10-15/quality-cost/implementation/case-outputs.json),
[attempt costs](../verification/llm-flow-2026-10-15/quality-cost/implementation/attempt-costs.json),
[source hashes](../verification/llm-flow-2026-10-15/quality-cost/implementation/source-manifest.json).


### Deployment ownership handoff

The owner assigned deployment to the Admin/Health integration agent. This thread
will not build or deploy. Coordination lives at
`/tmp/sn-reliability-20261015/LLM_COST_HANDOFF.json`, linked from the requested
`BACKEND_READY.json`. The model/reasoning candidate remains unaccepted and must not
be copied incidentally into an operational-reliability release. This handoff does
not certify the independently owned database, learning or Trading-memory repairs.
The exact source snapshot, pre-change baseline, combined diff and validation are
in `/tmp/sn-reasoning-cost-20261015/handoff`. Shared execution/job files include
independent backend edits, so the combined diff is not an automatically releasable
patch. Preserve those repairs and use their owner's scoped handoff when preparing
the release.


## October 16 continuation — analytical tool ownership

Resumed at the owner's request; deployment remains with the integration agent.
The pre-change sources for this pass are under
`/tmp/sn-reasoning-repair-20261016/before`.

The first controlled checks do not establish a prompt-only repair. A concise
review with explicit verification caught the impossible logistics mix but still
accepted the reversed server-close implication. Both baseline and candidate
Pro/max direct analyses exceeded the 90-second experimental step deadline;
usage was unavailable, so queue time and generation time cannot be distinguished.
Pro/high completed two independent cases but another exceeded that deadline.
No saved model setting, research size, depth, reviewer count or runtime limit
was changed. Retain interrupted attempts as unknown cost, not free work.

The earlier logistics harness also supplied a dictionary envelope where the
engine expects a list of source objects. The loop silently discarded those
records. Correct the harness and reject malformed evidence at the shared engine
boundary before inference. The numerical counterexample remains meaningful,
but the earlier result did not validate source transport. The source question's
75 seconds is an observed client setting, not an explicit immutable service
requirement: a longer deadline is a legitimate option if its objective is stated.
The material remaining logical error is diagnosing inference start from the
server-close observation alone.

The next shared change addresses execution ownership: hand routine research to
the configured synthesis model when analysis starts, keep that model in control
across its tool calls, and let it return the finished answer directly. Advertise
the existing bounded Python tool during analysis. Core synthesis already costs
one premium call; a direct analytical answer should replace that call, while
actual computations may require further decisions within the existing budget.
Explicit deeper analysis continues to use the separately configured reasoning
route. Do not switch back to Flash immediately after Pro requests a tool, and
do not run another synthesis after the analytical model has finished.

Checks must cover complete source answers, numerical feasibility, valid uncertain
hypotheses, correct task assumptions and new necessary/sufficient-condition
cases. Reviewer feedback remains evidence to assess rather than publication
permission. No question-specific answer rules or mandatory independent checker
chain are authorized by this experiment.

### October 16 results — handoffs repaired; reasoning acceptance still open

The analytical model now owns its tool calls through the finished answer. The
routine model hands over acquired evidence and open questions; analysis no longer
has to regenerate the planner's bookkeeping or inherit its proposed answer.
The analytical response contract contains a tool action or completed answer.
A successful analytical answer replaces the former separate synthesis call.
The separately configured deep-reasoning route keeps the same ownership.

The existing Python runner now accepts computation without a dataset. Declared
missing datasets still fail during input resolution. Structured numerical results
are retained in the existing result JSONB. The agent's optional index can summarize
logs, while original evidence now includes the bounded computation outputs,
including stdout, so analysis and review receive the values actually calculated.
The existing raw-output reader is also directly discoverable. No question parser,
fixed numerical examples, additional reviewer or new mandatory model pass was added.

Review now records the necessary correction alongside each checked claim. Code
derives the existing overall status from those edits; the reviewer cannot supply
an independent `accept` that drops its own corrections. The reviewer sees originals
and the draft without the author's separate self-justification. Its completed
review is checkpointed **before** correction inference starts. Pending corrections
resume without purchasing research or review again. Analytical follow-up can
resolve the same review without consuming another cycle.

The unreleased analytical default is **Pro/high**, with Pro/max still available
in Admin. Pro/max repeatedly exceeded the unchanged 90-second step budget;
Pro/high completed several material analyses and corrections within that budget.
This retains the premium model, rather than confusing model tier with maximum
thinking effort. Existing explicitly saved routes are preserved. News/Knowledge
Flash synthesis retains its previous max effort; Flash review stays low because
the no-thinking review experiment produced incorrect arithmetic and logic.
Candidate counts, research depth, source/search limits, reviewer count, one-cycle
default, schedules and whole-run/step limits are unchanged. No normal dev or
production configuration was saved.

Personal review of full live outputs found both progress and remaining failures:

- A full Pro/high run corrected the original logistics error: unchanged class
  rates would require a 109% express on-time rate, which is impossible. The largest
  mix-only improvement is about 3.43 points, below the observed six points. This
  requires some within-class performance change, while attribution to the routing
  model still needs evidence. Another run missed that calculation; review caught
  it, but correction timed out. The behavior is not yet reliable.
- A full source answer corrected the reversed server-close implication. Other
  attempts still confused the pre-inference threshold with an overall deadline
  or overdiagnosed causes from transport observations. A longer client deadline
  can be a legitimate recommendation; the original question did not make its
  observed 75 seconds an immutable service requirement.
- The final valid-mix run used Python and received its actual printed outputs.
  Constant outpatient/inpatient rates of 88%/98% explain aggregate 90%/94% with
  the supplied mix. That valid alternative must survive review. The reviewer
  nevertheless requested an aggregation qualification, causing another correction
  that timed out. This is a calibration and cost problem, not proof that a second
  reviewer or more rounds should be added.
- Separate checks accepted a randomized-trial estimate with uncertainty and a
  qualified business hypothesis, and caught a new necessary/sufficient-condition
  error. Those role probes do not substitute for complete runtime acceptance.

The final logistics continuation reused its recorded review with exactly one
correction call and no new research/review. Its budget was the original 300 seconds
minus the preceding attempt's elapsed time; the 90-second model limit was retained.
That correction also timed out. No further paid retries were started.

All 71 mechanical checks pass; they do not establish narrative quality. Keep the
candidate out of the independently owned backend release. Next work is reliable
analytical verification, reviewer calibration and completion within existing time
budgets, followed by a representative workload cost comparison. Do not buy repeated
full investigations to recover a pending correction, increase coverage/depth, or
claim the +20% workload condition from a few small examples.

Evidence and attempt accounting:
[October 16 review](../verification/llm-flow-2026-10-16/reasoning/README.md).
Deployment remains with the integration agent; the LLM-only coordination pointer
is `/tmp/sn-reliability-20261015/LLM_COST_HANDOFF.json`. It does not certify backend
reliability.

### October 16 evaluation clarification — controlled replay is the primary check

The owner's request for backtesting and proper evals clarifies the earlier term
"live checks." Fresh model inference is needed to evaluate changed model behavior;
it does not require current web searches, production jobs or deployment. The
preceding exploratory probes diagnose failures but are not the representative
paired evaluation needed for acceptance.

Use the existing historical corpus/reader and shared engine as foundations. The
older product eval script checks tagged fixtures and tool behavior, while the
critique harness can make web searches. Neither is an accepted paired, isolated
quality-and-cost suite for this candidate.

Proposed evaluation protocol:

1. Freeze representative ad hoc, queued-research, Home, Trading and news/company
   inputs, including original evidence, conversation state and a versioned memory
   snapshot. Include ordinary successes, known failures and unfamiliar cases.
2. For synthesis/review comparisons, hold acquired evidence fixed. For complete
   agent comparisons, let tools search/read the frozen corpus and isolated memory;
   do not restrict the candidate to the baseline's recorded action sequence or
   silently fall back to today's web. Preserve private-input access boundaries.
3. Run baseline and candidate through the same engine/budgets on the same cases.
   Version code, prompts, routes, corpus and settings. Repeat difficult cases to
   expose variability; reserve a held-out set that is not used for prompt tuning.
4. Grade numerical reasoning, source interpretation, task-premise preservation,
   useful connections and answer quality. Assess pre-review and post-review
   answers separately so unnecessary or harmful edits are visible. Use a blinded
   independent evaluator plus personal review of material disagreements and a
   sample of agreements. The production reviewer is not its own acceptance judge.
   Verify objective calculations with code; do not grade expected wording or
   require one preselected causal story.
5. Record all provider attempts, corrections, repairs, failures, unknown charges,
   completion rates and latency. Compare `sum(frequency × candidate cost)` with
   `sum(frequency × baseline cost)` using the same production workload frequencies.
   The combined ratio must be at most 1.20; a few cheap-query averages do not
   establish this. Count inference needed to construct reusable summaries too.
6. Only after replay acceptance, run a small integration check for actual provider,
   queue, authentication and persistence behavior. Historical Trading/forecast
   backtests additionally need available-at-time data and separately held outcome
   labels. Later returns do not by themselves establish good research reasoning,
   and historical facts may already be present in model pretraining.

This clarification does not run new paid evals or declare the suite implemented.
