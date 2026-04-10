# Alpaca Key Vault Hardening

Date: 2026-04-10

## Goal

Remove tracked plaintext Alpaca credentials from the repo, make runtime load Alpaca values only from Azure Key Vault, and keep future deploys from reintroducing stale local values.

## Scope

- remove tracked `streamlit_alpaca_app/.env`
- ignore `.env` files in git and Docker build context
- change UI and pipeline Alpaca config loading to use Key Vault secret names only
- keep default Alpaca secret names:
  - `apca-api-key`
  - `apca-api-secret-key`
- update setup/deploy docs to point operators at Key Vault instead of raw env vars
- deploy the secured UI build to dev, then promote the same image to prod

## Design

1. Runtime:
   - `services/config.py` resolves Alpaca secret names from `APCA_API_KEY_SECRET_NAME` / `APCA_API_KEY_SECRET` and `APCA_API_SECRET_KEY_SECRET_NAME` / `APCA_API_SECRET_KEY_SECRET`.
   - Raw Alpaca env values are no longer accepted by UI or pipeline config loaders.
2. Repo hygiene:
   - `.env` is removed from tracked files.
   - `.gitignore` and `.dockerignore` exclude `.env` and `.env.*` while keeping `.env.example`.
3. Deploy safety:
   - `deploy_pipeline_azure.sh` validates that Alpaca secrets already exist in Key Vault instead of writing them from shell env vars.
   - `deploy_ui_azure.sh` avoids the broken `az containerapp` extension path and patches Container Apps through ARM directly.

## Verification

- unit tests for Key Vault-only Alpaca config resolution
- focused pipeline test for `_alpaca_config()`
- dev deploy smoke check
- prod promotion from the same approved dev image

## Residual Risk

Old rotated Alpaca values still exist in git history before this hardening commit. Rotation removes active credential risk, but history rewrite is still a separate cleanup if you want the old blobs removed from remote history entirely.
