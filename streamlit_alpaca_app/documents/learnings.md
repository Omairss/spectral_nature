# Learnings

## 2026-04-07

## agentic API/auth gateway takeaways
- Treat REST and MCP transport as two facades over one execution core (`QueryService`) so auth, scopes, and behavior remain consistent.
- Keep machine credentials as first-class DB entities (`agent_api_keys`) with hashed secrets, scoped permissions, and revoke/expiry lifecycle; do not overload user session tokens for long-lived agents.
- Access/refresh token split gives better operational control: short-lived bearer for request auth, long-lived revocable refresh/session for continuity.

## macro release visibility in attention home
- If macro releases must appear on the homepage, release objects must be first-class events, not only evidence attached to symbol bundles.
- Promotion and non-suppression rules should be enforced in `_build_home_payload(...)` so slot competition cannot silently hide qualifying macro releases.
- Persisting a dedicated `macro_release_events_1d` frame keeps diagnostics and replay analysis possible without parsing `top_events` JSON blobs.

## runtime macro profile + diagnostics
- Keep macro release/component mapping in a versioned profile file (`config/attention_macro_signal_profile.v1.yaml`) and merge with safe defaults at load time; this allows partial overrides without brittle startup failures.
- Relationship checks should run before narrative synthesis and write a first-class dataset (`macro_relationship_checks_1d`) so support/conflict states are replayable and auditable.
- Hypothesis rows (`attention_hypotheses_1d`) should be built from deterministic check outputs first; retrieval-backed verification can be layered on top without changing the status contract.

## agent resource contract takeaways
- For cross-client agent UX, REST resources should model sessions, messages, runs, tool calls, artifacts, and notes explicitly instead of forcing clients to reconstruct state from MCP logs or plain chat text.
- Keep artifact payloads separate from transcript text so iPhone and Streamlit can render charts/tables/code without fragile parsing.
- Async run + pollable event stream is the safest first delivery shape; it fits both homepage and mobile without requiring streaming as a prerequisite.

## Spectral Nature 2 omnibar planning
- One shared text bar is cleaner than separate search and chat controls, but only if intent routing is backend-driven and reusable by both Streamlit and iPhone.
- Keep omnibar resolution non-mutating (`/v1/omnibar/resolve`) so clients can preview search/navigation versus agent actions before creating session state.
- Strong exact matches should stay fast-path navigation; agent runs should be reserved for prompts that actually need multi-step tool use.

## homepage editorial links
- Keep homepage external destinations in structured homepage metadata/helpers instead of embedding raw URLs directly inside render branches.
- A compact top-of-home CTA strip is a lower-risk way to add editorial destinations than threading one-off links through multiple cards or rails.

## 2026-04-06

## market explorer split takeaways
- Keeping market-wide scanners and ticker-deep-dive workflows in separate sections improves navigation clarity without changing data contracts.
- A shared session-state helper for ticker propagation (`_set_workspace_ticker`) is more reliable than repeating per-section key assignments.
- For section splits, preserving existing drilldown defaults while adding explicit handoff CTAs reduces migration risk.
- After consolidation, remove old sections from navigation and delete dead render branches in the same pass to avoid UI drift.

## attention scoring documentation parity
- Historical planning docs can drift from runtime scoring logic; explicitly label historical guidance versus current implementation formulas.
- For scoring questions, treat `compute/anomalies.py` as source of truth and backfill docs with exact equations/components, not shorthand prose.
- Keep architecture plans and plan index in sync so new integration tracks are discoverable across sessions.

## iOS MVP scaffold takeaways
- A thin FastAPI adapter over `QueryService` provides a stable contract for mobile without moving domain logic.
- Keeping auth optional-by-environment (required only when database auth is enabled) keeps local/dev flows simple while preserving secure environments.
- XcodeGen-based scaffolding is the most reliable cross-session approach from this workspace because generated `.xcodeproj` files are machine-specific churn.

## implementation guidance
- Start iOS with JSON-first payload rendering to validate endpoint contracts quickly, then layer typed view models.
- Keep base URL in xcconfig + Info.plist key so environment switching does not require source edits.

## homepage agent workspace planning
- Reuse `data_access/query_service.py` and `api/main.py` as the agent's structured-data boundary; do not route agent behavior through `app.py`.
- When adding new agent capabilities, define the API/resource contract first so the iPhone app can reuse it directly.
- Treat charts, datasets, anomalies, and run traces as tool calls, not RAG content.
- Use RAG only for unstructured evidence and documents with citation metadata.
- If users want "thoughts", expose explicit notes/scratchpad state instead of raw chain-of-thought.
- Keep sandbox execution as a separate bounded service with dev-first rollout.

