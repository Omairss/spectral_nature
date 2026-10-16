# Trading Agent Research Loop Plan

**October 16 retirement:** the inactive per-horizon rollback entrypoints, mode and timeout branches have been removed. Only the accepted shared-research producer is supported. Earlier rollback instructions below are historical evidence and must not be implemented.

Date: 2026-06-24

Last updated: 2026-09-21

## Purpose

Improve the Trading Agent's calls by turning each generated candidate into a
durable research experiment with an outcome, a post-mortem, and a specific
learning target for the next run.

This is not a plan to add a side trading bot, hardcoded rules, or a quick
performance patch. The loop should strengthen the existing AQL/Zopedia and
pipeline spine:

- Trading Agent remains a materialized admin experiment.
- Streamlit reads persisted runs and candidates.
- AQL/Zopedia owns evidence collection and synthesis.
- The system records outcomes and reviews, then uses those reviews to improve
  shared evidence contracts, prompts, retrieval, ranking, and company memory.
- Alpaca execution remains disabled unless explicitly approved later.

## Current Baseline

The first performance notebook, `notebooks/trading_agent_performance.ipynb`,
shows the current sample is too small to optimize against mechanically:

- 34 generated candidates.
- 20 candidates have at least one post-pick price bar.
- 2 candidates have reached their stated horizon.
- Matured win rate is 50.0 percent on 2 rows.
- Mark-to-market win rate is 45.0 percent on 20 scored rows.
- Directional early read: avoid calls look better than watch/long calls, but
  the sample is not large enough to turn that into a rule.

The right next step is not to tune thresholds. The right next step is to create
the feedback machinery that explains why each call worked or failed.

## Core Product Question

For every candidate, the system should be able to answer:

1. What was the trade thesis?
2. What evidence made the thesis worth watching?
3. What evidence was missing or weak?
4. What happened after the call?
5. Did the outcome validate the stated thesis, contradict it, or happen for an
   unrelated reason?
6. What should the next research loop change: retrieval, evidence slots,
   candidate selection, direction, horizon, sizing posture, or no change?

Without this, win rate is just a scoreboard. It does not tell the agent how to
get better.

## v0.5 Compatibility

This plan can fit v0.5, but only with strict sequencing.

Compatible now:

- Phase 0, `trading_agent_outcomes`, is a measurement artifact. It consumes
  existing persisted candidates and price history. It does not change candidate
  generation, AQL behavior, Trading Agent UI behavior, or user-facing calls.
- Phase 1, `trading_agent_research_reviews`, is compatible if it runs as a
  bounded background/admin review artifact over completed outcomes. It should
  not mutate prompts, memory, rankings, or product output directly.
- The performance notebook can become a consumer of these datasets. That
  supports v0.5 behavior locks because Trading Agent artifacts become easier to
  verify and audit.

Must wait for v0.5 P0 spine/retrieval work:

- Candidate Package V2 should wait until AQL/Zopedia retrieval/tool discovery
  and company-memory runtime proof are stable enough to supply the evidence
  slots reliably.
- Candidate Package V2 also waits for the v0.6 AQL/Zopedia gateway. The current
  Trading Agent can call AQL for ticker research and still make direct final
  synthesis/review model calls. That is not acceptable for V2.
- Shadow V2 should wait until the V2 package schema is backed by the shared
  AQL/Zopedia contracts, not by a Trading-Agent-specific side path.
- Replacing V1 should wait until fallback/failure contracts are resolved in
  the v0.5 simplification track.

Hard boundaries:

- Do not add a side research agent.
- Do not add deterministic performance-driven ticker filters.
- Do not tune directions or horizons from the current small sample.
- Do not write review recommendations directly into prompts or Zopedia memory.
  Reviews create an improvement queue; humans or gated jobs promote changes.
- Do not change Alpaca execution behavior.

Mapping to v0.5:

- Phase 0 belongs with **P0.1 Behavior Locks** because it makes Trading Agent
  output auditable and reproducible.
- Phase 1 can run alongside **P0.3 Company Memory Runtime Proof** as an admin
  analysis artifact, but it must not require company-memory writes to be
  complete.
- Phase 2 belongs after **P0.2 Retrieval / Tool Discovery** and **P0.3 Company
  Memory Runtime Proof**, and after the v0.6 AQL/Zopedia gateway can record
  Trading Agent research, final synthesis, and review calls by surface and
  purpose.
- Phase 3 and Phase 4 are post-P0 product evolution work.

