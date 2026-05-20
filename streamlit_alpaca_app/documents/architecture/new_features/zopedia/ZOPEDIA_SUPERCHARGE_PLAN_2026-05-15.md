# Zopedia Supercharge Plan

Date: 2026-05-15

Source reviewed: `https://github.com/zohairshafi/zopedia`

## Purpose

Rename **Chat + Search** to **Zopedia** and turn it into a first-class research memory product.

This is not a small rename. The bar is high: the result must feel as strong as standalone Zopedia, while fitting Spectral Nature's AQL, SAA, Agents, UI, and KG boundaries. A weaker partial clone would add negative value because it would create more state, more flows, and more maintenance without giving users the compounding memory loop that makes Zopedia compelling.

## Decision

Use Zopedia as a product and architecture reference, not as a production runtime dependency.

Important prerequisite:

**Fix AQL as the shared LLM/evidence layer before building Zopedia into the product.**

See: `documents/architecture/AQL/AQL_LLM_UNIFICATION_PLAN_2026-05-15.md`

Recommended target:

```text
Zopedia page in Spectral Nature
  -> Agents own conversation runtime and tool trace
  -> AQL owns planning, reasoning, synthesis, critique, and write-back proposals
  -> SAA owns durable memory, source ingestion, wiki pages, indexes, and retrieval
  -> KG owns typed market relationships
  -> UI owns chat, graph exploration, upload, maintenance controls, and review flows
```

Do not run a second production chat stack beside AQL/Agents. Do not make standalone Zopedia the canonical production endpoint.

## Implementation Status

Current completion checklist:

- `ZOPEDIA_PARITY_COMPLETION_PLAN_2026-05-17.md`

### 2026-05-16 LLM-Native Cleanup

- Visible **Chat + Search** surfaces and LLM config groups were renamed to **Zopedia**.
- Page summaries now enter through AQL/Zopedia evidence first and fail closed when grounded synthesis is unavailable.
- The old deterministic page-summary fallback was removed; feature pages now offer **Analyze in Zopedia** with the current page context.
- The legacy attention feed brief helper was deleted instead of being kept as an unused direct-LLM narrative path.
- Streamlit presentation loaders no longer import the LLM client for feed briefs or trading suggestions.
- Broad Economy product copy no longer exposes stationarization implementation details or materialized/scheduled summary state.

Open risks:

- Trading Agent still has a scheduled low-confidence fallback candidate path because earlier product guardrails require reviewable rows for each horizon. If the new standard is "no deterministic fallback candidates," remove that contract deliberately and update the Trading Agent guardrails/tests.
- Internal API/module names still use `omnibar` for compatibility. Rename only after endpoint/session compatibility is planned.

## Product Promise

Zopedia inside Spectral Nature should become:

> A market-aware research workspace that remembers source material, turns it into an interlinked knowledge base, lets the agent read and reason through that memory, and improves the memory through reviewed updates.

The user should be able to:

1. Ask a market or research question.
2. See the agent think, search, read, and cite sources.
3. Upload or paste a source, including YouTube links.
4. See that source become retained evidence and, when useful, wiki memory.
5. Explore the memory graph visually.
6. Review proposed additions, links, merges, archives, and KG updates.
7. Trust that the system gets better over time instead of only answering one-off prompts.

## What Zopedia Has That We Should Extract

### 1. Memory As A Wiki, Not Just Search Results

Zopedia stores durable generated pages:

- `sources/`
- `entities/`
- `concepts/`
- `analysis/`
- `godnodes/`
- `index.md`
- `index-concise.md`
- `index-godnodes.md`

Spectral Nature has retained SAA evidence and AQL traces, but it does not yet have this durable, user-facing, interlinked wiki memory.

Extraction:

- Add a SAA-owned wiki memory layer.
- Keep original source evidence as source of truth.
- Generate wiki pages as navigational and reasoning artifacts.
- Version every generated page.
- Keep generated memory reviewable and inspectable.

### 2. Universal Source Ingestion

Zopedia accepts:

- file uploads
- pasted URLs
- PDFs
- DOCX/PPTX/XLSX
- HTML/CSV/EPUB
- images and audio through MarkItDown when configured
- ZIP/text/code
- YouTube URLs through transcript fetch
- tweets/X links
- arXiv links

Spectral Nature already has live web/page research and retained evidence, but not a user-facing "drop anything into memory" workflow.

Extraction:

- Add SAA source acquisition APIs for upload and URL ingest.
- Add YouTube transcript support as a first source type.
- Consider MarkItDown for broad document conversion, but wrap it behind SAA so failures are contained.
- Store raw source, normalized markdown, source metadata, and extraction status.

### 3. Tool-Calling Wiki Retrieval

Zopedia's chat flow is:

```text
system prompt includes compact wiki index
model chooses page/community paths
model calls read_wiki_page
model follows wikilinks/backlinks
model optionally calls web_search
final answer streams after tool resolution
```

Spectral Nature has an agent tool loop and SAA search, but the agent does not yet have a wiki navigation mode. It searches chunks; it does not browse memory like a connected knowledge base.

Extraction:

- Add AQL/Agent tools:
  - `zopedia.search_pages`
  - `zopedia.read_page`
  - `zopedia.neighborhood`
  - `zopedia.graph_path`
  - `zopedia.ingest_source`
  - `zopedia.propose_change`
- Add a compact memory index to the planner context.
- Teach AQL when to use wiki memory before live search.
- Keep final answer grounded in evidence and citations.

### 4. Hierarchical Community Index

Zopedia solves scaling by clustering pages:

- build full link graph
- build bipartite graph between entity/concept pages and source/analysis pages
- project into entity and concept spaces
- run greedy modularity community detection
- write one `godnodes/*` page per community
- expose `index-godnodes.md` as a compact table of contents

This is one of the most valuable ideas. It lets the model navigate memory without stuffing every page into context.

Extraction:

- Add SAA memory communities.
- Store community membership and summaries in DB tables.
- Use this index in AQL planning.
- Use the same communities in the graph explorer.
- Rebuild communities on a schedule, not on every ingest.

### 5. Maintenance Lifecycle

Zopedia has a full health loop:

- lint orphans
- detect stale pages
- detect broken links
- detect missing concepts
- retry fallback analyses
- enrich analysis pages with links
- refresh backlinks
- compact bloated pages
- merge duplicate entity/concept pages
- rebuild community index
- archive stale source pages

Spectral Nature has pipeline jobs and critique passes, but no first-class memory hygiene system.

Extraction:

- Add a SAA wiki maintenance job.
- Make maintenance report visible in the Zopedia UI.
- Keep risky operations as proposals first.
- Auto-run safe checks, not destructive edits.

### 6. Graph Explorer UX

Zopedia's graph explorer has important UX decisions:

- Explore mode separate from Delete Queue mode.
- Strict 1-hop and 2-hop focus.
- Hide unfocused nodes for dense graphs.
- Search/list selection still works when nodes are hidden.
- Delete preview before apply.
- Add hidden nodes back into focus.
- Multiple layouts.

Spectral Nature has KG visualization work, but Zopedia's interaction model is more complete.

Extraction:

- Reuse these interaction patterns for the Zopedia memory graph.
- Keep the market KG graph explorer separate but visually consistent.
- Add tabs or modes:
  - Wiki Memory Graph
  - Market Relationship KG
  - Proposed Changes
  - Maintenance

### 7. Reasoning And Tool Trace UX

Zopedia exposes:

- reasoning controls
- reasoning stream when provider supports it
- tool status
- tool start
- tool end
- page read previews
- web search result status

Spectral Nature already captures planner/final reasoning traces and tool events in the omnibar agent. The UX exists but is not yet as productized or central.

Extraction:

- Rename "Thinking Trace" to "Thinking" or "Reasoning".
- Promote tool trace from admin/debug to normal product UI.
- Keep raw model reasoning visible when available.
- Also show durable research trace:
  - pages read
  - tools called
  - sources opened
  - evidence retained
  - proposals emitted

