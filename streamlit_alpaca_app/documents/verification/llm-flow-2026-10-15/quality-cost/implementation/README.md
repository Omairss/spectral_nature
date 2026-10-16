# October 15 implementation review

Status: implemented locally; **not accepted for dev release**. No images were
built, no app or worker was deployed, and no production setting was changed.
The local preview is `http://127.0.0.1:3088`, backed by the real settings service
in the isolated `llm-cost-upgrade-review-20261015` settings scope. Its synthetic
loopback principal does not establish normal signed-in dev acceptance.

## Implemented

- Workload selection and producer controls in Admin → LLM, using the shared
  registry consumed by jobs. Existing environment values remain the defaults:
  Home research/enrichment 12/12; Trading output/shortlist/pool/actions 4/6/8/4;
  news company/story/query/result limits 25/12/24/4. Reviewer count and cycle
  remain one. Time, output, concurrency and recovery settings retain their
  previous worker values. No schedule changes.
- Flash routine research, monitor, review and first-pass processing; Pro core
  Home/Trading/interactive analysis, substantive corrections and selected deeper
  research. Routine news/knowledge preparation remains Flash and may ask for
  Pro analysis within its current call. No added classifier or fixed escalation
  quota. Deep reasoning has editable shared instructions.
- Producer-specific identities and a shared lease for identical supplied-evidence
  work. Sources, schema, instructions, model, reasoning policy and ownership
  remain in the identity; unrelated monitor/reviewer changes do not invalidate
  an unchanged leaf. Existing operational retry identity and continuation are
  preserved. Private inputs do not become shared through this change.
- Explicit resolved search plans bypass the second planner. Unrestricted resolved
  dates remain unrestricted. Unresolved intent still receives LLM interpretation;
  semantic retrieval and original source access remain available.
- Model-chosen original/index views, with originals initially available. Reading
  an existing source does not reacquire it. Full originals reach analysis/review.
  The initial experiment forcing compact views caused repeated reading and
  exhausted its research time; that implementation was replaced.
- Final synthesis uses its reserved completion time. A failed synthesis is not
  immediately repurchased by the post-loop handoff on identical evidence. Run,
  stage and research limits remain unchanged.
- One representation of reviewer corrections, concise supporting basis in the
  existing analytical response, and no final automatic presentation rewrite.
  The first analytical synthesis receives originals rather than the routine
  planner's proposed answer/outline. Existing drafts remain available for
  review correction, user-requested revision, continuation and failure recovery.

## Evidence and limits

Read [complete answers and reviews](case-outputs.json), not just reviewer verdicts.
The synthetic retailer case calculated operating profit 12→8 and cash after
capex 4→8 correctly. The reservation case preserved task assumptions and the
one-way policy implication. Routine structured news preparation used one Flash
call without escalation. These are small checks, not a representative production
Home/news/Trading workload evaluation. The news probe used generic instructions,
not the complete business-model task.

Two material reasoning failures remain. In the technical source case, Pro still
turned a sufficient server-close condition into a diagnosis and recommended a
longer deadline without grounding it in the user's objective. Flash accepted it.
Removing the routine draft and repeating the direct analysis did not resolve the
failure. No more prompt revisions or paid reruns of that question were made.

The unfamiliar logistics case also made a numerical error that review accepted:
it said changing the standard/express mix could alone explain 88%→94% on-time
performance. Under unchanged class rates, the most favorable express rate is
100%; `0.7 × standard + 0.3 × 1 = 0.88` implies standard = 82.857%. With the new
50/50 mix, the maximum is 91.429%. Thus mix alone cannot explain 94%; at least
2.571 percentage points require within-class performance change or another change
in what was measured. The answer also invented 60/75-second decision targets.
This is independent evidence that the analytical/review combination is not yet
reliable enough to call the reasoning repair successful.

[Actual attempt accounting](attempt-costs.json) covers 41 distinct provider
attempts, including failures: **$0.168808242 known estimated cost**, with **two
attempts of unknown usage**. Start and terminal receipts are not counted twice;
reasoning is already in output tokens. This excludes earlier investigations and
is development experiment spend, not recurring application spend. Some receipts
could not be persisted to SQL; immediate local receipts were retained. The
`source-revised` historical filename actually contains a routine supply-agreement
question through the interactive route; the export labels the real task.

The earlier $37.21 reference and $5.64 premium scenario remain planning math.
The projected total is about $42.85 (+15.2%), before unmeasured savings, with
roughly $1.80 left below its $44.65 reference ceiling. Actual escalation frequency,
correction costs and output growth have not been established over the real mix.
Therefore the +20% workload requirement is **not verified**. No savings from
coalescing, caching or context changes are claimed as measured daily savings.

## UI and execution verification

The real editor rendered on desktop and at 390×844 with no horizontal overflow.
Observed Home 12/12, Trading shortlist/pool/actions 6/8/4 and workload-specific
Pro/Flash synthesis. Saving an isolated test value 8→9 succeeded after an initial
503; resetting to default restored 8. No normal dev knob was changed. The
underlying intermittent database failure is not proven fixed. Preview-only
`allowedDevOrigins` enabled loopback hydration; product security was unchanged.
The API projects the worker default after reset, not an earlier run's saved value.

TypeScript checking passed. All 44 Python checks pass for transport, ownership, settings, producer invalidation,
continuation, coalescing, resolved-plan execution and the completion handoff. They
do not assess prose or reasoning quality.
[Source hashes](source-manifest.json) identify this local checkpoint. The baseline
and raw paid outputs are retained under `/tmp/sn-reasoning-cost-20261015`.
Independent work changed shared execution/job files during this task; do not
replace them wholesale with old copies when preparing a future release.

## Next decision

Keep this candidate local. Assess the analytical model/effort and review contract
on independent retained financial and source tasks, including direct comparison
with the prior model at the same runtime limits. Then establish actual escalation
and total cost on the representative workload mix. Do not add reviewers, increase
research breadth/depth, impose proof gates or tune question-specific rules to
make these examples pass. A later dev release must cover API, UI and all research
consumers while preserving their environment values and schedules.
