#!/usr/bin/env bash
set -euo pipefail
#
# Deploy the FastAPI container app (sn-api-dev / sn-api).
#
# Source directories this container serves: api/, services/* (API-facing),
#   data_access/, requirements.txt
#
# If your change is in pipeline/, compute/, or services/aql/, you likely
# need deploy_pipeline_azure.sh. Run scripts/which_deploy.sh to check.
#
# Usage:
#   ./scripts/deploy_api_azure.sh                          # build & deploy to dev
#   ./scripts/deploy_api_azure.sh --target prod --promote-from dev
#   ./scripts/deploy_api_azure.sh --target dev --image-ref api:dev-20260418201500
#

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DEFAULT_DEPLOYMENT_ENV_FILE="infra/.generated/deployment.local.env"
LEGACY_DEPLOYMENT_ENV_FILE="infra/deployment.outputs.env"
DEPLOYMENT_ENV_FILE="${DEPLOYMENT_ENV_FILE:-$DEFAULT_DEPLOYMENT_ENV_FILE}"
REQUESTED_ENV_OVERRIDE_KEYS=()
REQUESTED_ENV_OVERRIDE_VALUES=()

capture_requested_env_override() {
  local key="$1"
  if [[ "${!key+x}" == "x" ]]; then
    REQUESTED_ENV_OVERRIDE_KEYS+=("$key")
    REQUESTED_ENV_OVERRIDE_VALUES+=("${!key}")
  fi
}

restore_requested_env_overrides() {
  local idx key
  for ((idx=0; idx<${#REQUESTED_ENV_OVERRIDE_KEYS[@]}; idx++)); do
    key="${REQUESTED_ENV_OVERRIDE_KEYS[$idx]}"
    printf -v "$key" '%s' "${REQUESTED_ENV_OVERRIDE_VALUES[$idx]}"
  done
}

requested_env_override_value() {
  local key="$1"
  local idx
  for ((idx=0; idx<${#REQUESTED_ENV_OVERRIDE_KEYS[@]}; idx++)); do
    if [[ "${REQUESTED_ENV_OVERRIDE_KEYS[$idx]}" == "$key" ]]; then
      printf '%s\n' "${REQUESTED_ENV_OVERRIDE_VALUES[$idx]}"
      return 0
    fi
  done
  return 0
}

for key in \
  LLM_API_KEY_SECRET_NAME LLM_PROVIDER LLM_MODEL LLM_DEPLOYMENT \
  LLM_BASE_URL LLM_TIMEOUT_SECONDS LLM_TEMPERATURE LLM_REASONING_EFFORT \
  OPENAI_API_KEY_SECRET_NAME OPENAI_MODEL OPENAI_BASE_URL OPENAI_REASONING_EFFORT \
  AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_VERSION AZURE_OPENAI_DEPLOYMENT \
; do
  capture_requested_env_override "$key"
done
TARGET="dev"
PROMOTE_FROM=""
IMAGE_REF=""
IMAGE_TAG=""
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-300}"
WAIT_INTERVAL_SECONDS="${WAIT_INTERVAL_SECONDS:-5}"
DEV_CONTAINER_APP="${DEV_API_CONTAINER_APP:-sn-api-dev}"
PROD_CONTAINER_APP="${PROD_API_CONTAINER_APP:-sn-api}"
IMAGE_REPOSITORY="${API_IMAGE_REPOSITORY:-api}"
DOCKERFILE="${API_DOCKERFILE:-Dockerfile.api}"
RELEASE_TS="${APP_RELEASE_TS:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
RELEASE_TAG_TS="$(date -u +%Y%m%d%H%M%S)"
RESOURCE_GROUP="${RESOURCE_GROUP:-}"
ACR_NAME="${ACR_NAME:-}"
REGISTRY_SERVER="${REGISTRY_SERVER:-}"
AZURE_STORAGE_CONTAINER="${AZURE_STORAGE_CONTAINER:-datasets}"
CONTAINERAPP_API_VERSION="${CONTAINERAPP_API_VERSION:-2025-07-01}"

# --- UI app name to mirror env vars from (first deploy) ---
UI_DEV_CONTAINER_APP="${DEV_CONTAINER_APP:-sn-streamlit-ui-dev}"
UI_PROD_CONTAINER_APP="${PROD_CONTAINER_APP:-sn-streamlit-ui}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/deploy_api_azure.sh [options]

Options:
  --target dev|prod          Target API app. Default: dev
  --promote-from dev|prod    Reuse the current image from another API app
  --image-ref REF            Deploy an existing image ref or repo:tag
  --image-tag REPO:TAG       Override the tag used when building a new image
  --help                     Show this message

Examples:
  ./scripts/deploy_api_azure.sh
  ./scripts/deploy_api_azure.sh --target prod --promote-from dev
  ./scripts/deploy_api_azure.sh --target dev --image-ref api:dev-20260418201500
EOF
}

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "$*"; }

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

target_app_name() {
  case "$1" in
    dev) echo "$DEV_CONTAINER_APP" ;;
    prod) echo "$PROD_CONTAINER_APP" ;;
    *) die "Unsupported target environment: $1" ;;
  esac
}

