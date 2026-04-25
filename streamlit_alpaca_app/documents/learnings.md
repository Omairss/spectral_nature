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
