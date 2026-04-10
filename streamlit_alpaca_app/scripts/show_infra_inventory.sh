#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DEFAULT_DEPLOYMENT_ENV_FILE="infra/.generated/deployment.local.env"
DEFAULT_EMAIL_OUTPUTS_FILE="infra/.generated/email_delivery.local.env"
LEGACY_DEPLOYMENT_ENV_FILE="infra/deployment.outputs.env"
LEGACY_EMAIL_OUTPUTS_FILE="infra/email_delivery.outputs.env"
CONTAINERAPP_API_VERSION="${CONTAINERAPP_API_VERSION:-2025-07-01}"
DEV_CONTAINER_APP="${DEV_CONTAINER_APP:-sn-streamlit-ui-dev}"
PROD_CONTAINER_APP="${PROD_CONTAINER_APP:-sn-streamlit-ui}"
TARGET="${TARGET:-dev}"
WRITE_LOCAL=false
RESOURCE_GROUP="${RESOURCE_GROUP:-${PIPELINE_RESOURCE_GROUP:-}}"
PIPELINE_RESOURCE_GROUP="${PIPELINE_RESOURCE_GROUP:-${RESOURCE_GROUP:-}}"
DEPLOYMENT_ENV_FILE="${DEPLOYMENT_ENV_FILE:-$DEFAULT_DEPLOYMENT_ENV_FILE}"
EMAIL_OUTPUTS_FILE="${EMAIL_OUTPUTS_FILE:-$DEFAULT_EMAIL_OUTPUTS_FILE}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/show_infra_inventory.sh [options]

