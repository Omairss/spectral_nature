# Zopedia benchmark and prospective forecast study

Updated October23, 2026. DeepSeek V4 Pro with high reasoning is being evaluated on native financial benchmarks and eight prospective Seeking Alpha investigations. Shared numerical computation and charts are in production, with native build13 installed. The complete benchmark program and future outcome evaluation remain unfinished.

## Ground truth

The primary agent personally defines custom targets, verifies actual outcomes from independent source records, and makes the final financial judgment. Another LLM must not create outcome labels or grade the predictions. Published external benchmark references remain separate native comparisons; code calculates their numerical scores. The analyst's internal writer and reviewer are generation steps, not ground truth or the final evaluator.

[Ground truth specification](ground-truth-spec.json) defines each issuer's consolidated revenue measure, fiscal year, price dates, total-return convention, corporate actions and publication vintage. [Forecast scoring](score_forecasts.py) accepts only personally reviewed, source-linked resolutions and calculates quantile loss, interval scores and Brier scores. Future actuals remain pending.

## Production capabilities

The [production release](../../../../verification/quantitative-compute-2026-10-22/production.json) is live on all 18 Python consumers. The shared analysis tool preserves complete inputs within a 128 MiB estimated DataFrame allocation, supports NumPy, pandas, SciPy and scikit-learn, and retains numerical results for synthesis. Calculations receive 120 seconds by default and can request up to 900 seconds within the investigation's remaining budget. Cancellation terminates the numerical process. Production selects DeepSeek V4 Pro with high reasoning and a 900-second investigation budget.

October23 queued-chat verification exposed an initialization-order bug: a fresh API process saved the old300/90-second run/model defaults before loading configured900/240values. The shared numeric getter fix is now deployed to all18Python consumers, with all36configurations preserved. A normal queued continuation persisted900/240, recovered its own timezone/date-format errors, and completed the three requested plots throughOctober22. It also exposed superseded successful chart versions appearing together. Shared publication now uses the explicit chart name to select the latest successful version on new and stored conversation reads. All three final images passed exact-image qualification and were promoted; a fresh production read shows exactly the three corrected charts. See the [chart plan](../../../../plans/ZOPEDIA_RESEARCH_CHARTS_2026-10-23.md).

Local execution reached two million rows and 96 columns in separate probes, including a numerical workload lasting 41 seconds. The live API completed a 100,001-row regression. The [full production model review](../../../../verification/quantitative-compute-2026-10-22/production-model-review.json) includes two successful real-data programs and a complete revenue forecast. Its parameter selection and uncertainty calibration remain weaknesses; execution success is not evidence of future accuracy. Native workbook authoring and an asynchronous service for hours of computation remain open.

## External benchmarks

| Benchmark | Public scope | Actual state |
| --- | --- | --- |
| FinQA | 1,147 test questions | 818 native execution matches, 71.32%; one transport failure remains in the denominator. |
| TAT-QA | 1,663 questions in the released labeled snapshot | 68.97% exact match, 78.17% F1; 12 transport failures remain in the denominator. The separate 1,669-question unlabeled test is not joined to these labels. |
| ConvFinQA | 421 development conversations, 1,490 turns | Original condition: 960 matches, 64.43%. Requested-unit revision: 369 matches, 24.77%, with ambiguous program references and syntax failures retained. |
| FinanceBench | 150 questions over 84 complete original reports | All150 full-report answers completed. All150 final answers personally compared; source and definition adjudication in progress. No final accuracy score yet. |
| FINCH | 172 native workflows | All 354 original assets acquired. Native workbook execution pending. |
| BlueFin | 11 public rows over nine task folders | Native asset acquisition and workbook execution incomplete. |
| EMB | One public SaaS workbook case | Original inputs and references acquired; native execution pending. Scratch and template conditions are one case. |
| ForecastBench | 500 questions in the October 11 cohort | Cohort retained. Its submission deadline has passed; an October 22 run cannot claim an on-time native future-prediction result. |
| FinDeepForecast | 1,370 official released tasks | Full retrospective thinking condition running: eight first32k probe outputs retained, 1,362 remaining attempts launched. Original questions are separate from references; all deadlines were in2025. |
| FinSheet-Bench | Official paper and publisher page | No official downloadable dataset verified. A third-party recreation is excluded. |

The [first-condition scores](native-scores.json) cover 4,300 questions or turns, not 4,300 independent financial investigations. The [second ConvFinQA condition](convfinqa-requested-units-scores.json) is retained alongside its [personal contract review](native-personal-review-convfinqa-requested-units.json). Correct financial prose can still fail the required native program contract. Executable programs can also use the wrong financial quantity. No additional full rerun has been launched.

