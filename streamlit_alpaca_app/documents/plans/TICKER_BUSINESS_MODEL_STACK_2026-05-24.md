# Ticker Business Model Stack

Status: implemented locally, hardened after live-probe review, not run as a scheduled pipeline job.

## Goal

Company news should resolve against durable business memory before becoming a story. The system should not jump from a headline or price move directly to a technical/statistical review. It should first ask what the company sells, who buys it, whether demand is improving, how fundamentals look, what workforce/customer/policy signals say, and what evidence gaps remain.

## Architecture

The implementation stays on the Zopedia/AQL spine:

1. Connector/adapters fetch evidence.
2. Zopedia pages and materialized evidence hold source-backed facts.
3. AQL/Zopedia builds a ticker-specific business research plan through a structured planner boundary.
4. Optional connector execution searches those slot questions through the shared search adapters and opens promising pages through the shared page-acquisition path.
5. AQL asks the shared tool-using Zopedia agent for per-slot verdicts on missing business questions.
6. AQL asks the shared tool-using Zopedia agent for the structured ticker business story.
7. AQL builds a ticker-specific `zopedia_ticker_business_model_stacks` row with coverage state.
8. News resolution reads that stack, then asks the shared tool-using Zopedia agent to resolve the event story.
9. Pipeline outputs remain materialized datasets; no UI/on-click research path is added.

## New Contract

Dataset: `zopedia_ticker_business_model_stacks`

Each row contains:

- `symbol`, `company_name`, `status`, `confidence`
- `slot_facts_json` keyed by the business-memory slots
- `slot_gaps_json` for missing questions like employee sentiment, website attention, hiring, or policy
- `slot_coverage_json` showing whether each slot is supported, searched, planned, or absent
- `research_query_plan_json`, `search_request_ids_json`, `search_result_ids_json`
- `business_story_markdown`
- `business_memory_page_id`, `business_memory_body_markdown`
- source/read/proposal/evidence-pack ids

Supporting datasets:

- `zopedia_business_model_research_plans`
- `zopedia_business_model_search_requests`
- `zopedia_business_model_search_results`

These make the business-memory cold start inspectable before the narrative layer uses it.

The stack uses the same slot vocabulary as news-business resolution:

- business model
- products/services
- customer segments
- named customers
- customer demand
- fundamentals
- backlog/RPO
- cash/runway
- workforce/hiring
- employee sentiment
- web/developer attention
- policy/regulatory environment
- supply chain/capacity constraints
- execution risks
- confirmation events
- invalidation events

## Pipeline Behavior

`run_news` now builds ticker business stacks before resolving news:

1. Load prior draft business memory pages plus committed Zopedia pages.
2. Load company baselines and quarterly fundamentals.
3. Build a business research plan for missing slots.
4. If `ZOPEDIA_TICKER_BUSINESS_RESEARCH_ENABLED=true`, run bounded SerpAPI/Tavily searches for the plan.
5. Build `zopedia_ticker_business_model_stacks`.
6. When an AQL/Zopedia LLM runtime is configured, the research plan, missing business questions, and stack story are resolved through the shared `run_aql_zopedia_structured_agent` tool harness.
7. Pass those stacks into `build_news_business_resolution_frames`.
8. When an AQL/Zopedia LLM runtime is configured, news-business synthesis is resolved through `run_aql_zopedia_structured_agent`.
9. Persist:
   - `zopedia_business_model_research_plans`
   - `zopedia_business_model_search_requests`
   - `zopedia_business_model_search_results`
   - `zopedia_ticker_business_model_stacks`
   - `zopedia_news_business_resolutions`
   - `zopedia_company_business_memory_pages`

This means future runs can read the stack artifact directly instead of reconstructing the business context from scratch.

## Guardrails

