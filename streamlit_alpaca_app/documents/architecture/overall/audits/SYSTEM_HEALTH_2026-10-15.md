# System health investigation — October 15, 2026

Captured approximately 19:44–19:51 PDT, October 15 / 02:44–02:51 UTC, October 16. Scope: preceding 24 hours of Azure console/system logs, execution history for all 27 jobs, eight applications, pipeline PostgreSQL, metrics for both database servers, current Blob manifests and retained output. Execution lists have service retention limits; logs independently cover the requested window.

**The application is available, but research remains degraded.** Recent failed executions recovered. Persistent problems remain in oversized model inputs, partial Trading Agent output, database connection capacity and incomplete operational records. Zero open incidents is not a complete health declaration.

## Current state

| Area | Observation |
|---|---|
| Applications | All eight apps are Running, provisioned, with matching latest/latest-ready revisions. Production root, health and Home, development health/Home, and legacy production API health return HTTP 200. The legacy API probe took 29.8 seconds; its cause was not established. |
| Production Home | Public run `2bb1d253-16bd-49b3-bacc-cbb2f00fe1ca`, as of October 15 13:50 PDT / 20:50 UTC, has eight story groups and **zero daily datapoint charts**. Its research-health artifact is `degraded`: ticker answer review is partial. All twelve ticker enrichments, four source-event investigations and five page summaries completed. |
| Trading Agent | Latest 13:35 PDT / 20:35 UTC execution completed with sixteen candidates and **four successful horizons out of five**. The three-month horizon has zero candidates and `LLMDeadlineExceeded`. |
| News | Latest 13:05 PDT / 20:05 UTC run completed at 14:48:56 PDT. Twelve business resolutions: ten `resolved`, two `cold_start_prepared`. |
| Interactive Zopedia | All five production research sessions started in the window are `completed`, including the 15:32 PDT submission. This investigation did not newly evaluate answer quality. |
| Numerical ingestion | Latest universe, equities, macro, commodities, options and fundamentals executions succeeded. Price history is as of 13:35 PDT with 1,470,960 rows; latest news has fifty rows. Monthly taxonomy and baseline jobs succeeded October 1, consistent with cadence. |
| Brokerage ingestion | All twenty-four listed production hourly sync executions in the window succeeded. |
| Monitoring | Captured 19:30 PDT console result has `open=0`; 19:45 execution subsequently succeeded. Incident query has no open incidents. The monitor itself failed at 14:15 PDT during connection exhaustion, then recovered. |
| Development | Home is available with five charts, but its snapshot is October 13 22:55 PDT / October 14 05:55 UTC. Development producers are manual jobs; availability does not imply current market data. |

## Confirmed failures

### Shared model-input growth

All four Home runs failed `daily_market_datapoints.direct` with HTTP 400. Provider-reported inputs were **2,156,335; 2,066,593; 1,842,193; and 1,803,428 tokens** against a **1,048,576-token** context limit. Production Home confirms the consequence: zero daily charts. The last database-registered chart dataset is from October 14 09:50 PDT / 16:50 UTC.

Zopedia learning's critic was rejected four times with approximately **3.54 million and 5.29 million tokens**. Both learning jobs nevertheless exited successfully. Each reports four events, zero verified replays and three regressed replays. These summaries do not establish successful learned improvements.

Source ownership: `services/daily_market_datapoints.py::select_daily_market_datapoints` assembles chart-selection context, while `pipeline/jobs/attention_home_build.py` catches its failure and continues publication. `services/zopedia_learning.py::critique_learning_event` includes thread messages and retained run payloads, catches critic failures, and returns a fixed fallback critique. Both enter the shared structured engine. The exact input fields responsible for expansion remain unprofiled; ongoing local changes must not be assumed byte-identical to deployed source.

Repair the shared evidence/context handoff and actual input accounting. Preserve complete evidence access and research breadth. Persist internal failures and expose partial output through monitoring while retaining useful Home stories; do not mask failures with invented fallback analysis.

### Pipeline PostgreSQL connection exhaustion

From **13:40:37 through 14:16:31 PDT / 20:40:37–21:16:31 UTC**, eleven captured warnings report `remaining connection slots are reserved for roles with the SUPERUSER attribute`. Affected consumers include the evidence index, options, Home's database sink and the health monitor.

Options' first replica failed, then its automatic retry succeeded. Evidence indexing has failed executions at 13:40 and 14:15 PDT, plus failed replicas within otherwise successful executions. Both 14:15 monitor replicas failed because no database connection was available.

Pipeline PostgreSQL is `Standard_B1ms`, with fifty maximum connections and ten superuser-reserved connections. Azure recorded a peak of forty-seven active connections; refusal messages independently establish exhaustion. Individual connection owners at the peak were not captured.

