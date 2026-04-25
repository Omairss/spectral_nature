# Mistakes Log

This is a curated list of repeated failure modes for this repo. Highest-risk items come first.

## 1. Verified the wrong end state

What went wrong:

- I treated unit or service checks as enough without verifying the exact UI or API path the user actually hits.
- I validated forced-refresh or on-demand paths but missed the default materialized path.
- I stopped after a deploy looked healthy without testing the affected screen or workflow end to end.

Never repeat:

1. Reproduce the issue through the smallest callable unit first.
2. Verify the exact user-facing path after that, including default materialized or cached paths.
3. Add at least one regression test on the final payload fields or contract the UI consumes.
4. After deploy, test the exact affected screen or endpoint, not just service health.

## 2. Let secrets and infra state drift

What went wrong:

- I allowed tracked `.env` files or build context uploads to carry real secrets.
- I trusted stale local env aliases or generated files instead of live Azure state.
- I verified one Key Vault path and assumed the running app was using the same secrets.

Never repeat:

1. Keep `.env` and `.env.*` ignored, while keeping `.env.example` tracked.
2. Mirror secret-file exclusions in build context rules such as `.dockerignore`.
3. Treat Azure runtime config as source of truth and local generated files as fallback only.
4. Normalize equivalent Key Vault env vars to one canonical value after discovery.
5. Confirm secrets in the exact vault and app environment the running workload uses.

## 3. Allowed stale or partial data to look successful

What went wrong:

- I treated "snapshot exists" as equivalent to "requested slice exists."
- I let warning-only source failures pass in composite jobs where the missing source was actually mandatory.
- I allowed empty or wrong-universe materialized data to block better live fallback behavior.

Never repeat:

1. Distinguish between a dataset existing and the requested slice being usable.
2. Fail composite jobs when mandatory sources fail.
3. Keep UI read paths and upstream jobs on the same symbol and data contract.
4. Surface provenance clearly enough to tell whether a result came from live, cached, or materialized data.
5. Add orchestration-level regression tests when multiple helpers or source paths interact.

## 4. Changed public contracts only halfway

What went wrong:

- I changed API, service, or UI contracts in one layer without updating the other layers in the same pass.
- I relied on helpers that existed only in the dirty local workspace and not in the clean deploy source.
- I assumed temporary test environments already included the dependencies the changed contract needed.

Never repeat:

1. Treat contract changes as one atomic change set across service, UI, tests, and docs.
2. Compare deploy-target files against `HEAD` when using a clean worktree, not against the dirty local version.
3. Smoke-test new route signatures and changed call sites immediately after editing them.
4. Make helper usage self-contained when patching mixed files for isolated deploys.
5. Confirm temporary test environments include real runtime test dependencies.

## 5. Let docs or runtime rules drift from the code

What went wrong:

- I relied on planning docs that no longer matched runtime formulas or defaults.
- I left important mappings and thresholds hardcoded long after the feature needed tuning.
- I allowed must-show product behavior to stay implicit instead of encoding it in persisted data and tested orchestration rules.

Never repeat:

1. Treat the runtime code path as source of truth when explaining current behavior.
2. Move tunable mappings and thresholds into versioned config before the feature expands.
3. Encode must-show behavior in one tested orchestration point, not in narrative side effects.
4. Update plan indexes and canonical docs when implementation shape changes.

## 6. Used overly broad or assumption-heavy tooling

What went wrong:

- I ran broad repo searches before confirming the right root path or narrowing the scope.
- I used short-interval polling or high-churn commands when slower polling would have been enough.
- I assumed commands like `python` or base-interpreter pytest environments would exist and match repo needs.

Never repeat:

1. Resolve the exact workspace root first.
2. Start with targeted `rg` searches on known files or directories before scanning broadly.
3. Exclude heavy artifacts on first-pass searches.
4. Poll long-running deploy and build commands less often and only ask for detail when state changes.
5. Prefer explicit repo-safe commands such as `python3` and `uv run` over environment assumptions.

## 7. Chose the container before the action model

What went wrong:

- I treated an admin CRUD problem as a table or grid problem before deciding what actions the user needed.

Never repeat:

1. Start with the per-row actions and state transitions the screen needs.
2. Prefer native row-based controls before adding a grid dependency.
3. Add the backend mutation path before polishing the UI shell around it.

