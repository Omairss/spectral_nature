# Security Review - 2026-04-11

Follow-up remediation log:

- `SECURITY_REMEDIATION_2026-04-11.md`

## Scope

Reviewed the local Git repo at `spectral_nature` with focus on:

- tracked plaintext secrets
- auth and session handling
- local repo layout and nested repos
- tracked operational metadata
- outbound hotlinks and external-content trust boundaries

Review method:

- targeted file review of auth, secrets, deploy, cache, and docs paths
- repo-wide pattern search for secrets, plaintext passwords, tokens, URLs, and dangerous auth fallbacks
- Git history spot checks for confirmed leaked values

## Repo inventory

- One Git repo only. No nested `.git` repos were found under the workspace.
- No tracked `.env` files, private keys, cert bundles, or local database files were found in the repo tree.
- The main active app is `streamlit_alpaca_app/`.
- A legacy `src/` tree and tracked notebooks still contain security-relevant material and must be treated as in-scope because they are committed.

## Severity summary

- Critical: 1
- High: 2
- Medium: 3
- Low: 2

## Findings

### Critical - Real database credentials are committed in source and notebooks

Evidence:

- `src/fred/db_connect.py:9-15`
- `src/fred/db_connect.py:25-31`
- `notebooks/db_test.ipynb:55-63`

What is exposed:

- Azure SQL hostname
- database name
- username
- plaintext password `monkey-loft-998644`

Why this matters:

- Anyone with repo access can try the credential directly.
- Because the secret is committed, it must be treated as already compromised.
- The value is also in Git history, not just the working tree.

Git history evidence:

- `git log --all -S 'monkey-loft-998644'` returned commits `0eb3998` and `3e48a37`

Required action:

1. Rotate the database password immediately.
2. Replace all plaintext uses with Key Vault or ignored local env files.
3. Rewrite Git history for the leaked credential, then invalidate old clones if possible.

### High - FRED API key is hardcoded in multiple committed files

Evidence:

- `src/fred/source.py:117-123`
- `src/fred/get_series_id.py:54-56`
- `src/fred/releases.py:61-63`
- `src/fred/get_series_id_data.py:54-56`

What is exposed:

- plaintext FRED API key `46ae2b0f7c69c4fa5b6f3f4710a107dc`

Why this matters:

- It is a real secret embedded in tracked source.
- The key is duplicated across several files, which increases cleanup risk.
- The value is also present in Git history.

Git history evidence:

- `git log --all -S '46ae2b0f7c69c4fa5b6f3f4710a107dc'` returned commits `3e48a37`, `4cd96b5`, and `73f6030`

Required action:

1. Rotate the FRED key.
2. Remove the value from all tracked files.
3. Rewrite Git history for the leaked key.

### High - API auth fails open when database auth is unavailable or errors

Evidence:

- `streamlit_alpaca_app/api/main.py:21-25`
- `streamlit_alpaca_app/api/main.py:95-96`
- `streamlit_alpaca_app/services/auth_service.py:991-1003`
- `streamlit_alpaca_app/services/api_auth.py:28-38`

Why this matters:

- If `database_auth_enabled()` returns false, or raises, the API treats auth as disabled.
- In that state `_resolve_principal(...)` returns an anonymous principal instead of rejecting the request.
- That anonymous principal inherits `DEFAULT_USER_SCOPES`, which include dataset read, chart read, query execute, omnibar resolve, MCP invoke, and agent run scopes.

Risk:

- A bad auth-store deploy, broken DB connection, or unexpected exception can turn the API into a public read or invoke surface.
- Legacy-auth-only mode also leaves the separate API surface effectively unauthenticated.

Required action:

1. Make auth resolution fail closed on backend errors.
2. Return no scopes for anonymous callers unless a route is explicitly public.
3. Gate API startup or health on auth backend readiness for protected environments.

### Medium - Access-token signing falls back to dashboard passwords

Evidence:

- `streamlit_alpaca_app/services/api_auth.py:110-126`

Why this matters:

- Access tokens are signed with `API_ACCESS_TOKEN_SECRET` when configured, but the fallback uses `DASHBOARD_AUTH_BOOTSTRAP_ADMIN_PASSWORD` or `DASHBOARD_AUTH_PASSWORD`.
- This mixes password material and token-signing material.
- A password leak now becomes a token-forging risk.

Required action:

1. Require a dedicated signing secret in protected environments.
2. Remove password fallback entirely.
3. Keep password rotation and token-signing rotation independent.

### Medium - Raw session tokens are stored in JavaScript-set cookies without `HttpOnly`

Evidence:

- `streamlit_alpaca_app/app.py:1546-1587`
- `streamlit_alpaca_app/app.py:1631-1650`

Why this matters:

- The browser cookie is written through injected JavaScript, so it cannot be `HttpOnly`.
- The raw session token is then read back and used to restore authenticated sessions.
- Any XSS, malicious extension, or other browser-side script with page access can read the token.