### 8. Chat History As Memory

Zopedia can save chat history into `raw/` so it gets ingested as source material.

Spectral Nature persists chat/agent findings and writes agent evidence to SAA, but does not expose this as a wiki-memory flow.

Extraction:

- Let users save a thread or answer into Zopedia memory.
- Turn high-quality agent answers into `analysis` pages after review.
- Use AQL critique before memory write-back.

## What We Already Have

Spectral Nature is not starting from scratch.

### Already Strong

- AQL agentic planning and synthesis.
- AQL critique/judge loop.
- SAA retained documents and evidence chunks.
- SAA lexical and semantic search.
- Market-specific tools and data access.
- Live web/page research.
- Attention pipeline and materialized artifacts.
- KG proposal flow.
- Entity extraction and entity taxonomy.
- Chat + Search tool loop.
- Reasoning trace capture for DeepSeek-like providers.
- Auth, deployment, config, and production infrastructure.

### Missing Or Weak

- Durable wiki memory pages.
- User-facing upload/source ingestion workflow.
- YouTube transcript ingestion.
- Wiki page reader and graph neighborhood tools.
- Community/god-node memory index.
- Wiki maintenance lifecycle.
- Memory graph explorer at Zopedia quality.
- Reviewed memory write-back workflow.
- Productized reasoning/tool trace, not just debug/admin surfaces.

## Target Architecture

### Dependency Direction

Keep the existing module direction:

```text
UI -> Agents -> AQL -> SAA
UI -> public service APIs
Pipelines -> public module APIs
KG service is called through public graph APIs
```

Do not allow:

- SAA importing AQL or UI.
- AQL importing UI or pipeline jobs.
- UI mutating SAA internals.
- Agents duplicating SAA/AQL business logic.
- Zopedia memory writes without review history.

### Module Ownership

| Layer | Owns |
| --- | --- |
| UI | Zopedia page, chat UX, upload UX, graph explorer, review surfaces |
| Agents | conversation runtime, tool routing, progress events, session history |
| AQL | planning, evidence-pack assembly, reasoning, synthesis, critique, proposal generation |
| SAA | source ingestion, normalized source store, wiki pages, links, chunks, indexes, retrieval, maintenance |
| KG | typed market relationship graph with direction/confidence/severity/evidence |
| Pipelines | scheduled maintenance, materialization, job status |

## Native SAA Wiki Memory

Create a SAA-owned wiki package:

```text
services/saa/wiki/
  __init__.py
  models.py
  storage.py
  ingestion.py
  pages.py
  links.py
  graph.py
  communities.py
  maintenance.py
  proposals.py
  retrieval.py
```

### Public API

```python
ingest_source(source, *, source_type="auto", user_id=None) -> IngestResult
search_wiki_pages(query, *, kinds=None, limit=10) -> list[WikiPageHit]
read_wiki_page(page_id, *, max_chars=12000) -> WikiPageRead
get_wiki_graph(*, include_analysis=True) -> WikiGraph
get_wiki_neighborhood(seed_page_ids, *, depth=1, kinds=None) -> WikiGraph
list_wiki_communities() -> list[WikiCommunity]
run_wiki_lint() -> WikiMaintenanceReport
propose_wiki_change(change_set) -> WikiChangeProposal
preview_wiki_delete(entry_type, entries, *, cascade=True) -> DeletePreview
apply_reviewed_wiki_change(review_id) -> ApplyResult
```

### Storage Tables

Prefer DB-backed state over filesystem-first state.

Core:

- `saa_wiki_pages`
- `saa_wiki_page_versions`
- `saa_wiki_links`
- `saa_wiki_page_entities`
- `saa_wiki_source_bindings`
- `saa_wiki_communities`
- `saa_wiki_community_members`
- `saa_wiki_change_proposals`
- `saa_wiki_maintenance_runs`

Optional export:

- markdown export under blob storage or local artifact paths
- imported/exported vault format for debugging

### Page Kinds

Use Zopedia's page kinds, with market-aware extensions:

- `source`: original source summary and source metadata
- `entity`: company, macro series, commodity, person, organization, policy, theme
- `concept`: reusable idea, mechanism, theme, market structure concept
- `analysis`: answer, report, event explanation, saved chat turn
- `community`: generated group/index page

### Page Contract

Each page should have:

- stable page ID
- kind
- title
- slug
- body markdown
- summary
- source evidence IDs
- linked entity IDs
- page status: active, archived, proposed, rejected
- created/updated timestamps
- generated_by: user, AQL, pipeline, import
- confidence/review state

## AQL Supercharge

### New Retrieval Order

For questions and attention events:

```text
1. Resolve intent and entities.
2. Search SAA retained evidence.
3. Search Zopedia wiki pages.
4. Read high-value wiki pages.
5. Expand 1-hop or 2-hop neighborhood if needed.
6. Use KG context only after entity resolution.
7. Fetch fresh web evidence when needed.
8. Build evidence pack.
9. Synthesize answer.
10. Critique and revise.
11. Emit proposals for memory/KG changes.
```

### New AQL Tools

Add tools visible to Agents:

- `zopedia.search_pages`
- `zopedia.read_page`
- `zopedia.expand_neighborhood`
- `zopedia.list_communities`
- `zopedia.ingest_url`
- `zopedia.propose_page`
- `zopedia.propose_link`
- `zopedia.propose_archive`
- `zopedia.propose_merge`

Existing research tools stay:

- `research.retained_context`
- `research.search_evidence`
- `research.live_event_evidence`
- `research.open_page`
- `hypothesis.verify`
- market/investigator tools

### Write-Back Policy

AQL may propose, not silently mutate:

- new source page
- new entity/concept page
- new wikilink
- updated page summary
- archive stale page
- merge duplicate pages
- add/update KG edge

AQL must include:

- evidence refs
- rationale
- confidence
- source pages used
- proposed diff
- user-visible preview

## Zopedia Page UX

Rename:

```text
Chat + Search -> Zopedia
```

This should be more than a sidebar label. The page should become a workspace with:

### Main Chat

- rich chat history
- reasoning/thinking panel
- tool trace panel
- source strip
- citations
- confidence/gaps
- "save to memory" action
- "propose memory update" action
- "dig deeper" action

### Source Intake

- upload files
- paste URL
- paste YouTube URL
- show ingestion status
- show extracted title/source type/transcript availability
- show generated pages/proposals

### Memory Browser

- pages grouped by source/entity/concept/analysis/community
- search and filters
- read page
- view backlinks
- view source evidence
- edit or propose edit

### Graph Explorer

- Explore mode
- Delete/Archive queue mode
- 1-hop/2-hop focus
- hide unfocused nodes
- add hidden seed
- layouts
- graph stats
- community coloring
- broken-link overlay
- proposed-link overlay

### Maintenance

- lint report
- stale pages
- broken links
- orphan pages
- duplicate candidates
- low-coverage sources
- community rebuild status
- compaction candidates
- review queue

## Current Chat + Search Refactor

Current code names still use omnibar terminology. The rename should be staged:

### User-Facing Rename First

- `AGENTIC_OMNIBAR_SECTION = "Zopedia"`
- prompt/config group label from `Chat + Search` to `Zopedia`
- visible captions and progress labels
- chat input placeholder
- admin prompt group labels

### Internal Rename Later

Avoid huge risky renames at first. Keep internal modules temporarily:

- `services.omnibar_agent`
- `services.omnibar_research`

Add compatibility names:

- `services.zopedia_agent`
- `services.zopedia_research`

Then migrate imports gradually.

## Quality Bar

This project is only worth doing if the result is Zopedia-quality or better.

### Non-Negotiables

1. User can ingest at least URL, YouTube, PDF/text, and pasted text.
2. Ingested sources become searchable SAA evidence.
3. Important sources can become wiki pages.
4. Agent can read wiki pages by exact ID.
5. Agent can navigate a compact community index.
6. Agent can show thinking/tool trace.
7. User can inspect sources and citations.
8. User can explore the memory graph.
9. User can review proposed memory changes.
10. Maintenance catches broken/orphan/stale/duplicate memory.

