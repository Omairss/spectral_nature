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
- `APCA_API_KEY`
- `APCA_API_SECRET_KEY`
- `APCA_API_BASE_URL`
- `ALPACA_DATA_BASE_URL`
- `FRED_API_KEY`

Cron schedules live in:

- `infra/job_schedules.env`

The taxonomy refresh job is deployed as:

- `entity-taxonomy-refresh`

Attention homepage materialization is deployed as:

- `attention-home-build`

Default cadence:

- once per month at `0 9 1 * *` UTC

Taxonomy flow charts and setup notes live in:

- `infra/TAXONOMY_PIPELINE_FLOW.md`

## Output

Deployment details are written to:

- `infra/deployment.outputs.env`

This file includes resource names, DB endpoint details, and image reference.

## UI App Deployment

The Streamlit UI app uses a separate deployment entrypoint:

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
- smoke-checks the Streamlit endpoint
- rewrites `infra/UI_DEPLOYMENT_STATUS.md` from live Azure state
