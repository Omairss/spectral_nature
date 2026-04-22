# AQL + SAA Phase 3 - Historical Chunk Search

Date: 2026-04-14

This note records the first shared historical search surface over retained evidence chunks.

Goal:

- let humans and agents search the actual evidence units AQL reasons over
- stop pushing historical research back toward latest-frame-only chunk inspection
- keep the implementation cheap and fast by reusing Postgres plus the existing chunk metadata contract

## What Changed

Implemented:

- durable `saa_evidence_chunks` Postgres table
- automatic chunk retention during `attention_evidence_chunks` persistence
- shared historical chunk-search helper
- shared data-access dataset for chunk search

Main source changes:

- `services/saa/storage.py`
  - bootstraps `saa_evidence_chunks`
  - persists retained chunk rows with:
    - `chunk_record_id`
    - `chunk_identity_sha256`
    - `canonical_document_id`
    - `chunk_text`
    - `search_text`
    - structured metadata for tickers, commodities, dates, and event tags
  - adds `search_retained_evidence_chunks(...)`
- `pipeline/jobs/main.py`
  - retains `attention_evidence_chunks` into SAA before parquet upload
- `data_access/layer.py`
  - adds `resolve_saa_chunk_search(...)`
- `data_access/query_registry.py`
  - adds dataset `saa_chunk_search`

## Query Surface

Dataset:

- `saa_chunk_search`

Filters:

- `query`
- `tickers`
- `commodities`
- `event_tags`
- `dates`
- `start_date`
- `end_date`
- `source_kinds`
- `providers`
- `research_scopes`
- `run_id`
- `canonical_document_id`
- `limit`

Returned shape:

- chunk ids
- canonical document id
- title
- excerpt
- chunk text
- providers
- dates
- tags
- search score

The raw full document still lives behind `saa_document`.

## Why This Matters

Before this change:

- retained documents were searchable across runs
- but the actual reasoning units, evidence chunks, still lived mainly in the latest parquet frame
- historical research still had to jump between document search and latest-run chunk inspection

After this change:

- chunk history is durable
- chunk history is searchable through the same dataset query API
- search can return the actual text AQL used as evidence, plus the canonical document id for reopening the source

## Design Notes

- This is intentionally not a full lexical or vector engine yet.
- The search path uses Postgres metadata filters plus stored `search_text` and lightweight ranking.
- Structured filters are pushed into SQL first where possible, then ranked in Python.
- This keeps the implementation simple while materially improving research access.

## Current Limits

Not done yet:

- lexical index
- semantic retrieval
- cross-source reranking
- `EvidencePack`
- gap-aware second-pass retrieval

This phase solves the next immediate bottleneck:

- durable historical chunk access

It does not yet solve:

- best-possible retrieval quality across a large long-term corpus
