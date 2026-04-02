# Email Delivery Setup Plan

## Goal

Enable real outbound email for password resets, admin-issued reset links, and invite-based onboarding in the Streamlit UI.

## Current architecture

- Auth flows already exist in `services/auth_service.py`.
- SMTP delivery already exists in `services/emailer.py`.
- The missing pieces were operational: stable secret names, Azure mail resource provisioning, and Container App env wiring.

## Target design

- Use Azure Communication Services Email with an Azure-managed sender domain.
- Use one Entra app registration as the SMTP auth principal.
- Store only secrets in Key Vault:
  - `app-smtp-username`
  - `app-smtp-password`
  - `app-email-from`
- Keep non-secret SMTP settings in Container App env:
  - `APP_SMTP_HOST=smtp.azurecomm.net`
  - `APP_SMTP_PORT=587`
  - `APP_SMTP_USE_TLS=true`
  - `APP_SMTP_USE_SSL=false`
- Keep the Streamlit app reading those values through `services/emailer.py`.

## Repo changes

- Added default Key Vault secret names in `services/emailer.py`.
- Added `scripts/setup_ui_email_delivery_azure.sh` to provision/update the Azure mail path.
- Updated `scripts/deploy_ui_azure.sh` so future UI deploys preserve `APP_PUBLIC_BASE_URL` and SMTP env settings.
- Documented the setup flow in `documents/README.md`, `documents/operations/PROJECT_SETUP_AND_OPERATIONS.md`, and `documents/infra/README.md`.

## Verification path

1. Run the setup script for `--target dev`.
2. Wait for the latest Container App revision to become ready.
3. Smoke-check the Streamlit health endpoint.
4. Send a real SMTP test message through `services.emailer.send_email`.

## Production follow-up

- Re-run the setup script with `--target prod` only when production email delivery is approved.
- If production should use a branded sender domain rather than the Azure-managed domain, switch the Email Service domain to a customer-managed domain first.
