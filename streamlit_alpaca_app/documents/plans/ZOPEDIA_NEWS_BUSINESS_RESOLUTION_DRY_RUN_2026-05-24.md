# Zopedia News Business Resolution Dry Run

Date: 2026-05-24

## Objective

Generalize the Trading Agent business-first idea across Zopedia.

When a new news item arrives, the system should not summarize the headline in isolation. It should map the new information onto what Zopedia already knows about the company: business model, products, customers, fundamentals, workforce, policy environment, risks, and prior claims. The output should be a coherent story with source support and memory-update proposals.

## Local Wiki-First Attempt

I tried the intended path first:

- `search_zopedia_pages(query="QBTS D-Wave Quantum business model bookings CHIPS")`
- `search_zopedia_pages(query="CRWV CoreWeave AI infrastructure demand revenue")`
- `search_zopedia_pages(query="NBIS Nebius AI cloud data center business")`

Initial result: `_db_connection()` returned `False`, so DB-backed Zopedia pages appeared unavailable. Follow-up on 2026-05-24 fixed the local secret-resolution path in `services.secrets`; `_db_connection()` now reaches Postgres through the generated deployment Key Vault context.

Updated result after the fix: the wiki store is reachable, but the company/business pages are still missing. `saa_zopedia_pages` has 50 active macro/eval-oriented pages and no pages for `QBTS`/D-Wave, `CRWV`/CoreWeave, or `NBIS`/Nebius. The retained evidence substrate does have company chunks, so the remaining product gap is source-to-company-page promotion and then news resolution against those pages.

Fallback evidence used for the manual trial:

- local `attention_ticker_zopedia_enrichments` cache for the current attention candidates
- local cached quarterly fundamentals where available
- local cached news/EDGAR evidence for CRWV and NBIS
- current official investor/company sources from the web

## Trial 1: QBTS / D-Wave Quantum

### Incoming News

D-Wave and the U.S. Department of Commerce signed a letter of intent for $100 million in proposed CHIPS and Science Act funding. The U.S. government would receive D-Wave common stock if final award documents are executed.

### What Existing Company Memory Should Contain

- D-Wave sells dual-platform quantum systems, software, services, and Leap cloud access.
- Advantage2 annealing systems are commercially available; gate-model work accelerated through Quantum Circuits.
- D-Wave revenue is still small and lumpy; demand is better seen through bookings, RPO, customer count, and named customer deployments.
- Q1 2026 bookings were $33.4 million, including a $20 million FAU system purchase and a $10 million two-year enterprise QCaaS deal with a Fortune 100 customer.
- Q1 2026 revenue was $2.9 million, while RPO reached $42.4 million.
- D-Wave recognized Q1 revenue from more than 100 customers, with over half commercial enterprises.

### Resolution

The CHIPS LOI supports the industrial-policy and financing side of the D-Wave story. It does not by itself prove customer demand or near-term revenue conversion.

The coherent story is:

D-Wave has commercial demand signals in a speculative sector: system purchase, enterprise QCaaS, named customer projects, and over 100 revenue customers. The CHIPS LOI adds public-sector endorsement and possible non-operating capital for scaling annealing and gate-model systems. The thesis depends on converting bookings/RPO into revenue and executing roadmap milestones. The main risks are final-award uncertainty, dilution from equity issuance, small current revenue, and technical execution.

### Memory Updates Zopedia Should Propose

- Add source page: `D-Wave CHIPS Act LOI, 2026-05-21`
- Update company page: `D-Wave Quantum`
- Update theme page: `Quantum Computing Industrial Policy`
- Update product pages: `Advantage2`, `Leap Quantum Cloud`, `Gate-Model Quantum Roadmap`
- Add or update edges:
  - `D-Wave Quantum` receives proposed funding from `CHIPS and Science Act`
  - `D-Wave Quantum` sells `Advantage2`, `Leap`, `Quantum Services`
  - `D-Wave Quantum` has customer/use-case evidence from `FAU`, `Fortune 100 QCaaS`, `Shionogi`, `Postquant Labs`

## Trial 2: CRWV / CoreWeave

### Incoming News

The local cached news feed contains the Meta $21 billion expansion, Anthropic multi-year AI cloud deal, product launches, and a debt offering. CoreWeave's Q1 2026 results say revenue backlog reached $99.4 billion.

### What Existing Company Memory Should Contain

