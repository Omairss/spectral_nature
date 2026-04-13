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
