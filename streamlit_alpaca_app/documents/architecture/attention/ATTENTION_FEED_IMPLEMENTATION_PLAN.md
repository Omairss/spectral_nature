# Attention Feed Implementation Plan

## Status

This is the canonical plan for the homepage attention rebuild.

The main simplification is structural:

- keep upstream domain jobs separate
- add one dedicated `attention-home-build` job
- make each attention layer publish a typed dataset
- keep the Streamlit homepage read-only against those datasets

Narrative generation hardening status as of 2026-03-31:

- `_write_event_bundle` now uses the mechanism-first production prompt validated in the prompt lab notebook
- the event writer payload now carries cause status, evidence quality, freshness quality, cluster context, and ranked claim metadata instead of a thin stat bundle
- `attention-home-build` deploys now inherit existing LLM job configuration when the local shell does not export `LLM_*`, which prevents silent regression to heuristic-only narrative mode
- search-provider quota and auth failures remain observable in diagnostics but are excluded from narrative evidence, chunking, and fallback claims
- low-signal analyst roundup snippets such as ratings-page boilerplate are filtered before chunking and claim extraction so they do not become event explanations
- the graph explorer now renders the global backbone with a dedicated isolate band and explicit graph-coverage stats, so single-name candidates remain visible without turning the main network into noise
- the graph edge builder now preserves upstream `peer_group_id`, treats explicit taxonomy peers as first-class edges, and enriches claim entities with structured taxonomy and role context so real peers connect without relying on sparse tag overlap
- the graph explorer notebook now recomputes edges from the current candidate and claim frames by default and auto-selects a live focus symbol, so notebook inspection tracks graph-model changes instead of stale materialized edge tables
- the network plotter now packs many connected components more horizontally, which cuts the tall-column whitespace that was making the global graph feel sparse even after edge coverage improved
- the plotted network now adds muted bridge-concept nodes derived from live taxonomy and tag text, so disconnected security clusters can be connected through real intermediate concepts instead of fake direct edges
- the graph edge builder can now use 180-day historical return correlation from `price_history` as a secondary edge signal when taxonomy, tags, and claims are too sparse, and the explorer notebook loads that dataset explicitly so correlation-backed edges are visible during inspection
- the explorer now prefers real topology-path expansion over concept bridges: it builds a broader taxonomy/tag/event supergraph, finds shortest paths between disconnected plotted components, and injects those actual intermediate nodes back into the candidate plot as faded `Path Nodes`
- the explorer now renders only the main global graph by default, hides isolate-only rows from that view, and uses slightly translucent candidate markers so dense clusters remain readable when nodes overlap
- the network plotter now picks labels with local crowding in mind and varies label anchors away from nearby nodes, which cuts the dense path-node pileups that made the main graph hard to scan
- the network plotter now adds muted per-connected-component captions (`CC1`, `CC2`, ...) based on peer-group/industry mix and shows compact company names under labeled tickers, while automatically increasing translucency for large sector clusters to reduce overlap noise
- attention candidate rows now carry `security_name` from taxonomy/listing metadata into `attention_candidates_1d`, so graph labels and hover detail can show company names at source instead of relying on plot-only fallbacks
- the homepage path now stores a compact banner-ready graph figure inside `attention_home_1d`, so Streamlit renders the precomputed relationship graph under the hero without recomputing topology or carrying notebook-only legends and titles into production

## Why the current shape feels messy

Today the system has most of the raw ingredients, but too much is fused together:

- `pipeline/jobs/main.py::_materialize_attention_outputs()` is doing orchestration that belongs in its own job
- `services/attention_agentic.py::build_bottom_up_attention_artifacts()` mixes candidate selection, search planning, retrieval, chunking, claim extraction, clustering, writing, and UI payload assembly
- `services/attention_market_events.py` still contains hardcoded theme logic that should be data-driven
- `services/attention_home_1d.py` and the materialized payloads carry both domain logic and final homepage view logic

That coupling makes the feed harder to reason about, harder to test, and harder to evolve.

## Design goals

1. Deterministic first, agentic second.
2. High recall early, precision late.
3. Fix things at source instead of patching homepage behavior.
4. Avoid hardcoded theme buckets, symbol lists, or text rules where structured data can replace them.
5. Make graph, narratives, and drilldowns share the same relationship and evidence backbone.
6. Keep UI rendering snapshot-first and read-only.

