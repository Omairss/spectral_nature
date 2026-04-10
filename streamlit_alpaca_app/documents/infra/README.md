# Azure Deployment (Pipeline Jobs)

## Prerequisites

- Azure CLI authenticated (`az login --use-device-code`)
- Access to a subscription with permission to create:
  - Resource group
  - Storage account
  - Container Apps environment and jobs
  - Azure Container Registry
  - Azure Database for PostgreSQL Flexible Server

## Deploy

From repo root:

```bash
chmod +x scripts/deploy_pipeline_azure.sh
./scripts/deploy_pipeline_azure.sh
```

Optional environment overrides:

- `LOCATION` (default: `eastus`)
- `RESOURCE_GROUP`
- `SUFFIX`
- `APCA_API_KEY_SECRET_NAME`
- `APCA_API_SECRET_KEY_SECRET_NAME`
- `APCA_API_BASE_URL`
- `ALPACA_DATA_BASE_URL`
- `FRED_API_KEY`

Alpaca credentials are now expected to already exist in Key Vault. The deploy script validates the named secrets instead of writing raw Alpaca values from the local shell.

Cron schedules live in:

- `infra/job_schedules.env`

The taxonomy refresh job is deployed as:

- `entity-taxonomy-refresh`

Attention homepage materialization is deployed as:

- `attention-home-build`

Default cadence:

- once per month at `0 9 1 * *` UTC

Taxonomy flow charts and setup notes live in:

- `documents/infra/TAXONOMY_PIPELINE_FLOW.md`

## Output

Local deployment details are written to:

- `infra/.generated/deployment.local.env`
- `infra/.generated/email_delivery.local.env`

These files are intentionally ignored by git. They keep local operator context out of the tracked repo while still giving scripts a stable place to read from.

The committed infra index lives in:

- `documents/infra/RESOURCE_INDEX.md`

Refresh the local generated files or print the current inventory with:

```bash
bash ./scripts/show_infra_inventory.sh
bash ./scripts/show_infra_inventory.sh --write-local
```

## UI App Deployment

The Spectral Nature UI app uses a separate deployment entrypoint:

```bash
./scripts/deploy_ui_azure.sh
```

Typical usage:

```bash
# Build current repo state and deploy to Development.
./scripts/deploy_ui_azure.sh

# Promote the currently running Development image to Production.
./scripts/deploy_ui_azure.sh --target prod --promote-from dev
```

The UI deploy script:

- builds `Dockerfile.app` into ACR when deploying to Development
- pins the deployed image by digest
- waits for the latest revision to become ready
- smoke-checks the UI endpoint
- rewrites `documents/infra/UI_DEPLOYMENT_STATUS.md` from live Azure state

Email delivery for password resets and invites is provisioned separately:

```bash
./scripts/setup_ui_email_delivery_azure.sh --target dev --test-to you@example.com
```