## 2026-04-05

## iPhone strategy takeaways
- The cleanest migration seam is already present: `data_access/query_service.py` + `data_access/contracts.py`.
- `compute/`, `services/`, and most of `data_access/` are Streamlit-independent, so backend API extraction can happen without rewriting core analytics logic.
- Mobile reliability improves if default reads stay materialized-first and provenance is surfaced to clients for debugging.

## process reminder
- Keep repo discovery scoped and avoid broad unbounded `rg` calls; start from known architecture docs and key boundary modules.
- Invite/reset email URL drift is an environment-config issue first: verify `APP_PUBLIC_BASE_URL` on the target Container App before changing auth/email code.
- For branded transactional emails, prefer CID inline attachments over externally hosted or `data:` URI images to improve client compatibility.
- For admin-editable templates, keep one render path for both preview and actual send; store only sanitized theme data in DB settings.
- For template-library migrations, update service, UI, and tests in one pass so preview/send call contracts cannot drift.
- Keep built-in immutable templates (dark/white) as safety anchors; allow customization via cloned templates instead of deleting defaults.
- For uploaded chart support, persist validated base64 payload + MIME metadata and enforce a strict size cap to keep invite send reliability predictable.
- For invite lifecycle controls, prefer status transitions (`pending` -> `revoked`) over hard deletes to retain auditability while giving admins operational control.
- For table-based admin actions, prefer `st.dataframe(..., on_select=\"rerun\", selection_mode=\"single-row\")` over separate selector widgets so action context stays in the table view.
- For Container Apps custom-domain promotion in the same environment, host ownership must be moved (`hostname delete` on dev, then `add/bind` on prod); DNS target cleanup can be done after cutover if both generated FQDNs resolve to the same ingress IP.

## 2026-04-03

## What burned resources
- I ran one overly broad recursive search (`rg` across the full repo with permissive patterns), which produced very large output and wasted tokens/time.
- I initially searched the wrong paths before resolving the real workspace location, causing an avoidable extra loop.
- I used short-interval polling repeatedly during deployment; this increased command churn without adding much signal.

## What worked better
- Reproducing with direct resolver calls (`DataAccessLayer().resolve_attention_ticker_background("BMY")`) gave fast, deterministic truth before touching deploy.
- Fixing at source (merge logic + stale cache detector) solved the issue without adding noisy UI-specific hacks.
- Adding targeted regression tests prevented rework and made follow-up deploy confidence higher.
- Treating critical source failures as job-failing conditions (instead of warning-only logs) prevents silent partial-success runs and stale datasets.

## Process changes going forward
- Resolve exact file roots first (`find`/targeted `rg`) before any broad search.
- Default to scoped grep commands (specific files/dirs); avoid repo-wide wildcard scans unless strictly necessary.
- For long-running deploy/build commands, poll less frequently (10-30s) and only request high-detail logs when state changes.
- Validate fixes via focused local function checks and tests before deployment.
- Keep deploy scope explicit: dev only unless prod permission is explicitly provided.
- For multi-source jobs, explicitly encode which steps are mandatory and fail the run if mandatory sources do not persist.
- Keep Key Vault parity between pipeline and UI environments for all search/LLM secrets; missing secrets in one vault can silently force fallback text and make feature work appear broken.
- Avoid hardcoded narrative fallback sentences in background copy; prefer real company context and sanitize listing instrument suffixes (`Common Stock`, `Class A`, etc.).
- When materialized payloads can retain legacy text, add runtime guards in resolver/overlay logic so UI quality improves immediately without waiting for full snapshot rebuilds.

## Practical checklist for similar incidents
1. Reproduce issue through smallest callable unit (resolver/service), not full UI first.
2. Confirm cache vs source-of-truth mismatch explicitly.
3. Patch source logic and add one regression test for the exact failing shape.
4. Run narrow tests + one direct payload sanity check.
5. Deploy to dev once, verify revision/image/health, then stop.

## 2026-04-07

- For macro integration, persisting the causal graph edges as a first-class dataset (`macro_causal_graph_edges_v1`) makes relationship checks auditable and reusable across homepage/API/mobile without duplicating profile parsing logic.
- Hypothesis verification is more reliable when web retrieval updates the same persisted hypothesis rows instead of creating parallel status channels; keep one status contract and blend deterministic + retrieval evidence.
- Adding macro provenance directly to `resolve_attention_feed` details removes the need for UI-specific diagnostics logic and keeps clients aligned on why macro events were promoted/suppressed.
- Optional macro scoring should ship with explicit shadow/live fields (`attention_score_v2_shadow`, `attention_score_v2`) while leaving `attention_score` unchanged by default to prevent accidental ranking drift.
