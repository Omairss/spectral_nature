# Spectral Nature 2 iPhone + Agent Reference Map (2026-04-07)

## Purpose

This map points to the current source-of-truth docs for:

- iPhone app work
- omnibar work
- shared agent/workspace APIs
- auth and MCP transport
- macro/attention reuse

## Canonical Negotiation Resolution

1. `SN2_NEGOTIATION_RESOLUTION_2026-04-07.md`
- Active resolved contract for iPhone + omnibar.
- Use this first for requirements, constraints, adopted clarifications, and implementation order.

2. `agent_omnibar/OTHER_TASK_RESPONSE_2026-04-08.md`
- Explicit ready-to-send response after resolution.
- Use this when another task asks for the concrete settled position.

3. `SN2_NEGOTIATION_BASELINE_2026-04-07.md`
- Historical negotiation baseline.

4. `SN2_NEGOTIATION_RESPONSE_2026-04-07.md`
- Historical negotiation response that fed into the resolution.

## API + Product Behavior

1. `AGENT_API_RESOURCE_CONTRACT_2026-04-07.md`
- Shared REST resources for sessions/messages/runs/artifacts/notes and omnibar resolve.
- The live tool-backed omnibar answer path for external clients belongs in this shared backend contract and should be delivered in the iPhone/shared API track.

2. `AGENTIC_API_AUTH_MCP_2026-04-07.md`
- Auth model, scoped agent keys, and MCP-compatible gateway behavior.

3. `HOMEPAGE_AGENT_WORKSPACE_PLAN_2026-04-06.md`
- Shared product behavior and rollout model for one-bar workspace flow.

## iPhone Delivery Context

1. `IPHONE_APP_STRATEGY_2026-04-05.md`
- High-level migration direction and sequencing.

2. `IPHONE_MVP_SCAFFOLD_2026-04-06.md`
- Current implementation seam and scaffold status.
- Keep standalone omnibar answer execution and deployment in this iPhone delivery track, not in the Streamlit-only task.

## Macro + Attention Reuse

1. `documents/architecture/data_pipelines/ATTENTION_FRED_INTEGRATION_PLAN_2026-04-06.md`
- Macro release and context reuse requirements.

## Omnibar Doc Hub

1. `agent_omnibar/README.md`
- Entrypoint directory for omnibar-specific referencing.
2. `agent_omnibar/OTHER_TASK_RESPONSE_2026-04-08.md`
- Ready-to-send response for the separate omnibar task.

## Reading Order

1. `SN2_NEGOTIATION_RESOLUTION_2026-04-07.md`
2. `agent_omnibar/OTHER_TASK_RESPONSE_2026-04-08.md`
3. `AGENT_API_RESOURCE_CONTRACT_2026-04-07.md`
4. `AGENTIC_API_AUTH_MCP_2026-04-07.md`
5. `HOMEPAGE_AGENT_WORKSPACE_PLAN_2026-04-06.md`
6. `IPHONE_MVP_SCAFFOLD_2026-04-06.md`
7. `documents/architecture/data_pipelines/ATTENTION_FRED_INTEGRATION_PLAN_2026-04-06.md` (if macro reuse is in scope)
