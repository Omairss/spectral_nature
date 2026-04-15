# AQL + SAA NLP / IR / Agent Architecture

Date: 2026-04-14

## Goal

Redesign AQL as a real research system, not a thin search-and-summarize loop.

The system should:

- retain what it paid to fetch
- support strong lexical, semantic, and structured retrieval
- let an LLM agent research in loops instead of one pass
- write outputs from explicit evidence packs, not vague memory
- work for homepage summaries, event cards, ticker workspaces, and future agent surfaces

## Naming

Call the new supporting search system `SAA`.

In this plan:

- `AQL` = the research and reasoning layer that product surfaces call
- `SAA` = the supporting search, acquisition, storage, and retrieval system underneath it

Simple mental model:

- AQL asks questions, plans research, reasons over evidence, and writes outputs
- SAA finds, stores, indexes, retrieves, and serves the evidence that AQL uses

This is not a separate product. It is the shared evidence engine that makes AQL strong.

## Main Problem To Fix

Today the system can search, open pages, and read some premium sources, but it still throws away too much value before retrieval and writing.

Bad patterns to eliminate:

- snippet-only retention
- pre-trimming long pages before ingestion
- `pieces[:3]` chunking
- `head(6)` retrieval
- one-pass writing from a tiny evidence slice
- separate lightweight logic for cards and stronger logic for summaries

That architecture wastes search spend, page-read effort, and model context.

## Design Principles

1. Retain first, compress later.
2. Separate storage, retrieval, and writing.
3. Use one shared evidence corpus across all product surfaces.
4. Combine structured filters, lexical search, semantic search, and reranking.
5. Make agentic loops explicit: plan, retrieve, detect gaps, re-retrieve, then write.
6. Every final sentence should be traceable to source evidence.
7. Use expensive LLM work only where deterministic or cheaper ranking is not enough.

## North Star Architecture

```text
External Sources
  -> SAA Acquisition Layer
  -> SAA Canonical Document Store
  -> SAA Enrichment Layer
  -> SAA Multi-Index Retrieval Layer
  -> Agent Research Loop
  -> Surface-Specific Writers
  -> Homepage / Cards / Omnibar / API
```

Relationship:

```text
Product Surface
  -> AQL
  -> SAA
  -> External / retained evidence
```

## 1. Acquisition Layer

This layer gathers raw evidence and stores it before any aggressive trimming.

Sources:

- SerpAPI
- Tavily
- page browsing
- Seeking Alpha
- SEC / EDGAR
- official company IR pages
- macro and market sources
- future premium feeds

Each fetch should produce a `SourceRecord`:

- `source_record_id`
- `run_id`
- `query_id`
- `provider`
- `source_kind`
- `url`
- `title`
- `published_at`
- `raw_provider_payload`
- `raw_extracted_text`
- `extraction_method`
- `fetch_status`
- `content_hash`
- `parent_query`

Rules:

- keep provider raw JSON when possible
- keep full extracted page text when possible
- store exact fetch method and errors
- never collapse full text into a snippet as the only retained artifact

## 2. Canonical Document Store

This is the durable source of truth for retrieved content.

Use two layers:

- Blob/object storage for large raw payloads and full text
- Postgres metadata rows for indexing, joins, and history

Recommended objects:

- `raw/provider/<provider>/<source_record_id>.json`
- `raw/page_text/<source_record_id>.txt`
- `derived/document/<document_id>.json`

Recommended Postgres tables:

- `research_queries`
- `research_fetches`
- `source_documents`
- `document_versions`
- `document_summaries`
- `evidence_chunks`
- `claims`
- `research_runs`
- `research_gap_checks`
- `final_outputs`

Why:

- Blob is cheap and durable for raw content
- Postgres is strong for metadata, joins, filters, and history
- this avoids treating container-local cache as storage

## 3. Enrichment Layer

This layer turns raw text into structured research objects.

Steps:

### A. Canonicalization

- dedupe by normalized URL + content hash
- collapse duplicate search hits from different providers
- version documents when content changes

### B. Metadata extraction

