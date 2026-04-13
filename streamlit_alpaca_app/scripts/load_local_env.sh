#!/usr/bin/env bash

ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

resolve_local_env_path() {
  local path="$1"
  if [[ -z "$path" ]]; then
    return 1
  fi
  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s/%s\n' "$ROOT_DIR" "$path"
  fi
}

load_local_env_file() {
  local path="$1"
  [[ -f "$path" ]] || return 0
  set -a
  # shellcheck disable=SC1090
  source "$path"
  set +a
}

load_first_available_local_env() {
  local preferred="$1"
  local legacy="$2"
  if [[ -f "$preferred" ]]; then
    load_local_env_file "$preferred"
    return 0
  fi
  if [[ "$preferred" != "$legacy" && -f "$legacy" ]]; then
    load_local_env_file "$legacy"
  fi
}

normalize_local_keyvault_env() {
  local canonical="${KEYVAULT_NAME:-${AZURE_KEY_VAULT_NAME:-${KEY_VAULT_NAME:-}}}"
  [[ -n "$canonical" ]] || return 0
  export KEYVAULT_NAME="$canonical"
  export AZURE_KEY_VAULT_NAME="$canonical"
  export KEY_VAULT_NAME="$canonical"
}

DEFAULT_DEPLOYMENT_ENV_FILE_ABS="$(resolve_local_env_path "infra/.generated/deployment.local.env")"
LEGACY_DEPLOYMENT_ENV_FILE_ABS="$(resolve_local_env_path "infra/deployment.outputs.env")"
DEFAULT_EMAIL_OUTPUTS_FILE_ABS="$(resolve_local_env_path "infra/.generated/email_delivery.local.env")"
LEGACY_EMAIL_OUTPUTS_FILE_ABS="$(resolve_local_env_path "infra/email_delivery.outputs.env")"
DOTENV_FILE_ABS="$(resolve_local_env_path "${DOTENV_FILE:-.env}")"

PREFERRED_DEPLOYMENT_ENV_FILE_ABS="$(resolve_local_env_path "${DEPLOYMENT_ENV_FILE:-infra/.generated/deployment.local.env}")"
PREFERRED_EMAIL_OUTPUTS_FILE_ABS="$(resolve_local_env_path "${EMAIL_OUTPUTS_FILE:-infra/.generated/email_delivery.local.env}")"

load_first_available_local_env "$PREFERRED_DEPLOYMENT_ENV_FILE_ABS" "$LEGACY_DEPLOYMENT_ENV_FILE_ABS"
load_first_available_local_env "$PREFERRED_EMAIL_OUTPUTS_FILE_ABS" "$LEGACY_EMAIL_OUTPUTS_FILE_ABS"

# Load .env last so explicit local overrides win over generated convenience files.
load_local_env_file "$DOTENV_FILE_ABS"
normalize_local_keyvault_env
