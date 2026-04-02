#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"
exec python -m streamlit run app.py --server.port "${STREAMLIT_SERVER_PORT:-8501}" --server.address "${STREAMLIT_SERVER_ADDRESS:-0.0.0.0}"
