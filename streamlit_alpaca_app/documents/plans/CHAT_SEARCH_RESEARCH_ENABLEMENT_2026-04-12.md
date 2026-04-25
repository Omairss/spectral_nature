# Chat + Search Research Enablement

Date: 2026-04-12

## Goal

Make `Chat + Search` reliably gather retained narrative context and fresh external evidence before answering time-sensitive analysis prompts.

For prompts like:

`How are things going to pan out now that there's no agreement in Iran US talks`

the system should:

- pull the best retained event or narrative context already in the product
- expand the event into likely impacted assets and sectors
- fetch current evidence when the question is time-sensitive
- answer with visible grounding instead of defaulting too easily to a zero-tool freehand synthesis

## Current Status

Implemented in this pass:

- added `research.retained_context`, `research.market_impact_map`, `research.live_event_evidence`, and `research.open_page` to the omnibar tool catalog
- added an evidence-seeking bias for live analysis prompts in the omnibar planner prompt
- passed richer research tool context into the planner and final synthesis path
- lightly encouraged one or two supporting source mentions when live external evidence is used
- added bounded page browsing with Playwright-preferred reads and an HTTP fallback

Still open:

- full browser-runtime provisioning for Playwright-heavy sites in deployed containers
- richer citation UI on the main answer surface
- broader config-driven impact maps beyond the first market-event themes

## What is happening now

- Routing into agent mode is working.
- The failure happens after routing.
- `services/omnibar_agent.py` lets the planner return `action="final"` immediately, so a model can answer with `0` tool calls.
- `services/agent_tools.py` only exposes `QueryService` dataset and chart tools.
- There are no omnibar tools for:
  - external web search
  - Tavily RAG fallback
  - source-document retrieval
  - agentic page browsing with Playwright
- Stronger research logic already exists in `services/attention_agentic.py`, but `Chat + Search` does not use it.
- Internal narrative sources already exist, but they are not elevated as first-class research tools:
  - `attention_home_1d`
  - `attention_research_bundle`
  - `attention_ticker_background`
  - `recent_news`
  - `attention_run_trace`

## Root causes

1. The planner is allowed to finalize before gathering evidence, and today there is not enough bias toward using the right tools first.
2. The omnibar tool surface is narrower than the product expectation.
3. The stronger attention research stack is separate from the omnibar agent path.
4. Event prompts are not being decomposed into impacted assets, themes, and second-order spillovers before planning.
5. The answer contract does not even lightly prefer source diversity or a couple of supporting citations for evidence-seeking prompts.
6. There is no existing omnibar support for agentic Playwright browsing, so the agent cannot open and inspect selected pages after search.

## Existing pieces we should reuse

Use existing code instead of building a second research system:

- `services/attention_agentic.py`
  - query planning
  - SerpApi search
  - Tavily fallback
  - source-document chunking
  - embeddings
  - evidence ranking
- `services/attention_market_events.py`
  - event themes
  - oil/rates/defensives/risk grouping
  - driver and beneficiary symbols
- `services/market.py`
  - commodity and proxy catalogs
- `documents/architecture/agents/DEPENDENCY_GRAPH_INTEGRATION_2026-04-10.md`
  - config-driven relationship expansion instead of Python hardcoding

## Recommended design

### 1. Add an evidence-seeking bias, not a hard evidence gate

Classify prompts before planning:

- direct lookup
- retained narrative lookup
- ticker drilldown
- market event or macro event
- open research

For prompts that ask `why`, `how this plays out`, `what changed`, `impact`, `outlook`, or anything tied to a live geopolitical or macro event:

- set `evidence_expected = true`
- strongly prefer using retained context first
- strongly prefer using a fresh external source when the question is clearly time-sensitive
- still allow a zero-tool answer when the model already has enough nearby retained context and low uncertainty

This keeps the system flexible while making tool use much more likely for the prompts that actually need it.

### 2. Expose a small number of high-level research tools

Do not dump dozens of low-level tools on the model and hope it self-assembles a research workflow.

Expose a few composed tools instead:

- `research.retained_context`
  - reads retained narrative beats, bundles, ticker background, and recent news
- `research.live_event_evidence`
  - wraps the existing `attention_agentic` search stack
- `research.market_impact_map`
  - expands an event into linked assets, sectors, and spillover candidates
- `research.open_page`
  - uses Playwright to open a selected result page and extract page text when snippets are too thin

This is more reliable than generic browser-first planning and keeps the answer path auditable.

### 3. Add deterministic event expansion before or during planning

For event prompts like Iran-US talks, do not rely on the LLM to invent the right follow-up assets.

Use config-driven expansion from existing event and dependency logic to produce candidate baskets such as:

- oil proxies
- energy equities
- travel and airlines
- broad risk assets
- rates and defensives
- freight or shipping proxies when supported by the graph or proxy catalog

Important:

- do not hardcode one-off symbols into the system prompt
- prefer config or graph-backed relationship data
- reuse the dependency-graph work where possible

### 4. Reuse the retained narrative stack first

Before external search, the agent should check:

- current homepage narrative beats
- matching research bundles
- ticker background and recent news for any impacted symbols
- recent event traces when debugging

That gives the answer a source-backed starting point and avoids paying for web search when the product already has a good explanation.

### 5. Reuse `attention_agentic` for search and use Playwright for page reads

Do not build a second search and RAG implementation for the omnibar.

Instead, wrap the existing attention research logic so `Chat + Search` can reuse:

- SerpApi primary search
- Tavily fallback when search is noisy or low-signal
- relevance filters
- chunked source documents
- embedding-backed document payloads

Then use Playwright selectively:

- after search has identified promising pages
- when snippets are too shallow
- when the answer depends on narrative details not present in the snippet
- when the user explicitly wants deeper browsing

Playwright should be the deep-read layer, not the first-line discovery layer.

### 6. Tighten the answer contract lightly

For evidence-seeking prompts, the final payload should prefer:

- `used_tool_call_ids`
- one or two supporting links or source mentions when external evidence was actually used
- a short evidence line or cited claim when it materially strengthens the hypothesis
- an uncertainty note when evidence is thin

This should be encouraged, not mandatory on every answer.

If the system cannot gather enough evidence, it should say that plainly instead of answering as if it already knows.

### 7. Add Playwright browsing as a first-class capability

Current state:

- there is browsing-related code in the repo, but not wired into the omnibar agent path as a registered tool
- the agent cannot currently choose a result, open the page, and read it as part of the normal `Chat + Search` workflow

Add a bounded browsing tool that:

- opens only selected URLs, not open-ended crawling
- extracts visible text, title, and canonical URL
- returns compact page summaries plus raw text slices for downstream evidence extraction
- times out safely and degrades cleanly

Current state:

- Wikipedia exists only as a company-background helper in `services/company.py`
- there is no private wiki/search connector in this repo for the omnibar

If a private corpus is added later, define:

- the source of truth
- auth and access method
- ingestion or search API path
- chunk and citation schema
- a registered tool

Until then, the web-browsing path should mean search plus optional Playwright page reads.

## Implementation phases

### Phase 0. Instrumentation and regression coverage

- log route subtype, `evidence_expected`, tool count, source groups, and finalization reason
- add a regression test for a time-sensitive event prompt that should usually use tools when the relevant tools are available

### Phase 1. Internal retained-context tool

- add `research.retained_context`
- make event and outlook prompts hit retained narrative sources before answering
- surface bundle and summary text in the answer path

### Phase 2. External live-evidence tool

- wrap the `attention_agentic` search stack as `research.live_event_evidence`
- return chunked evidence rows, citations, and source metadata

### Phase 3. Playwright page-read tool

- add `research.open_page`
- use it only after search returns a promising URL
- extract page text for evidence ranking and hypothesis support

### Phase 4. Impact-map expansion

- add `research.market_impact_map`
- source baskets from `attention_market_events.py`, `market.py`, and dependency-graph data
- use config, not prompt-only symbol lists

### Phase 5. UI grounding

- show citations or evidence chips on the main answer
- keep deeper traces in admin debug
- explain when the answer is using retained context only versus fresh web evidence

### Phase 6. Optional private corpus connectors

- add any private/wiki source only after the source contract is clear

## Verification

Add tests for:

- route classification marks geopolitical outlook prompts as `evidence_expected`
- omnibar planner is biased toward tool use for evidence-seeking prompts when relevant tools are available
- retained-context tool returns relevant bundle and summary data
- live-evidence tool reuses the existing search and fallback logic
- Playwright page-read tool extracts useful text from selected results without turning into open-ended crawling
- impact-map tool expands a live oil/geopolitical prompt into affected-asset baskets from config
- app-level render shows grounded answer content and admin debug still shows tool traces

Manual checks should include prompts like:

- `How are things going to pan out now that there's no agreement in Iran US talks`
- `Why are oil names moving if Iran talks stalled`
- `What else should move if crude stays bid from here`
- `Compare USO, CVX, airlines, and shipping after the latest Iran headline`

## Reliability and complexity

Lowest-risk path:

- reuse `attention_agentic`
- reuse retained narrative datasets
- add a small number of composed research tools
- add a strong evidence-seeking preference

Highest-risk path:

- give the LLM open-ended browser tools and expect it to discover the right workflow every time

That second path is slower, more expensive, and less reliable. It should not be the first implementation.

## Recommended order

1. Fix the zero-tool tendency with an evidence-seeking preference.
2. Add a retained-context research tool.
3. Add a live-event evidence tool using the existing attention research stack.
4. Add bounded Playwright page reads for selected results.
5. Add config-driven impact expansion for second-order assets.
6. Add optional private/wiki connectors only after their source contracts exist.
