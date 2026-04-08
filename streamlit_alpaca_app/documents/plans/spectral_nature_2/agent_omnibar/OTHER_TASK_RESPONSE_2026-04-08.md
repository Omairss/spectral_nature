# Other Task Response (Omnibar Track) (2026-04-08)

Use this as the direct response in the separate omnibar task.

## Accept As-Is

1. One shared backend truth with no duplicate client orchestration.
2. Shared omnibar contract via `POST /v1/omnibar/resolve`.
3. Shared auth model: access/refresh tokens for users, scoped agent keys for machine clients.
4. MCP compatibility retained with REST parity.
5. Dev-first rollout with explicit provenance/freshness in payloads.

## Propose To Phase

1. Omnibar UX depth (history, ranking, personalization) after baseline parity.
2. Streaming run transport after polling flow is stable.
3. Full typed resource coverage after high-value routes are locked.

## Request Changes

1. Publish explicit scope table for omnibar + workspace routes and enforce it in tests.
2. Require omnibar resolve trace fields: `request_id`, `intent`, `policy_version`, `confidence_band`.
3. Add cross-client parity fixtures so same inputs map to same intent class across homepage and iPhone.

## Non-Negotiable Guardrails

1. No mobile-only omnibar logic fork.
2. No deferred auth/scope enforcement.
3. No client-side text parsing as a substitute for typed artifacts/resources.