Options:
  --target dev|prod    Target app used when writing email local outputs. Default: dev
  --resource-group RG  Override the resource group
  --write-local        Refresh infra/.generated/*.local.env from available local/live values
  --help               Show this message
EOF
}

log() {
  printf '%s\n' "$*"
}

resolve_path() {
  local path="$1"
  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s/%s\n' "$ROOT_DIR" "$path"
  fi
}

load_env_file() {
  local path="$1"
  [[ -f "$path" ]] || return 0
  set -a
  # shellcheck disable=SC1090
  source "$path"
  set +a
}

load_local_context() {
  local deployment_file
  local email_file
  deployment_file="$(resolve_path "$DEPLOYMENT_ENV_FILE")"
  email_file="$(resolve_path "$EMAIL_OUTPUTS_FILE")"

  if [[ -f "$deployment_file" ]]; then
    load_env_file "$deployment_file"
  elif [[ "$DEPLOYMENT_ENV_FILE" == "$DEFAULT_DEPLOYMENT_ENV_FILE" && -f "$(resolve_path "$LEGACY_DEPLOYMENT_ENV_FILE")" ]]; then
    load_env_file "$(resolve_path "$LEGACY_DEPLOYMENT_ENV_FILE")"
  fi

  if [[ -f "$email_file" ]]; then
    load_env_file "$email_file"
  elif [[ "$EMAIL_OUTPUTS_FILE" == "$DEFAULT_EMAIL_OUTPUTS_FILE" && -f "$(resolve_path "$LEGACY_EMAIL_OUTPUTS_FILE")" ]]; then
    load_env_file "$(resolve_path "$LEGACY_EMAIL_OUTPUTS_FILE")"
  fi

  RESOURCE_GROUP="${RESOURCE_GROUP:-${PIPELINE_RESOURCE_GROUP:-}}"
  PIPELINE_RESOURCE_GROUP="${PIPELINE_RESOURCE_GROUP:-${RESOURCE_GROUP:-}}"
}

azure_ready() {
  command -v az >/dev/null 2>&1 && az account show >/dev/null 2>&1
}

containerapp_exists() {
  local app_name="$1"
  az resource show \
    -n "$app_name" \
    -g "$RESOURCE_GROUP" \
    --resource-type Microsoft.App/containerApps \
    --api-version "$CONTAINERAPP_API_VERSION" \
    --output none >/dev/null 2>&1
}

containerapp_query() {
  local app_name="$1"
  local query="$2"
  az resource show \
    -n "$app_name" \
    -g "$RESOURCE_GROUP" \
    --resource-type Microsoft.App/containerApps \
    --api-version "$CONTAINERAPP_API_VERSION" \
    --query "$query" \
    -o tsv 2>/dev/null || true
}

app_env_value() {
  local app_name="$1"
  local key="$2"
  containerapp_query "$app_name" "properties.template.containers[0].env[?name=='${key}'].value | [0]"
}

target_app_name() {
  case "$1" in
    dev) printf '%s\n' "$DEV_CONTAINER_APP" ;;
    prod) printf '%s\n' "$PROD_CONTAINER_APP" ;;
    *) printf '%s\n' "$DEV_CONTAINER_APP" ;;
  esac
}

discover_resource_group() {
  if [[ -n "$RESOURCE_GROUP" ]]; then
    PIPELINE_RESOURCE_GROUP="${PIPELINE_RESOURCE_GROUP:-$RESOURCE_GROUP}"
    return 0
  fi
  azure_ready || return 0
  local app_name=""
  local discovered=""
  for app_name in "$DEV_CONTAINER_APP" "$PROD_CONTAINER_APP"; do
    discovered="$(az resource list --name "$app_name" --resource-type Microsoft.App/containerApps --query "[0].resourceGroup" -o tsv 2>/dev/null || true)"
    if [[ -n "$discovered" && "$discovered" != "null" ]]; then
      RESOURCE_GROUP="$discovered"
      PIPELINE_RESOURCE_GROUP="$discovered"
      return 0
    fi
  done
}

discover_pipeline_context() {
  azure_ready || return 0
  [[ -n "$RESOURCE_GROUP" ]] || return 0

  ACR_NAME="${ACR_NAME:-$(az acr list -g "$RESOURCE_GROUP" --query '[0].name' -o tsv 2>/dev/null || true)}"
  STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-$(az storage account list -g "$RESOURCE_GROUP" --query '[0].name' -o tsv 2>/dev/null || true)}"
  if [[ -z "${STORAGE_URL:-}" && -n "$STORAGE_ACCOUNT" ]]; then
    STORAGE_URL="$(az storage account show -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query 'primaryEndpoints.blob' -o tsv 2>/dev/null || true)"
  fi
  AZURE_STORAGE_ACCOUNT_URL="${AZURE_STORAGE_ACCOUNT_URL:-${STORAGE_URL:-}}"
  CONTAINERAPPS_ENV="${CONTAINERAPPS_ENV:-$(az resource list -g "$RESOURCE_GROUP" --resource-type Microsoft.App/managedEnvironments --query '[0].name' -o tsv 2>/dev/null || true)}"
  POSTGRES_SERVER="${POSTGRES_SERVER:-$(az postgres flexible-server list -g "$RESOURCE_GROUP" --query '[0].name' -o tsv 2>/dev/null || true)}"
  if [[ -z "${POSTGRES_ADMIN:-}" && -n "$POSTGRES_SERVER" ]]; then
    POSTGRES_ADMIN="$(az postgres flexible-server show -g "$RESOURCE_GROUP" -n "$POSTGRES_SERVER" --query 'administratorLogin' -o tsv 2>/dev/null || true)"
  fi
  POSTGRES_DB="${POSTGRES_DB:-pipeline}"
  MANAGED_IDENTITY="${MANAGED_IDENTITY:-$(az identity list -g "$RESOURCE_GROUP" --query '[0].name' -o tsv 2>/dev/null || true)}"
  if [[ -z "${KEYVAULT_NAME:-}" || "$KEYVAULT_NAME" == "null" ]]; then
    local vault_name=""
    while IFS= read -r vault_name; do
      [[ -n "$vault_name" ]] || continue
      if az keyvault secret show --vault-name "$vault_name" --name "postgres-connection-string" --query id -o tsv >/dev/null 2>&1; then
        KEYVAULT_NAME="$vault_name"
        break
      fi
    done < <(az keyvault list -g "$RESOURCE_GROUP" --query '[].name' -o tsv 2>/dev/null || true)
  fi
  AZURE_KEY_VAULT_NAME="${AZURE_KEY_VAULT_NAME:-${KEYVAULT_NAME:-}}"
  KEY_VAULT_NAME="${KEY_VAULT_NAME:-${KEYVAULT_NAME:-}}"

  if containerapp_exists "$DEV_CONTAINER_APP"; then
    LLM_API_KEY_SECRET_NAME="${LLM_API_KEY_SECRET_NAME:-$(app_env_value "$DEV_CONTAINER_APP" 'LLM_API_KEY_SECRET_NAME')}"
    LLM_PROVIDER="${LLM_PROVIDER:-$(app_env_value "$DEV_CONTAINER_APP" 'LLM_PROVIDER')}"
    LLM_MODEL="${LLM_MODEL:-$(app_env_value "$DEV_CONTAINER_APP" 'LLM_MODEL')}"
    LLM_DEPLOYMENT="${LLM_DEPLOYMENT:-$(app_env_value "$DEV_CONTAINER_APP" 'LLM_DEPLOYMENT')}"
    AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-$(app_env_value "$DEV_CONTAINER_APP" 'AZURE_OPENAI_ENDPOINT')}"
    LLM_TEMPERATURE="${LLM_TEMPERATURE:-$(app_env_value "$DEV_CONTAINER_APP" 'LLM_TEMPERATURE')}"
    LLM_REASONING_EFFORT="${LLM_REASONING_EFFORT:-$(app_env_value "$DEV_CONTAINER_APP" 'LLM_REASONING_EFFORT')}"
  fi
}

discover_email_context() {
  azure_ready || return 0
  [[ -n "$RESOURCE_GROUP" ]] || return 0
  local app_name
  app_name="$(target_app_name "$TARGET")"
  containerapp_exists "$app_name" || return 0

  TARGET_APP="$app_name"
  UI_KEY_VAULT_NAME="$(app_env_value "$app_name" 'AZURE_KEY_VAULT_NAME')"
  if [[ -z "$UI_KEY_VAULT_NAME" || "$UI_KEY_VAULT_NAME" == "null" ]]; then
    UI_KEY_VAULT_NAME="$(app_env_value "$app_name" 'KEY_VAULT_NAME')"
  fi
  APP_PUBLIC_BASE_URL="${APP_PUBLIC_BASE_URL:-$(app_env_value "$app_name" 'APP_PUBLIC_BASE_URL')}"
  APP_SMTP_HOST="${APP_SMTP_HOST:-$(app_env_value "$app_name" 'APP_SMTP_HOST')}"
  APP_SMTP_PORT="${APP_SMTP_PORT:-$(app_env_value "$app_name" 'APP_SMTP_PORT')}"
  APP_SMTP_USE_TLS="${APP_SMTP_USE_TLS:-$(app_env_value "$app_name" 'APP_SMTP_USE_TLS')}"
  APP_SMTP_USE_SSL="${APP_SMTP_USE_SSL:-$(app_env_value "$app_name" 'APP_SMTP_USE_SSL')}"
  APP_SMTP_USERNAME_SECRET="${APP_SMTP_USERNAME_SECRET:-$(app_env_value "$app_name" 'APP_SMTP_USERNAME_SECRET')}"
  APP_SMTP_PASSWORD_SECRET="${APP_SMTP_PASSWORD_SECRET:-$(app_env_value "$app_name" 'APP_SMTP_PASSWORD_SECRET')}"
  APP_EMAIL_FROM_SECRET="${APP_EMAIL_FROM_SECRET:-$(app_env_value "$app_name" 'APP_EMAIL_FROM_SECRET')}"
}

write_deployment_local_file() {
  local path
  path="$(resolve_path "$DEPLOYMENT_ENV_FILE")"
  mkdir -p "$(dirname "$path")"
  cat > "$path" <<EOF
RESOURCE_GROUP=${RESOURCE_GROUP}
PIPELINE_RESOURCE_GROUP=${PIPELINE_RESOURCE_GROUP:-$RESOURCE_GROUP}
LOCATION=${LOCATION:-}
CONTAINERAPPS_ENV=${CONTAINERAPPS_ENV:-}
KEYVAULT_NAME=${KEYVAULT_NAME:-}
AZURE_KEY_VAULT_NAME=${AZURE_KEY_VAULT_NAME:-${KEYVAULT_NAME:-}}
KEY_VAULT_NAME=${KEY_VAULT_NAME:-${KEYVAULT_NAME:-}}
STORAGE_ACCOUNT=${STORAGE_ACCOUNT:-}
STORAGE_URL=${STORAGE_URL:-${AZURE_STORAGE_ACCOUNT_URL:-}}
AZURE_STORAGE_ACCOUNT_URL=${AZURE_STORAGE_ACCOUNT_URL:-${STORAGE_URL:-}}
AZURE_STORAGE_CONTAINER=${AZURE_STORAGE_CONTAINER:-datasets}
ACR_NAME=${ACR_NAME:-}
POSTGRES_SERVER=${POSTGRES_SERVER:-}
POSTGRES_DB=${POSTGRES_DB:-pipeline}
POSTGRES_ADMIN=${POSTGRES_ADMIN:-}
MANAGED_IDENTITY=${MANAGED_IDENTITY:-}
LLM_API_KEY_SECRET_NAME=${LLM_API_KEY_SECRET_NAME:-}
LLM_PROVIDER=${LLM_PROVIDER:-}
LLM_MODEL=${LLM_MODEL:-}
LLM_DEPLOYMENT=${LLM_DEPLOYMENT:-}
AZURE_OPENAI_ENDPOINT=${AZURE_OPENAI_ENDPOINT:-}
LLM_TEMPERATURE=${LLM_TEMPERATURE:-}
LLM_REASONING_EFFORT=${LLM_REASONING_EFFORT:-}
EOF
  log "Wrote ${path}"
}

write_email_local_file() {
  local path
  path="$(resolve_path "$EMAIL_OUTPUTS_FILE")"
  mkdir -p "$(dirname "$path")"
  cat > "$path" <<EOF
RESOURCE_GROUP=${RESOURCE_GROUP}
PIPELINE_RESOURCE_GROUP=${PIPELINE_RESOURCE_GROUP:-$RESOURCE_GROUP}
TARGET_APP=${TARGET_APP:-$(target_app_name "$TARGET")}
APP_PUBLIC_BASE_URL=${APP_PUBLIC_BASE_URL:-}
APP_SMTP_HOST=${APP_SMTP_HOST:-}
APP_SMTP_PORT=${APP_SMTP_PORT:-}
APP_SMTP_USE_TLS=${APP_SMTP_USE_TLS:-}
APP_SMTP_USE_SSL=${APP_SMTP_USE_SSL:-}
APP_SMTP_USERNAME_SECRET=${APP_SMTP_USERNAME_SECRET:-}
APP_SMTP_PASSWORD_SECRET=${APP_SMTP_PASSWORD_SECRET:-}
APP_EMAIL_FROM_SECRET=${APP_EMAIL_FROM_SECRET:-}
EOF
  log "Wrote ${path}"
}

print_file_status() {
  local label="$1"
  local path="$2"
  if [[ -f "$path" ]]; then
    printf '%s: %s\n' "$label" "$path"
  else
    printf '%s: missing (%s)\n' "$label" "$path"
  fi
}

print_ui_app_summary() {
  local app_name="$1"
  if ! azure_ready || [[ -z "$RESOURCE_GROUP" ]] || ! containerapp_exists "$app_name"; then
    printf 'UI %s: unavailable\n' "$app_name"
    return 0
  fi
  local fqdn
  local ready_revision
  local public_base_url
  local key_vault_name
  fqdn="$(containerapp_query "$app_name" 'properties.configuration.ingress.fqdn')"
  ready_revision="$(containerapp_query "$app_name" 'properties.latestReadyRevisionName')"
  public_base_url="$(app_env_value "$app_name" 'APP_PUBLIC_BASE_URL')"
  key_vault_name="$(app_env_value "$app_name" 'AZURE_KEY_VAULT_NAME')"
  if [[ -z "$key_vault_name" || "$key_vault_name" == "null" ]]; then
    key_vault_name="$(app_env_value "$app_name" 'KEY_VAULT_NAME')"
  fi
  printf 'UI %s:\n' "$app_name"
  printf '  fqdn=%s\n' "${fqdn:-}"
  printf '  latest_ready_revision=%s\n' "${ready_revision:-}"
  printf '  app_public_base_url=%s\n' "${public_base_url:-}"
  printf '  key_vault=%s\n' "${key_vault_name:-}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      TARGET="${2:-}"
      shift 2
      ;;
    --resource-group)
      RESOURCE_GROUP="${2:-}"
      PIPELINE_RESOURCE_GROUP="${RESOURCE_GROUP:-}"
      shift 2
      ;;
    --write-local)
      WRITE_LOCAL=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

load_local_context
discover_resource_group
discover_pipeline_context
discover_email_context

log "Tracked index"
log "  documents/infra/RESOURCE_INDEX.md"
log "  documents/infra/UI_DEPLOYMENT_STATUS.md"
log ""
log "Local generated files"
print_file_status "  deployment" "$(resolve_path "$DEPLOYMENT_ENV_FILE")"
print_file_status "  email" "$(resolve_path "$EMAIL_OUTPUTS_FILE")"
log ""
log "Pipeline context"
printf '  resource_group=%s\n' "${RESOURCE_GROUP:-}"
printf '  key_vault=%s\n' "${KEYVAULT_NAME:-}"
printf '  storage_account=%s\n' "${STORAGE_ACCOUNT:-}"
printf '  storage_url=%s\n' "${AZURE_STORAGE_ACCOUNT_URL:-${STORAGE_URL:-}}"
printf '  acr_name=%s\n' "${ACR_NAME:-}"
printf '  postgres_server=%s\n' "${POSTGRES_SERVER:-}"
printf '  managed_identity=%s\n' "${MANAGED_IDENTITY:-}"
log ""
log "UI apps"
print_ui_app_summary "$DEV_CONTAINER_APP"
print_ui_app_summary "$PROD_CONTAINER_APP"

if [[ "$WRITE_LOCAL" == "true" ]]; then
  write_deployment_local_file
  write_email_local_file
fi
