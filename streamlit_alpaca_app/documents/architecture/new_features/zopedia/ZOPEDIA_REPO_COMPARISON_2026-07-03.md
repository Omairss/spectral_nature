# Zopedia Core Research Architecture Comparison

Date: 2026-07-03

Scope: core wiki and LLM research architecture in the external `zohairshafi/zopedia` repository compared with Spectral Nature's current AQL/Zopedia architecture. This note intentionally ignores generic upload UX, YouTube ingestion, local packaging, and other product polish.

## Sources Checked

- External repo checked: `https://github.com/zohairshafi/zopedia`, cloned at `c3988f5a` on `main`.
- Latest visible tag from GitHub refs: `v1.1.6`. Current `main` still resolves to `c3988f5a`.
- External implementation files checked: `README.md`, `ARCHITECTURE.md`, `backend/core/wiki/engine.py`, `backend/core/research.py`, `backend/prompts.py`, `backend/core/wiki/watcher.py`, `backend/core/wiki/manager.py`.
- Local Spectral Nature files checked: `services/saa/zopedia.py`, `services/aql_zopedia_engine.py`, `services/zopedia_agent.py`, `services/aql/summarizer.py`, `services/aql/critique.py`, `services/aql/writer.py`, `services/agent_tools.py`, and guardrails.

## Executive Read

The useful part of the external Zopedia repo is not its UI. It is the wiki maintenance architecture:

- source material becomes structured entity/concept/source/analysis pages;
- entity and concept pages have stable sections for Summary, Facts, Contradictions, Assumptions, Data, Sources, analysis backlinks, merge history, and incremental updates;
- new evidence updates existing pages instead of only creating fresh summaries;
- maintenance repairs the wiki through linting, fallback retries, enrichment, backlinks, community indexes, duplicate merges, and LLM compaction;
- query/research quality improves because the LLM reads a maintained memory graph, not a pile of past answers.

Spectral Nature is stronger on evidence governance: Postgres storage, source refs, audit rows, reversible mutations, proposals, rollback, AQL tools, critique, and model-call telemetry. The gap is the durable knowledge lifecycle. Spectral has typed pages, but the page body is mostly free-form Markdown plus metadata. It does not yet have the external repo's structured knowledge sections, incremental update ledger, source-grounded compaction, or LLM maintenance pass as a first-class engine capability.

For better summaries and research output, the right move is to strengthen Spectral's memory architecture, not add a separate Research Mode. The priority should be: source-grounded page schema, update/merge/compaction lifecycle, community index quality, and synthesis prompts that read those maintained fields before writing summaries.

## External Zopedia Core Architecture

### Wiki Structure

The external repo uses a simple but important folder model:

- `raw/`: original source files, treated as immutable input.
- `wiki/entities/`: named people, companies, products, APIs, tools, places, and specific things.
- `wiki/concepts/`: ideas, methods, themes, protocols, principles, and abstract topics.
- `wiki/analysis/`: query answers and source-first reports filed back into the wiki.
- `wiki/sources/`: source summaries and source-grounded pages.
- `wiki/godnodes/`: community pages generated from the link graph.
- `index.md`, `index-concise.md`, `index-godnodes.md`: increasingly compact navigation layers.

The important design is not the filesystem. It is the separation of durable knowledge pages from analysis pages and source pages.

### Extraction Contract

On ingest, the external repo asks the LLM for:

- `summary`;
- `entities[]`;
- `concepts[]`;
- each entity/concept has `name`, `summary`, `facts[]`, `contradictions[]`, `assumptions[]`, `data[]`, `tags[]`, and `resources[]`.

This is better for long-term LLM output than a single free-form summary. Later prompts can retrieve exact facts, known contradictions, assumptions, numeric data, and source resources instead of re-inferring them from prose.

### Knowledge Page Lifecycle

Entity/concept pages are maintained as living knowledge pages:

- first write creates a stable page with Summary, Facts, Contradictions, Assumptions, Data, and Sources;
- later source ingests update the same page where possible;
- Summary can be replaced while the old version is archived in Incremental Updates;
- new facts, contradictions, assumptions, and data are deduplicated against existing and incremental bullets;
- source references are appended as source updates;
- duplicate candidates are marked during ingest and can be merged later.

This is the core architecture worth studying. It makes durable memory improve over time instead of becoming a stack of isolated one-off summaries.

### Maintenance Lifecycle

The external repo runs maintenance as part of the wiki lifecycle:

- lint finds orphan pages, stale pages, broken links, missing concepts, and duplicate candidates;
- fallback retry reruns analysis pages that previously used extractive fallback;
- enrichment repairs/expands analysis links, refreshes old pages, and can fill missing concept gaps;
- backlinks regenerate "Referenced by Analyses" sections;
- god-node rebuild clusters the graph and writes compact community indexes;
- compaction rewrites pages with too many incremental updates;
- merge maintenance combines duplicate entity/concept pages.

