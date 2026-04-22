# Learnings

This is a curated list of reusable lessons for this repo. Highest-leverage items come first.

## 1. Keep one source of truth and one shared contract

- Put shared behavior behind one execution core, then let Streamlit, REST, and MCP stay as thin facades.
- Model cross-client contracts directly: datasets, charts, artifacts, auth scopes, messages, and runs should be first-class objects, not UI text that other clients have to parse.
- For renames and navigation changes, update the source constants first and keep aliases for older saved state and links.

## 2. Fix behavior at the source, not in the UI

- If a summary, feed, or dashboard is wrong, fix the upstream data or service path instead of adding presentation-only cleanup.
- Build rollups from the same underlying payloads already used by detail cards so summary and detail cannot drift.
- Snapshot-backed jobs and views need the same symbol and data contract. Keep analysis universes separate from reference baskets when they serve different purposes.

## 3. Persist important product state as first-class data

- If something must appear in the product, persist it as a first-class dataset or event instead of leaving it as narrative side context.
- Usage and security telemetry should come from durable DB-backed events, not container logs.
- Keep provenance, support or conflict checks, and source type visible so debugging does not require reverse-engineering hidden state.

## 4. Prefer config-driven and auditable systems

- Move mappings, thresholds, and graph definitions into versioned config or data files instead of hardcoded Python dictionaries.
- Keep semantic meaning separate from display-only attributes. UI sizing and styling should not leak into core business fields.
- When deterministic logic and retrieval-backed logic both contribute to a feature, update one shared status contract instead of creating parallel channels.

## 5. Design for reliable operations first

- Azure is the live source of truth. Tracked docs are lookup guides, and ignored generated files are only operator convenience.
- If multiple env vars describe the same external dependency, normalize them to one logical setting after discovery.
- For shared external credentials, standardize on one Key Vault and resolve secret names first. Do not mix raw secrets and secret-name indirection in the same runtime contract.
- Paid or slow side effects should be user-triggered or explicitly invoked, and cacheable when possible.

## 6. Choose the simplest UI shape that matches the action model

- For admin CRUD, start from the actions users need per row. Row cards and native widgets are often a better fit than a grid.
- Keep one shared omnibar when routing can stay backend-driven, previewable, and reusable across clients.
- Store external destinations in structured helpers or metadata instead of scattering raw URLs through render branches.

## 7. Keep development and validation focused

- Reproduce issues through the smallest callable unit first, then verify the exact runtime path users hit.
- For login-gated sites, a persistent browser profile plus manual login is usually more reliable than scripted credential entry.
- Prefer generic extraction of visible content and metadata before reaching for brittle page-specific selectors.
- In this repo, explicit environment-aware commands are safer than assumptions about the base interpreter or preinstalled test extras.

## 8. Security hardening needs source-control discipline and fail-closed defaults

- If a secret appears anywhere in a tracked file, assume it is compromised and plan for both rotation and Git-history cleanup.
- Treat notebooks, legacy folders, and docs as first-class secret-scan targets, not as lower-risk scratch space.
- Auth and authorization paths should fail closed when their dependency chain is unavailable or throws, especially on public API surfaces.
- Token-signing keys need their own secret lifecycle. Do not borrow user passwords or bootstrap admin passwords for signing.
- Browser-readable session cookies are a framework workaround, not a strong security design. Prefer `HttpOnly` server-managed sessions whenever the stack allows it.
- When a framework cannot set `HttpOnly` cookies directly, make browser-persistent login opt-in and keep the secure default session-only instead of normalizing the weaker pattern.
- Refresh tokens should not double as bearer access tokens outside an explicit migration window.

## 9. Raw SQL changes need direct store coverage

- When a store function builds a multi-join SQL query, add one narrow test that checks the query shape and the returned payload mapping.
- Service and API tests are not enough when the failing behavior lives inside a raw SQL string.
- For admin data grids and cards, prefer testing the smallest store function that feeds the screen before validating the full UI.

## 10. User drilldowns should be on-demand

- For admin analytics, keep the default overview fast and only fetch the heavier per-user trail when someone actually selects a user.
- Reusing an existing event table is usually the lowest-latency path if the filter can stay on indexed actor and timestamp columns.

## 10. Incident review only works if telemetry exists first

- Activity logs can help with management-plane changes, but they are not enough to prove whether leaked credentials were used on the data plane.
- Secret rotation should be paired with audit and diagnostic coverage in the same hardening pass when possible.
- If a platform service has auditing disabled, document that as a real incident-response gap, not a minor ops TODO.
- If operators need to trust a security baseline day-to-day, surface audit and diagnostic coverage directly in the admin UI instead of leaving it as a CLI-only check.
- Azure management credentials may span multiple subscriptions. For admin health panels, resolve the target subscription from the actual resource group and resource ids instead of taking the first enabled subscription returned by ARM.
- Behavior-critical runtime differences between dev and prod should be explicit env settings in the deploy script and visible in the deployment tracker, not left to code defaults or tribal memory.

