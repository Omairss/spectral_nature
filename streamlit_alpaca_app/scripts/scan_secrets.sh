#!/usr/bin/env bash
# Scan staged files for patterns that look like leaked secrets.
# Designed to run as a pre-commit hook or standalone.
#
# Usage:
#   ./scripts/scan_secrets.sh          # scan staged files
#   ./scripts/scan_secrets.sh --all    # scan entire repo
#
# Install as pre-commit hook:
#   ln -sf ../../streamlit_alpaca_app/scripts/scan_secrets.sh .git/hooks/pre-commit
set -euo pipefail

# Patterns that strongly indicate a real secret value (not a key name or placeholder).
# Each pattern is a label:regex pair.
SECRET_PATTERNS=(
  "AWS Access Key:AKIA[0-9A-Z]{16}"
  "Azure Storage Key:[A-Za-z0-9+/]{86}=="
  "Generic API Key:(api[_-]?key|apikey|api[_-]?secret)['\"]?\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,}['\"]"
  "Connection String:(postgresql|mysql|mongodb|redis|amqp)://[^\s'\"]{10,}"
  "Bearer Token:bearer\s+[A-Za-z0-9_\-\.]{20,}"
  "Private Key:-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"
  "Azure SAS Token:sig=[A-Za-z0-9%+/]{20,}"
  "Password Assignment:(password|passwd|pwd)['\"]?\s*[:=]\s*['\"][^'\"]{8,}['\"]"
)

# Files that are allowed to contain secret-like patterns (key names, not values).
ALLOWLIST_PATTERNS=(
  "*.example"
  "*.md"
  "*.sh"        # deploy scripts reference secret *names*, not values
  "scan_secrets.sh"
)

MODE="${1:-staged}"

if [[ "$MODE" == "--all" ]]; then
  FILES="$(git ls-files -- '*.py' '*.json' '*.yaml' '*.yml' '*.toml' '*.cfg' '*.ini' '*.env' '*.ipynb' 2>/dev/null || true)"
else
  FILES="$(git diff --cached --name-only --diff-filter=ACM -- '*.py' '*.json' '*.yaml' '*.yml' '*.toml' '*.cfg' '*.ini' '*.env' '*.ipynb' 2>/dev/null || true)"
fi

if [[ -z "$FILES" ]]; then
  exit 0
fi

is_allowlisted() {
  local file="$1"
  local basename
  basename="$(basename "$file")"
  for pattern in "${ALLOWLIST_PATTERNS[@]}"; do
    case "$basename" in
      $pattern) return 0 ;;
    esac
  done
  return 1
}

FOUND=0

while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  [[ ! -f "$file" ]] && continue
  is_allowlisted "$file" && continue

  for entry in "${SECRET_PATTERNS[@]}"; do
    label="${entry%%:*}"
    pattern="${entry#*:}"
    # Use git show for staged content, or cat for --all
    if [[ "$MODE" == "--all" ]]; then
      matches="$(grep -nPi "$pattern" "$file" 2>/dev/null || true)"
    else
      matches="$(git show ":$file" 2>/dev/null | grep -nPi "$pattern" 2>/dev/null || true)"
    fi
    if [[ -n "$matches" ]]; then
      if ((FOUND == 0)); then
        echo "SECRET SCAN FAILED — potential secrets detected in staged files:"
        echo ""
      fi
      FOUND=1
      echo "  $file ($label):"
      echo "$matches" | head -3 | sed 's/^/    /'
      echo ""
    fi
  done
done <<< "$FILES"

if ((FOUND)); then
  echo "Remove the secrets and use Key Vault / env vars / .env (gitignored) instead."
  echo "If this is a false positive, add the file pattern to ALLOWLIST_PATTERNS in this script."
  exit 1
fi

exit 0
