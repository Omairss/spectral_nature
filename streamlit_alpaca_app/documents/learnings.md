# Learnings — Active Guidelines

Distilled from 40 past learnings (archived in `past_learnings.md`). These are principles to apply going forward.

---

## Architecture

1. **One source of truth, one shared contract.** Shared behavior lives behind one execution core. Streamlit, REST, and MCP are thin facades. Datasets, charts, artifacts, and auth scopes are first-class objects — not UI text other clients parse.
2. **Fix at the source, not in the UI.** If a summary or dashboard is wrong, fix the upstream data or service path. Build rollups from the same payloads used by detail views so they cannot drift.
3. **Persist important product state as first-class data.** If something must appear in the product, it needs a dataset or event — not a narrative side effect. Usage telemetry comes from durable DB events, not container logs.
4. **Config-driven over hardcoded.** Mappings, thresholds, and graph definitions go in versioned config or data files. When deterministic and retrieval-backed logic both contribute, update one shared status contract.

## Operations

5. **Azure runtime is the source of truth.** Local generated files are fallback only. Normalize equivalent env vars to one setting. Standardize on one Key Vault.
6. **Image promotion and env promotion are separate.** A promoted image digest does not carry new env keys. Deploy scripts must manage both.
7. **Identity-driven jobs need deploy-time sync and runtime fallback.** Attach the managed identity on every update. Fall back to the default attached identity when the configured client id is stale.
8. **Scheduled jobs fail on missing mandatory inputs.** A green skip for a required dataset hides stale data and delays incident detection.

## Security

9. **Source-control discipline and fail-closed defaults.** Any secret in a tracked file is compromised. Notebooks and legacy folders are full scan scope. Auth paths fail closed when dependencies are unavailable.
10. **Token-signing keys have their own lifecycle.** Do not borrow user passwords for signing. Browser-readable cookies are a workaround, not a design.
11. **Telemetry before credentials.** Enable auditing and diagnostics before or alongside any rollout that introduces real credentials. Missing audit trails are security defects.

## Testing & Validation

12. **Smallest callable unit first, then the full user path.** Reproduce issues at the store/service level, then verify the exact runtime path the user hits — including materialized and cached paths.
13. **Raw SQL gets a direct store test.** Service tests are not enough when the bug is in a SQL string.
14. **Streamlit changes need render checks.** `AppTest` verifies block order and visible text. Seed `_ui_user_context` for admin pages. Check presence and absence.
15. **Azure CLI success does not prove app-runtime RBAC.** Verify at least once from inside the live container with the app's actual managed identity.

## Evidence & Retrieval (AQL / SAA)

16. **One shared evidence trace.** All research paths route into `search_results -> source_documents -> evidence_chunks -> claims`. No parallel corpora.
17. **Retain the richest text available.** Prefer page text > provider text > snippet. When a source is worth opening, its text budget must be large enough to matter.
18. **Storage, then access, then retrieval.** First: stable document ids and durable blobs. Second: a shared search and document-open contract. Third: chunk history. Fourth: hybrid retrieval. Do not skip steps.
19. **Chunks are the reasoning unit.** Persist chunk history at write time. Keep the searchable unit aligned with what the pipeline actually reasons over.
20. **Match signals separate from rerank signals.** Lexical and semantic overlap determine match. Authority and freshness determine rank order.
21. **Vendor bulk endpoints are optional.** Keep the stable per-series path available. One shared payload shape behind both strategies.

## Agent Quality