## 11. Streamlit UI changes need render checks, not just diffs

- For layout cleanup in this app, `streamlit.testing.v1.AppTest` is fast enough to verify block order and visible copy without needing a browser session.
- For admin-only pages, seed `_ui_user_context` in the app test session state instead of skipping the route verification.
- When the goal is a cleaner screen, verify both the presence of the intended block and the absence of the removed text before promotion.

## 12. Image promotion and runtime config promotion are separate concerns

- For Azure Container Apps, promoting an image digest does not automatically promote newly introduced app env keys.
- If a deploy script manages only a partial env allowlist, new runtime capabilities can work in dev and still fail in prod after promotion.
- For shared runtime services such as the UI LLM client, keep deploy-time env management declarative and include source-app fallback when promoting to a target that may lag config.

## 13. ARM fallback ids must never count as discovered resources

- For Azure control-plane discovery, a synthesized fallback resource id is useful for error reporting but it is not proof that the resource exists.
- If subscription scoring counts fallback ids as real matches, multi-subscription credentials can silently drift to the wrong subscription and produce false health failures.
- When a panel depends on tracked Azure resources, separate `resolved` from `fallback` state and use only the resolved signal for tie-breaks.

## 14. Streamlit tabs are a poor fit for heavyweight admin screens

- `st.tabs(...)` still evaluates every tab body, so it is not a good boundary when each section has large tables, charts, or backend reads.
- For admin tools that should behave like separate pages, `st.segmented_control(...)` with conditional rendering is a better fit.
- Split page-specific filters with the page they control instead of leaving one large mixed filter bar for unrelated analytics views.

## 15. Azure CLI success does not prove app-runtime RBAC

- A local Azure CLI check can confirm that the resources exist, but it does not prove the managed identity inside the running app can read the same management-plane endpoints.
- For Azure-backed admin health panels, verify at least once from inside the live container with the app's actual managed identity.
- Secret-access roles on Key Vault are not enough for observability panels that also read SQL auditing settings, diagnostic settings, and workspace metadata through ARM.

## 15. Loading states should reuse real backend stages

- If a long-running UI flow already emits structured progress events, use those to drive the wait state instead of inventing fake token streaming.
- A short sequence of plain-language status updates is easier to trust than a generic progress bar with internal wording.
- Keep both layers when possible: plain-language status plus the real progress bar gives users direction and pacing.
- For clean screens, temporary loading panels should clear once the final answer renders instead of leaving stale process chrome behind.
- With `streamlit.testing.v1.AppTest`, seed session keys one by one; the testing session state proxy does not support `.update(...)`.

## 16. Precompute expensive homepage media and keep fallback async

- If a homepage asset can be derived deterministically from a scheduled snapshot, generate it in the job and persist it with that snapshot instead of rebuilding it on every page load.
- External media APIs such as TTS should not sit on the synchronous render path for a shared dashboard surface.
- A background fallback is still useful for backfill and recovery, but it should reuse the same payload shape as the precomputed path so the UI does not need separate rendering logic.

## 17. Agent tool access must match the product promise

- If a feature promises current-event analysis, narrative recall, and search-backed answers, those sources must be explicit tools or one shared orchestrator. The model cannot use tools it cannot see.
- For time-sensitive causal prompts, bias the planner toward retained context and fresh evidence instead of letting it jump too easily to a zero-tool answer.
- Reuse the repo's strongest retrieval and evidence stack behind one path instead of keeping a lighter agent route with silently weaker source access.

## 18. Draft-review-commit works better than direct live mutation for graph editing

- For editable knowledge graphs, keep a seeded or committed read snapshot separate from the user draft in session state.
- Let the review layer own adds, deletes, and edits, then commit one normalized delta into the durable store.
- Tombstones are a simple way to let reviewed commits suppress baseline seed content without mutating the seed files themselves.

## 18. Research browsing should degrade cleanly when full browser runtime is absent

- For agentic research, a bounded page-read tool is useful even before full Playwright provisioning is in every deployed container.
- Prefer a layered browser helper: use Playwright when available, then fall back to plain HTTP extraction so the feature stays useful instead of failing closed.
- Keep the planner bias soft. Encourage tools for live analysis prompts, but do not force a fake research loop when retained context is already enough.

## 19. Identity-driven Azure jobs need both deploy-time sync and runtime fallback

