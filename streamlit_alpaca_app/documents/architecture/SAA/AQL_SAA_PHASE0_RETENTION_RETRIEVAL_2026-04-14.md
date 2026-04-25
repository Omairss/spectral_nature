# AQL + SAA Phase 0 - Retention And Retrieval Fix

Date: 2026-04-14

## Status

Implemented in the current homepage-summary research path.

## Problem

The system was paying to search, browse, and extract evidence, but then narrowing that evidence too early:

- snippets were often treated as the canonical document
- provider raw payloads were discarded
- Seeking Alpha page reads were capped too tightly
- chunking only kept the first few pieces of each document
- claim extraction only looked at `head(6)` chunks

That made the summary loop flatter than the underlying research spend justified.

## What Changed

### 1. Richer search-result retention

`services/web_research.py`

- `WebSearchResult` now carries `raw_text`
- Tavily now prefers `raw_content` over `content` when available
- Tavily raw content is enabled by default unless `TAVILY_INCLUDE_RAW_CONTENT` is explicitly turned off

### 2. Provider payloads and richer provider text now flow into search results

`services/aql/collector.py`

- `_search_query_results(...)` can now retain:
  - `provider_text`
  - `provider_payload_json`
  - `query_text`
- the homepage-summary path enables those richer fields
- symbol-news payload building also prefers richer provider text when available

### 3. Source documents stop collapsing everything to snippet-only text

`services/aql/extractor.py`

- `_documents_from_search_results(...)` now prefers:
  - `page_text`
  - then `provider_text`
  - then snippet/title fallback
- document rows now record:
  - `raw_text_origin`
  - `raw_text_chars`
  - `provider_payload_json`
  - `query_text`

### 4. Chunking no longer keeps only the first few pieces

`services/aql/extractor.py`

- replaced first-few-piece logic with sentence-aware chunk assembly
- increased chunk retention budget per document
- kept low-signal boilerplate filtering so analyst-rating fluff does not poison chunks

### 5. Retrieval is now ranked before claim extraction

`services/aql/extractor.py`

- added chunk ranking based on:
  - freshness
  - authority
  - query overlap
  - entity/tag presence
  - whether the text came from page or provider text rather than a snippet
- `_fallback_claims_from_chunks(...)` and `_extract_claims(...)` now use ranked chunks instead of `head(6)`

### 6. Homepage summary trace keeps ranked chunks

`services/aql/summarizer.py`

- homepage summary research now stores ranked chunks with:
  - `retrieval_score`
  - `retrieval_rank`
- Seeking Alpha page max chars default was raised from `2800` to `12000` for this path

## Runtime Knobs

Updated:

- `.env.example`
- `scripts/deploy_pipeline_azure.sh`

Important defaults:

- `ATTENTION_HOME_SEEKING_ALPHA_PAGE_MAX_CHARS=12000`
- `TAVILY_INCLUDE_RAW_CONTENT=true`

## What This Fix Does Not Do Yet

This is still Phase 0, not full SAA.

It does **not** yet add:

- durable blob-backed raw document storage outside existing materialized frames
- OpenSearch lexical retrieval
- vector retrieval
- full `EvidencePack`
- explicit multi-step agent loops with gap-triggered re-retrieval

## Outcome

The homepage summary path now retains more of what it fetches and retrieves evidence from ranked chunks instead of first-chunk order. That should improve both evidence quality and the trace available for later inspection.
