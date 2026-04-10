# Infra Resource Index

This is the tracked index for Spectral Nature infrastructure.

It does not store live generated values. It stores the stable lookup rules, file locations, and the command to refresh local operator context.

## Source of truth

- Azure is the source of truth for live resource names, endpoints, and revisions.
- Key Vault is the source of truth for secret values.
- `infra/.generated/deployment.local.env` is the ignored local cache for pipeline/runtime context.
- `infra/.generated/email_delivery.local.env` is the ignored local cache for UI email/runtime context.
- `documents/infra/UI_DEPLOYMENT_STATUS.md` tracks live UI revisions and smoke-check status.

## Refresh commands

Refresh the ignored local cache files from current Azure state:

```bash
bash ./scripts/show_infra_inventory.sh --write-local
```

Print the current inventory without writing files:

```bash
bash ./scripts/show_infra_inventory.sh
```

## Logical inventory

### Pipeline platform

- `pipeline_resource_group`
  - Purpose: top-level Azure resource group for pipeline jobs and shared infra
  - Local cache: `PIPELINE_RESOURCE_GROUP` in `infra/.generated/deployment.local.env`
  - Live lookup: `az resource list --name sn-streamlit-ui-dev --resource-type Microsoft.App/containerApps --query "[0].resourceGroup" -o tsv`

- `pipeline_key_vault`
  - Purpose: pipeline and shared secret store
  - Local cache: `KEYVAULT_NAME`, `AZURE_KEY_VAULT_NAME`, `KEY_VAULT_NAME`
  - Live lookup: `bash ./scripts/show_infra_inventory.sh`

- `pipeline_storage_account`
  - Purpose: blob-backed dataset storage
  - Local cache: `STORAGE_ACCOUNT`, `AZURE_STORAGE_ACCOUNT_URL`
  - Live lookup: `az storage account list -g "$RESOURCE_GROUP" -o table`

- `pipeline_acr`
  - Purpose: container image registry
  - Local cache: `ACR_NAME`
  - Live lookup: `az acr list -g "$RESOURCE_GROUP" -o table`

- `pipeline_postgres_server`
  - Purpose: metadata database host
  - Local cache: `POSTGRES_SERVER`, `POSTGRES_DB`, `POSTGRES_ADMIN`
  - Live lookup: `az postgres flexible-server list -g "$RESOURCE_GROUP" -o table`

- `pipeline_managed_identity`
  - Purpose: runtime identity for jobs and Azure access
  - Local cache: `MANAGED_IDENTITY`
  - Live lookup: `az identity list -g "$RESOURCE_GROUP" -o table`

### UI apps

- `ui_dev_app`
  - Purpose: development UI Container App
  - Live lookup: `az resource show -n sn-streamlit-ui-dev -g "$RESOURCE_GROUP" --resource-type Microsoft.App/containerApps --api-version 2025-07-01`

- `ui_prod_app`
  - Purpose: production UI Container App
  - Live lookup: `az resource show -n sn-streamlit-ui -g "$RESOURCE_GROUP" --resource-type Microsoft.App/containerApps --api-version 2025-07-01`

- `ui_deployment_status`
  - Purpose: tracked snapshot of the current dev/prod image digests and revisions
  - Tracked file: `documents/infra/UI_DEPLOYMENT_STATUS.md`

### Secret names

These names are safe to track. Values stay in Key Vault.

- `apca-api-key`
- `apca-api-secret-key`
- `postgres-connection-string`
- `dashboard-auth-password`
- `dashboard-bootstrap-admin-password`
- `app-smtp-username`
- `app-smtp-password`
- `app-email-from`
- `azure-openai-api-key`

## Notes

- The old tracked `infra/*outputs.env` files were removed because they exposed live resource metadata in git.
- The replacement `.generated/*.local.env` files are ignored by git and only for local operator convenience.
- If a local file is missing, regenerate it from Azure instead of recreating it by hand.