- If a job sets `AZURE_CLIENT_ID`, the deploy path must also keep the matching user-assigned identity attached on every update, not only on first create.
- Shared Azure credential helpers should try the configured managed identity first and then fall back to the default attached managed identity in Azure runtime.
- Scheduled jobs should fail when required upstream datasets are missing. A green "skip" for required inputs hides stale data and makes incident detection slower.
- When a deploy script resolves a user-assigned identity by name, also resolve and store its full ARM resource id. Container Apps job create and identity-assign paths need the id, not just the client id or name.

## 20. Knowledge-graph search should return only confident anchors

- A resolver should not force a nearest fuzzy or semantic match when confidence is weak. Returning the wrong anchor is worse than returning none.
- Search open-ended graph nodes over descriptions and structured context, not only ids and short aliases.
- For agentic graph building, use the raw query when there is no confident anchor instead of pretending an unrelated node is close enough.

## 21. Keep homepage-summary enrichment in the shared summary layer

- If the homepage summary needs a richer narrative, put the planning, search, evidence extraction, and synthesis in the shared AQL summary service instead of adding job-only formatting logic.
- Let the materialization job decide between agentic and deterministic paths, then reuse the same audio-attachment step for both.
- When an agentic summary depends on live search, require real evidence and fall back cleanly instead of letting the summary invent a tape-wide story.

## 22. Authenticated research sources need three layers wired together

- A login-capable helper alone is not enough. The runtime also needs secret resolution and browser packaging in the deployed image.
- For paid or gated sources, keep credentials in Key Vault and pass only secret names through deploy-time env.
- If one source should enrich an existing research loop, attach it at the shared page-browsing layer and let downstream claim extraction prefer captured page text over raw search snippets.

## 23. Gated-page success must mean article text, not just HTTP 200

- For anti-bot or paywalled sites, a browser helper should verify the returned content, not just whether navigation succeeded.
- Treat known challenge pages and preview-only markers as failures, then retry or fall back explicitly.
- A stealth browser session with persisted cookies can be meaningfully more reliable than generic Playwright, but it still needs content-level checks before the runtime can claim full access.

## 24. Shared research indexes should extend the existing chunk trace, not fork from it

- If multiple AQL paths already materialize `search_results -> source_documents -> evidence_chunks -> claims`, reuse that contract for new searchable evidence features instead of introducing a second corpus.
- For cost and speed, start with deterministic metadata extraction on the materialized chunk frame before adding embeddings or a vector store.
- Homepage-summary research is still research. If it searches the web and opens pages, that evidence should be merged into the same trace frames as symbol and event research.

## 25. Local pipeline caches need hard bounds and must stay out of git

- `cache/pipeline_store` is a convenience layer, not a durable store. Keep blob storage and dataset manifests as the source of truth.
- A read-through cache in a long-lived container should prune itself after writes; otherwise harmless dataset reads slowly turn into disk pressure.
- If runtime cache artifacts are useful locally but not canonical, ignore them in git and keep only a placeholder `.gitignore` in the directory.

## 26. Agentic research quality depends more on gap detection than on adding one more source

- A system can browse, search, and even read long-form pages such as Seeking Alpha, but still sound flat if it compresses evidence too early and never asks what is still missing.
- First-chunk and first-claim bias is a real quality problem. Rank chunks by relevance and freshness before writing, instead of trusting the first few paragraphs of a page.
- Homepage summaries and event cards need different writers. Reusing feed-style surface summaries in tight homepage bullets makes the output feel clipped and repetitive.
- Generic phrases such as `with no clear catalyst` or `most plausible chain` are useful fallback language, but they should also be treated as quality signals that may justify another research pass.

## 27. Borrow advanced agent patterns selectively and only after the evidence layer is sound

- Coordinator-worker orchestration, scratchpads, live progress summaries, and consolidation gates are useful patterns for AQL + SAA.
- Speculative execution and remote deep-planning are not good first fixes when the real bottleneck is weak retention and weak retrieval.
- Before importing advanced agent architecture ideas, first check whether the system is still throwing away fetched content or writing from low-quality retrieval.

## 28. Retained provider text is often more valuable than the snippet, and chunk ranking matters immediately

- Search adapters should carry richer provider text and raw payloads forward when they are available. Otherwise the system pays for better retrieval but still writes from thin snippets.
- A small ranking layer over chunks improves quality quickly. Replacing first-chunk and `head(6)` bias is a high-leverage fix even before a full lexical + vector retrieval stack exists.
- If a premium or browsed source is worth opening, its retained text budget must be large enough to matter; a very low char cap turns page browsing back into snippet collection.

## 29. Durable document storage should start with stable document ids, not a brand-new retrieval stack

