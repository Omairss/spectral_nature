#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required."
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  echo "Run: az login --use-device-code"
  exit 1
fi

az extension add --name containerapp --upgrade >/dev/null

LOCATION="${LOCATION:-eastus}"
ENV_NAME="${ENV_NAME:-sn-pipeline-env}"
SUFFIX="${SUFFIX:-$(date +%m%d%H%M)}"
RESOURCE_GROUP="${RESOURCE_GROUP:-sn-pipeline-rg-${SUFFIX}}"
KEYVAULT_NAME="${KEYVAULT_NAME:-snpipelinekv${SUFFIX}}"
STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-snpipeline${SUFFIX}}"
ACR_NAME="${ACR_NAME:-snpipelineacr${SUFFIX}}"
POSTGRES_SERVER="${POSTGRES_SERVER:-sn-pg-${SUFFIX}}"
POSTGRES_DB="${POSTGRES_DB:-pipeline}"
POSTGRES_ADMIN="${POSTGRES_ADMIN:-snadmin}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)}"
CONTAINER_IMAGE_TAG="${CONTAINER_IMAGE_TAG:-pipeline-jobs:latest}"
LOG_ANALYTICS="${LOG_ANALYTICS:-sn-pipeline-la-${SUFFIX}}"
UAMI_NAME="${UAMI_NAME:-sn-pipeline-mi-${SUFFIX}}"
JOB_PREFIX="${JOB_PREFIX:-snpj-${SUFFIX}}"
POSTGRES_CONNECTION_STRING_SECRET_NAME="${POSTGRES_CONNECTION_STRING_SECRET_NAME:-postgres-connection-string}"
APCA_API_KEY_SECRET_NAME="${APCA_API_KEY_SECRET_NAME:-apca-api-key}"
APCA_API_SECRET_KEY_SECRET_NAME="${APCA_API_SECRET_KEY_SECRET_NAME:-apca-api-secret-key}"
FRED_API_KEY_SECRET_NAME="${FRED_API_KEY_SECRET_NAME:-Fred}"

# Storage account name constraints
STORAGE_ACCOUNT="$(echo "$STORAGE_ACCOUNT" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | cut -c1-24)"
ACR_NAME="$(echo "$ACR_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | cut -c1-50)"
KEYVAULT_NAME="$(echo "$KEYVAULT_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-' | cut -c1-24)"

echo "[1/11] Creating resource group"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "[2/11] Creating Key Vault"
az keyvault create \
  --name "$KEYVAULT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --enable-rbac-authorization true \
  --output none

echo "[3/11] Creating storage account"
az storage account create \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku Standard_LRS \
  --kind StorageV2 \
  --allow-blob-public-access false \
  --output none

ACCOUNT_KEY="$(az storage account keys list -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query "[0].value" -o tsv)"
az storage container create --name datasets --account-name "$STORAGE_ACCOUNT" --account-key "$ACCOUNT_KEY" --output none

echo "[4/11] Creating PostgreSQL flexible server"
az postgres flexible-server create \
  --name "$POSTGRES_SERVER" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --storage-size 32 \
  --version 16 \
  --admin-user "$POSTGRES_ADMIN" \
  --admin-password "$POSTGRES_PASSWORD" \
  --yes \
  --output none

az postgres flexible-server db create \
  --resource-group "$RESOURCE_GROUP" \
  --server-name "$POSTGRES_SERVER" \
  --database-name "$POSTGRES_DB" \
  --output none

echo "[5/11] Creating Log Analytics + Container Apps environment"
az monitor log-analytics workspace create \
  --resource-group "$RESOURCE_GROUP" \
  --workspace-name "$LOG_ANALYTICS" \
  --location "$LOCATION" \
  --output none

WORKSPACE_ID="$(az monitor log-analytics workspace show -g "$RESOURCE_GROUP" -n "$LOG_ANALYTICS" --query customerId -o tsv)"
WORKSPACE_KEY="$(az monitor log-analytics workspace get-shared-keys -g "$RESOURCE_GROUP" -n "$LOG_ANALYTICS" --query primarySharedKey -o tsv)"

az containerapp env create \
  --name "$ENV_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --logs-workspace-id "$WORKSPACE_ID" \
  --logs-workspace-key "$WORKSPACE_KEY" \
  --output none

echo "[6/11] Creating ACR and building pipeline image"
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic \
  --location "$LOCATION" \
  --admin-enabled false \
  --output none

az acr build --resource-group "$RESOURCE_GROUP" --registry "$ACR_NAME" --image "$CONTAINER_IMAGE_TAG" --file Dockerfile.pipeline .

REGISTRY_SERVER="$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query loginServer -o tsv)"
IMAGE="${REGISTRY_SERVER}/${CONTAINER_IMAGE_TAG}"

echo "[7/11] Creating user-assigned managed identity"
az identity create --name "$UAMI_NAME" --resource-group "$RESOURCE_GROUP" --location "$LOCATION" --output none
UAMI_ID="$(az identity show --name "$UAMI_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"
UAMI_PRINCIPAL_ID="$(az identity show --name "$UAMI_NAME" --resource-group "$RESOURCE_GROUP" --query principalId -o tsv)"
UAMI_CLIENT_ID="$(az identity show --name "$UAMI_NAME" --resource-group "$RESOURCE_GROUP" --query clientId -o tsv)"

