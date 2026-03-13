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

## Output

Deployment details are written to:

- `infra/deployment.outputs.env`

This file includes resource names, DB endpoint details, and image reference.
