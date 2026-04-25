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

Next steps:

1. Replace the remaining private SAA DB connection import with a public write-back gateway.
2. Move shared helper functions used by AQL and Attention into neutral public modules.
3. Move data-access JSON parsing away from AQL evidence internals.

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

Next steps:

1. Split compute functions away from service/client fetches.
2. Move Attention/AQL shared helpers into neutral public modules.
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
