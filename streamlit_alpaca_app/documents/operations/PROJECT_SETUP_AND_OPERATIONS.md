# Spectral Nature: Setup & Operations Guide

This guide is for new contributors/operators with **no prior project context**.

---

## 1) What this project is

`streamlit_alpaca_app` is the Spectral Nature UI application. It:

- serves UI views (portfolio, performance, FRED macro, market/technical/options/fundamentals)
- reads mostly from **precomputed pipeline snapshots** (Blob parquet + Postgres metadata)
- can fall back to live APIs for selected views when needed

### Core components

- **UI app**: `app.py`
- **Compute layer**: `compute/*` (pure transforms and derived-data logic)
- **Services**: `services/*` (API clients, caching, pipeline store integration, secrets)
- **Pipeline jobs**: `pipeline/jobs/main.py`
- **Infra scripts/docs**: `scripts/*`, `infra/*`, `documents/infra/*`
- **Working plans**: `documents/plans/*` for active refactor, recovery, and implementation notes

---

## 2) High-level architecture

### Data plane

1. Scheduled Azure Container App Jobs run ingestion/transforms.
2. Datasets are written to Blob Storage (`pipeline` container).
3. Dataset/job metadata is written to Postgres (`dataset_versions`, `job_runs`).
4. UI reads metadata + latest snapshot and renders quickly.

### Runtime/security plane

- Azure Container Apps + ACR + Managed Identity
- Key Vault for secrets
- authentication gate before UI access

---

## 3) Current Azure environments

### UI (separate prod/dev)

- **Prod (stable)**: `sn-streamlit-ui`
- **Dev**: `sn-streamlit-ui-dev`

See current URLs/revisions in:

- `documents/infra/UI_DEPLOYMENT_STATUS.md`

### Pipeline jobs

- `universe-builder`
- `equities-intraday-preload`
- `macro-fred-daily`
- `commodities-regime`
- `options-liquid-universe`
- `news-ingest-and-features`
- `attention-home-build`

Deployment outputs are stored in:

- `infra/deployment.outputs.env`

---

## 4) Local setup (developer machine)

## Prerequisites

- Python 3.10+
- Azure CLI (`az`) authenticated if you use Azure-backed features

## Install