## Prospective Seeking Alpha forecasts

Eight issuers use 259 complete retained article bodies plus 25 frozen SEC and price datasets: 128,777 fact rows and 17,374 price rows. The corpus was frozen at 2026-10-23 02:04:03 UTC. Source fields exclude historical outcome labels and author rankings; the actor cannot use live web, later prices, evaluator records or serving memory. Figures are retained but are not exposed in this text-only condition. Historical replays using modern weights are development material, not certified historical forecasts.

All eight primary attempts are recorded before the issue deadline. Seven completed; GNK's transport/accounting failure remains its primary record. A separately labeled GNK recovery completed successfully. [Task and issuance receipts](forecast-task-set.json) retain prediction hashes, times, quantiles, probability estimates and condition identity. Issued predictions are not rewritten after review.

The return target is October 23, 2026 close to January 21, 2027 close, including dividends, with the probability of outperforming SPY. The business target is first-reported consolidated fiscal 2027 revenue. These outcomes are not available yet. [Current forecast scoring](forecast-scores.json) therefore reports pending outcomes and the failed primary attempt, rather than invented accuracy.

All eight usable final forecasts now have [personal reviews](forecast-personal-review-summary.json), including the separately labeled GNK recovery. Reviews cover final answers and selected complete programs/stdout; they do not claim every intermediate or article was independently audited. The [HIMS review](forecast-personal-review-HIMS.json) found target/horizon and numerical fidelity failures. [GH](forecast-personal-review-GH.json), [AMD](forecast-personal-review-AMD.json), [HAL](forecast-personal-review-HAL.json) and [MAA](forecast-personal-review-MAA.json) show period, feature, horizon or uncertainty problems. [O](forecast-personal-review-O.json) correctly rejects a poor acquisition-distorted extrapolation, but its replacement assumptions remain weakly validated. [LCID](forecast-personal-review-LCID.json) recovers missing current-quarter data and reproduces its final numbers, while [GNK](forecast-personal-review-GNK.json) has useful baseline checks but inconsistent final tails. Issued forecasts remain unchanged; future accuracy is pending.

## Resume

[Study state](study.json) records conditions, immutable source revisions, protocols and private evidence locations. The private actor and evaluator directories remain separate. Existing output files prevent refetching or replacing completed first attempts. FinanceBench owns the native accounting ledger while it runs; do not start another process against that ledger concurrently.

The conservative reference allowance is $100: $82 for native benchmarks, $13 for prospective investigations and $5 for production qualification. Known usage and maximum exposure for unknown calls both count. At FinanceBench admission, native known usage was $35.2526 and 13 interrupted calls reserved $19.6805, totaling $54.9331. These are conservative reference amounts, not invoices. No GPU was rented or started.

All150 FinanceBench responses completed with no pending provider attempts. Native conservative known usage is$58.4848 plus$19.6805 maximum exposure for13 unknown attempts, totaling$78.1653 against the$82 native allowance. [Personal review](financebench-personal-review.json) now covers all150 complete final answers. Original-source checks found a genuine average-assets-denominator error, alongside publisher tax-sign, EPS-measure, remaining-cost and completed-spin-off mistakes. Quick-ratio and working-capital conventions also differ. Keep native agreement and financial validity distinct.

Next work is to finish FinanceBench source/definition adjudication, improve the shared data and numerical-reporting handoffs identified in the personal reviews, and implement native workbook execution through the shared analysis owner. Keep instructions concise and evaluate changes on new issuers/origins rather than patching issued forecasts. Resolve spreadsheet execution and distribution constraints before claiming FINCH, BlueFin or EMB completion. Keep the original prospective forecasts frozen until their actual outcomes mature.

The [FinDeepForecast format comparison](findeepforecast-probe-32k-personal-review.json) personally reviews all eight original prompts and public outputs:6/8complete at8k and8/8at32k. This is format/execution acceptance, with no reference accuracy claim. The [full condition](findeepforecast-full-manifest.json) retains the first eight32k attempts and runs all1,362remaining IDs through the same frozen gateway, prompt and model. Its new$35conservative reference cap extends the prior self-declared$100study allowance to$135; no owner-imposed dollar limit or credit purchase is inferred. The probe-based point estimate is$29.24for1,370cases, with budget admission enforced. Modern weights may know2025outcomes, and this model-only condition supplies no contemporaneous source reports. It cannot certify uncontaminated forward prediction. No external submission or LLM judge is used.
