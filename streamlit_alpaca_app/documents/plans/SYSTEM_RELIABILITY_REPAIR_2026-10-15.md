# System reliability repair proposal — October 15, 2026

Status: repairs released to development and promoted to production on October 16. Development acceptance passed; production remains operationally degraded as recorded in the production verification. The owner clarified that the supposed backend handoff was an incorrectly named LLM-only coordination file. This thread now owns Admin/Health, chart context, learning, database lifecycle/index recovery and the combined development release. The separate Trading-memory thread owns its memory/source-reference projection and research-checkpoint changes, which are preserved through scoped integration. Production was explicitly authorized and promoted on October 16. The [production verification](../verification/system-reliability-production-2026-10-16/README.md) records remaining database load, failed research verification and the health-worker recovery timeout. The [reasoning and cost candidate](ZOPEDIA_REASONING_QUALITY_COST_2026-10-15.md) remains unaccepted and excluded.

## Implementation coordination

- The LLM-only owner confirmed there never was a separate general-backend readiness handoff. The integration thread is implementing that unassigned scope; no further owner relay is needed.
- Shared handoff: `/tmp/sn-reliability-20261015/OWNERSHIP.md`; Admin-only source baseline and patch are in that directory. Backend readiness should include changed files, exact baseline/diff, dependency/config/schema requirements and completed validation, preferably in `BACKEND_READY.json` beside the handoff.
- Admin/Health now has an authenticated backend/BFF route and a separate tab. Seven local authorization, scope isolation, cache-concurrency and partial-outage checks pass against both the working tree and the accepted API source with scoped backend changes. Real PostgreSQL, app and job Azure metrics were read successfully. Desktop/phone rendering and web type checks pass. Task-only release contexts were assembled from the accepted deployed API and native/Trading source, hash checked, built and deployed to all four dev apps and eleven dev jobs. Live verification is recorded separately.
- Hierarchy experiments use only the isolated `hierarchy-lab-20261015` settings scope and cheap Flash routes through the shared engine. Originals, derived summaries, complete outputs and provider attempts are retained under `/tmp/sn-reliability-20261015/experiments/`. No summarization policy becomes an application default from these trials. The early source-changing and restricted-tool runs are exploratory. The final comparison freezes 159 source files and restores the exact shared original-context reader; complete answers are reviewed personally.

The [hierarchy experiment review](../architecture/AQL/audits/HIERARCHICAL_SUMMARIZATION_EXPERIMENTS_2026-10-15.md) records full-answer findings and protocol corrections. The [Admin acceptance record](../verification/admin-health-2026-10-15/README.md) records local and deployed identity/UI checks. The [combined release record](../verification/system-reliability-2026-10-16/README.md) owns the immutable images, integration checks and remaining limitations.

## Implementation update — October 16

- Invocation-owned evidence navigation lives in the shared QueryService/tool registry. The catalog supports complete pagination and literal metadata search; originals support lossless windows and JSON Pointers. No source shortlist, mandatory read depth or semantic publication gate was introduced. Full content hashes bind input identity, including data beyond the first read window. All chart histories remain available and render from the original arrays. The brief has an explicit text view; original Home fields remain addressable, while media remains in the original retained artifact and is excluded from ordinary inference context.
- Learning no longer fabricates a diagnosis after model failure. An episode is durable and pending before inference; only a completed model critique can reach the existing mutation policy. Matching completed critiques are reused without another call or mutation. Generated replay scenarios retain semantic guidance and the shared model review, replacing fixed phrase/tool-name pass criteria. Execution failure remains distinct from qualitative review.
- All seven direct PostgreSQL owners use a shared bounded null pool and transaction-owned logical session. Commit/rollback returns the physical connection; autocommit tracking releases after each cursor. Explicit transactions/savepoints keep their original semantics. The evidence-index session advisory lock has an explicit physical lease. Pools remain private to exact database credentials. The default is two concurrent physical connections per process/credential identity, bounded queueing, and no idle pool. This is not a server-wide concurrency guarantee.
- Twelve live PostgreSQL checks passed, including six concurrent clients under the two-connection limit, nested savepoint rollback, failed-transaction recovery, dictionary cursors, explicit advisory-lock ownership and zero remaining probe connections. No permanent application data was written by that probe.
- Blob manifests retain indexing fields. Committed Blob data remains usable if its SQL index write fails, with a component warning; the periodic health worker can idempotently reconcile registered dataset manifests and provider receipts. Missing individual news interpretations are recorded as degraded components after completed work is retained, rather than causing a blind whole-job retry. Genuine execution exceptions still fail their job.
- The final provider payload records serialized size by message role and schema size without raw prompt text. Started/final attempt receipts share one ID; late outbox starts must not overwrite completed usage. Provider usage remains the actual token authority.
- First local live review: 17 Flash attempts, $0.089923 estimated, 531,207 input / 16,206 output tokens; largest request 69,329 tokens. Learning challenged a prior fabricated detector diagnosis and reused its completed result without an additional call. The chart result stayed within context limits but failed personal review on an unplotted comparison and an overconfident financial inference. That output was held. Subsequent full-catalog experiments exposed circular prior captions and inaccurate range claims; the accepted evidence contract separates original observations, media and prior interpretations and provides exact numerical series metadata. No new paid tier or application model default was introduced.

