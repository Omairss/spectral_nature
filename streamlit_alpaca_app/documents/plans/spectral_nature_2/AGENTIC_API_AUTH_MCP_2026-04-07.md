# Agentic API + Auth + MCP Gateway (2026-04-07)

## Goal

Upgrade the external API from a thin mobile shim into a reusable, agent-ready gateway with:

- first-class auth model,
- scoped machine credentials,
- MCP-compatible tool transport,
- one shared query execution core.

## What was implemented

## 1) Unified auth model

Added `services/api_auth.py` as the security/control plane used by `api/main.py`.

Principal types:

- `user` (database-authenticated session user),
- `agent` (API key credential),
- `anonymous` (dev-only when database auth is disabled).

Token model:

- login issues:
  - short-lived signed `access_token` (bearer)
  - long-lived `refresh_token` (session-backed)
- refresh endpoint supports refresh-token rotation
- bearer auth supports:
  - new signed access tokens
  - legacy session tokens (backward compatibility)

Scopes:

- read/query scopes: `capabilities:read`, `dataset:read`, `chart:read`, `query:execute`
- agent/protocol scopes: `mcp:invoke`, `agent:run`
- admin scopes: `auth:agent_keys:read`, `auth:agent_keys:write`

## 2) Persistent agent credentials

Extended auth schema in `services/auth_store.py` with `agent_api_keys` table.

Stored fields include:

- key metadata (`name`, `key_prefix`, `notes`)
- `token_hash` (secret never stored in plaintext)
- scopes (`scopes_json`)
- lifecycle controls (`status`, `expires_at`, `revoked_at`, `last_used_at`)
- ownership/audit (`created_by`, timestamps)

Added store operations:

- `create_agent_api_key`
- `get_agent_api_key_by_hash`
- `list_agent_api_keys`
- `revoke_agent_api_key`

## 3) Agent gateway protocol surface

`api/main.py` now exposes both REST and MCP-compatible JSON-RPC:

REST:

- auth: login / refresh / logout / me / auth-status
- admin key management: create/list/revoke agent keys
- query: capabilities / query / dataset / chart
- tools: list tools + invoke a tool by name

MCP-compatible RPC endpoint:

- `POST /v1/agent/rpc`
- supported methods:
  - `mcp.initialize`
  - `mcp.tools.list`
  - `mcp.tools.call`
  - `rpc.ping`
- tool catalog is generated from `QueryService` capabilities
- tool invocation routes back to the same query execution path as REST

## 4) Authorization behavior

- Every executable endpoint validates principal scopes.
- Query operations enforce operation-level scopes (`dataset` vs `chart` vs `capabilities`).
- Agent key administration is admin-user only.
- Agent keys are intended for machine workloads and are scope-limited by design.

## 5) Backward compatibility

- Legacy bearer session tokens remain accepted while clients migrate to access/refresh flow.
- Existing query semantics and payload contracts remain intact.

## 6) Verification

Added tests:

- `tests/test_api_v1.py`
  - login token contract
  - scope enforcement
  - MCP tool call path
  - admin key creation path
- `tests/test_api_auth.py`
  - token issuance + access-token principal resolution
  - agent key scope normalization

Status:

- `pytest -q tests/test_api_v1.py tests/test_api_auth.py` passed.

## Operational notes

- Access token signing key resolution:
  - env/secret: `API_ACCESS_TOKEN_SECRET` (or Key Vault secret name)
  - fallback to runtime secret if not configured (dev-safe, restart-invalidating)
- For stable multi-instance production, configure a shared signing secret explicitly.

## Next hardening steps

1. Add request-level audit logging for principal id, scopes, method/tool, and result status.
2. Add per-principal and per-scope rate limits.
3. Add optional mTLS or private network ingress policy for agent endpoints.
4. Add explicit token introspection/revocation endpoint for access tokens if external policy engines are required.