- Theme/concept pages can provide context but are not promoted into company facts.
- Generated memory pages only carry forward original source-marked facts.
- Low evidence leaves slot gaps instead of inventing employee/headcount/web/policy facts.
- If the AQL/Zopedia verdict layer does not run or does not return a structured verdict, story fields stay empty and rows move to low-confidence unavailable states.
- A story with no source-backed slot facts is rejected, even if the formatter returned fluent markdown.
- Unresolved strings such as "No evidence available" never count as supported slot facts.
- Search-result facts must be company-relevant and slot-relevant before promotion; generic Similarweb/search/quote pages stay as searched evidence, not memory facts.
- Search-result-backed facts are treated as source-backed wiki facts when generated ticker business pages are traversed later.
- AQL/Zopedia-agent-backed verdict facts carry an `aql_zopedia_agent::...` source prefix so later wiki traversal can distinguish them from raw connector facts.
- Connector search is bounded by `ZOPEDIA_TICKER_BUSINESS_RESEARCH_QUERY_LIMIT` and `ZOPEDIA_TICKER_BUSINESS_RESEARCH_RESULTS_PER_QUERY`.
- Background research budgets default to long-running job settings: one-hour research budget, larger per-slot and stack tool-call caps, and crawler page-open budgets. Interactive chat settings do not constrain scheduled business research.
- Search and per-slot verdicts can run in parallel. Search workers clone connector clients from config so SerpAPI/Tavily sessions are not shared across threads.
- Slot and stack verdict agents use the general AQL/Zopedia tool harness. Wandering is controlled at the shared agent boundary through task/surface context, required Zopedia traversal, source-opening discipline, and the trajectory monitor rather than a feature-local tool filter.
- Hidden retained-evidence prefetch is disabled for business-stack verdict calls because the stack passes its own planned connector evidence and Zopedia-page context. This prevents a local DB lookup from blocking before verdict synthesis.
- Provider/agent warnings are redacted before being stored in stack rows.
- Connector-specific logic remains outside this layer. Future Tavily Extract, Firecrawl, Similarweb, or hiring-data adapters should feed normalized source evidence into this contract.

## Live-Probe Path

`scripts/company_business_stack_probe.py` runs the actual stack builder from a real script entrypoint. This matters because browser/crawler and analysis tooling can spawn child processes, and Python cannot re-import `<stdin>` in those children. The probe writes compact JSON/Markdown review artifacts under:

```text
documents/architecture/new_features/zopedia/company_business_probes/
```

The probe is not the production job. It is the qualitative review harness for inspecting planned queries, source intents, opened pages, slot facts, gaps, warnings, and accepted/rejected business stories before running scheduled jobs.

## Local Proof

Current targeted tests passed:

```text
26 targeted tests passed.
```

Covered:

- cold-start ticker stack creation
- per-missing-question AQL/Zopedia slot verdict calls
- structured AQL/Zopedia stack and news resolution synthesis boundary
- regression guard preventing direct `.generate_json()` calls in business stack/news resolution modules
- research-plan generation for workforce, sentiment, web attention, policy, and operating-business questions
- bounded fake connector execution into research request/result artifacts
- company-relevance and slot-relevance gates for promoted search evidence
- generated business wiki pages rehydrating search-result-backed facts on later traversal
- provider warning redaction
- stack use by news resolution
- broad theme pages kept out of company memory
- pipeline dataset registration
- data-access resolver for the new stack dataset

No scheduled jobs were run.

## Current Example Contract

Without the AQL/Zopedia verdict layer, a test fixture row can retain source facts such as:

```text
Business model: ExampleCo sells specialized infrastructure capacity.
Customer demand: a named customer is expanding contracted capacity with ExampleCo.
Fundamentals: revenue doubled, while operating loss widened.
Employee sentiment: employee reviews are mixed, with work-life balance pressure.
```

That is not enough for a user-facing story. The row must remain `needs_synthesis` with an empty story.

With the AQL/Zopedia verdict layer, the stored story has to make the business call:

```text
ExampleCo demand is improving: the customer expansion adds another contracted-capacity signal on top of the existing business model. The fundamental read is mixed rather than simply good: revenue is scaling quickly, but operating losses show the buildout is still expensive. Employee sentiment is also mixed, so rapid hiring/scaling is an execution risk rather than a clean positive.
```

This is the bar: evidence is retained as facts; verdicts come from Zopedia.