## Design objective

Preserve research breadth, complete original evidence and useful partial answers while bounding the resources consumed by each operation. Make interrupted work resumable and operational state faithful to the actual output. Extend the existing shared engine, artifact archive, execution state and monitoring; do not introduce a second agent, distributed workflow framework or another metadata authority.

Research structure stays adaptive. Summary levels, extra reads, retries and review are capabilities the shared agent can use, not a mandatory sequence for every question. Resource limits govern connection/memory ownership; they must not become fixed topic rules, source quotas or semantic publication gates. A failed source read, optional reviewer or subtask may be acceptable when the final work remains useful. Preserve valid output and recover selectively. Durable knowledge writes retain their separate authorization contract.

## 1. Give each model operation an explicit view of retained evidence

The application already retains evidence, shares exact duplicates by reference and checkpoints some completed structured stages. Extend that ownership rather than adding a generic summarizer. A durable research artifact is richer than the input needed by any one model call.

- At the final provider boundary, measure the assembled request by section and origin, using a model-compatible tokenizer where available. Include schema, instructions and reserved output. Estimated counts must remain identified as estimates. This diagnoses growth before making another paid attempt; it does not decide relevance.
- Keep originals in existing immutable storage. Model input carries the task, organized findings, relevant original passages and resolvable evidence identities, with user observations/assumptions distinct from external reports and generated interpretations.
- Never include encoded audio, images, storage bookkeeping or recursively embedded complete prior runs in ordinary text inference context. The captured Home artifact alone contains approximately 1.61 million characters of encoded audio; that is a storage observation, not proof it entered the rejected chart requests. The precise deployed input composition still needs measurement.
- For a large chart catalog, keep every series discoverable through the shared agent. Expose dated numerical metadata and access to complete histories; let the agent inspect useful series and compare originals. A no-tools call containing an oversized catalog cannot solve this reliably. Do not add a separate chart agent or a hand-coded economic shortlist.
- Learning reviews one episode with links to its complete thread, relevant execution events and sources, rather than recursively copying the last several entire runs. Broader context stays accessible when the critic needs it.

### Adaptive hierarchical summarization

Use the existing shared agent and retained evidence owners. Original captures can support source findings, connected investigation briefs and broader overviews. These levels are examples of useful views, not a required depth or a prescribed hierarchy of separate agents. Related companies, events and themes can cross-link through the same original source identities rather than being forced into exclusive buckets.

The agent may start with an overview, descend to findings or original passages, search across levels, and skip summarization for a small task. Originals remain directly retrievable; a topic omitted from an overview must not disappear from discovery. Summaries carry source identity, dates, material disagreements and context, with user assumptions kept distinct from source reports. Repeated summaries of one source are not independent corroboration. Agentic grouping and interpretation remain model work, without fixed ticker/topic branches.

Reuse current read summaries and company/event dossiers. Cache additional summaries by source versions and inference contract; rebuild only changed dependencies, and create upper levels when they serve a real consumer. Do not add a summarization call before every inference. Initial summary construction and invalidation have real cost; evaluate amortized full-workload cost, answer usefulness and information loss rather than assuming that shorter final prompts guarantee savings.

