# Attention Feed Redesign Plan

Implementation note: use `ATTENTION_FEED_IMPLEMENTATION_PLAN.md` as the canonical build plan. This file is retained as product/background context.

## Product Goal

Rebuild the attention feed as a strict `today / 1d` market-intelligence system.

The homepage and drilldowns must answer only three questions:

1. What changed today versus expectation?
2. Why did it change today?
3. What else moved because of it?

This system is not a generic anomaly browser. It is today's market activity with evidence-backed drilldowns.

## What Good Looks Like

If oil drops sharply on an Iran deal, the product should immediately show:

- oil down materially today
- why that happened today, with real source-backed context
- airlines, rates, bonds, energy equities, and related beneficiaries or losers

If a single name like `FSLY` or `PYPL` moves sharply, the drilldown should show:

- the move versus expectation
- whether there is a fresh same-day catalyst, a continuation of an older catalyst, or no clear explanation
- relevant peer or spillover moves, or an explicit statement that there was no clear spillover

## Why The Current System Is Still Below Bar

The current implementation improved ranking, but it still has structural gaps:

1. The deterministic layer finds moves, but the research layer is too shallow.
2. SEC filings are mostly referenced by labels and links, not by parsed content.
3. Evidence quality is inferred too loosely from source count and authority, not from freshness or causal relevance.
4. Single-name drilldowns can still use stale or generic text as if it explains today.
5. The system does not reliably answer `what else moved because of it` for single-name drilldowns.
6. Sector and macro intelligence are still too thin for broad, trustworthy interpretation.

## Design Principles

### 1. Deterministic first, agentic second

The system must first decide what matters today using deterministic market logic.

Only then should it dispatch agentic web research on a shortlisted set of events and movers.

### 2. Evidence beats prose

The system must never write a cleaner story than the retained evidence supports.

If the move is important but the cause is unclear, the correct output is `unresolved`, not a speculative narrative.

### 3. Freshness matters

An old 8-K, 10-K, or earnings release can be useful background context, but it is not a same-day catalyst unless evidence explicitly ties it to today’s move.

### 4. Structured metadata beats keyword guesses

Sectors, industries, and macro roles must come from a structured entity master and curated overrides.

Keywords may enrich explanation, but they may not assign sector or macro role.

### 5. Homepage Text Stays Terse; Research Can Be Deep

The homepage must be concise and high-signal.

The click-to-expand research bundle can carry the citations, evidence excerpts, peer checks, and source trail.

## Target System Architecture

The redesigned attention system has three layers.

### Layer 1: Deterministic Market-Activity Engine

This layer decides what changed and what deserves research.

Responsibilities:

- build the daily candidate universe
- compute `1d` moves versus expectation
- guarantee coverage of major movers and macro anchors
- cluster the strongest daily cross-asset events
- produce the homepage rails

Outputs:

- `attention_event_candidates_1d`
- `attention_event_feed_1d`
- `attention_event_impacts_1d`
- `attention_must_read_movers_1d`
- `attention_unresolved_large_moves_1d`

### Layer 2: Agentic research engine

This layer explains why the shortlisted moves happened today.

Responsibilities:

- search the web for same-day evidence
- query official sources directly when available
- parse SEC filing bodies, not just filing labels
- gather peer and spillover context
- classify the explanation as supported, continuation, unresolved, or conflicting

This layer should run hourly in batch and on-demand for clicked drilldowns.

### Layer 3: Controlled synthesis engine

This layer converts retained evidence into final user-facing text.

Responsibilities:

- produce `what changed / why today / what else moved`
- include confidence and evidence quality
- cite retained evidence rows
- refuse to invent unsupported cause text

## Canonical Data Products

### `attention_event_candidates_1d`

One row per materially important symbol or macro anchor today.

Required fields:

- `candidate_id`
- `symbol`
- `asset_class`
- `security_type`
- `sector`
- `industry`
- `change_pct`
- `expected_move_pct`
- `surprise_pct`
- `surprise_z`
- `dollar_volume`
- `candidate_score`
- `cause_status`
- `confidence_label`
- `what_changed_text`
- `why_now_text`
- `why_today_mode`
- `same_day_evidence_count`
- `background_evidence_count`
- `bundle_id`

### `attention_event_feed_1d`

One row per clustered market event today.

Required fields:

- `market_event_id`
- `event_type`
- `event_title`
- `event_score`
- `anchor_symbol`
- `anchor_direction`
- `what_happened_text`
- `why_happened_text`
- `affected_assets_summary_text`
- `driver_symbols`
- `beneficiary_symbols`
- `loser_symbols`
- `supporting_symbols`
- `cause_status`
- `bundle_id`

### `attention_event_impacts_1d`

