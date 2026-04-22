# AQL + SAA Phase 4 - Hybrid Chunk Retrieval

Date: 2026-04-14

This note records the first hybrid retrieval layer on top of retained SAA evidence chunks.

Goal:

- move `saa_chunk_search` beyond plain keyword search
- combine structured filters, lexical scoring, and semantic reranking
- do it with low added cost and low infrastructure complexity

## What Changed

Implemented:

- persisted chunk embeddings in `saa_evidence_chunks`
- hybrid chunk search in `search_retained_evidence_chunks(...)`
- shared `use_semantic` switch on `saa_chunk_search`

Main source changes:

- `services/saa/storage.py`
  - `saa_evidence_chunks` now retains:
    - `embedding_model`
    - `embedding_vector_json`
  - `search_retained_evidence_chunks(...)` now:
    - fetches lexical candidates
    - optionally widens to a recent candidate pool for semantic rerank
    - computes one query embedding
    - scores stored chunk vectors with cosine similarity
    - merges lexical and semantic signals into one final ranking
- `data_access/layer.py`
  - `resolve_saa_chunk_search(...)` now supports `use_semantic`
- `data_access/query_registry.py`
  - `saa_chunk_search` now exposes `use_semantic`

## Retrieval Model

Current retrieval is:

1. structured SQL filters
2. lexical match scoring
3. optional semantic rerank on stored chunk embeddings
4. final rerank using:
   - lexical score
   - semantic score
   - authority
   - recency

Returned fields now include:

- `score_lexical`
- `score_embedding`
- `score_rerank`
- `search_score`
- `match_source`

`match_source` can be:

- `lexical`
- `semantic`
- `hybrid`
- `structured`

## Cost And Speed Design

This phase avoids a new vector service.

Why:

- chunk embeddings were already generated in the attention pipeline
- reusing stored embeddings is much cheaper than re-embedding documents at query time
- only one query embedding is generated per semantic search
- candidate widening is bounded, so the semantic pass stays small

This keeps cost and latency much lower than introducing a full vector DB immediately.

## Current Limits

Still not done:

- global semantic recall across the full corpus
- dedicated lexical index such as OpenSearch
- ANN / vector index
- cross-source reranking model
- `EvidencePack`

So this phase improves retrieval quality, but it is still a lightweight hybrid layer.

## Why This Matters

Before this change:

- `saa_chunk_search` was mostly structured + lexical
- semantic-only matches were easy to miss
- stored chunk embeddings were not actually used by the shared search surface

After this change:

- the chunk store can surface evidence that is relevant even when wording differs
- semantic search reuses embeddings the pipeline already paid to compute
- the shared search path is materially stronger without adding another retrieval system
