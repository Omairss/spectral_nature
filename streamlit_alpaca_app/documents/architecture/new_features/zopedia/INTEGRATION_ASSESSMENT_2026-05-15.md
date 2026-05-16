# Zopedia Integration Assessment

Date: 2026-05-15

## Source Reviewed

Repository: `https://github.com/zohairshafi/zopedia`

Reviewed areas:

- `README.md`
- `ARCHITECTURE.md`
- `backend/core/wiki/*`
- `backend/routes/wiki.py`
- `backend/routes/chat.py`
- `backend/models/wiki.py`
- `graphify/graphify/*`
- `frontend/src/components/wiki-data-dialog.tsx`

## Summary

Zopedia is not just a graph viewer. It is a full LLM-maintained wiki system:

- raw source ingestion
- entity and concept page generation
- source and analysis pages
- wikilink graph construction
- graph exploration UI
- maintenance jobs for lint, enrichment, backlinks, compaction, merge, and community indexes
- tool-calling retrieval through `read_wiki_page`

For Spectral Nature, the right fit is:

```text
SAA owns Zopedia-style durable wiki storage and retrieval.
AQL consumes Zopedia context through SAA public APIs.
UI exposes graph exploration and review workflows.
KG stays separate as typed market relationship memory.
```

Do not fold Zopedia directly into AQL. That would make AQL own storage, indexing, graph maintenance, and wiki mutation, which conflicts with the current module boundary plan.

## What Maps Cleanly Into Spectral Nature

### SAA

SAA should absorb the durable knowledge-memory parts:

- wiki page storage
- raw source ingestion metadata
- page kinds: source, entity, concept, analysis
- wikilink parsing
- backlinks
- graph snapshot APIs
- archive state
- search and page retrieval
- community/god-node index generation

This extends SAA from retained evidence chunks into a readable, agent-navigable knowledge base.

### AQL

AQL should use Zopedia as a context and hypothesis source:

- extract entities from query/event
- ask SAA for relevant Zopedia pages
- retrieve 1-hop or 2-hop wiki neighborhoods
- use analysis pages as prior work
- use source pages as retained evidence
- verify current claims against fresh evidence before writing
- emit reviewable proposals for wiki/KG changes

AQL should not directly write wiki files or mutate the wiki graph.

### UI

The existing Zopedia graph explorer patterns are useful:

- separate Explore and Delete modes
- strict 1-hop/2-hop focus
- hide unfocused nodes for dense graphs
- keep selectable universe separate from rendered graph
- preview destructive operations before apply

These match the direction of the current Experiment/KG page work.

### Knowledge Graph

Zopedia should not replace the market KG.

Zopedia edge:

```text
page A links to page B
```

Market KG edge:

```text
entity/concept A affects entity/concept B with direction, severity, confidence, evidence, and conditions
```

The bridge should be shared entity IDs and citations, not merged storage.

## What To Reuse

High-value ideas to port:

- markdown page model with stable page IDs
- `[[wikilink]]` parsing and graph snapshot generation
- archive exclusion from active graph/retrieval
- delete preview/apply contract
- backlink maintenance
- community index for large graphs
- tool-style page reader for agents
- maintenance reports for broken links, orphans, stale pages, and duplicates

Code that can be adapted carefully:

- graph construction around `_all_wiki_pages`, `_build_link_graph`, and `get_wiki_data_graph`
- Pydantic response contracts for graph/delete operations
- graphify community/god-node ideas
- UI interaction model from `wiki-data-dialog.tsx`

## What Not To Import Wholesale

Avoid a direct transplant of the whole app:

- Zopedia has its own FastAPI app, auth assumptions, env names, frontend stack, and LLM client.
- `engine.py` is very large and combines ingestion, prompts, retrieval, maintenance, graph building, merge, and compaction.
- It uses filesystem wiki storage as the source of truth; Spectral Nature already has database-backed SAA evidence and pipeline materialization.
- Chat route behavior overlaps with our Agents/AQL planner.
- Web search fallback overlaps with existing AQL/page research tools.

The clean path is to extract concepts and contracts, not embed the application.

## Recommended Integration Shape

### Phase 1: SAA Wiki Submodule

Create a new SAA-owned submodule:

```text
services/saa/wiki/
  pages.py
  links.py
  graph.py
  maintenance.py
  proposals.py
```

Public API:

```python
search_wiki_pages(query, kinds=None, limit=10)
load_wiki_page(page_id)
get_wiki_graph(include_analysis=True)
get_wiki_neighborhood(seed_page_ids, depth=1, kinds=None)
propose_wiki_changes(change_set)
preview_wiki_delete(entry_type, entries, cascade=True)
apply_wiki_delete(review_id)
```

### Phase 2: Storage Model

Prefer database-backed tables over raw filesystem files as the canonical store:

- `saa_wiki_pages`
- `saa_wiki_links`
- `saa_wiki_page_versions`
- `saa_wiki_change_proposals`
- `saa_wiki_maintenance_runs`

Markdown can still be exported/imported, but DB rows should own the active state.

### Phase 3: AQL Retrieval Hook

Add an AQL context step:

```text
AQL query/event
  -> entity extraction
  -> SAA wiki search
  -> graph neighborhood expansion
  -> evidence pack assembly
  -> synthesis / critique
```

This should be small and optional at first. It should not block the core attention run if the wiki has no coverage.

### Phase 4: Reviewed Write-Back

AQL can propose:

- new entity/concept page
- new wikilink
- stale page archive
- duplicate page merge
- KG add/update edge proposal when evidence supports a typed relationship

All mutations should be review-first.

## Risks

- Too much Zopedia code imported directly would blur SAA/AQL boundaries.
- Filesystem-first storage would fight current SAA persistence and deployment patterns.
- Wiki links are useful navigation signals, but they are not typed causal relationships.
- LLM-generated pages need provenance, version history, and review controls before they become trusted memory.
- Maintenance jobs can become expensive if run on every ingest; schedule and cap them.

## Decision

Use Zopedia as the design reference for a SAA-owned durable wiki memory system.

Do not integrate it as a standalone app and do not make it part of AQL internals. AQL should call it through SAA retrieval and proposal APIs.
