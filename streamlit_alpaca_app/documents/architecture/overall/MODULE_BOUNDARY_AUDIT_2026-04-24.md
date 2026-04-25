# Module Boundary Audit (2026-04-24)

This audit summarizes the first parallel boundary review for independent modules.

## AQL And SAA

Current shape:

- AQL owns research planning, evidence collection, extraction, hypothesis verification, synthesis, summaries, and chat logs.
- SAA owns retained source documents, evidence chunks, metadata, search, retrieval, bootstrap, and document loading.

Boundary issues:

- AQL imported SAA through `services.saa.storage` instead of the package interface.
- SAA did not export all existing prepare functions from `services.saa`.
- Agents still have one private SAA DB connection import for retained-evidence write-back.
- Attention imports AQL private helpers in a few places.
- Data access imports an AQL evidence helper.

Executed in this pass:

- Exported SAA prepare functions from `services.saa`.
- Retargeted AQL imports to `services.saa`.
- Retargeted public SAA imports in pipeline and agent search paths.
- Added `persist_agent_research_evidence` so agent write-back no longer imports SAA storage internals.

Next steps:

1. Move data-access JSON parsing away from AQL evidence internals.
2. Retire the `attention_agentic.py` compatibility shim after tests and callers use AQL directly.
3. Add import-boundary tests once the remaining compatibility shim is gone.

## Attention And Market Data

Current shape:

- Attention owns market activity detection, event grouping, signal graph, surface text, materialized payloads, and ticker snapshots.
- Market Data owns price, volume, fundamentals, options, macro, ownership, anomaly, and signal extraction inputs.

Boundary issues:

- Some compute modules import service modules.
- Market service code mixes vendor fetches, pure transforms, dependency graph lookups, and fallback behavior.
- Attention ticker snapshots assemble company, taxonomy, fundamentals, and market profile inputs directly.
- Attention compatibility shims still expose AQL internals.

Executed in this pass:

- Added `services.market_data` as a stable namespace while legacy files remain in place.
- Added `documents/architecture/market_data/README.md`.
- Added `services.market_activity_shared` and retargeted Attention graph, macro, surface, and live-research helpers away from AQL private imports.

Next steps:

1. Split compute functions away from service/client fetches.
2. Retarget AQL internals to the neutral shared helper module to eliminate duplicated helper definitions.
3. Retarget callers to `services.market_data` before moving files.

## Agents, UI, And Data Pipelines

Current shape:

- Agents own omnibar resolution, agent run loop, tool catalog, and chat/search research helpers.
- UI owns Streamlit rendering and user flow.
- Pipelines own scheduled jobs, materialized datasets, cache/blob writes, and job status.

Boundary issues:

- UI still contains business logic and duplicate omnibar matching.
- Agent research mixes routing, retained context, live search, page browsing, and LLM interpretation.
- Agent tools contain business logic that should live behind QueryService or module APIs.
- Pipelines import many service modules directly; this is acceptable for orchestration but should stay out of UI.
- Query registry has special-case implementation logic.

Executed in this pass:

- Added `services.agents` as a stable namespace while legacy files remain in place.
- Added `documents/architecture/UI/README.md`.

Next steps:

1. Retarget UI to `services.omnibar.resolve_omnibar` and remove duplicate resolver logic.
2. Split agent tools into registry/dispatch plus module-owned tool implementations.
3. Keep `query_registry.py` declarative and move special cases into data access methods.

## Boundary Refactor Update

Executed after the first audit:

- Added `services.common` for shared contracts, shared market-activity helpers, and hypothesis verification.
- Moved agent chat-log persistence from `services.aql.chat_log` to `services.agents.chat_log`.
- Moved agent scratchpad state from `services.aql.scratchpad` to `services.agents.scratchpad`.
- Retargeted agent tools and omnibar persistence to `services.agents`.
- Exposed anomaly detection, attention-candidate construction, correlation phase shifts, momentum profiles, and commodity regimes through `services.market_data`.
- Retargeted pipeline and agent anomaly code to `services.market_data`.
- Retargeted Attention shared-helper imports to `services.common.market_activity`.
- Retargeted Attention macro schema imports to `services.common.contracts`.
- Added `tests/test_module_boundaries.py` to block the worst legacy import paths.

Remaining intentional coupling:

- Attention still calls AQL through `services.aql` for research/search/synthesis workflows.
- AQL still exposes compatibility names for old callers and tests.
- Legacy shim modules remain to avoid breaking old import paths, but new imports should target module interfaces.
