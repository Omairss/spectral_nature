# Actual daily DeepSeek spend — October 15, 2026

Status: provider dashboard confirmed **$37.21** for the **Spectral Nature** API key,
with **Today** selected and local **PDT / MST** dates. This supersedes the earlier
illustrative $3–$4 workload mix as a description of daily consumption.

The database snapshot was taken at October 16, 04:18 UTC (October 15, 21:18 PDT).
Existing blob outbox receipts and job execution records were subsequently read.
No model requests, pipeline executions, settings changes, reconciliation writes
or deployments were made for this audit.

## Provider totals and attributable spend

The actual rendered DeepSeek page reported:

| Provider measure | Today |
| --- | ---: |
| Cost | **$37.21** |
| API requests | 3,109 |
| Total tokens | 267,604,756 |
| Flash requests / tokens | 3,056 / 267,064,473 |
| Pro requests / tokens | 53 / 540,283 |

Known usage, repriced with the saved rates and applicable weekday UTC time band,
attributes approximately **$35.64**:

| Workload | Attributable estimated USD |
| --- | ---: |
| News ingestion and company research | **27.03** |
| Home builds | **5.92** |
| Queued ad hoc/research worker calls | **1.48** |
| Trading Agent candidate generation | **0.58** |
| Other recorded work, including experiments, macro and learning | **0.63** |
| Known subtotal | **35.64** |
| Difference from provider total, not yet assigned | **1.57** |

These buckets are estimates from recorded usage, not provider-issued per-job
line items. The production records report Flash. The isolated flow experiments
account for about $0.37 of known spend; premium routing in the unreleased flow
does not explain the large production workload.

The known merged usage is 248,081,490 input plus 12,722,049 output tokens, totaling
260,803,539. Reasoning is included in output. There are 3,079 recorded attempt IDs,
including 80 without priced usage (78 failures and two unfinished attempts).
The remaining provider tokens, requests and $1.57 are not attributed to any
specific failure or client without evidence. The existing schema also lacks an
API-key fingerprint comparable with the provider's selected key.

## Execution frequency and repeated work

Azure's current news configuration is `5 14,16,18,20 * * 1-5`, with one replica
retry and a 10,800-second replica timeout. Today's four scheduled news executions
produced five application-run identities:

| Start UTC | Application run | Recorded outcome | Attributable estimated USD |
| --- | --- | --- | ---: |
| 14:05 | `6824bf77-75c7-422e-bb56-8c0cf9d602e7` | Succeeded | 5.46 |
| 16:05 | `4e490042-de70-4f63-a546-45d900237e85` | Succeeded | 4.98 |
| 18:05 | `4e141e24-d8cd-4a40-b449-f774d257f754` | Failed: one required news interpretation missing | 5.49 |
| 19:42 | `b8e5bea0-52f5-442f-b89d-0e055ac3fa3c` | Retry under the same Azure execution; later platform-terminated | **4.19** |
| 20:05 | `e3c173e8-5a4c-4687-9238-5674c707fe48` | Succeeded | 6.90 |

The failed 18:05 application ended at 19:42:00; a fresh application run began at
19:42:36 under the same `news-ingest-and-features-29868125` platform execution.
Its new model usage is paid work even though it ultimately failed. The repeated
attempt overlaps the next scheduled execution. Retry alone bought 27.76 million
known generation tokens and cost $4.19; do not assume the whole amount is
avoidable without inspecting which completed stages were actually reused.

Home has four metered roots, at the scheduled 14:50, 16:50, 18:50 and 20:50 slots.
The actual news/company workload consumes 181.18 million known input tokens and
8.53 million output tokens. Its recurring full builds dominate the known bill;
the older one-build sample and small-query averages do not describe this day.

## Accounting recovery

A SQL-only read attributed $34.00. Read-only inspection found 161 production
outbox receipts whose attempt IDs were absent from that SQL snapshot. Merging
them locally by immutable attempt ID added $1.635, yielding $35.639. No receipt
was inserted into SQL or removed from the archive. Development/experiment
outboxes were checked too; their additional pointer did not add spend to the
local October 15 window.

Logical gateway records were cross-checked without adding them to transport
totals: 33 lacked a matching attempt and none exposed usage in the selected
response metadata. They do not justify an extra known-cost sum. Missing usage
does not establish zero provider cost.

Current [official prices](https://api-docs.deepseek.com/quick_start/pricing/)
agree with the saved snapshot. The observed production news/Home work is in
off-peak hours; time-band adjustments mainly affect queued research and local
experiments here. A price mismatch is not established as the main explanation.

## Consequence for the reasoning/cost plan

The earlier $3–$4 illustration omitted the actual four daily news builds and four
Home builds, used historical small examples and did not include today's repeated
work. It was not a daily estimate and should not have been used as the relevant
comparison when discussing the overall allowance.

Before expanding premium routes:

1. Fix continuation across platform retries at the shared execution owner so a
   failed narrative does not cause another costly pass over completed company
   work. Preserve failure history and count all paid work.
2. Inspect repeated company dossiers and retained-context assembly across the
   four scheduled news builds. Reuse completed work at its actual input identity;
   let the shared researcher choose useful incremental investigation. Preserve
   full source access and discovery breadth.
3. Reprice the actual daily role volumes, including Home, news, Trading Agent,
   queued research and failures. Separate model-routing cost from handoff changes.
4. Use representative daily workloads to assess the owner's combined +20%
   allowance. This single observed day, including its retry, is not a steady-state
   forecast or permission to spend another 20% on top of an unexamined bill.

The [reasoning/cost plan](../../../plans/ZOPEDIA_REASONING_QUALITY_COST_2026-10-15.md)
remains unaccepted for rollout. The [verification receipt](../../../verification/llm-flow-2026-10-15/quality-cost/daily-spend-2026-10-15.json)
retains aggregates, exact job identities, method, coverage limits and source-file
hashes. Raw read-only snapshots are under `tmp/daily-spend-20261015/`.