### Good Enough For MVP

MVP should include:

- Zopedia rename
- source ingestion for URLs and YouTube
- SAA wiki page tables
- generated source/entity/concept/analysis pages
- wiki search/read tools
- chat tool trace UI promoted out of debug
- graph explorer using wiki links
- lint report
- review queue for page/link proposals

Without these, it is just a renamed chat page and is not worth shipping.

### Better Than Standalone Zopedia

We can be better because we have:

- market data tools
- AQL hypothesis verification
- SAA retained evidence search
- KG typed relationships
- production auth/deploy
- scheduled attention runs
- source quality/citation expectations

Use those advantages. Do not build a generic personal wiki only.

## Phased Plan

### Phase -1: AQL LLM Unification

Goal:

- Make AQL the shared evidence and reasoning contract for all research-grade LLM output before Zopedia memory is added.

Why first:

- Zopedia should enrich the whole product, not only the renamed chat page.
- That only happens if attention summaries, page summaries, Trading Agent, stock/company pages, and chat all consume the same AQL evidence/memory contract.
- Current direct LLM helpers grew historically and need to be classified as either utility calls or formatter-only calls over AQL output.

Scope:

- Add formal AQL evidence-pack models.
- Make interactive agent runs and materialized jobs persist an AQL evidence-pack ID or serialized evidence pack.
- Migrate page summaries and Trading Agent first because they already call the shared agent path.
- Migrate attention feed briefs, EDGAR attention narratives, and live-research bundle synthesis behind AQL.
- Keep direct LLM calls only for narrow metadata extraction, routing, source classification, or final formatting over an AQL evidence pack.

Acceptance:

- Research-grade summaries have a source/citation/evidence-pack trace.
- The same event produces consistent facts across Zopedia chat, attention feed, stock page summaries, and Trading Agent.
- Formatter LLM calls cannot introduce unsupported facts beyond the AQL evidence pack.
- If AQL fails, surfaces show explicit `data_gaps`, not private fallback narratives.

### Phase 0: Parity Harness

Goal: prevent a weak clone.

Build a side-by-side evaluation set:

- generic uploaded PDF
- YouTube macro/markets video
- market news article
- prior attention summary
- company/ticker research question
- broad macro question
- memory graph exploration task

Compare:

- standalone Zopedia behavior
- current Chat + Search behavior
- new native Zopedia behavior

Track:

- answer quality
- citations/source trace
- memory pages created
- graph usefulness
- latency
- user-visible errors
- maintenance findings

### Phase 1: Product Rename And Trace UX

Scope:

- Rename visible Chat + Search to Zopedia.
- Promote Thinking Trace to normal UI.
- Show tool trace and model reasoning when available.
- Keep current agent runtime.
- No new storage yet.

Why first:

- Low risk.
- Makes the destination visible.
- Does not pretend memory exists before it does.

### Phase 2: Universal Source Intake Into SAA

Scope:

- URL ingest.
- YouTube transcript ingest.
- Basic file upload for text/PDF.
- Store source documents/chunks in SAA.
- Show ingestion status in Zopedia page.

Avoid:

- direct wiki mutation from ingest in the first pass.
- broad MarkItDown rollout before error handling is good.

### Phase 3: SAA Wiki Memory Core

Scope:

- Add wiki page tables.
- Generate source pages from ingested sources.
- Generate entity/concept candidates using entity extraction.
- Add page versions.
- Add wikilink parser.
- Add backlinks.
- Add `read_wiki_page` and `search_wiki_pages`.

Review policy:

- auto-create source pages
- propose entity/concept pages first unless confidence is high and source is internal/trusted

### Phase 4: AQL Wiki-Aware Retrieval

Scope:

- Add Zopedia tools to agent catalog.
- Add compact wiki index to planner context.
- Teach planner retrieval order.
- Add evidence-pack fields for wiki pages.
- Add citations for wiki pages and underlying sources.

