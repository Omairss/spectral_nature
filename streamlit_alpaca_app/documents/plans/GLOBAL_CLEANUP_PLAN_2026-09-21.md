# Global Cleanup Plan - 2026-09-21

Status: in progress. Grounded in `documents/architecture/overall/GLOBAL_OPTIMIZATION_AUDIT_2026-09-21.md`, the 2026-09-21 static probes, and the 2026-10-02 Next.js vertical-slice deployment.

## 2026-10-08 event-led discovery and coordinated research

Today's OpenAI-report selloff exposed a concrete missing path. The FT headline
was retained before Home's 18:20 UTC run, but none of its tagged instruments
entered the 78 candidates or 12 primary research plans. The 146 primary research
documents contained no OpenAI mention. All 12 ticker Zopedia enrichments recorded
timeouts; the market summary failed. The page led with Bitcoin weakness and
taxonomy cohorts. [Captured evidence and direct research traces](../architecture/overall/audits/evidence/AI_SELLOFF_DISCOVERY_AUDIT_2026-10-08.json).

Three direct shared-agent probes also missed the event. Their retrieval returned
unrelated headlines or old memory; two supplied article reads timed out. A
provider comparison returned zero Serper news results and nine general-web
results for the same short query. More research subjects or concurrency alone
cannot repair those admission, retrieval and execution failures.

The implementation priority is a shared event and investigation path:

1. Retain and index source events independently of daily ticker selection and
   downstream business-resolution success. Headlines and incomplete articles can
   nominate research. Private entities and concepts need no price row or fixed
   public-company mapping.
2. Use LLM/embedding retrieval to connect new claims with prior claims and
   relevant businesses. Cluster related reporting and joint numerical behavior;
   retain uncertainty and alternative connections. Learn event importance from
   reviewed usefulness and observed outcomes rather than fixed ticker lists,
   financial patterns, percentage thresholds or source-count requirements.
3. Let the shared AQL/Zopedia agent plan focused investigations around an event,
   retain their outputs, compare interpretations and synthesize one coherent Home
   lead with linked company context. The agent chooses the questions and number
   of investigations; there is no coded workflow specific to this example.
4. Give shared acquisition access to general-web and news search, retained raw
   news, memory and source reading. Query reformulation and semantic relevance
   belong to the shared agent. A news-only endpoint and exact ticker/word gates
   must not silently define available knowledge.
5. Use existing durable research execution to retain progress and schedule
   follow-ups with realistic provider budgets. Replace discarded 90-second whole
   investigations with explicit run/step state; do not merely increase the same
   blocking wrapper. Fresh source events must be able to update an investigation
   and Home between full scheduled builds.

The temporal and relationship learners in revision 2 remain useful inputs, but
training them is not a prerequisite for investigating an explicit ingested
headline. Evaluate event-to-research and event-to-publication latency, source and
relationship retrieval, usefulness and organization of the actual synthesized
lead, alongside chronological numerical evaluation. This is an architecture
correction and a read-only audit; no application code or deployment changed.

## 2026-10-07 historical training corpus

Free and existing-entitlement acquisition is running into the development Blob namespace. The shared-source implementation retains raw/cleaned data, revisions, complete pagination and immutable receipts; frozen frames separate available-at-time inputs from forward returns/downside targets. Full ingestion and broader free-source coverage remain incomplete. Follow the [collection plan](HISTORICAL_CONTEXT_TRAINING_HARNESS_2026-10-07.md), [status](../training/HISTORICAL_CORPUS_STATUS_2026-10-07.json), and [priced source TODO](../training/HISTORICAL_SOURCE_TODO_2026-10-07.md). Returns drive research direction; source/frame plumbing is not a trained discovery model or prospective evaluation.

## 2026-10-07 discovery model architecture — revision 2

The [14-page technical redesign](../../output/pdf/SPECTRAL_NATURE_LLM_DISCOVERY_REDESIGN_2026-10-07.pdf)
replaces the rejected product-oriented proposal. The editable specification is
[DISCOVERY_MODEL_ARCHITECTURE_2026-10-07.json](../architecture/overall/redesigns/DISCOVERY_MODEL_ARCHITECTURE_2026-10-07.json).
The original companies diagnose selection failures; they are neither model
classes nor coded inclusion rules. DeepSeek is restored.