22. **Retrieval before choreography.** Fix retention and retrieval before adding multi-agent orchestration. Advanced patterns do not rescue weak evidence.
23. **Agent tools must match the product promise.** If the feature promises search-backed analysis, the tools must include the actual research sources. The model cannot use tools it cannot see.
24. **Internal evidence first, external search second.** The SAA retained evidence store is a high-value, low-latency source. Agent prompts should direct the planner to search retained evidence before calling live web search APIs. This avoids missing evidence the system already has and reduces latency.
25. **Preserve the user's query phrasing through the search pipeline.** Intent classification is useful for routing but strips domain-specific terms. The original query text must reach the search API to avoid losing key phrases like "short squeeze" that the classifier maps to generic categories.
24. **Gap detection over source count.** A system can browse and search broadly but still sound flat if it compresses evidence too early and never asks what is still missing.
25. **Different surfaces need different writers.** Homepage summaries, narrative cards, and audio need different density and evidence presentation. Do not reuse one compact format for all.
26. **Generic phrasing triggers another research pass**, or at minimum a clearer "unresolved" explanation. Accept `"no clear catalyst"` only when the system has actually searched and found nothing.
27. **Consolidation should preserve shims while moving ownership.** When retiring legacy Attention reasoning paths, first move the source function into AQL/compute/Attention-owned signal modules, then keep thin compatibility imports until pipeline jobs, presentation loaders, and tests are retargeted.
28. **Single-shot LLM summaries hallucinate specifics; ground them with a critique+judge layer.** When the synthesizer LLM only sees text beats (no numerics, no source claims), it will invent percent moves and contradict its own catalyst names. The fix is two extra personas after synthesis: a critique agent with a focused tool subset (price lookup, evidence search, recent news) that flags numeric/contradiction/unsupported issues, and a judge LLM that rewrites only the flagged passages. Both must be best-effort with fall-through to the original summary so the pipeline stays robust. Pattern lives in `services/aql/critique.py`, wired into `build_attention_agentic_summary_with_trace`.

## LLM Integration

27. **All user-facing narrative is LLM-generated.** No `f"Oil is {direction}"`. No if/elif dispatch for narrative variation. Structured data goes into the LLM prompt; natural language comes out.
28. **LLM readiness is verified at startup.** `check_llm_readiness()` logs provider, model, deployment, and embedding status. "Embedding client exists" does not mean "embedding deployment is provisioned."
29. **Research browsing degrades cleanly.** Use Playwright when available, fall back to HTTP extraction. Keep planner bias soft — encourage tools, do not force fake research loops.
30. **Gated-source auth fails fast.** Fetch the target page first, authenticate only when gated. A slow fallback is still a broken product path.

## UI & Product

31. **Actions before containers.** Start with what the user needs to do per row, then pick the UI component. Backend mutations before UI polish.
32. **No implementation details on user screens.** Architecture explanations go in docs. If the text does not change the next click, remove it.
33. **Loading states reuse real backend stages.** Surface the agent's actual reasoning and tool calls — do not invent a generic progress bar. Keep the trace visible after completion.
34. **Precompute expensive media in the job.** TTS and heavy enrichment run in the scheduled job and persist with the snapshot. On-demand fallback is recovery, not the primary path.
35. **Tabs evaluate all bodies.** For heavyweight admin screens, use `st.segmented_control` with conditional rendering instead of `st.tabs`.

## Caching

36. **Local cache is disposable.** Blob storage + manifests are the durable layer. Cache caps and pruning live in the shared cache helper.
37. **Derived summaries rebuild from raw data on read.** Treat stored summary tables as performance convenience, not independent authority. Check whether a broken derived field can be recomputed before debugging the UI.

## Macro Dashboards

38. **Three lenses: hard data, market pricing, transmission.** Inflation, labor, and housing activity alone are not enough. Add curve shape, real yields, breakevens, dollar, and bank-credit flow early.

## Documentation

