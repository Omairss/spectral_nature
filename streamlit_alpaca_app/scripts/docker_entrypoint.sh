#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(pwd)}"
# shellcheck disable=SC1091
source "${ROOT_DIR}/scripts/load_local_env.sh"

exec "$@"
