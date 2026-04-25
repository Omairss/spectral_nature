# Spectral Nature 2 Negotiation Response (2026-04-07)

## Purpose

This is the explicit response to:

- `SN2_NEGOTIATION_BASELINE_2026-04-07.md`

Use this as the handoff response when another task asks for our position on the shared Spectral Nature 2 contract.

## Overall Position

We accept the baseline direction.

The baseline is correct on the important points:

- one backend truth
- one auth and scope model
- one omnibar behavior across homepage and iPhone
- one shared agent/session contract

That is the right architecture.

## Accepted As-Is

1. One backend truth
- Keep orchestration in Python.
- Do not duplicate core logic in Swift or in the Streamlit UI.

2. Shared omnibar contract
- Use one shared intent resolver:
  - `POST /v1/omnibar/resolve`
- Keep omnibar behavior aligned across homepage and iPhone.

3. Shared auth model
- User clients use access + refresh tokens.
- Machine clients use scoped agent keys.
- Auth and ownership checks remain server-side.

4. Shared agent/session resource model
- Reuse the shared session/message/run/artifact/note shape.
- Do not create a client-specific workspace contract.

5. MCP + REST parity direction
- Keep MCP-compatible transport for tool-native integrations.
- Keep REST first-class for UI clients.

6. Reliability-first data behavior
- Materialized-first and explicit provenance are correct defaults.
- Do not hide freshness/staleness behavior in UI-only logic.

7. Dev-first rollout discipline
- Dev first.
- No prod promotion without explicit approval.

## Accepted, But Should Be Phased

These are correct requirements, but the implementation order matters.

1. Omnibar UX sophistication
- Accept the one-bar model now.
- Phase advanced ranking, history, and personalization after the shared resolver contract is stable.

2. Streaming run transport
- Accept eventual streaming support.
- Start with async run + polling because it is simpler and more reliable across homepage and iPhone.

3. Full typed endpoint coverage
- Accept the goal of typed resources.
- Start with high-value routes first:
  - omnibar resolve
  - session/message/run/artifact/note
  - core home/ticker/portfolio routes

4. Native chart rendering breadth
- Accept native chart rendering as the target.
- Allow image or simpler artifact fallback for the hardest chart types early.

## Requested Changes To The Baseline

The baseline is good, but these additions should be explicit.

### 1) Add a strict fast-path rule for the omnibar

The baseline says one omnibar, which is right. It should also say:

- strong exact symbol/entity/release matches should resolve without forcing an agent run
- the agent path should be reserved for requests that actually need multi-step tool use or natural-language reasoning

Why:

- this keeps the one-bar model fast enough for daily use
- it reduces unnecessary session/run creation
- it preserves a clear difference between lookup and analysis

### 2) Add explicit trace fields on omnibar resolve responses

The baseline already asks for trace fields. That should be treated as mandatory in the first contract, not an optional follow-up.

Minimum trace fields:

- `request_id`
- `intent`
- `policy_version`
- `confidence_band`

Why:

- intent routing needs to be explainable across homepage and iPhone
- parity debugging will be hard without a stable trace surface

### 3) Add explicit cross-client parity fixtures

The baseline already points in this direction. It should be made concrete.

Add a shared fixture set where the same inputs must produce the same intent class on both clients because both clients hit the same backend policy.

Example fixture buckets:

- exact ticker
- exact macro release
- exact bundle id
- short ambiguous query
- natural-language analysis prompt
- follow-up in an existing session

Why:

- this protects the one-omnibar promise
- it catches drift before UX diverges

### 4) Add artifact-first requirement language to the baseline

The baseline should explicitly state:

- charts, tables, and code outputs must be returned as artifacts/resources
- clients should not parse assistant prose to reconstruct structured state

Why:

- this matters a lot for iPhone reliability
- it keeps Streamlit and iPhone rendering aligned

### 5) Add macro/attention reuse as a shared-client requirement

The baseline should explicitly reference that macro release objects and related provenance must remain shared backend resources, not homepage-only logic.

Why:

- otherwise the iPhone task may under-scope macro/attention reuse
- the attention/FRED integration already set the right source-first direction

## Implementation Order We Recommend

1. lock the shared omnibar resolve contract
2. lock the shared agent/session/run/artifact/note contract
3. enforce auth scopes and ownership checks on those routes
4. build homepage top bar against the shared resolver
5. build iPhone against the same resolver and resource contracts
6. add richer UX only after parity and traceability are stable

## Explicit Pushback We Support

We support pushing back on any proposal that does one of the following:

- puts omnibar intent logic in the client
- creates mobile-only workspace/session contracts
- weakens auth/scope requirements for speed
- reconstructs artifacts from assistant text
- hides provenance/freshness from clients
- turns the iPhone app into a long-term wrapper around existing UI

## Summary Response

Accepted:

- the core architecture and the shared-system direction

Accepted with phasing:

- richer omnibar UX
- streaming transport
- full typed endpoint breadth
- full native chart coverage

Requested changes:

1. strict fast-path rule for exact matches
2. required omnibar trace fields in first contract
3. explicit cross-client parity fixture suite
4. explicit artifact-first requirement
5. explicit macro/attention shared-resource requirement

## Related Docs

- `SN2_NEGOTIATION_BASELINE_2026-04-07.md`
- `AGENT_API_RESOURCE_CONTRACT_2026-04-07.md`
- `HOMEPAGE_AGENT_WORKSPACE_PLAN_2026-04-06.md`
- `AGENTIC_API_AUTH_MCP_2026-04-07.md`
- `IPHONE_MVP_SCAFFOLD_2026-04-06.md`
- `documents/architecture/data_pipelines/ATTENTION_FRED_INTEGRATION_PLAN_2026-04-06.md`