39. **Move stable architecture out of task plans.** Durable design docs belong under `documents/architecture/` with a local README. Leave short-lived fixes and recovery notes in `documents/plans/`.
40. **Use plain domain terms.** Prefer "text", "narrative", "market activity", and "market overview". Avoid legacy market-media jargon in product/domain code and docs; technical dataframe duplication APIs are still fine.
41. **Knowledge graph is a prior, not evidence.** AQL can use the reviewed graph to find entities, paths, hypotheses, and graph change proposals, but final causal narrative still needs current evidence and claim support. Keep graph persistence owned by `services.knowledge_graph`; let AQL write through reviewable proposals and graph-service commit APIs, not ad hoc SQL or silent narrative-time mutation.
42. **Resolve graph subjects before graph search.** Do not pass full AQL narratives directly into KG node search. Extract symbols, aliases, and domain terms first; otherwise generic words like "company", "demand", "and", or "on" can create high-confidence false graph matches.
43. **Entity extraction must be independent and non-blocking by default.** Broad taxonomy and universe lookups can touch materialized storage. Keep shared entity extraction on feed metadata, KG aliases, claim entities, and optional LLM output unless a caller explicitly opts into heavier lookup. Do not make entity extraction an AQL-owned subsystem.
44. **Knowledge-graph exploration should be entity-first.** UI graph builders should extract and link entities before running graph search or expansion. Raw text search is useful fallback for short manual seeds, but linked entities make add/remove review safer and easier to audit.
45. **Large graphs need an overview before search.** Exploration pages should show a capped, ranked subgraph on first load. Empty screens make users guess what the graph contains; full graphs become unreadable.
46. **Disconnected graph components need their own layout space.** A single force-directed simulation can overlap independent subgraphs because no edges separate them. Lay out components independently, then pack them with padding before rendering.
47. **Graph visuals must show graph semantics.** If the stored model has directed edges, confidence, and severity, the visualization must encode those fields directly. Otherwise users are judging a simplified drawing, not the graph they are being asked to validate.
48. **Attention jobs should propose KG changes, not apply them.** Scheduled runs can extract entities and causal edges from fresh evidence, but core graph writes need review until proposal precision is measured. Materialize proposal rows first, then let the graph review UI commit approved deltas.
49. **Page summaries need one shared contract.** Market, stock, macro, and trading surfaces should pass structured page facts into one LLM-backed summary helper, then render the returned headline, watch items, gaps, and confidence. This keeps summaries consistent and lets downstream agents consume them without scraping UI text.
50. **Shared contracts still need shared evidence.** A common page-summary schema is not enough; agentic summary surfaces should reuse AQL / Chat + Search for retained evidence, live evidence, ticker tools, and verification, then adapt that grounded output into the schema.
51. **Render paths stay cheap.** Main research features should render existing state first and make expensive AQL/LLM work explicit through a button or a scheduled job. Caching reduces repeated cost, but it does not make first-load agent runs acceptable.
52. **Batch labels from materialized snapshots.** Company names and display metadata should come from universe snapshots where possible. Per-symbol fallback is acceptable for a small selection, not for a full market table.
53. **Fixed market feeds materialize beside their summaries.** The Market Opportunity feed is derived from scheduled mover and momentum snapshots, so the attention job should persist the ranked feed and the UI should only select the requested focus/horizon from that materialized table.
54. **Materialized summary contexts must match UI contexts.** If a page summary is resolved by context signature, the job and UI must build the same compact context. Do not include job-only source row counts that the page no longer loads.
55. **Use compact guardrail references for repeated failure zones.** When mistakes/learnings get large, create a short reference file under `documents/reference/` that names the relevant IDs and the required architecture. Read that file before touching the same feature family again.
56. **Job-owned agentic work still needs bounded steps.** Scheduled materialization keeps page load fast, but it also needs per-step timeouts around external AQL/search/LLM paths, including downstream bundle writing and verification. Otherwise the job can hang before writing the cached outputs the UI depends on.
57. **Persisted job frames need explicit dtypes.** Review/proposal frames that mix optional text defaults with numeric values can fail only at parquet upload time. Coerce numeric review fields before handing frames to the pipeline store.
58. **Shared AQL in jobs is also a packaging contract.** If a scheduled job reuses AQL/Chat + Search modules, the job image must copy the same dependency folders the UI/API image has, not only `services` and `pipeline`.
59. **Fallback contracts need end-to-end tests at the outer timeout layer.** It is not enough to test that the summary helper falls back when AQL raises. Also test that the scheduled job timeout wrapper writes the same `fallback` schema, because that is the row the UI actually reads.
60. **Ticker identity is not a metric.** In dense trading or research cards, render ticker plus company name as the row identity and use metrics only for numeric values. Narrow metric widgets clip symbols and hide the context needed to judge a setup.
61. **Trading signals need a visual audit trail.** A composite score should show its visible components, selected horizon, trend quality, and missing data. Otherwise users cannot tell whether the setup is driven by a real trend, a one-day move, or an unexplained internal rank.
62. **News-count summaries are metadata, not context.** Sentences like "6 recent articles over 4 days; tone is mixed" can explain coverage volume, but they should not fill Background or What Happened slots. User-facing context sections need substantive company or catalyst text, or a clear unavailable state.
63. **Slow company context belongs in its own dataset.** Company background changes slowly and can be prefetched monthly from universe/taxonomy/Wikipedia. Daily attention jobs should update catalysts and headlines, then merge the slow baseline only when the narrative text is missing or generic.
64. **Public reference APIs need polite batch defaults.** Even low-volume scheduled jobs should identify themselves and include a configurable inter-request delay. This keeps small tests and future larger runs from accidentally behaving like a scraper.
65. **Company names need source-aware cleanup.** Exchange/universe names often include share-class and legal suffix text that does not map to reference pages. Try the official display name first, then a cleaned legal-suffix candidate before falling back to generic company text.
66. **Prebuilt audio is tied to generated text.** Homepage audio length is controlled by the materialized `audio_text` from `attention-home-build`, not only the render-time display limit. Regenerate the job output after prompt changes, and store a text hash beside embedded TTS bytes so stale audio can be detected.
67. **Research agents should avoid execution-like market wording.** Prompts that say "trade candidates", "short/avoid", or "execution" can be rejected before the model sees the safety qualifiers. Use neutral watchlist and downside-risk language in LLM prompts, then map back to UI labels after generation.
68. **AQL sidecar calls need transient retries.** Interactive surfaces that call AQL tools inherit flaky web/search/source connections. Retry dropped connections once at the feature boundary and translate repeated transport failures into a human-readable data gap.
69. **Transport sanitization belongs at shared agent boundaries.** Fixing one feature wrapper is not enough when the same Chat + Search agent powers multiple surfaces. The shared agent and UI shell should both translate dropped HTTP/model connections before raw provider exceptions reach the page.
70. **Reasoning-provider swaps need adapter seams.** Providers that expose reasoning traces may not share OpenAI/Azure strict JSON schema semantics. Add a provider adapter, capture the provider's reasoning field as trace metadata, and route it per agent before changing the app-wide LLM runtime.
71. **Runtime refresh triggers should use runtime credentials, not deployment tools.** Streamlit containers should not need Azure CLI to start a precompute job. Use managed identity plus Azure Resource Manager for in-app job triggers, with CLI only as a local developer fallback.
72. **Routers enrich chat requests; they do not decide whether the agent runs.** In chat-style research UI, deterministic routing should add direct matches and context metadata, then hand the request to the agent. Bare replies like "yes" also need to resolve against the prior assistant turn before enrichment so the agent receives an actionable continuation.
73. **Every model step in an interactive agent needs its own timeout.** Tool timeouts are not enough. A planner or final synthesis call can hang after useful evidence has already been collected, so bound each LLM step and degrade to synthesis or evidence-state fallback.
73. **Social state needs stable content anchors.** Reactions, comments, and share artifacts should attach to materialized run IDs, context signatures, tickers, bundle IDs, or content digests. Do not anchor durable interaction state to rendered markdown, card order, or `st.session_state`.
74. **Operational job controls belong in admin.** Feature pages should read materialized datasets and expose product actions only. Job starts, retries, and refresh controls live in Admin > Pipeline Jobs.
75. **Log-only trade actions should keep execution fields.** Even before Alpaca order placement is enabled, persist execution mode, broker, broker order id, and intended order payload so the future broker transition is an extension of the audit record.
76. **Required agentic horizons need fallback candidates, not only fallback status.** If AQL times out in a scheduled Trading Agent build, use the materialized opportunity feed to persist conservative low-confidence candidates and record the AQL gap. The UI should have something reviewable for every required horizon while still showing the evidence limitation.
77. **Interactive agents need deterministic evidence seeding.** For common query shapes, collect the obvious first evidence before asking an LLM planner: ticker context for ticker questions, all ticker contexts before comparisons, live event evidence for current catalyst questions, and the materialized homepage summary for broad "what matters" prompts. The planner should refine evidence, not spend latency discovering the first step.
78. **Generic tool summaries can hide the real answer.** Rich dataset payloads like `attention_home_1d` need a purpose-built LLM context extractor. Passing only object keys and run IDs makes the synthesizer claim missing current data even when the materialized narrative is present.
79. **Planning and synthesis can use different model latency profiles.** A reasoning model can be useful for tool choice and trace capture, but final synthesis after deterministic evidence seeding is mostly compression. Keep a scoped synthesis-model override so Chat + Search can reduce latency without changing the app-wide LLM runtime.
80. **Interactive agent tools and hidden preparation steps need heartbeats and timeouts.** A visible planner heartbeat is not enough if the current step is inside a slow live-search tool or a prefetch keyword extractor. Bound each nontrivial external/LLM step, emit elapsed-time progress, and synthesize from collected evidence when a step times out.
81. **Auth-action URLs are separate from app routing.** Reset and invite tokens should force a logged-out auth screen, skip public-home shortcuts, and never restore cookies before the form is shown. Completing a reset should not create a session; users must authenticate with the new password.
82. **Session validity is dynamic.** Restoring a browser/API session must re-check `users.status`, active portfolio status, and active membership. Authenticated context is not just a non-expired session row.
