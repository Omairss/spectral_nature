- [x] Compress market opportunity to a single table. Right now it's 3 different table + many other tools we can call on a ticker. I'd like to consume all that data, compress it to one feed of Market opportity for each stock and columns can add further details.

- [x] Agentic summary (Home page style) for Market Explorer, Stock Investigator and Broad Economy pages. Updates, what is worth looking into, etc.

- [x] Trading Agent - A Brand new experiment page (available only to admin) that consumes summaries from all agentic summaries, gathers additional evidence as neccesary and makes trade suggestions. Trading philosophy is largely - observe broad economic patterns (go with the wind), observe momentum, build hypothesis, validate hypothesis, identify tail risk, trade.


- You did a terrible job of reinventing things and putting it in the hot path riddled with bugs. You did not follow the existing arhcitecture, instead invented a new one. You did not test end to end. (Trading agent is showing a bug ValueError: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()), Market opportunity is is slow to load and borad economy is doing this - AQL agent failed: ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response')). MAKE SURE ALL THIS IS FIXED.

- Go back and rethink your strategy. Go through the learnings and mistakes relevant here. Come up with a better way to reference learnings, mistakes etc if it's too large. (Do we need a directory?) And make sure this never happens again.

- Make sure the new features fit in elegantly with the current setup. Clean up any mess, loose ends, irrelavant fallbacks. Make everything **sharp** and **clean**. Remove code that doesn't add value.

## Next.js Frontend Parity And Agentic Research Workbench


- [ ] Preserve the Spectral Nature identity while rebuilding the interface as a clean, sophisticated responsive product. Do not reproduce Streamlit's layout constraints, DOM, or CSS overrides, and do not add filler copy.

- [ ] Treat the destination as a chat-first market-intelligence IDE, not a visualization site: Bloomberg-like information density, Jupyter-like reproducible analysis, a VS Code-like tabbed and dockable agent workspace, and Claude Code-like collaboration over the user's active context. Chat is the only authoring and control interface; users do not write or execute code directly.

- [ ] Build one durable workspace model for tabs, panels, layouts, sessions, entities, datasets, files, notebooks, code, tables, charts, reports, graph canvases, citations, and background-job output. Existing product routes should be openable as workspace artifacts instead of becoming permanent isolated dashboard silos.

- [ ] Make agent-authored notebooks first-class product documents composed from typed Markdown, AQL query, dataset, code, visualization, graph, and report blocks. Persist the user's request, inputs, artifact versions, execution identity, source lineage, outputs, and rerun state so a research result is reproducible and can become a report or Zopedia update. Code and notebook cells are inspectable artifacts, not direct user-editing surfaces.

- [ ] Put all code, notebook, and long-running ML execution behind isolated per-run workers with explicit user identity, resource budgets, scoped inputs, bounded network and storage access, cancellation, durable logs, and typed outputs. The browser, Next.js BFF, and API hosts must never become a general shell or execute arbitrary analysis code in-process.

- [ ] Make the agent operate on explicit workspace context: the active conversation, selected outputs, open entities, pinned datasets, artifacts, source refs, and user-approved files. Agent-generated code and actions need inspectable diffs, durable run journals, and authorization at execution time; users request revisions through chat rather than editing code. Do not expose hidden reasoning or grant ambient repository, database, broker, or host access.

- [ ] Keep visualization contracts renderer-neutral. FastAPI should return typed analytical data, semantic encodings, provenance, and interaction metadata; the frontend may select the best renderer per surface instead of making Plotly or any other library the application contract.

- [ ] Complete behavioral parity before expanding the analytical repertoire. After parity, add a coherent advanced-analysis layer that can support word maps, multivariate heatmaps, high-density time series, linked views, knowledge and relationship graphs, network-science measures, and 3D plots without moving analytical meaning into TypeScript.

