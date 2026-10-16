# Retained hierarchy experiment evidence

[Research brief](../../README.md) · [Case catalog](../../FAILURE_CASES_2026-10-15.md) · [Original audit](../../../../architecture/AQL/audits/HIERARCHICAL_SUMMARIZATION_EXPERIMENTS_2026-10-15.md)

This review package was copied from the existing experiment archive on October 16. No new inference was run. It keeps the final comparison inspectable without relying solely on a temporary directory.

| File | Contents |
| --- | --- |
| [answers.json](answers.json) | All 38 complete final-batch answers, exact questions, variants, original/context read traces, elapsed times and per-answer accounting. The `record` field matches the filenames used in the case catalog. |
| [inputs.json](inputs.json) | Nine retained original-source objects grouped into six corpora, plus eleven generated summaries/overviews from the frozen snapshot. Source captures are historical inputs, not a claim about current external documentation. |
| [accounting.json](accounting.json) | Unchanged original accounting export: final-batch costs, retained summary construction and the whole investigation, including exploratory work and one unpriced attempt. |
| [source-manifest.json](source-manifest.json) | The frozen 159-file engine source manifest and digest. |
| [experiment-harness.py](experiment-harness.py) | Unchanged experiment harness for inspection of initial views, optional guidance, tool contracts and call settings. It retains its original local paths and is not a standalone replay command. |
| [manifest.json](manifest.json) | File hashes/counts for the exported evidence, and hashes of the original records represented in the other files. |

`flat` means full-source control; `hierarchy` means the basic navigable summary view; `guided_hierarchy` adds initial navigation guidance; `adaptive` is the catalog-first, planner-visible guidance variant. The final export contains 14, 14, 4 and 6 answers respectively. It includes both useful and unsuccessful reasoning, with no selection based on reviewer preference.

The full original records, provider-event ledger, exploratory/rejected-protocol outputs and frozen runtime remain in the repository-local `tmp/hierarchy-experiments-20261015/` archive and the original `/tmp/sn-reliability-20261015/` work area. This compact package preserves complete final answers and the evidence needed to review their claims; it does not replace the full runtime or individual HTTP receipts. Original-record hashes allow comparison with that archive. No credentials, deployment environment dump or hidden model reasoning was copied.

Known estimated investigation cost is **$0.375085866**, plus one interrupted attempt whose usage/charge is unknown. The final 38-answer batch is **$0.1342875**; eleven retained summary-build calls are **$0.008738826**. Construction is reused and provider caching affects the observed prices. These figures are experimental accounting, not a production per-answer price or an accepted workload saving.
