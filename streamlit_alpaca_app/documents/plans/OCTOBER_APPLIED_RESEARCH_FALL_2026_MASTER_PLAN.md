# October Applied Research Fall 2026 Master plan

**Direction approved: October 16, 2026. Execution pending.** This is the canonical applied-research plan for Spectral Nature. [Next-agent handoff](#next-agent-handoff).

Assessment: **2026-10-16**. [Research index](../research/README.md) · [Literature survey](../research/frontier-agentic-research/LITERATURE_SURVEY_2026-10-16.md) · [Prediction-machine scope](../research/frontier-agentic-research/PREDICTION_MACHINE_SCOPE_2026-10-16.md) · [Future research TODOs](../research/frontier-agentic-research/README.md)

**Spectral Nature has useful research infrastructure, but has not demonstrated a SOTA prediction-and-decision system.** The largest gaps are outcome-driven learning, reliable analytical correction, calibrated forecasting, and connecting forecasts to evaluated decisions. A modern-looking agent loop does not settle those questions.

Cheap open-source models are mandatory for runtime, teachers and graders. Primary system comparisons hold the model fixed. Keep current workload knobs unchanged and the combined operating-cost increase within **20%**. [Eligible model alternatives](../research/frontier-agentic-research/OPEN_MODEL_CANDIDATES_2026-10-16.md) have a separate comparison; proprietary-model escalation is excluded.

This assessment uses inspected local source and retained October 15–16 research outputs. The reasoning/cost candidate remains unaccepted and separate from the operational release. No new paid inference, backtest, runtime change or deployment was performed for this update.

## Where we stand

| Area | Where we stand | How to close the gap | Eval / objective — how we would measure improvement |
| --- | --- | --- | --- |
| **Research infrastructure** | Shared engine, tools, originals, hybrid retrieval, model settings, monitor, review and resumable work. **Suitable foundation.** These are credible modern patterns; empirical SOTA parity is unproven. | Preserve the shared spine. Improve evidence use and measured results within it. | **Increase useful completed answers per requested task.** Compare frozen questions using the same model and evidence access; assess usefulness blind, independently check material errors, and report completion, latency and full cost. Include failed attempts in the denominator. |
| **Reasoning and correction** | Some sound numerical/source analyses; repeated omitted feasibility checks, reversed implications, unnecessary review edits and correction timeouts. **Observed quality weakness.** The relative margin against another harness is unknown. | Learn/check the consequential derivation, expose actual tool results, and resolve material criticism against originals. Evaluate sound drafts alongside errors. | **Reduce material reasoning errors; increase net repair.** Independently grade draft, criticism and final answer. Measure wrong-to-useful-correct corrections minus sound-to-materially-worse revisions, alongside missed errors, false objections and correction completion. Include sound controls. |
| **Learning from failures** | Manual prompt/configuration revisions and retained traces/memory; no accepted feedback-trained advisor or prompt-search loop. **Clear missing capability relative to demonstrated methods.** | Compare reviewed reusable examples and feedback-driven shared-prompt optimization, then learned guidance if it adds held-out value. | **Reduce recurrence on unfamiliar cases versus the frozen pre-learning system.** Hold out entities, problem structures and later periods; measure material-error reduction and blind usefulness preference, including previously successful tasks. Report learning expense and amortized operating benefit separately. |
| **Model and effort allocation** | Configurable stage/workload routing and agent-selected tools; no demonstrated allocation learned from comparative quality/cost outcomes. **Clear missing capability relative to demonstrated methods.** | Learn when an eligible model, extra reasoning, read or calculation improves the result enough to pay for it. Include allocation overhead and downstream corrections. | **Improve quality at matched spend, or reduce spend at matched quality and completion.** Compare learned allocation with current fixed routes using the same eligible model pool. Count routing, retries and induced corrections; controlled alternative branches estimate the value of additional work. |
| **Retrieval, relevance and ranking** | Hybrid source retrieval; stock nominations use peer percentiles, domain balancing and rank fusion. **Working baselines; prediction value unproven.** We have not established that every retrieval method is deficient. | Compare complementary evidence retrieval and semantic reranking; learn source/subject priorities from reviewed usefulness and future outcomes. Preserve broad recall and exploration. | **Improve three separate targets:** evidence-aspect coverage and relevance ranking at fixed top-k; independently reviewed useful discoveries at fixed research capacity; future-outcome Rank IC for asset ranking. Compare current retrieval/fusion baselines, audit sampled rejects, and check downstream forecast or answer-quality gains. |
| **Projections and forecasting** | An analogue/resampling function emits numerical paths, intervals and probabilities; agent research supplies forward-looking hypotheses. **Baselines exist; calibrated predictive performance is not demonstrated.** | Define targets and horizons, compare statistical/learned forecasting baselines, add evidence-derived inputs, calibrate and measure strict future performance. | **Lower future-outcome forecast loss:** Brier loss for event probabilities; mean pinball loss across declared quantiles for numerical forecasts. Compare current analogue and simple baselines on identical later targets. Report calibration, interval coverage/width and performance by horizon and regime. |
| **Strategy, sizing and optionality** | The inspected Trading Agent prompt produces research packages. The reviewed records do not demonstrate a validated forecast-to-portfolio decision layer. **Capability/validation not established in the inspected path.** This is broader than an LLM review problem. | Audit existing owners first, then compare explicit action, sizing, hedging and waiting policies using the same forecasts, capital and executable costs. | **Higher net return at matched risk, or lower risk at matched return.** Hold forecasts, capital and executable cost assumptions fixed. Compare simple policies; report drawdown, tail losses and turnover. Measure waiting, staging and hedging by their incremental net decision value across later periods. |
| **Evaluation** | Detailed exploratory cases, personal assessment, mechanical checks and historical-data infrastructure; no accepted representative held-out quality/cost suite. **Clear measurement gap.** We cannot quantify the whole application gap or reliably select improvements. | Establish frozen comparisons, independent labels, temporal holdouts and a prospective cohort; report quality, completion and full cost together. | **Reduce evaluator mistakes against independent labels.** Measure missed material errors and false objections on untouched cases; compare subjective judgments with blind human assessment. Repeat with answer order reversed, audit agreements as well as disagreements, and measure uncertainty in candidate-versus-baseline conclusions. |

Source basis: [reasoning evidence](../verification/llm-flow-2026-10-16/reasoning/README.md), [routing](../../services/llm_flow.py), [all-signal ranking](../../services/all_signal_ranker.py), [ranker design](../architecture/market_data/TRADING_AGENT_ALL_SIGNAL_RANKER_2026-09-21.md), [forecast baseline](../../compute/signals.py), [Trading Agent](../../services/trading_agent.py), and [proposed discovery learner](../architecture/overall/redesigns/DISCOVERY_MODEL_ARCHITECTURE_2026-10-07.json). The historical reader supplies data/labels; it does not establish a trained policy or predictive benefit.

## Objective definitions and comparison rules

The final column specifies proposed evaluation objectives. Select the primary endpoint and practical improvement needed before comparing candidates. Keep research usefulness, source/math correctness, forecast accuracy and decision utility separate; they answer different questions.

- **Research quality:** independently verify calculations and source claims, then personally compare complete answers blind for usefulness and worthwhile connections. Report candidate wins, ties and losses alongside material errors and usable completion. A cautious but empty answer does not count as useful research. Semantic assessment accepts multiple sound answers; it does not match expected prose.
- **Net repair:** `(wrong → useful correct drafts − sound → materially worse drafts) / all reviewed drafts`. Report both transitions and unfinished corrections separately, so the net score cannot conceal damage to sound work. Establish the initial draft's status independently of the serving reviewer.
- **Retrieval and discovery:** fix k and research capacity to their current values. Evidence-aspect recall measures coverage of independently identified relevant aspects; NDCG@k measures whether graded relevant items appear early. Judge a pooled candidate set and sample omitted items; unjudged sources are unknown, not negatives. Track discovery yield and lead time separately. For assets, Rank IC is the per-date Spearman correlation between predicted ranks and the declared future-outcome ranks, aggregated across later dates. Relevance labels and asset-return labels stay distinct. [NDCG definition](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.ndcg_score.html).
- **Forecast loss:** binary Brier loss is `mean((p − y)²)`, where p is the predicted event probability and y is its resolved 0/1 outcome; lower is better. Numerical quantiles use mean pinball loss over a fixed quantile grid. Compare each target/horizon with the same baseline and weights; do not average raw dollar errors across unrelated scales. Report calibration and interval width together with coverage. Future outcomes grade explicit forecasts, not whether a source was represented faithfully or a past causal claim was valid. [Brier definition](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.brier_score_loss.html), [quantile loss definition](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.mean_pinball_loss.html).
- **Decision value:** choose a declared objective, such as net log-wealth growth under stated risk limits, before fitting or selecting a policy. Compare its realized held-out value with simple policies using the same forecasts and constraints; also report return, drawdown and tail losses separately. Waiting and hedging earn credit through their net outcomes, including foregone returns, financing, spreads and fees. An in-sample Sharpe ratio does not settle the comparison.

**A fair comparison:** freeze the baseline, workload mix and knobs; hold the backbone fixed for harness/retrieval/learning experiments. Allocation experiments may choose among the same fixed eligible model pool; compare alternative backbones separately. Use paired tasks and chronological holdouts with available-at-time inputs, mature labels and purged overlaps for financial outcomes. Fit prompts, rankers, calibration and policies on training/development data, then assess on untouched later data and a prospective cohort. Keep final evaluation labels outside the learning loop. The [ground-truth inventory](../research/frontier-agentic-research/GROUND_TRUTH_2026-10-16.md) defines the label sources and temporal constraints.

**What counts as improvement:** a practically useful gain on the declared held-out objective, with uncertainty reported and the relevant trade-offs visible. Estimate uncertainty using paired cases and event/time blocks rather than treating overlapping returns or repeated questions as independent samples. Personally inspect representative full outputs, including regressions. Check the combined system after isolated gains; improvements at adjacent stages may overlap.

**Cost objective:** representative total operating cost must remain `C_candidate ≤ 1.20 × C_baseline`. Weight per-workload costs by actual workload frequencies across Home, Trading, ad hoc and background research; include failures, retries, routing and downstream work. Missing provider charges remain unknown. Eval/teacher/training expense has a separate budget and amortization estimate. Show quality versus cost rather than inventing a universal weighted score or claiming that a reduction in one stage equals workload savings.

## Closure sequence

### 1. Establish measurement while improving reasoning and review

Use the existing frozen inputs and originals, plus representative unfamiliar tasks across ad hoc, queued research, Home, news and Trading. Independently label calculations and source claims; personally assess usefulness, competing explanations and material disagreements. Include correct controls so a stricter critic cannot appear better by objecting to everything.

Compare the present loop with candidates that change one substantive boundary: choosing/formulating a calculation, using its output, reading an original, or resolving criticism. Grade the draft, criticism and final answer separately. Completed continuation and valid JSON are engineering results, not answer-quality labels.

**Evidence to move forward:** unfamiliar tasks show a repeatable improvement in consequential reasoning and useful completed corrections, without material harm to sound answers. The representative workload supplies a trustworthy cost/completion baseline. This can start with existing evidence; it does not need a large annotation platform first.

### 2. Add a learning loop inside the existing architecture

Start with validated examples and offline shared-prompt optimization, using [GEPA](https://arxiv.org/abs/2507.19457v2) as a method baseline. Test learned request-specific guidance only after that comparison, informed by [HSAD](https://arxiv.org/abs/2610.13000v1). Use eligible cheap open models in our experiments; copying a paper's closed teacher is unnecessary.

Build action-value data by forking identical checkpoints into controlled alternatives: read, calculate, search, change effort/model or stop. Compare outcome-informed allocation with the current routes and a strong cheap model choosing its next action; [RSI-Router](https://arxiv.org/abs/2609.34712v1) is a relevant allocation reference. Unchosen actions remain unobserved, and the best sampled branch is an optimistic reference.

**Evidence to move forward:** fewer recurring failures and better held-out quality/cost after counting guidance, optimization, retries and induced work. Reuse existing stages before adding a mandatory serving call. One-off research expense needs its own explicit budget and amortization estimate.

### 3. Improve information selection and rankings

Separate three targets: source relevance, value of investigating a subject, and ranking assets by a declared future financial outcome. A profitable asset is not automatically a good research lead, and a relevant document is not necessarily an informative acquisition.

Compare hybrid retrieval alone with complementary-evidence retrieval and semantic reranking at the same candidate/depth settings. For numeric outcome ranking, compare regularized/boosted supervised models with the current percentile/fusion baseline. The [BRIGHT-Pro study](https://arxiv.org/abs/2605.04018v1) motivates aspect coverage; [LambdaRankIC](https://arxiv.org/abs/2605.00501v1) is a financial-outcome ranking candidate, not a universal relevance objective. Include sampled rejects and independent candidate discovery so selection bias is visible.

**Evidence to move forward:** improved evidence coverage and verified useful discoveries, or stronger declared future-outcome ranking, at unchanged capacity and measured total cost. An impressive retrieval score without downstream benefit does not close the product gap.

### 4. Build and calibrate the forecast layer

Define specific forecast targets, horizons, observation dates and resolution rules. Compare the current analogue method against base-rate/seasonal/persistence forecasts where appropriate, regularized or boosted predictors, and a small open forecasting model such as [Chronos-2](https://arxiv.org/abs/2510.15821v1). Its published general forecasting results make it a comparison candidate, not proven SOTA for our financial targets. Test research-derived signals on top of the same numerical information set.

Use chronological training/selection/evaluation, purged overlapping labels, versioned source availability and future-outcome isolation. Fit any calibration/combination on development data, not the final holdout. Report proper probability/distribution scores, interval calibration and performance by horizon/regime. [FinDeepForecast](https://arxiv.org/abs/2601.05039v1) is a directly relevant research-to-future-outcome benchmark precedent.

**Evidence to move forward:** forecasts improve over simple baselines on untouched later periods and a prospective cohort, including calibration and coverage. Good report writing, simulated price paths and a single successful prediction are different results.

### 5. Evaluate decisions, sizing and optionality

First hold forecasts fixed. Compare simple declared portfolio/action policies with risk-aware optimization under the same capital, concentration, liquidity, turnover, financing and transaction-cost assumptions. Include estimation uncertainty and dependence between positions. For optionality, compare immediate, staged, deferred and hedged choices. Options-contract comparisons require dated executable quotes, expiries and spreads.

[Risk-constrained Kelly](https://www.web.stanford.edu/~boyd/papers/kelly.html) is an established sizing baseline, not a prescribed sizing rule or a claim of current SOTA. [Decision-focused portfolio learning](https://arxiv.org/abs/2601.04062v1) is a candidate after modular baselines: it tests optimizing decisions rather than only prediction error. The reviewed paper is an ETF study, not proof of general dominance.

**Evidence to move forward:** better net decision outcomes and risk behavior under matched forecasts and costs, robust to estimation errors and time periods. Then test the combined research–forecast–decision system. This identifies whether gains come from information, forecasting or allocation.

## How to measure the actual harness gap

Run the **same cheap open model** in our loop and a reproducible reference research harness on common tasks. First match evidence/tool access, inference settings and budgets to study orchestration. Separately compare native complete systems at matched as-of information and budget to include each system's retrieval/acquisition choices. Preserve complete attempts and blind assessment.

For forecasting and portfolio layers, use task-specific temporal baselines and future outcomes rather than a financial-report rubric alone. There is no single leaderboard percentage that measures the whole prediction machine.

## Model selection and cost

[Marked eligible candidates](../research/frontier-agentic-research/OPEN_MODEL_CANDIDATES_2026-10-16.md): Ling 3.0 Flash Fin first, GLM-5.2 second. The survey retains external model-in-harness comparisons; none is our application score. A model comparison and a harness comparison are distinct experiments.

Keep candidate counts, research depth, schedules and output settings fixed initially. Fund stronger checking through reuse, avoided repeated research/correction, compact representations that preserve originals, and cheap learned/numerical components where they help. Do not assume these savings before measurement. Select serving configurations on quality/completion/cost trade-offs with **combined cost at most 1.20× baseline**; do not average per-run percentage changes. No extra reviewer or model call is the default answer to a gap.


## Next-agent handoff

**Start here:** execute the first work packet below, then continue through the closure sequence. The approved direction is to improve the complete prediction system around cheap open-source models. The eight-row table retains current position, work to close each gap and the evaluation objective together.

### First work packet: measurement and reasoning/review repair

1. Read [project philosophy](../philosophy.md), the [ground-truth inventory](../research/frontier-agentic-research/GROUND_TRUTH_2026-10-16.md), the [reasoning failures and sound controls](../research/reasoning-and-review/README.md), and their complete retained inputs, outputs and cost records. The October 15–16 examples are development evidence. Preserve evaluator corrections and successful controls.
2. Record the actual baseline: source revision plus any relevant uncommitted changes, shared-engine path, model/provider, prompts, effective settings, evidence/memory snapshots and workload frequencies. Preserve candidate counts, research depth, schedules, output limits, reviewer count and review-cycle values. Keep the existing one-cycle setting configurable.
3. Prepare a representative workload and a separate diagnostic cohort. Define independent source/math labels, blind usefulness criteria, the primary endpoint, temporal/event splits, sample-size rationale and research-spend estimate before inference. Keep untouched evaluation examples and future labels outside the optimization loop. Use mature future outcomes for forecasts and independent source/calculation checks for reasoning.
4. Compare a focused shared-engine change addressing calculation choice/formulation, use of actual tool results, original-source interpretation or resolution of material review feedback. Preserve complete draft, criticism and final answer. Personally assess full outputs and sound controls; evaluate all attempts, including unfinished corrections.
5. Save the protocol, dataset/split manifest, baseline/candidate identities, complete outputs, independent grades, provider receipts and comparison report under `documents/research/frontier-agentic-research/experiments/<dated-topic>/`. Report paired quality change, both repair/harm transitions, completion, latency and weighted full-workload cost. Keep unpriced attempts explicit.
6. Update this plan and the research experiment register with actual findings, remaining failures and the next decision. Coordinate accepted implementation with the deployment owner using a durable repository handoff and the exact source/evidence identity.

### Work queue

- [ ] Establish the representative quality/completion/cost baseline and independently reviewed labels.
- [ ] Improve reasoning and review on unfamiliar cases, preserving useful hypotheses and sound drafts.
- [ ] Compare learning from failures and allocation against improved prompts/current routes.
- [ ] Improve retrieval, relevance, discovery and asset ranking against their separate objectives.
- [ ] Evaluate and calibrate forecasts on later periods and prospective outcomes.
- [ ] Evaluate strategy, sizing, hedging and waiting with forecasts and execution assumptions held fixed.
- [ ] Assess the combined system, including overlapping savings and induced work, against the overall cost ceiling.

### Boundaries and current acceptance

Use cheap open-source models for serving, offline teachers and graders. Primary harness comparisons hold the backbone fixed; model candidates remain a separate comparison. Reuse the shared AQL/Zopedia path and retained originals. Semantic improvements belong in shared instructions, learned signals or evidence use; code handles execution contracts and objective calculations.

The accepted operating constraint is **at most +20% across the overall weighted workload**. Keep workload knobs unchanged initially. State eval/training spend separately with an amortization estimate. A quoted model price, successful tool call, reviewer approval or passed software test is not evidence of better reasoning or prediction.

The reasoning/cost candidate remains unaccepted; the operational release has separate ownership and evidence. The named master plan records accepted direction and pending work. Release acceptance must refer to completed evaluation and the exact implementation. Historical `/tmp` readiness files do not establish readiness of a later candidate.

## Repository checkpoint — October 16, 2026

The owner explicitly requested committing and pushing **all pending repository changes**, including the existing backend, web, iOS, pipeline, research and deployment records. This checkpoint preserves those workstreams together; their recorded implementation, evaluation and release statuses remain authoritative. No deployment or paid evaluation is part of this repository cleanup.

- Canonical plan: this file. The earlier SOTA-gap path remains a forwarding link; the research/planning indices and supporting research now link here.
- Research handoff: the next agent starts with the first work packet above, then updates the work queue and experiment register with measured results.
- Local cleanup: ignore temporary release copies, private diagnostic JSON and macOS metadata. Keep local working files on disk; remove tracked macOS metadata from Git. Retain the approved mockup, design PDF, research sources and experiment evidence.
- Git housekeeping: removed 156 abandoned, unopened temporary object files from October 14, recovering **39.70 GiB**. Valid Git objects and history were preserved; repository maintenance uses `--prune=never`.
- Publication-history cleanup removed previously committed `web/node_modules/` and `web/.next/` only from the unpublished range after `origin/master`. The source tree and commit metadata/messages were verified unchanged. Original history remains on local branch `archive/pre-october-repo-cleanup-20261016`; the portable [cleanup receipt and commit mapping](../verification/repository-cleanup-2026-10-16/README.md) let another agent resolve the changed unpublished commit IDs. Keep the archive local and push the cleaned branch as a fast-forward of the existing remote history.
- Publication checks: Python source parsing, JSON parsing, shell syntax, source-whitespace checks, credential-pattern review and master-plan link/table checks. Retained raw build logs keep their original whitespace. These establish the integrity of the checkpoint; qualitative model acceptance remains in the research work queue.

Resume from the pushed `master` checkpoint, inspect any newer working-tree changes, and record the exact source/configuration baseline before starting an experiment. The published October 15–16 diagnostics remain development evidence rather than an untouched evaluation set.

**Cleanup policy updated October 16:** archive superseded material before removal; retain archives for 30 days and discard expired archives during routine cleanup. The local original-history archive reaches 30 days on **November 15, 2026**. Current source, canonical plans and retained research evidence remain active material.
