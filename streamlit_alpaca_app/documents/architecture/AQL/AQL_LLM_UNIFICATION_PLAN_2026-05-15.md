# AQL LLM Unification Plan

Date: 2026-05-15

Last updated: 2026-07-01

## Decision

Fix the LLM architecture before the Zopedia integration.

Zopedia should not become another smart layer beside existing LLM paths. The product needs one shared research-grade contract:

```text
source data + retained memory + live evidence + KG/Zopedia context
  -> AQL evidence pack
  -> AQL synthesis / critique / proposals
  -> surface-specific formatting
```

Direct LLM calls are still allowed for narrow utility tasks, but not for research-grade claims or user-facing market narratives.

## 2026-07-01 Correction

The first unification pass was not enough.

It created a shared AQL/Zopedia engine boundary and moved many surfaces to
`load_aql_zopedia_llm_client(surface=...)`, but that only labels the model
client. It does not enforce budgets, evidence-pack ownership, provider/model
policy, request telemetry, cost attribution, or direct-call bans.

The next release gate is v0.6:
[v0.6 AQL/Zopedia Gateway Roadmap](../../plans/V0_6_AQL_ZOPEDIA_GATEWAY_ROADMAP_2026-07-01.md).

From this point forward, a surface is not considered centralized merely because
it uses the shared loader. It is centralized only when research-grade and
formatter-over-AQL model calls enter an enforceable gateway and leave observable
surface/purpose/model records.

## Why Not Every LLM Layer Uses AQL Today

This is historical and architectural drift, not a deliberate final design.

AQL grew around attention, evidence collection, synthesis, and critique. Other features were built before or beside that path and kept local LLM calls because they needed one small thing at the time:

- route an omnibar query
- classify a source
- extract tags
- summarize EDGAR text
- write a feed brief
- format a page summary
- expand a graph draft
- create trading-watchlist copy

Some of those are fine as utility calls. The problem is when they produce research conclusions or market narratives without the same evidence, citation, critique, and memory contract.

## Rule

Use this split:

| LLM Use | AQL Required? | Reason |
| --- | --- | --- |
| Metadata extraction | No, if narrow | Examples: tags, source type, entity labels. Output is not final narrative. |
| Routing/planning | Usually no, but should feed AQL | Examples: query intent, UI confidence bands. |
| Evidence extraction from a bounded source | Not always | Example: extracting structured facts from a filing can be a source-processing step. |
| User-facing market narrative | Yes | Needs shared evidence, citations, critique, and gaps. |
| Research synthesis | Yes | Needs retained evidence, live evidence, and source trace. |
| Trading/watchlist reasoning | Yes | Must be grounded and critique-aware. |
| KG/wiki mutation proposal | Yes or AQL-compatible proposal contract | Needs evidence refs, rationale, confidence, review state. |

## Current Inventory

### Already AQL Or AQL-Adjacent

| Area | Current state | Action |
| --- | --- | --- |
| `services/aql/*` | AQL core. | Keep as owner. |
| Homepage attention summary | Uses AQL summary path and critique in `attention_home_build.py`. | Extend evidence pack with Zopedia memory later. |
| Page agentic summaries | Calls `run_aql_zopedia_agent` first, then formats result. | Keep, but replace generic agent result JSON with formal AQL evidence pack. |
| Trading Agent | Calls `run_aql_zopedia_agent` first, then formats candidates. | Keep, but require formal AQL evidence pack and citations before formatting. |
| Zopedia / Omnibar agent | Tool loop, retained search, live search, hypothesis tool. | Keep as the interactive AQL runtime, not a separate research brain. |

Correction on 2026-07-01: the rows above are AQL-adjacent, not complete
governance. Trading Agent, Attention/Home, Zopedia memory, KG expansion, and
page summaries still need gateway migration for formatter/review/utility calls
that currently invoke `generate_json` directly.

### Direct LLM Calls That Can Stay Utility-Scoped

| Area | File | Why allowed |
| --- | --- | --- |
| Entity taxonomy classification | `services/entity_taxonomy.py` | Metadata enrichment, not final narrative. |
| Entity extraction | `services/entity_extraction.py` | Candidate extraction for linking/proposals. |
| Evidence tag extraction | `services/aql/evidence_index.py` | Metadata tags for retrieval. |
| Query intent / routing | `services/omnibar.py`, `services/omnibar_research.py` | Planning aid; must preserve original query into AQL. |
| Hypothesis scoring tool | `services/common/hypothesis.py`, `services/agent_tools.py` | Tool used inside AQL/agent flow. |
| Source/search query helpers | `services/attention_live_research.py` | Utility only if output is a query, not final conclusion. |

### Direct LLM Calls To Migrate Behind AQL

