# Learnings

## 2026-04-06

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
