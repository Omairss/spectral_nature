#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(pwd)}"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

if [[ -f "${ROOT_DIR}/infra/deployment.outputs.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/infra/deployment.outputs.env"
  set +a
fi

exec "$@"