## Implementation Status

Updated: 2026-09-21

The candidate-generation boundary has moved to shared research V2. The active
path builds balanced horizon scout lists, researches each selected company
once through Zopedia/AQL, persists `trading_agent_research_packages`, and then
uses those packages for every relevant horizon synthesis. Recall and cost are
independently tunable through scout breadth, unique-company pool size, AQL tool
depth, and provider-safe parallelism.

The old per-horizon implementation remains available through
`TRADING_AGENT_GENERATION_MODE=legacy_per_horizon_v1`. The default is
`shared_research_v2`.

Production diagnosis on 2026-08-10 found the previous path timing out after
300 seconds on all five sequential horizons. Its thread timeout did not cancel
the underlying research, and timeout rows became price-only fallback
candidates. Shared research V2 removes that horizon timeout wrapper and never
creates fallback candidates. If no grounded package is available, the horizon
persists an error with zero candidates. If every horizon fails, the job fails
after diagnostic datasets are persisted.

A latest-feed replay reduced the planned research work from 30 per-horizon
company packages to 8 unique packages while retaining representation across
all five horizons. A live one-company probe completed through the actual
gateway and correctly returned a low-confidence watch candidate when company
and current-event evidence were missing.

Production verification completed on 2026-08-10. The first shared-research V2
execution completed seven of eight unique company packages, isolated one
provider empty-content response, produced successful runs for all five
horizons and 13 grounded candidates, and emitted no fallback rows. The
production job is pinned to the final V2 image digest. Recall can now be tuned
through scout breadth, global pool size, and per-company research depth
without restoring repeated per-horizon research.

Development namespace verification completed on 2026-09-21. A manual Azure
execution completed eight of eight company packages and all five horizons,
then materialized 14 candidates and 14 outcomes without moving production
pointers or writing Zopedia memory. All 14 candidates were research-only
`no_call` rows and all outcomes were `not_scoreable`; this is a valid pipeline
and presentation check, not evidence of investment performance. The exact
view adapters accepted all five horizon results and all 14 candidates under
the `plain_investor_research_v2` contract.

Candidate retrieval was upgraded later that day. The All Market materialized
corpus now retains all 999 ranked symbols per horizon, each horizon performs a
24-company primary retrieval followed by reciprocal-rank-fusion reranking,
and the eight-company research pool evaluates complete rank tiers before its
cutoff. Development run `194a70a0-cfaf-47c2-ba6b-bc5b72df0c0c` completed the
new path with 8/8 packages, 5/5 horizons, and 12 candidates. One candidate was
a supported directional call; the other 11 remained research-only. This is a
better measured selection contract, not yet proof of better returns.

The development-only ranking revision now replaces that four-field retrieval
score with `domain_balanced_all_signal_v1`. The Trading Agent loads all
usable pre-decision stock-linked sources before research, normalizes them within
industry/size peers, balances correlated fields inside signal families, treats
missing sparse sources as neutral, and persists the complete ranking trace in
`trading_agent_ranked_opportunities`. Outcome labels and post-call reviews stay
outside the rank. This broadens evidence coverage but still does not establish
predictive performance; the historical point-in-time backtest remains required.

Development execution `0ce40dea-5b75-4460-a654-69df89f13773` verified the
completed path on 2026-09-21: 5,994 ranked rows, 8/8 company packages, 5/5
horizon runs, and 18 candidates. Every horizon selected research through
`all_signal_rank`. Five evidence domains were populated; positions and
stock-linked macro context were empty, and commodity proxies remain unlinked
to individual stocks.

Phase 0 and Phase 1 first slice are implemented behind the existing
`trading-agent-build` materialization job.

Delivered:

- `trading_agent_outcomes` is now a materialized dataset emitted by the
  Trading Agent job.
- Outcomes consume current plus recent `trading_agent_candidates` and
  `trading_agent_runs`, then score them against latest `price_history`.
- Outcome rows keep `matured`, `mark_to_market`, `no_future_bar`,
  `missing_price`, and `missing_entry` separate.
- Direction-aware performance is recorded as `signed_return_pct`, while raw
  ticker movement stays in `raw_return_pct`.
- `trading_agent_research_reviews` is now a bounded materialized dataset for
  mature outcomes, with optional mark-to-market review behind
  `TRADING_AGENT_REVIEW_INCLUDE_MARK_TO_MARKET`.
- Review recommendations are inert audit columns. They do not mutate prompts,
  memory, rankings, candidate generation, or Alpaca behavior.