Exact deduplication cannot fix unique bulk or an inappropriate stage input. Hierarchical views should make originals easier to navigate; they must not leave the final analysis dependent on an irreversible chain of increasingly compressed prose. Personally inspect complete outputs on unfamiliar source-heavy, financial and dated-event cases, including useful indirect leads and cross-source contradictions. [RAPTOR](https://arxiv.org/abs/2401.18059) provides relevant evidence for multi-level retrieval, not proof that a particular summarization design will pass this application's qualitative bar.

## 2. Preserve an uncompleted critique as uncompleted work

`critique_learning_event` currently returns a fixed critique after exceptions, and the learning job can proceed to a durable update. Replace this with an explicit retained failed attempt and a resumable pending critique. A detected episode remains a valid lead; failed execution must not manufacture a model diagnosis or authorize a learned conclusion.

Keep successful learning from other episodes and preserve existing memory-write permissions. Execution checks establish whether a critique actually ran; qualitative assessment determines whether a completed critique is useful. Fixed phrase-based replay checks cannot establish reasoning improvement.

## 3. Resume failed work and publish coherent results

Use existing execution-state and immutable-artifact owners to checkpoint acquisition, company/event research, synthesis, individual horizon output and materialization. A restart adopts compatible completed work and retries only missing/failed work.

Compatibility includes environment, access scope, input/source versions and the frozen inference contract. A genuinely new scheduled observation must reassess freshness; it must not silently replay stale live acquisition. Existing live-acquisition reuse is tied to a platform execution, so automatic-retry behavior and cross-execution continuation need deliberate lineage rather than broader cache matching.

For news, a missing interpretation should retain all other completed interpretations and resume that unit. Prevent two replicas from paying for the same unit using existing durable ownership/claims. Superseding scheduled runs should make an explicit freshness/continuation choice instead of racing blind whole-job retries.

Write an immutable completion receipt for every committed stage/snapshot before advancing its published pointer. Include dataset versions, source versions, per-component state and execution identity. PostgreSQL indexes these same receipts; idempotent reconciliation repairs missing metadata after an outage. Readers select one declared snapshot, avoiding accidental mixtures of independently selected versions. A newer valid Blob receipt remains usable when its database index is temporarily behind. This extends existing manifest/receipt infrastructure rather than creating a third source of truth.

## 4. Bound database work across all consumers

The pipeline and SAA owners currently open their own psycopg connections. Consolidate acquisition/lifecycle policy while preserving distinct pipeline and control databases, roles, credentials and caller-owned transactions.

**First-pass constraint: retain the current paid database tier and container allocations.** No additional pooling service, Redis, telemetry platform or higher database tier is part of this proposal. The goal is better use of current capacity; additional instrumentation still consumes some existing CPU/I/O and must stay lightweight.

- Acquire database sessions around actual SQL phases; release them before model calls, browser work and other remote waits. Preserve protective transaction timeouts.
- Use small, measured per-process connection limits with bounded waiting. Allocate limits across maximum simultaneous replicas, workers, tracking, maintenance and operations; a pool of five in every process is not a server-wide five-connection limit.
- Queue database access independently from research work so research breadth is retained. Track acquisition wait, open/idle/transaction states and caller identity.
- Protect interactive and monitoring capacity from background fan-out. Avoid holding a database connection merely to wait for a connection allowance.
- Start short-lived workers with no pre-opened idle pool. Use a small measured pool or Psycopg's bounded null-pool pattern, which can queue callers and close unused sessions. Size the total against simultaneous processes/replicas rather than assigning every process a large default pool.
- Reuse already loaded metadata/read context within a job, batch related SQL and writes, and keep schema DDL in explicit migrations. Avoid new sessions and repeated metadata lookups for each symbol or tool result when one coherent read suffices.
- Eliminate duplicate overlapping work through existing execution ownership and checkpoints. Queue deferrable background SQL when interactive demand rises while preserving research recall and required freshness. Waiting and selective continuation are preferable to multiplying replicas/retries against an exhausted server.

Psycopg provides bounded pool acquisition and queuing ([documentation](https://www.psycopg.org/psycopg3/docs/advanced/pool.html)). Azure's built-in PgBouncer is available on General Purpose and Memory Optimized tiers, **not the current Burstable B1ms** ([Azure documentation](https://learn.microsoft.com/en-us/azure/postgresql/connectivity/concepts-pgbouncer)). It is therefore outside this no-upgrade pass. Raising `max_connections` also increases PostgreSQL resource allocation ([PostgreSQL documentation](https://www.postgresql.org/docs/current/runtime-config-connection.html)); keep the protective limits. If measured useful demand still exceeds existing capacity after the source changes, report the observed throughput/freshness tradeoff before proposing paid capacity.

## 5. Make outcome computation incremental and memory-bounded

The current Trading Agent path loads twelve full candidate/run versions, retains copies and concatenations, then loads the whole price-history frame. Outcome preparation copies/sorts that history and creates per-symbol frames. The failure logs stop in this phase. This identifies a concrete design target, not a measured attribution of every byte.

Separate completed research publication from outcome computation through resumable stages, giving the outcome phase an independent process if profiling confirms retained research state is material to peak memory. It must be able to restart without buying research again.

- Read candidate IDs, horizons, anchors and scoring fields first. Load prose and research payloads only for a review that actually needs them.
- Scan only required price columns, candidate symbols, benchmark and date ranges, using bounded batches. Push selection into storage readers before constructing Pandas frames. Arrow supports column projection, filtering, batch sizing and bounded read-ahead ([documentation](https://arrow.apache.org/docs/python/generated/pyarrow.dataset.Scanner.html)). Physical layout determines how much I/O can actually be skipped.
- Update open/unmatured outcomes as prices arrive. Reuse unchanged completed outcomes, while invalidating affected results when price revisions, corporate actions or scoring methodology change. Preserve every candidate; do not shrink history to fit RAM.
- Use one prepared numeric series per symbol and avoid repeatedly copying it for each candidate.

Keep current allocations during this first pass. Profile the whole process, native allocations and child processes as applicable. Acceptance needs measured headroom on a larger retained workload and no duplicate research attempts during recovery. Do not reduce candidate breadth or discard historical research to force memory below a number.

## 6. Monitor usable output and the observer itself

Record process outcome, component execution state, published artifact/version and freshness separately in **Admin/Health**. Keep valid partial output useful. Do not add operational counters, failure badges, monitoring panels or generic caveats to Home, Markets, Portfolio or research answers. Existing user-relevant progress and genuinely unavailable actions keep their concise states.

Extend the existing Admin System/LLM read models and actual provider-attempt ledger. Provide shared time/environment filters, trends and drilldown to the responsible job/run/stage:

| Area | Useful observations |
|---|---|
| LLM usage | Actual HTTP attempts versus logical operations; input/output/reasoning and cached tokens where reported; context utilization; estimated cost and unpriced usage; latency; retries, repairs and recovery work; summary construction/reuse |
| PostgreSQL | CPU/memory, connections versus available capacity, acquisition waits, idle/idle-in-transaction sessions, rejected connections, locks/slow SQL and storage; preserve the pipeline/control distinction |
| Containers and jobs | Per-replica CPU/memory versus allocation, peaks, restarts/exit codes, duration, active execution/queue state and repeated attempts |
| Delivered work | Latest published version/time, retained completed/failed components, usable horizons, absent chart selection, pending learning critiques, recovery and manifest/database reconciliation |

Reported reasoning tokens are a breakdown of completion usage where applicable, not an extra charge to add again. Unknown usage stays unknown; missing telemetry is not zero. All metrics show their observation time and scope. Use actual per-replica memory/capacity rather than mixing aggregate usage with one replica's limit.

Cache and batch metric reads in the existing backend, with bounded refresh only where useful; page rendering reads those snapshots. Reuse existing Azure standard platform metrics and current logs/receipts. Do not add verbose raw-prompt logging, a new telemetry service or paid alert rules as part of the first pass. Standard metric collection has no charge, but REST retrieval, extra log ingestion and some alerts can be billed ([Azure cost documentation](https://learn.microsoft.com/en-us/azure/azure-monitor/fundamentals/cost-usage)); API polling is not inherently free.

Admin/Health should distinguish four successful horizons plus one timeout, failed chart selection, partial review and a failed learning critic. These observations support diagnosis; they are not deterministic judgments of narrative quality or automatic publication blockers.

Use existing Azure monitoring for a missing health-worker heartbeat, database connection rejections and memory pressure. Detection of pipeline-database failure must not depend entirely on writing a new incident to that same database. A database-only view cannot see a job whose sink never opened.

Existing transition deduplication can continue to prevent repeated alerts. Keep individual recoverable source/model failures in the drilldown. Escalate persistent freshness loss, repeated resource exhaustion or materially unavailable delivered work; do not page on every source 404 or optional review timeout. Failure counts alone do not determine severity. A last-good result retains its own version/time beside a failed new attempt. Historical `Running` rows without exact execution identity remain historical unknowns until matching evidence exists.

## Sequence and acceptance

1. Add final-request composition measurements and accurate component outcomes; remove fabricated learning-success fallbacks.
2. Compare adaptive hierarchical evidence views and repair database lifecycle/concurrency in shared owners within existing paid capacity. Measure overlapping useful workloads and retained output rather than optimizing all failure counters to zero.
3. Add resumable stage completion and reconcile manifest/database metadata; make outcome reads incremental and memory-bounded.
4. Verify the actual affected paths with retained large inputs and interruption/recovery exercises. Personally read complete LLM results, preserving useful hypotheses, originals and user premises. Measure all provider attempts and recovery cost against the accepted combined-workload allowance.
5. Release only locally accepted repairs to development, keeping unreleased reasoning experiments separate. Production requires explicit approval.