Guardrail:

- wiki context can guide hypotheses, but final market claims still need source evidence.

### Phase 4.5: LLM Surface Rollout

Goal:

- Make the AQL upgrade improve every product surface that depends on LLM reasoning, not only the Zopedia chat page.

Important correction:

- This is not automatic unless each surface uses the shared AQL evidence contract.
- Some surfaces already call AQL or Chat + Search paths.
- Some surfaces still have direct LLM formatting helpers or materialized summary prompts.
- Those direct helpers should either call AQL for evidence/memory context or receive a prepared AQL evidence pack.

Shared AQL evidence contract should include:

- resolved entities and tickers
- retained SAA chunks
- Zopedia wiki pages read
- wiki backlinks/neighborhoods used
- KG paths or typed relationship proposals
- fresh/live evidence when current facts matter
- citations and source IDs
- critique findings
- proposed memory/KG updates
- explicit data gaps

Surface rollout:

| Surface | How Zopedia/AQL Should Improve It |
| --- | --- |
| Zopedia chat | Primary interactive surface; reads wiki pages, opens retained sources, cites evidence, proposes memory changes. |
| Attention level 1 feed / group summary | Use wiki memory for prior context, recurring themes, known relationships, and source history; write reviewed summaries as `analysis` pages. |
| Attention level 2 feed / stock summary | Use entity/company pages, prior catalysts, retained source evidence, and KG paths before synthesis; propose stale/changed relationships when new evidence conflicts with old memory. |
| Homepage summary | Consume materialized AQL/Zopedia evidence packs instead of generic dataset summaries; cite retained memory and live evidence separately. |
| Stock/company pages | Use Zopedia entity pages for durable company context, source pages for prior research, and live evidence for current catalysts. |
| Trading Agent | Use memory for background and recurring setup context, but keep trade/watchlist claims grounded in fresh data and critique. |
| Page-level summaries | Replace direct `llm.generate_json(page_data)` shortcuts with shared AQL-backed summary contracts where evidence quality matters. |
| Audio/TTS | Generate audio only from already-grounded materialized text; do not make TTS a separate reasoning layer. |
| KG explorer/review | Separate wiki links from typed market KG edges, while letting AQL propose reviewed updates to both. |
| Maintenance/admin | Show memory health, proposal queues, stale pages, duplicate pages, and failed ingest/analysis states. |

Acceptance:

- A prompt answered from Zopedia chat and the matching attention/page summary should use the same underlying memory/evidence contract.
- Surface-specific wording can differ, but facts, citations, source IDs, and gaps should match.
- No product surface should silently bypass AQL/SAA memory for research-grade claims unless it is clearly a lightweight display-only formatter.

### Phase 5: Graph Explorer

Scope:

- Build wiki memory graph from links.
- Add Explore/Delete modes.
- Add 1-hop/2-hop focus.
- Add hide unfocused.
- Add add-seed search.
- Add proposed-link overlay.
- Add broken-link overlay.

Reuse:

- Zopedia `wiki-data-dialog.tsx` interaction model.
- Existing Streamlit graph/KG work for rendering.

### Phase 6: Maintenance And Community Index

Scope:

- lint job
- broken links
- orphans
- stale pages
- duplicate candidates
- community/god-node index
- compaction candidates
- maintenance dashboard

Schedule:

- lightweight checks after ingest
- heavier community rebuild on cron or manual action

### Phase 7: Reviewed Write-Back

Scope:

- AQL proposes page/link/archive/merge updates.
- User reviews in Zopedia UI.
- Accepted changes update SAA wiki tables.
- Accepted typed market relationships go to KG proposal/commit flow.

### Phase 8: Full Product Polish

Scope:

- source upload polish
- thread save-to-memory
- graph layout quality
- memory page editor
- performance limits
- admin metrics
- import/export
- dev/prod rollout docs

## Acceptance Tests

### Source Ingestion

- YouTube URL produces a retained source with transcript or clear unavailable state.
- PDF/text upload produces source document and searchable chunks.
- URL ingestion stores canonical URL, title, captured_at, source type.

