# Global Optimization Audit - 2026-09-21

Status: architecture evaluation only. No implementation files were changed.

## Rubric

I evaluated the repo against the current project philosophy:

- one AQL/Zopedia spine, thin UI/API/job surfaces;
- fix source contracts instead of patching render/prose symptoms;
- keep render paths cheap and read-only;
- use LLMs for narrative/planning but code for typed contracts;
- reduce owners, paths, wrappers, stale fallbacks, and boilerplate.

I also checked current framework documentation where tool choice matters:

- [Streamlit](https://docs.streamlit.io/develop/concepts/multipage-apps/page-and-navigation) documents `st.Page` and `st.navigation` as the preferred multipage API, with the entrypoint acting as a shared router/frame around pages.
- [Pydantic](https://pydantic.dev/docs/validation/latest/concepts/models/) models are designed for typed schema validation, serialization, and JSON Schema generation.
- [Alembic](https://alembic.sqlalchemy.org/en/latest/) is the SQLAlchemy database migration tool.
- [uv](https://docs.astral.sh/uv/concepts/projects/config/) projects centralize Python version, dependencies, scripts, lock/sync, and project configuration in `pyproject.toml`.

## Scope Inspected

- `streamlit_alpaca_app/documents/philosophy.md`, `learnings.md`, `mistakes.md`, and AQL/Zopedia guardrails.
- Python surface: 201 files, about 133,287 lines.
- Docs surface: 203 Markdown files, about 39,255 lines.
- Representative modules: `app.py`, `data_access/layer.py`, `data_access/query_service.py`, `presentation/dashboard_loaders.py`, `services/aql_zopedia_engine.py`, `services/zopedia_agent.py`, `services/agent_tools.py`, `services/aql/__init__.py`, `pipeline/jobs/main.py`, `pipeline/jobs/attention_home_build.py`.
- Static import graph, direct LLM call scan, dependency files, and legacy root tree.

The worktree is already heavily dirty, so this audit avoids implementation edits.

## Executive Read

The biggest optimization is not a new framework. It is reducing the number of live ownership paths.

The project has already moved toward the right architecture: shared DAL, QueryService, AQL/Zopedia engine, gateway telemetry, materialized datasets, and page modules. The remaining complexity comes from partially completed migrations:

1. The AQL/Zopedia gateway is real, but direct model calls still exist and the boundary tests do not scan every production layer.
2. `data_access.layer` is both a read layer and a live agentic/research runner.
3. `app.py` is still the shell plus Home plus Zopedia plus routing plus auth plus rendering.
4. The AQL/Zopedia/DataAccess/tooling modules form a large import cycle, which makes the intended layers advisory instead of enforceable.
5. Pipeline jobs, DB schema creation, dataset persistence, and job dispatch are concentrated in one large script.
6. Old root dependencies and the disconnected `src/` tree keep legacy mental models alive.

## Priority Findings

### 1. Finish The AQL/Zopedia Boundary

Evidence:

- The guardrail says every research-grade model call must go through the AQL/Zopedia gateway, not a raw model client.
- `tests/test_zopedia_native_llm_boundary.py` scans only `app.py`, `services`, and `pipeline`, and still allowlists many raw `generate_json` call sites.
- `data_access/layer.py` imports `load_llm_client` and `load_embedding_client`, then calls them inside resolver paths for news and attention artifacts.

Why it matters:

The project can pass the current boundary tests while a read/data path still starts live model work. That is the highest-risk mismatch with the philosophy because it reintroduces hidden research on render/query paths.

Recommended fix:

- Define model calls as one of three categories: `engine_run`, `gateway_utility`, or `forbidden`.
- Expand direct-call tests to scan all production roots: `app.py`, `api`, `compute`, `data_access`, `pipeline`, `presentation`, `services`, `views`, and non-probe scripts.
- Replace raw direct calls in `data_access` with materialized reads or explicit AQL/Zopedia engine requests.
- Turn the current direct-call allowlist into a migration ledger with owner, surface, purpose, and expiry.
- Treat `load_aql_zopedia_llm_client(surface=...)` as labeling only. Product JSON calls should use `generate_json_via_aql_zopedia_gateway(...)` or a typed engine mode.

### 2. Break The 16-Module Import Cycle

Static analysis found one large strongly connected component:

`data_access.layer`, `data_access.query_registry`, `data_access.query_service`, `services.agent_tools`, `services.aql.critique`, `services.aql.summarizer`, `services.aql_zopedia_engine`, `services.attention_live_research`, `services.attention_materialized`, `services.attention_ticker_snapshots`, `services.company`, `services.company_baseline`, `services.page_agentic_summary`, `services.zopedia_agent`, `services.zopedia_research`, `services.zopedia_resolver`.

High-signal cycle edges:

- `data_access.layer` imports service research/presentation helpers.
- `zopedia_agent` imports `QueryService`, `agent_tools`, and `aql_zopedia_engine`.
- `agent_tools` imports `QueryService` and `aql_zopedia_engine`.
- `aql.summarizer` imports `aql_zopedia_engine`, while the engine imports AQL summarizer functions.
- `zopedia_research` imports `data_access.layer` and the engine.

Why it matters:

This cycle makes it hard to enforce "data access below agent; engine above tools; UI above data". It also encourages local imports and wrappers because no layer can be cleanly depended on.

Recommended fix:

- Extract stable contracts into a low-level package, e.g. `services/contracts/` or `services/aql/contracts.py`.
- Keep `QueryService` independent of `DataAccessLayer` construction. Pass a service/protocol instance in from the app/API/job edge.
- Split AQL summarization into pure builders and engine modes so the engine does not import the facade that imports the engine.
- Make `agent_tools` depend on protocols and registries, not on engine loading.
- Add an import DAG test that fails on cycles across production modules.

### 3. Split `app.py` Into A Real Shell

Evidence:

- `app.py` is about 7,757 lines and has about 220 functions.
- It imports data access, presentation, auth, market services, knowledge graph, SAA, LLM prompt admin, AQL engine, Zopedia presentation, and multiple page/service helpers.
- Page extraction exists for Portfolio, Admin, Trading Agent, Broad Economy, Market Explorer, Stock Investigator, and Options.
- Home v3 and Zopedia chat remain embedded in `app.py`.

Why it matters:

The app shell is still an integration magnet. Every Home/Zopedia tweak risks auth/nav/mobile/sidebar behavior, and every shell tweak sees too much product code.

Recommended fix:

- Move Home into `views/home.py`.
- Move Zopedia chat/memory/source UI into `views/zopedia.py`.
- Keep `app.py` responsible for page config, auth, session restore, nav, layout selection, shared providers, and route dispatch only.
- Consider Streamlit `st.Page`/`st.navigation` after the split. It fits the "entrypoint as frame around pages" model, but custom mobile/auth routing may justify retaining a custom nav. The point is page ownership, not a cosmetic nav rewrite.

### 4. Make Data Access Read-Only By Default

Evidence:

- `DataAccessLayer` has `materialized_only`, but non-materialized paths can still build live attention artifacts with LLM clients, embeddings, search clients, and `build_bottom_up_attention_artifacts`.
- Presentation creates the DAL with `materialized_only=_presentation_layer_only()`, which depends on runtime mode rather than a hard architectural rule.

Why it matters:

The DAL should be the stable read/compute contract for UI/API/agent tools. If it can start agentic work, every reader has to reason about provider cost, timeouts, retries, and evidence ownership.

Recommended fix:

- Keep `DataAccessLayer` to materialized-first reads, deterministic local compute, and cheap live market/account fetches where explicitly allowed.
- Move agentic attention bundle construction into scheduled jobs or explicit AQL/Zopedia engine actions.
- Make agentic/live resolvers return an unavailable contract with a named required action instead of launching hidden research.
- Add a test: `data_access/*` must not import `services.llm`, `load_aql_zopedia_llm_client`, search client loaders, or gateway internals.

### 5. Collapse Presentation Loader Boilerplate

Evidence:

- `presentation/dashboard_loaders.py` wraps many DAL methods manually and owns Streamlit session refresh state.
- It also contains live public price-history fallback and ticker snapshot profile assembly.

Why it matters:

This layer duplicates `QueryService` routing and grows product-specific fallback logic. It should be a thin Streamlit cache/view adapter, not a second data policy engine.

Recommended fix:

- Rebase loaders on `QueryService` dataset/chart names where possible.
- Keep Streamlit cache keys at the presentation edge, but move ticker profile fallback policy into a data contract or materialized snapshot.
- Remove public-provider fallback from presentation unless it is an explicit user action or a documented public-data resolver.

### 6. Turn Pipeline Jobs Into Specs

Evidence:

- `pipeline/jobs/main.py` is about 3,138 lines.
- It owns job dispatch, DB bootstrap DDL, dataset persistence, source refresh, provider setup, and many job handlers.
- Dispatch is a local dict keyed by `PIPELINE_JOB_NAME`.

Why it matters:

The current shape makes every new job add more import surface and environment parsing. It also keeps schema evolution in runtime code.

Recommended fix:

- Keep Azure Container Apps jobs for now. A new orchestrator would be premature unless backfills/dependency scheduling become the bottleneck.
- Introduce a `JobSpec` registry with name, handler, required inputs, output datasets, freshness gates, timeout budget, optional companions, and deploy owner.
- Move dataset upload/manifest/DB metadata into a pipeline storage service.
- Move DB table creation to migrations. Alembic is the right tool once the repo already has Postgres tables across auth, pipeline, memory, health, and chat.

### 7. Type The Boundary Contracts

The repo is schema-heavy but still dict-heavy: model-call records, engine results, materialized rows, job progress, chat messages, tool payloads, memory mutations, and page summaries often travel as `dict[str, object]`.

Recommended fix:

- Add Pydantic only at boundaries, not everywhere.
- First targets: AQL/Zopedia engine result, gateway call metadata, job progress event, materialized summary row, QueryService request/response, memory mutation request/result.
- Keep internal pandas transforms as pandas/dataclasses where that is simpler.

### 8. Clean Dependency And Repo Shape

Evidence:

- Root `requirements.txt` contains Flask, Dash, Robinhood-era dependencies.
- Active app requirements live under `streamlit_alpaca_app/requirements.txt`.
- There is no `pyproject.toml`, formatter/linter config, or CI config in the inspected root.
- `src/` has older notebooks/modules and no imports from the active Streamlit app.

Recommended fix:

- Archive or remove the disconnected `src/` tree after a human check.
- Delete or rename the root `requirements.txt` so it cannot be mistaken for the active runtime.
- Move dependency/tooling config into `streamlit_alpaca_app/pyproject.toml`, with dev extras for pytest/httpx/ruff and a declared Python version.
- Use `uv` or another lock/sync tool to produce reproducible local, Docker, and CI environments. uv is a good fit because the repo already needs project-level dependency and Python-version discipline.

### 9. Make Boundary Tests Structural, Not Incident-Only

Existing tests encode many hard-won incidents, which is good. The gap is global architecture coverage.

Recommended new structural tests:

- No production import cycles across declared layers.
- No direct model client loading outside `services/zopedia_runtime.py` and `services/aql_zopedia_engine.py`.
- No raw `.generate_json(` outside gateway/provider adapter/tests unless listed in a migration ledger.
- No LLM/search/embedding imports in `data_access` or `presentation`.
- No Streamlit imports outside `app.py`, `views`, and `presentation`.
- No root `src` import into active app.
- QueryService capability registry and dashboard loaders do not drift: each loader must map to a dataset/chart spec or be explicitly local UI state.

### 10. Compact Documentation Into Living Maps

Docs are useful but numerous. The risk is not too much writing; it is stale active status.

Recommended fix:

- Keep `philosophy.md`, `learnings.md`, and `mistakes.md`.
- Add a generated or manually maintained `documents/architecture/ACTIVE_MAP.md` with current owners and retired paths.
- Move old probes/comparisons into an archive folder when they stop guiding active work.
- In plan docs, separate "implemented", "proven in product", and "recommended next" so a future agent does not treat ambition as current state.

## Suggested Sequence

1. Make the boundary measurable.
   - Add full-root static scans for direct LLM calls, model loaders, Streamlit imports, data-access forbidden imports, and import cycles.
   - Do not migrate everything yet. First make the remaining exceptions visible.

2. Stop hidden research in read paths.
   - Remove direct `load_llm_client`/`load_embedding_client` usage from `data_access.layer`.
   - Return materialized/unavailable contracts unless an explicit engine action is requested.

3. Split the shell.
   - Move Home and Zopedia out of `app.py`.
   - Keep app shell to auth/nav/session/layout/provider wiring.

4. Break the import cycle.
   - Extract low-level contracts and protocols.
   - Untangle engine/summarizer/tool/query dependencies.

5. Spec the jobs.
   - Add `JobSpec`.
   - Move persistence and schema migration ownership out of `pipeline/jobs/main.py`.

6. Clean repo/tooling.
   - Archive `src/`.
   - Consolidate requirements into `pyproject.toml`.
   - Add ruff/static boundary checks to dev flow.

## Keep / Do Not Add Yet

Keep:

- Streamlit for this internal intelligence workspace.
- FastAPI for external clients/API/MCP-like access.
- Azure Container Apps jobs for scheduled materialization.
- pandas/pyarrow for current materialized frame workflows.
- The provider-neutral JSON LLM adapter and app-owned session/tool/evidence state.

Do not add yet:

- A hosted agent SDK that owns sessions/tools/memory. It conflicts with the project philosophy.
- Dagster/Prefect/Airflow before the lighter `JobSpec` registry proves insufficient.
- A frontend rewrite. The worst complexity is ownership, not Streamlit itself.
- More feature-local helpers for research quality. Fix the AQL/Zopedia engine and acquisition spine.

## Deletion / Retirement Candidates

- Root `requirements.txt` after replacing it with a clear project config.
- Disconnected `src/` tree, or move to `archive/legacy_src/`.
- Underscored internals exported from `services/aql/__init__.py`.
- Direct `generate_json` allowlist entries that are product analysis rather than gateway internals or temporary utility calls.
- Presentation fallbacks that fetch public market data or synthesize profile state outside the shared data/query contract.

## Bottom Line

The project is not suffering from a lack of ambition or tooling. It is suffering from partially completed consolidation. The globally optimal move is to make the intended architecture mechanically enforceable, then delete the paths that violate it.
