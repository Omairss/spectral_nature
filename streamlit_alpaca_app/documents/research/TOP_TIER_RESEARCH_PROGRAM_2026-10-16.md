# Top tier research with one annotator and Spectral Nature

Date: October 16, 2026. Status: proposed research designs; no new experiments or runtime changes.

[Research index](README.md) · [Existing research agenda](frontier-agentic-research/README.md)

**Owner constraint:** use cheap open-source models, including teachers/graders in these experiments. Optimize the surrounding prediction system; hold the backbone fixed in primary method comparisons. The [full scope](frontier-agentic-research/PREDICTION_MACHINE_SCOPE_2026-10-16.md) and [eligible model shortlist](frontier-agentic-research/OPEN_MODEL_CANDIDATES_2026-10-16.md) govern model selection and cost.

The strongest project is **learning the value of research from future outcomes**. Spectral Nature supplies continuing questions, source acquisition, retained evidence, execution history and costs. Future disclosures supply most correctness labels. The owner's annotation effort defines meaningful tasks and audits semantic mistakes. A second, faster experimental direction is **learning to acquire evidence that distinguishes competing explanations** using executable latent worlds and carefully audited real tasks.

These are new studies to undertake. The October 15–16 failures are development evidence and motivating examples. They do not supply the proposed results. The public application is an execution and publication surface; its existence does not establish a large user cohort or grant access to private user data.

## Project 1 Learning the value of research from future outcomes

Working title: **Learning the Value of Research from Future Outcomes**.

