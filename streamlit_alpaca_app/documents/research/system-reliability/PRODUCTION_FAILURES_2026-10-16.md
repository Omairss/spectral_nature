# Why the October 16 reliability release remained degraded

[Master research index](../README.md) · [Production release receipt](../../verification/system-reliability-production-2026-10-16/README.md) · [Retained incident evidence](evidence/2026-10-16.json)

This records the October 16 promotion observations, not a fresh certification of current production health. The release reached four apps and sixteen job templates, but the overall service-recovery objective was **not achieved**. Component checks established narrower improvements than the production outcome required. The integration work should have distinguished those improvements from sustained recovery under concurrent production load before promotion.

## What the evidence establishes

| Failure | Observed evidence | Why the repair did not resolve it |
| --- | --- | --- |
| Pipeline database saturation | Before recovery, one-minute CPU averages ranged about 91.8–99.3%. Twelve stale client evidence-search reads and one parallel worker were cancelled; two later overdue legacy read connections were closed. The subsequent 17:27–17:37 UTC metric window still showed about 93.4–99.2% CPU. | Releasing connections and bounding new sessions does not reduce the work of expensive queries or control aggregate demand across all processes. Fresh expensive reads appeared after cancellation. |
| Home intermittently returned 503 | The public Home endpoint returned 200 before promotion and after initial recovery, then 503 on the final checks. Published Blob artifacts were present. | The delivery path was not fully isolated from SQL: on a metadata-cache miss, a valid Blob manifest is followed by a synchronous SQL lookup before the pointer is returned. That is a concrete delay dependency; the exact cause of each final 503 was not fully traced. |
| Retained-data research produced no answer | Run `agrun_5d01a223232f4fde82aa7c1e` failed with `no_usable_answer`. One planner request succeeded. The retained-investigation tool timed out; final synthesis reached its deadline before HTTP transport and was recorded as `not_sent`. | Execution spent its budget before a usable answer was produced. Connection-lifecycle repair did not establish bounded useful source access and completion under this load. This is not evidence of a failed synthesis response from the model. |
| Scheduled health exceeded its deadline | New-image execution `system-health-29869530` repaired 18 dataset indexes, then exceeded the existing 300-second platform window while processing the provider-receipt backlog. Azure marked it Failed; SQL still had a Running row. | This repair put bulk reconciliation before core observation. A receipt count limit of 1,000 was not a wall-clock budget. Successful partial repair did not leave enough time to complete monitoring and persist its terminal state. |
| Health UI initially timed out | Sequential SQL collection could consume the BFF deadline and hide independent Azure/Blob data. | The initial validation covered immediate failures, not a dependency that remained slow. The bounded concurrent Health collector subsequently corrected this path; heavy legacy Admin collection can still fail independently. |

The metric windows overlap recovery and continuing work; they are not a controlled before/after estimate of release impact. They establish persistent saturation, not how much CPU any individual caller caused.

## The design gaps

**A connection budget is not a query-work budget.** The shared owner returns physical connections at transaction boundaries and defaults to two simultaneous leases per process/credential identity. That protects idle capacity and local fan-out. It does not impose a server-wide limit across API replicas, scheduled jobs, recovery and research workers, or make an expensive retained-evidence query cheap. New clients have scoped application deadlines; the separate 120-second database-role default catches otherwise uncapped clients. Neither guarantees the database can complete the arriving workload. Query plans, work volume and caller overlap still need to be measured together.

**Updating templates does not replace already-running executions.** Home/news jobs that started on old images continued running, with their existing connections and query behavior. Their paid completed work was preserved rather than discarded. The rollout therefore did not instantly remove the workload that existed before it. This is part of the recovery explanation, not the sole proven cause: the newer research and monitoring executions also failed.

**Publication recovery and fast delivery were conflated.** Restoring SQL indexes from durable manifests is useful. It does not make an already-published page independent of SQL if metadata selection still waits on SQL. The accepted snapshot's `latest_dataset_metadata` confirms that wait even when it has found a stable manifest. Protecting the published-content read path is a separate architectural requirement. Whether this was the immediate cause of each Home failure remains open.

**Recovery was put on the monitor's critical path.** The accepted `run_system_health_check` reconciles dataset manifests and provider receipts before reading/evaluating product health. That ordering was introduced by this repair. Local recovery checks used a small owned dataset and individual receipts; they did not represent a real backlog on a saturated server. The corrected boundary should allow observation to complete independently, with bounded, resumable maintenance preserving unreconciled receipts. Merely increasing the platform deadline would leave the coupling intact.

**The research deadline covered more than provider inference.** A successful planner transport did not imply a healthy research path. The source tool timed out, and final synthesis never reached transport. Preparation, source lookup and other orchestration/persistence waits must be included when locating lost time. The evidence does not provide a complete phase-by-phase attribution, so neither provider latency nor SQL alone is established as the cause of every elapsed second.

## What improved, and what the validation missed

The transaction/concurrency checks demonstrated local connection ownership. Artifact/receipt checks demonstrated recoverability for the scoped inputs. The new scheduled worker really did repair 18 metadata indexes. The corrected Health collector returned independent publication/resource observations within about 20 seconds under real database load. These are useful results with limited scope.

They did not demonstrate repeated successful Home and retained-data research requests while the normal production jobs and recovery backlog overlapped. Ready revisions, image hashes, trivial `/health` responses, local connection peaks and a successful partial reconciliation could not establish that result. The production failures show that the acceptance evidence was insufficient for the overall reliability goal.

This is an engineering repair and validation gap. Hierarchical summarization was off and cannot explain these production failures. The separate reasoning/cost candidate was also excluded.

## Evidence needed for the next reliability repair

1. Attribute expensive SQL to actual consumers/executions and inspect representative query plans and work volume. Measure server-wide overlap, including legacy executions and recovery; do not infer adequate capacity from low connection counts.
2. Follow an already-published Home artifact through the actual request path while SQL is slow. Establish where time is spent and make the shared publication reader resilient at that boundary.
3. Give observation and maintenance independent execution budgets. Exercise a retained backlog and prove that unfinished work remains resumable while the monitor completes and records a truthful terminal state.
4. Repeat real user-path checks across the normal overlap period. Record Home availability, retained-data answer completion, database work and recovery progress together. Do not repurchase full paid research jobs just to substitute process completion for diagnosis.

Those are proposed acceptance observations, not fixes completed by writing this record. They do not require stricter semantic gates, new premium model routes or an assumed infrastructure upgrade.

## Evidence preservation and source identity

[Incident evidence](evidence/2026-10-16.json) retains sanitized Azure metric points, the observed health-job log, public research outcome and relevant accepted-source excerpts/hashes. The excerpt source is `/tmp/sn-reliability-20261015/accepted-release-source`, whose worker identity is recorded in the release receipts. Current workspace files have later edits, so their complete contents must not be treated as the deployed snapshot.

Private operator/rollback material remains under `/tmp/sn-reliability-prod-20261016/`. It includes credentials/configuration and must not be copied wholesale into research documents. The public [release JSON](../../verification/system-reliability-production-2026-10-16/release.json) records images, source guards, protected configuration and remaining issues.