ui_app_name() {
  case "$1" in
    dev) echo "$UI_DEV_CONTAINER_APP" ;;
    prod) echo "$UI_PROD_CONTAINER_APP" ;;
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
  restore_requested_env_overrides
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
  [[ -n "$RESOURCE_GROUP" ]] || die "RESOURCE_GROUP is required."
  [[ -n "$ACR_NAME" ]] || die "ACR_NAME is required."
  if [[ -z "$REGISTRY_SERVER" ]]; then
    REGISTRY_SERVER="$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query loginServer -o tsv)"
  fi
}

app_env_value() {
  local app_name="$1" key="$2"
  az resource show \
    -n "$app_name" -g "$RESOURCE_GROUP" \
    --resource-type Microsoft.App/containerApps \
    --api-version "$CONTAINERAPP_API_VERSION" \
    --query "properties.template.containers[0].env[?name=='${key}'].value | [0]" \
    -o tsv 2>/dev/null || true
}

containerapp_query() {
  local app_name="$1" query="$2"
  az resource show \
    -n "$app_name" -g "$RESOURCE_GROUP" \
    --resource-type Microsoft.App/containerApps \
    --api-version "$CONTAINERAPP_API_VERSION" \
    --query "$query" -o tsv 2>/dev/null || true
}

# Try: override → existing on target → existing on UI app (same env) → fallback
resolve_env() {
  local app_name="$1" key="$2" override="$3" fallback="${4:-}"
  if [[ -n "$override" ]]; then echo "$override"; return 0; fi
  local current
  current="$(app_env_value "$app_name" "$key")"
  if [[ -n "$current" && "$current" != "null" ]]; then echo "$current"; return 0; fi
  # Fall back to the UI app's value (useful for first deploy)
  local ui_app
  ui_app="$(ui_app_name "$TARGET")"
  if [[ -n "$ui_app" ]]; then
    current="$(app_env_value "$ui_app" "$key")"
    if [[ -n "$current" && "$current" != "null" ]]; then echo "$current"; return 0; fi
  fi
  echo "$fallback"
}

maybe_append_env() {
  local key="$1" value="$2"
  if [[ -n "$value" ]]; then
    UPDATE_ENV_VARS+=("${key}=${value}")
  fi
}

resolve_image_ref() {
  local ref="$1"
  ensure_registry_context
  if [[ "$ref" == *@sha256:* ]]; then
    if [[ "$ref" == "${REGISTRY_SERVER}/"* ]]; then echo "$ref"; else echo "${REGISTRY_SERVER}/${ref}"; fi
    return 0
  fi
  local repo_tag="$ref"
  [[ "$repo_tag" != "${REGISTRY_SERVER}/"* ]] || repo_tag="${repo_tag#${REGISTRY_SERVER}/}"
  [[ "$repo_tag" == *:* ]] || die "Image reference must include a tag or digest: $ref"
  local repository="${repo_tag%%:*}"
  local digest
  digest="$(az acr repository show -n "$ACR_NAME" --image "$repo_tag" --query digest -o tsv)"
  [[ -n "$digest" ]] || die "Unable to resolve image digest for $repo_tag"
  echo "${REGISTRY_SERVER}/${repository}@${digest}"
}

