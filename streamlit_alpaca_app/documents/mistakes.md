# Mistakes — Active Guidelines

Distilled from 41 past incidents (archived in `past_mistakes.md`). These are the rules to follow, not the stories behind them.

---

## Deploy & Operations

1. **Run `scripts/which_deploy.sh` before every deploy.** It tells you which container has changed files. Deploying the wrong one is a no-op that wastes 10 minutes.
2. **Prod promotion = image + runtime env.** When promoting dev to prod, diff env keys between the two apps. A missing env key makes a working feature look broken.
3. **Identity attachment runs on every update, not just create.** `az containerapp job update` does not reattach user-assigned identities automatically.
4. **Optional shell lookups return 0 with empty output.** Under `set -e`, a "not found" exit code kills the entire deploy script.
5. **Dev/prod behavioral differences must be explicit env vars**, visible in the deployment tracker. Never rely on code defaults or tribal memory.
6. **Use the repo deploy script for dev UI changes.** Do not start a localhost Streamlit server as the default validation path in this repo. For UI deploys, run `scripts/which_deploy.sh --check ui`, then `bash scripts/deploy_ui_azure.sh --target dev`; only use localhost when explicitly requested.

## Verification & Testing

6. **Verify the user-facing path, not just the service layer.** A passing unit test does not prove the homepage, the API endpoint, or the materialized view is correct.
7. **After deploy, test the exact affected screen or endpoint.** HTTP 200 on the health check is not enough.
8. **Raw SQL changes need a direct store-level test.** Service and API tests cannot catch a misaliased `FROM` clause.
9. **"Code exists" does not mean "feature is live."** Verify the deployment, the config, and the runtime dependency. Embedding client constructed ≠ embedding deployment provisioned.
10. **Test Azure features from inside the live container**, not from local CLI. Managed identity RBAC and CLI credentials are different.
11. **Use the project `.venv` for local pytest.** System Python can miss repo dependencies such as `networkx`, creating false failures and wasted debugging time.

## Secrets & Security

11. **Pre-commit hook scans for secrets.** Installed at `.git/hooks/pre-commit` via `scripts/scan_secrets.sh`. If it blocks your commit, the secret must be removed — not the hook.
12. **Auth fails closed.** When the auth store, Key Vault, or config is unavailable, deny access. Never fall back to anonymous.
13. **Notebooks, legacy folders, and docs are full secret-scan scope.** No file is "low risk."
14. **Secrets live in Key Vault. Config carries secret names, never values.**

## Data Pipeline

15. **Mandatory datasets fail the job. Optional datasets degrade gracefully.** A green job with stale output is worse than a failed job with a clear error. `_MANDATORY_DATASETS` in `attention_home_build.py` enforces this.
16. **One evidence trace contract for all research paths.** Every workflow that gathers evidence routes into `search_results -> source_documents -> evidence_chunks -> claims`. No parallel corpora.
17. **Raw observations are the source of truth. Summaries are cache.** Rebuild derived fields from the observation frame; do not trust a stale summary table.
18. **Retain full text, not just snippets.** When the fetch layer has richer content, carry it forward. A tight char cap turns page browsing into snippet collection. Do not truncate data by default. If text is genuinely too large and risks performance issues, use a generous limit and explicitly note that truncation occurred.
19. **Durable storage needs a read path immediately.** Storage without access traps value in implementation details.
20. **Chunks are the reasoning unit.** Document search alone is not "historical retrieval complete" when the runtime reasons over chunks.
21. **Match signals separate from rerank signals.** Lexical/semantic overlap decides whether a row matched. Authority and freshness refine ordering after.

## LLM & Narrative

22. **No hardcoded sentence templates for user-facing narrative.** Any string the user reads that contains a data value must be LLM-generated, not string-interpolated. No `f"Oil is {direction}"`.
23. **No if/elif dispatch for narrative variation.** Theme, direction, and intensity differences go into the LLM prompt, not branching code.
24. **LLM readiness is logged at startup.** `check_llm_readiness()` runs once per session. If embeddings show "disabled," `EMBEDDING_DEPLOYMENT` is not set.
25. **Generic phrasing is a quality signal.** `"with no clear catalyst"` or `"most plausible chain"` should trigger another research pass, not be accepted as final text.

## Agent Architecture