One row per `event x affected asset`.

Required fields:

- `market_event_id`
- `symbol`
- `impact_role`
- `direction`
- `change_pct`
- `sector`
- `industry`
- `bundle_id`

### `attention_must_read_movers_1d`

One row per important daily mover not absorbed into a top market event.

Required fields:

- `symbol`
- `headline`
- `what_changed_text`
- `why_now_text`
- `what_else_moved_text`
- `cause_status`
- `confidence_label`
- `candidate_score`
- `same_day_evidence_count`
- `bundle_id`

### `attention_research_bundle`

Lazy-loaded drilldown evidence pack for an event or symbol.

Required fields:

- `bundle_id`
- `bundle_type`
- `headline` or `event_title`
- `what_changed_text` or `what_happened_text`
- `why_now_text` or `why_happened_text`
- `what_else_moved_text` or `affected_assets_summary_text`
- `cause_status`
- `evidence_quality`
- `freshness_quality`
- `evidence`
- `background_context`
- `related_symbols`
- `peer_moves`
- `source_summary`

### `attention_entity_master`

Structured metadata used for classification and enrichment.

Required fields:

- `symbol`
- `asset_class`
- `security_type`
- `sector`
- `industry`
- `subindustry`
- `country`
- `commodity_role`
- `rates_role`
- `defensive_role`
- `macro_role_tags`
- `business_role_tags`
- `peer_group_id`
- `source_of_truth`
- `override_reason`

## Resolver Contract

Primary homepage resolver:

- `resolve_attention_home_1d()`

Returned payload:

- `top_events`
- `must_read_movers`
- `unresolved_large_moves`
- `generated_at_utc`
- `coverage_summary`
- `event_candidates_1d`
- `event_impacts_1d`
- `entity_master`

Lazy detail resolver:

- `resolve_attention_research_bundle(bundle_id)`

The old `attention_feed` and `commodity_attention_feed` remain supporting datasets only. They are not the homepage source of truth.

## Coverage Model

### Base universe

- target top ~1500 liquid US equities by dollar volume
- implementation note (verified 2026-05-19): the shipped default is
  `EQUITY_UNIVERSE_TARGET_SIZE=1000`, snapshots use the IEX feed, and the universe is
  padded up to the target with a sub-threshold `liquidity_fallback` tier when fewer names
  pass the liquidity filters. See `ATTENTION_FEED_GENERATION_TRACE_AND_OVERINDEXING_2026-05-19.md`.
- all current portfolio holdings
- curated macro anchors across oil, gas, gold, silver, Treasuries, dollar, credit, volatility, airlines, travel, semis, defensives, and broad-market ETFs

### Daily inclusion guarantees

Every hourly run must include:

- top 25 liquid gainers by `1d` move
- top 25 liquid losers by `1d` move
- top macro anchors by absolute `1d` move
- any symbol above the configured move plus liquidity threshold
- any materially moving portfolio holding

This is what prevents obvious names like oil, `PYPL`, `QXO`, `MT`, or `FSLY` from being buried.

## Ranking Model

### Candidate score

Candidate ranking should combine:

- actual `1d` move magnitude
- surprise versus expected `1d` move
- liquidity / dollar-volume significance
- same-day evidence quality
- breadth of peer or cross-asset confirmation
- portfolio relevance as a modifier, not as a gate

### Event score

Event ranking should combine:

- anchor move severity
- breadth across affected assets
- market importance
- quality of same-day explanation
- clarity of spillover

### Dedupe rule

The same symbol may appear only once across homepage rails unless nested inside a drilldown.

## Agentic Research Stack

The research layer should be pluggable, but the recommended default stack is:

- workflow/orchestration: `LangGraph`
- model runtime and tool use: `OpenAI Responses API`
- search providers: `SerpApi` and/or `Tavily`
- extraction: `Firecrawl`
- browser fallback: `Playwright`
- official-source connectors: direct `SEC EDGAR`, `FRED`, issuer IR pages, and other primary endpoints where available
- storage and cache: Postgres for structured evidence, blob/object storage for raw page snapshots, Redis or queue-backed workers for batch execution

### Why this stack

- deterministic market-activity selection keeps coverage broad and reliable
- agentic search improves same-day explanation quality
- direct official-source ingestion avoids search-engine dependence for filings and macro data
- cached extracted evidence keeps latency acceptable for hourly publishing

## Agent Roles

For each shortlisted event or symbol, the research graph should run specialized workers.

### `trigger router`

Classifies the trigger as:

- macro event
- sector / theme event
- single-name mover
- unresolved candidate

### `official-source agent`

Pulls:

- SEC filings
- company IR releases
- government / macro statements
- FRED or other official time series for background context only