- `SOURCE_DATASETS["trading_agent"]` now advertises runs, candidates, outcomes,
  and research reviews together.

Intentionally deferred:

- Benchmark-relative performance is represented in the schema but not yet
  populated.
- Post-call news/event attribution is not yet part of the review package.
- Candidate Package V2 now has a first durable package dataset and shared
  research execution path. The richer evidence-slot schema and historical
  evidence-native backtest remain incomplete.
- Trading Agent research, final synthesis, and research review use the shared
  AQL/Zopedia engine or gateway. Package rows retain evidence-pack IDs and
  bounded tool summaries for auditability.
- No performance tuning, direction filtering, prompt mutation, or Zopedia
  memory promotion was added.

Operational note on 2026-07-01:

- The live `trading-agent-build` job is configured for DeepSeek
  `deepseek-reasoner`.
- Latest runs produced five horizon run rows but zero candidates because the
  provider returned `402 Insufficient Balance`.
- That failure reinforces the v0.6 requirement: provider/model failures need to
  be reported by gateway telemetry and Admin/System Health, not inferred from
  raw job output or account dashboards.

## Target Architecture

### 1. Outcome Ledger

Create a durable `trading_agent_outcomes` materialized dataset.

Inputs:

- `trading_agent_runs`
- `trading_agent_candidates`
- `price_history`
- later: benchmark/sector ETF returns
- later: event/news rows after candidate time
- later: Place/Reject action log

Per candidate fields:

- `candidate_id`
- `run_id`
- `horizon_key`
- `ticker`
- `direction`
- `setup`
- `hypothesis`
- `confidence`
- `entry_ts`
- `entry_close`
- `target_exit_ts`
- `actual_exit_ts`
- `exit_close`
- `raw_return_pct`
- `signed_return_pct`
- `benchmark_return_pct`
- `excess_signed_return_pct`
- `outcome_status`: `matured`, `mark_to_market`, `no_future_bar`,
  `missing_price`, `missing_entry`
- `is_win`
- `is_matured`
- `generated_at_utc`
- `asof_time_utc`

Rules:

- Do not count same-day rows with no future price bar as wins or losses.
- Report matured and mark-to-market statistics separately.
- Keep repeated tickers across different horizons as separate calls, but add
  grouped views so duplicate exposure is visible.
- Add benchmark-relative performance before using aggregate win rate as a
  quality metric.

### 2. Research Review Ledger

Create a durable `trading_agent_research_reviews` dataset.

This should be LLM-reviewed, but grounded in the exact candidate, evidence,
post-call price path, and post-call events. Deterministic code assembles the
review package; the LLM writes the qualitative review.

Per candidate review fields:

- `candidate_id`
- `review_status`
- `outcome_summary`
- `thesis_verdict`: `validated`, `contradicted`, `unrelated_move`,
  `too_early`, `insufficient_evidence`
- `direction_verdict`: `right_direction`, `wrong_direction`,
  `direction_unproven`
- `horizon_verdict`: `too_short`, `too_long`, `appropriate`, `unproven`
- `evidence_verdict`: `strong`, `mixed`, `weak`, `stale`, `missing_key_slot`
- `primary_failure_mode`
- `primary_success_factor`
- `missing_evidence_slots_json`
- `recommended_contract_change`
- `recommended_prompt_change`
- `recommended_retrieval_change`
- `recommended_candidate_selection_change`
- `source_refs_json`

Review principles:

- Evidence is not analysis. The review must say what the evidence meant.
- Do not reward a correct price move if the stated thesis was unsupported.
- Do not punish a call that is not yet mature, but do flag early contradiction
  if the market or business evidence moved sharply against the thesis.
- A call can be a good research call even if it loses money because a named
  tail risk occurred. That should become a risk-model learning, not a blanket
  candidate-selection failure.

### 3. Candidate Research Package V2

The current candidate schema is too thin for learning. Add a richer research
package object before final candidate synthesis.

V2 gateway requirement:

- Per-ticker research packages, final cross-candidate synthesis, and post-call
  reviews must all enter through the v0.6 AQL/Zopedia gateway.
- Final synthesis is `formatter_over_aql`, not free research. It may compare
  completed AQL packages, but it must not introduce unsupported claims.
- Reviews are `research_grade` or `review` calls over the exact candidate,
  evidence pack, outcome, and post-call evidence, with model-call IDs retained
  in the review artifact.