26. **Fix retention and retrieval before adding agent choreography.** Fancy orchestration does not rescue weak evidence.
27. **Ship the smallest reliable improvement first.** A bounded helper with graceful fallback beats waiting for full Playwright provisioning.
28. **Agent tools must match the product promise.** If the feature promises search-backed analysis, the tools must include live search. The model cannot use tools it cannot see.
29. **Agent must search retained evidence before going external.** The SAA evidence store often already contains the answer. Chat + Search was unable to find an obvious Avis short squeeze because it only did live web search and anomaly checks, missing 15+ articles already in the retained store. Always wire internal evidence search as a first-class tool.
30. **Live search must preserve the user's original query phrasing.** Intent classification strips domain-specific terms ("short squeeze" → "risk"). Pass the user's raw query to the search query builder so it reaches the web search API intact.
31. **Gated sources fail fast.** Fetch the page first, authenticate only when gated. Blocked login pages are fast-fail, not retry-loop.
30. **Match the ambition level of the request.** When the user asks for something ambitious, build the ambitious version — don't downgrade to the easiest thing that vaguely fits. "Agentic" doesn't mean "automated." "Dynamic" doesn't mean "fixed." The path of least resistance is often the wrong path when the ask is ambitious.
31. **Trading suggestions must fail closed without grounded synthesis.** Do not turn deterministic scanner scores into trade advice when the LLM or evidence path is unavailable. Show the data gap and require a grounded synthesis with invalidation and tail-risk fields.
32. **Do not create parallel research agents when AQL already owns evidence.** Page-level agentic summaries and trading synthesis must enter through the shared AQL / Chat + Search agent path, then adapt the grounded result for the UI. A direct `llm.generate_json(page_data)` helper is a formatting shortcut, not a research agent.

## Azure

32. **Resolve subscription from the resource group, not from list order.** Multi-subscription credentials pick the wrong one silently.
33. **ARM fallback ids are for error reporting, not for scoring.** They must never count as discovered resources.
34. **Keep admin observability config separate from pipeline env** when the monitored resources are in a different resource group.

## UI & Product

33. **Start with the actions, then choose the container.** Per-row actions and state transitions first. Grid/table/card second.
37. **Conversational agents need conversation history.** If the UI is a chat model with follow-ups, the backend agent must receive prior turns. A bare follow-up string like "I mean the recent weeks" is meaningless without the prior exchange. Thread compact conversation context through the full call chain.
34. **Implementation details stay in docs, not on user screens.** If the text does not change what the next click should be, remove it.
35. **Loading states reuse real backend stages.** If the agent already emits progress events, surface them — do not invent a fake progress bar.
36. **Precompute expensive media in the scheduled job.** TTS, image generation, and heavy enrichment should not sit on the render path.
37. **Main features do not run on page load.** Page render should show cached/materialized data and explicit actions. Agentic/AQL work belongs behind a button or in a scheduled materialization job, not in a panel constructor.
38. **Do not fan out per-symbol metadata during table render.** Use materialized universe snapshots or a batch lookup for company names. A table with 80 symbols should not make 80 metadata calls just to fill labels.
39. **Fixed market opportunity feeds are job outputs.** If the page is showing a fixed set of ranked opportunities with no user-authored query, build it in the attention job and expose it through data access. Streamlit render should not call mover/momentum scanners to assemble that feed.
40. **Multi-surface feature slices need one architecture pass.** If a TODO spans feed, summaries, and an agent, verify the existing job, data-access, and AQL patterns for all of them before editing. Do not fix one surface while leaving the same anti-pattern in the others.
41. **Materialized AQL steps need time bounds.** Moving AQL work into a scheduled job is not enough if one search, bundle-writing, verification, or critique step can run for an hour and block persistence. Bound expensive steps and fall back to deterministic summaries with data gaps so mandatory feed outputs can still materialize.
42. **Materialized frame columns need stable types.** Empty strings in numeric columns can pass pandas tests but fail pyarrow during job persistence. Normalize optional numeric fields to numeric/null before returning job frames.
43. **Job images must include every module used by shared paths.** Reusing AQL from a pipeline job means the pipeline image must package AQL's dependencies, including data-access helpers. Local tests can pass while the container falls back if the Dockerfile omits a package directory.
44. **Timeout wrappers must return the same fallback contract as the wrapped code.** A service can correctly convert AQL failures into `fallback`, but an outer job timeout can still materialize `error` rows if it has its own error payload. Scheduled summary timeouts should write usable fallback rows with `data_gaps`, not raw unavailable records.
45. **Do not render trading-agent evidence as raw JSON or clipped metric labels.** Candidate cards need full ticker/company identity, direction, confidence, key numeric context, invalidation, risk, and source trace. Raw expanders and narrow identity metrics make the agent look less grounded than the evidence it already has.
46. **Do not expose composite trading scores without decomposition.** A number like `Opportunity 88` looks arbitrary unless the UI shows the selected horizon, price action, momentum pace, trend quality, and missing fields that shaped the rank.
47. **Do not promote news aggregate fallback text into narrative slots.** Coverage-count lines are useful metadata, but repeating them as both Background and What Happened creates empty-looking context. Filter low-signal aggregate text before rendering narrative sections.
48. **Do not ship baseline company text with daily-news fallback language.** A monthly company-background dataset should not include sentences like "current narrative is still thin"; strip render-path/news-gap language so the baseline stays timeless and useful.
49. **Do not assume raising a display limit lengthens generated audio.** If the stored homepage summary already has a short `audio_text` and embedded TTS bytes, the UI will play that short clip until `attention-home-build` regenerates the snapshot. Remove source prompt constraints and rerun the job.
50. **Do not show raw provider policy errors in user-facing agent panels.** If an LLM provider rejects a generated prompt, translate it into an app-level message and log the raw detail elsewhere. Dumping `invalid_prompt` JSON makes the feature look broken and gives the user no fix path.
51. **Do not surface raw transport exceptions as agent output.** Errors like `RemoteDisconnected` usually mean a source closed an HTTP connection, not that the user's request is invalid. Retry once and show a concise "research source connection dropped" message if it repeats.
52. **Do not patch only the product-specific wrapper for shared agent failures.** The Trading Agent and Omnibar Research Agent both use shared AQL/Chat + Search paths. Transport cleanup has to live in the shared agent boundary and the UI error boundary, or another surface will still leak `RemoteDisconnected`.
53. **Do not swap an LLM provider globally when the request is agent-specific.** Global `LLM_PROVIDER` drives summaries, KG, AQL, embeddings, and admin readiness. Use scoped env prefixes for experiments like DeepSeek reasoning traces so one agent can change without destabilizing unrelated surfaces.
54. **Do not put Azure CLI on the request path.** A deployed app container may not include `az`, and installing it would still be the wrong fix for a page button. Runtime actions need SDK/ARM calls through the app identity, while precomputed summaries remain owned by scheduled jobs.
55. **Do not let deterministic routing block submitted chat requests.** The router can find symbols, releases, bundles, and confidence bands, but it should only enrich the agent request. If it classifies a message as search/ambiguous before the agent runs, one-word follow-ups like "yes" and broad questions both feel broken.
56. **Do not let LLM calls run unbounded inside an interactive agent.** The UI can keep emitting heartbeats while the request is still effectively stuck. Bound planner and final synthesis calls separately so a multi-tool query can return a degraded but useful answer instead of spinning forever.
56. **Do not scatter job trigger controls across feature pages.** Normal users should not see operational refresh buttons. Centralize job starts in Admin > Pipeline Jobs so feature pages stay read-only over materialized state.
57. **Trading action logs must be broker-aware even before execution is enabled.** A log-only Place decision should still record execution mode, broker, order payload, and broker order id fields so the later Alpaca API handoff does not require changing the audit contract.
58. **Do not call a multi-horizon materialized job complete until every required horizon has a usable row.** A job can succeed overall while one AQL horizon times out and leaves the UI empty for that horizon. Required horizons need either grounded candidates or explicit low-confidence fallback candidates with data gaps.
59. **Do not spend bootstrap slots on low-yield search before required entity context.** A multi-ticker comparison that calls retained search first can exhaust its deterministic budget and miss one of the requested tickers. Seed all requested ticker contexts first, then add live or retained evidence.
60. **Do not feed rich materialized datasets to the LLM as generic objects.** If the summarizer only sees keys like `homepage_summary` and `top_events`, it may say current data is missing even though the payload contains the answer. Add dataset-specific context extraction at the shared tool-summary boundary.
61. **Do not leave live-search tools or prefetch fallbacks indistinguishable from hangs.** A status line like "Checking live event evidence" without elapsed-time heartbeats or a per-step timeout makes a slow vendor call look stuck. Hidden prefetch LLM calls can create the same failure before visible tools even start. Tool execution and preparation fallbacks need the same bounded, observable treatment as LLM planning.

