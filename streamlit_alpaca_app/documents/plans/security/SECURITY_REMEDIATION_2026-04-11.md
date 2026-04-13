# Security Remediation Update - 2026-04-11

This note tracks the immediate remediation work completed after the repo security review on `2026-04-11`.

Related review:

- `SECURITY_REVIEW_2026-04-11.md`

## Completed immediately

### 1. Azure SQL admin password was rotated

What changed:

- Rotated the live Azure SQL server admin password for:
  - server: `spectral-nature-server`
  - resource group: `spectral-nature-2`
  - admin login: `sn-sql-db`
- Stored the replacement in Azure Key Vault secret:
  - vault: `spectral-nature-kvault`
  - secret: `legacy-azure-sql-admin-password`

Why this matters:

- The old password was committed in tracked code and a notebook, so it had to be treated as compromised.

### 2. FRED key was updated

What changed:

- Updated Azure Key Vault secret:
  - vault: `spectral-nature-kvault`
  - secret: `Fred`
- Verified the replacement key against the live FRED API.

Why this matters:

- The old key was hardcoded in several tracked files and must be treated as leaked.

## Repo changes completed

### Hardcoded secrets removed from tracked runtime files

Changed files:

- `src/LinkedAuth.py`
- `src/fred/fred_config.py`
- `src/fred/db_connect.py`
- `src/fred/source.py`
- `src/fred/get_series_id.py`
- `src/fred/releases.py`
- `src/fred/get_series_id_data.py`
- `notebooks/db_test.ipynb`

What changed:

- Added shared legacy secret resolution through env vars or Azure Key Vault.
- Removed hardcoded Azure SQL password from `src/fred/db_connect.py`.
- Removed hardcoded FRED API key from the legacy FRED scripts.
- Cleared the committed notebook output that exposed the SQL endpoint failure details and replaced literal connection settings with env-based loading.

### API auth now fails closed for missing auth context

Changed files:

- `streamlit_alpaca_app/api/main.py`
- `streamlit_alpaca_app/services/api_auth.py`
- `streamlit_alpaca_app/tests/test_api_auth.py`
- `streamlit_alpaca_app/tests/test_api_v1.py`

What changed:

- Protected API routes no longer fall back to an anonymous principal when auth is disabled.
- Missing credentials now return:
  - `401` when auth is enabled
  - `503` when the auth backend is disabled or unavailable
- Auth status now surfaces backend errors instead of silently downgrading to `False`.
- Access-token signing no longer falls back to dashboard passwords.
- A dedicated access-token signing secret is now required unless `API_ALLOW_EPHEMERAL_ACCESS_TOKEN_SECRET=1` is explicitly set for controlled dev or test use.
- Legacy session-token bearer auth is now disabled by default. Re-enable it only with `API_ALLOW_LEGACY_SESSION_BEARER=1` for short-lived migration work.

### Browser-persistent login is no longer the default

Changed files:

- `streamlit_alpaca_app/app.py`
- `streamlit_alpaca_app/services/auth_service.py`
- `streamlit_alpaca_app/tests/test_auth_service.py`

What changed:

- The Streamlit UI no longer writes a readable auth cookie by default.
- Login persistence now defaults to session-only behavior in the browser.
- Existing readable auth cookies are actively cleared when the secure default is in effect.
- The old browser-persistent cookie path is still available only as an explicit opt-in with `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE=1`.
- Production now treats that setting as an explicit deploy-time choice instead of an implicit code default.

Why this matters:

- Streamlit cannot set an `HttpOnly` cookie directly from the app code path.
- The previous implementation stored the raw session token in a JavaScript-written cookie, which made it readable to in-page script.
- The secure code default remains available, but browser persistence can still be restored intentionally where product continuity requires it.

### Legacy CLI password handling was reduced

Changed files:

- `src/CurrentStatus.py`
- `src/TechnicalAnalyzer.py`
- `src/OptionFinder.py`
- `src/MarketExplorer.py`
- `src/MarketExplorerDataCruncher.py`