Move learning before the research shortlist. A shared, causal temporal encoder
represents all covered entities with masked multiresolution reconstruction and
future-block likelihood objectives. Compare it with dynamic-factor/state-space
and simple historical baselines. Preserve observed trajectories, shared behavior,
and conditional surprise separately; residual surprise is not research utility.

Estimate signed empirical relationships and learned cohorts from histories;
regularized conditional lag models assess information beyond shared factors and
own history. Independently retrieve article-derived relationships through
Zopedia, with open descriptions, conditions and contextual entity recovery.
Neither modality requires confirmation by the other. No fixed signs, target
lists, named financial patterns, exact-source matches or complete provenance
requirements define admission.

Use broad nomination and shared LLM reranking initially. Collect personally
reviewed comparisons of investigation usefulness before training a pairwise
preference ranker. Retain selection history, cost and known-probability exploration
to expose blind spots. Evaluate with chronological folds, matched-budget
ablations, low-exposure samples and prospective qualitative review; old
deterministic selections cannot establish outcomes for unchosen research.

Separate numerical publication from narrative availability at existing owners.
Fit models in analytical jobs, not page renders or the bounded Zopedia analysis
sandbox. Market Explorer's ranked view requests 80 rows while Broad Markets
already exposes full-universe data. Separate result breadth, numerical compute,
12-versus-24 research subjects and provider concurrency when sizing work.

Sequence: establish the available-at-time training view; benchmark numerical
learners; connect both relationship sources; run broad research in shadow; then
learn and promote selection when reviewed data supports it. This remains an
architecture proposal; no application implementation or deployment occurred.

## 2026-10-07 stock decline discovery audit

The [production discovery audit](../architecture/overall/audits/DECLINE_DISCOVERY_AUDIT_2026-10-07.md)
adds a product-critical dependency to the cleanup priorities. Since July 1,
Home recorded 310 failed attempts and 13 successes; 309 failures report provider
balance errors. Numerical `market_opportunity_feed` publication remains downstream
of the mandatory Home narrative review, so an LLM outage freezes discovery data
while equities ingestion continues. A September 18 Trading Agent package used
August 14 opportunities. Moving ticker snapshots independently did not remove
this remaining dependency.

After availability is repaired, discovery still needs an architectural change:
sustained company drawdowns and broad industry weakness cannot depend on a daily
mover shortlist or peer-relative anomaly qualification. The data exists, but
research selection, universe continuity, source acquisition, and public surfaces
do not consistently preserve those concerns. Current macro-role tags also conflate
economic exposure with a measuring instrument, and a public NIB card resolves the
cocoa instrument to an Australian insurer.

Prioritize numerical publication at the existing equities owner, source-version
freshness and producer health, then company/cohort discovery and persistent
research priorities inside AQL/Zopedia. Preserve shared instrument identity and
one return-window definition. These are audit recommendations; no runtime change
or deployment was made. The linked report and captured evidence contain the
historical coverage, selection replay, precise scope, and proposed evaluation.

## 2026-10-07 implementation update

Checkpoint `35ea4cb6` preserves all pending work, including the native iPhone app.
The first implementation increment removes the duplicate research runtime from
`data_access/layer.py`: 536 deleted lines, including all three raw model-client
loads, both embedding-client loads, and the direct agentic/search imports.
Research bundle reads now use retained artifacts regardless of runtime mode or
legacy environment flags; missing artifacts return an unavailable contract naming
`attention-home-build`. Refresh re-reads retained bundles rather than bypassing
storage. Raw Alpaca news reads remain supported; hidden web search is retired.

This advances Phase 1, but does not claim all phase exit criteria: remaining
service imports and transitive ownership need a complete graph audit. Phase 0's
metric script is still absent, so the score below is a historical baseline, not
current debt. See [ACTIVE_MAP](../architecture/ACTIVE_MAP.md) for current owners
and explicitly unfinished simplifications. No frontend rewrite or broker change
is included.

Validation: 81 focused DAL/QueryService, API, and presentation checks pass in the
repository runtime environment. The checks cover both DAL modes, refresh reads,
legacy flag settings, unavailable research, and unchanged API/presentation paths.
These are contract checks, not claims about LLM output quality; model behavior was
not changed. Development release is scoped to the isolated Next.js API's existing
image plus `data_access/layer.py`.