### Wiki Memory

- Source page can be loaded by page ID.
- Entity/concept pages link back to sources.
- Wikilinks produce graph edges.
- Archived pages do not appear in active retrieval or graph.

### Agent Behavior

- Agent chooses wiki memory for known prior material.
- Agent chooses live search for current facts.
- Agent cites pages and source documents.
- Tool trace shows wiki reads, source opens, and searches.
- Raw reasoning displays when provider returns it.

### Graph UX

- Large graph remains usable.
- Hide unfocused removes noise.
- Search/list still works when graph nodes are hidden.
- Delete/archive operation has preview before apply.

### Maintenance

- Broken link is reported.
- Orphan page is reported.
- Duplicate candidate is proposed, not silently merged.
- Community index rebuild produces compact communities.

## Complexity Controls

This work should stop if it becomes a parallel app inside the app.

Avoid:

- copying Zopedia's full `engine.py`
- standalone production Zopedia endpoint
- filesystem-first source of truth
- a second chat agent
- AQL owning wiki storage
- hidden automatic memory mutation
- graph edges that mix wikilinks and causal KG edges without type labels

Prefer:

- small SAA public APIs
- reviewable changes
- DB-backed state
- explicit source provenance
- measurable parity against standalone Zopedia
- incremental rollout behind feature flags

## Licensing Risk

Zopedia files include AGPL headers and the README says it retains AGPL lineage from Unsloth Studio.

Use the repo as a design reference unless legal review says direct code import is acceptable. Reimplementing core ideas behind our own interfaces is safer than copying runtime code.

## Rollout

### Dev Only

- rename page
- source intake
- wiki memory tables
- AQL tools
- graph explorer
- maintenance dashboard

### Production

No production rollout until:

- parity harness passes
- maintenance/reporting works
- memory writes are reviewable
- source/citation behavior is reliable
- licensing approach is clear

## Final Recommendation

Build native Zopedia inside Spectral Nature.

Do it only if we commit to the full memory loop:

```text
ingest -> retain -> extract -> page -> link -> retrieve -> reason -> cite -> propose -> review -> maintain
```

If we only rename Chat + Search and add a few tools, do not do it. That would create complexity without the core compounding-memory value that makes Zopedia worth copying.

## 2026-05-16 Implementation Pass

Implemented native Zopedia v1 inside Spectral Nature:

- Added SAA-owned `saa_zopedia_pages` and `saa_zopedia_change_proposals` storage.
- Added source ingestion, LLM page extraction, page search/read/neighborhood, YouTube transcript fetch, and reviewable change proposals.
- Added Zopedia agent tools: `zopedia.search_pages`, `zopedia.read_page`, `zopedia.neighborhood`, `zopedia.ingest_source`, `zopedia.ingest_youtube`, `zopedia.propose_change`, and `zopedia.list_proposals`.
- Added AQL evidence-pack support for `zopedia_pages` and proposal refs.
- Added a Zopedia Memory panel on the Zopedia page with initial page search, source ingest, and proposal creation before chat input.
- Kept deterministic evidence seeding conservative: obvious datasets/ticker context come before Zopedia memory so Zopedia does not make direct queries slower or worse.
- Added full regression tests for wiki-page normalization/search, ingest, graph neighborhood, YouTube parsing, agent tool dispatch, and evidence-pack promotion.

Pass results:

- Focused Zopedia/AQL/SAA suite: `41 passed`.
- Full app suite in project venv: `464 passed, 6 warnings`.
- Real YouTube URL check for the three supplied URLs reached YouTube but returned `ipblocked` through `youtube-transcript-api`; timedtext fallback also returned empty captions. Product behavior is a clear unavailable state plus pasted-transcript ingestion.
- Dev deployment completed on 2026-05-16:
  - API: `sn-api-dev--0000012`, health check passed at `/health`.
  - UI: `sn-streamlit-ui-dev--0000309`, root returned HTTP 200.
  - Pipeline jobs: image built/pushed and scheduled dev jobs updated.