| Area | File | Problem | Target |
| --- | --- | --- | --- |
| Attention feed brief | `services/attention_feed_brief.py` | Deleted on 2026-05-16; it wrote user-facing feed text from mixed context directly. | Keep deleted. Rebuild only as a formatter over AQL evidence pack if needed. |
| Attention EDGAR narrative | `services/attention_context_llm.py` | `build_attention_context_narratives` writes feed narrative directly. | EDGAR evidence extraction can stay; narrative should flow through AQL summary/writer. |
| Live attention research bundle synthesis | `services/attention_live_research.py` | `_synthesize_with_llm` writes why-now / what-else-moved text directly. | AQL symbol evidence pack + writer. |
| Legacy homepage summary helper | `services/aql/summarizer.py` `_llm_home_summary` | Inside AQL but older direct path. | Keep only as AQL writer fallback with trace/critique metadata. |
| Knowledge graph expansion | `services/knowledge_graph.py` | Direct LLM proposes nodes/edges using web snippets. | Use shared entity extraction + AQL proposal contract; retain review flow. |
| Company news theme extraction | `services/company.py` | Usually metadata, but can shape narrative. | Keep as metadata only; do not use as final explanation without AQL evidence. |
| Data access live attention artifact fallbacks | `data_access/layer.py` | Render-time code can trigger LLM-backed artifact building. | Prefer materialized outputs; if live fallback exists, call public AQL runtime with timeouts. |

## Target AQL Contract

Create one structured object that every research-grade surface can consume.

Proposed type:

```python
class AQLEvidencePack:
    query: str
    surface: str
    entities: list[ResolvedEntity]
    retained_chunks: list[EvidenceChunkRef]
    source_documents: list[SourceDocumentRef]
    zopedia_pages: list[WikiPageRef]
    kg_paths: list[KGPathRef]
    live_evidence: list[EvidenceChunkRef]
    hypotheses: list[HypothesisCheck]
    citations: list[CitationRef]
    data_gaps: list[str]
    critique_findings: list[CritiqueFinding]
    proposals: list[ReviewableProposal]
    trace: list[AQLTraceEvent]
```

Surfaces should pass this to their own writer/formatter instead of rebuilding evidence.

## Migration Order

### Phase -1A: Inventory And Guardrails

- Add this inventory to architecture docs.
- Add a lightweight code comment or lint convention: direct LLM calls must state `utility`, `aql_core`, or `formatter_over_aql`.
- Do not block existing functionality yet.

Status on 2026-05-15:

- Inventory documented.
- Zopedia plan updated to make this a prerequisite.
- Learning logged in `documents/learnings.md`.

### Phase -1B: Formal Evidence Pack

- Add AQL evidence-pack models and serializer.
- Make `run_aql_zopedia_agent` or a sibling service return the pack in addition to answer markdown.
- Include source IDs, citations, tool trace, and data gaps.

Status on 2026-05-15:

- Added `services/aql/evidence_pack.py`.
- `run_aql_zopedia_agent` now returns:
  - `aql_evidence_pack_id`
  - `aql_evidence_pack`
- Tool summaries now preserve `evidence_refs` when tool payloads expose stable IDs.
- `research.search_evidence` now carries SAA `chunk_record_id`, `canonical_document_id`, `document_id`, and `chunk_id` into its result rows.

### Phase -1C: Migrate Page Summaries And Trading Agent

- They already call the agent first.
- Replace ad hoc `aql_agent` JSON with the formal evidence pack.
- Keep the final LLM call as a formatter only.
- Add tests that formatter prompts cannot introduce unsupported claims.

Status on 2026-05-15:

- Page summaries now include the agent evidence pack inside `aql_agent`.
- Trading Agent now includes the agent evidence pack inside `aql_agent`.
- Further work still needed: persist evidence pack as its own stable materialized artifact instead of only embedding it in each surface's result JSON.

### Phase -1D: Migrate Attention Feed Text

- Do not reintroduce `attention_feed_brief.py` as a direct LLM narrative helper.
- EDGAR/source extraction can remain a source-processing step.
- Final feed text should come from AQL writer/formatter with citations/gaps.

Status on 2026-05-16:

- Deleted the legacy `services/attention_feed_brief.py` runtime helper instead of preserving another direct LLM narrative path.
- Removed the Streamlit presentation aliases that exposed `_load_attention_feed_brief_cached`, `_load_attention_brief_payloads`, `_build_attention_brief_input`, and `_build_trading_agent_suggestions_cached`.
- `presentation/` no longer imports `load_llm_client`; render paths should not own LLM research work.
- Broad Economy, Market Explorer, and page-summary empty states now route to a Zopedia analysis action instead of exposing materialized/scheduled/precomputed job language.
- Page summaries now fail closed when AQL or the formatter cannot produce grounded output. They no longer synthesize deterministic fallback prose from row values.
- Visible Chat + Search labels were renamed to Zopedia. Internal `omnibar_*` names remain only as compatibility/API/module names.

### Phase -1E: Migrate Live Research Bundle Text

