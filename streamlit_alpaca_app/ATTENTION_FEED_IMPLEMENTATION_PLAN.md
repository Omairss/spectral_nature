# Attention Feed Implementation Plan

## Objective

Deliver a day-only attention system that is:

- broad enough to catch the obvious tape
- intelligent enough to explain why today
- accurate enough to avoid false narratives
- fast enough to refresh hourly
- reliable enough to publish even when some research sources fail

## Recommended Architecture

### Control plane

- orchestration: `LangGraph`
- model runtime and tool use: `OpenAI Responses API`
- tracing and evaluation: optional `LangSmith`

### Retrieval plane

- search providers: `SerpApi` and/or `Tavily`
- extraction: `Firecrawl`
- browser fallback: `Playwright`
- official connectors: direct `SEC EDGAR`, issuer IR/newsroom pages, `FRED`, and other primary endpoints

### Storage and execution

- Postgres for structured candidate, event, bundle, and evidence tables
- object storage for raw page snapshots and extracted text
- worker queue or job runner for hourly and on-demand research
- Redis or similar cache for short-lived drilldown and search results

## Phase 0: Instrumentation And Evaluation Harness

Deliverables:

- fixture set for known market days and bad-current-output cases
- eval cases for oil / Iran, `FSLY`, `PYPL`, `QXO`, `MT`, `APGE`, and macro tape days
- evidence audit format with retained source ids

Acceptance gate:

- every generated homepage card and drilldown can be evaluated against a stored gold-standard expectation

## Phase 1: Deterministic Tape Foundation

Deliverables:

- strict `resolve_attention_home_1d()`
- top liquid-universe discovery
- curated macro-anchor universe
- daily inclusion guarantees
- event-first homepage rails

Acceptance gate:

- homepage is hard-locked to `1d`
- obvious liquid movers and macro anchors reliably appear

## Phase 2: Structured Entity And Peer Intelligence

Deliverables:

- stronger `attention_entity_master`
- sector / industry / subindustry coverage
- peer-group mapping
- macro-role overrides and beneficiary / loser maps

Acceptance gate:

- major names no longer render as `Unknown | Unknown` when structured metadata exists
- peer and spillover checks become available for single-name drilldowns

## Phase 3: Evidence Model And Judgment Layer

Deliverables:

- structured evidence table with freshness, relevance, and causal scores
- cause-status model: `supported / continuation / unresolved / conflicting`
- same-day evidence counting separate from background evidence counting
- evidence-quality model that accounts for freshness, not just authority

Acceptance gate:

- old filings and stale articles no longer produce `High` evidence quality for today’s move
- unsupported explanations downgrade to `continuation` or `unresolved`

## Phase 4: Direct Official-Source Ingestion

Deliverables:

- SEC EDGAR fetcher
- filing body retrieval
- parsed filing sections and material fact extraction
- issuer press-release and IR fetchers
- official-source cache

Acceptance gate:

- filing content, not just filing labels, becomes available to drilldowns
- the system can distinguish fresh filing catalysts from background context

## Phase 5: Agentic Search And Retrieval Graph

Deliverables:

- trigger router
- official-source agent
- market-news agent
- peer-and-spillover agent
- evidence judge
- writer

Recommended workflow per candidate:

1. classify trigger
2. generate search queries
3. search for fresh coverage
4. fetch and extract pages
5. normalize and dedupe evidence
6. score freshness / relevance / causality
7. check peers and spillover
8. assign cause status
9. write final drilldown text from retained evidence only

Acceptance gate:

- research bundles improve explanation quality without breaking homepage latency
- agent failure in one branch does not block publication

## Phase 6: Homepage And Drilldown UX Rewrite

Deliverables:

- final homepage rails:
  - `Top Market Events Today`
  - `Must-Read Movers Today`
  - `Unresolved Large Moves`
- final drilldown sections:
  - `What Changed Today`
  - `Why Today`
  - `What Else Moved`
  - `Evidence`
  - `Background Context`
- suppression of irrelevant roundup content
- no residual / z-score prose in explanatory copy

Acceptance gate:

- homepage reads like the tape
- drilldowns read like analyst notes, not debug output

## Phase 7: Operations, Caching, And Reliability

Deliverables:

- hourly batch refresh for shortlisted triggers
- on-demand drilldown refresh path
- evidence cache and snapshot retention
- fallback path when search or scraping fails
- alerting for empty or degraded runs

Acceptance gate:

- homepage still publishes a useful daily tape when one provider fails
- drilldown latency remains acceptable because official and extracted evidence is cached

## Proposed Tables And Artifacts

Structured tables:

- `attention_event_candidates_1d`
- `attention_event_feed_1d`
- `attention_event_impacts_1d`
- `attention_must_read_movers_1d`
- `attention_unresolved_large_moves_1d`
- `attention_research_bundle`
- `attention_evidence`
- `attention_entity_master`
- `attention_peer_map`

Raw artifacts:

- page snapshots
- filing text snapshots
- extracted sections
- search result logs
- per-run evaluation logs

## Service Layout

Recommended new modules:

- `services/attention_trigger_router.py`
- `services/attention_entity_master.py`
- `services/attention_peer_map.py`
- `services/attention_evidence_store.py`
- `services/attention_search.py`
- `services/attention_extract.py`
- `services/attention_sec.py`
- `services/attention_research_graph.py`
- `services/attention_judge.py`
- `services/attention_writer.py`

Existing modules to evolve:

- `services/attention_home_1d.py`
- `services/attention_feed_brief.py`
- `services/homepage_v2.py`
- `data_access/layer.py`
- `data_access/query_service.py`
- `app.py`

## Evaluation And Regression Suite

Required tests:

- oil-down / airlines-up / bonds-up day becomes a top event
- `PYPL`, `QXO`, `MT`, and similar liquid movers appear in either `Top Market Events Today` or `Must-Read Movers Today`
- homepage never mixes longer horizons into ranking
- APGE/APG-style names are not mislabeled as macro sectors
- symbol drilldowns do not show unrelated roundup articles
- symbol drilldowns separate same-day catalyst from background context
- SEC filings are parsed and classified correctly
- stale filings do not create false `supported` labels
- unresolved large moves remain visible and honestly unresolved
- evidence quality reflects freshness and causal clarity

## Monitoring

Track at minimum:

- candidate count per run
- top-event count per run
- must-read count per run
- unresolved count per run
- share of cards with same-day evidence
- share of cards with official or wire evidence
- average evidence freshness by card
- homepage render latency
- drilldown bundle load latency
- search-provider failure rate
- filing-parse success rate
- percentage of cards downgraded to unresolved or continuation

## Operating Principle

When the system is uncertain, it should still publish the move and lower the confidence.

It must never publish a sharper causal narrative than the evidence can support.
