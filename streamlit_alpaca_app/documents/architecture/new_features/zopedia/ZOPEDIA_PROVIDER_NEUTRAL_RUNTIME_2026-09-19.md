# Zopedia Provider-Neutral Research Runtime

Date: 2026-09-19
Status: implementation contract

## Decision

Spectral Nature will own its agent runtime. DeepSeek is the current model provider and a local GPU inference service is the expected next transport. The runtime must not depend on a hosted agent SDK, hosted session store, provider-managed memory, or provider-specific tool protocol.

The stable boundary is a small JSON model client:

```text
generate_json(system_prompt, user_prompt, schema_name, schema) -> object
```

Provider adapters may use different wire formats, but the research runtime does not know which provider is behind that boundary.

## Ownership

The application owns:

- research session identity and lifecycle
- append-only progress and execution events
- tool definitions, discovery, invocation, permissions, and provenance
- evidence, source text, and freshness state
- Zopedia semantic memory and proposals
- checkpoints, cancellation state, and future resume behavior
- model-call audit rows
- final product artifacts

The model owns:

- research strategy
- tool choice
- interpretation of evidence
- deciding when a grounded partial or complete answer is ready
- draft and critic judgments

The model provider owns only inference.

## Runtime Shape

```text
run_aql_zopedia_agent(...)
  -> ResearchSession
      -> append progress/event records
      -> researcher loop
          -> JSON model client (DeepSeek or local)
          -> ToolRegistry
              -> tool search
              -> tool invocation
          -> evidence journal
      -> critic
      -> final result
  -> Zopedia memory and materialized product artifacts
```

`run_aql_zopedia_agent(...)` remains the public entry point. Product surfaces must not call a provider-specific runtime directly.

## Session State

The session ledger is separate from chat history and Zopedia memory.

`aql_research_sessions` stores one current row per run:

- run id, surface, task, and query hash
- status and timestamps
- provider and model identity when known
- latest event sequence
- final result summary or sanitized error

`aql_research_session_events` is append-only:

- run id and monotonic sequence
- event type/stage
- user-safe message and progress
- sanitized structured payload
- creation timestamp

The first implementation records the existing progress stream durably. A later phase will add resumable checkpoints containing the complete researcher state. Event persistence must never make a research run fail when the database is unavailable.

## Model Transport

The runtime uses a structural `JSONLLMClient` protocol instead of unions of provider classes.

Supported transports:

- `deepseek`: authenticated DeepSeek chat-completions endpoint
- `local`: local or private chat-completions-compatible HTTP endpoint; API key optional
- existing transports remain compatibility adapters, not architectural dependencies

The local transport must work without importing an SDK. Model name, base URL, timeout, and temperature come from the existing environment configuration.

## Tool Registry

The tool catalog becomes a `ToolRegistry` with one canonical definition per tool. The existing `build_tool_catalog(...)` function remains as a compatibility view.

The registry provides:

- exact lookup and namespace grouping
- write-policy filtering
- bounded lexical search over names, descriptions, schemas, examples, failure modes, and empty-result guidance
- a stable `tools.search` capability for deferred discovery

Tool search is model-directed. Code ranks tool metadata; it does not classify the user's intent or hardcode query-to-tool routes.

Deferred discovery will be activated only after replay evaluation. The initial integration keeps the complete catalog available to the current planner so this infrastructure change does not silently degrade research quality.

## Memory Boundaries

These stores must remain distinct:

1. Active context: bounded prompt context for the current inference call.
2. Session ledger: durable execution events and future checkpoints.
3. Episodic research: completed evidence journals and source bundles.
4. Semantic memory: source-backed Zopedia pages, events, relationships, and proposals.
5. Product artifacts: reports, summaries, forecasts, and feed rows.

A session event is not automatically a Zopedia fact. Promotion into semantic memory continues through the existing evidence and proposal contracts.

## Complexity Removal

The migration should remove, not duplicate, orchestration:

- one model protocol replaces provider-class unions
- one session wrapper owns lifecycle/event persistence
- one tool registry owns discovery and exact lookup
- one gateway owns model-call telemetry
- one researcher loop eventually replaces planner, trajectory-kill, fallback synthesis, and feature-local research loops

Thread-based deadlines in the current loop do not cancel underlying work. They remain a known transition issue. The durable runtime must not describe an abandoned thread as cancelled. Model transport timeouts and tool-level cooperative deadlines should replace these wrappers as tools migrate.

## Rollout

1. Add the model protocol, local adapter, session ledger, and tool registry without changing research decisions.
2. Persist the current progress stream and link model-call events to the run id.
3. Replay real Zopedia failures and inspect the complete event trail.
4. Enable deferred tool discovery in development and evaluate it with DeepSeek.
5. Replace the planner/monitor split with the researcher/critic loop described in the existing researcher runtime plan.
6. Add resumable checkpoints only after the researcher state contract is stable.
7. Move Trading Agent and materialized surfaces onto the shared runtime after Zopedia chat quality is acceptable.

## Development Deployment Verification

Verified on 2026-09-20 against the development Container Apps environment:

- pipeline jobs use `pipeline-jobs:20260919221758`; UI revision `0000435` and API revision `0000046` are ready and running
- all 14 scheduled jobs resolve `LLM_PROVIDER=deepseek` and `LLM_MODEL=deepseek-reasoner`
- live research run `agrun_76639664e6` completed through the shared runtime with one FRED tool call, a grounded answer, critic revision, and preserved Markdown headings, paragraphs, bullets, and table structure
- the session ledger contains 18 distinct events with an exact sequence of 1 through 18 and no sensitive-payload matches
- all four model-call rows are successful and carry the same run id in both the indexed column and metadata; the chat row is also complete
- the next normal scheduled `system-health` execution succeeded after the one-off probe, confirming that execution overrides did not mutate the job template
- desktop and mobile browser renders reached the live Streamlit app with no exceptions, console errors, page errors, or horizontal overflow; the Plotly graph rendered after its asynchronous load interval

This deployment adds no OpenAI or Anthropic runtime dependency. DeepSeek remains the configured transport, and the unauthenticated local chat-completions adapter remains the migration path to a local GPU service.

Sessions are durable but intentionally report `resumable=false`; complete researcher checkpoints remain a later architectural phase. Deferred tool discovery also remains disabled by default until the qualitative replay set justifies enabling it.

No production rollout occurs without explicit approval.