- If provider/budget/evidence failures block synthesis, persist a horizon run
  state with explicit gaps instead of inventing candidates from price-only rows.

Required package sections:

- `business_identity`: what the company does, revenue model, customer type.
- `demand_quality`: bookings, backlog/RPO, revenue quality, customer count,
  customer wins, renewals, usage, or explicit absence.
- `recent_change`: what changed recently in business, macro, policy, sector,
  or price.
- `market_surface`: why the ticker appeared in the opportunity feed.
- `technical_context`: trend, volatility, support/resistance, stretch,
  event-window returns.
- `macro_sector_context`: rates, commodity, policy, sector/peer behavior when
  relevant.
- `counter_evidence`: strongest evidence against the call.
- `confirmation_events`: what would prove the thesis.
- `invalidation_events`: what would break the thesis.
- `horizon_rationale`: why this horizon matches the thesis.
- `expected_mechanism`: how the thesis should become price action.
- `evidence_gaps`: exact missing slots.

The final Trading Agent candidate should be synthesized from these packages,
not from price movement alone.

### 4. Research Loop Stages

Stage A: Candidate Scout

- Read materialized market opportunity, attention candidates, broad market
  coverage, technical signals, sector/peer behavior, and company memory.
- Produce a broad candidate pool with reasons for inclusion.
- Do not decide direction yet.

Stage B: Evidence Planner

- For each candidate, create a slot plan from the package schema.
- Use Zopedia memory first when current enough.
- Search/paginate only for slots that remain unfilled.
- Use live/current evidence for current trigger claims.
- Keep exact source refs and dates.

Stage C: Per-Ticker Research Package

- Run bounded AQL/Zopedia packages in parallel.
- Fill package sections with verdicts, not source dumps.
- Fail closed when evidence is insufficient.
- Record progress logs for long runs.

Stage D: Decision Synthesis

- Compare packages across candidates.
- Prefer fewer high-quality candidates over filling every horizon.
- Pick direction only when evidence supports it.
- If evidence is mixed but interesting, output `watch`, not `long`.
- Use `avoid` only when there is an actual risk-control or fading-momentum
  thesis, not just missing evidence.
- Include no candidate rather than a weak candidate when a horizon has no
  grounded package. This conflicts with the older fallback-candidate rule and
  should be resolved in the v0.5 failure-contract cleanup.

Stage E: Outcome Scoring

- Materialize outcome rows daily after price history refresh.
- Separate matured, mark-to-market, and not-yet-scoreable rows.
- Add benchmark-relative returns.
- Track duplicate ticker exposure.

Stage F: Post-Call Review

- For matured rows, run a full qualitative review.
- For mark-to-market rows, run lightweight interim review only when the move is
  large or contradicts the thesis early.
- Write recommended changes to review rows, not directly into prompts.

Stage G: Improvement Queue

- Aggregate review recommendations.
- Group by failure mode.
- Promote changes only when multiple reviews point to the same underlying
  issue or when a single issue is an obvious contract failure.
- Changes should target shared contracts:
  - retrieval/tool discovery
  - evidence-slot definitions
  - company memory promotion
  - candidate package schema
  - final synthesis prompt
  - opportunity scoring contract
  - UI/debug visibility

Stage H: Shadow Comparison

- Run the current Trading Agent and the research-loop V2 in parallel.
- Persist both outputs with comparable candidate IDs.
- Do not replace the user-facing Trading Agent until V2 beats V1 on:
  - matured win rate
  - excess return
  - thesis review quality
  - lower unsupported-claim rate
  - fewer weak/generic candidates

## Performance Metrics

Do not optimize only for raw win rate.

Primary metrics:

- matured win rate
- average and median signed return
- benchmark-relative signed return
- hit rate by direction
- hit rate by horizon
- average adverse excursion after entry
- average favorable excursion after entry
- thesis validation rate
- unsupported thesis rate

Secondary metrics:

- candidate count by horizon
- no-future-bar and missing-price count
- duplicate ticker concentration
- data-gap frequency by slot
- AQL package completion rate
- tool timeout rate
- source freshness distribution

Important views:

- all candidates
- matured only
- mark-to-market only
- long/watch vs avoid/short
- by horizon
- by confidence
- by setup type
- by source coverage quality
- by business sector
- benchmark-relative by sector

## Failure Modes To Track

- Price-only thesis: candidate surfaced from momentum but lacked business
  confirmation.
- Weak direction: research supported interest but not long/avoid direction.
- Horizon mismatch: thesis was long-term but candidate scored on a short
  horizon, or vice versa.
