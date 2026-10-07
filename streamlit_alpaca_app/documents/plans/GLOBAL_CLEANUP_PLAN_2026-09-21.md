# Global Cleanup Plan - 2026-09-21

Status: in progress. Grounded in `documents/architecture/overall/GLOBAL_OPTIMIZATION_AUDIT_2026-09-21.md`, the 2026-09-21 static probes, and the 2026-10-02 Next.js vertical-slice deployment.

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