Spectral should not copy this exact filesystem process. It should adopt the lifecycle roles inside the existing Postgres/AQL/Zopedia engine.

### Compaction

External Zopedia compaction is a real LLM maintenance step:

- it scans entity/concept pages for too many `## Incremental Updates` blocks;
- pages with the most overflow are processed first;
- the LLM rewrites the whole page, merging updates into Summary, Facts, and Contradictions;
- Sources, Referenced by Analyses, wikilinks, dates, and recent changes are preserved;
- if the LLM fails, it falls back to trimming older update blocks.

This directly affects summary quality. A clean compacted page gives the next answer a current, deduplicated representation of the topic. A bloated page forces the LLM to reconcile stale fragments during every query.

### Analysis Pages

The external repo stores analysis output as first-class wiki pages. Long sources can be chunked, analyzed by chunk, then merged into a source-level analysis report with strict sections:

- brief summary;
- key takeaways;
- wiki updates;
- equations/data;
- caveats;
- assumptions;
- source/conversation classification;
- disputable claims;
- time sensitivity;
- citations/references.

This is relevant to Spectral because it creates durable intermediate research artifacts. Spectral already has evidence packs and traces, but the user-facing memory layer should preserve the same kind of reusable analysis structure.

## Spectral Nature Current Architecture

### What Is Stronger

Spectral already has a stronger production foundation:

- typed Postgres pages in `saa_zopedia_pages`;
- source document IDs, source URLs, entity refs, outgoing links, versions, status, and metadata;
- full-text search vector over title, summary, and body;
- change proposals;
- mutation audit rows with before/after state and rollback hints;
- safe typed mutations: `upsert_pages`, `link_pages`, `metadata_patch`;
- backlinks, community index, and maintenance report tables;
- AQL/Zopedia tool access to retained evidence, live evidence, source read-through, page search/read, proposals, mutations, rollback, and bounded Python analysis;
- answer judge, trajectory monitor, and post-answer memory reflection.

This foundation is better than the external repo for auditability, rollback, and financial-product governance.

### What Is Weaker

The durable page contract is weaker for LLM memory quality:

- page body is broad Markdown, not a structured knowledge document with enforced Facts/Contradictions/Assumptions/Data sections;
- Spectral currently has `source`, `concept`, and `entity` Zopedia page types, but not a first-class `analysis` page type comparable to the external repo's `wiki/analysis/` folder;
- source extraction returns page objects with `page_type`, `title`, `summary`, `body_markdown`, `entity_refs`, and `outgoing_links`, but does not require structured fact/contradiction/assumption/data fields;
- maintenance currently derives structural issues such as broken links, weak sources, orphan pages, stale pages, bloated pages, duplicate candidates, and communities;
- maintenance reports issues but does not yet run the full LLM lifecycle: update sections, compact pages, merge duplicates, refresh source-grounded facts, and rewrite bloated pages;
- compaction is flagged as `compact_with_sources`, but there is no first-class source-grounded page rewrite lifecycle comparable to the external repo's LLM compaction.

That is the architecture gap that matters for LLM output.

## What To Take From The External Repo

### 0. Folder Hierarchies In Postgres

Do not make separate page tables for folders. Keep one canonical page table, then add a folder tree and page memberships on top of it. `page_type` alone is not enough for hierarchy because pages may need both a canonical location and secondary placements in community/index folders.

Current state: Spectral has `page_type`, `slug`, `page_id`, links, backlinks, and communities. It does not yet have durable `parent_folder_id`, `canonical_path`, or folder membership fields.

Replicate the external repo's folder semantics this way:

| External folder/file | Spectral Postgres equivalent |
|---|---|
| `raw/` | retained documents/evidence store plus `source` pages with `source_document_ids_json` and `source_urls_json` |
| `wiki/sources/` | folder row `wiki/sources`, containing pages with `page_type = 'source'` |
| `wiki/entities/` | folder row `wiki/entities`, containing pages with `page_type = 'entity'` |
| `wiki/concepts/` | folder row `wiki/concepts`, containing pages with `page_type = 'concept'` |
| `wiki/analysis/` | folder row `wiki/analysis`, containing new `analysis` or `research_report` pages |
| `wiki/godnodes/` | generated folder subtree backed by `saa_zopedia_community_index` |
| `index.md` | generated folder/view over all active pages |
| `index-concise.md` | generated compact folder/view over active pages by namespace |
| `index-godnodes.md` | generated folder/view over community folders and central pages |

Add two hierarchy tables:

```sql
CREATE TABLE saa_zopedia_folders (
    folder_id TEXT PRIMARY KEY,
    tree_key TEXT NOT NULL DEFAULT 'zopedia',
    parent_folder_id TEXT REFERENCES saa_zopedia_folders(folder_id),
    folder_type TEXT NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    path TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    metadata_json JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL,
    updated_at_utc TIMESTAMPTZ NOT NULL,
    UNIQUE (tree_key, path)
);

CREATE TABLE saa_zopedia_page_folder_memberships (
    folder_id TEXT NOT NULL REFERENCES saa_zopedia_folders(folder_id),
    page_id TEXT NOT NULL REFERENCES saa_zopedia_pages(page_id),
    membership_type TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    metadata_json JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL,
    updated_at_utc TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (folder_id, page_id, membership_type)
);
```

Use these membership types:

- `primary`: the page's canonical folder path.
- `alias`: an intentional second placement.
- `community`: generated placement from community/god-node maintenance.
- `index`: generated placement for index views.

The initial tree should be:

```text
wiki/
  sources/
  entities/
  concepts/
  analysis/
  godnodes/
  indexes/
```

Each page gets one `primary` membership. For example:

- `page_type='entity'`, title `Nvidia` -> primary folder `wiki/entities`, canonical path `wiki/entities/nvidia`;
- `page_type='concept'`, title `Data Center Capex` -> `wiki/concepts/data-center-capex`;
- `page_type='source'`, title `Nvidia FY2026 Q1 10-Q` -> `wiki/sources/nvidia-fy2026-q1-10-q`;
- `page_type='analysis'`, title `Nvidia Demand Stack` -> `wiki/analysis/nvidia-demand-stack`.

`canonical_path` can be stored on `saa_zopedia_pages` as a denormalized read field, but the source of truth should be the `primary` folder membership. Product links and mutations should still use `page_id`; paths are for navigation, retrieval hints, and human inspection.

Path maintenance rules:

- moving a folder updates `saa_zopedia_folders.path` for that folder and descendants, not the page IDs;
- renaming a page updates the primary membership path alias, but links remain stable because they resolve by `page_id`;
- old paths should be kept in a small alias/redirect table if URLs or saved references need backward compatibility;
- generated folders such as `godnodes/` and `indexes/` are rebuilt by maintenance, not manually edited;
- subtree reads use either recursive CTEs over `parent_folder_id` or prefix scans over `path`; if we need fast deep subtree queries later, add Postgres `ltree` as an optimization, not the first design.

### 1. Structured Knowledge Sections

Add a canonical Spectral memory page contract for durable research pages:

- Summary;
- Facts;
- Contradictions;
- Assumptions;
- Data;
- Sources;
- Referenced by Research;
- Merge History;
- Incremental Updates;
- Recent Changes.

These can live inside `body_markdown` at first, but the engine should treat them as structured sections, not arbitrary prose. Longer term, Facts/Contradictions/Assumptions/Data should become typed JSON fields or derived indexed rows so prompts can select them directly.

### 2. Source-Grounded Extraction

Upgrade source extraction so the LLM produces durable knowledge fields, not just page prose:

- for every entity/concept/theme/ticker/macro/event page, return facts, contradictions, assumptions, data points, source refs, dates, confidence, and time sensitivity;
- require every claim-like bullet to be traceable to the source text or retained evidence;
- reject pages that lack source refs unless they are explicitly internal/index pages.

This should go through the AQL/Zopedia gateway. The current direct `llm_client.generate_json(...)` extraction path should be migrated or classified.

### 3. Incremental Update Lifecycle

When new evidence touches an existing page:

- replace Summary only when the newer source changes the current understanding;
- append old summary and novel facts into Incremental Updates;
- dedupe against existing Facts/Contradictions/Assumptions/Data;
- append Source updates with retained source refs;
- preserve contradiction candidates instead of smoothing them away.

This is a source fix for better summaries. The LLM should read a maintained current page, not resolve every historical update during final synthesis.

### 4. LLM Compaction

Implement a first-class `compact_zopedia_pages` engine operation:

- candidate selection from maintenance reports: bloated pages, too many update blocks, stale high-use pages, repeated contradictions, or duplicate pressure;
- load full page plus source refs/evidence excerpts;
- ask the LLM to rewrite Summary/Facts/Contradictions/Assumptions/Data;
- preserve sources, backlinks, dates, wikilinks/page IDs, merge history, and recent changes;
- write via `propose` or `safe_auto` mutation policy with before/after audit and rollback;
- run a critic pass that checks rewritten bullets against source refs before commit.

This should improve LLM summaries more than tweaking final-answer prose prompts.

### 5. Maintenance As Repair, Not Just Reporting

