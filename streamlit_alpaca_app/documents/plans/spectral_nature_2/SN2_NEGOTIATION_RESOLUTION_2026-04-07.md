# Spectral Nature 2 Negotiation Resolution (2026-04-07)

## Status

This negotiation is resolved.

The baseline architecture is accepted, and the response clarifications below are adopted into the active contract.

Use this document as the working source of truth for Spectral Nature 2 planning across:

- iPhone app work
- agent omnibar work
- homepage agent workspace work
- shared API/auth/resource contract work

The earlier baseline and response docs remain useful as negotiation history, but they are no longer the active handoff artifact.

## Final Agreement

### Accepted Architecture

1. One backend truth
- Keep business logic and orchestration in shared Python services.
- Do not duplicate routing, state reconstruction, or intent logic in SwiftUI or Streamlit UI code.

2. One shared API contract
- Use shared `/v1/*` resources for homepage and iPhone.
- Use `POST /v1/omnibar/resolve` as the common intent entrypoint.
- Extend shared contracts instead of creating client-specific forks.

3. One auth and scope model
- User clients use access token + refresh token.
- Machine and tool-native clients use scoped agent keys.
- Authorization, scopes, and ownership checks stay server-side.

4. One shared agent/session model
- Reuse shared `session`, `message`, `run`, `artifact`, and `note` resources.
- Do not create homepage-only or iPhone-only workspace state contracts.

5. MCP-compatible transport with REST parity
- Keep `POST /v1/agent/rpc` MCP-compatible.
- Keep REST first-class for product clients.
- Avoid feature drift between REST and MCP without explicit contract updates.

6. Native iPhone client
- The iPhone app remains SwiftUI-native.
- No long-term Streamlit wrapper or WebView strategy.

7. Reliability-first data behavior
- Prefer materialized-first or cached reads.
- Return explicit provenance and freshness.
- Use deterministic fallback behavior on ambiguity or staleness.

8. Dev-first release discipline
- Promote to dev first.
- Do not promote to prod without explicit approval.

## Adopted Clarifications

### 1. Omnibar fast path is required

- Strong exact symbol, entity, bundle, and macro-release matches should resolve through `search` or `navigate` without forcing agent session/run creation.
- The `agent` path is reserved for requests that actually need multi-step reasoning, tool use, or follow-up session context.

### 2. Omnibar trace fields are required in the first contract

Every `POST /v1/omnibar/resolve` response must include:

- `request_id`
- `intent`
- `policy_version`
- `confidence_band`

These fields are mandatory for parity debugging, auditability, and policy-change tracking.

### 3. Route-to-scope mapping is explicit

Reserve and enforce the following scope table:

| Route family | Required scope |
| --- | --- |
| `POST /v1/omnibar/resolve` | `omnibar:resolve` |
| `GET /v1/omnibar/suggestions` | `omnibar:resolve` |
| `GET /v1/agent/sessions*` | `agent:session:read` |
| `POST /v1/agent/sessions*` | `agent:session:write` |
| `POST /v1/agent/messages*` | `agent:session:write` |
| `POST /v1/agent/runs*` | `agent:run` |
| `GET /v1/agent/artifacts*` | `agent:artifact:read` |
| `GET /v1/agent/notes*` | `agent:note:read` |
| `POST`, `PUT`, `DELETE /v1/agent/notes*` | `agent:note:write` |
| `POST /v1/agent/rpc` | `mcp:invoke` |

Contract tests must verify:

- authenticated user access with the expected scopes
- denial on missing scopes
- ownership enforcement for session, run, artifact, and note reads/writes

### 4. Cross-client parity fixtures are required

Add a shared fixture suite where the same input under the same policy version must resolve to the same intent class across homepage and iPhone.

Minimum fixture buckets:

- exact ticker
- exact macro release
- exact bundle id
- short ambiguous query
- natural-language analysis prompt
- follow-up within an existing session

### 5. Artifact-first output is required

- Charts, tables, code outputs, and other structured payloads must be returned as artifacts/resources.
- Clients must not parse assistant prose to reconstruct structured state.

### 6. Macro and attention reuse is shared-client scope

- Macro release objects, macro provenance, and attention/FRED reuse remain shared backend resources.
- This behavior cannot live only in homepage assembly logic.
- iPhone and homepage must consume the same backend macro/attention objects when those features are in scope.

### 7. Tool-backed omnibar answers are shared backend scope

- Agent-mode omnibar requests should be able to execute against the shared MCP/query tool surface to answer questions, not only route users into sections.
- This behavior belongs in shared backend services and shared `/v1/*` contracts, not in Streamlit-only helper logic.
- The first required product consumer for the live HTTP version is the iPhone app, so standalone endpoint and deployment work for this path stays in the iPhone/shared API task.

## Phased Items

The following are accepted, but phased after baseline contract parity:

1. Advanced omnibar ranking, history, and personalization
2. Streaming run transport after async run + polling is stable
3. Broader typed endpoint coverage after the core routes are locked
4. Full native chart rendering breadth after artifact contracts are stable
5. Live omnibar answer execution over the shared tool registry for non-Streamlit clients, owned by the iPhone/shared API track

## Non-Negotiable Guardrails

Reject proposals that:

- put omnibar intent logic in the client
- create mobile-only or homepage-only workspace/session contracts
- defer auth or scope enforcement for speed
- reconstruct artifacts from assistant text
- hide provenance or freshness from clients
- turn the iPhone app into a long-term wrapper around existing UI

## Implementation Order

1. Lock `POST /v1/omnibar/resolve` with fast-path behavior, trace fields, and scope enforcement.
2. Lock the shared session/message/run/artifact/note contract.
3. Enforce auth scopes, ownership checks, and parity fixtures in tests.
4. Add the shared tool-backed omnibar answer path as a backend/API deliverable in the iPhone track.
5. Build homepage and iPhone clients against the same resolver and resource contracts.
6. Add richer UX depth and streaming only after parity is stable.

## Related Docs

1. `SN2_NEGOTIATION_BASELINE_2026-04-07.md`
2. `SN2_NEGOTIATION_RESPONSE_2026-04-07.md`
3. `AGENT_API_RESOURCE_CONTRACT_2026-04-07.md`
4. `AGENTIC_API_AUTH_MCP_2026-04-07.md`
5. `HOMEPAGE_AGENT_WORKSPACE_PLAN_2026-04-06.md`
6. `IPHONE_APP_STRATEGY_2026-04-05.md`
7. `IPHONE_MVP_SCAFFOLD_2026-04-06.md`
8. `documents/architecture/data_pipelines/ATTENTION_FRED_INTEGRATION_PLAN_2026-04-06.md`