```bash
cd Users/omai.r/spectral_nature/streamlit_alpaca_app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure env

```bash
cp .env.example .env
# set APCA_API_KEY / APCA_API_SECRET_KEY etc.
```

For Azure-backed runs, load deployment outputs:

```bash
source infra/deployment.outputs.env
```

## Run locally

```bash
./scripts/run_ui_local.sh
```

---

## 5) Key environment variables

## Authentication/UI

- `DASHBOARD_AUTH_ENABLED=true|false`
- `DASHBOARD_AUTH_USERNAME_SECRET`
- `DASHBOARD_AUTH_PASSWORD_SECRET`
- `DASHBOARD_AUTH_MODE=legacy|database|auto`
- `APP_PUBLIC_BASE_URL`
- `AZURE_KEY_VAULT_NAME` / `KEY_VAULT_NAME`

## Email delivery

- `APP_SMTP_HOST` (ACS SMTP: `smtp.azurecomm.net`)
- `APP_SMTP_PORT` (ACS SMTP: `587`)
- `APP_SMTP_USE_TLS=true|false`
- `APP_SMTP_USE_SSL=true|false`
- `APP_SMTP_USERNAME_SECRET` (default secret: `app-smtp-username`)
- `APP_SMTP_PASSWORD_SECRET` (default secret: `app-smtp-password`)
- `APP_EMAIL_FROM_SECRET` (default secret: `app-email-from`)

## Pipeline store access

- `POSTGRES_CONNECTION_STRING_SECRET_NAME` (default secret: `postgres-connection-string`)
- `AZURE_STORAGE_ACCOUNT_URL`
- `AZURE_STORAGE_CONTAINER`
- `PIPELINE_RESOURCE_GROUP`

## Environment labels

- `APP_TRACK=production|development|local`
- `APP_DISABLE_CACHE=true|false` (dev currently true, prod false)

## Fundamentals refresh cadence

- `FUNDAMENTALS_MIN_REFRESH_HOURS` (default `24`)

## Working design docs

- `documents/plans/README.md` is the entrypoint for active implementation plans.
- `documents/reference/ATTENTION_FEED_GUIDELINES.md` holds the current product standard and evidence rules.
- Keep transient notes in `documents/plans/` instead of adding new root-level scratch docs.

---

## 6) Deploying pipeline jobs

Main script:

```bash
./scripts/deploy_pipeline_azure.sh
```

This provisions/updates:

- Resource group
- ACR
- Managed identity
- Container Apps environment + jobs
- Storage account
- Postgres
- Key Vault wiring

Notes:

- Jobs use managed identity for ACR pull.
- Jobs resolve secrets via Key Vault-backed env patterns.

---

## 7) Deploying UI app

Main script:

```bash
./scripts/deploy_ui_azure.sh
```

Default behavior:

1. Build/push `Dockerfile.app` as `streamlit-ui:<target>-<timestamp>` in ACR.
2. Deploy the digest-pinned image to `sn-streamlit-ui-dev`.
3. Wait for the latest revision to become ready.
4. Run endpoint smoke checks.
5. Rewrite `documents/infra/UI_DEPLOYMENT_STATUS.md` from live Azure state.

Promote the currently running Development image to Production:

```bash
./scripts/deploy_ui_azure.sh --target prod --promote-from dev
```

Useful flags:

- `--image-ref <repo:tag|repo@sha256:...>` to deploy an existing image
- `--refresh-tracker-only` to rewrite `documents/infra/UI_DEPLOYMENT_STATUS.md` without deploying

Warning:

- Keep `APP_FORCE_DATA_REFRESH_DEFAULT=false` for normal dev and prod rollouts.
- For Home and other snapshot-first surfaces, setting it to `true` is not a harmless performance toggle. It changes behavior and can bypass precomputed views in favor of on-demand rebuilds.
- `scripts/deploy_ui_azure.sh` now pins the default to `false` for both environments. Only override it intentionally with `APP_FORCE_DATA_REFRESH_DEFAULT_OVERRIDE=true` for targeted debugging, and revert immediately after.

### Custom domains and managed TLS

Current Development custom domains are bound on `sn-streamlit-ui-dev` in:

- Resource group: `sn-pipeline-rg-03130136`
- Container Apps environment: `sn-pipeline-env`
- Generated app FQDN: `sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io`

For Azure Container Apps managed certificates, publish DNS first:

- Apex domain (`torres-cap.com`)
  - `A @ -> 172.168.33.46` (the Container Apps environment static IP)
  - `TXT asuid -> <customDomainVerificationId>`
- Subdomain (`www.torres-cap.com`)
  - `CNAME www -> sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io`
  - `TXT asuid.www -> <customDomainVerificationId>`

You can fetch the verification ID with:

```bash
az containerapp show \
  -g sn-pipeline-rg-03130136 \
  -n sn-streamlit-ui-dev \
  --query properties.customDomainVerificationId -o tsv
```

Bind the hostnames after DNS is live:

```bash
az containerapp hostname add \
  -g sn-pipeline-rg-03130136 \
  -n sn-streamlit-ui-dev \
  --hostname torres-cap.com

az containerapp hostname bind \
  -g sn-pipeline-rg-03130136 \
  -n sn-streamlit-ui-dev \
  --environment sn-pipeline-env \
  --hostname torres-cap.com \
  --validation-method HTTP

az containerapp hostname add \
  -g sn-pipeline-rg-03130136 \
  -n sn-streamlit-ui-dev \
  --hostname www.torres-cap.com

az containerapp hostname bind \
  -g sn-pipeline-rg-03130136 \
  -n sn-streamlit-ui-dev \
  --environment sn-pipeline-env \
  --hostname www.torres-cap.com \
  --validation-method CNAME