Primary targets: ICML or NeurIPS main conference. A reusable prospective intervention environment could support an evaluation-focused submission if that becomes the central contribution. [ICML's scope](https://icml.cc/Conferences/2026/CallForPapers) includes active and online learning, decision theory and evaluation. NeurIPS currently names its resource track [Evaluations and Datasets](https://nips.cc/Conferences/2026/EvaluationsDatasetsFAQ). These are target venue families, not claims about open submission deadlines.

### Research question and technical contribution

Can an agent learn which investigation to conduct next from the eventual information value of its work, while sharing evidence across many questions and learning from delayed outcomes?

An investigation can be valuable for several questions. Reading a large customer's capital-spending disclosures may improve research on several suppliers. Its value can also depend on another source establishing each supplier's exposure. A per-question difficulty router or an allocator that sums isolated source values can miss those relationships. Ordinary caching reduces repeated work after acquisition; the proposed policy must learn which acquisition is worth making in the first place.

The method to develop is a learned action-value model over the current evidence, question population, candidate research actions and remaining workload budget. It estimates the change in externally scored predictions across affected questions, including source reuse and complementary sources. The semantic planner remains the shared AQL/Zopedia agent. No ticker rules, fixed finance-mechanism classes, separate agent framework or additional mandatory review stage is proposed.

Start with a frozen analyst and a small learned allocation policy. Candidate actions come from the existing planner/tool contracts. Before an acquisition, the policy sees only information already available: retained context, titles, previews and acquisition metadata. It cannot inspect a full source for free and then claim to have predicted its value. All preparation costs count. Compare a single-step value model with a limited two-step lookahead model; interactions earn their complexity only if the observed data shows they matter.

A concrete first learner predicts an action-to-question vector of marginal score changes from pretrained text representations and the current evidence state. Aggregate that vector across the declared questions to value shared acquisition. A separate interaction term, trained on sampled action pairs, estimates gains that isolated actions miss. The zero-interaction version and an allocator using the same reuse but independent question values are essential ablations. A classical information-value identity or a generic greedy selector is not itself a new algorithmic contribution.

### Observable training signal

For a fixed set of future questions, let `Y_q` be the resolved outcome, `p_q` a forecast, `L` a prespecified proper prediction loss, and `w_q` a weight set before outcomes. From a checkpoint, execute a stop branch and a research branch before resolution. Their paired value is:

`G(a) = sum_q w_q [L(p_q_stop, Y_q) - L(p_q_after_a, Y_q)]`.

The policy maximizes expected cumulative improvement subject to a shared resource budget. Loss units and weights must be comparable: use a common binary Brier loss initially, or evaluate continuous outcomes separately with a declared scale. This is a measure of forecast-directed research value. It does not certify the truth of an economic causal narrative or substitute for the owner's assessment of discovery usefulness.

On sampled checkpoints, fork the same input/evidence state into stop, original-source acquisition, a computation or comparison, and selected additional investigation. Use a frozen continuation policy when estimating an action's effect. Run all branches before the outcome becomes known. Once the disclosure arrives, score every branch against the same outcome. Record branch selection probabilities; unexecuted alternatives remain unobserved. A small randomized subset of two-action sequences supplies interaction evidence. Preserve adverse effects as well as gains.

The matched fork uses one captured source/version boundary. Randomize execution order so provider caching and elapsed time do not systematically favor an arm. Full longitudinal policies receive the same recorded arrivals. Extra sampling or reasoning without new evidence is a control: a research benefit should not merely be an unacknowledged ensembling or calibration benefit. Under an ideal Bayesian forecaster, expected log-loss reduction has a classical conditional-information interpretation; for a bounded language-model analyst, additional evidence can worsen predictions. The measured target is the effect on that analyst, not an assumed intrinsic source value.

A separate full-policy evaluation measures long-horizon performance, including memory and all downstream spending. A good isolated action does not establish a good complete policy. Distinguish randomized effects of computational/information interventions from causal effects in the economy.

### Data and the owner's role

Begin with independently sampled scheduled corporate and macro releases whose outcomes can be matched to public structured records. Each contract specifies the measure, entity, fiscal or observation period, units, forecast cutoff, first-publication target and resolution rule. Human review is required for semantic mappings such as GAAP versus adjusted figures and original versus revised releases. The resolver remains independent of the tested agent.

Every policy forecasts the entire declared question pool; its choice is where to spend investigation effort. It cannot improve its score by reporting only easy questions. Tasks unresolved at the analysis cutoff retain a declared pending state; report resolution coverage and delay rather than silently dropping inconvenient outcomes. Questions about one release form a correlated group, not many independent observations.

The owner approves a small initial set of source/metric contracts and inspects random samples plus disputed resolutions. Routine structured outcomes do not require report-by-report human labels. Blinded preferences over a limited sample of research outputs remain a separate measure of usefulness for this one expert. Repeated masked items can measure that expert's consistency; they cannot establish inter-annotator agreement or population preference.

The existing historical corpus supports development, source inspection and replay mechanics. The decisive evaluation is prospective: freeze policy/model versions and issue forecasts before the relevant outcomes exist. Historical evidence cutoffs do not remove later information from pretrained weights. Learn only from outcomes available before each policy update. Keep an untouched final cohort and a transfer cohort drawn from an existing general forecasting resource such as ForecastBench.

### Strong controls and the result required

Compare against a statistical/no-additional-research forecaster, uniform research allocation, uncertainty-based allocation, repeated sampling/reasoning without new evidence, the existing shared agent, a strong LLM choosing the next action, and a per-question value-of-information policy. All acquisition policies receive the same source access, analyst, task population and artifact-sharing mechanism. Otherwise a benefit from source reuse could be misreported as a learning result.

Measure paired prediction loss, calibration, complete cost, latency, resolution coverage and performance over independent event/time groups. Count training branches, source acquisition, failed work and learned-policy overhead separately from serving cost. Report the quality/cost curve and amortization. The application's existing +20% operating allowance does not authorize an unspecified research spend.

The main-conference result to establish is that the learned policy improves prospective outcomes across the whole task population at a matched cost, beyond strong allocation baselines with identical reuse, and that the improvement transfers to an unfamiliar interpreter or task population. Ablations must explain the contributions of delayed external feedback, cross-question effects and source complementarity.

The first pilot estimates whether different research actions have repeatable effects beyond model sampling variation and whether those effects are predictable before acquisition. If strong simple policies exhaust the useful signal, or the apparent gain disappears when cache access is matched, the proposed method has not earned a methods paper. Do not buy a larger experiment before resolving that uncertainty.

### Closest prior work and the precise gap

- [ForecastBench](https://forecastbench.org/docs/index.html), published at ICLR 2025, already provides future-event questions and automated outcomes. Future labels are an evaluation foundation, not the novelty claim.
- [FinDeepForecast](https://arxiv.org/html/2601.05039v1) already generates live corporate and macro forecasting tasks and evaluates research agents. The proposed study must add measured action-level research value and learning under a shared budget, rather than present another live finance leaderboard.
- [Inference-Time Budget Control for LLM Search Agents](https://arxiv.org/html/2605.05701v1) already scores research actions using operational task-level value. Its inspected formulation uses critic-derived progress and structural signals on multi-hop QA. The proposed method learns from later external outcomes and evaluates shared effects across questions.
- [Adaptive submodularity](https://arxiv.org/abs/1003.3967) provides classical guarantees for certain information-gathering objectives. Those guarantees cannot be assumed when evidence is complementary or a bounded analyst is harmed by extra information. Neither value of information nor the observation that sources can complement each other is new by itself.
- [FutureSim](https://arxiv.org/abs/2605.15188) and [ReLiveGym](https://arxiv.org/abs/2610.00710) already explore adaptation and long-lived agents in replayed environments. A longitudinal research claim needs a direct comparison and a prospective contribution beyond replay.

## Project 2 Learning to distinguish explanations with evidence

Working title: **Learning to Acquire Evidence That Distinguishes Explanations**.

Primary targets: ICLR or ACL/EMNLP main conference. The contribution would be a general acquisition and reasoning method with verified supervision and transfer to real analytical questions. A collection of counterexamples alone is insufficient.

### Research question and method

Can a research agent learn to identify the observation that would distinguish materially different explanations, while correctly recognizing conclusions that the current evidence already establishes?

Represent the current evidence as a set of possible worlds consistent with it. For a consequential claim or decision, seek two compatible worlds that imply different conclusions. Their difference identifies what additional observation could matter. The agent chooses whether to obtain it, compute a bound, inspect an original, or retain a useful partially resolved answer. This is a targeted research action; it does not force every product narrative through formal certification.

The LLM proposes the representation, hypotheses and information requests. Numerical/logical tools check proposed constructions and consequences. A learned acquisition policy prioritizes observations that reduce decision ambiguity or regret, rather than merely reducing uncertainty about irrelevant details. With sufficient evidence, it should draw the conclusion. With multiple remaining explanations, it should express the useful distinction and choose a discriminating next investigation.

For example, an aggregate operating improvement may force some within-group improvement while leaving the intervention's causal effect unidentified. A useful agent derives the first result, preserves the second uncertainty and seeks an appropriate comparison. The old logistics case is development evidence only; it must not become a coded problem class or an evaluation target tuned until it passes.

### Why one annotator can support it

Construct interactive environments from executable latent models. The environment generates internally consistent observations, hides selected information and exposes it through tools. Actual latent states and solver-checked consequences supply objective labels, including valid uncertainty and whether an acquisition changes the available decision. LLMs can propose models and natural-language renderings; they are not the final correctness oracle.

The owner audits model-to-language fidelity, task relevance and a held-out collection of real source questions. A proof about a mistranslated model does not prove the real-world conclusion. Report translation errors independently from reasoning and acquisition errors. Use a broad task collection to propose mechanisms, with held-out structures and language styles; do not multiply a few hand-written fixtures into an apparently large independent dataset.

Causal ground truth from a generated world applies to that world. On real observational material, conclusions remain conditional on the supported assumptions. The method should identify what the evidence permits, including useful bounds and discriminating follow-up, without pretending to recover a uniquely true economic mechanism.

Controls include fully informative cases, underdetermined cases, misleading correlations, useful indirect evidence, redundant sources and sound initial answers. Measure valid deductions, unsupported conclusions, unnecessary abstention, acquisition efficiency and retention of useful hypotheses. The final real-task assessment uses the one owner's blinded judgments, with its scope explicit, alongside source/numerical checks.

### The contribution required and the decisive comparison

Compare with the same LLM plus Python, the same LLM plus a symbolic solver, ordinary self-review, uncertainty-driven question asking and hypothesis-driven acquisition. Match budgets and expose the same tools. The result required is a learned policy that acquires more decision-relevant evidence and reaches better conclusions on unfamiliar structures, then transfers to real source-grounded questions. Solver-assisted arithmetic alone does not establish that result.

Useful partial results remain answers: ambiguity about causation should not erase an established numerical bound or a plausible hypothesis. The objective must penalize both unsupported certainty and unnecessary withholding. The acquisition policy cannot see the hidden latent state or the evaluator's formal specification.

The early pilot asks whether contrastive possible worlds lead to better acquisitions than a strong model simply proposing the next useful question. If the gains vanish with solver access or depend on generator language patterns, the new acquisition mechanism is not supported.

Close predecessors include [Logic-LM](https://aclanthology.org/2023.findings-emnlp.248/), which integrates language models and symbolic solvers; [LogicSkills](https://arxiv.org/abs/2602.06533), which evaluates symbolization and countermodel construction; and [Alien Abduction](https://arxiv.org/abs/2608.03388), which studies active evidence gathering and hypothesis revision. The proposed contribution must connect verified competing worlds to learned, decision-relevant acquisition and demonstrate real-task transfer. It cannot claim to invent symbolic reasoning, countermodels or active learning.

## First research decision

Prioritize Project 1 for its fit with the live application and its supply of externally resolved outcomes. Project 2 provides faster experimental feedback and a more controlled route to studying general analytical reasoning. Pursue one central contribution first rather than staffing two programs with the same annotation time.

For Project 1, first inspect a small set of actual upcoming source-defined outcomes and design same-checkpoint branches. For Project 2, first inspect semantically faithful interactive worlds and the strongest solver-equipped acquisition baseline. These pilots retire the main scientific uncertainties before a large build or training run. Select the experiment size from observed variability and independent task groups; a round number of model outputs is not an evidence standard.

Preserve the existing shared engine and its artifact/receipt owners. Experimental state belongs in isolated development/shadow namespaces, with no customer-facing treatment or production publication implied. A public precommitment mechanism needs independent timestamps before resolution; an editable webpage or a self-dated Git commit alone cannot establish chronology. Release complete inspectable inputs, actions, forecasts, source versions and outcomes where the source permits it. The owner controls the public application and any later publication.
