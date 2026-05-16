# Entity Extraction System

**Date:** 2026-04-25

## What Changed

Added a first production-shaped entity extraction/linking system:

- `services/entity_extraction.py`
- `tests/test_entity_extraction.py`

The system extracts typed mentions from feed text and metadata, links high-confidence mentions to knowledge-graph nodes, and leaves unlinked mentions available for add-node proposals.

This is intentionally independent from AQL. AQL, Agents, Attention, UI, and future graph workflows should call the same entity extraction API instead of owning their own NER/entity-linking logic.

## Why

The KG/AQL evaluation showed that full attention narratives should not be passed directly into knowledge-graph search. Long text can match unrelated graph nodes through generic words such as `company`, `demand`, `and`, or `on`.

The new rule is:

```text
extract entities first
  -> link entities to KG nodes
  -> traverse KG only from linked nodes
```

## Current Extraction Sources

The first implementation uses:

- feed subject symbol
- feed member symbols
- exact KG aliases and node ids
- known commodity proxy tickers
- claim entity strings
- optional LLM structured extraction

Taxonomy lookup is opt-in through `include_taxonomy=True` because it can touch materialized dataset storage. Entity extraction must stay fast and non-blocking by default.

## Public API

```python
extract_entities(...)
linked_kg_node_ids(...)
graph_add_node_candidates(...)
```

Import from:

```python
from services.entity_extraction import extract_entities
```

`extract_entities(...)` returns `EntityMention` objects with:

- mention text
- entity type
- source
- confidence
- canonical id
- linked KG node id, if any
- link status and reason
- metadata

## LLM Role

The optional LLM extractor returns structured entities only. It should not make graph commits.

Use it for entities deterministic logic misses, such as:

- `fuel cells`
- `Oracle`
- `quantum computing`
- `optical networking`
- `800G transceivers`

Those unlinked entities become graph add-node candidates when confidence is high enough.

## Tests Added

The focused tests verify:

- BE / Oracle / fuel-cell text links to existing AI infrastructure KG nodes and produces add-node candidates for missing entities.
- SaaS text does not accidentally activate helium, lithium, oil, or industrial gas graph nodes.
- exact ticker and alias text links `NVDA`, `SMR`, and `ai_compute`.

Command:

```bash
streamlit_alpaca_app/.venv/bin/python -m pytest streamlit_alpaca_app/tests/test_entity_extraction.py -q
```

Result:

```text
3 passed
```

## Experiment Page Integration

The Experiment page now uses entity extraction before knowledge-graph search.

Current flow:

```text
seed query
  -> services.entity_extraction.extract_entities(...)
  -> linked_kg_node_ids(...) become preferred graph anchors
  -> search_knowledge_graph_nodes(...) is still used as fallback
  -> unlinked graph_add_node_candidates(...) are inserted as proposed node rows
  -> build_knowledge_graph_draft(...) creates the reviewable graph
  -> data editors approve adds, updates, and removals
  -> commit_knowledge_graph_review(...) persists the reviewed delta
```

This keeps exploration tied to explicit entities first, while still allowing manual graph search for short seed queries.

The page supports:

- first-load visualization of the most connected/important part of the committed graph
- visualizing the draft graph
- adding node rows from extracted unlinked entities
- adding node or edge rows manually in the editors
- removing committed/seeded nodes or edges by clearing `keep`
- updating node and edge attributes before commit

 The first-load overview comes from `build_knowledge_graph_overview(...)`. The Experiment page currently asks it for the full graph because the graph is still small enough to inspect directly. If the graph grows beyond readable size, the same function can cap the overview by graph connectivity and centrality.

Disconnected graph components are laid out separately and packed with padding in `plot_knowledge_graph_draft(...)`. This avoids visually overlapping independent subgraphs while keeping the renderer self-contained in NetworkX and Plotly.

The graph visualization now renders edge direction and edge strength explicitly:

- arrows show stored `source -> target` direction
- edge hover text shows relationship, polarity, directness, severity, confidence, source status, mechanism, and conditions
- thicker edges mean higher severity
- more opaque edges mean higher confidence
- the first-load page also shows the raw edge table so the visual can be checked against source/target metadata

## Ownership

Entity extraction owns:

- mention extraction
- deterministic alias/ticker linking
- optional LLM structured entity extraction
- KG node linking
- add-node candidate preparation

It does not own:

- AQL research planning
- KG persistence
- graph traversal
- UI presentation
- pipeline materialization

## Next Step

Move more graph exploration logic out of `app.py` if the Experiment page grows further. The current UI correctly calls the independent entity extraction module, but repeated review-table and graph-draft orchestration may deserve a small `services.knowledge_graph_explorer` facade later.