```

### Email delivery setup

Use the Azure setup script when enabling password reset and invite emails for a UI app:

```bash
./scripts/setup_ui_email_delivery_azure.sh --target dev --test-to you@example.com
```

What it does:

1. Creates or reuses Azure Communication Services Email and Communication resources in the UI resource group.
2. Creates an Azure-managed domain plus a sender username for outbound mail.
3. Creates or reuses an Entra app registration for SMTP auth and stores the client secret in Key Vault.
4. Writes the UI-facing mail secrets to the Key Vault used by the selected Container App.
5. Updates the target Container App with `APP_SMTP_*` and `APP_EMAIL_FROM_SECRET`.
6. Waits for the new revision, smoke-checks the app, and optionally sends a test message.

Non-secret outputs are written to `infra/email_delivery.outputs.env`.

Operational notes:

- The managed certificate creation step can stay in `Pending` for several minutes before Azure binds it.
- Apex domains use `HTTP` validation; subdomains use `CNAME` validation.
- If CAA records exist on the root domain, allow DigiCert or managed certificate issuance can fail.
- As of `2026-03-20`, `torres-cap.com` and `www.torres-cap.com` both return `HTTP 200` over HTTPS on the Development app.

---

## 8) Caching behavior

- Global cache helpers are in `services/data_cache.py`.
- Dev can disable cache via `APP_DISABLE_CACHE=true`.
- Prod should keep cache enabled for speed.

Current behavior:

- **Dev**: cache disabled to maximize visibility/iteration speed.
- **Prod**: cache enabled for stable responsive UX.

---

## 9) Data refresh cadences (current)

From active job schedules:

- `universe-builder`: `20 13 * * 1-5`
- `equities-intraday-preload`: `35 13,16,18,20 * * 1-5`
- `macro-fred-daily`: `0 11 * * *`
- `commodities-regime`: `10 14,18,22 * * 1-5`
- `options-liquid-universe`: `45 14,20 * * 1-5`
- `news-ingest-and-features`: `5 14,16,18,20 * * 1-5`
- `attention-home-build`: `20 14,16,18,20 * * 1-5`
- `entity-taxonomy-refresh`: `0 9 1 * *`

Fundamentals are generated inside equities preload, but throttled to once per `FUNDAMENTALS_MIN_REFRESH_HOURS` (default 24h).
Taxonomy refresh runs once a month by default and publishes the DB-backed entity taxonomy snapshot for NASDAQ and NYSE listings.
For setup and runtime flow charts, see `documents/infra/TAXONOMY_PIPELINE_FLOW.md`.

---

## 10) Troubleshooting quick reference

## “CLI execution failed: No such file or directory: az” in job tracker

- Cause: UI container does not include Azure CLI.
- Current fix: tracker is DB-first and no longer depends on `az` for status table.

## “Postgres job metadata unavailable”

- Usually missing `postgres-connection-string` secret in the Key Vault used by UI.
- Ensure UI vault contains that secret and restart app revision.

## FRED “deferred until requested” message

- This guard is intended to avoid cold-start hangs.
- With pipeline store configured and preload healthy, FRED should load from snapshot path.

## Fundamentals CSV not found

- Runtime image must include `data/stock_fundamental`.
- `Dockerfile.app` explicitly copies this path.

## “Pipeline Jobs page not updating after deploy”

- Force a fresh revision (change env stamp) and hard refresh browser.
- Prefer digest-pinned images over `:latest` for deterministic rollout.

---

## 11) Suggested reorg/simplification (recommended)

These are high-impact and low-risk improvements:

1. **Split monolithic `app.py` into view modules**
   - Create `views/` package (`views/fred.py`, `views/pipeline_jobs.py`, etc.)
   - Keep `app.py` as router + shared sidebar state.

2. **Extend UI deployment checks**
   - Keep `scripts/deploy_ui_azure.sh` aligned with runtime behavior.
   - Add deeper preflight checks for secrets and dataset freshness before rollout.

3. **Consolidate environment docs**
   - Keep one source of truth for env vars and secret names (`docs/configuration.md`).

4. **Separate schedule ownership from code**
   - Put all cron settings in one file (e.g., `infra/job_schedules.env`) and consume from deploy scripts.

5. **Introduce lightweight smoke checks**
   - Script that checks:
     - Key Vault required secrets
     - latest `job_runs` freshness
     - latest `dataset_versions` freshness
     - UI endpoint health

6. **Standardize status tracker updates**
   - Auto-update `documents/infra/UI_DEPLOYMENT_STATUS.md` in deploy script after successful rollout.

---

## 12) First-day checklist for a new engineer

1. Read this file and `documents/infra/UI_DEPLOYMENT_STATUS.md`.
2. Run app locally with `.env` + `infra/deployment.outputs.env`.
3. Verify login and Pipeline Jobs page.
4. Trigger one source refresh and confirm `job_runs` updates.
5. Make change in dev app only, validate, then promote.

---

If this guide drifts from runtime behavior, treat Azure resource state + deployment scripts as source of truth and update this file immediately.
