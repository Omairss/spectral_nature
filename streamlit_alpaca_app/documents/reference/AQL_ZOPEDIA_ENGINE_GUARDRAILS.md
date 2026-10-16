# AQL / Zopedia Engine Guardrails

Read this before changing Zopedia, AQL, Attention summaries, SAA research, live research, evidence packs, or any feature that calls an LLM for product analysis.

Current implementation: [October 14 engine/conversation repair](../plans/ZOPEDIA_CONVERSATION_REPAIR_2026-10-14.md). The [v0.6 gateway roadmap](../plans/V0_6_AQL_ZOPEDIA_GATEWAY_ROADMAP_2026-07-01.md) describes the earlier transport consolidation.

## Non-Negotiable Architecture

All research-grade LLM work enters through one AQL/Zopedia engine contract.

The engine owns planning, acquisition, synthesis, review, budgets, continuation
and public progress. A common inference gateway alone does not satisfy this
contract. Every product model operation follows these boundaries:

- features enter `run_aql_zopedia_agent` or `run_aql_zopedia_structured_agent`;
- structured tasks without acquisition use `max_tool_calls=0`;
- only the shared engine and its agent implementation call the gateway;
- only the gateway invokes the provider JSON client;
- the call declares `surface`, `purpose`, and `call_type`;
- the gateway records provider, requested model, resolved/provider-reported
  model when available, status, timing, sanitized error, usage when available,
  and durable artifact links;
- formatter calls must link to an AQL evidence pack or explicit unavailable
  state;
- utility calls are still allowed only when labeled and observable.

Calling `load_aql_zopedia_llm_client(surface=...)` by itself is not enough. It
labels a client, but it does not enforce budgets, evidence ownership, request
telemetry, cost attribution, or direct-call bans. Feature code must not call
`generate_json_via_aql_zopedia_gateway(...)` directly. Domain schemas and storage
adapters remain separate modules; independent investigation lifecycles do not.

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

## Research Objective

AQL/Zopedia is a statistical, high-recall discovery and relevance system. Preserve
useful noisy leads, indirect business relationships and reasonable hypotheses.
LLMs rank relevance, explain connections and direct follow-up. Review is editorial
refinement, not a certification or publication gate. A valid answer survives an
incomplete review, with the real review state retained in metadata. Exact ticker
matches, source counts, complete provenance and proven causality are not universal
admission requirements. Respect a user's explicit evidence scope. Code protects
execution budgets, schemas and permissions; durable memory writes follow their
separate write policy.

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
4. Migrate every feature-local raw-client or gateway call into the shared engine. Do not add blanket module/prefix exceptions to the architecture boundary.
5. Verify identity, authorization, complete evidence, source projection and gateway accounting mechanically; personally read live outputs to evaluate research quality.
6. Do not call the work complete until the actual product path is verified and the model-call ledger shows the expected surface/purpose/model rows.

## Source Of The Lesson

The prior Zopedia work accidentally made the chat/omnibar path full-power while Attention, live research, summaries, and critique kept partial legacy paths. That created inconsistent memory/tool access and repeated "not fully wired" failures. This file exists so the correction is visible before implementation starts.


## Conversation and release ownership

One conversation contains ordered messages and linked execution attempts. A new
message preserves earlier answers, evidence, corrections and exact failed-attempt
outcomes; it gets a fresh investigation budget. Retrying an interrupted execution
resumes that execution's checkpoint. Do not restart a conversation after failure.
Public updates contain concise activity, findings and next actions; private model
reasoning and raw diagnostic events stay outside the reading path.

Release the identical shared engine source revision to API, queued research and
background AI consumers. Check the actual executor's immutable image and returned
engine revision. Different process images may retain domain dependencies and
platform commands, but their engine implementation must be identical. The
October 14 development release receipt records all consumers and rollback images.