At approximately 19:46 PDT it had recovered: CPU average 19.3%, memory 57.9%, storage 18.2%, active connections average nine. The separate control database was Ready, with 6.3% CPU, 55.6% memory, seven connections and no failed-connection samples. Storage capacity is not the current constraint.

An earlier evidence-index failure recorded `the database system is shutting down` at 01:20 PDT / 08:20 UTC. Its initiating operation was not established. No database configuration was changed by this investigation.

Repair connection ownership, bound simultaneous database use and size capacity for overlapping jobs. Measure peak callers before selecting pooling or capacity changes; preserve independent tracking and the control database boundary.

### Trading Agent memory failures

Executions `trading-agent-build-29867915` and `trading-agent-build-29868155`, scheduled at **07:35 and 11:35 PDT**, failed after automatic retries. Azure reports exit **137**, reason `ProcessExited`.

Time-aligned memory metrics reached **2.0402 GiB** and **2.1065 GiB** against a **2 GiB** allocation. Each attempt's final progress is `build_research_loop`, building outcomes for 171–207 candidate experiments after research packages, horizon rows and candidates were written. This strongly supports memory exhaustion during outcome generation. No captured kernel OOM record identifies the precise allocation.

The later 13:35 execution completed with about **1.3963 GiB** peak usage, but its three-month horizon still failed on a model deadline. Profile shared outcome/history loading and memory lifecycle, and retain completed research across retries. A later successful run does not resolve the source issue.

### News narrative failure and retry deadline

`news-ingest-and-features-29868125` started at **11:05 PDT / 18:05 UTC**. Its first attempt failed around **12:42 PDT**: `News research completed with missing required narratives: {'news interpretations': 1}`.

An automatic retry began around 12:42 PDT, overlapping the next scheduled 13:05 run. The old execution exceeded its **10,800-second / three-hour** deadline at 14:05 PDT / 21:05 UTC. Its later database reconciliation timestamp is not its original failure time. The next scheduled execution succeeded.

Retained per-result continuation and explicit partial state should prevent replaying completed acquisition/research because one interpretation failed.

## Other findings and monitoring gaps

- Model-call receipts record **78 deadline failures, eight API errors and two JSON parse failures**. Provider-attempt receipts are a separate accounting layer: production DeepSeek has 2,777 successful attempts, 67 transport failures and nine HTTP 400 failures. Do not combine these counts as unique failed research runs. No production 402/429/503 attempt rows appear in this capture window; the earlier promotion-time 503 diagnosis is not today's dominant failure.
- Production frontend logs contain **147 malformed Server Action ID errors**, with values including `0`, `1`, `x` and `action`, plus accompanying documentation lines, from 07:23–13:07 PDT. Caller attribution and exploitability were not established; these messages do not demonstrate a current outage.
- Brief development autoscaler metric lookup failures occurred around 08:08 PDT. Latest application revisions are ready.
- **Blob/database metadata diverged after final Home publication.** Home, opportunity feed, page summaries and research-health manifests point to the 13:50 run; latest PostgreSQL rows point to the 11:50 run. Home logged a database connection refusal at startup. The public reader serves the newer Blob version; database-only operational readers miss the execution. Metadata recovery needs durable artifact identity and idempotent reconciliation.
- **210 historical `Running` rows lack Azure execution IDs**: 182 news, 23 production Home, four learning and one development Home. Their newest start is September 25. These are stale records, not 210 active jobs; establish execution identity before assigning terminal outcomes.
- Current monitoring catches failed jobs, stale Home and zero successful Trading Agent horizons. It misses a single failed horizon, absent daily charts, partial review, swallowed critic failures and executions whose database sink never opened. Monitor these declared output contracts without suppressing useful research.

## Repair order

The [repair proposal](../../../plans/SYSTEM_RELIABILITY_REPAIR_2026-10-15.md) develops the shared architecture, tradeoffs and acceptance criteria below. It is proposed work, not an implemented repair.

1. Fix shared context representation and input accounting for chart selection and learning; inspect complete live results through deployed consumers.
2. Fix database concurrency/capacity and durable execution/metadata recovery.
3. Profile Trading Agent outcome generation, preserve completed work, and verify each retained horizon.
4. Add output degradation to health monitoring and per-result continuation to news; reconcile historical rows only with matching execution evidence.

Diagnosis only: no production/dev runtime, settings, credentials, schedules or data changed; no paid research reruns were started. Local documentation and diagnostic captures were updated.

Evidence: [compact capture](evidence/SYSTEM_HEALTH_2026-10-15.json). Raw database captures, Azure queries/metrics, execution lists, Blob manifests, HTTP responses and seven retained datasets are stored privately under `tmp/health-audit-20261015/`.
