# AQL + SAA Phase 1 - Retention Foundation

This note records the first durable-storage layer under AQL.

Goal:

- keep retained source documents reopenable without rerunning search

## What Changed

Implemented:

- canonical document identity fields on AQL source documents
- durable raw-document JSON blobs under Azure Blob storage
- Postgres metadata table for retained documents
- reopen helper for retained documents

Main source changes:

- `services/aql/evidence_index.py`
  - every annotated source document now gets:
    - `canonical_document_id`
    - `canonical_url`
    - `url_host`
    - `document_identity_sha256`
    - `document_content_sha256`
    - `provider_payload_sha256`
- `services/aql/extractor.py`
  - chunk rows now carry the canonical document identity fields from their parent document
- `services/saa/storage.py`
  - new SAA retention module
  - prepares retained-document blob payloads
  - upserts retained-document metadata into Postgres
  - exposes `load_retained_document(...)`
- `pipeline/jobs/main.py`
  - bootstraps SAA storage tables
  - runs SAA retention before persisting `attention_source_documents`

## Storage Model

Blob:

- retained raw documents are written as JSON under:
  - `saa/raw_documents/provider=<provider>/dt=<date>/document=<canonical_document_id>/content=<content_sha>.json`

Postgres:

- retained document metadata lives in table `saa_documents`
- the table stores:
  - canonical id
  - url metadata
  - provider metadata
  - published dates
  - content and payload hashes
  - latest blob path
  - evidence tags such as tickers, commodities, dates, and event tags

Materialized dataset:

- `attention_source_documents` now also carries:
  - canonical ids and hashes
  - `raw_text_blob_path`
  - `retained_at_utc`

## Why This Matters

Before this change:

- source documents only lived in the materialized parquet dataset
- reopening a document depended on reloading the latest frame
- there was no durable raw-document layer underneath AQL

After this change:

- the system keeps a reopenable raw-document version in blob storage
- AQL document rows now point to that durable version
- Postgres can answer "where is this document?" without rerunning search

## Current Limits

This is still not full SAA.

Not done yet:

- chunk-level durable storage
- document-version history beyond the latest retained blob path
- lexical index or vector retrieval
- shared `EvidencePack`
- gap-aware follow-up research

Current scope is:

- `attention_source_documents`

That is enough to stop the most obvious raw-document loss and gives the next retrieval phase a stable document layer to build on.
