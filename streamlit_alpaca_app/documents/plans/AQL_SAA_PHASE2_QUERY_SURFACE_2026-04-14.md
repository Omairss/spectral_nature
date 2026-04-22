# AQL + SAA Phase 2 - Historical Query Surface

This note records the first shared retrieval surface on top of retained SAA documents.

Goal:

- let humans and agents search retained historical documents without scanning only the latest parquet frame

## What Changed

Implemented:

- search-friendly retained-document metadata in `saa_documents`
- shared retained-document search helper
- shared retained-document open helper
- data-access query surfaces for search and document read

Main source changes:

- `services/saa/storage.py`
  - retained documents now persist:
    - `display_excerpt`
    - `search_text`
    - key fields for tickers, commodities, event tags, and dates
  - new helpers:
    - `search_retained_documents(...)`
    - `load_retained_document_metadata(...)`
- `data_access/layer.py`
  - new resolvers:
    - `resolve_saa_document_search(...)`
    - `resolve_saa_document(...)`
- `data_access/query_registry.py`
  - new dataset names:
    - `saa_document_search`
    - `saa_document`

## Query Surface

Search:

- dataset: `saa_document_search`
- filters:
  - `query`
  - `tickers`
  - `commodities`
  - `event_tags`
  - `dates`
  - `start_date`
  - `end_date`
  - `source_kinds`
  - `providers`
  - `run_id`
  - `limit`

Direct document open:

- dataset: `saa_document`
- required:
  - `canonical_document_id`
- optional:
  - `include_raw_text`

## Why This Matters

Before this change:

- the system had durable retained documents
- but there was no shared query surface over them
- cross-run lookup still pushed users back toward latest-frame inspection

After this change:

- historical retained documents can be searched through the same dataset query API
- a retained document can be reopened directly by canonical id
- agents and humans now have a stable entry point into SAA history

## Current Limits

This is still an early retrieval layer.

Not done yet:

- lexical index
- vector retrieval
- chunk-level historical retrieval
- `EvidencePack`
- gap-aware second-pass research

Current retrieval quality is based on:

- Postgres metadata filters
- stored search text
- lightweight ranking in Python

That is enough to stop latest-frame-only lookup and gives the next hybrid retrieval step a stable contract to build on.
