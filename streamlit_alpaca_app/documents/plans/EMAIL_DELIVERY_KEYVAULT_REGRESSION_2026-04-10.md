# Email Delivery Key Vault Regression

Date: 2026-04-10

## Goal

Restore invite/reset email reliability after the Key Vault consolidation and make the app report the real email block reason instead of one generic fallback.

## Finding

- Local generated env state had split Key Vault names:
  - `KEYVAULT_NAME=spectral-nature-kvault`
  - `AZURE_KEY_VAULT_NAME=snpipelinekv03130136`
  - `KEY_VAULT_NAME=snpipelinekv03130136`
- The old vault no longer had `app-email-from`, so local secret resolution fell back to the retired vault path and email delivery showed as not configured.
- The UI also collapsed multiple failure modes into the same message:
  - missing sender address secret
  - missing `APP_PUBLIC_BASE_URL`
  - generic SMTP setup failure

## Source fixes

1. Local env normalization
- `streamlit_alpaca_app/scripts/load_local_env.sh` now normalizes all Key Vault alias env vars to one canonical value after local files are loaded.
- `streamlit_alpaca_app/scripts/show_infra_inventory.sh` now rewrites `AZURE_KEY_VAULT_NAME` and `KEY_VAULT_NAME` from the discovered canonical `KEYVAULT_NAME` instead of preserving stale aliases.

2. Runtime secret diagnostics
- `streamlit_alpaca_app/services/secrets.py` now exposes structured secret-resolution diagnostics:
  - env vs Key Vault source
  - vault name/url
  - failure reason
  - lookup error type when Key Vault reads fail

3. Email diagnostics
- `streamlit_alpaca_app/services/emailer.py` now builds an email delivery status object instead of one boolean.
- Sender-address failures now report the exact missing dependency, including the secret name and vault when available.

4. Auth flow diagnostics
- `streamlit_alpaca_app/services/auth_service.py` now separates:
  - mail transport configured
  - public auth-link base URL configured
- Invite and reset flows now return the blocking message directly.
- The admin Access UI now shows the detailed reason when email is unavailable.

5. Pending invite resend
- Added `resend_pending_invite(...)` in `streamlit_alpaca_app/services/auth_service.py`.
- Resend reuses the selected pending invite's email, role, and share settings.
- Reissuing an invite intentionally goes through `issue_invite(...)`, which revokes any existing pending invite for that email and creates a fresh token.
- Added a `Resend Invite` action next to `Delete Invite` in Access Admin, with rerun-safe notices so the UI can refresh the pending-invite table without losing the result message.

## Verification

- Focused tests passed:
  - `tests/test_emailer.py`
  - `tests/test_auth_service.py`
  - `tests/test_secrets.py`
- Local runtime verification after sourcing `load_local_env.sh` now resolves:
  - Key Vault: `spectral-nature-kvault`
  - sender address source: Key Vault
  - email delivery status: configured

## Outcome

- The immediate regression was local stale-vault alias drift after consolidation.
- The local generated env files have been refreshed to the consolidated vault.
- Future failures should now tell us whether the problem is:
  - sender secret resolution
  - SMTP host config
  - missing public base URL
- Prod rollout is complete:
  - SMTP env was applied to `sn-streamlit-ui`
  - prod now points at image `sha256:c8408bd01f5d0d7b97a43e2ecfb505cb59fe46e84156928594ca70f2ef34eacd`
  - latest ready prod revision is `sn-streamlit-ui--0000050`
  - resend-invite UI is now live in both dev and prod