## 8. Allowed tracked secrets and fail-open auth paths

What went wrong:

- Real credentials were allowed to live in tracked legacy code and notebooks.
- The API auth path fell back to anonymous access when auth readiness failed instead of failing closed.
- Browser session restoration relied on a JavaScript-written cookie that cannot be `HttpOnly`.

Never repeat:

1. Treat tracked notebooks and legacy folders as full secret-scan scope.
2. Never commit a real secret, even for old code, one-off scripts, or local test notebooks.
3. Auth checks must fail closed on backend errors, missing stores, or partial configuration.
4. Do not reuse dashboard passwords as token-signing material.
5. If the framework forces browser-readable cookies, document the risk and plan the replacement instead of treating it as fully acceptable.
6. Keep insecure migration paths behind explicit env flags and make the default posture the safer one.

## 9. Shipped a SQL rewrite without a store-level regression test

What went wrong:

- I expanded `auth_store.list_users()` to include credential and session stats.
- The rewritten query referenced `u.*` but the `FROM` clause no longer aliased `users` as `u`.
- There was no direct `auth_store` test covering the SQL string or the merged payload path.

Never repeat:

1. When rewriting SQL in store code, add a direct store-level regression test for the exact query path.
2. If a query uses table aliases in joins or ordering, verify the alias is declared in the `FROM` clause before deploy.
3. For admin surfaces backed by raw SQL, test the store function directly instead of relying only on service and API coverage.

## 10. Left incident-response visibility weaker than the secrets footprint

What went wrong:

- Real secrets were allowed into tracked files before full SQL and Key Vault diagnostics were in place.
- That meant the breach review could confirm management-plane events but could not confidently rule out data-plane misuse.

Never repeat:

1. Enable service auditing and diagnostics before or alongside any rollout that introduces real credentials.
2. When a secret leak is found, document the telemetry gap as part of the remediation, not as a separate cleanup task.
3. Treat missing audit trails as a security defect with operational impact, not just an infrastructure nicety.
4. When a security control is enabled by CLI, expose its steady-state status in the admin surface so drift is visible without another manual audit.

## 11. Assumed Azure control-plane discovery was single-subscription

What went wrong:

- I initially resolved the admin security panel subscription by taking the first enabled subscription visible to the Azure credential.
- The credential had access to multiple subscriptions, so the panel queried the right resource group name in the wrong subscription and showed false 404-based failures.
- I also let the panel inherit the generic pipeline resource-group env, even though the audited SQL and Key Vault resources live outside that pipeline RG.

Never repeat:

1. For Azure health or security panels, resolve the subscription from the configured resource group and tracked resources, not from subscription list order.
2. Keep admin observability config separate from generic pipeline env defaults when the monitored resources are different.
3. Validate a new control-plane status reader against the live resource ids before trusting the UI summary.

## 12. Let a prod-facing auth behavior depend on an implicit code default

What went wrong:

- Browser-persistence behavior changed in code, but the production UI app did not have an explicit env setting for that behavior.
- The deployment tracker also did not surface the actual UI resource group or the auth-persistence mode, which slowed down root-cause confirmation.

Never repeat:

1. Put behavior-critical prod expectations behind explicit deploy-time env vars.
2. Surface those env-backed behaviors in the deployment tracker when they materially affect user experience or security posture.
3. When prod and dev intentionally differ, encode that in the deploy script instead of relying on remembered manual toggles.

## 12. Left internal implementation text in the end-user workspace

What went wrong:

- I left resolver and mode-explanation text directly in `Chat + Search`, even though it described implementation details instead of helping the user complete a task.
- That made the screen noisier and pushed real controls further down without adding decision value.

Never repeat:

1. Keep architecture explanations in docs, comments, or admin surfaces unless the user explicitly needs them on-screen.
2. For workflow pages, remove any text that does not change what the next click should be.
3. Before promotion, verify the rendered page for both visual order and text relevance, not just functional correctness.

## 13. Promoted a prod image without promoting new runtime env

What went wrong:

- I promoted the dev UI image to prod, but the UI deploy script did not carry the LLM runtime env keys used by Chat + Search.
- Dev already had those env values, so the feature looked healthy there while prod fell back to `LLM runtime is unavailable`.
- I also allowed `.env.example` to omit the UI LLM runtime section, which made the intended deploy config less explicit.