## Search Pipeline

37. **Hardcoded intent classifiers destroy domain-specific terms.** A 5-category enum ("oil/rates/defensives/risk/generic") mapped "short squeeze" to "risk" and then searched for SPY/QQQ. The LLM never saw the user's actual query. Classifiers must preserve the user's original phrasing through the search pipeline.
38. **Relevance gates scored against the classified theme, not the user's query.** When the classifier was wrong, the gate rejected every relevant article. Always check match against the user's original query terms as a bypass.
39. **Corrupted timestamps crash psycopg silently.** A year-48113 value in `published_at` crashed `cur.fetchall()` inside a `try/finally` (no `except`), making the function return empty. Cast timestamps to text in SELECTs when the table can contain corrupted data.
40. **Three-stage truncation (2600→1600→1600 chars) starves the LLM of evidence.** Each truncation point was independently "reasonable" but the cascade lost 60-80% of the evidence. Treat the full pipeline's truncation budget as one budget.
41. **Full narrative KG lookup creates false graph matches.** Long attention summaries fed directly into `search_knowledge_graph_nodes` matched unrelated nodes through generic context words. AQL needs a subject extractor and stopword/domain gates before KG traversal.

## Caching & Vendor

42. **Local cache is disposable. Blob + manifests are durable.** Cache caps and pruning belong in the shared cache helper, not in cleanup scripts.
43. **Vendor bulk endpoints are optional** when the stable per-series path still works. One broken auth key should not look like a full data outage.

## Social Interaction

44. **Do not attach comments to mutable UI output.** Homepage summaries, stock summaries, and Trading Agent cards can be regenerated, reordered, or held only in session state. Persist a stable content anchor first, then attach reactions, comments, and share artifacts to that anchor.
45. **Password reset links must not inherit browser sessions.** Auth-action query params such as `reset_token` and `invite_token` are public account-management forms, not workspace navigation. Clear local auth state and block cookie restore before rendering them, or a reset link can appear to log a user into the app.
46. **Session restore must re-check account and membership state.** A session that was valid when issued can become invalid when a user is disabled or loses portfolio membership. Every restore/API-token validation path must check active user status and active portfolio membership, not just session expiry.
