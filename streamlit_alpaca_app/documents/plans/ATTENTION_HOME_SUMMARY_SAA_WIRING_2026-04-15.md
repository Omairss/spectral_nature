# Attention Home Summary SAA Wiring

Date: 2026-04-15

## Problem

The dev homepage summary was deployed, but the UI still looked only slightly more agentic.

The root causes were:

- the homepage summary writer still used its own local search-to-chunk path instead of the shared SAA retrieval path
- top search results often kept only titles because search snippets were thin and the homepage path only opened Seeking Alpha pages
- homepage summary chunks did not get embeddings, so the new hybrid retrieval work was not helping that surface
- the UI only showed `summary_text`, so the user could not see the searches, sources, or supporting evidence that existed behind the run

## Source Fix

This change fixes the problem at source instead of trying to style around it.

### 1. Broader page enrichment before writing

- the homepage summary path now opens a small number of thin search results, not just Seeking Alpha links
- page text is retained in the summary search trace and carried into source documents
- this makes `raw_text` materially larger for the homepage summary corpus when search snippets are weak

### 2. Shared SAA retrieval for homepage summary chunks

- SAA now exposes `search_prepared_evidence_chunks(...)` for in-memory chunk frames
- the homepage summary prepares its fresh research chunks into the same searchable shape used by retained SAA chunks
- chunk retrieval now runs through the shared lexical + semantic scoring path before claim extraction

This keeps the homepage summary on the same retrieval contract as SAA without requiring an early Postgres round-trip during the job.

### 3. Embeddings now reach the homepage summary trace

- `attention-home-build` now passes the loaded embedding client into the homepage summary builder
- homepage summary chunks now persist `embedding_model` and `embedding_vector_json`
- semantic reranking can now actually apply to the homepage summary evidence set

### 4. The writer now has a visible evidence trace

- homepage summary payloads now carry:
  - `research_queries`
  - `top_sources`
  - `supporting_claims`
- the UI now renders a compact `Research trace` expander under the summary card

That makes it possible to see:

- what the agent searched
- which sources it actually used
- which evidence bullets supported the hypothesis

## Expected Result

After this change, the homepage summary should feel more grounded for two separate reasons:

1. the evidence set is stronger because more real page text is retained
2. the UI shows the research trace instead of hiding all of it behind one paragraph

This does **not** fully solve the broader writing-quality problem by itself. The summary can still sound too compressed if the writer prompt or beat format is weak. But it removes the main wiring problem where better retrieval existed in SAA and the homepage summary still did not use it.

## Follow-up

If the output is still too flat after this wiring fix, the next work should be:

1. use retrieved SAA chunks from the broader attention corpus, not only the homepage-summary search trace
2. add a second-pass gap check when the first hypothesis is too generic
3. rewrite `Top Events` and `Key Movers` so they stop reusing clipped feed-style text