Never repeat:

1. When a feature depends on app env, update the deploy script in the same change that introduces or relies on that config.
2. Treat prod promotion as both image promotion and runtime-config reconciliation.
3. Add env documentation for any shared runtime dependency the UI needs in cloud deployments.

## 14. Counted fallback Azure ids as if they were real resource matches

What went wrong:

- I added fallback ARM ids so the admin security panel could still emit readable error paths, but I also let those fallback ids count as successful resource discovery.
- In a multi-subscription credential, that made two subscriptions look equally valid even though only one actually contained the tracked SQL server and Key Vault.
- The resolver then picked the first subscription in the ARM list and the UI showed false cloud-audit failures.

Never repeat:

1. Keep `resolved` and `fallback` resource states separate in any Azure discovery helper.
2. Use fallback ids only for follow-up error reporting, never for subscription or resource scoring.
3. Verify subscription selection with a deliberately wrong resource-group hint before trusting a new ARM resolver.

## 15. Treated local Azure checks as equivalent to app-runtime permissions

What went wrong:

- I verified the cloud-audit reader with my local Azure CLI credential and assumed the app runtime would behave the same way.
- The running UI container was using a managed identity that could read secrets but could not read SQL auditing settings or diagnostic settings through ARM.
- That left the admin panel broken in live environments even though the same code looked healthy from my local machine.

Never repeat:

1. When a feature depends on Azure ARM, test it once from inside the live container with the actual managed identity.
2. Separate secret-read roles from management-plane read roles in the diagnosis. They solve different problems.
3. If a live Azure panel shows `AuthorizationFailed`, inspect the identity's RBAC before spending more time on source-code fixes.

## 15. Left a long-running agent flow with no readable wait state

What went wrong:

- I cleaned up Chat + Search but left the wait experience as a generic progress bar, so the user still had to sit through a long agent run without meaningful feedback.
- The agent was already emitting useful stages, but I did not convert that stream into user-facing language.
- I also lost time on the first render test by assuming the AppTest session state proxy supported `.update(...)`.

Never repeat:

1. When an interaction can take several seconds, ship a visible wait state in the same cleanup pass.
2. Reuse existing progress events before adding new loading infrastructure.
3. For Streamlit AppTest, assign seeded session-state keys individually instead of calling `.update(...)`.
4. When restyling a wait state, keep the actual progress indicator wired in instead of downgrading it to text-only status updates.

## 16. Left external TTS generation on the normal homepage render path

What went wrong:

- I initially generated homepage narration during page render, which coupled the user's wait time to ElevenLabs latency.
- That also meant the same summary audio could be regenerated repeatedly even when the underlying home snapshot had not changed.
- The first async fallback pass also kept failed futures cached in memory instead of clearing them once the error state was recorded.

Never repeat:

1. If a summary is derived from a scheduled snapshot, compute and persist it in the job with the snapshot.
2. Keep any on-demand fallback non-blocking and treat it as recovery, not the primary path.
3. When background work fails, clear the cached future as part of error handling so retries and memory behavior stay predictable.

## 17. Let a research prompt finalize with zero evidence

What went wrong:

- `Chat + Search` routed a live analysis prompt into agent mode, but the omnibar planner was still allowed to return `final` before any tool call.
- The omnibar tool surface exposed only shared dataset and chart tools, not the full research sources users expected such as live web evidence, RAG fallback, or wiki-like corpora.
- Stronger retrieval code already existed elsewhere in the repo, but it was not wired into the omnibar path.

Never repeat:

1. For time-sensitive `why`, `impact`, or `how this plays out` prompts, add a strong evidence-seeking preference so tool use becomes the default behavior when the right sources are available.
2. Expose the actual product research sources as explicit tools or one shared research orchestrator.
3. Reuse the repo's strongest retrieval and evidence pipeline instead of letting a second lighter agent path silently answer from less context.

## 18. Confused browser depth with browser availability

What went wrong:

- I initially framed Playwright browsing as if it needed to be fully provisioned before the omnibar could benefit from page reads at all.
- That would have delayed the source-level fix even though a bounded helper with graceful fallback was enough to improve research behavior immediately.
- I also needed to keep the original product constraint in view: tool use and citations should be encouraged, not required.