### `market-news agent`

Pulls:

- wires
- reputable finance press
- broader web coverage when higher-authority evidence is thin

### `peer-and-spillover agent`

Checks:

- same-sector peers
- suppliers / customers where mapped
- macro beneficiaries / losers
- ETFs and related proxies

### `evidence judge`

Scores:

- freshness
- symbol relevance
- causal relevance
- source authority
- conflict / contradiction

### `writer`

Outputs only:

- what changed today
- why today
- what else moved

The writer must only use retained evidence rows that passed the judge.

## Search And Retrieval Strategy

### Search

Use search engines for discovery, not as the final truth source.

Recommended search query families:

- `${symbol} stock up today`
- `${company_name} ${date} news`
- `${symbol} SEC 8-K`
- `${company_name} investor relations press release`
- `${theme} today`
- `${symbol} peers today`

### Extraction

After discovery:

- fetch the actual page
- extract the body text or structured content
- store a normalized snapshot
- dedupe repeated articles and syndications

### Official sources

Where an official source exists, prefer direct fetch over search:

- SEC EDGAR for filings
- issuer IR or newsroom pages
- official government releases

## SEC Parsing Requirements

The current system cannot stop at filing labels like `8-K` or `10-K`.

The SEC pipeline must:

1. fetch the filing body
2. parse filing sections
3. extract key facts
4. classify the filing type and materiality
5. decide whether the filing is a fresh catalyst, background context, or likely irrelevant to today

### Material facts to extract

- earnings or preliminary results
- guidance changes
- leadership changes
- auditor changes
- debt or equity financing
- M&A or asset sale activity
- litigation or regulatory outcomes
- approvals or contract wins
- customer, pricing, or commercial disclosures

### Filing relevance rules

- a same-day filing with material new information can support `why today`
- an older filing can be cited as background context only
- a filing headline alone does not justify a `supported` cause label
- auditor or routine governance filings should not be treated as a move catalyst unless evidence explicitly connects them to the move

## Evidence Model

Each retained evidence row must carry:

- `evidence_id`
- `symbol`
- `event_id` if applicable
- `source`
- `source_type`
- `authority_bucket`
- `published_at`
- `retrieved_at`
- `headline`
- `summary`
- `excerpt`
- `url`
- `freshness_score`
- `relevance_score`
- `causal_score`
- `evidence_role`
- `is_same_day`

### Source hierarchy

1. official / primary
2. top wires
3. reputable finance press
4. broader web

### Evidence roles

- `fresh_catalyst`
- `same_day_confirmation`
- `background_context`
- `peer_confirmation`
- `market_color`
- `conflicting_claim`

### Cause status

The final explanation must classify into exactly one of:

- `supported`
- `continuation`
- `unresolved`
- `conflicting`

`supported` requires same-day or clearly causal evidence.

`continuation` means the move is still likely reacting to a prior catalyst, but no clear fresh catalyst was identified today.

`unresolved` means the move is important, but no trustworthy causal explanation is available yet.

`conflicting` means multiple plausible explanations disagree materially.

## Homepage Behavior

The homepage has only three rails:

- `Top Market Events Today`
- `Must-Read Movers Today`
- `Unresolved Large Moves`

### Top Market Events Today

- real daily cross-asset or thematic events
- 3 to 5 cards
- concise `what happened / why it happened / affected assets`

### Must-Read Movers Today

- important single-name or isolated movers
- 5 to 10 cards
- strong names not absorbed into a larger event belong here

### Unresolved Large Moves

- important moves with weak or conflicting evidence
- the move stays visible, but the explanation remains honest

## Drilldown Contract

Each drilldown must render these sections in this order:

1. `What Changed Today`
2. `Why Today`
3. `What Else Moved`
4. `Evidence`
5. `Background Context`

### Drilldown rules

- no residual or z-score prose in the explanation sentence
- no `Unknown | Unknown` metadata if structured classification is available
- no unrelated roundup articles in a symbol drilldown
- if no clear spillover exists, say so explicitly
- if evidence is thin, label the cause accordingly instead of writing around it

## Failure And Fallback Behavior

If the research layer fails partially:

- the deterministic market-activity view still publishes
- the move still appears if it is important
- confidence and cause status degrade
- no speculative explanation is emitted

If search or scraping fails:

- use official and cached sources first
- mark the item unresolved if no sufficient same-day explanation survives

## Success Criteria

The redesign succeeds only if the product reliably does the following:

- surfaces the obvious things the user needs to read today
- explains those moves with real same-day evidence or honest uncertainty
- parses official filings instead of only labeling them
- shows actual peer or cross-asset spillover where it exists
- stops producing misleading, generic, or stale narrative text
