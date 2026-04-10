# Mistakes Log

## 2026-04-10 - Infra Inventory Could Prefer Stale Local Cache

### What went wrong

- `show_infra_inventory.sh` could inherit `KEYVAULT_NAME` from the ignored local output file before checking the live job env vars.
- That made the local inventory report less reliable during the Key Vault consolidation work.

### Impact

- The script could point us at an old vault name even after the live jobs had already moved.

### Never repeat checklist

1. For live infra reporting, prefer Azure runtime config first and use local generated files only as fallback.
2. When a deploy changes shared infra references, refresh the tracker and re-read the live env before trusting cached output.
3. Treat ignored local env files as convenience state, not source of truth.

## 2026-04-10 - Tracked `.env` Held Real Broker Credentials

### What went wrong

- Real Alpaca credentials were committed in `streamlit_alpaca_app/.env`.
- The repo root ignore rules did not block `.env` files.
- The Docker build context also allowed `.env`, so local secret files could have been sent to remote builds.

### Impact

- Secrets were present in plaintext on disk, in git history, and potentially in build context uploads.

### Never repeat checklist

1. Ignore `.env` and `.env.*` at the repo root, while explicitly keeping `.env.example`.
2. Mirror the same exclusions in `.dockerignore` so build contexts do not upload local secret files.
3. For production credentials, load from Key Vault secret names only; do not allow direct raw env fallbacks unless there is a deliberate local-only need.
4. When rotating secrets, remove the tracked file in the same change so the repo state matches the new security model.

## 2026-04-08 - Assumed `python` Alias Existed

### What went wrong

- I used `python` for an environment check before confirming that this workspace exposes that alias.

### Impact

- Lost one command cycle before re-running the check with `python3`.

### Never repeat checklist

1. Use `python3` for repo-level checks unless the environment has already proven that `python` exists.
2. Keep notebook setup commands tied to `sys.executable` so they use the active kernel environment.
