#!/usr/bin/env bash
# Deploy the pipeline container (scheduled jobs) to Azure Container Apps.
#
# Source directories this container runs:
#   pipeline/, compute/, services/aql/, services/saa/,
#   services/attention_home_summary.py, services/attention_agentic.py,
#   services/attention_live_research.py, services/attention_market_events.py,
#   services/attention_home_1d.py, services/attention_ticker_snapshots.py,
#   services/omnibar*.py, services/web_research.py, services/page_browsing.py,
#   services/seeking_alpha_access.py, services/signals.py,
#   data_access/, requirements.txt
#
# If your change is only in app.py, presentation/, or config/, you likely
# need deploy_ui_azure.sh instead. Run scripts/which_deploy.sh to check.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DEFAULT_DEPLOY_OUTPUTS_FILE="${ROOT_DIR}/infra/.generated/deployment.local.env"
LEGACY_DEPLOY_OUTPUTS_FILE="${ROOT_DIR}/infra/deployment.outputs.env"
DEPLOY_OUTPUTS_FILE="${DEPLOY_OUTPUTS_FILE:-$DEFAULT_DEPLOY_OUTPUTS_FILE}"
JOB_SCHEDULES_FILE="${ROOT_DIR}/infra/job_schedules.env"
REQUESTED_KEYVAULT_NAME="${KEYVAULT_NAME:-${AZURE_KEY_VAULT_NAME:-${KEY_VAULT_NAME:-}}}"

