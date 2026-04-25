# AQL + SAA V1 Implementation Roadmap

Date: 2026-04-14

Related:

- [AQL + SAA NLP / IR / Agent Architecture](/Users/omairs/Documents/code/spectral_nature/streamlit_alpaca_app/documents/architecture/AQL/AQL_NLP_IR_AGENT_ARCHITECTURE_2026-04-14.md)
- [Attention Research Quality Fix](/Users/omairs/Documents/code/spectral_nature/streamlit_alpaca_app/documents/architecture/attention/ATTENTION_RESEARCH_QUALITY_FIX_2026-04-14.md)
- [AQL Evidence Index](/Users/omairs/Documents/code/spectral_nature/streamlit_alpaca_app/documents/architecture/AQL/AQL_EVIDENCE_INDEX_2026-04-14.md)

## Goal

Turn the north-star AQL + SAA architecture into a practical v1 that:

- stops losing research value
- supports better retrieval than `first chunks + top snippets`
- gives AQL one shared evidence system
- materially improves homepage summaries and narrative cards

This roadmap is intentionally biased toward source fixes before fancy orchestration.

## V1 Definition

`AQL` remains the reasoning and writing layer.

`SAA` becomes the supporting evidence system that handles:

- acquisition
- durable retention
- indexing
- retrieval
- reranking
- evidence-pack assembly support

## What V1 Must Deliver

V1 is successful if all of these are true:

1. Search and page-read results are retained durably as full documents, not just snippets.
2. AQL can retrieve evidence by structured filters, lexical search, and semantic search.
3. Homepage summaries and event cards use ranked evidence, not `head(6)` and first-paragraph bias.
4. AQL can detect evidence gaps and run a targeted follow-up retrieval pass.
5. Final outputs are built from a shared `EvidencePack`.
6. Research runs are inspectable through run ids, logs, and retained retrieval traces.

## Non-Goals For V1

Do not do these first:

- speculative execution with isolated writes
- remote deep-planning sessions
- a separate graph database
- full agent swarm orchestration everywhere
- replacing the whole pipeline scheduler

Those can come later. V1 should first make the existing system trustworthy.

## Design Rules

1. Retain first, compress later.
2. Keep one shared evidence corpus.
3. Prefer deterministic ranking before extra LLM calls.
4. Add expensive LLM passes only when gap checks say they are needed.
5. Keep AQL and SAA boundaries clean.

## V1 Architecture Slice

```text
Sources
  -> SAA fetch + retain
  -> SAA document / chunk / claim pipeline
  -> SAA indexes
  -> SAA retrieval + reranking
  -> AQL EvidencePack
  -> AQL writer / critic
  -> homepage, event cards, ticker tools
```

## Workstreams

## Workstream 1. Durable Retention

Problem:

- current paths often retain snippets or pre-trimmed page text only

Deliverables:

- durable raw provider payload storage
- durable full page text storage
- canonical `source_documents` records with blob pointers
- document versioning by content hash

Suggested implementation:

- new SAA storage helpers under `services/aql/` or a new `services/saa/`
- extend current search-result and page-browse flows so they write:
  - provider raw JSON
  - full extracted text
  - normalized document rows

Recommended persisted fields:

- `source_record_id`
- `document_id`
- `content_hash`
- `raw_payload_blob_path`
- `raw_text_blob_path`
- `text_length`
- `extraction_method`
- `fetch_status`

Done means:

- Seeking Alpha, SerpAPI, Tavily, and opened pages all retain full fetched artifacts
- local cache is not the source of truth

## Workstream 2. SAA Data Model

Problem:

- current retained evidence is too flat and too tied to immediate summary flows

Deliverables:

- canonical entities:
  - `research_queries`
  - `research_fetches`
  - `source_documents`
  - `document_summaries`
  - `evidence_chunks`
  - `claims`
  - `research_runs`
  - `research_gap_checks`
  - `final_outputs`

Suggested implementation:

- Postgres tables for metadata and history
- blob/object storage for large raw content
- compatibility shims from current materialized frames into the new tables

Done means:

- every research run can be reconstructed from stored metadata and raw content

## Workstream 3. Chunking And Summaries

Problem:

- `pieces[:3]` and hard early trimming lose real signal

Deliverables:

- hierarchical segmentation:
  - document
  - section
  - chunk
- full-document chunking without first-paragraph bias
- document-level map summaries for long sources
- structured claim extraction linked back to chunk ids

Suggested implementation:

- replace current chunking in `services/aql/extractor.py`
- add section-aware chunk metadata
- add `document_summaries` as a first-class artifact

Done means:

- long Seeking Alpha pieces, filings, and long articles can be summarized without losing later sections

## Workstream 4. Hybrid Retrieval

Problem:

- current retrieval is mostly top-of-frame scanning

Deliverables:

- structured filter retrieval
- lexical retrieval
- semantic retrieval
- hybrid merge + rerank

Recommended stack:

- Postgres for metadata filters
- OpenSearch for full-text search
- `pgvector` for semantic retrieval

Suggested implementation:

- SAA retrieval service with:
  - `search_structured(...)`
  - `search_lexical(...)`
  - `search_semantic(...)`
  - `retrieve_hybrid(...)`
  - `rerank_candidates(...)`

Ranking signals:

- recency
- source authority
- entity overlap
- query overlap
- semantic similarity
- same-day relevance
- source diversity contribution

Done means:

- no summary path depends on `head(6)` or first chunk position

## Workstream 5. EvidencePack

Problem:

- writers see too little evidence and too little structure

Deliverables:

- shared `EvidencePack` object used by homepage summaries, event cards, and future agent answers

Recommended fields:

- `task`
- `entities`
- `query_plan`
- `top_documents`
- `top_document_summaries`
- `top_chunks`
- `top_claims`
- `counterpoints`
- `coverage_gaps`
- `source_diversity`
- `retrieval_trace`

Suggested implementation:

- new shared builder in AQL
- serialization format for debugging and materialization

Done means:

- the same retrieved evidence can feed multiple writers without re-querying

## Workstream 6. Gap-Aware Agent Loop

Problem:

- current logic searches once and writes once

Deliverables:

- planner
- retriever
- gap detector
- targeted second-pass retrieval
- writer
- critic

Borrowed pattern:

- coordinator-worker, but only where it pays off

Practical v1 form:

- start single-process
- keep the loop explicit in code
- allow later fan-out into workers for deep research mode

Gap checks should ask:

- do we have same-day evidence?
- do we have a concrete driver?
- are we overfitting one source?
- is the output too generic?
- do we have counter-evidence?

Done means:

- homepage summaries and event cards can trigger targeted follow-up research only when needed

## Workstream 7. Surface Writers

Problem:

- homepage, event cards, and audio currently reuse too much compact or generic text

Deliverables:

- homepage writer
- event-card writer
- ticker brief writer
- audio writer
- critic / rewrite pass

Rules:

- all consume `EvidencePack`
- all cite the same underlying evidence objects
- each surface gets its own output schema

Done means:

- homepage bullets are short and dense
- event cards stop defaulting to canned macro filler
- weak outputs can be critiqued and rewritten

## Workstream 8. Tasking, Logs, And Monitoring

Borrowed patterns worth using now:

- explicit task states
- durable output/log path
- live status summary
- time + count + lock consolidation gates

Deliverables:

- typed research run ids
- persisted run logs
- live progress summaries
- background consolidation job for dedupe and pruning

Done means:

- long runs are inspectable
- evidence maintenance does not race or overlap

## Phased Build Plan

## Phase 0. Stabilize The Current Summary Path

Purpose:

- stop the worst waste immediately

Tasks:

- retain full fetched page text for homepage research
- retain provider raw payloads
- stop truncating to first few chunks before retrieval
- remove `head(6)` from summary retrieval

Acceptance:

- homepage research trace stores full document references and ranked chunks

## Phase 1. SAA Retention Foundation

Purpose:

- create durable raw-content and metadata storage

Tasks:

- add SAA storage layer
- add canonical ids and hashes
- persist raw payloads and full text to blob
- persist metadata rows to Postgres

Acceptance:

- any retained document can be reopened from stored metadata without rerunning search

## Phase 2. SAA Retrieval Foundation

Purpose:

- make retrieval good enough for v1 writing quality

Tasks:

- ship structured filters
- add lexical index
- add embeddings for chunks and document summaries
- implement hybrid retrieval and reranking

Acceptance:

- AQL can retrieve strong evidence for dates, symbols, and narrative queries without frame scanning

## Phase 3. EvidencePack And Writers

Purpose:

- stop writing directly from raw search rows

Tasks:

- build `EvidencePack`
- rewrite homepage summary path
- rewrite event-card path
- add critique pass for generic outputs

Acceptance:

- both homepage and cards use the same evidence object model

## Phase 4. Gap-Aware Research Loop

Purpose:

- make the system behave like an actual analyst

Tasks:

- add coverage scoring
- add targeted re-search loops
- add source-diversity and same-day checks
- persist `research_gap_checks`

Acceptance:

- if initial retrieval is weak, the system performs one targeted follow-up pass before writing

## Phase 5. Consolidation And Monitoring

Purpose:

- make the system durable over time

Tasks:

- consolidation scheduler
- contradiction detection
- stale evidence pruning
- live run summaries
- better run-state tracking

Acceptance:

- evidence quality improves over repeated runs instead of degrading into duplicates and stale clutter

## Concrete Module Plan

Suggested repo shape:

### AQL

- `services/aql/agent_loop.py`
- `services/aql/evidence_pack.py`
- `services/aql/writers/homepage.py`
- `services/aql/writers/event_card.py`
- `services/aql/writers/audio.py`
- `services/aql/critic.py`

### SAA

- `services/saa/storage.py`
- `services/saa/models.py`
- `services/saa/ingest.py`
- `services/saa/chunking.py`
- `services/saa/summaries.py`
- `services/saa/claims.py`
- `services/saa/retrieval.py`
- `services/saa/rerank.py`
- `services/saa/consolidation.py`
- `services/saa/tasks.py`

This can start inside `services/aql/` if that is faster, but the boundary should still be respected.

## API / Query Surface

V1 should expose a clean research API for humans and agents.

Minimum endpoints or query functions:

- `search_evidence(query, filters)`
- `get_document(document_id)`
- `get_document_summary(document_id)`
- `get_chunks(document_id)`
- `get_claims(subject, filters)`
- `build_evidence_pack(task, entities, filters)`
- `run_gap_aware_research(task, entities, filters)`

## Metrics

Track these before and after rollout:

- average retained text length per fetched document
- percentage of fetches with durable raw payloads
- percentage of summaries with at least 2 source families
- percentage of summaries with at least 1 opened page
- percentage of summaries with explicit unresolved language when evidence is weak
- generic-phrase density
- average retrieval latency
- average extra cost from second-pass loops

## Suggested Order For Real Implementation

If engineering starts immediately, do this order:

1. remove snippet-only retention
2. add durable source-document storage
3. add ranked retrieval
4. add `EvidencePack`
5. rewrite homepage summary
6. rewrite event cards
7. add gap-aware second-pass retrieval
8. add consolidation and monitoring

This order fixes the current waste first, then improves output quality, then adds more agent behavior.