- tickers
- company names
- sectors
- commodities
- macro entities
- dates
- relative dates normalized to absolute dates
- event tags
- source authority
- same-day vs background classification

### C. Hierarchical segmentation

Do not just create flat chunks.

Create:

- document
- section
- chunk
- claim

For long documents:

- detect headings
- keep section ids
- chunk within sections
- preserve chunk order, section title, and surrounding context

### D. Document summaries

For each strong document, generate a compact structured summary:

- main thesis
- concrete dated facts
- catalysts
- counterpoints
- uncertainty
- quoted entities

This should be map-style summarization over full documents, not snippet rewriting.

### E. Claim extraction

Extract claims into structured rows:

- `claim_id`
- `document_id`
- `chunk_id`
- `claim_text`
- `claim_type`
- `entities`
- `supports_topics`
- `freshness_class`
- `confidence`
- `citation_offsets`

Claims should always link back to chunks and documents.

## 4. Multi-Index Retrieval Layer

The retrieval layer should not rely on one index type.

Use four retrieval modes together:

### A. Structured retrieval

Best for:

- ticker
- date range
- commodity
- event tag
- source kind
- provider
- run id

Backed by:

- Postgres indexes

### B. Lexical retrieval

Best for:

- exact phrases
- headlines
- narrative keywords
- analyst terms
- uncommon market language

Backed by:

- OpenSearch / Elasticsearch
or
- Postgres full-text as a first step

For the best version of this system, prefer OpenSearch for serious text search and faceting.

### C. Semantic retrieval

Best for:

- concept similarity
- hidden thematic links
- paraphrased questions
- long-form research recall

Backed by:

- `pgvector` if staying simple
or
- a dedicated vector store if scale demands it later

For this repo, `pgvector` is the best first semantic layer because it fits the current stack and keeps ops simpler.

### D. Graph retrieval

Best for:

- entity relationships
- sector spillovers
- theme clusters
- recurring narrative patterns

Backed by:

- graph tables in Postgres at first
- not a separate graph database yet unless graph complexity becomes dominant

## 5. Retrieval Strategy

The best retrieval path is hybrid retrieval plus reranking.

Flow:

1. Planner decomposes the question
2. Structured filters narrow the candidate set
3. Lexical retrieval finds exact and recent matches
4. Semantic retrieval finds conceptually related evidence
5. Graph expansion finds linked entities and related themes
6. A reranker scores the combined candidate set
7. The agent decides whether coverage is enough or another pass is needed

Recommended ranking signals:

- recency
- source authority
- exact ticker/topic overlap
- semantic relevance
- market-same-day relevance
- diversity across source families
- directness of causal language

## 6. Agent Research Loop

This is the core upgrade.

The agent should not just:

- search once
- take top snippets
- write a paragraph

It should run an explicit loop:

### Step 1. Plan

Break the task into:

- core question
- subquestions
- likely evidence types
- required entities
- must-confirm unknowns

### Step 2. Retrieve

Pull:

- prior retained evidence
- fresh external evidence
- official sources
- premium sources when relevant

### Step 3. Build an evidence notebook

Create a working set with:

- top documents
- top chunks
- top claims
- source diversity summary
- unresolved gaps

### Step 4. Gap detection

Ask:

- what is still missing?
- which beats are still unsupported?
- are we overfitting one source?
- do we have same-day evidence?
- do we have counter-evidence?

### Step 5. Targeted follow-up retrieval

Only if needed:

- refine queries
- open more pages
- widen entity set
- pull supporting official or premium documents

### Step 6. Write from evidence pack

The writer receives:

- evidence notebook
- top citations
- open questions
- source diversity stats

Not raw search clutter.

### Step 7. Critique

Run a final critic pass:

- is the answer generic?
- is it overconfident?
- are the strongest facts cited?
- are gaps admitted?

If weak, rewrite or retrieve again.

## 7. Writer Architecture

Writers should be surface-specific but evidence-shared.

Do not use one summary style everywhere.

Use separate writers for:

- homepage market summary
- event card
- ticker brief
- audio script
- agent answer