- Once AQL source documents have stable canonical ids and content hashes, later retrieval and cross-run history work becomes much simpler.
- The first durable SAA layer does not need a new search engine on day one. Blob storage plus Postgres metadata is enough to stop raw-document loss and make documents reopenable.
- Store the full raw document in blob, but keep the dataset frame and chunk rows carrying the canonical id so later retrieval layers can join back to the durable source of truth.

## 30. Historical search needs a lightweight query surface before it needs a full retrieval engine

- Once retained documents exist, the next bottleneck is usually access, not embeddings. A simple shared search and document-open contract is enough to unlock a lot of value quickly.
- Keep search results lightweight. Return excerpt, ids, providers, and tags, but keep full raw text behind a direct document-open path.
- A good early retrieval layer can be Postgres filters plus stored search text and simple ranking. That is often enough to stop latest-frame-only debugging and research.

## 31. Historical research needs chunk access, not just document access

- Once documents are reopenable, the next missing layer is usually chunk history, because the system reasons over chunks, not whole documents.
- Persist chunk history at dataset-write time, not as a sidecar export later. That keeps the stored research units aligned with the actual AQL pipeline.
- Keep chunk search lightweight at first: structured SQL filters plus simple ranking is enough to make historical evidence practically usable before a full hybrid retrieval stack exists.

## 32. Vendor bulk endpoints should be optional when the stable per-series path still works

- If a curated dashboard only needs a few dozen important series, keep the stable per-series path available even if a bulk endpoint looks cleaner on paper.
- Put the fetch strategy behind one shared payload contract so `bulk` and `fallback` loaders can swap without touching the UI or pipeline job logic.
- Validate secrets against the exact live endpoint that matters. A key working on `v1` does not prove it will work on `v2` bulk.

## 33. Hybrid retrieval can start by reusing stored chunk embeddings

- If the pipeline already generates chunk embeddings, the cheapest next retrieval upgrade is usually to persist and reuse them rather than standing up a new vector system immediately.
- Separate match signals from rerank signals. Query overlap and semantic similarity should decide whether a row matched; recency and authority should only refine ordering afterward.
- One query embedding plus a bounded candidate pool is a good early hybrid pattern. It improves recall without making every search expensive.

## 34. A macro dashboard needs market pricing and transmission, not just hard data

- Inflation, labor, and housing activity are necessary but not enough for a trading-oriented macro panel.
- Add the market-implied layer early: curve shape, real yields, breakevens, dollar, and bank-credit flow often move before the slower monthly macro series.
- A small curated set is still fine, but it should span three lenses at once: hard data, market pricing, and transmission channels.

## 35. A better retrieval layer does not change the UI unless the writing surface actually uses it

- It is not enough to build hybrid retrieval in SAA and assume the homepage summary will improve automatically. The writing surface has to route through that same evidence path.
- If a summary still only writes from a local search-to-chunk shortcut, the richer retained corpus and semantic scoring can sit unused while the UI looks unchanged.
- When a surface becomes more agentic, expose a small visible trace too. Otherwise it is hard to tell the difference between “no research happened” and “research happened but the UI hid it.”

## 36. Gated-source auth has to fail fast inside user-facing research loops

- A gated-source helper can be technically correct and still be a product bug if it burns minutes before falling back.
- Try the target page first, then authenticate only when the page is clearly gated or redirected to login.
- If the login page itself is blocked by anti-bot protections, surface that quickly and move on instead of stalling the whole summary path.

## 37. Azure semantic retrieval is not real until the embedding deployment exists

- Loading an embedding client object is not enough. You have to verify that the actual Azure embedding deployment is provisioned and callable.
- If Azure does not have an embedding deployment, skip embeddings explicitly instead of silently retrying a chat deployment for embedding requests.
- Keep chat deployment and embedding deployment separate in config and deploy scripts. They are different runtime contracts.

## 38. Derived macro summaries should be rebuilt from the raw observation frame when possible

- If the materialized layer already stores the raw observation history, derived fields such as `latest_date`, `prev_delta`, and `YoY` should be recomputed from that history instead of trusted blindly from a stale summary table.
- This is especially important when vendor metadata contracts drift, because one bad frequency label can make every `YoY` field look missing even though the observations are fine.
- The raw observations should be the source of truth; the summary row should be treated as a cache, not as a second authority.

## 39. Optional shell lookups must not return a fatal status under `set -e`

- In deploy scripts, a helper that means "nothing found" should not return a non-zero status if callers use command substitution under `set -e`.
- For optional config hydration, empty output is enough. Returning `1` can abort the whole deploy even when nothing is actually wrong.
- Treat missing donor env values as a normal branch, not as an error path.
