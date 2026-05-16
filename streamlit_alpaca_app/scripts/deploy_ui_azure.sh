#!/usr/bin/env bash
# Deploy the UI (Streamlit) container to Azure Container Apps.
#
# Source directories this container serves at request time:
#   app.py, presentation/, config/, branding/, services/* (UI-facing only),
#   data_access/, requirements.txt
#
# If your change is in pipeline/, compute/, services/aql/, services/saa/,
# or services/attention_*_summary.py etc., you likely need
# deploy_pipeline_azure.sh instead. Run scripts/which_deploy.sh to check.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Deploy guard: warn if no UI-relevant files changed.
if [[ -x "$ROOT_DIR/scripts/which_deploy.sh" ]]; then
  if ! "$ROOT_DIR/scripts/which_deploy.sh" --check ui 2>/dev/null; then
    echo ""
    read -r -p "Continue anyway? [y/N] " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
      echo "Aborted."
      exit 0
    fi
  fi
fi

DEFAULT_DEPLOYMENT_ENV_FILE="infra/.generated/deployment.local.env"
LEGACY_DEPLOYMENT_ENV_FILE="infra/deployment.outputs.env"
DEPLOYMENT_ENV_FILE="${DEPLOYMENT_ENV_FILE:-$DEFAULT_DEPLOYMENT_ENV_FILE}"
STATUS_FILE="${STATUS_FILE:-documents/infra/UI_DEPLOYMENT_STATUS.md}"
TARGET="dev"
PROMOTE_FROM=""
IMAGE_REF=""
IMAGE_TAG=""
REFRESH_TRACKER_ONLY=false
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-300}"
WAIT_INTERVAL_SECONDS="${WAIT_INTERVAL_SECONDS:-5}"
DEV_CONTAINER_APP="${DEV_CONTAINER_APP:-sn-streamlit-ui-dev}"
PROD_CONTAINER_APP="${PROD_CONTAINER_APP:-sn-streamlit-ui}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-streamlit-ui}"
DOCKERFILE="${DOCKERFILE:-Dockerfile.app}"
RELEASE_TS="${APP_RELEASE_TS:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
RELEASE_TAG_TS="$(date -u +%Y%m%d%H%M%S)"
RESOURCE_GROUP="${RESOURCE_GROUP:-}"
ACR_NAME="${ACR_NAME:-}"
REGISTRY_SERVER="${REGISTRY_SERVER:-}"
AZURE_STORAGE_CONTAINER="${AZURE_STORAGE_CONTAINER:-datasets}"
CONTAINERAPP_API_VERSION="${CONTAINERAPP_API_VERSION:-2025-07-01}"
LLM_API_KEY_SECRET_NAME="${LLM_API_KEY_SECRET_NAME:-${AZURE_OPENAI_API_KEY_SECRET_NAME:-}}"
OPENAI_API_KEY_SECRET_NAME="${OPENAI_API_KEY_SECRET_NAME:-}"
LLM_PROVIDER="${LLM_PROVIDER:-}"
LLM_MODEL="${LLM_MODEL:-}"
OPENAI_MODEL="${OPENAI_MODEL:-}"
LLM_DEPLOYMENT="${LLM_DEPLOYMENT:-}"
AZURE_OPENAI_DEPLOYMENT="${AZURE_OPENAI_DEPLOYMENT:-}"
LLM_BASE_URL="${LLM_BASE_URL:-}"
OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-}"
AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-}"
LLM_TIMEOUT_SECONDS="${LLM_TIMEOUT_SECONDS:-}"
LLM_TEMPERATURE="${LLM_TEMPERATURE:-}"
LLM_REASONING_EFFORT="${LLM_REASONING_EFFORT:-}"
OPENAI_REASONING_EFFORT="${OPENAI_REASONING_EFFORT:-}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-}"
EMBEDDING_DEPLOYMENT="${EMBEDDING_DEPLOYMENT:-}"
PIPELINE_CACHE_MAX_BYTES="${PIPELINE_CACHE_MAX_BYTES:-}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/deploy_ui_azure.sh [options]

Options:
  --target dev|prod          Target UI app. Default: dev
  --promote-from dev|prod    Reuse the current image from another UI app
  --image-ref REF            Deploy an existing image ref or repo:tag
  --image-tag REPO:TAG       Override the tag used when building a new image
  --refresh-tracker-only     Skip deployment and rewrite documents/infra/UI_DEPLOYMENT_STATUS.md
  --help                     Show this message

