#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DEPLOYMENT_ENV_FILE="${DEPLOYMENT_ENV_FILE:-infra/deployment.outputs.env}"
STATUS_FILE="${STATUS_FILE:-infra/UI_DEPLOYMENT_STATUS.md}"
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

usage() {
  cat <<'EOF'
Usage:
  ./scripts/deploy_ui_azure.sh [options]

Options:
  --target dev|prod          Target UI app. Default: dev
  --promote-from dev|prod    Reuse the current image from another UI app
  --image-ref REF            Deploy an existing image ref or repo:tag
  --image-tag REPO:TAG       Override the tag used when building a new image
  --refresh-tracker-only     Skip deployment and rewrite infra/UI_DEPLOYMENT_STATUS.md
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

load_deployment_context() {
  if [[ -f "$DEPLOYMENT_ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$DEPLOYMENT_ENV_FILE"
  fi

  RESOURCE_GROUP="${RESOURCE_GROUP:-${PIPELINE_RESOURCE_GROUP:-}}"
  ACR_NAME="${ACR_NAME:-}"
}

ensure_azure_ready() {
  require_command az
  require_command curl

  if ! az account show >/dev/null 2>&1; then
    die "Azure CLI is not authenticated. Run: az login --use-device-code"
  fi

  az extension add --name containerapp --upgrade >/dev/null
}

ensure_registry_context() {
  [[ -n "$RESOURCE_GROUP" ]] || die "RESOURCE_GROUP is required. Source $DEPLOYMENT_ENV_FILE or export RESOURCE_GROUP."
  [[ -n "$ACR_NAME" ]] || die "ACR_NAME is required. Source $DEPLOYMENT_ENV_FILE or export ACR_NAME."

  if [[ -z "$REGISTRY_SERVER" ]]; then
    REGISTRY_SERVER="$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query loginServer -o tsv)"
  fi
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
  az containerapp show -n "$source_app" -g "$RESOURCE_GROUP" --query "properties.template.containers[0].image" -o tsv
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

  if ! az containerapp show -n "$app_name" -g "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
    echo "| **${role_label}** | \`${app_name}\` | n/a | \`n/a\` | \`n/a\` | Unavailable |"
    return 0
  fi

  local fqdn
  local image
  local ready_revision
  local url
  local http_code

  fqdn="$(app_fqdn "$app_name")"
  image="$(az containerapp show -n "$app_name" -g "$RESOURCE_GROUP" --query "properties.template.containers[0].image" -o tsv)"
  ready_revision="$(az containerapp show -n "$app_name" -g "$RESOURCE_GROUP" --query "properties.latestReadyRevisionName" -o tsv)"
  url="https://${fqdn}"
  http_code="$(root_http_code_for_url "$url")"

  echo "| **${role_label}** | \`${app_name}\` | ${url} | \`${ready_revision:-n/a}\` | \`${image:-n/a}\` | HTTP ${http_code} |"
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

| Role | Container App | URL | Latest Revision | Image | Health |
|---|---|---|---|---|---|
${prod_row}
${dev_row}

## Promotion Workflow

1. Deploy new UI changes to **Development** app (\`${DEV_CONTAINER_APP}\`) first.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (\`${PROD_CONTAINER_APP}\`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- Both apps use the same managed identity and Key Vault-based auth configuration.
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

if [[ -n "$PROMOTE_FROM" ]]; then
  log "[1/5] Resolving image from $(target_app_name "$PROMOTE_FROM")"
  IMAGE_TO_DEPLOY="$(image_from_app "$PROMOTE_FROM")"
  [[ -n "$IMAGE_TO_DEPLOY" ]] || die "No image found on $(target_app_name "$PROMOTE_FROM")"
  IMAGE_TO_DEPLOY="$(resolve_image_ref "$IMAGE_TO_DEPLOY")"
elif [[ -n "$IMAGE_REF" ]]; then
  log "[1/5] Resolving provided image reference"
  IMAGE_TO_DEPLOY="$(resolve_image_ref "$IMAGE_REF")"
else
  build_ui_image
fi

log "[2/5] Updating ${TARGET_APP}"
az containerapp update \
  -n "$TARGET_APP" \
  -g "$RESOURCE_GROUP" \
  --image "$IMAGE_TO_DEPLOY" \
  --set-env-vars APP_RELEASE_TS="$RELEASE_TS" AZURE_STORAGE_CONTAINER="$AZURE_STORAGE_CONTAINER" \
  >/dev/null

log "[3/5] Waiting for latest revision to become ready"
READY_REVISION="$(wait_for_ready_revision "$TARGET_APP")"

log "[4/5] Running smoke checks"
ROOT_HTTP_CODE="$(smoke_check_app "$TARGET_APP")"

log "[5/5] Refreshing UI deployment tracker"
write_status_tracker

log "Deployment complete"
log "Target app: ${TARGET_APP}"
log "Ready revision: ${READY_REVISION}"
log "Image: ${IMAGE_TO_DEPLOY}"
log "Root HTTP status: ${ROOT_HTTP_CODE}"