## Recommended job topology

Keep the existing source jobs:

- `equities-intraday-preload`
- `news-ingest-and-features`
- `commodities-regime`
- `macro-fred-daily`
- `entity-taxonomy-refresh`
- fundamentals refresh inside the existing equities path

Add two attention-specific jobs:

1. `attention-home-build`
   - periodic
   - consumes the latest materialized source datasets
   - produces all attention-layer datasets and homepage views

2. `attention-home-validate`
   - on-demand or hourly for top narrative candidates
   - does deeper agentic validation and refreshes bundle-quality fields

This removes attention-home orchestration from `run_news()` and makes failures easier to isolate.

## Layered architecture

### Layer 0: Source snapshots

Purpose:

- provide clean, versioned raw inputs

Inputs:

- `daily_movers`
- `price_history`
- `technical_signals_latest`
- `quarterly_fundamentals`
- `news_articles`
- `edgar_filings`
- `edgar_evidence`
- `peer_group_membership`
- `correlation_phase_shift_summary`
- `commodity_regime_summary`
- `yield_curve_facts_1d`
- `fred_summary`
- `entity_taxonomy_labels`

Rule:

- no homepage logic here

### Layer 1: Anomaly layer

Purpose:

- high-recall detection of what changed today

Responsibilities:

- compute stock ticker anomaly candidates
- keep thresholds loose enough to avoid missing important market activity
- score surprise versus baseline, peer basket, and benchmark
- materialize both equities and commodity anomalies using the same contract

Use existing code:

- `compute/anomalies.py`
- `attention_candidates`
- `anomaly_events`
- `attention_feed`
- `commodity_attention_candidates`
- `commodity_attention_feed`

Recommended output contract:

- `attention_anomaly_candidates_1d`

Required fields:

- `candidate_id`
- `symbol`
- `asset_class`
- `change_pct`
- `expected_move_pct`
- `surprise_pct`
- `surprise_z`
- `liquidity_score`
- `candidate_score`
- `is_macro_anchor`
- `is_commodity_anchor`
- `attention_reason_codes`

Important rule:

- this layer answers `what moved`, not `why`

### Layer 2: Stock enrichment layer

Purpose:

- enrich candidates with context before any narrative generation

Responsibilities:

- technical snapshot
- fundamentals snapshot
- current news snapshot
- optional precision-oriented LLM reasoning over structured candidate context

Inputs:

- anomaly candidates
- technicals
- fundamentals
- news
- taxonomy

Recommended output contract:

- `attention_stock_enrichment_1d`

Required fields:

- `candidate_id`
- `symbol`
- `sector`
- `industry`
- `peer_group_id`
- `technical_state`
- `technical_flags`
- `fundamental_state`
- `fundamental_flags`
- `news_headlines`
- `news_freshness_score`
- `llm_precision_label`
- `llm_precision_notes`

Important rule:

- this layer may refine precision, but it must not suppress the anomaly record itself

### Layer 3: Stock relationship layer

Purpose:

- build the clean relationship graph that powers narratives and the homepage graph

Responsibilities:

- connect symbols through existing peer mechanisms
- connect symbols through rolling and regime-aware correlations
- keep edge types explicit instead of hiding logic inside prose

Inputs:

- anomaly candidates
- enrichment rows
- `peer_group_membership`
- `correlation_phase_shift_summary`
- commodity peer mappings
- taxonomy

Recommended output contracts:

- `attention_relationship_nodes_1d`
- `attention_relationship_edges_1d`

Node types:

- `stock`
- `commodity`
- `macro`
- `narrative`

Edge types:

- `taxonomy_peer`
- `current_mechanism`
- `historical_correlation`
- `commodity_transmission`
- `macro_transmission`
- `evidence_link`

Required edge fields:

- `left_id`
- `right_id`
- `edge_type`
- `edge_weight`
- `confidence`
- `reason_code`
- `reason_text`

Important rule:

- move hardcoded theme buckets out of `services/attention_market_events.py`
- replace them with data-driven mappings from taxonomy plus relationship rules

### Layer 4: Commodities layer

Purpose:

- make commodities a first-class driver instead of a sidecar add-on

Responsibilities:

- detect commodity anomalies
- translate commodity moves into impacted equities and sectors
- expose transmission paths like oil -> airlines, copper -> miners, yields -> duration-sensitive equities

Recommended output contract:

- `attention_commodity_context_1d`

Required fields:

- `driver_symbol`
- `driver_type`
- `move_direction`
- `move_strength`
- `linked_symbols`
- `transmission_reason`
- `evidence_strength`

### Layer 5: Context layer

Purpose:

- add macro background without mixing it directly into stock detection

Responsibilities:

- yield data
- FRED data
- rate-of-change anomaly detection for FRED series
- macro-state summary for narrative grouping

Use existing inputs:

- `yield_curve_facts_1d`
- `yield_curve_summary`
- `fred_summary`
- `fred_observations`

Recommended output contracts:

- `attention_macro_context_1d`
- `attention_fred_anomalies_1d`

Required fields:

- `context_id`
- `context_type`
- `series_id`
- `direction`
- `roc_score`
- `surprise_score`
- `linked_assets`
- `summary_text`

### Layer 6: Narrative candidate layer

Purpose:

- turn connected moves into candidate narratives before any expensive agentic work

Responsibilities:

- group anomalies through relationship edges
- identify candidate narrative anchors
- assign draft titles and grouping hypotheses
- keep unresolved large single-name moves visible as standalone candidates

Inputs:

- anomaly candidates
- stock enrichment
- relationship graph
- commodity context
- macro context

Recommended output contract:

- `attention_narrative_candidates_1d`

Required fields:

- `narrative_candidate_id`
- `anchor_id`
- `anchor_type`
- `member_ids`
- `candidate_type`
- `draft_title`
- `draft_hypothesis`
- `supporting_reason_codes`
- `priority_score`

Important rule:

- this layer is deterministic
- it should be cheap enough to run every scheduled build

### Layer 7: Agentic narrative validation layer

Purpose:

- validate or reject narrative candidates through evidence-backed exploration

Responsibilities:

- identify narrative candidates worth deeper work
- retrieve evidence from web, EDGAR, FRED, and materialized internal datasets
- build claims from retrieved evidence
- judge whether the narrative is supported, continuation, conflicting, or unresolved

Recommended output contracts:

- `attention_research_plans`
- `attention_source_documents`
- `attention_evidence_chunks`
- `attention_claims`
- `attention_narrative_validation_1d`

Required validation fields:

- `narrative_candidate_id`
- `cause_status`
- `same_day_evidence_count`
- `background_evidence_count`
- `evidence_quality`
- `freshness_quality`
- `confidence_label`
- `retained_claim_ids`

Important rule:

- this layer uses agentic tooling
- the final narrative may only cite retained evidence and internal datasets

### Layer 8: Final narrative synthesis layer

Purpose:

- write the final narrative objects used by the homepage and drilldowns

Responsibilities:

- synthesize what changed
- synthesize why today
- synthesize what else moved
- attach technical and fundamental breakdown references

Recommended output contract:

- `attention_narratives_1d`

Required fields:

- `narrative_id`
- `narrative_title`
- `headline`
- `what_changed_text`
- `why_today_text`
- `what_else_moved_text`
- `technical_breakdown`
- `fundamental_breakdown`
- `cause_status`
- `confidence_label`
- `related_symbols`
- `driver_context_ids`
- `bundle_id`

Important rule:

- writing is the last step, not the source of truth

### Layer 9: Homepage view layer

Purpose:

- provide a clean Streamlit contract with no hidden business logic

Responsibilities:

- publish narrative cards
- publish graph nodes and edges
- publish drilldown bundles

Recommended output contracts:

- `attention_home_cards_1d`
- `attention_home_graph_nodes_1d`
- `attention_home_graph_edges_1d`
- `attention_home_drilldowns_1d`

The homepage should read only these datasets plus lightweight metadata.

## What the homepage should show

### Default homepage

- a clean relationship graph centered on narratives, not raw symbols
- a narrative rail sorted by priority and confidence
- a visible unresolved rail for big moves without enough evidence

### Graph design

Default nodes:

- narrative nodes
- top contributing stocks
- macro context nodes
- commodity driver nodes

Default edges:

- strongest typed relationships only

Interaction:

- click narrative -> expand member stocks, commodity drivers, macro context
- click stock -> show technical breakdown, fundamentals, evidence, and neighboring relationships

### Drilldown design

Every narrative or symbol drilldown should have:

- `What changed`
- `Why today`
- `What else moved`
- `Technical breakdown`
- `Fundamental breakdown`
- `Evidence`
- `Background context`

## Recommended module layout

Keep pure transformations in `compute/`. Keep external IO and retrieval in `services/`. Keep orchestration in `pipeline/jobs/`.

Recommended refactor target:

```text
compute/
  attention_anomaly.py
  attention_enrichment.py
  attention_relationships.py
  attention_context.py
  attention_narratives.py

services/
  attention_research.py
  attention_claims.py
  attention_writer.py
  attention_graph_views.py
  attention_materialized.py

pipeline/jobs/
  attention_home_build.py
  attention_home_validate.py
```

Minimal-change mapping from current code:

- keep `compute/anomalies.py` as the anomaly core
- split `services/attention_agentic.py` into planner, evidence, claims, clustering, and writer modules
- shrink `services/attention_home_1d.py` into a thin assembly/view contract
- keep `services/attention_materialized.py` focused on serialization only
- move `_materialize_attention_outputs()` out of `pipeline/jobs/main.py`
- make `app.py` a pure reader/renderer for attention home datasets

## Phased implementation plan

### Phase 1: Extract orchestration and contracts

Changes:

- create `pipeline/jobs/attention_home_build.py`
- move attention-home orchestration out of `run_news()`
- define canonical dataset contracts for every layer
- keep current homepage behavior working through a compatibility wrapper

Acceptance:

- the existing homepage still renders
- the attention build can run independently from the news job

### Phase 2: Separate deterministic layers

Changes:

- split anomaly, enrichment, relationship, commodity, and macro context into separate functions and datasets
- keep all outputs materialized individually
- reuse existing `attention_candidates`, `commodity_attention_feed`, correlation, yield, and FRED datasets where possible

Acceptance:

- each layer can be tested independently
- failures in enrichment or relationships do not wipe out anomaly output

### Phase 3: Replace hardcoded event logic

Changes:

- remove theme-specific symbol buckets from `services/attention_market_events.py`
- replace them with structured relationship rules and taxonomy-driven tags
- make event grouping graph-based instead of theme-constant-based

Acceptance:

- oil, rates, and macro narratives still work
- new themes can appear without code changes

### Phase 4: Build narrative candidate and validation layers

Changes:

- materialize `attention_narrative_candidates_1d`
- refactor agentic retrieval into explicit planner, retrieval, claim, and judge steps
- validate narratives using internal datasets plus external evidence

Acceptance:

- the system can say `supported`, `continuation`, `conflicting`, or `unresolved`
- retained evidence is inspectable

### Phase 5: Build homepage graph and drilldown views

Changes:

- materialize homepage graph nodes and edges
- materialize narrative cards and drilldown bundles
- wire Streamlit homepage to read only those datasets

Acceptance:

- homepage graph is readable and stable
- narrative drilldowns show technical and fundamental breakdowns cleanly

### Phase 6: Remove compatibility debt

Changes:

- retire the monolithic `attention_home_snapshots_1d` assembly path
- keep a thin compatibility layer only if older readers still need it
- reduce duplicate docs and make this file the only implementation plan

Acceptance:

- attention-home data flow is understandable from datasets alone

## Testing and evaluation

Add tests at three levels:

1. Layer unit tests
   - anomaly scoring
   - enrichment joins
   - relationship edge generation
   - narrative clustering

2. Golden-day regression tests
   - oil-down / airlines-up / yields-down day
   - rates shock day
   - large unexplained single-name mover
   - commodity spillover day

3. Homepage contract tests
   - graph nodes and edges are stable
   - narratives include technical and fundamental drilldown fields
   - unresolved moves stay visible instead of disappearing

## Immediate implementation recommendation

Do not start by rewriting the homepage.

Start by extracting the current attention orchestration into a dedicated job and then break the current monolith into these contracts in order:

1. anomaly candidates
2. stock enrichment
3. relationship edges
4. macro and commodity context
5. narrative candidates
6. agentic validation
7. homepage views

That sequence fixes complexity at the source and gives the UI a much cleaner surface to consume.