Development release completed: `sn-api-next-dev--0000012`. Health, public Home,
and retained-bundle refresh passed on the running revision; the unavailable path
also returned correctly without live research. The dataset contained 79 retained
bundles. Exact image and rollback are recorded in `infra/UI_DEPLOYMENT_STATUS.md`.

## Next cleanup priorities — source review 2026-10-07

This is an assessment, not an implementation or performance benchmark. The
current production roots (`app.py`, `api`, `compute`, `data_access`, `pipeline`,
`presentation`, `services`, `views`) contain 162 Python files and 120,813 lines.
An AST import-reference graph finds a 26-module circular dependency group spanning
DAL/QueryService and AQL/Zopedia, plus a two-module SAA storage/Zopedia group.
The graph includes function-local imports: these counts describe coupling, not
proof of eager import failures or measured startup time. The old debt score has
not been recomputed.

Recommended order:

1. **Remove runtime schema work from jobs and telemetry.**
   `pipeline/jobs/main.py::_db_bootstrap` runs CREATE/ALTER statements at job start;
   `pipeline_store.py::record_connector_call_event` and model-call recording call
   table-creation functions before inserts. Move schema evolution into explicit
   deployment migrations, retain bounded readiness checks, and remove the legacy
   schema fallback branches after the target environments are verified. This
   reduces database work and lock exposure. `latest_job_status_table` also makes
   one latest-row query per job; fetch those rows together.
2. **Bound and reuse retained-data reads at their owner.**
   The DAL's single-ticker snapshot, metadata, and research-bundle paths load full
   datasets before selecting a row. `pipeline_store` already supports bounded
   symbol slices, but several consumers bypass them. Use filtered/batch reads and
   reuse a dataset version within one request. Preserve namespace, authorization,
   freshness, and replay identity. Measure actual frame sizes, I/O, allocations,
   and route latency before choosing broader caching or new partition schemes.
3. **Separate persisted contracts from research execution imports.**
   Even `services.aql.evidence_index::parse_json_list` imports the AQL package,
   whose initializer eagerly re-exports collectors, synthesis, and the engine.
   Baseline/page-summary modules also mix readers with producers. Put persisted
   decoding and transport contracts in the existing lower-level ownership boundary;
   research imports those contracts. Remove unused re-export facades and the
   test-only patch-propagating module replacement in `attention_agentic.py` once
   callers and tests use the actual owner. Reduce dependency cycles by deleting
   reverse dependencies, not by adding lazy-import workarounds.
4. **Consolidate the second loader policy layer.**
   `presentation/dashboard_loaders.py` still has 60 functions and global callback
   configuration alongside QueryService's dataset/chart registry. Keep UI-specific
   caching/session mechanics there; use the existing query contracts for data
   selection and refresh policy. Retire Streamlit routes only after verifying their
   required behavior is available in the replacement surface.
5. **Simplify pipeline declarations and persistence ownership.**
   Job dispatch, source-to-job mappings, runtime bootstrap, and dataset persistence
   still span the 3,276-line job runner and pipeline store. Consolidate existing
   declarations and storage behavior; avoid adding a framework or merely moving
   functions to more files. Readiness/migrations in item 1 should precede this.
6. **Unify dependencies and retire verified legacy roots.**
   Root `src/` has 62 tracked legacy files and a separate Dash/Flask requirements
   file. Active API/pipeline Dockerfiles install the application's broad shared
   requirements, including UI and acquisition dependencies. Establish one resolved
   dependency source with role-specific selections and a common supported Python
   contract, then archive legacy launch paths after reference verification. This
   is expected to reduce build/image overhead; no image-size savings are measured.

An additional iPhone efficiency opportunity is `APIClient.data`: every request
waits on one global task chain. That protects cookie/refresh-token rotation, but
also queues independent reads. Evaluate public-read concurrency first; private
read concurrency requires a shared session-refresh design and cancellation/logout
correctness across the native client and web gateway. Do not simply remove the
queue or weaken refresh serialization.

Start with items 1 and 2 for operational efficiency, then item 3 for architectural
simplification. No runtime changes or deployments were made during this review.

## Intent

This cleanup is not a frontend rewrite and not a cosmetic file split. The goal is to make the intended architecture mechanically true:

- AQL/Zopedia owns research and model synthesis.
- Data/query layers read materialized state or return explicit unavailable contracts.
- Streamlit, API, jobs, and scripts trigger or render shared behavior; they do not become private research runtimes.
- Frontends share one authentication/account service; browser transports may differ, but users, password hashes, memberships, lockouts, and policy do not fork.
- Large files shrink because ownership moved to the right layer, not because code was scattered.
- Old dependency roots and legacy paths stop competing with the active app.

## Verified Baseline

The current active production probe covered `app.py`, `api`, `compute`, `data_access`, `pipeline`, `presentation`, `services`, `views`, and `scripts`.

- Production Python surface: 145 files, 107,742 lines.
- `app.py`: 7,758 lines, 233 functions.
- `data_access/layer.py`: 3,935 lines, 111 functions.
- `presentation/dashboard_loaders.py`: 981 lines, 60 functions.
- `pipeline/jobs/main.py`: 3,139 lines, 104 functions.
- `services/zopedia_agent.py`: 4,481 lines, 94 functions.
- Direct `.generate_json(` calls: 26 total, 25 outside `services/aql_zopedia_gateway.py`.
- `load_llm_client(` calls: 9 total, including 3 in `data_access/layer.py`.
- `load_embedding_client(` calls: 10 total, including 2 in `data_access/layer.py`.
- `load_aql_zopedia_llm_client(` calls: 24 total across app, jobs, services, and probes.
- Forbidden read-layer live model/embedding owner: `data_access/layer.py`.
- Import cycles: 2 strongly connected components, with 19 modules total in cycles.
- Largest import cycle: 17 modules across `data_access`, AQL, Zopedia, attention, company, and agent-tool services.
- Internal production import edges: 498.
- Approved-hub incident import edges: 103, or 20.7% of internal edges.
- Non-hub internal import edges: 395.
- Upward layer edges: 24.
- Max module out-degree: 31, from `app.py` and `data_access/layer.py`.
- Active production imports from root `src`: 0 found.
- Dependency shape: root `requirements.txt` plus `streamlit_alpaca_app/requirements.txt`; no `pyproject.toml`, `uv.lock`, `poetry.lock`, or `.github` workflow found in the inspected root.
- Legacy root: `src/` contains older Dash/Robinhood/notebook-era app code and cached bytecode.

## Cleanup Metric

Track one headline metric: **Cleanup Debt Score**. Lower is better.

The score is intentionally weighted toward architectural boundaries. Line count matters, but only after hidden live behavior and cycles are visible.

```
Cleanup Debt Score =
  100 * hidden_live_read_path_files
+  10  * raw_model_call_sites_outside_gateway
+  5   * modules_in_import_cycles
+  1   * critical_file_excess_100_line_units
+  1   * production_python_loc_1000_line_units
+  1   * production_python_file_10_file_units
+  1   * topology_mess_10_point_units
+  25  * legacy_root_count
```

Definitions:

- `hidden_live_read_path_files`: files under `data_access` or `presentation` that import or call model, embedding, search, or gateway clients.
- `raw_model_call_sites_outside_gateway`: direct `.generate_json(` calls outside the gateway/provider adapter/test/probe allowlist.
- `modules_in_import_cycles`: production modules inside strongly connected components larger than one module.
- `critical_file_excess_100_line_units`: sum of `ceil(excess_lines / 100)` for:
  - `app.py` over 1,500 lines;
  - `pipeline/jobs/main.py` over 1,000 lines;
  - `data_access/layer.py` over 2,000 lines;
  - `services/zopedia_agent.py` over 2,000 lines;
  - `presentation/dashboard_loaders.py` over 500 lines.
- `production_python_loc_1000_line_units`: `ceil(active production Python LOC / 1000)`.
- `production_python_file_10_file_units`: `ceil(active production Python file count / 10)`.
- `topology_mess_10_point_units`: `ceil(Topology Mess Score / 10)`.
- `legacy_root_count`: stale top-level dependency or code roots that can be mistaken for active runtime ownership.

Topology Mess Score tracks whether the architecture is moving toward a star/spine shape rather than a fully connected peer graph:

```
Topology Mess Score =
  non_hub_internal_edges
+ 5  * upward_layer_edges
+ 2  * max(0, 60 - approved_hub_edge_ratio_percent)
+ 10 * max(0, max_module_out_degree - 20)
```

Definitions:

- `internal_import_edges`: unique imports from one active production module to another.
- `approved_hub_edge_ratio_percent`: percent of internal edges where either endpoint is an approved hub.
- `approved hubs`: the small set of intentional spine/contract modules, initially `data_access.contracts`, `data_access.query_registry`, `data_access.query_service`, `services.aql_zopedia_engine`, `services.aql_zopedia_gateway`, `services.zopedia_runtime`, `services.pipeline_store`, `services.runtime_policy`, and `services.common/*`. This list belongs in the active architecture map and should stay small.
- `non_hub_internal_edges`: internal edges where neither endpoint is an approved hub.
- `upward_layer_edges`: imports from a lower layer into a higher/product layer, such as `data_access` importing `services`.
- `max_module_out_degree`: largest number of active production modules imported by one module.

Star-topology target:

- More imports should terminate at a few declared spine modules and contracts.
- Peer-to-peer feature imports should fall.
- Lower layers should not import product/service surfaces.
- Hubs must be intentional. Adding a messy module to the hub list is not cleanup unless it is explicitly promoted into a stable contract/spine owner.

Current baseline:

```
hidden_live_read_path_files = 1
raw_model_call_sites_outside_gateway = 25
modules_in_import_cycles = 19
critical_file_excess_100_line_units = 135
production_python_loc_1000_line_units = 108
production_python_file_10_file_units = 15
topology_mess_10_point_units = 71
legacy_root_count = 2

Cleanup Debt Score = 824
```

Topology baseline:

```
internal_import_edges = 498
approved_hub_edge_ratio_percent = 20.7
non_hub_internal_edges = 395
upward_layer_edges = 24
max_module_out_degree = 31

Topology Mess Score = 704
```

Targets:

- Phase 0 target: score script exists and reproduces the baseline.
- Phase 1 target: score below 700 by removing hidden live read-path behavior and the worst upward layer edges.
- Phase 2 target: score below 600 by splitting the Streamlit shell without adding new peer coupling.
- Phase 3 target: score below 425 by breaking production import cycles and improving hub ratio.
- Phase 4 target: score below 300 by collapsing presentation/job/tooling duplication.
- Long-term target: score below 175, with zero hidden live read paths, zero production import cycles, materially smaller active LOC/file count, `approved_hub_edge_ratio_percent >= 60`, and `upward_layer_edges = 0`.

Anti-gaming rules:

- A file split does not count as cleanup unless it removes an ownership problem, import cycle, or forbidden boundary.
- A line-count reduction cannot be claimed if the same logic moved into a new unowned helper.
- A direct model call is not clean because it labels a `surface`; product JSON calls need the gateway or a typed AQL/Zopedia engine mode.
- A UI fallback is not clean if it hides a missing materialized contract.
- Deleting a path requires no active imports, no required job/API/UI use, and a note in the active architecture map or archive.
- Total LOC and file count should go down through deletion, consolidation, or retirement of active runtime code. Moving the same code to more files is a regression unless it removes a higher-weight ownership violation.
- Hub promotion is a design decision, not a metric loophole. A module becomes an approved hub only if it has a stable contract, clear ownership, and fewer responsibilities than the peer graph it replaces.

## Phase 0 - Make Cleanup Measurable

Purpose: make future cleanup hard to fake.

Work:

- Add `scripts/architecture_cleanup_metrics.py`.
- Have it compute the Cleanup Debt Score, Topology Mess Score, hub ratio, non-hub edge count, upward layer edge count, max out-degree, and component counts.
- Add a focused test for the metric logic using small fixture modules.
- Add structural checks for:
  - production import cycles;
  - direct `.generate_json(` calls outside allowed gateway/adapter/test/probe paths;
  - model/search/embedding imports in `data_access` and `presentation`;
  - Streamlit imports outside `app.py`, `views`, and `presentation`;
  - imports from root `src` into the active app.
- Add topology thresholds in warn mode first, then fail mode after the active hub list is reviewed in `ACTIVE_MAP.md`.
- Convert the current direct-call allowlist into a migration ledger with owner, purpose, expiry, and replacement path.

Exit criteria:

- The metric script reproduces the 824 Cleanup Debt Score and 704 Topology Mess Score baselines or explains any drift from concurrent user changes.
- CI or local test commands can fail on new boundary violations.
- Existing violations are recorded as migration debt, not silently accepted.

## Phase 1 - Stop Hidden Live Work In Read Paths

Purpose: protect render/query paths from provider cost, latency, retries, and side effects.

Work:

- Remove `load_llm_client` and `load_embedding_client` usage from `data_access/layer.py`.
- Replace live research fallbacks with one of:
  - materialized read;
  - explicit unavailable contract naming the required job/action;
  - explicit AQL/Zopedia engine action invoked by a caller that asked for live research.
- Ensure `DataAccessLayer` construction cannot silently switch from read-only into live research because of runtime mode.
- Keep cheap deterministic local transforms in `data_access`.
- Keep account/market provider reads only where they are explicitly part of the data contract.

Exit criteria:

- `hidden_live_read_path_files = 0`.
- `data_access` and `presentation` have no model, embedding, search, or gateway imports.
- Streamlit render of Home/Zopedia cannot start hidden provider/model work.

## Phase 2 - Make Streamlit Replaceable

Purpose: keep Streamlit if it is useful, but stop making it the app.

Work:

- Move Home rendering from `app.py` to `views/home.py`.
- Move Zopedia chat/memory/source rendering from `app.py` to `views/zopedia.py`.
- Keep `app.py` responsible for page config, auth/session restore, navigation, route dispatch, and shared view wiring.
- Decide after extraction whether `st.Page`/`st.navigation` improves the shell or whether custom routing is still justified by auth/mobile constraints.
- Preserve current product behavior while moving ownership.

Exit criteria:

- `app.py` is below 2,500 lines for this phase and trending toward 1,500.
- No page module imports private research clients directly.
- Home and Zopedia render from shared data/query/service contracts.

## Phase 3 - Break The AQL/Zopedia/Data Import Cycle

Purpose: make layer ownership enforceable by Python imports, not just philosophy.

Work:

- Extract low-level contracts/protocols for:
  - QueryService request/response;
  - AQL/Zopedia engine input/result;
  - model-call metadata;
  - tool evidence/result;
  - materialized artifact identity.
- Stop `QueryService` from constructing or depending directly on concrete `DataAccessLayer` internals where a protocol is enough.
- Split AQL summarization into pure builders and engine modes so `aql_zopedia_engine` does not import a facade that imports the engine.
- Make `agent_tools` depend on protocols/registries, not engine loading.
- Move company/page summary helpers out of `data_access.layer` ownership.

Exit criteria:

- `modules_in_import_cycles = 0`.
- Import DAG test fails on any new production cycle.
- AQL/Zopedia engine can be imported without importing Streamlit or concrete presentation code.

## Phase 4 - Collapse Presentation Loader Boilerplate

Purpose: remove the second data policy layer.

Work:

- Rebase `presentation/dashboard_loaders.py` on QueryService dataset/chart specs.
- Keep only Streamlit cache/session mechanics in presentation.
- Move ticker profile fallback policy into materialized data contracts or explicit resolvers.
- Remove live public-provider fallback from presentation unless it is an explicit user action with a named contract.

Exit criteria:

- `presentation/dashboard_loaders.py` is below 500 lines.
- Each loader either maps to a QueryService spec or is documented as pure UI state.
- Presentation has no model/search/embedding/provider acquisition ownership.

## Phase 5 - Turn Pipeline Jobs Into Specs

Purpose: keep Azure jobs, reduce job boilerplate and schema drift.

Work:

- Add a `JobSpec` registry with:
  - name;
  - handler;
  - required inputs;
  - output datasets;
  - freshness gates;
  - timeout budget;
  - optional companion artifacts;
  - deployment owner.
- Move dataset upload, manifest writing, and DB metadata writes into a pipeline storage service.
- Move table creation out of runtime job handlers and toward Alembic migrations.
- Keep small callable probes for each job before full scheduled runs.

Exit criteria:

- `pipeline/jobs/main.py` is below 1,800 lines for this phase and trending toward 1,000.
- Adding a new job requires a spec entry and a handler, not more global dispatch plumbing.
- Runtime jobs do not create or mutate schema except through declared migrations.

