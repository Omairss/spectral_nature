# Reasoning and review failures

[Master research index](../README.md) · [Hierarchy cases](../hierarchical-summarization/FAILURE_CASES_2026-10-15.md)

Status, October 16, 2026: the separate shared reasoning/cost candidate remains **unaccepted and unreleased**. These cases extend the context research beyond compression: some errors recur with originals, analytical tools and a reviewer available. They were not deployed with the operational reliability release.

Read the complete retained [October 15 review](../../verification/llm-flow-2026-10-15/quality-cost/implementation/README.md) and [October 16 review](../../verification/llm-flow-2026-10-16/reasoning/README.md), with [inputs](../../verification/llm-flow-2026-10-16/reasoning/case-inputs.json), [outputs](../../verification/llm-flow-2026-10-16/reasoning/case-outputs.json) and [attempt accounting](../../verification/llm-flow-2026-10-16/reasoning/attempt-costs.json). Those reviews preserve the changing source/configuration identities and exploratory scope.

## Cases and useful controls

| Case | Observation | Open question |
| --- | --- | --- |
| Source-condition direction | Analysis/review sometimes converted a sufficient no-inference close condition into an exhaustive diagnosis. Some later runs corrected it, others still overread the observation. | How can the shared analysis/review preserve the direction and scope of a source condition without task-specific rules? |
| Delivery-mix feasibility | Answers claimed a standard/express mix change could explain 88%→94% on-time performance under fixed class rates. With weights 70/30 then 50/50, solving those totals gives rates 79% and 109%, exposing impossibility. Some runs calculated this correctly; others omitted the check. | When does the analyst choose the consequential calculation rather than a plausible qualitative explanation? |
| Numerical output handoff | A successful Python call initially reached the next stage only as stdout statistics. Later handoff included actual bounded output and the analyst used it. | Is useful analytical evidence preserved across every stage, including optional context views and correction? |
| Necessary correction did not finish | Review caught the missing delivery-mix calculation, but correction exceeded the unchanged 90-second step deadline. | Can completed review and paid analysis support a useful timely correction? Persistence success alone does not establish this. |
| Sound patient-mix explanation over-edited | Rates 88%/98% reproduced the supplied aggregate 90%/94%. The analyst used the tool result, but review requested an unnecessary aggregation qualification, triggering another correction that timed out. | Can review protect a sound draft and improve material errors without buying avoidable rewrites? |
| Policy interpretation transfer | Review caught registration being treated as approval while preserving the ownership/submission assumptions. | Does the improvement transfer across unfamiliar policy and source-implication questions? |
| Useful exploratory inference | Role probes retained uncertainty in a randomized-trial estimate and a tentative business connection. | Can stronger checking preserve useful hypotheses rather than flattening research into only directly quoted facts? |

For the delivery example, maximizing the second total subject to the first and valid class rates gives **91.4286%**, so fixed-rate mix changes explain at most **3.4286 percentage points** of the six-point improvement. The remaining **2.5714 points** require within-class changes or a change in what was measured. This does not identify the new routing model as the cause. These are human checks of the experiment, not production formulas keyed to a question.

## Evaluation and protocol lessons

An earlier logistics harness supplied a dictionary envelope where a list of source objects was required, silently losing evidence. That result could not establish source transport; the corrected run used the actual contract. The evaluator also initially treated the observed 75-second client setting as an immutable user requirement. That judgment was corrected: a longer setting can be reasonable when its objective and assumptions are explained. Keep evaluator mistakes in the record alongside model mistakes.

The experiments show that more expensive analysis and an available reviewer do not by themselves establish a reliable repair. Later runs improved some original errors but also introduced unnecessary changes and timeouts. Compare complete baseline/candidate flows on frozen unfamiliar cases, retain sound-draft controls and personally assess useful reasoning. Count all paid work and unknown failure charges. The overall +20% application-cost condition remains unverified; small-query prices are not a workload-wide result.
