#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck disable=SC1091
source "${ROOT_DIR}/scripts/load_local_env.sh"

RELOAD_FLAG=""
if [[ "${API_SERVER_RELOAD:-true}" == "true" ]]; then
  RELOAD_FLAG="--reload"
fi

exec python -m uvicorn api.main:app \
  --host "${API_SERVER_ADDRESS:-0.0.0.0}" \
  --port "${API_SERVER_PORT:-8080}" \
  ${RELOAD_FLAG}
