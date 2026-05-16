# Studio Wiki and Graph Explorer Architecture

## 1. Purpose
This document explains the current architecture for Studio Wiki retrieval, maintenance, and graph visualization, including the recent graph-explore UX work. It is intended to help another engineer or agent:

- understand responsibilities across frontend, API, manager, and engine layers
- re-implement the same behavior from scratch
- reason about tradeoffs and extension points
- avoid regressions we already hit and fixed

This document is based on:

- `updates.md` (phase and addendum history)
- current implementation in:
  - `studio/frontend/src/components/wiki-data-dialog.tsx`
  - `studio/backend/routes/inference.py`
  - `studio/backend/core/wiki/manager.py`
  - `studio/backend/core/wiki/engine.py`

## 2. Architecture at a Glance

```mermaid
flowchart LR
    UI[Wiki Data Dialog\nReact + ReactFlow] --> API[/api/inference/wiki/data/graph\n/wiki/delete/preview\n/wiki/delete/apply]
    API --> MANAGER[WikiManager]
    MANAGER --> ENGINE[WikiEngine]
    ENGINE --> VAULT[Wiki Vault Filesystem\nraw + wiki pages + index + log]

    ENGINE --> LINT[Lint/Enrich/Merge Maintenance]
    LINT --> VAULT
```

## 3. Layer Responsibilities

### 3.1 Frontend layer (Dialog, local graph interactions)
File: `studio/frontend/src/components/wiki-data-dialog.tsx`

Responsibilities:

- fetches graph snapshot (`nodes`, `edges`) from backend
- renders node-link graph using ReactFlow
- supports two interaction modes:
  - Delete Queue mode (destructive queueing and apply)
  - Explore mode (non-destructive topology exploration)
- owns all local focus/filter/layout logic
- calls delete preview/apply APIs for destructive workflows

Important architecture decision:

- graph exploration (1-hop/2-hop, seed set, hide unfocused) is computed client-side over the fetched graph snapshot, not server-side.

### 3.2 API layer (route contracts)
File: `studio/backend/routes/inference.py`

Responsibilities:

- route auth and HTTP contracts
- transform untyped engine dictionaries into typed response models
- expose key endpoints used by graph workflow:
  - `GET /api/inference/wiki/data/graph?include_analysis=true|false`
  - `POST /api/inference/wiki/delete/preview`
  - `POST /api/inference/wiki/delete/apply`

### 3.3 Manager layer (facade)
File: `studio/backend/core/wiki/manager.py`

Responsibilities:

- thin facade over engine behavior for routes
- current graph and delete flows are pass-through

Key methods:

- `get_wiki_data_graph(include_analysis: bool)`
- `delete_wiki_entries(...)`

### 3.4 Engine layer (source of truth)
File: `studio/backend/core/wiki/engine.py`

Responsibilities:

- vault page discovery
- graph building from wiki links
- delete planning/apply behavior
- broader wiki maintenance and retrieval logic documented in `updates.md`

Graph-specific behavior:

- `get_wiki_data_graph(include_analysis)`
  - selects allowed kinds (`source`, `entity`, `concept`, optional `analysis`)
  - builds outbound/inbound relationships via wiki links
  - emits normalized node and edge payload
- `_build_link_graph(pages)`
  - parses all `[[...]]` links in page text
  - computes inbound/outbound maps and broken links
- `_all_wiki_pages()`
  - excludes `.archive` subtree

## 4. Data Model and Contracts

### 4.1 Node kinds
Canonical kinds for graph UI:

- `source`
- `analysis`
- `entity`
- `concept`

Kinds are inferred from path prefixes (`sources/`, `analysis/`, `entities/`, `concepts/`).

### 4.2 Graph response contract
Returned by `GET /api/inference/wiki/data/graph`:

- `nodes[]`
  - `id`
  - `kind`
  - `label`
  - `inbound_links`
  - `outbound_links`
- `edges[]`
  - `id`
  - `source`
  - `target`

Node IDs are page-relative paths without `.md` suffix.

### 4.3 Delete preview/apply contract
Used by dialog side panel:

- accepts `entry_type` + `entries[]`
- returns planned/archived/deleted page lists and counts
- supports dry-run preview and apply modes

## 5. Graph Construction Semantics (Critical)

### 5.1 What counts as an edge
An edge exists when source page text contains a valid wiki link `[[target]]` where target resolves to another included page.

### 5.2 Are enrichment links included
Yes, with caveats:

- enrichment links are included when they are expressed as `[[...]]` in included pages
- if `include_analysis=false`, analysis pages are excluded from node/edge set, so analysis enrichment links are not surfaced
- non-wikilink URLs (plain markdown URLs) do not create graph edges
- links to targets outside included graph pages are tracked as broken and excluded from returned edge list

### 5.3 Archive behavior
Archived pages under `.archive` are excluded from `_all_wiki_pages()`, so they cannot re-enter active graph/retrieval pipelines.

## 6. Explore and Delete Mode Separation

A key architecture outcome from this cycle is strict separation of destructive vs exploratory state.

- Delete Queue mode:
  - selection drives delete preview/apply
  - no exploration side effects
