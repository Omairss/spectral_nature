#!/usr/bin/env bash
# Run a SQL query or Python snippet against the production Postgres database
# from your local machine.
#
# Handles the firewall lifecycle automatically:
#   1. Adds a temporary firewall rule for your current public IP
#   2. Runs the query
#   3. Removes the firewall rule (even on error/interrupt)
#
# Usage:
#   scripts/query_db.sh "SELECT count(*) FROM aql_chat_sessions"
#   scripts/query_db.sh --recent-chats          # shortcut: last 15 chat sessions
#   scripts/query_db.sh --chat <run_id>          # shortcut: full session by run_id
#   scripts/query_db.sh --tables                 # shortcut: list all tables
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Load deployment config ──
ENV_FILE="$ROOT_DIR/infra/.generated/deployment.local.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: $ENV_FILE not found. Run a deploy first to generate it." >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

RESOURCE_GROUP="${RESOURCE_GROUP:-sn-pipeline-rg-03130136}"
POSTGRES_SERVER="${POSTGRES_SERVER:-sn-pg-03130136}"
KEYVAULT_NAME="${KEYVAULT_NAME:-spectral-nature-kvault}"
RULE_NAME="local-query-$(date +%s)"

# ── Resolve public IP ──
PUBLIC_IP="$(curl -s --max-time 5 ifconfig.me || curl -s --max-time 5 api.ipify.org || true)"
if [[ -z "$PUBLIC_IP" ]]; then
  echo "Error: could not determine public IP." >&2
  exit 1
fi

# ── Cleanup trap: always remove firewall rule ──
cleanup() {
  echo ""
  echo "Removing firewall rule ${RULE_NAME}..."
  az postgres flexible-server firewall-rule delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "$POSTGRES_SERVER" \
    --rule-name "$RULE_NAME" \
    --yes --output none 2>/dev/null || true
  echo "Firewall rule removed."
}
trap cleanup EXIT INT TERM

# ── Add firewall rule ──
echo "Adding temporary firewall rule for ${PUBLIC_IP}..."
az postgres flexible-server firewall-rule create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$POSTGRES_SERVER" \
  --rule-name "$RULE_NAME" \
  --start-ip-address "$PUBLIC_IP" \
  --end-ip-address "$PUBLIC_IP" \
  --output none

echo "Firewall rule active. Running query..."
echo ""

# ── Resolve connection string from Key Vault ──
CONN_STRING="$(az keyvault secret show \
  --vault-name "$KEYVAULT_NAME" \
  --name postgres-connection-string \
  --query value -o tsv 2>/dev/null)"

if [[ -z "$CONN_STRING" ]]; then
  echo "Error: could not resolve postgres-connection-string from Key Vault." >&2
  exit 1
fi

# ── Handle shortcut flags ──
SQL=""
case "${1:-}" in
  --recent-chats)
    SQL="SELECT created_at, status, confidence, tool_call_count, duration_seconds,
                query
         FROM aql_chat_sessions
         ORDER BY created_at DESC
         LIMIT 15"
    ;;
  --chat)
    if [[ -z "${2:-}" ]]; then
      echo "Usage: query_db.sh --chat <run_id>" >&2
      exit 1
    fi
    SQL="SELECT run_id, query, status, confidence, tool_call_count,
                answer_preview, tool_names_json, symbols_json,
                error_text, created_at, duration_seconds
         FROM aql_chat_sessions
         WHERE run_id = '${2}'"
    ;;
  --tables)
    SQL="SELECT table_name FROM information_schema.tables
         WHERE table_schema = 'public' ORDER BY table_name"
    ;;
  *)
    SQL="${1:-}"
    ;;
esac

if [[ -z "$SQL" ]]; then
  echo "Usage: query_db.sh <SQL> | --recent-chats | --chat <run_id> | --tables" >&2
  exit 1
fi

# ── Run query ──
python3 -c "
import psycopg, sys

conn = psycopg.connect('''${CONN_STRING}''', connect_timeout=15)
cur = conn.cursor()
cur.execute('''${SQL}''')

if cur.description:
    headers = [d.name for d in cur.description]
    rows = cur.fetchall()
    # Print header
    print('\t'.join(headers))
    print('\t'.join('-' * max(len(h), 4) for h in headers))
    for row in rows:
        print('\t'.join(str(v)[:120] if v is not None else '-' for v in row))
    print(f'\n({len(rows)} row(s))')
else:
    print('Query executed (no result set).')

conn.close()
"
