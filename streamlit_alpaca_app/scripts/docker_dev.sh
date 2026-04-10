#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_CMD=(docker compose -f "${ROOT_DIR}/docker-compose.dev.yml")

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but was not found on PATH." >&2
  exit 1
fi

action="${1:-shell}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "${action}" in
  build)
    exec "${COMPOSE_CMD[@]}" build dev
    ;;
  up)
    exec "${COMPOSE_CMD[@]}" up -d dev
    ;;
  down)
    exec "${COMPOSE_CMD[@]}" down --remove-orphans
    ;;
  shell)
    exec "${COMPOSE_CMD[@]}" run --rm dev bash
    ;;
  test)
    if [[ $# -eq 0 ]]; then
      set -- tests
    fi
    exec "${COMPOSE_CMD[@]}" run --rm dev python -m pytest "$@"
    ;;
  api)
    exec "${COMPOSE_CMD[@]}" run --rm --service-ports dev ./scripts/run_api_local.sh
    ;;
  ui)
    exec "${COMPOSE_CMD[@]}" run --rm --service-ports dev ./scripts/run_ui_local.sh
    ;;
  *)
    cat >&2 <<'EOF'
Usage: ./scripts/docker_dev.sh [build|up|down|shell|test|api|ui] [args...]

Examples:
  ./scripts/docker_dev.sh build
  ./scripts/docker_dev.sh shell
  ./scripts/docker_dev.sh test tests/test_api_v1.py
  ./scripts/docker_dev.sh api
  ./scripts/docker_dev.sh ui
EOF
    exit 1
    ;;
esac