STORAGE_ID="$(az storage account show -n "$STORAGE_ACCOUNT" -g "$RESOURCE_GROUP" --query id -o tsv)"
ACR_ID="$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)"
KEYVAULT_ID="$(az keyvault show -n "$KEYVAULT_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)"

echo "[8/11] Assigning RBAC for storage, ACR, and Key Vault access"
az role assignment create --assignee-object-id "$UAMI_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Storage Blob Data Contributor" --scope "$STORAGE_ID" --output none || true
az role assignment create --assignee-object-id "$UAMI_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "AcrPull" --scope "$ACR_ID" --output none || true
az role assignment create --assignee-object-id "$UAMI_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Key Vault Secrets User" --scope "$KEYVAULT_ID" --output none || true

PG_HOST="$(az postgres flexible-server show -g "$RESOURCE_GROUP" -n "$POSTGRES_SERVER" --query fullyQualifiedDomainName -o tsv)"
PG_CONN="postgresql://${POSTGRES_ADMIN}:${POSTGRES_PASSWORD}@${PG_HOST}:5432/${POSTGRES_DB}?sslmode=require"
STORAGE_URL="https://${STORAGE_ACCOUNT}.blob.core.windows.net"

echo "[9/11] Writing deployment secrets to Key Vault"
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "$POSTGRES_CONNECTION_STRING_SECRET_NAME" --value "$PG_CONN" --output none
if [[ -n "${APCA_API_KEY:-}" ]]; then
  az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "$APCA_API_KEY_SECRET_NAME" --value "$APCA_API_KEY" --output none
fi
if [[ -n "${APCA_API_SECRET_KEY:-}" ]]; then
  az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "$APCA_API_SECRET_KEY_SECRET_NAME" --value "$APCA_API_SECRET_KEY" --output none
fi
if [[ -n "${FRED_API_KEY:-}" ]]; then
  az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "$FRED_API_KEY_SECRET_NAME" --value "$FRED_API_KEY" --output none
fi

create_job () {
  local job_name="$1"
  local cron="$2"

  az containerapp job create \
    --name "$job_name" \
    --resource-group "$RESOURCE_GROUP" \
    --environment "$ENV_NAME" \
    --trigger-type Schedule \
    --cron-expression "$cron" \
    --replica-timeout 3600 \
    --replica-retry-limit 1 \
    --replica-completion-count 1 \
    --parallelism 1 \
    --image "$IMAGE" \
    --registry-server "$REGISTRY_SERVER" \
    --registry-identity "$UAMI_ID" \
    --cpu 1.0 \
    --memory 2Gi \
    --mi-user-assigned "$UAMI_ID" \
    --env-vars \
      PIPELINE_JOB_NAME="$job_name" \
      AZURE_CLIENT_ID="$UAMI_CLIENT_ID" \
      AZURE_KEY_VAULT_NAME="$KEYVAULT_NAME" \
      AZURE_STORAGE_ACCOUNT_URL="$STORAGE_URL" \
      AZURE_STORAGE_CONTAINER="datasets" \
      POSTGRES_CONNECTION_STRING_SECRET="$POSTGRES_CONNECTION_STRING_SECRET_NAME" \
      APCA_API_KEY_SECRET="$APCA_API_KEY_SECRET_NAME" \
      APCA_API_SECRET_KEY_SECRET="$APCA_API_SECRET_KEY_SECRET_NAME" \
      APCA_API_BASE_URL="${APCA_API_BASE_URL:-https://paper-api.alpaca.markets}" \
      ALPACA_DATA_BASE_URL="${ALPACA_DATA_BASE_URL:-https://data.alpaca.markets}" \
      FRED_KEY_VAULT_SECRET="$FRED_API_KEY_SECRET_NAME" \
      UNIVERSE_VERSION="$(date +%Y%m%d)" \
    --output none
}

echo "[10/11] Creating scheduled jobs"
# UTC schedules approximating US market hours windows.
create_job "universe-builder" "20 13 * * 1-5"
create_job "equities-intraday-preload" "35 13,16,18,20 * * 1-5"
create_job "macro-fred-daily" "0 11 * * *"
create_job "commodities-regime" "10 14,18,22 * * 1-5"
create_job "options-liquid-universe" "45 14,20 * * 1-5"
create_job "news-ingest-and-features" "5 14,16,18,20 * * 1-5"

echo "[11/11] Capturing deployment outputs"
cat > infra/deployment.outputs.env <<EOF
RESOURCE_GROUP=${RESOURCE_GROUP}
LOCATION=${LOCATION}
CONTAINERAPPS_ENV=${ENV_NAME}
KEYVAULT_NAME=${KEYVAULT_NAME}
STORAGE_ACCOUNT=${STORAGE_ACCOUNT}
STORAGE_URL=${STORAGE_URL}
ACR_NAME=${ACR_NAME}
IMAGE=${IMAGE}
POSTGRES_SERVER=${POSTGRES_SERVER}
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_ADMIN=${POSTGRES_ADMIN}
MANAGED_IDENTITY=${UAMI_NAME}
EOF

echo "Deployment complete"
echo "Saved outputs: infra/deployment.outputs.env"
echo "Portal RG: https://portal.azure.com/#@/resource/subscriptions/$(az account show --query id -o tsv)/resourceGroups/${RESOURCE_GROUP}/overview"
