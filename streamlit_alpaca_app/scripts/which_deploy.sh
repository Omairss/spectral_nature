#!/usr/bin/env bash
# Deploy guard: checks which container(s) have changed files and warns
# if the caller is about to deploy the wrong one.
#
# Usage:
#   ./scripts/which_deploy.sh              # prints which containers need deploying
#   ./scripts/which_deploy.sh --check ui   # exits 0 if UI has changes, 1 otherwise
#   ./scripts/which_deploy.sh --check pipeline
#   ./scripts/which_deploy.sh --check api
#
# Source directories per container:
#   UI       — app.py, presentation/, config/, branding/, services/* (request-time only)
#   Pipeline — pipeline/, compute/, services/aql/, services/saa/, services/attention_home_summary.py,
#              services/attention_agentic.py, services/attention_live_research.py,
#              services/attention_market_events.py, services/attention_home_1d.py,
#              services/attention_ticker_snapshots.py, services/omnibar*.py,
#              services/web_research.py, services/page_browsing.py,
#              services/seeking_alpha_access.py, services/signals.py
#   API      — api/
#   Shared   — services/ (files used by both UI and pipeline), data_access/, requirements.txt
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Default: compare against HEAD~1. Override with DEPLOY_GUARD_BASE.
BASE="${DEPLOY_GUARD_BASE:-HEAD~1}"

# If there are uncommitted changes, include them too.
CHANGED_FILES="$(git diff --name-only "$BASE" HEAD -- . 2>/dev/null || true)"
UNSTAGED="$(git diff --name-only -- . 2>/dev/null || true)"
UNTRACKED="$(git ls-files --others --exclude-standard -- . 2>/dev/null || true)"
ALL_CHANGED="$(printf '%s\n%s\n%s' "$CHANGED_FILES" "$UNSTAGED" "$UNTRACKED" | sort -u | grep -v '^$' || true)"

if [[ -z "$ALL_CHANGED" ]]; then
  echo "No changed files detected (base=$BASE)."
  exit 0
fi

# Classify each changed file
UI_CHANGES=()
PIPELINE_CHANGES=()
API_CHANGES=()
SHARED_CHANGES=()

# Pipeline-only service files (these run inside the pipeline job, not the UI)
PIPELINE_SERVICE_PATTERNS=(
  "services/aql/"
  "services/saa/"
  "services/attention_home_summary.py"
  "services/attention_agentic.py"
  "services/attention_live_research.py"
  "services/attention_market_events.py"
  "services/attention_home_1d.py"
  "services/attention_ticker_snapshots.py"
  "services/attention_materialized.py"
  "services/omnibar"
  "services/web_research.py"
  "services/page_browsing.py"
  "services/seeking_alpha_access.py"
  "services/signals.py"
  "services/elevenlabs_tts.py"
)

is_pipeline_service() {
  local file="$1"
  for pattern in "${PIPELINE_SERVICE_PATTERNS[@]}"; do
    if [[ "$file" == *"$pattern"* ]]; then
      return 0
    fi
  done
  return 1
}

while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  # Strip streamlit_alpaca_app/ prefix if present
  rel="${file#streamlit_alpaca_app/}"

  case "$rel" in
    pipeline/*|compute/*)
      PIPELINE_CHANGES+=("$rel")
      ;;
    api/*)
      API_CHANGES+=("$rel")
      ;;
    app.py|presentation/*|branding/*|config/*)
      UI_CHANGES+=("$rel")
      ;;
    services/*)
      if is_pipeline_service "$rel"; then
        PIPELINE_CHANGES+=("$rel")
      else
        SHARED_CHANGES+=("$rel")
      fi
      ;;
    data_access/*|requirements.txt|Dockerfile.*)
      SHARED_CHANGES+=("$rel")
      ;;
    *)
      # docs, tests, scripts, etc. — informational only
      ;;
  esac
done <<< "$ALL_CHANGED"

MODE="${1:-}"
CHECK_TARGET="${2:-}"

print_section() {
  local label="$1"
  shift
  local files=("$@")
  if ((${#files[@]} > 0)); then
    echo "  $label (${#files[@]} files)"
    for f in "${files[@]}"; do
      echo "    - $f"
    done
  fi
}

if [[ "$MODE" == "--check" ]]; then
  case "$CHECK_TARGET" in
    ui)
      if ((${#UI_CHANGES[@]} > 0 || ${#SHARED_CHANGES[@]} > 0)); then
        exit 0
      else
        echo "WARNING: No UI-relevant files changed. Are you sure you want to deploy the UI container?"
        exit 1
      fi
      ;;
    pipeline)
      if ((${#PIPELINE_CHANGES[@]} > 0 || ${#SHARED_CHANGES[@]} > 0)); then
        exit 0
      else
        echo "WARNING: No pipeline-relevant files changed. Are you sure you want to deploy the pipeline container?"
        exit 1
      fi
      ;;
    api)
      if ((${#API_CHANGES[@]} > 0 || ${#SHARED_CHANGES[@]} > 0)); then
        exit 0
      else
        echo "WARNING: No API-relevant files changed. Are you sure you want to deploy the API container?"
        exit 1
      fi
      ;;
    *)
      echo "Usage: $0 --check ui|pipeline|api"
      exit 2
      ;;
  esac
fi

# Default: print summary
echo "Changed files since $BASE:"
echo ""

NEEDS_DEPLOY=()

if ((${#UI_CHANGES[@]} > 0)); then
  NEEDS_DEPLOY+=("UI (deploy_ui_azure.sh)")
  print_section "UI container" "${UI_CHANGES[@]}"
fi
if ((${#PIPELINE_CHANGES[@]} > 0)); then
  NEEDS_DEPLOY+=("Pipeline (deploy_pipeline_azure.sh)")
  print_section "Pipeline container" "${PIPELINE_CHANGES[@]}"
fi
if ((${#API_CHANGES[@]} > 0)); then
  NEEDS_DEPLOY+=("API (deploy_api_azure.sh)")
  print_section "API container" "${API_CHANGES[@]}"
fi
if ((${#SHARED_CHANGES[@]} > 0)); then
  print_section "Shared (affects all containers)" "${SHARED_CHANGES[@]}"
  if [[ ! " ${NEEDS_DEPLOY[*]:-} " =~ "UI" ]]; then
    NEEDS_DEPLOY+=("UI (deploy_ui_azure.sh)")
  fi
  if [[ ! " ${NEEDS_DEPLOY[*]:-} " =~ "Pipeline" ]]; then
    NEEDS_DEPLOY+=("Pipeline (deploy_pipeline_azure.sh)")
  fi
fi

echo ""
if ((${#NEEDS_DEPLOY[@]} > 0)); then
  echo "Containers that need deploying:"
  for target in "${NEEDS_DEPLOY[@]}"; do
    echo "  -> $target"
  done
else
  echo "No application containers affected by these changes."
fi