Never repeat:

1. Ship the smallest reliable research improvement first, then harden the runtime later.
2. Treat Playwright as a preferred deep-read layer, not a blocker for basic page extraction.
3. When the product wants soft bias instead of hard requirements, implement that policy in the planner prompt and answer contract instead of in rigid gates.

## 19. Let deployment env and attached job identity drift apart

What went wrong:

- I updated pipeline job env to point at a different managed identity client id, but the deployment update path did not reattach that identity on existing Azure Container App jobs.
- The shared Azure credential path also assumed the configured managed identity would always be valid for the running container.
- The attention job then treated missing required inputs as a skip, which hid the real failure behind a green job run and a stale homepage.

Never repeat:

1. Treat identity attachment as part of the normal update path, not just the create path.
2. In Azure runtime, prefer a managed-identity chain that can fall back to the attached default identity when one configured client id is stale.
3. If a scheduled job cannot load required source datasets, fail the run instead of skipping and leaving stale materialized outputs behind.

## 20. Let weak resolver scores pretend they were real graph anchors

What went wrong:

- The knowledge-graph resolver allowed low-quality fuzzy matches from short aliases and generic string similarity.
- That made unrelated nodes look like valid anchors for open-ended queries such as `fertilizer`.
- The UI then presented those weak matches without enough execution-state feedback, which made the system look more hardcoded and less trustworthy than it was.

Never repeat:

1. Gate resolver output by confidence and return no anchor when the score is weak.
2. Search rich node context such as descriptions and structured attributes before relying on generic string similarity.
3. For long-running graph builders, expose the actual backend stages so users can tell whether the system is scanning the graph, researching, or calling the LLM.

## 21. Treated optional browser code as if it meant deployed browser support

What went wrong:

- The repo already had Playwright-based helpers, but the shared app and pipeline images did not install Playwright or Chromium.
- That made it too easy to overestimate what the deployed runtime could really do against gated sources such as Seeking Alpha.
- Browser-authenticated research needs packaging, secret lookup, and runtime env wiring together.

Never repeat:

1. When adding a browser-backed feature to shared services, check the Dockerfiles and runtime dependencies before assuming the code path is live.
2. Keep secret values in Key Vault and pass only secret names through deploy config.
3. For authenticated page enrichment, wire the source at the shared browsing layer so every caller gets the same fallback behavior.

## 22. Counted a loaded page as if it were full gated-content access

What went wrong:

- I initially treated “browser opened the page” as equivalent to “the runtime can access the article.”
- Seeking Alpha was still returning an anti-bot page or preview shell in that path, so the helper looked healthy while the actual content was still blocked.
- That would have let the summary loop enrich results with weak or wrong page text.

Never repeat:

1. For gated sources, verify the returned text contains real article signals and does not contain known challenge or preview markers.
2. Run at least one live probe through the exact shared helper before claiming the runtime path works.
3. Keep anti-bot handling, login, and content validation in the same source helper so callers do not each invent their own success rules.

## 23. Let homepage-summary research live outside the shared evidence trace

What went wrong:

- The agentic homepage summary had its own search loop, but its requests, results, source documents, chunks, and claims were not being persisted into the main AQL trace datasets.
- That meant a meaningful slice of external evidence was invisible to later search, debugging, and agent retrieval even though the system had already paid to collect it.
- It also pushed the codebase toward parallel evidence paths instead of one shared corpus.

Never repeat:

1. If a workflow gathers external evidence, route it into the shared `search_results -> source_documents -> evidence_chunks -> claims` contract.
2. Keep derived summaries lean, but persist the underlying trace separately so humans and agents can inspect it later.
3. Before adding a new retrieval surface, check whether the real gap is missing metadata and missing persistence on the existing materialized chunk store.

## 24. Let runtime cache artifacts behave like source files

What went wrong:

- `cache/pipeline_store` artifacts were allowed to accumulate in the repo and in local runtime storage as if they were durable project assets.
- That created noisy git state and let local cache growth depend on how many different datasets a container happened to touch.
- The cache had no shared size guardrail, so there was nothing stopping slow disk creep in long-lived containers.

Never repeat:

