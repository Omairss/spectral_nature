# Research index

This is the master directory for open research questions, observed failure cases and experiment records. Start here before repeating an investigation. Implementation plans remain in [plans](../plans/README.md); release receipts remain in `documents/verification/`.

**Non-negotiable: cheap open-source models.** Improve the complete system around them: harness, research loops, retrieval, ranking/sorting/relevance, projections, forecasting, strategy, sizing and optionality. [Prediction-machine scope and workstreams](frontier-agentic-research/PREDICTION_MACHINE_SCOPE_2026-10-16.md) · [Marked cheap open-source model candidates](frontier-agentic-research/OPEN_MODEL_CANDIDATES_2026-10-16.md). Learning from failures, model allocation, reasoning/correction and evaluation remain priorities. Primary system comparisons hold the model fixed.

[October Applied Research Fall 2026 Master plan](../plans/OCTOBER_APPLIED_RESEARCH_FALL_2026_MASTER_PLAN.md): present state by layer, concrete missing capabilities, the work to close each gap, and an **eval / objective** column defining how to measure improvement. Includes metric definitions, baselines, temporal holdouts and the overall +20% operating-cost ceiling, followed by the closure sequence. Assess improvements under the cheap-open-model constraint; the research programs below describe future work.

[Top tier research with one annotator](TOP_TIER_RESEARCH_PROGRAM_2026-10-16.md): two new methods projects using future outcomes or executable latent worlds for most supervision. The leading proposal learns the value of shared research from externally resolved outcomes, targeting ICML/NeurIPS main-conference work. The alternative learns acquisitions that distinguish competing explanations, targeting ICLR/ACL/EMNLP main-conference work.

[Earlier publication assessment and venue fit](../architecture/overall/audits/PUBLICATION_PROSPECTS_2026-10-16.md#venue-fit-and-acceptance-readiness) assesses the existing small evidence-handoff pilot. Its workshop recommendation applies to that pilot, not the new research program. The broad claim that sufficient evidence can still yield wrong answers has established prior work.

## Topics

| Topic | Question and current state | Start here |
| --- | --- | --- |
| Top tier research with one annotator | Can the agent learn which research actions improve future outcomes, or which observations distinguish competing explanations? New study designs with external/programmatic supervision and limited expert audit. | [Research program and decisive experiments](TOP_TIER_RESEARCH_PROGRAM_2026-10-16.md) |
| Frontier agentic research, ground truth and learning | How should research decisions be evaluated and improved against current literature while keeping the clean flow and overall cost increase within 20%? Research TODOs 2–6, every recorded hierarchy/reasoning case, temporal labels and a dated SOTA assessment; no new evals or runtime changes. | [Research TODOs](frontier-agentic-research/README.md), [ground truth](frontier-agentic-research/GROUND_TRUTH_2026-10-16.md), [literature survey](frontier-agentic-research/LITERATURE_SURVEY_2026-10-16.md) |
| Hierarchical summarization and agentic context | Can optional, reusable summaries lower complete-workflow cost while preserving useful reasoning and effective access to originals? The cheap prototype remains off; its failures do not settle the broader research question. | [Research brief](hierarchical-summarization/README.md), [case catalog](hierarchical-summarization/FAILURE_CASES_2026-10-15.md), [retained evidence](hierarchical-summarization/evidence/2026-10-15/README.md) |
| Reasoning and review | Can analysis use numerical tools and source conditions correctly, and can review improve a sound draft without creating unnecessary corrections? The separate reasoning candidate remains unaccepted. | [Failure cases and controls](reasoning-and-review/README.md) |
| Operational reliability | Why did component repairs fail to restore production service? The October 16 promotion remained degraded. These engineering failures are recorded separately from model-quality findings. | [Production failure analysis](system-reliability/PRODUCTION_FAILURES_2026-10-16.md), [retained incident evidence](system-reliability/evidence/2026-10-16.json) |

## Experiment register

| Date | Investigation | Evidence and interpretation |
| --- | --- | --- |
| 2026-10-15 | Cheap hierarchy comparison | [Original audit](../architecture/AQL/audits/HIERARCHICAL_SUMMARIZATION_EXPERIMENTS_2026-10-15.md). Fourteen matched pairs, four guidance variants and six catalog-first variants: 38 completed answers. Known whole-investigation estimate $0.37509 plus one unpriced attempt. Repeated consequential omissions; useful controls and improvements retained. |
| 2026-10-15 | Shared reasoning/cost implementation | [Complete review](../verification/llm-flow-2026-10-15/quality-cost/implementation/README.md). Source-condition and unfamiliar delivery-mix failures survived analysis/review. Implementation remained local. |
| 2026-10-16 | Reasoning ownership, computation and review continuation | [Complete review](../verification/llm-flow-2026-10-16/reasoning/README.md). Some correct analyses, inconsistent numerical checking, unnecessary review edits and correction timeouts. These are exploratory outputs, not a matched general benchmark. |
| 2026-10-16 | Combined operational release | [Production receipt](../verification/system-reliability-production-2026-10-16/README.md). Images/configuration verified; database saturation, intermittent Home 503s, failed research and an over-deadline scheduled monitor remained. |

## How to extend this directory

Record the question, source/model/configuration identity, actual inputs, complete outputs, observable tool behavior and complete cost, including failures and unknown charges. Distinguish an observation from a proposed cause. Keep control successes, counterexamples and invalid-protocol runs visible, with their proper scope.

For LLM work, compare frozen baseline/candidate runs and unfamiliar cases through the shared engine, then personally assess reasoning and usefulness. Revisit the complete answers after original evidence is revealed. A fixture matching one preferred wording, a reviewer verdict or execution completion is not quality acceptance. Some failed steps are acceptable when useful overall analysis survives; the important distinction is whether a failure changes the answer materially.

Add new topics to this index and link the active plan back here. Preserve historical results when a later attempt improves them; do not quietly replace an unsuccessful experiment with its best rerun.