- CoreWeave sells specialized AI cloud infrastructure, GPU capacity, inference, orchestration, and developer/ML tooling.
- Customers are AI labs, hyperscalers, and enterprises.
- Q1 2026 revenue was $2.078 billion versus $982 million in Q1 2025.
- Revenue backlog was $99.4 billion as of March 31, 2026.
- CoreWeave has major customers or customer groups including Meta, Anthropic, Cohere, Jane Street, Mistral, Perplexity, and World Labs.
- The business is capital intensive: Q1 net loss was $740 million, interest expense was $536 million, and the company is using large debt facilities to build capacity.

### Resolution

The new feed confirms that CoreWeave demand is real at the signed-contract/backlog level. The better story is not "CRWV went up on AI." It is: hyperscalers and AI labs are outsourcing scarce AI infrastructure capacity to CoreWeave, and the company has translated that into large backlog.

The contradiction or tension is capital intensity. Revenue and adjusted EBITDA show scale, but the business needs enormous power, data-center capacity, GPUs, and financing. The correct story must carry both facts: customer demand is very strong; execution risk is capacity delivery, leverage, interest expense, customer concentration, and backlog conversion.

### Memory Updates Zopedia Should Propose

- Add source pages:
  - `CoreWeave Q1 2026 Results`
  - `CoreWeave Meta $21B AI Infrastructure Agreement`
  - `CoreWeave Anthropic AI Cloud Agreement`
- Update company page: `CoreWeave`
- Update theme pages:
  - `AI Neoclouds`
  - `AI Infrastructure Demand`
  - `Hyperscaler Capex Transmission`
  - `Customer Concentration Risk`
- Add or update edges:
  - `Meta` buys AI cloud capacity from `CoreWeave`
  - `Anthropic` uses `CoreWeave` for Claude infrastructure
  - `CoreWeave` depends on `NVIDIA GPUs`, `Power Capacity`, and `Debt Financing`

## Trial 3: NBIS / Nebius Group

### Incoming News

The local cached feed includes NBIS news around a Meta AI infrastructure agreement and earnings expectations. Nebius's Q1 2026 shareholder letter says group revenue reached $399.0 million, Nebius AI cloud revenue reached $389.7 million, and the latest Meta agreement can total $27 billion over five years.

### What Existing Company Memory Should Contain

- Nebius is an AI infrastructure company and full-stack AI-native cloud provider.
- The company is expanding beyond raw compute into inference, agentic workloads, and platform software through Tavily, Eigen AI, and Clarifai.
- Q1 group revenue was $399.0 million, up 684% year over year.
- Nebius AI cloud revenue was $389.7 million, up 841% year over year, and about 98% of group revenue.
- ARR was $1.92 billion at quarter end.
- 2026 guidance targets $3.0 billion to $3.4 billion revenue and $7 billion to $9 billion ARR.
- Meta agreement structure: $12 billion committed compute capacity starting early 2027 plus an additional $15 billion capacity option or market-rate resale structure.
- The company is hiring senior GTM leadership across Americas, APJ, and Middle East/Africa.

### Resolution

The Meta agreement confirms the strategic demand side of the Nebius story but is partly long-dated. The Q1 results confirm current revenue acceleration and operating leverage in AI cloud. The coherent story is:

Nebius is scaling from AI compute provider into a fuller AI cloud and inference platform. Demand is visible in revenue growth, ARR, Meta/Microsoft-style large agreements, healthcare/life-sciences customer wins, and GTM hiring. The risk is execution: bringing capacity online, financing capex, delivering on long-dated Meta capacity, integrating acquisitions, and avoiding customer concentration.

### Memory Updates Zopedia Should Propose

- Add source pages:
  - `Nebius Q1 2026 Shareholder Letter`
  - `Nebius Meta AI Infrastructure Agreement`
- Update company page: `Nebius Group`
- Update theme pages:
  - `AI Infrastructure Demand`
  - `Full-Stack AI Cloud`
  - `Inference Platform Consolidation`
  - `Hyperscaler AI Capacity Procurement`
- Add or update edges:
  - `Meta` buys capacity from `Nebius`
  - `Nebius` sells `AI Cloud`, `Token Factory`, `AI Infrastructure`
  - `Nebius` acquired `Tavily`, `Eigen AI`, and `Clarifai`
  - `Nebius` depends on `Power Capacity`, `GPU Supply`, and `Data Center Execution`

## Production Design

### Principle

The resolver belongs inside the shared AQL/Zopedia engine. Do not build a new side agent.

The code can enforce contracts, slot coverage, source quality, timeouts, and mutation state. The LLM should extract and synthesize narrative from evidence; it should not be bypassed by hardcoded business heuristics.

### Required Flow

