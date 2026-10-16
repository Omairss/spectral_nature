# Cheap open-source model candidates

As of **2026-10-16**. [Prediction-machine scope](PREDICTION_MACHINE_SCOPE_2026-10-16.md) · [Current gap](../../plans/OCTOBER_APPLIED_RESEARCH_FALL_2026_MASTER_PLAN.md)

The owner's constraint is **cheap open-source models**. Improve the surrounding system first. Mark eligible alternatives when they offer better quality/cost; do not introduce closed-model escalation, including for offline teachers or evaluation. Published closed-model results remain external reference points.

## Candidates to mark

The following scores and per-query costs come from the **same Finance Agent v2 harness** on the [official FrontierFinance September 28 leaderboard](https://samaya.ai/benchmarks/frontier-finance), rechecked October 16. They are external task results, not Spectral Nature measurements or current API quotes.

| Model | Score | Published model cost/query | Difference from Pro | Status |
| --- | ---: | ---: | --- | --- |
| DeepSeek V4 Pro | 40.5% | $0.68 | Current analytical reference | Retain as baseline. |
| **Ling 3.0 Flash Fin** | **46.6%** | **$0.05** | **+6.1 percentage points; 92.6% lower published run cost** | **First candidate to evaluate.** |
| **GLM-5.2** | **42.8%** | **$0.63** | **+2.3 points; 7.4% lower published run cost** | **Second candidate to evaluate.** |

Ling closes 6.1 of the 15.4 points between Pro and the strongest listed model in that harness—about **40% of that external score gap**. This is a useful selection signal, not a prediction that replacing Pro improves our application by six points or cuts its bill by 93%.

Openness was checked against the publishers' repositories, including the presence of weight files:

- **[Ling 3.0 Flash Fin](https://huggingface.co/inclusionAI/Ling-3.0-flash-Fin):** publisher model metadata declares MIT; actual Safetensors weight shards are present. Inspected revision: `a5b4afb48f526175991724472c179e5a065604de`.
- **[GLM-5.2](https://huggingface.co/zai-org/GLM-5.2):** MIT license and actual weight shards verified. Inspected revision: `cf457fa734ab149ffef225f80893eb38c6ff5cdc`.

The current [Novita price list](https://novita.ai/pricing) lists Ling Fin at **$0.075/million input tokens**, **$0.22/million output**, and **$0.015/million cached input**. GLM-5.2 is listed at $1.40/$4.40/$0.26 respectively. These explain why token prices and complete task costs must both be compared: a model can use fewer tokens/calls yet have a higher output-token rate. Do not use temporary free access in the economic baseline.

The OpenRouter public endpoint-inventory request returned an empty endpoint list during this check despite its model page listing providers. That route's serving availability remains unverified. No authenticated inference was sent. Hosted access to an open model can satisfy the model constraint; owning GPUs is not assumed to be cheaper.

## Other models

Kimi K3 and GLM-5.3 have published weights but custom licenses. They are **open-weight watchlist entries**, not silently classified as MIT/open-source replacements. Their model cards and license files were inspected; they are not the primary candidates under the stated constraint. The [model-source receipt](evidence/open-model-candidates-2026-10-16.json) records the checked revisions and weight inventories.

## Comparison before any switch

Use the same frozen tasks, originals, memory, tools, review policy and workload limits. Compare each candidate on analytical reasoning, source use, tool/structured-output compatibility, correction quality, completion and total cost. Include unfamiliar cases, sound-draft controls and complete Home/Trading/news/ad hoc workload weights. Keep retries, repairs and failed attempts in the cost denominator.

The main harness experiments should hold the model fixed. Run a separate model comparison, then check interactions only where useful. This lets us distinguish a better model choice from a better prediction system. No route, provider or runtime setting was changed by this shortlist.