1. Ignore runtime cache artifacts in git unless there is a very explicit reason to version them.
2. Put cache caps and pruning in the shared cache helper, not in one-off cleanup scripts.
3. Treat blob/object storage plus manifests as the durable layer; local cache should always be disposable.

## 25. Treated agentic collection as if it automatically produced agentic writing

What went wrong:

- I improved search, page access, Seeking Alpha reading, and evidence indexing, but the final homepage and event-card writers still compressed that evidence too aggressively.
- The summary flow still relies on a single short-paragraph hypothesis prompt, first-chunk bias, and a small top-claim slice.
- The event-card path still allows canned theme fallbacks to stand in for actual gap-driven follow-up research.

Why that was a mistake:

- It made the system look weaker than the underlying retrieval stack really is.
- Users saw generic lines such as `with no clear catalyst` or `most plausible chain` and reasonably concluded that the agent was not thinking hard enough.
- The system was doing more work under the hood than the final text made visible.

Never repeat:

1. When upgrading research access, also check whether the writer is still losing too much source context before the final synthesis step.
2. Treat generic final phrasing as a quality failure that should trigger either another research pass or a clearer unresolved explanation.
3. Do not reuse one compact summary format across homepage summaries, narrative cards, and audio when those surfaces need different density and evidence presentation.

## 26. Reached for advanced agent tricks before locking down evidence retention and retrieval

What went wrong:

- It was tempting to focus on multi-agent orchestration, speculation, and deeper planning patterns before the evidence layer was actually trustworthy.
- That would have added complexity on top of a system that was still clipping documents, overusing snippets, and retrieving the first few chunks instead of the best evidence.
- Fancy orchestration does not rescue weak storage and retrieval fundamentals.

Never repeat:

1. Fix retention and retrieval before adding more agent choreography.
2. Treat source-of-truth evidence quality as the prerequisite for any deeper agent behavior.
3. Borrow advanced architecture patterns selectively, based on the current bottleneck rather than what looks most impressive.

## 27. Let the fetch layer stop at snippets even when richer text was already available

What went wrong:

- Search providers and page readers were sometimes returning richer text or raw payloads, but the pipeline normalized those results down to short snippets before document building.
- The homepage-summary path then chunked and ranked that thinner representation, which made the later writer look weaker than the upstream retrieval really was.
- A tight character cap on Seeking Alpha enrichment compounded the problem by making opened pages behave like oversized snippets.

Never repeat:

1. Carry provider raw payloads and richer provider text forward when the fetch layer has them.
2. Prefer page text, then provider text, then snippet fallback when building canonical source documents.
3. When a retrieval path depends on long-form content, raise or justify the text budget explicitly instead of inheriting a convenience cap.

## 28. Kept source documents only inside the latest materialized frame

What went wrong:

- Even after improving retention inside the current run, raw source documents still only lived inside the latest parquet frame.
- That meant reopening a document later depended on the latest dataset cache and made cross-run history harder than it needed to be.
- The system had more document value than the storage model reflected.

Never repeat:

1. Give retained documents a stable canonical id and content hash as early as possible.
2. Store the raw document durably in blob storage before treating the parquet frame as the only home for the content.
3. Keep a lightweight metadata table in Postgres so later retrieval does not need to rediscover where a document lives.

## 29. Stopped at durable storage without exposing a shared read path

What went wrong:

- A durable raw-document layer helps, but by itself it still leaves humans and agents without a clean historical query surface.
- That pushes debugging and research back toward latest-frame inspection even after the real source of truth has improved.
- Storage without access keeps too much of the value trapped in implementation details.

Never repeat:

1. After adding durable storage, add a shared read contract immediately.
2. Expose both search and direct open paths, because one without the other is awkward for real research use.
3. Keep the first read path simple and reliable before adding heavier retrieval machinery.

## 30. Improved historical research at the document layer but still left the reasoning units behind

What went wrong:

- Reopenable documents are useful, but AQL does not actually reason over whole documents. It reasons over evidence chunks.
- That meant the first historical SAA query layer still left a gap between what humans could reopen and what the runtime actually used to write.
- Historical debugging could still fall back to latest-frame chunk inspection, which weakened the value of the new retained corpus.

Never repeat:

1. After durable documents, move immediately to durable chunk history.
2. Keep the searchable unit aligned with the reasoning unit whenever possible.
3. Do not treat document search alone as “historical retrieval complete” when downstream logic still depends on chunk-level evidence.

## 31. Mixed retrieval-match logic with rerank logic

What went wrong:

- The early chunk search score mixed true match signals with recency and authority boosts.
- That made semantic-only matches harder to detect because almost every recent authoritative row looked like at least a weak lexical hit.
- The result was a blur between “this row matched the query” and “this row is worth ranking higher once it matched.”

Never repeat:

1. Keep lexical and semantic match signals separate from rerank bonuses.
2. Use authority and freshness to order results, not to decide whether a result matched.
3. When adding hybrid retrieval, explicitly track where each hit came from: lexical, semantic, or both.

## 32. Trusted a vendor bulk endpoint as the only refresh path

What went wrong:

- The curated FRED loader depended on the v2 bulk release endpoint even though the stable v1 per-series endpoints still had current data.
- That made one vendor auth mismatch look like a full data outage.
- There was no automatic fallback, so the pipeline either failed or kept serving stale materialized data.

Never repeat:

1. Keep a stable per-series fallback when a vendor bulk endpoint is new, stricter, or less battle-tested.
2. Test credentials against the exact endpoint the runtime uses, not only adjacent endpoints from the same provider.
3. Build one shared payload shape first, then let multiple fetch strategies feed it.

## 33. Stopped the first FRED curation pass too early

What went wrong:

- The initial curated dashboard had enough hard-data series to look reasonable, but it still missed key market-pricing and transmission signals.
- That left the panel better at describing the economy than at spotting regime shifts the way a macro PM would.
- Housing activity without home prices, inflation without breakevens, and policy rates without curve or real-rate context were all avoidable gaps.

Never repeat:

1. For any macro dashboard, explicitly check hard data, market pricing, and transmission channels before calling the first pass complete.
2. Add the minimum useful rates layer early: front end, long end, curve, real yield, and breakevens.
3. Make sure housing and consumer coverage include both activity and balance-sheet or price context.

## 34. Assumed the homepage summary was using SAA just because SAA existed

What went wrong:

- The repo had better retention, chunk history, and hybrid retrieval work in SAA, but the homepage summary still used its own local search-to-chunk path.
- That meant the deployed UI changed only a little even after the underlying retrieval system improved.
- The homepage summary also hid its research trace, so it was easy to miss that the writing surface and the retrieval surface were still disconnected.

Never repeat:

1. After building a new shared retrieval layer, explicitly trace which product surfaces consume it and which still bypass it.
2. Verify a live run by inspecting retained documents, chunk embeddings, and the actual materialized summary payload, not only by checking that the deploy succeeded.
3. When a surface becomes agentic, add a small visible trace so users can see the queries, sources, and evidence it actually used.

## 35. Let Seeking Alpha auth retries sit inside the homepage critical path

What went wrong:

- The homepage summary path could spend minutes inside Seeking Alpha auth attempts after the rest of the research work was already done.
- That made the UI look stale even when the new summary code was deployed, because the write landed too late.
- The runtime also tried to log in before proving the target page actually needed auth.

Never repeat:

1. For gated sources, fetch the target page first and authenticate only when the response is clearly gated or redirected to login.
2. Treat blocked login pages as fast-fail conditions, not something to keep retrying inside a user-facing summary path.
3. Measure the product effect, not just the helper behavior. A slow fallback is still a broken UI path.

## 37. **[HIGH PRIORITY]** Hardcoded user-facing narrative with templates and if/elif dispatch

What went wrong:

- Homepage section headers ("Top Events", "Key Movers", "Unresolved Large Moves") were hardcoded string literals in `app.py` and `summarizer.py`.
- Attention card narrative was assembled from fixed template strings with if/elif dispatch by theme: `"Oil is {direction}, which points to supply-risk pressure across market activity."`, `"{symbol} rose/fell {intensity} today relative to its recent baseline."`, etc.
- Intensity words ("modestly", "meaningfully", "sharply"), causal phrasing ("which points to", "which signals"), and narrative structure were all hardcoded.
- Even after replacing keyword-based classification with LLM calls, the output strings feeding the UI were still templates.
- The result looked mechanical and low-trust to users even though the underlying data was real.

Why this is a structural mistake:

- Templates produce text that sounds like templates. Users notice immediately.
- if/elif theme dispatch cannot handle novel situations — it silently falls through to generic fallbacks.
- Every new theme, direction, or asset class requires a code change instead of being handled naturally.
- It decouples narrative quality from data quality: good signals still produce canned sentences.

Never repeat:

1. **No hardcoded sentence templates for user-facing narrative.** Any string the user reads that contains a data value must be LLM-generated, not assembled by string interpolation.
2. **No if/elif dispatch for narrative variation.** Theme, direction, intensity, and tone differences must be expressed as LLM prompt context, not as branching code.
3. **Section headers and labels must come from the data or the LLM**, not from hardcoded strings, unless they are purely structural UI chrome (e.g. a button label).
4. **Test narrative quality, not just text presence.** A test that asserts `"oil" in text` is not enough — verify the sentence reads naturally for the given inputs.
5. When auditing a feature, check the final user-visible string, not just the service layer. If the final string has `{variable}` shape, it is a template and must be replaced.

## 36. Assumed Azure embeddings were active because the code constructed an embedding client

What went wrong:

- The runtime built an Azure embedding client but still pointed embedding requests at the chat deployment.
- That meant semantic retrieval never actually turned on, even though the code path looked wired.
- Without checking the live Azure deployments, it was easy to mistake “embedding code exists” for “semantic retrieval is live.”

Never repeat:

1. Verify the exact Azure embedding deployment name before calling embeddings in production code.
2. Keep chat and embedding deployments as separate config values.
3. If the embedding deployment is not configured, disable embeddings explicitly instead of letting silent request failures pile up.

## 38. Trusted a materialized macro summary even though the raw observation frame was already available

What went wrong:

- The Broad Economy page loaded `fred_summary` as if it were the source of truth, even though `fred_observations` was already present beside it.
- That let one metadata drift issue break every `YoY` field and left stale summary dates visible even when the repair logic could have been derived from the observation frame.
- The UI ended up reflecting the quirks of a cached summary table instead of the more reliable raw time series.

Never repeat:

1. When both summary and raw observations are stored, rebuild the summary from observations on read unless there is a strong reason not to.
2. Treat stored summary tables as a performance convenience, not as an independent source of truth.
3. Check whether a broken derived field can be recomputed from existing raw data before debugging the UI layer.

## 39. Let an optional deploy-script lookup fail the whole rollout under `set -e`

What went wrong:

- `deploy_pipeline_azure.sh` used a helper to look up optional donor env values from existing jobs.
- That helper returned `1` when nothing was found, which is reasonable in isolation but fatal inside command substitution under `set -e`.
- The result was an unnecessary deploy failure before any real validation or rollout work happened.

Never repeat:

1. If a helper represents an optional lookup, make "not found" return success with empty output.
2. Audit shell helpers for `set -e` behavior, not just logical correctness.
3. Fix deploy reliability issues at source immediately when they block validation, because they will recur on every future rollout.

## 40. Deployed the wrong container for a pipeline-only change

What went wrong:

- The signal extraction layer lives entirely in the pipeline job (`attention_home_build.py` and `services/aql/summarizer.py`), not the UI.
- I ran `deploy_ui_azure.sh` instead of `deploy_pipeline_azure.sh`, which had no effect on the feature.
- I then described the UI as "reading" the signals, when in reality the summarizer runs inside the pipeline job too.

Never repeat:

1. Before deploying, identify which container the changed code runs in (UI vs pipeline job).
2. If the change is in `pipeline/jobs/` or services called only from pipeline jobs, deploy the pipeline container.
3. The UI container only matters for code that runs at request time in the Streamlit app.

## 41. Pre-existing test failure: `test_bottom_up_attention_artifacts_uses_runtime_macro_profile_for_release_mapping`

What went wrong:

- `build_bottom_up_attention_artifacts` test expects a non-empty `release_frame` when a macro signal profile is provided.
- The test writes a YAML file but the loader encounters a `JSONDecodeError` parsing it, suggesting a format mismatch between the writer and reader.
- The release frame comes back empty because the macro profile never loaded.

Never repeat:

1. When adding a structured config file reader, verify round-trip compatibility with the test fixture format.
2. Keep test fixtures as close to the real runtime format as possible.
