- [x] Compress market opportunity to a single table. Right now it's 3 different table + many other tools we can call on a ticker. I'd like to consume all that data, compress it to one feed of Market opportity for each stock and columns can add further details.

- [x] Agentic summary (Home page style) for Market Explorer, Stock Investigator and Broad Economy pages. Updates, what is worth looking into, etc.

- [x] Trading Agent - A Brand new experiment page (available only to admin) that consumes summaries from all agentic summaries, gathers additional evidence as neccesary and makes trade suggestions. Trading philosophy is largely - observe broad economic patterns (go with the wind), observe momentum, build hypothesis, validate hypothesis, identify tail risk, trade.


- You did a terrible job of reinventing things and putting it in the hot path riddled with bugs. You did not follow the existing arhcitecture, instead invented a new one. You did not test end to end. (Trading agent is showing a bug ValueError: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()), Market opportunity is is slow to load and borad economy is doing this - AQL agent failed: ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response')). MAKE SURE ALL THIS IS FIXED.

- Go back and rethink your strategy. Go through the learnings and mistakes relevant here. Come up with a better way to reference learnings, mistakes etc if it's too large. (Do we need a directory?) And make sure this never happens again.

- Make sure the new features fit in elegantly with the current setup. Clean up any mess, loose ends, irrelavant fallbacks. Make everything **sharp** and **clean**. Remove code that doesn't add value.

- [x] Inventory and move UI job trigger buttons to Admin > Pipeline Jobs so normal users do not see or trigger jobs.

- [x] Run Trading Agent as a materialized pipeline job across 1 week, 1 month, 3 month, 1 year, and 5 year horizons.

- [x] Log Trading Agent Place / Reject decisions. Place is log-only for now, but the audit row keeps Alpaca handoff fields for future order submission.

## v0.6 AQL/Zopedia Gateway

- [x] Build the initial enforceable AQL/Zopedia model gateway described in `documents/plans/V0_6_AQL_ZOPEDIA_GATEWAY_ROADMAP_2026-07-01.md`.

- [ ] Reuse the existing Zopedia/AQL knowledge cache consistently across product surfaces. Do not create a second caching layer; feature modules may materialize display artifacts, but reusable research knowledge belongs in Zopedia memory, retained evidence, pages, proposals, and source refs with freshness policy attached.

- [x] Persist an initial model-call ledger with `surface`, `purpose`, `call_type`, provider, requested model, resolved model, status, sanitized error, timing, prompt/schema hashes, and metadata links. Provider-reported model, token usage, and cost stay pending until adapters expose them.

- [x] Hard-move active Zopedia implementation ownership out of Omnibar-named modules. Production code uses `services.zopedia_agent`, `services.zopedia_research`, `services.zopedia_resolver`, and `/v1/zopedia/*`; old `services.omnibar*` modules, `/v1/omnibar/*` routes, and `omnibar:resolve` scope are retired.

- [ ] Replace the old "reviewed direct `generate_json` call" standard with a source-scan allowlist limited to gateway internals, provider adapter internals, tests/fakes, and temporary migration entries with owner and expiry.

- [ ] Complete Trading Agent migration through the gateway. Final candidate synthesis and research reviews are done; per-ticker tool-agent planner/model steps still depend on the general AQL/Zopedia agent-loop migration. Candidate Package V2 should not become the default until this is done.

- [ ] Migrate Attention/Home group synthesis, ticker enrichment, public review, and market-summary calls through the gateway. AQL search acquisition now uses Serper as the default/primary provider through the shared AQL loader, labels the real provider, and routes collector LLM planning/relevance/router calls through the gateway. Tavily and SerpAPI are parked unless deliberately re-enabled in a future provider policy. Remaining work is the broader Home synthesis/review surfaces.

- [ ] Migrate Zopedia chat synthesis, memory proposal generation, maintenance learning, KG expansion, company memory, and news-business resolution through the gateway.

- [ ] Add Admin/System Health visibility for provider/model failures, latest failing surfaces, quota/auth errors, and surface-level usage.

## Zopedia Retrieval / Tool Discovery

- [ ] Add bounded BM25 lexical retrieval as a first-class signal for Zopedia memory pages, retained evidence chunks, and agent tool metadata. Keep it hybrid with embeddings and reranking; BM25 should improve exact-term recall, not become a hardcoded routing layer.

- [ ] Add cursor pagination to search-style tools where top-k truncation can hide evidence: `zopedia.search_pages`, `research.search_evidence`, retained source/chunk search, news/source search, and any future tool-search endpoint. Include `has_more`, `next_cursor`, `total_estimate` when available, and ranking/match diagnostics.

- [ ] Add agent-facing tool search over the shared tool catalog. Index tool name, description, schema, examples, failure modes, empty-result causes, and fallback hints so the agent can find capabilities like event-window returns or primitive price data without relying on a giant prompt or hardcoded query classifiers.

- [ ] Teach the planner to page or search tools only when an evidence-plan slot remains unfilled. The intended loop is: missing slot -> tool search or memory/source search -> call best tool -> paginate or fallback if coverage is partial -> inspect `empty_reason` before stating a limitation.

- [ ] Add evals for retrieval/tool discovery: exact ticker/company/event terms, long natural-language wiki queries, tool lookup for event-window ETF returns, and evidence cases where the needed row appears after the first page.

## Zopedia Deferred UI Shots

- [ ] Take a focused shot at the full Zopedia graph explorer after the backend graph contract is stable. Do not lead with this. Wait until provenance, backlinks, and godnodes/community indexes exist so the graph shows real semantics instead of decorative nodes.

- [ ] Evaluate whether advanced graph modes can be integrated cleanly: community view, page-neighborhood view, evidence-trace view, proposal overlay, bridge/surprising-connection view, and delete queue. If the implementation requires special-case graph state or duplicated KG logic, keep it out of the core Zopedia UI.

- [ ] Revisit a Wiki Behaviour/config panel after core Zopedia works. Keep runtime/budget controls admin-only unless the setting directly helps a normal user complete an action.