build_api_image() {
  ensure_registry_context
  local tag="${IMAGE_TAG:-${IMAGE_REPOSITORY}:${TARGET}-${RELEASE_TAG_TS}}"
  log "[1/5] Building API image $tag in ACR"
  az acr build -g "$RESOURCE_GROUP" -r "$ACR_NAME" -t "$tag" -f "$DOCKERFILE" .
  IMAGE_TO_DEPLOY="$(resolve_image_ref "$tag")"
}

image_from_app() {
  local source_app
  source_app="$(target_app_name "$1")"
  containerapp_query "$source_app" "properties.template.containers[0].image"
}

update_containerapp() {
  local app_name="$1" image_ref="$2"
  shift 2
  local env_updates=("$@")
  local current_json patch_json resource_id
  current_json="$(mktemp)"
  patch_json="$(mktemp)"

  az resource show \
    -n "$app_name" -g "$RESOURCE_GROUP" \
    --resource-type Microsoft.App/containerApps \
    --api-version "$CONTAINERAPP_API_VERSION" \
    -o json > "$current_json"

  python3 - "$current_json" "$patch_json" "$image_ref" "${env_updates[@]}" <<'PY'
import json, sys
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
if env_by_name.get("LLM_PROVIDER", {}).get("value") == "deepseek":
    obsolete_env = {
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_DEPLOYMENT",
        "OPENAI_API_KEY_SECRET_NAME",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_REASONING_EFFORT",
        "LLM_REASONING_EFFORT",
    }
    for key in obsolete_env:
        env_by_name.pop(key, None)
    env_order = [name for name in env_order if name not in obsolete_env]
container["env"] = [env_by_name[name] for name in env_order]
with open(patch_path, "w", encoding="utf-8") as handle:
    json.dump({"properties": {"template": template}}, handle)
PY

  resource_id="$(python3 - "$current_json" <<'PY'
import json, sys
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

wait_for_ready_revision() {
  local app_name="$1"
  local previous_latest_revision="${2:-}"
  local attempts=$(( (WAIT_TIMEOUT_SECONDS + WAIT_INTERVAL_SECONDS - 1) / WAIT_INTERVAL_SECONDS ))
  local saw_new_revision=false
  [[ -n "$previous_latest_revision" ]] || saw_new_revision=true

  for ((i=1; i<=attempts; i++)); do
    local latest_revision ready_revision
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
  die "Timed out waiting for $app_name to become ready."
}

app_fqdn() {
  containerapp_query "$1" "properties.configuration.ingress.fqdn"
}

smoke_check_api() {
  local app_name="$1"
  local fqdn
  fqdn="$(app_fqdn "$app_name")"
  [[ -n "$fqdn" ]] || die "Missing ingress FQDN for $app_name"

  local url="https://${fqdn}"
  local health_body
  health_body="$(curl -fsS --max-time 30 "${url}/health" || true)"

  if echo "$health_body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null; then
    log "Health check passed: ${url}/health"
  else
    die "Health check failed for $app_name (${url}/health). Response: ${health_body:-<empty>}"
  fi

  local http_code
  http_code="$(curl -sS -L -o /dev/null -w "%{http_code}" --max-time 30 "$url" || true)"
  echo "${http_code:-000}"
}

# ---------- parse args ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)     [[ $# -ge 2 ]] || die "--target requires a value"; TARGET="$2"; shift 2 ;;
    --promote-from) [[ $# -ge 2 ]] || die "--promote-from requires a value"; PROMOTE_FROM="$2"; shift 2 ;;
    --image-ref)  [[ $# -ge 2 ]] || die "--image-ref requires a value"; IMAGE_REF="$2"; shift 2 ;;
    --image-tag)  [[ $# -ge 2 ]] || die "--image-tag requires a value"; IMAGE_TAG="$2"; shift 2 ;;
    --help|-h)    usage; exit 0 ;;
    *)            die "Unknown argument: $1" ;;
  esac
done

case "$TARGET" in dev|prod) ;; *) die "--target must be dev or prod" ;; esac
[[ -z "$PROMOTE_FROM" ]] || case "$PROMOTE_FROM" in dev|prod) ;; *) die "--promote-from must be dev or prod" ;; esac
[[ -z "$PROMOTE_FROM" || -z "$IMAGE_REF" ]] || die "Use either --promote-from or --image-ref, not both."