if [[ -f "$DEPLOY_OUTPUTS_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$DEPLOY_OUTPUTS_FILE"
elif [[ "$DEPLOY_OUTPUTS_FILE" == "$DEFAULT_DEPLOY_OUTPUTS_FILE" && -f "$LEGACY_DEPLOY_OUTPUTS_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$LEGACY_DEPLOY_OUTPUTS_FILE"
fi

if [[ -f "$JOB_SCHEDULES_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$JOB_SCHEDULES_FILE"
fi

if [[ -n "$REQUESTED_KEYVAULT_NAME" ]]; then
  KEYVAULT_NAME="$REQUESTED_KEYVAULT_NAME"
  AZURE_KEY_VAULT_NAME="$REQUESTED_KEYVAULT_NAME"
  KEY_VAULT_NAME="$REQUESTED_KEYVAULT_NAME"
fi

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required."
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  echo "Run: az login --use-device-code"
  exit 1
fi

az extension add --name containerapp --upgrade >/dev/null

LOCATION="${LOCATION:-centralus}"
ENV_NAME="${ENV_NAME:-${CONTAINERAPPS_ENV:-sn-pipeline-env}}"
SUFFIX="${SUFFIX:-$(date +%m%d%H%M)}"
RESOURCE_GROUP="${RESOURCE_GROUP:-${PIPELINE_RESOURCE_GROUP:-sn-pipeline-rg-${SUFFIX}}}"
KEYVAULT_NAME="${KEYVAULT_NAME:-${AZURE_KEY_VAULT_NAME:-${KEY_VAULT_NAME:-snpipelinekv${SUFFIX}}}}"
STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-snpipeline${SUFFIX}}"
ACR_NAME="${ACR_NAME:-snpipelineacr${SUFFIX}}"
POSTGRES_SERVER="${POSTGRES_SERVER:-sn-pg-${SUFFIX}}"
POSTGRES_DB="${POSTGRES_DB:-pipeline}"
POSTGRES_ADMIN="${POSTGRES_ADMIN:-snadmin}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
CONTAINER_IMAGE_TAG="${CONTAINER_IMAGE_TAG:-pipeline-jobs:$(date +%Y%m%d%H%M%S)}"
LOG_ANALYTICS="${LOG_ANALYTICS:-sn-pipeline-la-${SUFFIX}}"
UAMI_NAME="${UAMI_NAME:-${MANAGED_IDENTITY:-sn-pipeline-mi-${SUFFIX}}}"
EXISTING_UAMI_RESOURCE_ID="${MANAGED_IDENTITY_RESOURCE_ID:-${UAMI_RESOURCE_ID:-}}"
EXISTING_UAMI_RESOURCE_GROUP="${MANAGED_IDENTITY_RESOURCE_GROUP:-}"
JOB_PREFIX="${JOB_PREFIX:-snpj-${SUFFIX}}"
POSTGRES_CONNECTION_STRING_SECRET_NAME="${POSTGRES_CONNECTION_STRING_SECRET_NAME:-postgres-connection-string}"
APCA_API_KEY_SECRET_NAME="${APCA_API_KEY_SECRET_NAME:-apca-api-key}"
APCA_API_SECRET_KEY_SECRET_NAME="${APCA_API_SECRET_KEY_SECRET_NAME:-apca-api-secret-key}"
FRED_API_KEY_SECRET_NAME="${FRED_API_KEY_SECRET_NAME:-Fred}"
FRED_BULK_MODE="${FRED_BULK_MODE:-v1_only}"
LLM_API_KEY_SECRET_NAME="${LLM_API_KEY_SECRET_NAME:-${AZURE_OPENAI_API_KEY_SECRET_NAME:-}}"
LLM_PROVIDER="${LLM_PROVIDER:-}"
LLM_MODEL="${LLM_MODEL:-}"
LLM_DEPLOYMENT="${LLM_DEPLOYMENT:-}"
AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-}"
LLM_TEMPERATURE="${LLM_TEMPERATURE:-}"
LLM_REASONING_EFFORT="${LLM_REASONING_EFFORT:-}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-}"
EMBEDDING_DEPLOYMENT="${EMBEDDING_DEPLOYMENT:-}"
LLM_ENV_SOURCE_JOBS="${LLM_ENV_SOURCE_JOBS:-attention-home-build news-ingest-and-features}"
PIPELINE_CACHE_MAX_BYTES="${PIPELINE_CACHE_MAX_BYTES:-}"
TAVILY_INCLUDE_RAW_CONTENT="${TAVILY_INCLUDE_RAW_CONTENT:-true}"

UNIVERSE_BUILDER_CRON="${UNIVERSE_BUILDER_CRON:-20 13 * * 1-5}"
EQUITIES_INTRADAY_PRELOAD_CRON="${EQUITIES_INTRADAY_PRELOAD_CRON:-35 13,16,18,20 * * 1-5}"
MACRO_FRED_DAILY_CRON="${MACRO_FRED_DAILY_CRON:-0 11 * * *}"
COMMODITIES_REGIME_CRON="${COMMODITIES_REGIME_CRON:-10 14,18,22 * * 1-5}"
OPTIONS_LIQUID_UNIVERSE_CRON="${OPTIONS_LIQUID_UNIVERSE_CRON:-45 14,20 * * 1-5}"
NEWS_INGEST_AND_FEATURES_CRON="${NEWS_INGEST_AND_FEATURES_CRON:-5 14,16,18,20 * * 1-5}"
ATTENTION_HOME_BUILD_CRON="${ATTENTION_HOME_BUILD_CRON:-20 14,16,18,20 * * 1-5}"
ATTENTION_HOME_RESEARCH_LIMIT="${ATTENTION_HOME_RESEARCH_LIMIT:-12}"
ATTENTION_HOME_SEEKING_ALPHA_PAGE_LIMIT="${ATTENTION_HOME_SEEKING_ALPHA_PAGE_LIMIT:-2}"
ATTENTION_HOME_SEEKING_ALPHA_PAGE_MAX_CHARS="${ATTENTION_HOME_SEEKING_ALPHA_PAGE_MAX_CHARS:-12000}"
ENTITY_TAXONOMY_REFRESH_CRON="${ENTITY_TAXONOMY_REFRESH_CRON:-0 9 1 * *}"
SEEKING_ALPHA_USERNAME_SECRET_NAME="${SEEKING_ALPHA_USERNAME_SECRET_NAME:-seeking-alpha-username}"
SEEKING_ALPHA_PASSWORD_SECRET_NAME="${SEEKING_ALPHA_PASSWORD_SECRET_NAME:-seeking-alpha-password}"
SEEKING_ALPHA_BROWSER_HEADLESS="${SEEKING_ALPHA_BROWSER_HEADLESS:-true}"

SIMFIN_API_KEY_SECRET_NAME="${SIMFIN_API_KEY_SECRET_NAME:-SimFinAPI}"
FUNDAMENTALS_QUARTERLY_REFRESH_CRON="${FUNDAMENTALS_QUARTERLY_REFRESH_CRON:-0 12 * * 1-5}"

job_exists() {
  local job_name="$1"
  az containerapp job show --name "$job_name" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1
}

maybe_append_env() {
  local key="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    JOB_ENV_VARS+=("${key}=${value}")
  fi
}

require_keyvault_secret() {
  local secret_name="$1"
  if ! az keyvault secret show --vault-name "$KEYVAULT_NAME" --name "$secret_name" --query id -o tsv >/dev/null 2>&1; then
    echo "Required Key Vault secret is missing: ${secret_name}"
    echo "Add or rotate the secret in Key Vault before deploying pipeline jobs."
    exit 1
  fi
}

sync_job_identity() {
  local job_name="$1"
  az containerapp job identity assign \
    --name "$job_name" \
    --resource-group "$RESOURCE_GROUP" \
    --user-assigned "$UAMI_ID" \
    --output none
}

job_env_value() {
  local job_name="$1"
  local key="$2"
  az containerapp job show \
    --name "$job_name" \
    --resource-group "$RESOURCE_GROUP" \
    --query "properties.template.containers[0].env[?name=='${key}'].value | [0]" \
    -o tsv 2>/dev/null || true
}

first_nonempty_job_env_value() {
  local key="$1"
  shift
  local job_name=""
  local value=""
  for job_name in "$@"; do
    [[ -z "$job_name" ]] && continue
    if ! job_exists "$job_name"; then
      continue
    fi
    value="$(job_env_value "$job_name" "$key")"
    if [[ -n "$value" && "$value" != "null" ]]; then
      printf '%s\n' "$value"
      return 0
    fi
  done
  return 0
}

hydrate_llm_env_defaults() {
  local donor_jobs=()
  local value=""
  local hydrated_keys=()
  read -r -a donor_jobs <<< "$LLM_ENV_SOURCE_JOBS"
  if ((${#donor_jobs[@]} == 0)); then
    return
  fi
  if [[ -z "$LLM_PROVIDER" ]]; then
    value="$(first_nonempty_job_env_value "LLM_PROVIDER" "${donor_jobs[@]}")"
    if [[ -n "$value" ]]; then
      LLM_PROVIDER="$value"
      hydrated_keys+=("LLM_PROVIDER")
    fi
  fi
  if [[ -z "$LLM_MODEL" ]]; then
    value="$(first_nonempty_job_env_value "LLM_MODEL" "${donor_jobs[@]}")"
    if [[ -n "$value" ]]; then
      LLM_MODEL="$value"
      hydrated_keys+=("LLM_MODEL")
    fi
  fi
  if [[ -z "$LLM_DEPLOYMENT" ]]; then
    value="$(first_nonempty_job_env_value "LLM_DEPLOYMENT" "${donor_jobs[@]}")"
    if [[ -n "$value" ]]; then
      LLM_DEPLOYMENT="$value"
      hydrated_keys+=("LLM_DEPLOYMENT")
    fi
  fi
  if [[ -z "$LLM_API_KEY_SECRET_NAME" ]]; then
    value="$(first_nonempty_job_env_value "LLM_API_KEY_SECRET_NAME" "${donor_jobs[@]}")"
    if [[ -n "$value" ]]; then
      LLM_API_KEY_SECRET_NAME="$value"
      hydrated_keys+=("LLM_API_KEY_SECRET_NAME")
    fi
  fi
  if [[ -z "$AZURE_OPENAI_ENDPOINT" ]]; then
    value="$(first_nonempty_job_env_value "AZURE_OPENAI_ENDPOINT" "${donor_jobs[@]}")"
    if [[ -n "$value" ]]; then
      AZURE_OPENAI_ENDPOINT="$value"
      hydrated_keys+=("AZURE_OPENAI_ENDPOINT")
    fi
  fi
  if [[ -z "$LLM_TEMPERATURE" ]]; then
    value="$(first_nonempty_job_env_value "LLM_TEMPERATURE" "${donor_jobs[@]}")"
    if [[ -n "$value" ]]; then
      LLM_TEMPERATURE="$value"
      hydrated_keys+=("LLM_TEMPERATURE")
    fi
  fi
  if [[ -z "$LLM_REASONING_EFFORT" ]]; then
    value="$(first_nonempty_job_env_value "LLM_REASONING_EFFORT" "${donor_jobs[@]}")"
    if [[ -n "$value" ]]; then
      LLM_REASONING_EFFORT="$value"
      hydrated_keys+=("LLM_REASONING_EFFORT")
    fi
  fi
  if [[ -z "$EMBEDDING_MODEL" ]]; then
    value="$(first_nonempty_job_env_value "EMBEDDING_MODEL" "${donor_jobs[@]}")"
    if [[ -n "$value" ]]; then
      EMBEDDING_MODEL="$value"
      hydrated_keys+=("EMBEDDING_MODEL")
    fi
  fi
  if [[ -z "$EMBEDDING_DEPLOYMENT" ]]; then
    value="$(first_nonempty_job_env_value "EMBEDDING_DEPLOYMENT" "${donor_jobs[@]}")"
    if [[ -n "$value" ]]; then
      EMBEDDING_DEPLOYMENT="$value"
      hydrated_keys+=("EMBEDDING_DEPLOYMENT")
    fi
  fi
  if ((${#hydrated_keys[@]} > 0)); then
    echo "[init] Reused LLM env from existing jobs (${LLM_ENV_SOURCE_JOBS}): ${hydrated_keys[*]}"
  fi
}

# Storage account name constraints
STORAGE_ACCOUNT="$(echo "$STORAGE_ACCOUNT" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | cut -c1-24)"
ACR_NAME="$(echo "$ACR_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | cut -c1-50)"
KEYVAULT_NAME="$(echo "$KEYVAULT_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-' | cut -c1-24)"

hydrate_llm_env_defaults

echo "[1/11] Creating resource group"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "[2/11] Ensuring Key Vault"
KEYVAULT_RESOURCE_GROUP="$(az keyvault show --name "$KEYVAULT_NAME" --query resourceGroup -o tsv 2>/dev/null || true)"
if [[ -z "$KEYVAULT_RESOURCE_GROUP" || "$KEYVAULT_RESOURCE_GROUP" == "null" ]]; then
  az keyvault create \
    --name "$KEYVAULT_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --enable-rbac-authorization true \
    --output none
  KEYVAULT_RESOURCE_GROUP="$RESOURCE_GROUP"
else
  echo "  - using existing Key Vault ${KEYVAULT_NAME} (${KEYVAULT_RESOURCE_GROUP})"
fi

echo "[3/11] Creating storage account"
if ! az storage account show --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
  az storage account create \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2 \
    --allow-blob-public-access false \
    --output none
fi

ACCOUNT_KEY="$(az storage account keys list -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query "[0].value" -o tsv)"
az storage container create --name datasets --account-name "$STORAGE_ACCOUNT" --account-key "$ACCOUNT_KEY" --output none

echo "[4/11] Creating PostgreSQL flexible server"
POSTGRES_SERVER_EXISTS=false
if az postgres flexible-server show --name "$POSTGRES_SERVER" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
  POSTGRES_SERVER_EXISTS=true
else
  if [[ -z "$POSTGRES_PASSWORD" ]]; then
    POSTGRES_PASSWORD="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)"
  fi
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
fi

if ! az postgres flexible-server db show --resource-group "$RESOURCE_GROUP" --server-name "$POSTGRES_SERVER" --database-name "$POSTGRES_DB" --output none >/dev/null 2>&1; then
  az postgres flexible-server db create \
    --resource-group "$RESOURCE_GROUP" \
    --server-name "$POSTGRES_SERVER" \
    --database-name "$POSTGRES_DB" \
    --output none
fi

echo "[5/11] Creating Log Analytics + Container Apps environment"
if ! az monitor log-analytics workspace show --resource-group "$RESOURCE_GROUP" --workspace-name "$LOG_ANALYTICS" --output none >/dev/null 2>&1; then
  az monitor log-analytics workspace create \
    --resource-group "$RESOURCE_GROUP" \
    --workspace-name "$LOG_ANALYTICS" \
    --location "$LOCATION" \
    --output none
fi

WORKSPACE_ID="$(az monitor log-analytics workspace show -g "$RESOURCE_GROUP" -n "$LOG_ANALYTICS" --query customerId -o tsv)"
WORKSPACE_KEY="$(az monitor log-analytics workspace get-shared-keys -g "$RESOURCE_GROUP" -n "$LOG_ANALYTICS" --query primarySharedKey -o tsv)"

if ! az containerapp env show --name "$ENV_NAME" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
  az containerapp env create \
    --name "$ENV_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --logs-workspace-id "$WORKSPACE_ID" \
    --logs-workspace-key "$WORKSPACE_KEY" \
    --output none
fi

echo "[6/11] Creating ACR and building pipeline image"
if ! az acr show --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" --output none >/dev/null 2>&1; then
  az acr create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACR_NAME" \
    --sku Basic \
    --location "$LOCATION" \
    --admin-enabled false \
    --output none
fi

az acr build --resource-group "$RESOURCE_GROUP" --registry "$ACR_NAME" --image "$CONTAINER_IMAGE_TAG" --file Dockerfile.pipeline .

REGISTRY_SERVER="$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query loginServer -o tsv)"
IMAGE="${REGISTRY_SERVER}/${CONTAINER_IMAGE_TAG}"

if [[ -n "$EXISTING_UAMI_RESOURCE_ID" ]]; then
  echo "[7/11] Using existing user-assigned managed identity"
  UAMI_ID="$EXISTING_UAMI_RESOURCE_ID"
  UAMI_NAME="$(basename "$UAMI_ID")"
  UAMI_SHOW_ARGS=(--ids "$UAMI_ID")
else
  echo "[7/11] Creating user-assigned managed identity"
  if ! az identity show --name "$UAMI_NAME" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
    az identity create --name "$UAMI_NAME" --resource-group "$RESOURCE_GROUP" --location "$LOCATION" --output none
  fi
  UAMI_SHOW_ARGS=(--name "$UAMI_NAME" --resource-group "$RESOURCE_GROUP")
fi

UAMI_ID="$(az identity show "${UAMI_SHOW_ARGS[@]}" --query id -o tsv)"
UAMI_PRINCIPAL_ID="$(az identity show "${UAMI_SHOW_ARGS[@]}" --query principalId -o tsv)"
UAMI_CLIENT_ID="$(az identity show "${UAMI_SHOW_ARGS[@]}" --query clientId -o tsv)"

STORAGE_ID="$(az storage account show -n "$STORAGE_ACCOUNT" -g "$RESOURCE_GROUP" --query id -o tsv)"
ACR_ID="$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)"
KEYVAULT_ID="$(az keyvault show -n "$KEYVAULT_NAME" --query id -o tsv)"

echo "[8/11] Assigning RBAC for storage, ACR, and Key Vault access"
az role assignment create --assignee-object-id "$UAMI_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Storage Blob Data Contributor" --scope "$STORAGE_ID" --output none || true
az role assignment create --assignee-object-id "$UAMI_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "AcrPull" --scope "$ACR_ID" --output none || true
az role assignment create --assignee-object-id "$UAMI_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Key Vault Secrets User" --scope "$KEYVAULT_ID" --output none || true

PG_HOST="$(az postgres flexible-server show -g "$RESOURCE_GROUP" -n "$POSTGRES_SERVER" --query fullyQualifiedDomainName -o tsv)"
STORAGE_URL="https://${STORAGE_ACCOUNT}.blob.core.windows.net"

EXISTING_PG_CONN="$(az keyvault secret show --vault-name "$KEYVAULT_NAME" --name "$POSTGRES_CONNECTION_STRING_SECRET_NAME" --query value -o tsv 2>/dev/null || true)"
if [[ "$POSTGRES_SERVER_EXISTS" == "true" && -n "$EXISTING_PG_CONN" && -z "$POSTGRES_PASSWORD" ]]; then
  PG_CONN="$EXISTING_PG_CONN"
else
  if [[ -z "$POSTGRES_PASSWORD" ]]; then
    echo "POSTGRES_PASSWORD must be provided when no existing connection string secret is available."
    exit 1
  fi
  PG_CONN="postgresql://${POSTGRES_ADMIN}:${POSTGRES_PASSWORD}@${PG_HOST}:5432/${POSTGRES_DB}?sslmode=require"
fi

echo "[9/11] Writing deployment secrets to Key Vault"
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "$POSTGRES_CONNECTION_STRING_SECRET_NAME" --value "$PG_CONN" --output none
if [[ -n "${FRED_API_KEY:-}" ]]; then
  az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "$FRED_API_KEY_SECRET_NAME" --value "$FRED_API_KEY" --output none
fi
require_keyvault_secret "$APCA_API_KEY_SECRET_NAME"
require_keyvault_secret "$APCA_API_SECRET_KEY_SECRET_NAME"

create_or_update_job () {
  local job_name="$1"
  local cron="$2"
  local timeout="$3"
  local cpu="$4"
  local memory="$5"
  shift 5

  JOB_ENV_VARS=(
    "PIPELINE_JOB_NAME=${job_name}"
    "AZURE_CLIENT_ID=${UAMI_CLIENT_ID}"
    "AZURE_KEY_VAULT_NAME=${KEYVAULT_NAME}"
    "KEY_VAULT_NAME=${KEYVAULT_NAME}"
    "AZURE_STORAGE_ACCOUNT_URL=${STORAGE_URL}"
    "AZURE_STORAGE_CONTAINER=datasets"
    "POSTGRES_CONNECTION_STRING_SECRET=${POSTGRES_CONNECTION_STRING_SECRET_NAME}"
    "POSTGRES_CONNECTION_STRING_SECRET_NAME=${POSTGRES_CONNECTION_STRING_SECRET_NAME}"
    "APCA_API_KEY_SECRET=${APCA_API_KEY_SECRET_NAME}"
    "APCA_API_KEY_SECRET_NAME=${APCA_API_KEY_SECRET_NAME}"
    "APCA_API_SECRET_KEY_SECRET=${APCA_API_SECRET_KEY_SECRET_NAME}"
    "APCA_API_SECRET_KEY_SECRET_NAME=${APCA_API_SECRET_KEY_SECRET_NAME}"
    "APCA_API_BASE_URL=${APCA_API_BASE_URL:-https://api.alpaca.markets}"
    "ALPACA_DATA_BASE_URL=${ALPACA_DATA_BASE_URL:-https://data.alpaca.markets}"
    "FRED_KEY_VAULT_SECRET=${FRED_API_KEY_SECRET_NAME}"
    "FRED_API_KEY_SECRET_NAME=${FRED_API_KEY_SECRET_NAME}"
    "UNIVERSE_VERSION=$(date +%Y%m%d)"
    "CODE_VERSION=${CONTAINER_IMAGE_TAG}"
    "IMAGE_TAG=${CONTAINER_IMAGE_TAG}"
  )

  maybe_append_env "LLM_PROVIDER" "$LLM_PROVIDER"
  maybe_append_env "LLM_MODEL" "$LLM_MODEL"
  maybe_append_env "LLM_DEPLOYMENT" "$LLM_DEPLOYMENT"
  maybe_append_env "LLM_API_KEY_SECRET_NAME" "$LLM_API_KEY_SECRET_NAME"
  maybe_append_env "AZURE_OPENAI_ENDPOINT" "$AZURE_OPENAI_ENDPOINT"
  maybe_append_env "LLM_TEMPERATURE" "$LLM_TEMPERATURE"
  maybe_append_env "LLM_REASONING_EFFORT" "$LLM_REASONING_EFFORT"
  maybe_append_env "EMBEDDING_MODEL" "$EMBEDDING_MODEL"
  maybe_append_env "EMBEDDING_DEPLOYMENT" "$EMBEDDING_DEPLOYMENT"
  maybe_append_env "PIPELINE_CACHE_MAX_BYTES" "$PIPELINE_CACHE_MAX_BYTES"
  maybe_append_env "FRED_BULK_MODE" "$FRED_BULK_MODE"
  maybe_append_env "TAVILY_INCLUDE_RAW_CONTENT" "$TAVILY_INCLUDE_RAW_CONTENT"
  maybe_append_env "ATTENTION_HOME_RESEARCH_LIMIT" "$ATTENTION_HOME_RESEARCH_LIMIT"
  maybe_append_env "ATTENTION_HOME_SEEKING_ALPHA_PAGE_LIMIT" "$ATTENTION_HOME_SEEKING_ALPHA_PAGE_LIMIT"
  maybe_append_env "ATTENTION_HOME_SEEKING_ALPHA_PAGE_MAX_CHARS" "$ATTENTION_HOME_SEEKING_ALPHA_PAGE_MAX_CHARS"
  maybe_append_env "SEEKING_ALPHA_USERNAME_SECRET_NAME" "$SEEKING_ALPHA_USERNAME_SECRET_NAME"
  maybe_append_env "SEEKING_ALPHA_PASSWORD_SECRET_NAME" "$SEEKING_ALPHA_PASSWORD_SECRET_NAME"
  maybe_append_env "SEEKING_ALPHA_BROWSER_HEADLESS" "$SEEKING_ALPHA_BROWSER_HEADLESS"
  maybe_append_env "SIMFIN_API_KEY_SECRET" "$SIMFIN_API_KEY_SECRET_NAME"
  maybe_append_env "SIMFIN_API_KEY_SECRET_NAME" "$SIMFIN_API_KEY_SECRET_NAME"

  while (($#)); do
    JOB_ENV_VARS+=("$1")
    shift
  done

  if job_exists "$job_name"; then
    echo "  - updating ${job_name} (${cron})"
    az containerapp job update \
      --name "$job_name" \
      --resource-group "$RESOURCE_GROUP" \
      --cron-expression "$cron" \
      --replica-timeout "$timeout" \
      --replica-retry-limit 1 \
      --replica-completion-count 1 \
      --parallelism 1 \
      --image "$IMAGE" \
      --container-name "$job_name" \
      --cpu "$cpu" \
      --memory "$memory" \
      --set-env-vars "${JOB_ENV_VARS[@]}" \
      --output none
    sync_job_identity "$job_name"
  else
    echo "  - creating ${job_name} (${cron})"
    az containerapp job create \
      --name "$job_name" \
      --resource-group "$RESOURCE_GROUP" \
      --environment "$ENV_NAME" \
      --trigger-type Schedule \
      --cron-expression "$cron" \
      --replica-timeout "$timeout" \
      --replica-retry-limit 1 \
      --replica-completion-count 1 \
      --parallelism 1 \
      --image "$IMAGE" \
      --registry-server "$REGISTRY_SERVER" \
      --registry-identity "$UAMI_ID" \
      --cpu "$cpu" \
      --memory "$memory" \
      --mi-user-assigned "$UAMI_ID" \
      --env-vars "${JOB_ENV_VARS[@]}" \
      --output none
    sync_job_identity "$job_name"
  fi
}

echo "[10/11] Creating scheduled jobs"
# Cron schedules are UTC.
create_or_update_job "universe-builder" "${UNIVERSE_BUILDER_CRON}" 3600 1.0 2Gi
create_or_update_job "equities-intraday-preload" "${EQUITIES_INTRADAY_PRELOAD_CRON}" 3600 1.0 2Gi
create_or_update_job "macro-fred-daily" "${MACRO_FRED_DAILY_CRON}" 3600 1.0 2Gi
create_or_update_job "commodities-regime" "${COMMODITIES_REGIME_CRON}" 3600 1.0 2Gi
create_or_update_job "options-liquid-universe" "${OPTIONS_LIQUID_UNIVERSE_CRON}" 3600 1.0 2Gi
create_or_update_job "news-ingest-and-features" "${NEWS_INGEST_AND_FEATURES_CRON}" 3600 1.0 2Gi
create_or_update_job "attention-home-build" "${ATTENTION_HOME_BUILD_CRON}" 3600 1.0 2Gi
create_or_update_job \
  "entity-taxonomy-refresh" \
  "${ENTITY_TAXONOMY_REFRESH_CRON}" \
  14400 \
  1.0 \
  2Gi \
  "TAXONOMY_INCLUDE_ETFS=true" \
  "TAXONOMY_INCLUDE_NON_COMMON=false" \
  "TAXONOMY_LISTINGS_TIMEOUT_SECONDS=60" \
  "TAXONOMY_LLM_BATCH_SIZE=25" \
  "LLM_TIMEOUT_SECONDS=180"
create_or_update_job \
  "fundamentals-quarterly-refresh" \
  "${FUNDAMENTALS_QUARTERLY_REFRESH_CRON}" \
  3600 \
  0.5 \
  1Gi \
  "SIMFIN_REFRESH_ENABLED=true"

echo "[11/11] Capturing deployment outputs"
mkdir -p "$(dirname "$DEPLOY_OUTPUTS_FILE")"
cat > "$DEPLOY_OUTPUTS_FILE" <<EOF
RESOURCE_GROUP=${RESOURCE_GROUP}
PIPELINE_RESOURCE_GROUP=${RESOURCE_GROUP}
LOCATION=${LOCATION}
CONTAINERAPPS_ENV=${ENV_NAME}
KEYVAULT_NAME=${KEYVAULT_NAME}
KEYVAULT_RESOURCE_GROUP=${KEYVAULT_RESOURCE_GROUP}
AZURE_KEY_VAULT_NAME=${KEYVAULT_NAME}
KEY_VAULT_NAME=${KEYVAULT_NAME}
STORAGE_ACCOUNT=${STORAGE_ACCOUNT}
STORAGE_URL=${STORAGE_URL}
AZURE_STORAGE_ACCOUNT_URL=${STORAGE_URL}
AZURE_STORAGE_CONTAINER=datasets
ACR_NAME=${ACR_NAME}
POSTGRES_SERVER=${POSTGRES_SERVER}
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_ADMIN=${POSTGRES_ADMIN}
MANAGED_IDENTITY=${UAMI_NAME}
LLM_API_KEY_SECRET_NAME=${LLM_API_KEY_SECRET_NAME}
LLM_PROVIDER=${LLM_PROVIDER}
LLM_MODEL=${LLM_MODEL}
LLM_DEPLOYMENT=${LLM_DEPLOYMENT}
AZURE_OPENAI_ENDPOINT=${AZURE_OPENAI_ENDPOINT}
LLM_TEMPERATURE=${LLM_TEMPERATURE}
LLM_REASONING_EFFORT=${LLM_REASONING_EFFORT}
EOF

echo "Deployment complete"
echo "Saved local outputs: ${DEPLOY_OUTPUTS_FILE}"
echo "Portal RG: https://portal.azure.com/#@/resource/subscriptions/$(az account show --query id -o tsv)/resourceGroups/${RESOURCE_GROUP}/overview"