## Phase 6 - Clean Dependency And Legacy Roots

Purpose: remove stale mental models and make local/dev/prod environments reproducible.

Work:

- Add `streamlit_alpaca_app/pyproject.toml` with runtime deps, dev deps, Python version, and ruff/pytest config.
- Introduce `uv.lock` or another lock file after dependency review.
- Retire or archive root `requirements.txt` so it cannot be mistaken for the active app dependency source.
- Archive root `src/` after confirming no active imports or deployment references.
- Delete cached bytecode under legacy roots.

Exit criteria:

- `legacy_root_count = 0`.
- One documented dependency source exists for the active app.
- Local setup, Docker, and dev deploy use the same dependency contract.

## Phase 7 - Compact Active Documentation

Purpose: keep the project memory useful without treating old plans as current state.

Work:

- Add `documents/architecture/ACTIVE_MAP.md`.
- Track active owners, retired paths, allowed model-call boundaries, job specs, and frontend role.
- Move stale probes/comparisons into archive folders when they stop guiding implementation.
- In new plans, label state as `implemented`, `proven in product`, or `recommended next`.

Exit criteria:

- A future agent can identify active architecture without reading every historical plan.
- Old docs are preserved when useful, but they do not compete with current ownership.

## Verification Rhythm

For each phase:

1. Run the cleanup metric before editing.
2. Make the smallest source-level change that removes an ownership problem.
3. Run structural checks.
4. Run the smallest representative product path:
   - exact Streamlit page render for UI changes;
   - exact API route for API changes;
   - callable job handler or fixture-backed job run for pipeline changes;
   - live qualitative review for LLM narrative changes.
5. Update the plan with actual score movement and any scope changes.

Do not deploy to production without explicit permission. Dev deploy is appropriate only after the relevant local path is proven.

## Frontend Decision Gate

The 2026-10-02 isolated Next.js vertical slice passed the original gate for Home and
Zopedia without authorizing a wholesale rewrite. Streamlit stays active while route
ownership is migrated deliberately. The slice proved:

- a typed FastAPI Home view model with no hidden live research;
- a durable user-owned Zopedia run and event journal independent of Streamlit;
- browser-safe authentication, CSRF protection, and a same-origin BFF;
- a private one-run research worker that reuses the AQL/Zopedia harness;
- the existing visual identity in a responsive production-mode Next.js build.

The remaining gate applies per route rather than to the framework decision globally:

- Streamlit pages are thin clients over stable service/API contracts.
- Home and Zopedia no longer depend on `app.py` internals.
- Hidden live research is gone from render/read paths.
- Import cycles are gone.
- The remaining pain is clearly frontend capability: interaction model, mobile polish, multi-user UX, auth/account workflow, or client-side performance.

The 2026-10-02 authentication incident reinforced two cleanup requirements:

- availability failures in the shared account store must remain distinct from invalid credentials at every surface;
- repository reads must not run schema DDL. Schema readiness may be checked cheaply at runtime, but migrations own schema evolution and database sessions need bounded lock/statement lifetimes.
- account/admin/orchestration state must move to a control-plane PostgreSQL boundary so
  analytical job saturation cannot consume sign-in capacity. The staged design and current
  Central US cost envelope are recorded in
  `documents/architecture/overall/CONTROL_DATA_PLANE_DATABASE_ISOLATION_2026-10-02.md`.

Do not port a tangled route. First establish its service ownership and typed contract,
then prove its Next.js replacement beside Streamlit, and only then retire the old route.

The 2026-10-06 research-depth increment follows that boundary: managed AQL-selected
datapoints, materialized financial trends, simultaneous investigator panels, trailing
macro smoothing, and a read-only linked-wiki browser. Local review and release state are
tracked in `documents/plans/WORKBENCH_RESEARCH_DEPTH_2026-10-06.md`.

## First Implementation Order

1. Add and baseline `architecture_cleanup_metrics.py`.
2. Fix `data_access/layer.py` live model/embedding ownership.
3. Move Home out of `app.py`.
4. Move Zopedia out of `app.py`.
5. Break the 17-module cycle through contracts/protocols.
6. Collapse presentation loaders onto QueryService.
7. Introduce `JobSpec`.
8. Consolidate dependencies and archive legacy roots.