load_deployment_context
ensure_azure_ready

[[ -n "$RESOURCE_GROUP" ]] || die "RESOURCE_GROUP is required. Source $DEPLOYMENT_ENV_FILE or export RESOURCE_GROUP."

if [[ "$TARGET" == "prod" && -z "$PROMOTE_FROM" && -z "$IMAGE_REF" ]]; then
  die "Direct prod builds are blocked. Use --promote-from dev or --image-ref <digest>."
fi

if [[ -n "$PROMOTE_FROM" && "$PROMOTE_FROM" == "$TARGET" ]]; then
  die "Promotion source and target must be different."
fi

TARGET_APP="$(target_app_name "$TARGET")"
PROMOTION_SOURCE_APP=""
[[ -z "$PROMOTE_FROM" ]] || PROMOTION_SOURCE_APP="$(target_app_name "$PROMOTE_FROM")"

# ---------- resolve / build image ----------
if [[ -n "$PROMOTE_FROM" ]]; then
  log "[1/5] Resolving image from ${PROMOTION_SOURCE_APP}"
  IMAGE_TO_DEPLOY="$(image_from_app "$PROMOTE_FROM")"
  [[ -n "$IMAGE_TO_DEPLOY" ]] || die "No image found on ${PROMOTION_SOURCE_APP}"
  IMAGE_TO_DEPLOY="$(resolve_image_ref "$IMAGE_TO_DEPLOY")"
elif [[ -n "$IMAGE_REF" ]]; then
  log "[1/5] Resolving provided image reference"
  IMAGE_TO_DEPLOY="$(resolve_image_ref "$IMAGE_REF")"
else
  build_api_image
fi

# ---------- env vars (carried from UI app on first deploy, then self-maintained) ----------
TARGET_TRACK_VALUE="development"
[[ "$TARGET" != "prod" ]] || TARGET_TRACK_VALUE="production"

UPDATE_ENV_VARS=(
  "APP_TRACK=${TARGET_TRACK_VALUE}"
  "APP_RELEASE_TS=${RELEASE_TS}"
  "AZURE_STORAGE_CONTAINER=${AZURE_STORAGE_CONTAINER}"
)

# Carry over key env vars from the UI app (or the API app's own existing values)
for key in \
  APCA_API_BASE_URL ALPACA_DATA_BASE_URL \
  LLM_API_KEY_SECRET_NAME OPENAI_API_KEY_SECRET_NAME \
  LLM_PROVIDER LLM_MODEL OPENAI_MODEL \
  LLM_DEPLOYMENT AZURE_OPENAI_DEPLOYMENT \
  LLM_BASE_URL OPENAI_BASE_URL \
  AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_VERSION \
  LLM_TIMEOUT_SECONDS LLM_TEMPERATURE \
  LLM_REASONING_EFFORT OPENAI_REASONING_EFFORT \
  EMBEDDING_MODEL EMBEDDING_DEPLOYMENT \
  PIPELINE_CACHE_MAX_BYTES \
; do
  maybe_append_env "$key" "$(resolve_env "$TARGET_APP" "$key" "$(requested_env_override_value "$key")")"
done

log "[2/5] Updating ${TARGET_APP}"
PREVIOUS_LATEST_REVISION="$(containerapp_query "$TARGET_APP" "properties.latestRevisionName")"
update_containerapp "$TARGET_APP" "$IMAGE_TO_DEPLOY" "${UPDATE_ENV_VARS[@]}"

log "[3/5] Waiting for latest revision to become ready"
READY_REVISION="$(wait_for_ready_revision "$TARGET_APP" "$PREVIOUS_LATEST_REVISION")"

log "[4/5] Running smoke checks"
ROOT_HTTP_CODE="$(smoke_check_api "$TARGET_APP")"

log "[5/5] Done"
log "Deployment complete"
log "Target app: ${TARGET_APP}"
log "Ready revision: ${READY_REVISION}"
log "Image: ${IMAGE_TO_DEPLOY}"
log "Root HTTP status: ${ROOT_HTTP_CODE}"
