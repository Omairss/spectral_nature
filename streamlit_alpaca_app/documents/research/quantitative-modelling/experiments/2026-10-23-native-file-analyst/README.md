# Native file analysis and external workflows

Updated October 24, 2026. The public workflow evaluation through Zopedia's shared agent and new file-analysis owner is **stopped after a filesystem-space error**. Its scope is 172 FINCH tasks, 11 BlueFin questions/workflows and one EMB case. All three predetermined third-pilot attempts are retained; 181 new cases were declared in benchmark round-robin order. This is a controlled supplied-assets condition, without live web or evaluator access. A task needing unavailable external information retains that limitation.

The [October 24 audit](../2026-10-22-benchmark-and-forecast/status-2026-10-24.json) finds 133 new first-attempt receipts: 112 completed responses and 21 failures. Including the three carried pilot responses, 136/184 attempts are recorded and 115 responses completed. Failures comprise 11 synthesis phase timeouts, four provider/request deadlines and six output-limit failures. Another 48 cases lack final receipts; some may still have incurred provider work. The log ends with ENOSPC while creating a final receipt. No invalid final JSON was found at audit. Full financial grading is pending.

The batch occupies about 747 MiB; at the later audit the host had about 77 GiB free and the container disk about 18 GiB free. Those later measurements do not establish what exhausted capacity earlier. Add storage admission and failure-safe result persistence, reconcile receipt-less work and preserve the original condition before resuming. The idle local VM is now stopped, with shutdown verified.

[The full protocol](protocol-full-01.json) pins source, model, runtime image, actor hashes and all selected IDs. [Preparation](prepare_public.py) admits 211 original input files, excludes reference workbooks, rubrics, expected answers and cell/state checks, and treats EMB scratch/template as one case. BlueFin's original interrogation JSON mixes questions and answers; only its question text and IDs enter the actor package. All 42 BlueFin assets and 11 EMB assets are retained separately with download hashes. Existing FINCH acquisition retains all 354 assets.

The source branch is `experiment/native-file-analysis-20261023`, at `eafdc56bdb1cf19c62f3e1ef0d3356ebf2b9bd47`. Its shared engine revision is `bfd65099722adab4d7e1`. [The execution design](../../../../plans/ZOPEDIA_NATIVE_FILE_ANALYSIS_2026-10-23.md) separates declared inputs, the container workspace, checkpoints and exported files. A timeout destroys all container processes; the next call restores the last successful working state. Native UNO editing and recalculation disable macros and external updates. Only the runtime file and declared inputs/checkpoint enter the nonroot container; no application tree, provider key, Docker socket or evaluator directory is mounted.

## Cost choice

The owner's standing instruction is to estimate Lambda GPU versus DeepSeek cost and use the cheaper suitable option. [The comparison](compute-cost-comparison.json) records current official prices, observed cache/reasoning usage, startup and throughput assumptions, and break-even sensitivities. The new 181 tasks project about **$46–92** with observed caching and off-peak/peak API rates. The conservative all-cache-miss peak reference cap is $200; that cap is not the expected bill. Three format-selected pilot cases are a weak cost sample, so actual receipts and task complexity remain visible.

DeepSeek remains the lower expected-cost choice for this two-investigation interactive workload. The GPU scenarios that could overlap API cost require very high unmeasured sustained batching and omit prefill/tool-idle costs. They do not establish GPU savings. No GPU was allocated. Re-estimate for larger saturated workloads, measured throughput or an existing warm qualified endpoint; do not assume the API is always cheaper.

## Personal pilot review

The primary agent inspects financial results and native files; another LLM does not assign outcome labels or the final evaluation judgment. The shared analyst's generation/revision calls remain generation steps. These exposed development cases do not estimate held-out accuracy.

| Condition | Actual outcome |
| --- | --- |
| [Pilot 01](protocol-pilot-01.json) | Three responses but zero file executions. Source filtering hid the file tool and source-free discovery. All first attempts retained. |
| [Pilot 02](protocol-pilot-02.json) | Actual file execution. Case 0 calculated useful controls but openpyxl authoring removed all 12 original shapes. Case 6 timed out during a recursive filesystem search and could not recover the terminated workspace. Case 22 modified pivot-display values; a complete financial review of that older output remains pending. |
| [Pilot 03](protocol-pilot-03.json) | All three produced native files after shared capability, native-editing and checkpoint changes. Detailed results are below; successful execution is not universal preservation acceptance. |

Case 0's Balance Sheet controls calculate zero in all four columns. The equity roll-forward flags a genuine total-column discrepancy of -333, while program columns reconcile. An independent source/output comparison finds no removed or changed original formulas and no changed original literal values; all 12 shapes survive. LibreOffice still changes merges on two original sheets, so complete formatting preservation is not accepted.

Case 6 produces a report, workbook and two legible PNG charts. Independent calculations on all 53 annual observations reproduce the 8.61% average GNI growth, 1974 maximum of 24.15%, 1991 minimum of -3.40%, contemporaneous correlation -0.16948 and lagged correlation -0.05396. Both charts were visually inspected. The claim that these weak correlations establish countercyclical institutional behavior is too strong; the descriptive numerical work is useful.

Case 22 changes eight pivot-display/total cells. The primary agent independently sums the relevant five source records to -16,000 and all nominal volumes to 3,340,795, matching the corrected cells and total. Only `xl/worksheets/sheet2.xml` changes; every other original ZIP part is byte-identical, with original sheets, formulas and pivot definition retained. A separate Excel refresh was not performed.

An actual timeout qualification also showed that an uncommitted overwrite was discarded, the preceding working file and native workbook restored, and the input hash unchanged. The restarted workbook retained its calculated control and all 12 original shapes. No model calls were used for that execution-boundary check.

## Resume and production

Private execution is under `~/Library/Application Support/SpectralNature/research/2026-10-22-analyst-evaluation/native-workflows/`. The full run's `execution.json` retains historical PID 97859; that process has exited. Logs, per-case receipts, code, results, checkpoints and accounting retain actual progress. [The versioned runner](run_v2.py) skips retained first attempts and stops on provider or spending failures, but its final receipt write was not protected against a full filesystem. Preserve the runner and condition; correct persistence in a declared continuation. Original pilots remain unchanged. FinDeepForecast's separate continuation also stopped before new generation because the provider account is unavailable.

The file executor is an experimental local transport, not a production download feature. Hosted isolation, authenticated user/run-owned artifact delivery, client integration and source reconciliation remain open. Existing production numerical modeling and charts remain deployed. Preserve issued prospective forecasts until real outcomes mature; the [parent study](../2026-10-22-benchmark-and-forecast/README.md) tracks those targets separately.
