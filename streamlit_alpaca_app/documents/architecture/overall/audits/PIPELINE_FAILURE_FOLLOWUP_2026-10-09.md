# Pipeline failure follow-up — October 9, 2026

Historical diagnosis captured before the subsequent repair. Current restoration
status and acceptance are tracked in the [active repair plan](../../../plans/PIPELINE_RELIABILITY_REPAIR_2026-10-09.md).

At this capture, remaining faults were not repaired. Checked at approximately
20:12 UTC / 13:12 Pacific. This supersedes any interpretation of the earlier
repair receipt as proof that every pipeline job was healthy. That receipt remains
valid evidence for its specific Trading Agent, equity and monitor acceptance runs.

## Current production state

| Job | Latest relevant execution | Result |
|---|---|---|
| Trading Agent | `trading-agent-build-29859515`, 18:35 UTC | Succeeded at 18:50:09; all five retained horizons have `ok` status. |
| Equity refresh | `equities-intraday-preload-29859515`, 18:35 UTC | Succeeded at 18:40:57 on the repaired allocation. |
| Home | `attention-home-build-29859530`, 18:50 UTC | Azure succeeded at 19:29:21; database Warning for partial market inputs. Twelve ticker investigations, four source events and publication completed. |
| News ingestion | `news-ingest-and-features-29859485`, 18:05 UTC | Both attempts killed with exit 137; final termination 18:30:24. The next 20:05 execution is still running at this capture. |
| Zopedia maintenance | `zopedia-maintenance-29859530`, 18:50 UTC | Both attempts failed with `IdleInTransactionSessionTimeout`; final process exit 19:06:22. |
| Zopedia learning | `zopedia-learning-29859560`, 19:20 UTC | Both attempts failed with the same database exception; final process exit 19:34:41. |
| Health monitor | `system-health-29859600`, 20:00 UTC | Succeeded; three open incidents for news, maintenance and learning. |

The public Home API returns HTTP 200, `available`, and run
`2500f952-4f84-4a28-bfe5-eaccc3c91d06`, as of 18:50:38 UTC. This establishes
publication availability, not complete upstream health or semantic quality.
The latest executions of the other inspected scheduled production jobs succeeded;
some have older monthly schedules, so their October 1 receipts are not fresh runs.

## Database failure: an incomplete transaction-lifecycle change

Commit `6856a1de02cb28a0af9f0ae5d347a6275ac6aa11` on October 7 added
`idle_in_transaction_session_timeout=60000` to the pipeline connection, alongside
lock and statement timeouts. The actual pre-repair worker payload already has
this setting; today's compatibility overlay preserved it. The local ordinary
database connection reports a zero default, so the pipeline's connection-level
setting matters.

Maintenance reads pages using the job connection, constructs the maintenance
snapshot while that transaction remains open, then tries to persist its derived
indexes and mutation audit. PostgreSQL has already terminated the connection.
The traceback reaches `persist_zopedia_maintenance_snapshot` and
`_zopedia_storage_ready`. Learning likewise retains database transactions across
critic/replay work. Today's traceback reaches `_merge_learning_event_metadata`
after replay, at deployed `services/zopedia_learning.py:911`.

Both jobs also fail to record their own terminal status because they reuse the
closed connection. The updated monitor subsequently records the exact Azure
failure. Its database reconciliation timestamp is later than the process exit;
do not report it as the process failure time.

This predates today's repair. Azure lists all four October 8 maintenance runs as
Failed, and their console logs contain the same timeout. Both October 8 learning
runs also contain it. Maintenance's last successful database run began October 6
at 18:51 UTC. Learning is intermittent: its October 9 15:20 run succeeded.

## News failure: insufficient memory during acquisition

News ingestion persisted its 50 articles and attention/filing context before both
attempts were killed during business research. Last progress identifies connector
acquisition for 23 and 24 planned questions. Azure reports exit 137 with reason
`ProcessExited`, not an explicit OOM reason.

Azure `UsageBytes` reached **2,261,024,768 bytes (2.1057 GiB)** at 18:30 UTC.
The worker allocation is **2 GiB**. The first attempt's boundary also has a
2,257,809,408-byte sample at 18:14 UTC. These measurements strongly support memory
exhaustion, although they do not provide a kernel OOM record or identify the
specific allocation responsible. The exact per-component allocation remains
unprofiled. The source search fan-out defaults to 64 workers and this job has no
override; query acquisition can also open source pages. Increasing equity
capacity did not increase this independently configured worker's capacity.

## Why alerts appeared after a reported recovery

Earlier monitoring covered Home/Trading database failures and provider errors.
Killed processes and database-disconnected jobs left misleading `Running` rows.
The new monitor can reconcile executions that record platform IDs, exposing the
next scheduled failures in previously unmonitored jobs. Older unidentified rows
remain unchanged. Zero open incidents at 18:15 was a snapshot with incomplete
historical coverage, not evidence of global recovery. News was then still running;
maintenance and learning had not yet run with the new tracking.

One separate monitor incident recorded an Azure lookup HTTP 500 at 19:00 and
resolved at 19:15. It is not one of the three currently open incidents.

## Required source repair and acceptance

- Give shared maintenance and learning services explicit read/compute/write
  phases. Materialize reads in short owned transactions, perform computation and
  remote research outside transactions, and persist each coherent result with its
  audit atomically. Respect caller transaction ownership. Learning verification
  needs a separate transaction per completed result. Keep the protective limits.
- Separate job tracking connections from research transaction ownership so a
  closed work connection cannot erase the actual application failure.
- Profile shared connector acquisition, including child processes and page reads.
  Size news capacity with headroom and control simultaneous costly acquisition at
  its shared owner. Queue the full planned work; do not reduce recall by dropping
  questions, symbols or sources to fit memory.
- Audit scheduled-job inventory and historical Azure failures, including jobs
  without new tracking IDs. Reconcile old rows only when exact execution identity
  is established. Accept each affected full scheduled path and its next cycle;
  app HTTP health and a repaired subset are insufficient.

No runtime code or deployment was changed during this follow-up diagnosis.
Evidence: [compact capture](evidence/PIPELINE_FAILURE_FOLLOWUP_2026-10-09.json).
Raw Azure console/system logs, historical errors, execution lists, memory metrics,
database rows and product metadata are retained under
`tmp/service-followup-20261009/` in this workspace.
