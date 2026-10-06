# UI Copy and Broad Economy Resilience

## Scope

- Remove disclaimer, implementation, audit, confidence, and failure prose from normal product surfaces.
- Keep operational diagnostics in Admin, research-session events, model-call telemetry, and job logs.
- Fix the Broad Economy page summary at the shared DeepSeek-compatible adapter and Zopedia/AQL planner boundaries.

## Root Cause

The 2026-09-20 `macro-fred-daily` run loaded 35 FRED summary rows and completed the first Broad Economy research tool call. DeepSeek returned an HTTP-success response with empty content on planner iteration two. The planner only preserved prior evidence for `TimeoutError`, so the generic `LLMAPIError` aborted the run and materialized an unavailable page-summary row. The UI then rendered the stored failure reason as product copy.

## Design

1. DeepSeek-compatible transports retry one response that contains no choices or no usable content. This also applies to the future local chat-completions runtime through the same adapter.
2. If a later planner turn still times out, drops its connection, or returns empty/no-choice content after a real evidence tool completed, planning stops and final synthesis uses the collected evidence.
3. The failure remains visible through gateway telemetry and progress events. It is not appended to public limitations.
4. Unavailable materialized page summaries have empty public narrative fields, and normal product renderers do not display unavailable summary panels.

## Verification

- Focused adapter and recovery regression: 10 passed.
- Zopedia, page-summary, DeepSeek, and pipeline regression: 112 passed.
- Live local replay against `fred_summary__20260920T110032Z__da30af65` and its matching release index returned `status=ok`.
- Reviewed output: the summary identified rising 10Y real yields as the tightening signal and produced concrete rate, credit, mortgage, labor, and inflation checks without exposing runtime failure prose.

## Deployment

- Deployed the verified UI and shared-service code to development. The namespace rollout advanced it to revision `sn-streamlit-ui-dev--0000440`.
- Development image digest: `sha256:b22bdbb6bbd168f7e6ab0a6398f888a5e11b5f02e2d78df67a03d85d5c9b84fd`.
- Verified the development root and Streamlit health endpoint return HTTP 200.
- Verified the Broad Economy page locally through the real Streamlit view: the stale unavailable artifact produces no summary panel and none of the removed failure copy appears.
- Added the manual-only isolated job `macro-fred-daily-dev` after the development dataset namespace became available. It uses `PIPELINE_DATASET_NAMESPACE=development`, the verified `pipeline-jobs:dev-namespace-20260921020436` image, and `PAGE_AGENTIC_SUMMARY_WRITE_POLICY=none` so the validation run cannot mutate shared Zopedia memory.
- Execution `macro-fred-daily-dev-fubq371` succeeded on 2026-09-21. It wrote fresh FRED and page-summary artifacts beneath `namespaces/development/` and materialized Broad Economy with `status=ok`.
- The dev application reader resolved the new summary through its normal surface fallback. The production `page_agentic_summaries` pointer remained on `page_agentic_summaries__20260920T110032Z__da30af65`; no production job or production dataset pointer changed.

## Production Promotion

- Production promotion was explicitly authorized on 2026-10-01 and reused the verified development artifacts instead of rebuilding the dirty shared worktree.
- The UI now runs revision `sn-streamlit-ui--0000085` from digest `sha256:b22bdbb6bbd168f7e6ab0a6398f888a5e11b5f02e2d78df67a03d85d5c9b84fd`. The production dataset namespace remains unset, authentication remains enabled, and both the root and Streamlit health endpoint return HTTP 200.
- `macro-fred-daily` now runs `pipeline-jobs:dev-namespace-20260921020436` on its existing production schedule. Execution `macro-fred-daily-79t3j8i` succeeded and published `page_agentic_summaries__20261002T043246Z__aff7e7fe` with `status=ok` and no public runtime-failure prose.
- The live Broad Economy summary focuses on the rise in long nominal and real rates and gives concrete yield-curve and mortgage-rate checks. It was read qualitatively after persistence, not accepted from job status alone.
