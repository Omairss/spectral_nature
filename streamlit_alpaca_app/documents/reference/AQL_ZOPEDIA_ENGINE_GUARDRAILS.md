# AQL / Zopedia Engine Guardrails

Read this before changing Zopedia, AQL, Attention summaries, SAA research, live research, evidence packs, or any feature that calls an LLM for product analysis.

Current roadmap: [v0.6 AQL/Zopedia Gateway Roadmap](../plans/V0_6_AQL_ZOPEDIA_GATEWAY_ROADMAP_2026-07-01.md).

## Non-Negotiable Architecture

All research-grade LLM work enters through one AQL/Zopedia engine contract.

As of the v0.6 gateway implementation, "enters through the engine" has a
concrete meaning:

- the call is made through the AQL/Zopedia gateway, not a raw model client;
- the call declares `surface`, `purpose`, and `call_type`;
- the gateway records provider, requested model, resolved/provider-reported
  model when available, status, timing, sanitized error, usage when available,
  and durable artifact links;
- formatter calls must link to an AQL evidence pack or explicit unavailable
  state;
- utility calls are still allowed only when labeled and observable.

Calling `load_aql_zopedia_llm_client(surface=...)` by itself is not enough. It
labels a client, but it does not enforce budgets, evidence ownership, request
telemetry, cost attribution, or direct-call bans. Product JSON calls that have
been migrated should use `generate_json_via_aql_zopedia_gateway(...)`.

Thin wrappers are allowed:

- Chat / Zopedia UI: streaming wrapper.
- Attention and summary jobs: scheduled/materialized wrappers.
- API routes: transport wrappers.
- Admin: inspection and operations wrapper.

Separate orchestration endpoints are not allowed for:

- live research,
- homepage summary,
- page agentic summary,
- critique/judge,
- evidence-pack generation,
- memory/wiki reads,
- memory/wiki writes,
- tool-calling loops.

Those are engine capabilities, not feature-owned workflows.

## Required Engine Capabilities

Every engine run should have one result contract with:

- normalized request metadata: surface, task, query, context, budget, write policy;
- planner/tool trace;
- retained evidence access;
- live evidence access;
- Zopedia memory/wiki search and page read access;
- source/evidence tracing;
- answer or summary payload;
- evidence pack;
- critique/judge result;
- confidence and explicit gaps;
- optional safe memory updates or proposals through typed mutation APIs.

Every model call inside or below the engine should also have one gateway event
contract with:

- surface and purpose;
- call type: `research_grade`, `formatter_over_aql`, `utility`,
  `schema_repair`, or `admin_probe`;
- provider and model metadata;
- budget and retry metadata;
- status and sanitized failure metadata;
- usage/cost metadata when available;
- durable links to run ids, dataset versions, evidence packs, pages, candidates,
  or proposals.

## Write Policy

Reads are default for research-grade surfaces.

Writes are controlled by `write_policy`:

- `none`: no memory mutation.
- `propose`: create proposal only.
- `safe_auto`: commit only source-backed, reversible typed mutations with audit and rollback.

Do not let render paths patch memory directly.

## Before Editing Checklist

1. Identify whether the change is a new engine capability or a wrapper around the engine.
2. If it gathers evidence, calls tools, uses memory, critiques an answer, or writes wiki state, it belongs in the engine.
3. If an existing feature path bypasses the engine, migrate the entrypoint instead of adding another helper.
4. If an existing feature path calls `llm_client.generate_json(...)` directly, classify it as gateway internals, provider adapter internals, tests/fakes, or a temporary migration allowlist entry with owner and expiry.
5. Update or add tests that prove the affected surface receives the shared evidence pack and records gateway telemetry.
6. Do not call the work complete until the actual product path is verified and the model-call ledger shows the expected surface/purpose/model rows.

## Source Of The Lesson

The prior Zopedia work accidentally made the chat/omnibar path full-power while Attention, live research, summaries, and critique kept partial legacy paths. That created inconsistent memory/tool access and repeated "not fully wired" failures. This file exists so the correction is visible before implementation starts.
