# Independent Modules Plan (2026-04-24)

## Goal

Turn the current service layout into clean independent modules with explicit contracts. The immediate goal is not a risky file move. The first goal is to define ownership, add stable import seams, and migrate callers in small steps.

## Target Shape

| Module | Owns | May Call | Must Not Own |
| --- | --- | --- | --- |
| SAA | retained documents, chunks, provenance, storage and retrieval | secrets, embedding client, data store | query planning, narrative generation, UI rendering |
| AQL | query planning, evidence collection, claim extraction, hypothesis checks, synthesis | SAA, web/page research, market data APIs, LLM APIs | UI rendering, scheduled-job orchestration, raw market transforms |
| Attention | market activity detection, event grouping, signal graph, homepage-ready attention payloads | market data, AQL public APIs, data access | web research internals, retained-evidence storage, UI widgets |
| Market Data | prices, volume, fundamentals, options, macro, ownership, anomalies, signal extraction | vendor clients, compute transforms, data access | attention-specific event writing, agent planning, UI rendering |
| Agents / Chat + Search | planning, tool routing, conversations, omnibar workflows | AQL, SAA retrieval, data access, market data, page research | module-specific business logic duplicated from tools |
| Data Pipelines | ingestion, scheduled jobs, materialization, manifests, cache/blob writes | all producer modules through public APIs | UI rendering, ad hoc query planning |
| UI | Streamlit pages, controls, charts, admin screens, user flows | public service APIs, data access, presentation helpers | business rules, ingestion, retained evidence storage |

## Dependency Direction

Preferred direction:

`UI -> Agents -> AQL -> SAA`

`UI -> Data Access -> Market Data / Attention`

`Pipelines -> public module APIs -> materialized datasets`

`Attention -> Market Data + AQL public APIs`

Disallowed direction:

- SAA importing AQL, Attention, Agents, UI, or pipeline jobs.
- AQL importing UI or pipeline jobs.
- Attention importing AQL private helpers.
- Agents importing AQL or Attention private helpers.
- UI importing module internals instead of public seams.

## Public Interfaces To Stabilize

### SAA

- `persist_retained_source_documents`
- `persist_retained_evidence_chunks`
- `search_retained_documents`
- `search_retained_evidence_chunks`
- `load_retained_document`
- `load_retained_document_metadata`

### AQL

- `build_bottom_up_attention_artifacts`
- `build_bottom_up_attention_home`
- `build_bottom_up_attention_bundle`
- `build_attention_agentic_summary`
- `build_attention_agentic_summary_with_trace`
- `verify_hypothesis`
- `search_symbol_news_payload`

### Attention

- attention payload builders
- market event grouping
- signal graph builders
- surface text helpers
- materialized payload serialization

### Market Data

- universe and listing APIs
- market/fundamental/options loaders
- anomaly and signal extraction APIs
- macro dashboard loaders

### Agents

- omnibar resolution
- agent run loop
- tool catalog
- tool invocation
- retained/live research helpers

### Data Pipelines

- scheduled job entrypoints
- dataset write/read contract
- manifest and cache policy
- materialized dataset names

### UI

- page routing
- presentation helpers
- chart rendering
- admin controls

## Current Implementation Status

Status after the boundary refactor:

- `services.common` owns shared contracts, shared market-activity helpers, and shared hypothesis verification.
- `services.agents` owns agent chat-log persistence and scratchpad state.
- `services.market_data` owns the import interface for anomaly detection, attention candidates, correlation phase shifts, momentum profiles, and commodity regimes.
- `services.saa` remains the interface for retained evidence persistence and retrieval.
- Attention can still call AQL, but active imports now go through the AQL package interface rather than AQL private modules.
- Compatibility shims remain for old paths such as `services.aql.chat_log`, `services.aql.scratchpad`, and `services.market_activity_shared`; new code must not import them.

Boundary enforcement:

- `tests/test_module_boundaries.py` blocks new imports from AQL chat/scratchpad internals, old market-activity shims, direct `compute.anomalies` imports outside `services.market_data`, and direct AQL hypothesis-verification imports.

## Execution Phases

### Phase 0: Contracts And Namespaces

Status: complete.

- Add durable architecture docs for module ownership.
- Add namespace packages for modules that still live in legacy files.
- Keep existing imports working.
- Add tests that compile new namespace packages.

### Phase 1: Stop Private Cross-Imports

Status: mostly complete for the high-risk paths.

- Move shared helpers used by AQL and Attention into neutral modules.
- Replace imports from `services.aql._shared` in Attention with public helper modules.
- Replace imports from `services.attention_*` in AQL with public Attention or compute APIs.
- Keep compatibility shims during migration.

### Phase 2: Retarget Callers

- Retarget pipelines from legacy Attention shims to AQL or Attention public namespaces.
- Retarget agents from legacy direct helpers to AQL/Attention public namespaces.
- Retarget UI from service internals to public namespaces and presentation helpers.

### Phase 3: Move Files Behind Namespaces

- Move Attention files under `services/attention/`.
- Move agent files under `services/agents/`.
- Move market data files under `services/market_data/`.
- Keep thin compatibility modules for old import paths.

### Phase 4: Enforce Boundaries

Status: started.

- Add import-boundary tests.
- Add docs checks for public APIs.
- Fail CI on new private cross-module imports.

## First Implementation Slice

This pass implements Phase 0:

- Creates module ownership docs.
- Adds `services.market_data` and `services.agents` namespace packages.
- Updates architecture indexes so future sessions know where module work belongs.
- Leaves runtime behavior unchanged.

## Reliability Notes

The worktree already contains many unrelated changes. Broad file moves would be high-risk because they can collide with active edits and make review harder. The safe path is to add public seams first, migrate imports second, and move files last.
