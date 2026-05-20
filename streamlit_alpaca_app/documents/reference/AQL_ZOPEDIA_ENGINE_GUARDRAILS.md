# AQL / Zopedia Engine Guardrails

Read this before changing Zopedia, AQL, Attention summaries, SAA research, live research, evidence packs, or any feature that calls an LLM for product analysis.

## Non-Negotiable Architecture

All research-grade LLM work enters through one AQL/Zopedia engine contract.

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
4. Update or add tests that prove the affected surface receives the shared evidence pack and can access Zopedia memory/tools.
5. Do not call the work complete until the actual product path is verified.

## Source Of The Lesson

The prior Zopedia work accidentally made the chat/omnibar path full-power while Attention, live research, summaries, and critique kept partial legacy paths. That created inconsistent memory/tool access and repeated "not fully wired" failures. This file exists so the correction is visible before implementation starts.
