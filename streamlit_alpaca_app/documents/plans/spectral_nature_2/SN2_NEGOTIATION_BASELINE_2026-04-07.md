# Spectral Nature 2 Negotiation Baseline (2026-04-07)

## Purpose

This is the single negotiation baseline for:

- iPhone app work
- agent omnibar work
- shared homepage workspace behavior


It replaces separate negotiation docs and defines one contract for requirements, constraints, and tradeoff boundaries.

## Core Position

Spectral Nature 2 must ship as one shared system:

- one backend truth
- one auth and scope model
- one omnibar behavior across homepage and iPhone
- one shared agent/session resource model

Clients should be native and thin. Core orchestration belongs in backend services.

## Non-Negotiable Requirements

1. One backend truth
- Keep business logic in Python (`compute/`, `data_access/`, `services/`).
- No duplicate routing/orchestration logic in Swift or Streamlit UI.

2. One shared API contract
- Use shared `/v1/*` resources for iPhone and homepage.
- Reuse `POST /v1/omnibar/resolve` for intent routing.
- Extend shared contracts instead of creating client-specific forks.

3. Production auth model
- User clients use access token + refresh token.
- Machine/agent clients use scoped agent keys.
- Authorization checks (scope + ownership) stay server-side.
- Auth is in-scope now, not a later phase.

4. Shared agent/session model
- Use shared `session`, `message`, `run`, `artifact`, and `note` resources.
- No iPhone-only agent state model.

5. MCP-compatible transport with REST parity
- Keep `POST /v1/agent/rpc` MCP-compatible for tool-native integrations.
- REST remains first-class for UI clients.
- Do not create feature drift between MCP and REST without contract updates.

6. Native iPhone app requirement
- iPhone client should be SwiftUI-native.
- No long-term Streamlit/WebView wrapper approach.

7. Reliability-first data behavior
- Materialized-first or cached reads by default.
- Explicit provenance and freshness in payloads.
- Deterministic fallback on ambiguity/staleness.
- No silent fallback heuristics.

8. No MVP shortcuts
- Required now: typed contracts, auth scopes, error model, auditability, tests.
- Temporary UI shortcuts are allowed only if they do not break system invariants.

9. Dev-first release discipline
- Promote to dev first.
- Do not promote to prod without explicit approval.

## Constraints

1. Versioned contracts only
- Contract changes must be additive or explicitly versioned.

2. Config-driven behavior
- Intent thresholds, ambiguity rules, and suggestion weights must be config-driven.
- No hardcoded per-client behavior branches.

3. Traceable decisions
- Omnibar intent decisions must be observable.
- Handoff to workspace must preserve request ids and context refs.

4. Cross-client parity
- Same input under same policy should resolve to the same intent class across homepage and iPhone.
- UI differences are allowed; backend decision rules are not.

## Flexible Areas

1. Omnibar UX depth
- Start simple (input + suggestions + resolve).
- Add advanced ranking/history iteratively.

2. Run transport
- Polling-first run updates are acceptable initially.
- Streaming can be phased.

3. Chart rendering details
- Prefer native chart rendering from shared artifacts.
- Temporary server-rendered image artifacts are acceptable for complex traces.

4. Endpoint rollout order
- Deliver highest-value typed resources first, while some lower-priority views can temporarily use generic query endpoints.

## Pushback Triggers

- "Let’s build separate mobile omnibar logic for speed."
- "Let’s delay auth/scopes until later."
- "Let’s parse assistant text in clients to reconstruct state."
- "Let’s keep omnibar rules in UI and call backend only for data."
- "Let’s hide freshness/provenance and infer in UI."

## Required Source Docs

1. `documents/plans/spectral_nature_2/AGENT_API_RESOURCE_CONTRACT_2026-04-07.md`
2. `documents/plans/spectral_nature_2/AGENTIC_API_AUTH_MCP_2026-04-07.md`
3. `documents/plans/spectral_nature_2/HOMEPAGE_AGENT_WORKSPACE_PLAN_2026-04-06.md`
4. `documents/plans/spectral_nature_2/IPHONE_APP_STRATEGY_2026-04-05.md`
5. `documents/plans/spectral_nature_2/IPHONE_MVP_SCAFFOLD_2026-04-06.md`
6. `documents/architecture/data_pipelines/ATTENTION_FRED_INTEGRATION_PLAN_2026-04-06.md`
7. `documents/plans/spectral_nature_2/SN2_IPHONE_AGENT_REFERENCE_MAP_2026-04-07.md`

## Minimum Deliverables

1. iPhone: native SwiftUI shell with shared auth/session integration.
2. Omnibar: shared intent resolver integration using `POST /v1/omnibar/resolve`.
3. Agent: shared resource usage for sessions/messages/runs/artifacts.
4. Security: scope checks and ownership checks wired for omnibar + agent paths.
5. Testing:
- intent classification + ambiguity fallback
- auth/scope enforcement
- cross-client parity fixtures
- artifact payload contract checks
6. Rollout notes with reliability/complexity assessment and known risks.

## Response To The Other Task (Ready To Send)

Use this as the response in the separate omnibar task.

### Accept As-Is

1. One shared backend truth and no duplicated client orchestration.
2. Shared omnibar contract via `POST /v1/omnibar/resolve`.
3. Shared auth model with access/refresh tokens and scoped agent keys.
4. MCP compatibility preserved with REST parity.
5. Dev-first rollout and explicit provenance/freshness.

### Propose To Phase

1. Omnibar UX sophistication (history/ranking/personalization) after baseline parity.
2. Streaming run transport after polling path is stable.
3. Full typed resource coverage after high-value routes are locked.

### Request Changes

1. Make scope names for omnibar and workspace routes explicit in one table and enforce them in tests.
2. Add a required trace field set on omnibar resolve responses (`request_id`, `intent`, `policy_version`, `confidence_band`).
3. Add a cross-client parity test fixture suite (same input set, same expected intent class).

## Negotiation Summary

Protect this order:

1. one backend truth
2. one auth + scope model
3. one omnibar behavior across clients
4. typed shared resource contracts
5. iterative UX improvements that do not violate 1-4
