#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DEPLOYMENT_ENV_FILE="${DEPLOYMENT_ENV_FILE:-infra/deployment.outputs.env}"
EMAIL_OUTPUTS_FILE="${EMAIL_OUTPUTS_FILE:-infra/email_delivery.outputs.env}"
TARGET="${TARGET:-dev}"
TEST_TO="${TEST_TO:-}"
ROTATE_CLIENT_SECRET="${ROTATE_CLIENT_SECRET:-false}"
RESOURCE_GROUP="${RESOURCE_GROUP:-}"
KEYVAULT_NAME="${KEYVAULT_NAME:-}"
KEYVAULT_NAME_SOURCE="auto"
DATA_LOCATION="${DATA_LOCATION:-unitedstates}"
LOCATION="${LOCATION:-global}"
EMAIL_DOMAIN_NAME="${EMAIL_DOMAIN_NAME:-AzureManagedDomain}"
SENDER_USERNAME="${SENDER_USERNAME:-noreply}"
SENDER_DISPLAY_NAME="${SENDER_DISPLAY_NAME:-Spectral Nature}"
ENTRA_APP_DISPLAY_NAME="${ENTRA_APP_DISPLAY_NAME:-sn-ui-email-smtp}"
SMTP_AUTH_RESOURCE_NAME="${SMTP_AUTH_RESOURCE_NAME:-spectral-ui-smtp-resource}"
SMTP_AUTH_USERNAME="${SMTP_AUTH_USERNAME:-spectraluismtp}"
SMTP_USERNAME_SECRET_NAME="${SMTP_USERNAME_SECRET_NAME:-app-smtp-username}"
SMTP_PASSWORD_SECRET_NAME="${SMTP_PASSWORD_SECRET_NAME:-app-smtp-password}"
EMAIL_FROM_SECRET_NAME="${EMAIL_FROM_SECRET_NAME:-app-email-from}"
SMTP_HOST="${SMTP_HOST:-smtp.azurecomm.net}"
SMTP_PORT="${SMTP_PORT:-587}"
SMTP_USE_TLS="${SMTP_USE_TLS:-true}"
SMTP_USE_SSL="${SMTP_USE_SSL:-false}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-300}"
WAIT_INTERVAL_SECONDS="${WAIT_INTERVAL_SECONDS:-5}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/setup_ui_email_delivery_azure.sh [options]

Options:
  --target dev|prod             Update the selected UI app env. Default: dev
  --test-to EMAIL               Send an SMTP test message after setup
  --rotate-client-secret        Rotate the Entra app client secret before storing it in Key Vault
  --resource-group NAME         Resource group containing the UI and communication resources
  --keyvault-name NAME          Key Vault used by the UI app
  --help                        Show this message

Notes:
  - Provisions Azure Communication Services Email + Communication resources when absent.
  - Creates an Azure-managed sender domain and a sender username for invites/reset mail.
  - Creates or reuses an Entra app for SMTP auth and stores the resulting secrets in Key Vault.
  - Updates the selected Container App with the SMTP env vars expected by services/emailer.py.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

log() {
  echo "$*"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

target_app_name() {
  case "$1" in
    dev) echo "sn-streamlit-ui-dev" ;;
    prod) echo "sn-streamlit-ui" ;;
    *) die "Unsupported target environment: $1" ;;
  esac
}

app_exists() {
  local app_name="$1"
  az containerapp show -n "$app_name" -g "$RESOURCE_GROUP" --output none >/dev/null 2>&1
}

app_env_value() {
  local app_name="$1"
  local key="$2"
  az containerapp show \
    -n "$app_name" \
    -g "$RESOURCE_GROUP" \
    --query "properties.template.containers[0].env[?name=='${key}'].value | [0]" \
    -o tsv 2>/dev/null || true
}

app_fqdn() {
  local app_name="$1"
  az containerapp show -n "$app_name" -g "$RESOURCE_GROUP" --query "properties.configuration.ingress.fqdn" -o tsv
}

root_http_code_for_url() {
  local url="$1"
  local code
  code="$(curl -sS -L -o /dev/null -w "%{http_code}" --max-time 30 "$url" || true)"
  echo "${code:-000}"
}