- Explore mode:
  - selection drives neighborhood focus only
  - no delete side effects

This split prevents accidental destructive operations during graph exploration.

## 7. Frontend Derived-Data Pipeline

The dialog computes graph data in stages:

1. `graphNodes` and `graphEdges` from backend
2. kind and analysis filters
3. `searchFilteredGraphNodes`
4. focus set from seed nodes and hop depth
5. optional hide-unfocused projection (`displayGraphNodes`)
6. edge projection over displayed node IDs
7. layout projection and ReactFlow rendering

Critical design rule:

- use search-filtered universe for selection/list operations
- use display-filtered subset only for rendering

This rule fixed a major bug where hidden nodes could not be selected.

## 8. Key Problems Encountered and Architectural Fixes

### 8.1 Problem: explore selection was coupled to delete queue
Symptoms:

- selecting nodes for exploration polluted destructive queue

Fix:

- split state into `selectedNodeIds` (delete) and `exploreNodeIds` (explore)
- add explicit `interactionMode`

### 8.2 Problem: requested 1-hop behavior was not strict
Symptoms:

- larger connected component was highlighted

Fix:

- implement depth-limited BFS from explore seed set
- expose hop depth switch (1-hop, 2-hop)

### 8.3 Problem: large graphs remained visually overwhelming
Symptoms:

- opacity-only dimming was insufficient

Fix:

- add hide-unfocused mode that removes non-focused nodes from rendered graph
- keep list/search operating over broader filtered universe

### 8.4 Problem: hidden-node workflows became unselectable
Symptoms:

- when nodes were hidden, users could not easily add them back

Fix:

- add dedicated explore-seed search over kind-filtered nodes
- add keyboard and row-click interactions
- seed add action forces explore mode, enables hide-unfocused, and clears restrictive search

### 8.5 Problem: backend semantics ambiguity (enrichment links)
Symptoms:

- uncertainty whether enrichment links contribute to graph

Fix:

- trace and document engine behavior in this architecture doc
- clarified link type and include-analysis caveats

## 9. Broader Wiki Platform Architecture Context (from updates.md)

The graph workflow runs inside a larger wiki platform with these stable pillars:

- ingestion and pending-raw detection
- retrieval and context injection for chat
- lint/enrich/retry-fallback maintenance
- archive and merge-maintenance workflows
- graphify interoperability (lint insights and export)
- runtime-env-driven behavior controls

Recent hardening themes:

- semantic-first ranking and enrichment selection
- defensive fallbacks when LLM output is invalid
- context budget controls to mitigate degeneration
- duplicate and stale content handling
- operational diagnostics and trace endpoints

## 10. Operational and Config Considerations

### 10.1 Performance controls in UI

- force layout node limit with dagre fallback for large graphs
- filtered list rendering cap (first 200)
- candidate cap in seed-add list (first 24)

### 10.2 Safety controls in backend

- archive exclusion from active pages
- delete preview before apply
- normalized model conversion at route layer

### 10.3 Runtime config surfaces

The system exposes many `UNSLOTH_*` controls documented in `updates.md` for retrieval size, enrichment behavior, reranking, maintenance cadence, and compaction.

## 11. Re-Implementation Blueprint

If implementing this architecture in another stack, keep these invariants:

1. Preserve mode-separated selection state (explore vs destructive).
2. Build focus neighborhoods with explicit depth-limited traversal.
3. Separate selectable universe from rendered subset.
4. Keep delete operations preview-first and grouped by kind.
5. Build graph edges from canonical wikilink syntax and normalize targets.
6. Exclude archived content from active graph/retrieval corpus.
7. Keep route contracts stable and typed at boundaries.

## 12. Validation and Regression Strategy

Minimum validation checklist:

- API graph returns stable node kinds and edge IDs
- include-analysis toggle removes only analysis nodes and dependent edges
- 1-hop and 2-hop focus sets are deterministic from same seeds
- hide-unfocused does not break selection/list operations
- adding seed from hidden-search immediately affects focus
- delete preview/apply remain isolated to delete mode
- archived pages do not appear in graph

Recommended tests:

- backend unit tests for graph construction and include-analysis behavior
- frontend tests for mode-switch state retention and focus calculations
- integration tests for delete preview/apply from selected node sets

## 13. Teaching Notes

When teaching this architecture, emphasize these concepts first:

- state-domain separation (explore state vs destructive state)
- data pipeline layering (raw graph -> filtered universe -> rendered projection)
- deterministic graph semantics from file links
- explicit guardrails around destructive operations

A good teaching sequence is:

1. start with backend graph contract
2. show frontend derived-data pipeline
3. walk through one user flow per mode
4. explain historical bugs and why each invariant exists
5. map extension ideas to the correct layer

## 14. Extension Opportunities

Near-term enhancements:

- persisted explore sessions (save seed sets/hop settings)
- path tracing between selected nodes
- server-side graph paging/windowing for very large corpora
- explicit broken-link overlay layer in graph UI
- API-level graph metrics endpoint (connected components, density, centrality)

Long-term:

- typed graph domain module shared between frontend/backend
- automated architecture conformance tests for mode invariants
- observability dashboards for graph-size growth and maintenance quality