- Replace `_synthesize_with_llm` with an AQL symbol evidence pack + writer.
- Keep deterministic fallbacks for unavailable LLM/AQL.

### Phase -1F: KG/Zopedia Proposal Contract

- Move graph/wiki proposal generation to an AQL-compatible proposal schema.
- KG service still owns storage and commits.
- AQL proposes; review flow applies.

### Phase 0: v0.6 Gateway Enforcement

This supersedes the weaker "reviewed direct call" standard.

- Add an AQL/Zopedia gateway around model calls.
- Classify every model call as `research_grade`, `formatter_over_aql`,
  `utility`, `schema_repair`, or `admin_probe`.
- Persist model-call events with surface, purpose, provider, model, status,
  timing, sanitized errors, usage when available, and durable artifact links.
- Require formatter-over-AQL calls to link to an evidence pack or explicit AQL
  unavailable state.
- Move Trading Agent final synthesis and research reviews through the gateway.
- Move Attention/Home group synthesis, public review, ticker enrichment, and
  summary calls through the gateway.
- Move Zopedia chat synthesis, memory proposal, maintenance learning, KG
  expansion, company memory, and news-business resolution through the gateway.
- Replace direct-call source-scan allowlists with temporary migration allowlists
  that have owner, reason, target, and expiry milestone.

## Acceptance Tests

### Architecture Tests

- Each research-grade summary row includes an AQL run ID or evidence-pack ID.
- Page summary and Trading Agent rows persist the evidence-pack ID, not only final markdown.
- Direct LLM narrative functions are removed from product code or routed through
  the AQL/Zopedia gateway as formatter-over-AQL with evidence-pack linkage.
- Every migrated model call writes a gateway telemetry row.
- Source scans fail on new product `generate_json` call sites outside gateway
  internals, provider adapters, tests/fakes, or a temporary migration allowlist.

### Behavioral Tests

- The same ticker/event should produce consistent facts across:
  - Zopedia chat
  - attention feed
  - stock page summary
  - Trading Agent
- Surface wording can differ.
- Citations, source IDs, and data gaps should match.

### Regression Tests

- If AQL is unavailable, interactive/page summaries fail closed with `data_gaps` and a Zopedia action instead of fabricated prose.
- Scheduled Trading Agent horizons produce explicit run states for every
  required horizon. v0.6 should not invent research candidates without
  gateway-governed evidence; provider/budget/evidence failures become
  unavailable or insufficient-evidence states with telemetry.
- If formatter LLM fails, surfaces still show the AQL evidence state.
- No render path starts an unbounded LLM/AQL call.
- If provider quota/auth/model errors occur, Admin/System Health can identify
  the failing surface and purpose without reading raw job logs.

## Zopedia Dependency

Zopedia starts after this foundation.

Once AQL is the shared contract, Zopedia memory becomes one more evidence source inside AQL:

```text
SAA retained chunks + Zopedia wiki pages + KG paths + live evidence
  -> AQL evidence pack
  -> all LLM surfaces
```

That is what makes the integration improve the whole product instead of only the chat page.

## Verification Log

2026-05-15 initial Phase -1 slice:

```bash
PYTHONPATH=streamlit_alpaca_app streamlit_alpaca_app/.venv/bin/pytest -q \
  streamlit_alpaca_app/tests/test_omnibar_agent.py \
  streamlit_alpaca_app/tests/test_omnibar_research.py \
  streamlit_alpaca_app/tests/test_saa_storage.py \
  streamlit_alpaca_app/tests/test_knowledge_graph.py
# 36 passed in 14.14s

PYTHONPATH=streamlit_alpaca_app streamlit_alpaca_app/.venv/bin/pytest -q \
  streamlit_alpaca_app/tests/test_page_agentic_summary.py
# 8 passed in 0.46s

PYTHONPATH=streamlit_alpaca_app streamlit_alpaca_app/.venv/bin/pytest -q \
  streamlit_alpaca_app/tests/test_trading_agent.py
# 11 passed in 0.47s

PYTHONPATH=streamlit_alpaca_app streamlit_alpaca_app/.venv/bin/pytest -q \
  streamlit_alpaca_app/tests/test_pipeline_jobs.py -k "page_agentic_summary or trading_agent"
# 4 passed, 34 deselected in 0.81s

PYTHONPATH=streamlit_alpaca_app streamlit_alpaca_app/.venv/bin/pytest -q \
  streamlit_alpaca_app/tests/test_module_boundaries.py \
  streamlit_alpaca_app/tests/test_agent_tools.py
# 3 passed in 0.58s
```

2026-05-15 dev deployment:

```bash
bash scripts/deploy_pipeline_azure.sh --target dev
# Deployment complete

bash scripts/deploy_ui_azure.sh --target dev
# Deployment complete
# Target app: sn-streamlit-ui-dev
# Ready revision: sn-streamlit-ui-dev--0000307
# Root HTTP status: 200
```