What changed:

- `--password` is no longer accepted on these entrypoints.
- They now prefer:
  - `RH_PASSWORD`
  - Azure Key Vault secret `rh-pswd`
  - interactive prompt fallback
- `LinkedAuth.get_creds_old_v1(...)` is now blocked by default and only works when `ALLOW_LEGACY_PLAINTEXT_SECRETS=1` is set for explicit migration work.

### Azure SQL and Key Vault observability were enabled and surfaced in admin

Changed resources:

- SQL server: `spectral-nature-server`
- SQL databases: `spectral-nature-db`, `master`
- Key Vault: `spectral-nature-kvault`
- Log Analytics workspace: `spectralnature8166173703`

Changed files:

- `streamlit_alpaca_app/services/admin_security_status.py`
- `streamlit_alpaca_app/services/auth_service.py`
- `streamlit_alpaca_app/app.py`
- `streamlit_alpaca_app/tests/test_admin_security_status.py`
- `streamlit_alpaca_app/tests/test_auth_service.py`

What changed:

- Enabled SQL server auditing to Log Analytics.
- Enabled SQL database auditing to Log Analytics for both `spectral-nature-db` and `master`.
- Enabled SQL server diagnostic settings.
- Enabled broader SQL database diagnostics for both databases alongside the audit feed.
- Enabled Key Vault diagnostic settings.
- Added a `Cloud Audit Coverage` block to the Access Admin `Usage + Security` view so operators can see current audit and diagnostic coverage without leaving the app.
- Fixed the admin status reader to resolve the target Azure subscription by checking the configured resource group and tracked SQL / Key Vault resources instead of assuming the first enabled subscription visible to the credential.
- Stopped the admin status reader from inheriting the generic pipeline resource-group env by default, because those pipeline resources do not map to the SQL and Key Vault resources being audited here.

Live verification after the fix:

- Direct app-side status check now reports:
  - healthy resources: `4/4`
  - SQL audit enabled: `3/3`
  - diagnostics enabled: `4/4`
  - workspace mismatches: `0`
- Verified resources:
  - SQL server: `spectral-nature-server`
  - SQL databases: `spectral-nature-db`, `master`
  - Key Vault: `spectral-nature-kvault`
  - Log Analytics workspace resource id: `/subscriptions/b69da224-4a61-4fc4-a5b2-3bb567436762/resourceGroups/spectral-nature-2/providers/Microsoft.OperationalInsights/workspaces/spectralnature8166173703`

## Breach and log check

What was checked:

- Azure Activity Log for the SQL server over the last 30 days
- Azure Activity Log for the Key Vault over the last 30 days
- Defender / Azure security alerts
- SQL server and SQL database audit policy state
- Azure diagnostic settings on the SQL server and Key Vault

What was found:

- No unusual management-plane activity showed up in the reviewed Activity Log window.
- No Azure security alerts were present at review time.
- Recent SQL server admin activity matched the password rotation work done on `2026-04-11`.
- Recent Key Vault management activity matched known role-assignment changes.

Limits of confidence at review time:

- Before this remediation pass, SQL server auditing was disabled.
- Before this remediation pass, SQL database auditing was disabled.
- Before this remediation pass, SQL server diagnostic settings were absent.
- Before this remediation pass, Key Vault diagnostic settings were absent.

Result:

- There was no clear sign of abuse in the available management-plane logs.
- At review time there still was not enough telemetry to rule out earlier data-plane use of the previously leaked SQL password or the old FRED key.
- Going forward, this telemetry gap is materially reduced because SQL audit flow and the requested diagnostics are now enabled and visible in admin.

## Remaining work

Still open:

1. Rewrite Git history for the leaked SQL password and old FRED key.
2. Invalidate or coordinate cleanup for old clones that still contain the leaked values.
3. If browser-persistent login is needed again, replace the old readable-cookie model with a true `HttpOnly` server-managed path or a trusted proxy auth layer.
4. Decide whether tracked research cache artifacts and direct outbound hotlinks should stay in Git in their current form.
