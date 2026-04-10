# Other Task Response (Omnibar Track) (Resolved)

Use this as the direct response in the separate omnibar task after the negotiation was resolved.

## Resolved Position

Accepted:

1. One shared backend truth with no duplicate client orchestration.
2. Shared omnibar contract via `POST /v1/omnibar/resolve`.
3. Shared auth model with access/refresh tokens for users and scoped agent keys for machine clients.
4. Shared agent/session/artifact model across homepage and iPhone.
5. MCP compatibility retained with REST parity.
6. Dev-first rollout with explicit provenance/freshness in payloads.
7. Tool-backed answers for agent-mode omnibar requests belong in shared backend services, not in Streamlit-only UI logic.

Adopted clarifications:

1. Exact symbol, entity, bundle, and macro-release matches use a strict fast path and should not force an agent run.
2. Omnibar resolve responses must include `request_id`, `intent`, `policy_version`, and `confidence_band`.
3. Route-to-scope mapping is explicit and must be enforced in tests.
4. Cross-client parity fixtures are required so the same input maps to the same intent class across homepage and iPhone.
5. Structured outputs must be artifact-first; clients must not parse assistant prose to rebuild state.
6. Macro/attention reuse remains shared backend behavior, not homepage-only logic.

Still phased:

1. Omnibar UX depth such as history, ranking, and personalization
2. Streaming run transport after polling flow is stable
3. Broader typed endpoint coverage after high-value routes are locked
4. Full native chart breadth after the artifact contract is stable
5. Live deployed omnibar answer execution over the shared MCP/query tool surface for external clients, owned by the iPhone/shared API task

## Non-Negotiable Guardrails

1. No mobile-only omnibar logic fork.
2. No homepage-only workspace/session contract.
3. No deferred auth/scope enforcement.
4. No client-side text parsing as a substitute for typed artifacts/resources.
5. No hidden freshness/provenance behavior.