wait_for_ready_revision() {
  local app_name="$1"
  local attempts=$(( (WAIT_TIMEOUT_SECONDS + WAIT_INTERVAL_SECONDS - 1) / WAIT_INTERVAL_SECONDS ))
  local latest_revision=""
  local ready_revision=""

  for ((i=1; i<=attempts; i++)); do
    latest_revision="$(az containerapp show -n "$app_name" -g "$RESOURCE_GROUP" --query "properties.latestRevisionName" -o tsv)"
    ready_revision="$(az containerapp show -n "$app_name" -g "$RESOURCE_GROUP" --query "properties.latestReadyRevisionName" -o tsv)"
    if [[ -n "$latest_revision" && "$latest_revision" == "$ready_revision" ]]; then
      echo "$ready_revision"
      return 0
    fi
    sleep "$WAIT_INTERVAL_SECONDS"
  done

  die "Timed out waiting for $app_name to make the latest revision ready."
}

smoke_check_app() {
  local app_name="$1"
  local fqdn
  fqdn="$(app_fqdn "$app_name")"
  [[ -n "$fqdn" ]] || die "Missing ingress FQDN for $app_name"

  local url="https://${fqdn}"
  local health
  health="$(curl -fsS --max-time 30 "${url}/_stcore/health")"
  [[ "$health" == "ok" ]] || die "Health check failed for $app_name (${url}/_stcore/health)"

  local http_code
  http_code="$(root_http_code_for_url "$url")"
  case "$http_code" in
    200|302|401) ;;
    *) die "Unexpected root HTTP status for $app_name: $http_code" ;;
  esac

  echo "$http_code"
}