```text
news_articles / attention_web_search_news / edgar_evidence
  -> source page ingest
  -> entity resolution
  -> Zopedia page search/read/source trace
  -> fundamentals/company baseline retrieval
  -> claim extraction into evidence slots
  -> resolution against durable memory
  -> coherent story + evidence pack
  -> typed safe memory update or proposal
  -> materialized story artifact for UI/Trading Agent/Attention
```

### Core Evidence Slots

Company slots:

- `business_model`
- `products_and_services`
- `customer_segments`
- `named_customers`
- `customer_demand`
- `fundamentals`
- `backlog_or_rpo`
- `cash_and_runway`
- `workforce_and_hiring`
- `employee_sentiment`
- `web_or_developer_attention`
- `policy_or_regulatory_environment`
- `supply_chain_or_capacity_constraints`
- `execution_risks`
- `confirmation_events`
- `invalidation_events`

Resolution labels:

- `confirms_existing_story`
- `extends_existing_story`
- `contradicts_existing_story`
- `updates_magnitude`
- `stale_memory_detected`
- `insufficient_evidence`

### Data Contracts

New or revised engine contract:

- `NewsBusinessResolutionRequest`
  - `source_event_ids`
  - `symbols`
  - `query`
  - `surface`
  - `write_policy`
  - `evidence_slot_policy`

- `NewsBusinessResolutionResult`
  - `entities`
  - `source_page_ids`
  - `zopedia_page_ids_read`
  - `fundamental_datasets_used`
  - `slot_facts`
  - `resolved_changes`
  - `coherent_story_markdown`
  - `memory_mutation_ids`
  - `proposal_ids`
  - `evidence_pack_id`
  - `confidence`
  - `data_gaps`

Materialized dataset:

- `zopedia_news_business_resolutions`
  - one row per source event and resolved entity
  - stores the typed result, evidence pack id, source refs, proposal ids, and generated story

### Zopedia Page Model

Use existing page types first:

- `source`: each article, filing, press release, transcript, or uploaded source
- `ticker`: company/ticker page
- `entity`: named customers, products, executives, suppliers
- `theme`: cross-company themes such as AI infrastructure demand or quantum policy
- `market_event`: incoming news event or catalyst
- `concept`: products, technologies, metrics, risks

Add metadata, not new table sprawl:

- `slot_tags`: `["business_model", "customer_demand"]`
- `symbols`: `["CRWV"]`
- `source_authority`: `official_company`, `sec_filing`, `news`, `third_party_estimate`
- `freshness_class`: `current`, `slow_baseline`, `historical`
- `claim_confidence`
- `last_resolved_at_utc`

Typed market/causal edges should still go through `services.knowledge_graph` or proposal APIs, not raw wikilinks.

### Engine Tool Behavior

For company-related news, the planner should execute in this order:

1. `zopedia.search_pages` for company/ticker and themes.
2. `zopedia.read_page` for the top relevant pages.
3. `zopedia.sources_for_page` or `zopedia.trace_to_evidence` for original support.
4. `investigator.company_context`, `investigator.fundamentals`, and `investigator.recent_news`.
5. `research.live_event_evidence` only for current gaps.
6. `hypothesis.verify` or a new resolver judge over the filled slots.

The final answer or materialized story cannot be high-confidence unless the resolver read at least one company memory page, one current source page, and one fundamentals/company baseline source when available.

## Cold Start Slice Implemented

Status: implemented and deployed to dev pipeline jobs on 2026-05-24.

The first production slice does not wait for a fully populated wiki. It now creates two materialized datasets from the `news-ingest-and-features` pipeline job:

- `zopedia_news_business_resolutions`: one row per news event and symbol with source refs, company memory pages read, business slot facts, resolved changes, confidence, data gaps, proposal ids, and the coherent story.
- `zopedia_company_business_memory_pages`: draft ticker-shaped Zopedia pages built from source-backed business slots. Future pipeline runs read these drafts as cold-start memory before falling back to an empty wiki.

The resolver lives under the AQL/Zopedia boundary in `services/aql/news_business_resolution.py`. It uses existing Zopedia page helpers and proposal helpers. When an LLM client is available, it asks the model to fill business slots and synthesize the story; without an LLM, it still emits a bounded, low/medium-confidence artifact with explicit gaps.

Runtime proof exposed an important guardrail: related Zopedia theme/concept pages are useful context, but they cannot be promoted into company business-memory slots. Generated business-memory drafts are also not allowed to recursively summarize themselves into new business-model facts. They may only carry forward direct source-backed facts from `company_baselines`, `quarterly_fundamentals`, `news_articles`, and `edgar_evidence`.

The job now loads, in order:

1. previous `zopedia_company_business_memory_pages` drafts,
2. committed Zopedia pages when a DB connection is available,
3. `company_baselines`,
4. `quarterly_fundamentals`,
5. current `news_articles` and `edgar_evidence`.

Default write policy is still `propose`. The cold-start slice generates proposal ids and proposed page payloads, but it does not automatically commit broad company story rewrites into Zopedia. This keeps the first loop safe while making the materialized memory reusable by later runs.

Read path added:

- `DataAccessLayer.resolve_news_business_resolutions(ticker, limit=...)`

Tests added:

- cold-start company memory page generation,
- existing-memory read path before cold start,
- broad related theme pages stay out of company business-memory slots,
- generated memory pages cannot recursively promote prior summaries,
- persisted Parquet symbol arrays are parsed like live news lists,
- `news-ingest-and-features` persists the two datasets,
- data access resolves the materialized resolution dataset.

Dev proof:

- image `pipeline-jobs:20260524143354` was deployed to dev after the source-backed memory guard.
- manual execution `news-ingest-and-features-t4u7cln` succeeded on 2026-05-24.
- it persisted `zopedia_news_business_resolutions` rows=12 and `zopedia_company_business_memory_pages` rows=8.
- local replay and the final dev execution regenerated MRVL/FLY/LUNR memory without broad macro phrases in the company business-memory body.

### Memory Writes

`write_policy=none`:

- read-only trial mode for chat and evaluation.

`write_policy=propose`:

- default for production summaries at first.
- creates proposals for changed company story, stale pages, contradictory facts, or new relationships.

`write_policy=safe_auto`:

- safe source-backed writes only:
  - create source pages
  - attach source refs
  - add low-risk metadata
  - add source-backed facts with rollback snapshots

Risky changes stay proposals:

- rewriting company business model
- deleting old claims
- changing causal/risk edges
- broad thematic conclusions
- interpreting employee sentiment or weak web-traffic evidence

### Job Integration

Best integration point:

- extend `news-ingest-and-features` after `news_articles` and `edgar_evidence` persist.
- run a bounded Zopedia news-resolution stage for high-priority symbols/events.
- persist `zopedia_news_business_resolutions`.
- optionally let `attention-home-build`, Trading Agent, and page summaries consume this artifact instead of redoing the same research.

Do not put this on the UI render path.

### Tests And Gates

Golden scenarios:

- QBTS: CHIPS LOI must resolve as policy/financing support, not customer-demand proof.
- CRWV: Meta/Anthropic news must resolve as real AI infrastructure demand plus debt/capacity/customer concentration risk.
- NBIS: Meta agreement must resolve as long-dated capacity demand plus current Q1 growth and GTM expansion.

Required tests:

- source-level unit test for `NewsBusinessResolutionRequest`/`Result` schema.
- tool-trace test proving company news runs call `zopedia.search_pages` and `zopedia.read_page` before live search.
- empty-wiki test returns `insufficient_evidence` or `wiki_unavailable`, not a confident story.
- memory proposal test includes page IDs, source refs, before/after state, rollback hint.
- materialized dataset dtype test for proposal ids, confidence, numeric slots, and JSON fields.
- rendered card test: first viewport says what changed against the company story, not just the stock move.

### Immediate Implementation Slice

1. Create the typed request/result model and a small service module under the shared engine boundary.
2. Add a Zopedia company-memory bootstrap for top Attention symbols from company baselines, fundamentals, and retained source pages.
3. Add the resolver mode to `run_aql_zopedia_agent` rather than a new endpoint.
4. Wire one bounded pipeline stage in dev for 3-5 symbols.
5. Persist `zopedia_news_business_resolutions`.
6. Add the three golden evals above.
7. Verify dev job counts: source pages created, wiki pages read, proposals created, stories materialized.

## Source Register

- D-Wave Q1 2026 results: https://ir.dwavequantum.com/news/news-details/2026/D-Wave-Reports-First-Quarter-2026-Results/default.aspx
- D-Wave CHIPS LOI: https://www.dwavequantum.com/company/newsroom/press-release/d-wave-and-department-of-commerce-sign-letter-of-intent-for-100-million-in-chips-and-science-act-funding/
- CoreWeave Q1 2026 results: https://investors.coreweave.com/news/news-details/2026/CoreWeave-Reports-Strong-First-Quarter-2026-Results/default.aspx
- Nebius Q1 2026 shareholder letter: https://assets.nebius.com/assets/aa1bc2e6-df83-40cd-a6a2-95e7cda3d16c/Nebius%20SHL_Q1%202026.pdf