- Stale current trigger: recent-change claim used stale or undated evidence.
- Missing counter-evidence: final synthesis ignored the strongest bear/bull
  evidence.
- Duplicate exposure: same ticker appeared across horizons without distinct
  thesis.
- Sector beta mistaken for ticker alpha: move explained by broad sector or market action.
- Volatility trap: large move looked like opportunity but was unstable noise.
- Avoid overreach: avoid call used absence of evidence instead of a real
  downside thesis.
- Business-memory gap: company identity or business model was unresolved.
- Tool/retrieval gap: relevant source existed but was not found or paginated.

## Data Products

Add these datasets in order:

1. `trading_agent_outcomes`
2. `trading_agent_research_reviews`
3. `trading_agent_candidate_packages`
4. `trading_agent_shadow_candidates`
5. `trading_agent_improvement_queue`

The first two are enough to start learning from existing runs. The third makes
future calls better. The last two support safe replacement of V1.

## Notebook And Admin Surface

Extend `notebooks/trading_agent_performance.ipynb` into a recurring analysis:

- latest outcome ledger summary
- matured vs mark-to-market split
- direction and horizon breakdowns
- best/worst calls with thesis text
- review verdicts and failure modes
- drift over time
- V1 vs V2 shadow comparison once available

Admin UI should show only the useful product-facing subset:

- latest candidate performance snapshot
- matured win rate and excess return
- biggest failure modes
- data coverage warnings
- current shadow comparison status

Detailed traces stay in notebook/debug/admin, not public product cards.

## Implementation Order

### Phase 0: Outcome Ledger

- Move notebook scoring logic into a service-level outcome builder.
- Materialize `trading_agent_outcomes` after price history refresh and Trading
  Agent runs.
- Add the notebook as a consumer of the materialized outcome dataset.
- Do not change Trading Agent generation yet.

Exit criteria:

- Every persisted candidate has one outcome row or a clear unavailable state.
- Matured and mark-to-market metrics are reproducible without notebook-only
  code.

### Phase 1: Qualitative Review Loop

- Build review packages from candidate, original evidence, price path, and
  post-call events.
- Run LLM review for matured rows.
- Persist `trading_agent_research_reviews`.
- Manually inspect at least 20 reviews before using the recommendations.

Exit criteria:

- Reviews correctly distinguish thesis quality from price outcome.
- Failure modes are specific enough to drive contract changes.

### Phase 2: Candidate Package V2

- Add `trading_agent_candidate_packages`.
- Convert per-ticker AQL packages into the V2 package schema.
- Require counter-evidence and horizon rationale.
- Keep current final candidate schema as an adapter until UI changes are ready.
- Migrate Trading Agent model calls through the v0.6 AQL/Zopedia gateway before
  using V2 output as a product surface.

Exit criteria:

- Candidate packages are readable and decisive.
- Weak evidence produces no candidate or an explicit watch-only candidate.
- Every candidate package, final synthesis, and review can be traced to gateway
  model-call events by surface and purpose.

### Phase 3: Shadow V2

- Run V1 and V2 side by side.
- Persist `trading_agent_shadow_candidates`.
- Compare V1 and V2 in the notebook.

Exit criteria:

- V2 has better qualitative reviews and at least no early quantitative
  regression before becoming the default.

### Phase 4: Replace V1

- Promote V2 candidate packages to the main Trading Agent output.
- Remove timeout-generated fallback candidate synthesis.
- Keep explicit `unavailable` or `insufficient_evidence` states when required
  horizons cannot be grounded.

Exit criteria:

- Main Trading Agent output is business-first, evidence-backed, reviewable, and
  measurable.

## Open Decisions

1. Should the primary objective be win rate, average signed return, or
   benchmark-relative return?
2. Should `watch` be treated as a directional long call for performance, or as
   a separate non-actionable research state?
3. Should duplicated tickers across horizons be allowed by default?
4. Should `avoid` be scored as a short-equivalent outcome, or as a risk-control
   call with separate metrics?
5. What is the minimum sample size before promoting V2? A practical default is
   at least 100 scored candidates and 30 matured candidates, plus qualitative
   review.

## Near-Term Recommendation

Start with Phase 0 and Phase 1.

Do not change candidate generation yet. The current data says the system may be
better at identifying avoid/risk-control setups than long/watch setups, but the
sample is too small to encode that into behavior. Build the outcome and review
loop first, then let the evidence show which part of the research contract needs
to change.