Notes:

- `Secure` is added only when the page is already on HTTPS.
- `SameSite=Lax` helps somewhat, but it does not protect against in-page script access.

Required action:

1. Move session handling to a server-managed `HttpOnly` cookie path or trusted reverse-proxy auth layer.
2. If Streamlit blocks that, treat the current model as a known risk and narrow where it is used.

### Medium - Legacy tools still encourage plaintext secret handling

Evidence:

- `src/CurrentStatus.py:597-603`
- `src/TechnicalAnalyzer.py:319-324`
- `src/OptionFinder.py:448-452`
- `src/MarketExplorer.py:407-410`
- `src/MarketExplorerDataCruncher.py:406-409`
- `src/LinkedAuth.py:6-21`

Why this matters:

- Several legacy entrypoints require `--password` on the command line.
- Command-line passwords leak into shell history and process listings.
- `LinkedAuth.get_creds_old_v1(...)` reads plaintext credential JSON from a local `secrets/` path.

Required action:

1. Remove or quarantine these scripts from active use.
2. Replace password CLI args with secure prompts or Key Vault lookup only.
3. Delete the plaintext-local-secret helper once migration is complete.

### Low - Tracked docs expose live infra names, URLs, revisions, and secret names

Evidence:

- `streamlit_alpaca_app/documents/infra/UI_DEPLOYMENT_STATUS.md:7-10`
- `streamlit_alpaca_app/documents/infra/RESOURCE_INDEX.md:33-46`
- `streamlit_alpaca_app/documents/infra/RESOURCE_INDEX.md:70-94`

Why this matters:

- These files do not expose secret values.
- They do expose container app names, public URLs, registry image digests, Key Vault names, database server names, and secret-name inventory.
- That reduces attacker recon effort and gives a cleaner map of the environment.

Required action:

1. Keep only what operators truly need in tracked docs.
2. Prefer ignored generated inventory for live environment details.
3. Avoid publishing exact public endpoints and revision IDs unless there is a strong operational reason.

### Low - Tracked research cache artifacts and UI hotlinks trust third-party URLs directly

Evidence:

- `streamlit_alpaca_app/app.py:3096`
- `streamlit_alpaca_app/app.py:3195`
- `streamlit_alpaca_app/app.py:4642`
- `streamlit_alpaca_app/app.py:4787`
- `streamlit_alpaca_app/app.py:5001`
- `streamlit_alpaca_app/app.py:5023`
- tracked cache files under:
  - `streamlit_alpaca_app/cache/pipeline_store/attention_search_results/.../frame.pkl`
  - `streamlit_alpaca_app/cache/pipeline_store/attention_source_documents/.../frame.pkl`

Why this matters:

- The repo tracks third-party search-result URLs and article text snippets.
- The UI renders outbound links from those datasets without an allowlist or reputation check.
- This is mainly a trust and content-governance issue, not a direct code-execution flaw.

Notes:

- Most outbound links are HTTPS.
- Local-only HTTP values were found in expected dev paths such as `.env.example` and iOS debug config, not in production-facing runtime code.

Required action:

1. Decide whether tracked research cache artifacts belong in Git at all.
2. Add a lightweight allowlist or URL validation layer before rendering outbound links.
3. Keep third-party content in generated or ignored storage when possible.

## Plaintext exposure inventory

Actual secrets found in tracked files:

- Azure SQL password in `src/fred/db_connect.py`
- Azure SQL password in `notebooks/db_test.ipynb`
- FRED API key in `src/fred/source.py`
- FRED API key in `src/fred/get_series_id.py`
- FRED API key in `src/fred/releases.py`
- FRED API key in `src/fred/get_series_id_data.py`

Sensitive but non-secret metadata found in tracked files:

- live public UI URLs and revision IDs
- Azure Key Vault name
- Azure Postgres server name
- secret-name inventory
- container registry image digests

Items not found in tracked files during this review:

- tracked `.env` values
- tracked PEM or private-key material
- tracked certificate bundles
- nested Git repos

## Remediation order

1. Rotate the exposed Azure SQL password.
2. Rotate the exposed FRED API key.
3. Remove all real secrets from tracked files and notebooks.
4. Rewrite Git history for the leaked values.
5. Make API auth fail closed.
6. Require a dedicated `API_ACCESS_TOKEN_SECRET`.
7. Replace or narrow the browser-readable session-cookie pattern.
8. Remove or quarantine legacy plaintext-secret workflows in `src/`.
9. Reduce tracked infra metadata and tracked research-cache artifacts.

## Review notes

- The most serious issues are in committed legacy files and notebooks, but they still matter because Git exposure is exposure.
- The current `streamlit_alpaca_app/` tree is materially better than the legacy `src/` tree on secret handling, but the API auth fallback behavior still needs hardening.