All of them should consume the same `EvidencePack`.

Recommended `EvidencePack` shape:

- `task`
- `entities`
- `top_documents`
- `top_document_summaries`
- `top_chunks`
- `top_claims`
- `counterpoints`
- `coverage_gaps`
- `source_diversity`
- `retrieval_trace`

## 8. Long-Document Strategy

Long documents should use hierarchical map-reduce.

Pipeline:

1. retain full text
2. split into sections
3. summarize each section
4. summarize the document from section summaries
5. extract claims from the section or document summaries
6. let retrieval operate over both raw chunks and document summaries

This is critical for:

- Seeking Alpha
- long research notes
- SEC filings
- macro reports

Without this, the system will always overvalue the first paragraphs.

## 9. Memory and History

The system needs both short-term and long-term memory.

### Short-term working memory

Used inside one agent run:

- current question
- evidence notebook
- unresolved gaps
- follow-up query history

### Long-term research memory

Used across runs:

- documents
- chunks
- claims
- document summaries
- final outputs
- quality scores

This lets the agent reuse prior work instead of re-searching the same topic every time.

## 10. Quality Controls

Every research run should emit measurable quality signals.

Track:

- number of opened pages
- number of premium pages successfully read
- same-day evidence count
- source-family diversity
- official-source presence
- lexical vs semantic retrieval mix
- generic-language density in final output
- citation coverage
- unresolved-gap count

These metrics should gate final writing quality.

Examples:

- if source diversity is `1`, final output should hedge more
- if no same-day evidence exists, do not write a confident causal story
- if generic phrase density is high, trigger critique or re-retrieval

## 11. Useful Patterns To Borrow From Claude Code

The notes in [claude_code_architecture_ideas.md](/Users/omairs/Documents/code/spectral_nature/streamlit_alpaca_app/documents/claude_agent_learnings/claude_code_architecture_ideas.md) contain several patterns worth reusing.

The best ones for AQL + SAA are:

### A. Coordinator-worker orchestration

Use a clear split between:

- parallel research workers
- one coordinator that synthesizes findings into a precise evidence brief
- narrow writers
- independent verifiers

Why this is useful:

- it keeps synthesis in one place instead of pushing it into every worker
- it maps cleanly to AQL stages such as collect, extract, cluster, write, verify
- it makes agent traces easier to debug

Recommendation:

- AQL should use coordinator-style orchestration for deep research flows and high-value summary jobs

### B. Shared scratchpad / staging area

Use a shared scratchpad or staging directory for cross-stage state.

Why this is useful:

- it gives workers a durable place to exchange evidence artifacts
- it reduces function-argument sprawl across multi-step workflows
- it naturally supports review-before-promote flows

Recommendation:

- SAA should own a structured staging area for fetched documents, ranked evidence packs, intermediate summaries, and verification artifacts

### C. Time + count + lock consolidation gates

The background memory-consolidation pattern is directly useful for evidence maintenance.

Why this is useful:

- consolidation is expensive enough to gate
- it avoids duplicate or overlapping maintenance runs
- it gives a deterministic schedule for cleanup and merge work

Recommendation:

- run SAA consolidation only when both a time or run-count gate is met, and protect it with a lock
- use it for dedupe, contradiction detection, pruning, and index rebuild work

### D. Strong tool contracts

The tool contract idea is very useful if AQL exposes retrieval and research operations as agent tools.

Important fields to copy conceptually:

- read-only vs write
- concurrency-safe
- destructive
- max result size
- validation
- permission checks
- interrupt behavior

Recommendation:

- any future AQL / SAA tool surface should declare these properties up front

### E. Explicit task state machine

The task lifecycle pattern is a strong fit for long-running research and pipeline jobs.

Recommendation:

- model deep-research runs, consolidations, and monitor loops as tasks with states such as `pending`, `running`, `completed`, `failed`, and `killed`
- give every run a durable output/log path
- add typed task ids so logs are easier to scan

### F. Live agent summary

The lightweight progress-summary idea is valuable for long-running research.

Recommendation:

- expose a short live status string for long AQL jobs such as:
  - `Ranking evidence for rates rotation`
  - `Reading Seeking Alpha for NVDA demand thesis`
  - `Verifying event-card causal claims`

This is better than a blank spinner for multi-minute workflows.

### G. Always-on monitoring

The always-on monitor idea fits the attention system well.

Recommendation:

- treat the attention pipeline as the first version of an always-on market monitor
- later, let SAA maintain standing watches on entity sets, macro regimes, and narrative shifts instead of relying only on fixed cron-style refreshes

## 12. What To Borrow Carefully

Some ideas are good, but should not be first priority.

### A. Speculative execution

Copy-on-write speculative execution is powerful, but not the first bottleneck here.

Why not first:

- the current biggest issue is lost evidence and weak retrieval, not idle time between steps
- speculation adds complexity and operational surface area

Recommendation:

- revisit only after full-text retention, hybrid retrieval, and evidence-pack writing are in place

### B. Remote deep planning sessions

Long-running remote planning is useful for deep-research mode, but it is an add-on, not the foundation.

Recommendation:

- keep this as a future `deep research` mode for complex investigations, not the default path for normal homepage or event-card runs

## 13. Recommended Tech Stack

Best practical architecture for this repo:

- Blob storage for raw payloads and full text
- Postgres for metadata, structured filters, run history, and graph tables
- OpenSearch for lexical full-text search and faceting
- `pgvector` in Postgres for semantic retrieval
- existing LLM stack for planning, summarization, critique, and claim extraction

Why this stack:

- strong IR without forcing every problem into a vector DB
- structured filters remain first-class
- full-text search becomes much better than pandas-frame scanning
- semantic retrieval is added without introducing too many new moving parts

## 14. What To Avoid

Avoid these anti-patterns:

- treating snippets as canonical documents
- storing only the first few chunks of a document
- conflating search results with evidence
- writing directly from raw retrieval without an evidence notebook
- mixing UI formatting concerns into retrieval logic
- making each product surface invent its own research loop
- adding a NoSQL store just because the data is document-shaped

Generic NoSQL is not the right answer here.

The real needs are:

- durable document storage
- structured metadata queries
- strong full-text retrieval
- semantic similarity
- agent working memory

That is better served by blob + Postgres + OpenSearch + vector support.

## 15. Migration Plan

### Phase 1. Stop losing information

- retain full fetched page text
- retain provider raw payloads
- add durable source-document storage outside container cache
- stop collapsing all search evidence into snippet-only `raw_text`

### Phase 2. Fix retrieval

- add full-text index
- add vector embeddings for chunks and document summaries
- replace `head(6)` and first-chunk logic with ranked retrieval
- add hybrid retrieval and reranking

### Phase 3. Add evidence notebook

- create a shared `EvidencePack`
- make homepage summary, event cards, and agent answers all use it
- persist gap checks and critique output

### Phase 4. Add full agent loop

- planner
- retriever
- gap detector
- targeted follow-up retrieval
- writer
- critic

### Phase 5. Unify product surfaces

- homepage
- narrative cards
- ticker pages
- omnibar
- API agent endpoints

All should share the same research system.

## 16. AQL / SAA Boundary

Keep the boundary clean:

### AQL owns

- task planning
- agent loops
- evidence-pack assembly
- writer prompts
- critique and gap handling
- surface-specific outputs

### SAA owns

- fetch orchestration
- raw content retention
- document and chunk storage
- document summaries
- claims
- lexical index
- semantic index
- structured filters
- retrieval and reranking services

This keeps AQL from turning into a pile of storage and index code, while also keeping SAA focused on evidence plumbing instead of product writing.

## 17. Immediate Recommendation

If implementation starts now, the highest-value order is:

1. full-text retention and durable document storage
2. ranked hybrid retrieval over documents, summaries, chunks, and claims
3. evidence notebook + gap detection
4. surface-specific writers backed by the same evidence pack
5. critique loop for generic or weak answers

That is the path from a useful search-backed prototype to a real research-grade agent system.