Keep the current structural maintenance snapshot, but add repair phases:

- source coverage repair proposals for weak-source pages;
- link repair proposals for broken links;
- duplicate merge proposals with LLM rationale;
- stale-page refresh jobs that reopen source refs or live evidence;
- contradiction review that asks the judge to preserve both sides when evidence conflicts;
- community rebuild that names clusters using existing page summaries/facts, not only connected components.

Admin/System Health should show counts and diffs. Product research should benefit from the repaired memory without seeing maintenance plumbing.

### 6. Analysis Pages As Reusable Research Artifacts

Spectral already has evidence packs and traces. Add a memory-facing analysis artifact for high-value research runs:

- title;
- research question;
- answer summary;
- key facts;
- caveats;
- assumptions;
- disputed/unsupported claims;
- time sensitivity;
- source refs;
- affected entities/pages;
- proposed memory updates.

This would make later summaries better because the agent can read prior analysis pages as structured context, not only search old answer text.

## Implementation Scope

Folder hierarchy is only the navigation and organization layer. It does not replace the rest of the LLM architecture work. The full implementation should include these pieces:

1. **Folder hierarchy.** Add folder and page-membership tables so Postgres can represent `wiki/entities/`, `wiki/concepts/`, `wiki/sources/`, `wiki/analysis/`, `wiki/godnodes/`, and generated indexes without splitting pages across separate tables.
2. **Analysis page type.** Add a first-class `analysis` or `research_report` page type so durable AQL/Zopedia research outputs can be stored separately from entities, concepts, and sources.
3. **Structured page sections.** Give durable memory pages canonical sections: Summary, Facts, Contradictions, Assumptions, Data, Sources, Referenced by Research, Merge History, Incremental Updates, and Recent Changes.
4. **Source-grounded extraction.** Upgrade extraction so the LLM writes structured facts, contradictions, assumptions, data points, source refs, dates, confidence, and time sensitivity instead of only broad page Markdown.
5. **Incremental updates.** When new evidence touches an existing page, append novel facts/data/source refs into an update ledger, archive replaced summaries, dedupe bullets, and preserve contradictions.
6. **LLM compaction.** Add an engine operation that rewrites bloated pages by merging update history into current sections while preserving source refs, dates, links, backlinks, merge history, and recent changes.
7. **Maintenance repairs.** Extend maintenance from reporting into proposals or safe mutations for link repair, source coverage, duplicate merges, stale refreshes, contradiction review, and community rebuilds.
8. **Research/synthesis integration.** Change summary and research prompts so the agent reads maintained page sections and analysis pages before final synthesis; judges should evaluate answer claims against Facts/Data/Sources and known contradictions.

The hierarchy work should come first because it gives analysis pages, god-node communities, and generated indexes somewhere coherent to live. But it is only the first layer.

## Impact On LLM Output Quality

The final summary prompts are not the main lever. Better output needs better memory shape before synthesis:

- Facts let the model write specific claims without re-reading every source.
- Contradictions stop the model from flattening conflicting evidence into a confident answer.
- Assumptions make caveats explicit and reduce vague hedging.
- Data fields give summaries numbers and dates without stat-dump prose.
- Incremental updates preserve change history without polluting the main page.
- Compaction keeps pages current, concise, and readable.
- Analysis pages preserve prior reasoning as reusable research, not transient chat output.

Prompt work still matters, but it should sit on top of this architecture:

- final summaries should read current compacted pages plus source refs;
- research answers should cite source-backed facts and name contradictions/gaps;
- judges should compare answer claims against Facts/Data/Sources, not raw body prose alone;
- memory reflection should write structured section updates, not loose page bodies.

## Recommended Next Work

1. Write a Spectral Zopedia page-section contract for durable research pages.
2. Upgrade source extraction to return Facts, Contradictions, Assumptions, Data, source refs, dates, and time-sensitivity fields through the AQL/Zopedia gateway.
3. Implement incremental update and dedupe logic for existing pages.
4. Implement source-grounded LLM compaction with proposal/safe-auto write policy and rollback.
5. Extend maintenance from reporting to repair proposals: link repair, source coverage, duplicate merge, stale refresh, contradiction review.
6. Add structured analysis pages for high-value AQL/Zopedia research runs.
7. Rework summary/research prompts to read the maintained fields first and only then synthesize final prose.

## Bottom Line

The external Zopedia repo is architecturally useful because it treats the wiki as maintained LLM memory: structured pages, incremental updates, compaction, merge, backlinks, and community indexes. Spectral Nature already has the better production substrate. To improve LLM summaries and research, Spectral should add the missing memory lifecycle inside its existing AQL/Zopedia engine rather than copy the external app's UI or source-ingestion surfaces.