Current go/no-go:

- **Go for dev testing of native Zopedia v1.**
- **No-go for claiming reliable server-side YouTube transcript retrieval until we solve IP/proxy/cookie/provider constraints.**

## 2026-05-16 Zopedia-Native LLM Boundary

The app now has one model-loading boundary for product code:

```text
services.zopedia_runtime.load_zopedia_llm_client(surface=...)
```

Rules:

- Feature code, pipeline jobs, tools, and UI modules do not call `services.llm.load_llm_client()` directly.
- Every model-using surface must name a `surface`, for example `zopedia.agent`, `attention.home_build`, `trading_agent`, `entity_taxonomy`, or `knowledge_graph.expansion`.
- Zopedia/AQL internals may call `llm_client.generate_json(...)` only from reviewed modules. The source-scan test `tests/test_zopedia_native_llm_boundary.py` fails if a new unreviewed model surface appears.
- Cheap deterministic routing should stay deterministic. The old omnibar confidence-band LLM call was removed instead of being routed through the model gateway.

Current meaning of "Zopedia native":

```text
UI / job / API feature
  -> load_zopedia_llm_client(surface=...)
  -> Zopedia agent, AQL, SAA memory, KG proposal, or reviewed formatter/extractor
  -> evidence pack / trace / proposal where applicable
```

This does not mean every internal extraction step is a chat-style agent call. It means no feature owns a private model client and every model caller is either a reviewed Zopedia/AQL surface or fails the audit test.

## 2026-05-17 Deep Product Eval

Added and ran a repeatable headless product eval:

```bash
streamlit_alpaca_app/.venv/bin/python streamlit_alpaca_app/scripts/zopedia_product_eval.py --check-dev-urls
```

Latest report:

- `documents/architecture/new_features/zopedia/ZOPEDIA_DEEP_PRODUCT_EVAL_REPORT_2026-05-17.md`
- `documents/architecture/new_features/zopedia/eval_runs/ZOPEDIA_PRODUCT_EVAL_REPORT_zopedia-eval-20260517-061751.md`

Result: **10 pass, 1 fail, decision hold**.

Passed:

- dev LLM runtime, database, API, and UI health
- text/source ingest into Zopedia pages
- long-query wiki search recall after fallback fix
- exact page read
- graph neighborhood determinism
- reviewable add/delete/update proposals
- agent tool search/read/neighborhood
- memory-backed Zopedia answer with visible page support

Failed:

- real YouTube transcript retrieval for all three supplied URLs returned `ipblocked`

Changes made from eval findings:

- `search_zopedia_pages` now falls back to a bounded recent-page candidate set when strict SQL/full-text search returns no rows, then applies the in-memory scorer.
- deterministic Zopedia seeding now reads the top page after `zopedia.search_pages` when budget allows.
- the product eval now fails if the agent claims memory is missing or fails to read/cite a Zopedia page.
- the final-answer prompt now asks for Zopedia page title/page_id support when memory is used.

Current go/no-go:

- **Go for dev testing the native Zopedia backend path.**
- **No-go for broad Zopedia parity or reliable YouTube claims until transcript retrieval has a reliable provider path or first-class pasted transcript UX.**

## 2026-05-17 Hardcoding Correction

The evidence-routing standard is now:

- The code must not use market-domain keyword lists to decide that a query is a company, macro, current-news, or broad-market query.
- The LLM planner owns tool choice from the live tool catalog.
- Deterministic code may enforce generic safety and reliability behavior only: tool timeouts, duplicate-call blocking, evidence-only synthesis, AQL evidence-pack capture, and bounded retained-evidence prefetch.
- Product quality is enforced through evals that inspect final behavior: company questions must use fundamentals/news/history, macro questions must use local macro datasets first, stale memory must produce reviewable Zopedia proposals, and false premises must be checked against evidence.

This replaces the short-lived keyword bootstrap approach. `Agent bootstrap tool calls` now defaults to `0`; `_bootstrap_tool_plan` remains only as a compatibility hook and returns no calls.