load_deployment_context() {
  local requested_resource_group="$RESOURCE_GROUP"
  local requested_keyvault_name="$KEYVAULT_NAME"
  local requested_keyvault_source="$KEYVAULT_NAME_SOURCE"

  if [[ -f "$DEPLOYMENT_ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$DEPLOYMENT_ENV_FILE"
  fi

  if [[ -n "$requested_resource_group" ]]; then
    RESOURCE_GROUP="$requested_resource_group"
  fi
  if [[ "$requested_keyvault_source" == "cli" ]]; then
    KEYVAULT_NAME="$requested_keyvault_name"
    KEYVAULT_NAME_SOURCE="$requested_keyvault_source"
  fi
}

resolve_suffix() {
  if [[ "$RESOURCE_GROUP" =~ ([0-9]{8})$ ]]; then
    echo "${BASH_REMATCH[1]}"
  else
    echo "shared"
  fi
}

ensure_azure_ready() {
  require_command az
  require_command curl
  require_command python

  if ! az account show >/dev/null 2>&1; then
    die "Azure CLI is not authenticated. Run: az login --use-device-code"
  fi

  az extension add --name communication --upgrade >/dev/null
  az extension add --name containerapp --upgrade >/dev/null
}

ensure_target_context() {
  TARGET_APP="$(target_app_name "$TARGET")"
  [[ -n "$RESOURCE_GROUP" ]] || die "RESOURCE_GROUP is required."
  app_exists "$TARGET_APP" || die "Container App not found: $TARGET_APP"

  if [[ "$KEYVAULT_NAME_SOURCE" != "cli" ]]; then
    KEYVAULT_NAME="$(app_env_value "$TARGET_APP" "AZURE_KEY_VAULT_NAME")"
    if [[ -z "$KEYVAULT_NAME" || "$KEYVAULT_NAME" == "null" ]]; then
      KEYVAULT_NAME="$(app_env_value "$TARGET_APP" "KEY_VAULT_NAME")"
    fi
  fi
  [[ -n "$KEYVAULT_NAME" ]] || die "Unable to determine KEYVAULT_NAME from the target app."

  PUBLIC_BASE_URL="$(app_env_value "$TARGET_APP" "APP_PUBLIC_BASE_URL")"
  if [[ -z "$PUBLIC_BASE_URL" || "$PUBLIC_BASE_URL" == "null" ]]; then
    PUBLIC_BASE_URL="https://$(app_fqdn "$TARGET_APP")"
  fi

  RESOURCE_SUFFIX="$(resolve_suffix)"
  EMAIL_SERVICE_NAME="${EMAIL_SERVICE_NAME:-sn-email-${RESOURCE_SUFFIX}}"
  COMMUNICATION_SERVICE_NAME="${COMMUNICATION_SERVICE_NAME:-sn-comm-${RESOURCE_SUFFIX}}"
}

ensure_email_service() {
  if ! az communication email show -g "$RESOURCE_GROUP" -n "$EMAIL_SERVICE_NAME" --output none >/dev/null 2>&1; then
    log "[1/8] Creating Email Service ${EMAIL_SERVICE_NAME}"
    az communication email create \
      -g "$RESOURCE_GROUP" \
      -n "$EMAIL_SERVICE_NAME" \
      --location "$LOCATION" \
      --data-location "$DATA_LOCATION" \
      --output none
  else
    log "[1/8] Reusing Email Service ${EMAIL_SERVICE_NAME}"
  fi
}

ensure_email_domain() {
  if ! az communication email domain show -g "$RESOURCE_GROUP" --email-service-name "$EMAIL_SERVICE_NAME" --domain-name "$EMAIL_DOMAIN_NAME" --output none >/dev/null 2>&1; then
    log "[2/8] Creating Azure-managed domain ${EMAIL_DOMAIN_NAME}"
    az communication email domain create \
      -g "$RESOURCE_GROUP" \
      --email-service-name "$EMAIL_SERVICE_NAME" \
      --domain-name "$EMAIL_DOMAIN_NAME" \
      --location "$LOCATION" \
      --domain-management AzureManaged \
      --user-engmnt-tracking Disabled \
      --output none
  else
    log "[2/8] Reusing domain ${EMAIL_DOMAIN_NAME}"
  fi

  DOMAIN_ID="$(az communication email domain show -g "$RESOURCE_GROUP" --email-service-name "$EMAIL_SERVICE_NAME" --domain-name "$EMAIL_DOMAIN_NAME" --query id -o tsv)"
  FROM_SENDER_DOMAIN="$(az communication email domain show -g "$RESOURCE_GROUP" --email-service-name "$EMAIL_SERVICE_NAME" --domain-name "$EMAIL_DOMAIN_NAME" --query fromSenderDomain -o tsv)"
  if [[ -z "$FROM_SENDER_DOMAIN" || "$FROM_SENDER_DOMAIN" == "null" ]]; then
    FROM_SENDER_DOMAIN="$(az communication email domain show -g "$RESOURCE_GROUP" --email-service-name "$EMAIL_SERVICE_NAME" --domain-name "$EMAIL_DOMAIN_NAME" --query mailFromSenderDomain -o tsv)"
  fi
  [[ -n "$FROM_SENDER_DOMAIN" && "$FROM_SENDER_DOMAIN" != "null" ]] || die "Unable to resolve the Azure-managed sender domain."
}

ensure_communication_service() {
  if ! az communication show -g "$RESOURCE_GROUP" -n "$COMMUNICATION_SERVICE_NAME" --output none >/dev/null 2>&1; then
    log "[3/8] Creating Communication Service ${COMMUNICATION_SERVICE_NAME}"
    az communication create \
      -g "$RESOURCE_GROUP" \
      -n "$COMMUNICATION_SERVICE_NAME" \
      --location "$LOCATION" \
      --data-location "$DATA_LOCATION" \
      --linked-domains "$DOMAIN_ID" \
      --output none
  else
    log "[3/8] Ensuring Communication Service ${COMMUNICATION_SERVICE_NAME} is linked to the email domain"
    az communication update \
      -g "$RESOURCE_GROUP" \
      -n "$COMMUNICATION_SERVICE_NAME" \
      --linked-domains "$DOMAIN_ID" \
      --output none
  fi

  COMMUNICATION_SERVICE_ID="$(az communication show -g "$RESOURCE_GROUP" -n "$COMMUNICATION_SERVICE_NAME" --query id -o tsv)"
}

ensure_sender_username() {
  if ! az communication email domain sender-username show -g "$RESOURCE_GROUP" --email-service-name "$EMAIL_SERVICE_NAME" --domain-name "$EMAIL_DOMAIN_NAME" --sender-username "$SENDER_USERNAME" --output none >/dev/null 2>&1; then
    log "[4/8] Creating sender username ${SENDER_USERNAME}"
    az communication email domain sender-username create \
      -g "$RESOURCE_GROUP" \
      --email-service-name "$EMAIL_SERVICE_NAME" \
      --domain-name "$EMAIL_DOMAIN_NAME" \
      --sender-username "$SENDER_USERNAME" \
      --username "$SENDER_USERNAME" \
      --display-name "$SENDER_DISPLAY_NAME" \
      --output none
  else
    log "[4/8] Reusing sender username ${SENDER_USERNAME}"
  fi

  FROM_ADDRESS="${SENDER_USERNAME}@${FROM_SENDER_DOMAIN}"
}

ensure_entra_app() {
  ENTRA_APP_ID="$(az ad app list --display-name "$ENTRA_APP_DISPLAY_NAME" --query "[0].appId" -o tsv)"
  if [[ -z "$ENTRA_APP_ID" || "$ENTRA_APP_ID" == "null" ]]; then
    log "[5/8] Creating Entra app ${ENTRA_APP_DISPLAY_NAME}"
    ENTRA_APP_ID="$(az ad app create --display-name "$ENTRA_APP_DISPLAY_NAME" --sign-in-audience AzureADMyOrg --query appId -o tsv)"
  else
    log "[5/8] Reusing Entra app ${ENTRA_APP_DISPLAY_NAME}"
  fi

  ENTRA_SP_ID="$(az ad sp show --id "$ENTRA_APP_ID" --query id -o tsv 2>/dev/null || true)"
  if [[ -z "$ENTRA_SP_ID" || "$ENTRA_SP_ID" == "null" ]]; then
    ENTRA_SP_ID="$(az ad sp create --id "$ENTRA_APP_ID" --query id -o tsv)"
  fi

  TENANT_ID="$(az account show --query tenantId -o tsv)"
}

ensure_role_assignment() {
  log "[6/8] Ensuring SMTP auth principal can manage communication email resources"
  az role assignment create \
    --assignee-object-id "$ENTRA_SP_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "Communication and Email Service Owner" \
    --scope "$COMMUNICATION_SERVICE_ID" \
    --output none >/dev/null 2>&1 || true
}

ensure_smtp_auth_username() {
  log "[7/8] Creating or updating SMTP auth username ${SMTP_AUTH_USERNAME}"
  az rest \
    --method put \
    --url "https://management.azure.com/subscriptions/$(az account show --query id -o tsv)/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Communication/communicationServices/${COMMUNICATION_SERVICE_NAME}/smtpUsernames/${SMTP_AUTH_RESOURCE_NAME}?api-version=2025-09-01" \
    --body "{\"properties\":{\"username\":\"${SMTP_AUTH_USERNAME}\",\"entraApplicationId\":\"${ENTRA_APP_ID}\",\"tenantId\":\"${TENANT_ID}\"}}" \
    --output none
}

write_keyvault_secrets() {
  log "[8/8] Writing SMTP secrets into ${KEYVAULT_NAME}"

  SMTP_PASSWORD_VALUE=""
  if [[ "$ROTATE_CLIENT_SECRET" != "true" ]]; then
    SMTP_PASSWORD_VALUE="$(az keyvault secret show --vault-name "$KEYVAULT_NAME" --name "$SMTP_PASSWORD_SECRET_NAME" --query value -o tsv 2>/dev/null || true)"
  fi
  if [[ -z "$SMTP_PASSWORD_VALUE" ]]; then
    SMTP_PASSWORD_VALUE="$(az ad app credential reset --id "$ENTRA_APP_ID" --append --display-name "$SMTP_AUTH_RESOURCE_NAME" --query password -o tsv)"
  fi

  az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "$SMTP_USERNAME_SECRET_NAME" --value "$SMTP_AUTH_USERNAME" --output none
  az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "$SMTP_PASSWORD_SECRET_NAME" --value "$SMTP_PASSWORD_VALUE" --output none
  az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "$EMAIL_FROM_SECRET_NAME" --value "$FROM_ADDRESS" --output none
}

update_container_app_env() {
  log "Updating ${TARGET_APP} with SMTP env vars"
  az containerapp update \
    -g "$RESOURCE_GROUP" \
    -n "$TARGET_APP" \
    --set-env-vars \
      APP_PUBLIC_BASE_URL="$PUBLIC_BASE_URL" \
      APP_SMTP_HOST="$SMTP_HOST" \
      APP_SMTP_PORT="$SMTP_PORT" \
      APP_SMTP_USE_TLS="$SMTP_USE_TLS" \
      APP_SMTP_USE_SSL="$SMTP_USE_SSL" \
      APP_SMTP_USERNAME_SECRET="$SMTP_USERNAME_SECRET_NAME" \
      APP_SMTP_PASSWORD_SECRET="$SMTP_PASSWORD_SECRET_NAME" \
      APP_EMAIL_FROM_SECRET="$EMAIL_FROM_SECRET_NAME" \
    --output none
}

write_outputs_file() {
  cat > "$EMAIL_OUTPUTS_FILE" <<EOF
RESOURCE_GROUP=${RESOURCE_GROUP}
KEYVAULT_NAME=${KEYVAULT_NAME}
TARGET_APP=${TARGET_APP}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL}
EMAIL_SERVICE_NAME=${EMAIL_SERVICE_NAME}
EMAIL_DOMAIN_NAME=${EMAIL_DOMAIN_NAME}
FROM_SENDER_DOMAIN=${FROM_SENDER_DOMAIN}
COMMUNICATION_SERVICE_NAME=${COMMUNICATION_SERVICE_NAME}
ENTRA_APP_DISPLAY_NAME=${ENTRA_APP_DISPLAY_NAME}
ENTRA_APP_ID=${ENTRA_APP_ID}
SMTP_AUTH_RESOURCE_NAME=${SMTP_AUTH_RESOURCE_NAME}
SMTP_AUTH_USERNAME=${SMTP_AUTH_USERNAME}
SENDER_USERNAME=${SENDER_USERNAME}
FROM_ADDRESS=${FROM_ADDRESS}
SMTP_USERNAME_SECRET_NAME=${SMTP_USERNAME_SECRET_NAME}
SMTP_PASSWORD_SECRET_NAME=${SMTP_PASSWORD_SECRET_NAME}
EMAIL_FROM_SECRET_NAME=${EMAIL_FROM_SECRET_NAME}
SMTP_HOST=${SMTP_HOST}
SMTP_PORT=${SMTP_PORT}
SMTP_USE_TLS=${SMTP_USE_TLS}
SMTP_USE_SSL=${SMTP_USE_SSL}
EOF
}

send_test_email() {
  [[ -n "$TEST_TO" ]] || return 0

  log "Sending SMTP test email to ${TEST_TO}"
  local max_attempts=12
  local attempt=1
  while (( attempt <= max_attempts )); do
    if PYTHONPATH="$ROOT_DIR" \
      APP_SMTP_HOST="$SMTP_HOST" \
      APP_SMTP_PORT="$SMTP_PORT" \
      APP_SMTP_USE_TLS="$SMTP_USE_TLS" \
      APP_SMTP_USE_SSL="$SMTP_USE_SSL" \
      APP_SMTP_USERNAME="$SMTP_AUTH_USERNAME" \
      APP_SMTP_PASSWORD="$SMTP_PASSWORD_VALUE" \
      APP_EMAIL_FROM="$FROM_ADDRESS" \
      python - "$TEST_TO" <<'PY'
import json
import sys

from services.emailer import send_email

recipient = sys.argv[1]
result = send_email(
    to_address=recipient,
    subject="Spectral Nature SMTP setup test",
    text_body="SMTP test from the Spectral Nature Azure email setup workflow.",
)
print(json.dumps({"sent": result.sent, "message": result.message}))
raise SystemExit(0 if result.sent else 1)
PY
    then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 10
  done

  die "SMTP test email failed after ${max_attempts} attempts."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || die "--target requires a value"
      TARGET="$2"
      shift 2
      ;;
    --test-to)
      [[ $# -ge 2 ]] || die "--test-to requires a value"
      TEST_TO="$2"
      shift 2
      ;;
    --rotate-client-secret)
      ROTATE_CLIENT_SECRET="true"
      shift
      ;;
    --resource-group)
      [[ $# -ge 2 ]] || die "--resource-group requires a value"
      RESOURCE_GROUP="$2"
      shift 2
      ;;
    --keyvault-name)
      [[ $# -ge 2 ]] || die "--keyvault-name requires a value"
      KEYVAULT_NAME="$2"
      KEYVAULT_NAME_SOURCE="cli"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

case "$TARGET" in
  dev|prod) ;;
  *) die "--target must be either dev or prod" ;;
esac

load_deployment_context
ensure_azure_ready
ensure_target_context
ensure_email_service
ensure_email_domain
ensure_communication_service
ensure_sender_username
ensure_entra_app
ensure_role_assignment
ensure_smtp_auth_username
write_keyvault_secrets
update_container_app_env
READY_REVISION="$(wait_for_ready_revision "$TARGET_APP")"
ROOT_HTTP_CODE="$(smoke_check_app "$TARGET_APP")"
write_outputs_file
send_test_email

log "Email delivery setup complete"
log "Target app: ${TARGET_APP}"
log "Ready revision: ${READY_REVISION}"
log "Root HTTP status: ${ROOT_HTTP_CODE}"
log "From address: ${FROM_ADDRESS}"
if [[ -n "$TEST_TO" ]]; then
  log "SMTP test sent to: ${TEST_TO}"
fi
log "Saved outputs: ${EMAIL_OUTPUTS_FILE}"
