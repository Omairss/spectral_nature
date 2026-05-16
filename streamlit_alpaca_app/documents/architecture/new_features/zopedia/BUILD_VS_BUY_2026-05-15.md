# Zopedia Build vs Buy Decision

Date: 2026-05-15

## Question

Should Spectral Nature run Zopedia as-is as a separate Container App and endpoint, or learn from it and integrate the useful parts into the product?

## Short Answer

Use Zopedia as a design reference and integration prototype, not as the long-term production subsystem.

Recommended path:

```text
Short term: run Zopedia separately only as a dev/internal evaluation service.
Product path: integrate the durable memory model into SAA and the chat/tool-loop ideas into AQL/Agents.
Do not make the standalone Zopedia app the canonical production memory or chat layer.
```

## Option A: Run Zopedia As-Is

### Benefits

- Fastest way to see the full product working.
- Existing chat UI already supports reasoning display, wiki reads, web search, uploads, and graph exploration.
- Existing backend already handles file ingestion, YouTube transcript ingestion, wikilink graph, maintenance, and community indexes.
- Lower first-week engineering cost.

### Costs

- Creates a second chat/reasoning stack beside AQL/Agents.
- Creates a second evidence/memory store beside SAA.
- Creates a second frontend beside Streamlit.
- Creates separate auth, config, deploy, observability, and secret-management surfaces.
- Filesystem-first wiki storage does not match Spectral Nature's DB/materialized dataset patterns.
- Tooling overlaps with existing AQL web/page research and SAA retained evidence.
- Harder to make market-specific guarantees around evidence, citations, freshness, and reviewed write-back.

### Good Use

Use as a dev-only evaluation container when we want to test the raw Zopedia UX quickly.

Avoid as production canonical infrastructure.

## Option B: Integrate Zopedia Concepts

### Benefits

- Keeps one source of truth for retained evidence in SAA.
- Keeps one reasoning layer in AQL/Agents.
- Lets YouTube transcripts, web pages, PDFs, and wiki pages become normal SAA evidence.
- Lets the graph explorer use shared product auth, data access, logging, and review controls.
- Lets AQL use wiki memory without becoming the wiki storage engine.
- Fits existing module boundaries:

```text
UI -> Agents -> AQL -> SAA
```

### Costs

- Slower initial build.
- Requires designing SAA wiki tables/API instead of importing the filesystem engine wholesale.
- Requires porting the best pieces carefully:
  - wikilink parsing
  - graph snapshot
  - page reader
  - transcript ingestion
  - maintenance reports
  - graph explorer interactions

### Good Use

This should be the production path.

## Option C: Hybrid Adapter

Run Zopedia as a sidecar service, but only behind a Spectral Nature adapter.

Possible adapter API:

```text
SAA/ZopediaAdapter.search_pages(...)
SAA/ZopediaAdapter.read_page(...)
SAA/ZopediaAdapter.get_graph(...)
SAA/ZopediaAdapter.ingest_source(...)
```

This can work for a limited pilot, but it should be treated as transitional. The adapter should make it easy to swap the backend from standalone Zopedia to native SAA storage later.

## Recommendation

Use a hybrid sequence:

1. **Pilot Zopedia separately in dev only** if we want hands-on validation of UX and maintenance behavior.
2. **Do not expose standalone Zopedia as the product chat endpoint.**
3. **Build native SAA wiki memory** using Zopedia's page/link/maintenance model.
4. **Expose AQL/Agents tools** for wiki read/search/neighborhood retrieval.
5. **Port graph explorer UX** into the existing product UI.
6. **Add transcript ingestion** as an SAA source acquisition feature.

## Why Not Standalone Production

The standalone app is attractive because it is already complete, but it would create duplicate infrastructure at exactly the wrong layer:

- duplicate chat agent
- duplicate memory
- duplicate graph UI
- duplicate LLM config
- duplicate source ingestion

That would make the product faster to demo but harder to trust, operate, and evolve.

## Decision

Treat Zopedia as an upstream reference implementation.

Use it to accelerate design and testing, but integrate the concepts into Spectral Nature's own SAA, AQL, Agents, and UI boundaries for production.