- [ ] Implement the renderer-neutral artifact and adapter plan in `documents/architecture/UI/ADVANCED_VISUALIZATION_ARCHITECTURE_2026-10-02.md`: Plotly for parity/scientific 3D, Vega-Lite for default analytical graphics, Perspective as the streaming financial-view candidate, Sigma/Graphology for networks, and deck.gl for GPU-scale views. Keep shared design tokens, selection state, provenance, exports, and failure behavior across adapters; retain only candidates that pass real development spikes.

  - [x] Establish `dataset.v1` and `visualization.v1`, the authorized Next.js BFF route, an allowlisted renderer registry, and the CSP-safe Plotly adapter.
  - [x] Migrate Stock Investigator's technical price-channel chart as the first real responsive parity slice.
  - [ ] Migrate the remaining existing chart families before adding Vega-Lite, Perspective, Sigma/Graphology, or deck.gl.

## Deferred Agent Runtime And Long-Running ML

- [ ] Park agent-harness replacement until the frontend parity milestone is materially complete. The deployed development slice is an application-owned agent runtime around the existing AQL/Zopedia loop, not proof that the custom loop is the best available harness.

- [ ] When resumed, run a development bake-off behind the existing `run_aql_zopedia_agent(...)` contract. Compare the current loop with Pydantic AI Harness as the leading Python candidate, DeepSeek Harness as an experimental reference, and LangGraph or a durable workflow runtime where explicit checkpointed orchestration is required. Preserve application-owned auth, tools, evidence, SAA, Zopedia, run state, and telemetry.

- [ ] Design long-running ML as a separate typed execution plane. The harness should submit, monitor, cancel, and interpret isolated ML jobs; it must not turn bounded `analysis.run_python` or an open model request into a training runtime.

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

- [ ] Complete Zopedia gateway migration. Chat planning, trajectory review, final synthesis, answer review, and post-answer memory reflection now use the gateway with the research run id. Maintenance learning, KG expansion, company memory, news-business resolution, and remaining source-scan entries are still pending.

- [x] Add scheduled System Health alerts for failing Market Overview and Trading Agent jobs, stale Market Overview data, empty Trading Agent horizon output, and provider billing/quota/authentication failures. Incidents are deduplicated and send a recovery email when resolved.

- [ ] Extend Admin/System Health visibility with provider/model failure history and surface-level usage. The scheduled job now alerts on immediate operational failures; this remaining work is the richer operator-facing history and usage view.

## Zopedia Retrieval / Tool Discovery

- [ ] Add bounded BM25 lexical retrieval as a first-class signal for Zopedia memory pages, retained evidence chunks, and agent tool metadata. Keep it hybrid with embeddings and reranking; BM25 should improve exact-term recall, not become a hardcoded routing layer.

- [ ] Add cursor pagination to search-style tools where top-k truncation can hide evidence: `zopedia.search_pages`, `research.search_evidence`, retained source/chunk search, news/source search, and any future tool-search endpoint. Include `has_more`, `next_cursor`, `total_estimate` when available, and ranking/match diagnostics.

- [x] Add a canonical agent tool registry with bounded BM25 search over tool names, descriptions, and schemas; expose it as `tools.search`, and add a deferred-discovery planner mode behind a disabled-by-default config flag.

- [ ] Enrich shared tool metadata with maintained examples, failure modes, empty-result causes, and fallback hints. Run the DeepSeek replay set before enabling deferred discovery in development.

- [ ] Teach the planner to page or search tools only when an evidence-plan slot remains unfilled. The intended loop is: missing slot -> tool search or memory/source search -> call best tool -> paginate or fallback if coverage is partial -> inspect `empty_reason` before stating a limitation.

- [ ] Add evals for retrieval/tool discovery: exact ticker/company/event terms, long natural-language wiki queries, tool lookup for event-window ETF returns, and evidence cases where the needed row appears after the first page.

## Zopedia Deferred UI Shots

- [ ] Take a focused shot at the full Zopedia graph explorer after the backend graph contract is stable. Do not lead with this. Wait until provenance, backlinks, and godnodes/community indexes exist so the graph shows real semantics instead of decorative nodes.

- [ ] Evaluate whether advanced graph modes can be integrated cleanly: community view, page-neighborhood view, evidence-trace view, proposal overlay, bridge/surprising-connection view, and delete queue. If the implementation requires special-case graph state or duplicated KG logic, keep it out of the core Zopedia UI.

- [ ] Revisit a Wiki Behaviour/config panel after core Zopedia works. Keep runtime/budget controls admin-only unless the setting directly helps a normal user complete an action.
