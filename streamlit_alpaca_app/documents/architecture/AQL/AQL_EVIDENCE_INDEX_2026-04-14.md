# AQL Evidence Index - 2026-04-14

## Goal

Make external AQL evidence searchable in one shared place for both humans and agents.

This includes:

- web search results
- Tavily and SerpApi results
- page-read enrichments such as Seeking Alpha
- source documents built from those results
- homepage agentic-summary research, not just symbol and event bundles

## Constraints

- Optimize for cost and speed
- Fix this at the shared AQL layer, not in one UI surface
- Avoid adding a vector DB or heavy retrieval stack for this first pass

## Design

Use the existing materialized AQL trace datasets instead of creating a second storage system.

Shared storage:

- `attention_search_requests`
- `attention_search_results`
- `attention_source_documents`
- `attention_evidence_chunks`
- `attention_claims`

Shared deterministic metadata on documents and chunks:

- `published_date`
- `primary_date`
- `mentioned_dates_json`
- `mentioned_tickers_json`
- `mentioned_commodities_json`
- `event_tags_json`
- compact `*_key` fields for cheap filtering without JSON parsing

Search surface:

- new materialized dataset resolver: `attention_evidence_search`
- filters by:
  - free-text query
  - tickers
  - commodities
  - event tags
  - dates or date range
  - source kinds
  - providers
  - research scope
  - run id

## Why This Approach

This is cheaper and faster than a new RAG stack because:

- metadata extraction is deterministic and local
- retrieval works over already-materialized chunk frames
- there is no extra embedding job, vector index, or remote retrieval service
- agents can still query the same evidence corpus through the dataset tool surface

## Implementation Notes

### 1. Shared metadata extraction

Added `services/aql/evidence_index.py`.

It extracts:

- ticker mentions
- commodity tags
- event tags
- explicit and relative date mentions

### 2. Source-level indexing

`_documents_from_search_results(...)` now annotates search documents with index metadata.

`build_bottom_up_attention_artifacts(...)` now annotates all deduped source documents before materialization, so non-search context docs also land in the same contract.

### 3. Chunk-level indexing

`_chunk_source_documents(...)` now carries:

- source kind
- provider/search metadata
- deterministic ticker/date/commodity/event fields

### 4. Homepage summary trace

The homepage agentic summary path now has a trace-capable variant:

- `build_attention_agentic_summary_with_trace(...)`

This keeps the summary payload lean while still returning materializable:

- search requests
- search results
- source documents
- evidence chunks
- claims

`attention_home_build` merges that trace into the normal AQL frames before persistence.

## Outcome

After this change, the summary research path no longer lives outside the searchable AQL trace.

The searchable source of truth is now:

- one shared chunk corpus
- one shared metadata contract
- one shared dataset search entrypoint

## Next Step

If this needs stronger long-document recall later, add embedding-backed reranking on top of the same chunk corpus instead of replacing this deterministic index.