Examples:
  ./scripts/deploy_ui_azure.sh
  ./scripts/deploy_ui_azure.sh --target prod --promote-from dev
  ./scripts/deploy_ui_azure.sh --target dev --image-ref streamlit-ui:dev-20260317201859
  ./scripts/deploy_ui_azure.sh --refresh-tracker-only
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

log() {
  echo "$*"
}

truthy_value() {
  local value
  value="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]' | xargs)"
  case "$value" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

maybe_append_env() {
  local key="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    UPDATE_ENV_VARS+=("${key}=${value}")
  fi
}

target_app_name() {
  case "$1" in
    dev) echo "$DEV_CONTAINER_APP" ;;
    prod) echo "$PROD_CONTAINER_APP" ;;
    *) die "Unsupported target environment: $1" ;;
  esac
}

load_deployment_context() {
  if [[ -f "$DEPLOYMENT_ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$DEPLOYMENT_ENV_FILE"
  elif [[ "$DEPLOYMENT_ENV_FILE" == "$DEFAULT_DEPLOYMENT_ENV_FILE" && -f "$LEGACY_DEPLOYMENT_ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$LEGACY_DEPLOYMENT_ENV_FILE"
  fi

  RESOURCE_GROUP="${RESOURCE_GROUP:-${PIPELINE_RESOURCE_GROUP:-}}"
  ACR_NAME="${ACR_NAME:-}"
}

ensure_azure_ready() {
  require_command az
  require_command curl
  require_command python3

  if ! az account show >/dev/null 2>&1; then
    die "Azure CLI is not authenticated. Run: az login --use-device-code"
  fi
}

ensure_registry_context() {
  [[ -n "$RESOURCE_GROUP" ]] || die "RESOURCE_GROUP is required. Source $DEPLOYMENT_ENV_FILE or export RESOURCE_GROUP."
  [[ -n "$ACR_NAME" ]] || die "ACR_NAME is required. Source $DEPLOYMENT_ENV_FILE or export ACR_NAME."

  if [[ -z "$REGISTRY_SERVER" ]]; then
    REGISTRY_SERVER="$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query loginServer -o tsv)"
  fi
}

app_env_value() {
  local app_name="$1"
  local key="$2"
  az resource show \
    -n "$app_name" \
    -g "$RESOURCE_GROUP" \
    --resource-type Microsoft.App/containerApps \
    --api-version "$CONTAINERAPP_API_VERSION" \
    --query "properties.template.containers[0].env[?name=='${key}'].value | [0]" \
    -o tsv 2>/dev/null || true
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

existing_or_override_env() {
  local app_name="$1"
  local key="$2"
  local override="$3"
  local fallback="${4:-}"
  local current=""

  if [[ -n "$override" ]]; then
    echo "$override"
    return 0
  fi

  current="$(app_env_value "$app_name" "$key")"
  if [[ -n "$current" && "$current" != "null" ]]; then
    echo "$current"
    return 0
  fi

  echo "$fallback"
}

existing_or_override_or_source_env() {
  local app_name="$1"
  local key="$2"
  local override="$3"
  local source_app="${4:-}"
  local fallback="${5:-}"
  local current=""

  if [[ -n "$override" ]]; then
    echo "$override"
    return 0
  fi

  current="$(app_env_value "$app_name" "$key")"
  if [[ -n "$current" && "$current" != "null" ]]; then
    echo "$current"
    return 0
  fi

  if [[ -n "$source_app" ]]; then
    current="$(app_env_value "$source_app" "$key")"
    if [[ -n "$current" && "$current" != "null" ]]; then
      echo "$current"
      return 0
    fi
  fi

  echo "$fallback"
}

resolve_image_ref() {
  local ref="$1"
  ensure_registry_context

  if [[ "$ref" == *@sha256:* ]]; then
    if [[ "$ref" == "${REGISTRY_SERVER}/"* ]]; then
      echo "$ref"
    else
      echo "${REGISTRY_SERVER}/${ref}"
    fi
    return 0
  fi

  local repo_tag="$ref"
  if [[ "$repo_tag" == "${REGISTRY_SERVER}/"* ]]; then
    repo_tag="${repo_tag#${REGISTRY_SERVER}/}"
  fi

  [[ "$repo_tag" == *:* ]] || die "Image reference must include a tag or digest: $ref"

  local repository="${repo_tag%%:*}"
  local digest
  digest="$(az acr repository show -n "$ACR_NAME" --image "$repo_tag" --query digest -o tsv)"
  [[ -n "$digest" ]] || die "Unable to resolve image digest for $repo_tag"

  echo "${REGISTRY_SERVER}/${repository}@${digest}"
}

build_ui_image() {
  ensure_registry_context

  local tag="${IMAGE_TAG:-${IMAGE_REPOSITORY}:${TARGET}-${RELEASE_TAG_TS}}"
  log "[1/5] Building UI image $tag in ACR"
  az acr build -g "$RESOURCE_GROUP" -r "$ACR_NAME" -t "$tag" -f "$DOCKERFILE" .
  IMAGE_TO_DEPLOY="$(resolve_image_ref "$tag")"
}

image_from_app() {
  local source_app
  source_app="$(target_app_name "$1")"
  containerapp_query "$source_app" "properties.template.containers[0].image"
}

wait_for_ready_revision() {
  local app_name="$1"
  local previous_latest_revision="${2:-}"
  local attempts=$(( (WAIT_TIMEOUT_SECONDS + WAIT_INTERVAL_SECONDS - 1) / WAIT_INTERVAL_SECONDS ))
  local latest_revision=""
  local ready_revision=""
  local saw_new_revision=false

  if [[ -z "$previous_latest_revision" ]]; then
    saw_new_revision=true
  fi

  for ((i=1; i<=attempts; i++)); do
    latest_revision="$(containerapp_query "$app_name" "properties.latestRevisionName")"
    ready_revision="$(containerapp_query "$app_name" "properties.latestReadyRevisionName")"

    if [[ -n "$latest_revision" && -n "$previous_latest_revision" && "$latest_revision" != "$previous_latest_revision" ]]; then
      saw_new_revision=true
    fi

    if [[ "$saw_new_revision" == true && -n "$latest_revision" && "$latest_revision" == "$ready_revision" ]]; then
      echo "$ready_revision"
      return 0
    fi

    sleep "$WAIT_INTERVAL_SECONDS"
  done

  if [[ -n "$previous_latest_revision" ]]; then
    die "Timed out waiting for $app_name to make a new ready revision after ${previous_latest_revision}."
  fi

  die "Timed out waiting for $app_name to make the latest revision ready."
}

app_fqdn() {
  local app_name="$1"
  containerapp_query "$app_name" "properties.configuration.ingress.fqdn"
}

update_containerapp() {
  local app_name="$1"
  local image_ref="$2"
  shift 2
  local env_updates=("$@")
  local current_json
  local patch_json
  local resource_id

  current_json="$(mktemp)"
  patch_json="$(mktemp)"

  az resource show \
    -n "$app_name" \
    -g "$RESOURCE_GROUP" \
    --resource-type Microsoft.App/containerApps \
    --api-version "$CONTAINERAPP_API_VERSION" \
    -o json > "$current_json"

  python3 - "$current_json" "$patch_json" "$image_ref" "${env_updates[@]}" <<'PY'
import json
import sys

source_path, patch_path, image_ref, *env_updates = sys.argv[1:]
with open(source_path, "r", encoding="utf-8") as handle:
    current = json.load(handle)

template = current["properties"]["template"]
containers = template.get("containers") or []
if not containers:
    raise SystemExit("Container App template did not include any containers.")

container = containers[0]
container["image"] = image_ref

env_items = container.get("env") or []
env_by_name = {}
env_order = []
for item in env_items:
    name = item.get("name")
    if not name:
        continue
    env_by_name[name] = dict(item)
    env_order.append(name)

for pair in env_updates:
    key, value = pair.split("=", 1)
    item = env_by_name.get(key, {"name": key})
    item.pop("secretRef", None)
    item["value"] = value
    env_by_name[key] = item
    if key not in env_order:
        env_order.append(key)

container["env"] = [env_by_name[name] for name in env_order]

with open(patch_path, "w", encoding="utf-8") as handle:
    json.dump({"properties": {"template": template}}, handle)
PY

  resource_id="$(python3 - "$current_json" <<'PY'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    print(json.load(handle)["id"])
PY
)"

  az rest \
    --method PATCH \
    --uri "https://management.azure.com${resource_id}?api-version=${CONTAINERAPP_API_VERSION}" \
    --body "@${patch_json}" \
    >/dev/null

  rm -f "$current_json" "$patch_json"
}

root_http_code_for_url() {
  local url="$1"
  local code
  code="$(curl -sS -L -o /dev/null -w "%{http_code}" --max-time 30 "$url" || true)"
  echo "${code:-000}"
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

status_row() {
  local role_label="$1"
  local app_name="$2"

  if ! az resource show -n "$app_name" -g "$RESOURCE_GROUP" --resource-type Microsoft.App/containerApps --api-version "$CONTAINERAPP_API_VERSION" --output none >/dev/null 2>&1; then
    echo "| **${role_label}** | \`${RESOURCE_GROUP}\` | \`${app_name}\` | n/a | \`n/a\` | \`n/a\` | n/a | Unavailable |"
    return 0
  fi

  local fqdn
  local image
  local ready_revision
  local url
  local http_code
  local browser_cookie_value
  local auth_persistence_label

  fqdn="$(app_fqdn "$app_name")"
  image="$(containerapp_query "$app_name" "properties.template.containers[0].image")"
  ready_revision="$(containerapp_query "$app_name" "properties.latestReadyRevisionName")"
  disable_cookie_value="$(app_env_value "$app_name" "UI_DISABLE_BROWSER_SESSION_COOKIE")"
  legacy_allow_value="$(app_env_value "$app_name" "UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE")"
  # Persistence is ON by default; only off if explicitly disabled.
  auth_persistence_label="browser cookie (default)"
  if truthy_value "$disable_cookie_value"; then
    auth_persistence_label="session only (disabled)"
  elif [[ -n "$legacy_allow_value" ]] && ! truthy_value "$legacy_allow_value"; then
    auth_persistence_label="session only (legacy opt-out)"
  fi
  url="https://${fqdn}"
  http_code="$(root_http_code_for_url "$url")"

  echo "| **${role_label}** | \`${RESOURCE_GROUP}\` | \`${app_name}\` | ${url} | \`${ready_revision:-n/a}\` | \`${image:-n/a}\` | ${auth_persistence_label} | HTTP ${http_code} |"
}

write_status_tracker() {
  [[ -n "$RESOURCE_GROUP" ]] || die "RESOURCE_GROUP is required to refresh $STATUS_FILE"

  local updated_at
  local prod_row
  local dev_row

  updated_at="$(date -u '+%Y-%m-%d %H:%M')"
  prod_row="$(status_row "Production (stable)" "$PROD_CONTAINER_APP")"
  dev_row="$(status_row "Development" "$DEV_CONTAINER_APP")"

  cat > "$STATUS_FILE" <<EOF
# UI Deployment Status Tracker

Last updated (UTC): ${updated_at}

## Environment Mapping

| Role | Resource Group | Container App | URL | Latest Revision | Image | Auth Persistence | Health |
|---|---|---|---|---|---|---|---|
${prod_row}
${dev_row}

## Promotion Workflow

1. Deploy new UI changes to **Development** app (\`${DEV_CONTAINER_APP}\`) first.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (\`${PROD_CONTAINER_APP}\`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- UI container apps live in resource group \`${RESOURCE_GROUP}\`.
- Both apps use the same managed identity and Key Vault-based auth configuration.
- Browser session persistence is **on by default**. Disable with \`UI_DISABLE_BROWSER_SESSION_COOKIE=1\`. The legacy \`UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE=0\` also disables it.
- Sidebar now displays \`Environment: production\` or \`Environment: development\` via \`APP_TRACK\`.
- Keep Production stable by avoiding direct experimental changes to \`${PROD_CONTAINER_APP}\`.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || die "--target requires a value"
      TARGET="$2"
      shift 2
      ;;
    --promote-from)
      [[ $# -ge 2 ]] || die "--promote-from requires a value"
      PROMOTE_FROM="$2"
      shift 2
      ;;
    --image-ref)
      [[ $# -ge 2 ]] || die "--image-ref requires a value"
      IMAGE_REF="$2"
      shift 2
      ;;
    --image-tag)
      [[ $# -ge 2 ]] || die "--image-tag requires a value"
      IMAGE_TAG="$2"
      shift 2
      ;;
    --refresh-tracker-only)
      REFRESH_TRACKER_ONLY=true
      shift
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

if [[ -n "$PROMOTE_FROM" ]]; then
  case "$PROMOTE_FROM" in
    dev|prod) ;;
    *) die "--promote-from must be either dev or prod" ;;
  esac
fi

if [[ -n "$PROMOTE_FROM" && -n "$IMAGE_REF" ]]; then
  die "Use either --promote-from or --image-ref, not both."
fi

load_deployment_context
ensure_azure_ready
AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-$(az account show --query id -o tsv 2>/dev/null || true)}"

if [[ "$REFRESH_TRACKER_ONLY" == true ]]; then
  log "[1/1] Refreshing UI deployment tracker"
  write_status_tracker
  log "Updated $STATUS_FILE"
  exit 0
fi

[[ -n "$RESOURCE_GROUP" ]] || die "RESOURCE_GROUP is required. Source $DEPLOYMENT_ENV_FILE or export RESOURCE_GROUP."

if [[ "$TARGET" == "prod" && -z "$PROMOTE_FROM" && -z "$IMAGE_REF" ]]; then
  die "Direct prod builds are blocked by default. Use --promote-from dev or --image-ref <digest>."
fi

if [[ -n "$PROMOTE_FROM" && "$PROMOTE_FROM" == "$TARGET" ]]; then
  die "Promotion source and target must be different."
fi

TARGET_APP="$(target_app_name "$TARGET")"
PROMOTION_SOURCE_APP=""
if [[ -n "$PROMOTE_FROM" ]]; then
  PROMOTION_SOURCE_APP="$(target_app_name "$PROMOTE_FROM")"
fi
TARGET_TRACK_VALUE="development"
TARGET_CACHE_DISABLED_VALUE="true"
#
# Keep force refresh disabled by default in both dev and prod. For snapshot-first
# surfaces such as Home, enabling this changes runtime semantics and bypasses the
# precomputed views in favor of the on-demand builders.
#
# Only flip this on intentionally for targeted runtime testing, and do it via an
# explicit rollout override so we do not accidentally regress the default UX on
# every subsequent deploy.
TARGET_FORCE_REFRESH_DEFAULT_VALUE="${APP_FORCE_DATA_REFRESH_DEFAULT_OVERRIDE:-false}"
if [[ "$TARGET" == "prod" ]]; then
  TARGET_TRACK_VALUE="production"
  TARGET_CACHE_DISABLED_VALUE="false"
fi

TARGET_FQDN="$(app_fqdn "$TARGET_APP")"
TARGET_PUBLIC_BASE_URL_VALUE="$(existing_or_override_env "$TARGET_APP" "APP_PUBLIC_BASE_URL" "${APP_PUBLIC_BASE_URL:-}" "${TARGET_FQDN:+https://${TARGET_FQDN}}")"
TARGET_ALPACA_BASE_URL_VALUE="$(existing_or_override_env "$TARGET_APP" "APCA_API_BASE_URL" "${APCA_API_BASE_URL:-}")"
TARGET_ALPACA_DATA_BASE_URL_VALUE="$(existing_or_override_env "$TARGET_APP" "ALPACA_DATA_BASE_URL" "${ALPACA_DATA_BASE_URL:-}" "https://data.alpaca.markets")"
TARGET_SMTP_HOST_VALUE="$(existing_or_override_env "$TARGET_APP" "APP_SMTP_HOST" "${APP_SMTP_HOST:-}")"
TARGET_SMTP_PORT_VALUE="$(existing_or_override_env "$TARGET_APP" "APP_SMTP_PORT" "${APP_SMTP_PORT:-}")"
TARGET_SMTP_USE_TLS_VALUE="$(existing_or_override_env "$TARGET_APP" "APP_SMTP_USE_TLS" "${APP_SMTP_USE_TLS:-}")"
TARGET_SMTP_USE_SSL_VALUE="$(existing_or_override_env "$TARGET_APP" "APP_SMTP_USE_SSL" "${APP_SMTP_USE_SSL:-}")"
TARGET_SMTP_USERNAME_SECRET_VALUE="$(existing_or_override_env "$TARGET_APP" "APP_SMTP_USERNAME_SECRET" "${APP_SMTP_USERNAME_SECRET:-}")"
TARGET_SMTP_PASSWORD_SECRET_VALUE="$(existing_or_override_env "$TARGET_APP" "APP_SMTP_PASSWORD_SECRET" "${APP_SMTP_PASSWORD_SECRET:-}")"
TARGET_EMAIL_FROM_SECRET_VALUE="$(existing_or_override_env "$TARGET_APP" "APP_EMAIL_FROM_SECRET" "${APP_EMAIL_FROM_SECRET:-}")"
# Persistence is on by default; these vars are only needed to opt out.
TARGET_UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE_VALUE="$(existing_or_override_env "$TARGET_APP" "UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE" "${UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE:-}")"
TARGET_UI_DISABLE_BROWSER_SESSION_COOKIE_VALUE="$(existing_or_override_env "$TARGET_APP" "UI_DISABLE_BROWSER_SESSION_COOKIE" "${UI_DISABLE_BROWSER_SESSION_COOKIE:-}")"
TARGET_LLM_API_KEY_SECRET_NAME_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "LLM_API_KEY_SECRET_NAME" "${LLM_API_KEY_SECRET_NAME:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OPENAI_API_KEY_SECRET_NAME_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OPENAI_API_KEY_SECRET_NAME" "${OPENAI_API_KEY_SECRET_NAME:-}" "$PROMOTION_SOURCE_APP")"
TARGET_LLM_PROVIDER_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "LLM_PROVIDER" "${LLM_PROVIDER:-}" "$PROMOTION_SOURCE_APP")"
TARGET_LLM_MODEL_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "LLM_MODEL" "${LLM_MODEL:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OPENAI_MODEL_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OPENAI_MODEL" "${OPENAI_MODEL:-}" "$PROMOTION_SOURCE_APP")"
TARGET_LLM_DEPLOYMENT_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "LLM_DEPLOYMENT" "${LLM_DEPLOYMENT:-}" "$PROMOTION_SOURCE_APP")"
TARGET_AZURE_OPENAI_DEPLOYMENT_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "AZURE_OPENAI_DEPLOYMENT" "${AZURE_OPENAI_DEPLOYMENT:-}" "$PROMOTION_SOURCE_APP")"
TARGET_LLM_BASE_URL_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "LLM_BASE_URL" "${LLM_BASE_URL:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OPENAI_BASE_URL_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OPENAI_BASE_URL" "${OPENAI_BASE_URL:-}" "$PROMOTION_SOURCE_APP")"
TARGET_AZURE_OPENAI_ENDPOINT_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "AZURE_OPENAI_ENDPOINT" "${AZURE_OPENAI_ENDPOINT:-}" "$PROMOTION_SOURCE_APP")"
TARGET_AZURE_OPENAI_API_VERSION_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "AZURE_OPENAI_API_VERSION" "${AZURE_OPENAI_API_VERSION:-}" "$PROMOTION_SOURCE_APP")"
TARGET_LLM_TIMEOUT_SECONDS_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "LLM_TIMEOUT_SECONDS" "${LLM_TIMEOUT_SECONDS:-}" "$PROMOTION_SOURCE_APP")"
TARGET_LLM_TEMPERATURE_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "LLM_TEMPERATURE" "${LLM_TEMPERATURE:-}" "$PROMOTION_SOURCE_APP")"
TARGET_LLM_REASONING_EFFORT_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "LLM_REASONING_EFFORT" "${LLM_REASONING_EFFORT:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OPENAI_REASONING_EFFORT_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OPENAI_REASONING_EFFORT" "${OPENAI_REASONING_EFFORT:-}" "$PROMOTION_SOURCE_APP")"
TARGET_EMBEDDING_MODEL_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "EMBEDDING_MODEL" "${EMBEDDING_MODEL:-}" "$PROMOTION_SOURCE_APP")"
TARGET_EMBEDDING_DEPLOYMENT_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "EMBEDDING_DEPLOYMENT" "${EMBEDDING_DEPLOYMENT:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OMNIBAR_AGENT_LLM_API_KEY_SECRET_NAME_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OMNIBAR_AGENT_LLM_API_KEY_SECRET_NAME" "${OMNIBAR_AGENT_LLM_API_KEY_SECRET_NAME:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OMNIBAR_AGENT_LLM_PROVIDER_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OMNIBAR_AGENT_LLM_PROVIDER" "${OMNIBAR_AGENT_LLM_PROVIDER:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OMNIBAR_AGENT_LLM_MODEL_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OMNIBAR_AGENT_LLM_MODEL" "${OMNIBAR_AGENT_LLM_MODEL:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OMNIBAR_AGENT_LLM_BASE_URL_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OMNIBAR_AGENT_LLM_BASE_URL" "${OMNIBAR_AGENT_LLM_BASE_URL:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OMNIBAR_AGENT_LLM_TIMEOUT_SECONDS_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OMNIBAR_AGENT_LLM_TIMEOUT_SECONDS" "${OMNIBAR_AGENT_LLM_TIMEOUT_SECONDS:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OMNIBAR_AGENT_LLM_REASONING_EFFORT_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OMNIBAR_AGENT_LLM_REASONING_EFFORT" "${OMNIBAR_AGENT_LLM_REASONING_EFFORT:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OMNIBAR_AGENT_SYNTHESIS_LLM_MODEL_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OMNIBAR_AGENT_SYNTHESIS_LLM_MODEL" "${OMNIBAR_AGENT_SYNTHESIS_LLM_MODEL:-}" "$PROMOTION_SOURCE_APP")"
TARGET_OMNIBAR_AGENT_SYNTHESIS_LLM_DEPLOYMENT_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "OMNIBAR_AGENT_SYNTHESIS_LLM_DEPLOYMENT" "${OMNIBAR_AGENT_SYNTHESIS_LLM_DEPLOYMENT:-}" "$PROMOTION_SOURCE_APP")"
TARGET_PIPELINE_CACHE_MAX_BYTES_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "PIPELINE_CACHE_MAX_BYTES" "${PIPELINE_CACHE_MAX_BYTES:-}" "$PROMOTION_SOURCE_APP")"
TARGET_PIPELINE_RESOURCE_GROUP_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "PIPELINE_RESOURCE_GROUP" "${PIPELINE_RESOURCE_GROUP:-${RESOURCE_GROUP:-}}" "$PROMOTION_SOURCE_APP")"
TARGET_AZURE_SUBSCRIPTION_ID_VALUE="$(existing_or_override_or_source_env "$TARGET_APP" "AZURE_SUBSCRIPTION_ID" "${AZURE_SUBSCRIPTION_ID:-}" "$PROMOTION_SOURCE_APP")"

if [[ -n "$PROMOTE_FROM" ]]; then
  log "[1/5] Resolving image from ${PROMOTION_SOURCE_APP}"
  IMAGE_TO_DEPLOY="$(image_from_app "$PROMOTE_FROM")"
  [[ -n "$IMAGE_TO_DEPLOY" ]] || die "No image found on ${PROMOTION_SOURCE_APP}"
  IMAGE_TO_DEPLOY="$(resolve_image_ref "$IMAGE_TO_DEPLOY")"
elif [[ -n "$IMAGE_REF" ]]; then
  log "[1/5] Resolving provided image reference"
  IMAGE_TO_DEPLOY="$(resolve_image_ref "$IMAGE_REF")"
else
  build_ui_image
fi

log "[2/5] Updating ${TARGET_APP}"
UPDATE_ENV_VARS=(
  "APP_TRACK=${TARGET_TRACK_VALUE}"
  "APP_DISABLE_CACHE=${TARGET_CACHE_DISABLED_VALUE}"
  "APP_FORCE_DATA_REFRESH_DEFAULT=${TARGET_FORCE_REFRESH_DEFAULT_VALUE}"
  "APP_RELEASE_TS=${RELEASE_TS}"
  "AZURE_STORAGE_CONTAINER=${AZURE_STORAGE_CONTAINER}"
)
maybe_append_env "APP_PUBLIC_BASE_URL" "$TARGET_PUBLIC_BASE_URL_VALUE"
maybe_append_env "APCA_API_BASE_URL" "$TARGET_ALPACA_BASE_URL_VALUE"
maybe_append_env "ALPACA_DATA_BASE_URL" "$TARGET_ALPACA_DATA_BASE_URL_VALUE"
maybe_append_env "APP_SMTP_HOST" "$TARGET_SMTP_HOST_VALUE"
maybe_append_env "APP_SMTP_PORT" "$TARGET_SMTP_PORT_VALUE"
maybe_append_env "APP_SMTP_USE_TLS" "$TARGET_SMTP_USE_TLS_VALUE"
maybe_append_env "APP_SMTP_USE_SSL" "$TARGET_SMTP_USE_SSL_VALUE"
maybe_append_env "APP_SMTP_USERNAME_SECRET" "$TARGET_SMTP_USERNAME_SECRET_VALUE"
maybe_append_env "APP_SMTP_PASSWORD_SECRET" "$TARGET_SMTP_PASSWORD_SECRET_VALUE"
maybe_append_env "APP_EMAIL_FROM_SECRET" "$TARGET_EMAIL_FROM_SECRET_VALUE"
maybe_append_env "UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE" "$TARGET_UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE_VALUE"
maybe_append_env "UI_DISABLE_BROWSER_SESSION_COOKIE" "$TARGET_UI_DISABLE_BROWSER_SESSION_COOKIE_VALUE"
maybe_append_env "LLM_API_KEY_SECRET_NAME" "$TARGET_LLM_API_KEY_SECRET_NAME_VALUE"
maybe_append_env "OPENAI_API_KEY_SECRET_NAME" "$TARGET_OPENAI_API_KEY_SECRET_NAME_VALUE"
maybe_append_env "LLM_PROVIDER" "$TARGET_LLM_PROVIDER_VALUE"
maybe_append_env "LLM_MODEL" "$TARGET_LLM_MODEL_VALUE"
maybe_append_env "OPENAI_MODEL" "$TARGET_OPENAI_MODEL_VALUE"
maybe_append_env "LLM_DEPLOYMENT" "$TARGET_LLM_DEPLOYMENT_VALUE"
maybe_append_env "AZURE_OPENAI_DEPLOYMENT" "$TARGET_AZURE_OPENAI_DEPLOYMENT_VALUE"
maybe_append_env "LLM_BASE_URL" "$TARGET_LLM_BASE_URL_VALUE"
maybe_append_env "OPENAI_BASE_URL" "$TARGET_OPENAI_BASE_URL_VALUE"
maybe_append_env "AZURE_OPENAI_ENDPOINT" "$TARGET_AZURE_OPENAI_ENDPOINT_VALUE"
maybe_append_env "AZURE_OPENAI_API_VERSION" "$TARGET_AZURE_OPENAI_API_VERSION_VALUE"
  maybe_append_env "LLM_TIMEOUT_SECONDS" "$TARGET_LLM_TIMEOUT_SECONDS_VALUE"
  maybe_append_env "LLM_TEMPERATURE" "$TARGET_LLM_TEMPERATURE_VALUE"
  maybe_append_env "LLM_REASONING_EFFORT" "$TARGET_LLM_REASONING_EFFORT_VALUE"
  maybe_append_env "OPENAI_REASONING_EFFORT" "$TARGET_OPENAI_REASONING_EFFORT_VALUE"
  maybe_append_env "EMBEDDING_MODEL" "$TARGET_EMBEDDING_MODEL_VALUE"
  maybe_append_env "EMBEDDING_DEPLOYMENT" "$TARGET_EMBEDDING_DEPLOYMENT_VALUE"
  maybe_append_env "OMNIBAR_AGENT_LLM_API_KEY_SECRET_NAME" "$TARGET_OMNIBAR_AGENT_LLM_API_KEY_SECRET_NAME_VALUE"
  maybe_append_env "OMNIBAR_AGENT_LLM_PROVIDER" "$TARGET_OMNIBAR_AGENT_LLM_PROVIDER_VALUE"
  maybe_append_env "OMNIBAR_AGENT_LLM_MODEL" "$TARGET_OMNIBAR_AGENT_LLM_MODEL_VALUE"
  maybe_append_env "OMNIBAR_AGENT_LLM_BASE_URL" "$TARGET_OMNIBAR_AGENT_LLM_BASE_URL_VALUE"
  maybe_append_env "OMNIBAR_AGENT_LLM_TIMEOUT_SECONDS" "$TARGET_OMNIBAR_AGENT_LLM_TIMEOUT_SECONDS_VALUE"
  maybe_append_env "OMNIBAR_AGENT_LLM_REASONING_EFFORT" "$TARGET_OMNIBAR_AGENT_LLM_REASONING_EFFORT_VALUE"
  maybe_append_env "OMNIBAR_AGENT_SYNTHESIS_LLM_MODEL" "$TARGET_OMNIBAR_AGENT_SYNTHESIS_LLM_MODEL_VALUE"
  maybe_append_env "OMNIBAR_AGENT_SYNTHESIS_LLM_DEPLOYMENT" "$TARGET_OMNIBAR_AGENT_SYNTHESIS_LLM_DEPLOYMENT_VALUE"
  maybe_append_env "PIPELINE_CACHE_MAX_BYTES" "$TARGET_PIPELINE_CACHE_MAX_BYTES_VALUE"
maybe_append_env "PIPELINE_RESOURCE_GROUP" "$TARGET_PIPELINE_RESOURCE_GROUP_VALUE"
maybe_append_env "AZURE_SUBSCRIPTION_ID" "$TARGET_AZURE_SUBSCRIPTION_ID_VALUE"

PREVIOUS_LATEST_REVISION="$(containerapp_query "$TARGET_APP" "properties.latestRevisionName")"
update_containerapp "$TARGET_APP" "$IMAGE_TO_DEPLOY" "${UPDATE_ENV_VARS[@]}"

log "[3/5] Waiting for latest revision to become ready"
READY_REVISION="$(wait_for_ready_revision "$TARGET_APP" "$PREVIOUS_LATEST_REVISION")"

log "[4/5] Running smoke checks"
ROOT_HTTP_CODE="$(smoke_check_app "$TARGET_APP")"

log "[5/5] Refreshing UI deployment tracker"
write_status_tracker

log "Deployment complete"
log "Target app: ${TARGET_APP}"
log "Ready revision: ${READY_REVISION}"
log "Image: ${IMAGE_TO_DEPLOY}"
log "Root HTTP status: ${ROOT_HTTP_CODE}"
