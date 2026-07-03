# v0.6 AQL/Zopedia Gateway Roadmap

Date: 2026-07-01

## Purpose

v0.6 exists to deepfix the repeated AQL/Zopedia centralization failure.

Earlier work routed many surfaces through `load_aql_zopedia_llm_client(surface=...)`.
That was useful as a transition step, but it was not real governance. A tagged
LLM loader does not enforce research contracts, model routing, budgets, request
telemetry, cost attribution, evidence-pack ownership, or direct-call bans.

Zopedia/AQL is already the knowledge caching layer. v0.6 must not invent a
second cache. The work is to use the existing Zopedia memory, retained evidence,
pages, proposals, source refs, and freshness contracts as the universal
knowledge cache for every product surface, then make bypasses visible and
eventually impossible.

The v0.6 standard is stricter:

```text
UI / job / API / tool
  -> AQL/Zopedia gateway request
  -> existing Zopedia knowledge cache: memory, retained evidence, pages, proposals
  -> budget and provider policy
  -> evidence acquisition or formatter contract
  -> model adapter
  -> telemetry and artifact linkage
  -> typed output, gaps, and optional memory proposal
```

No research-grade product path is considered centralized until it enters this
gateway and leaves an observable request record.

No feature-owned prompt cache, sidecar research cache, page-summary cache, or
Trading Agent context cache should become the product source of truth. Those
surfaces can materialize display artifacts, but reusable knowledge belongs in
Zopedia/AQL with freshness, provenance, and memory-write policy attached.

## Implementation Status

Updated 2026-07-01:

- Implemented `services/aql_zopedia_gateway.py` as the first enforceable model
  boundary for JSON calls. It requires `surface`, `purpose`, and `call_type`,
  records provider/model/status/timing metadata, hashes prompts/schemas instead
  of storing prompt text, and strips provider reasoning internals before
  returning payloads.
- Added the `aql_zopedia_model_call_events` Postgres ledger and
  `model_call_rollup(...)` in `services/pipeline_store.py`.
- Migrated AQL/Zopedia structured direct synthesis, structured repair, and
  `analysis.run_python` argument repair through the gateway.
- Migrated Trading Agent final candidate synthesis and research reviews through
  the gateway.
- Migrated AQL collector planning, search relevance, and search-router JSON
  calls through the gateway. AQL search acquisition now uses Serper as the
  primary provider through the shared loader, records the actual provider name
  in search artifacts, and keeps Tavily/SerpAPI parked unless a future provider
  policy deliberately re-enables them.
- Moved active Zopedia ownership out of Omnibar-named modules:
  `zopedia_agent.py`, `zopedia_research.py`, and `zopedia_resolver.py` own the
  implementation. Old `omnibar*` modules, `/v1/omnibar/*` routes, and
  `omnibar:resolve` scope are retired instead of kept as compatibility aliases.
- Added source-boundary tests so `services/trading_agent.py` and
  `services/aql_zopedia_engine.py` cannot reintroduce direct
  `llm_client.generate_json(...)` calls.
- Live provider smoke passed against DeepSeek `deepseek-reasoner` after the
  account was funded.
- Live Serper backfill probe passed through the Attention helper for NVDA, AMD,
  and AVGO, returning article rows for all three symbols.

Remaining migration work:

- Reuse the existing Zopedia/AQL knowledge cache consistently instead of
  feature-local caches for research context, source snippets, page summaries,
  Trading Agent context, or Attention explanations.
- Tool-using AQL/Zopedia planner/model steps inside the general agent loop.
- Remaining Attention/Home group synthesis, enrichment, and public review call
  sites beyond the collector/acquisition path.
- Zopedia chat, memory proposal generation, maintenance learning, KG expansion,
  company memory, and news-business resolution.
- Admin/System Health UI rollup for the new model-call ledger.
- Provider-reported model/usage/cost fields when provider adapters expose them.

## Why This Is v0.6

v0.5 should not absorb this as another cleanup item. The current failure is
architectural, not a small refactor:

- Trading Agent can use AQL for ticker research, then still call
  `llm_client.generate_json(...)` directly for final synthesis and reviews.
- Attention/Home uses AQL/Zopedia in several places, but direct formatter and
  reviewer calls still bypass one enforceable gateway.
- Zopedia memory, live research, KG expansion, company memory, and page
  summaries have mixed call patterns.
- DeepSeek account usage cannot be reconciled cleanly to product surfaces
  because requests do not persist `surface`, `purpose`, provider, model, status,
  timing, and usage metadata in one place.
- Existing guardrail tests catch some loader bypasses, but they allow reviewed
  direct `generate_json` call sites instead of forcing one product gateway.

v0.6 makes the gateway the release gate before more Trading Agent V2, attention
tuning, or Zopedia expansion work.

## Non-Goals

- Do not switch providers as the main fix. Provider choice is policy inside the
  gateway, not the architecture.
- Do not add per-feature wrappers that only rename `generate_json`.
- Do not hide weak research behind deterministic fallbacks.
- Do not count fixed tests as proof of LLM quality. Tests protect plumbing and
  call boundaries; live qualitative review still decides whether output is good.
- Do not log prompts, secrets, user private content, or full evidence blobs in
  telemetry.

## Target Gateway Contract

Add one product-facing request boundary, owned by AQL/Zopedia.

Suggested module shape:

```text
services/aql_zopedia_gateway.py
  AQLZopediaRequest
  AQLZopediaResponse
  run_aql_zopedia_model_call(...)
  run_aql_zopedia_research_call(...)
  run_aql_zopedia_formatter_call(...)
```

The exact names can change, but the contract must support:

- `surface`: product surface, for example `attention.home_build`,
  `trading_agent.synthesis`, `trading_agent.review`, `zopedia.chat`,
  `entity_taxonomy`, or `knowledge_graph.expansion`.
- `purpose`: concrete use, for example `research`, `formatter`, `review`,
  `classifier`, `extractor`, `schema_repair`, `memory_proposal`.
- `call_type`: one of `research_grade`, `formatter_over_aql`, `utility`,
  `schema_repair`, or `admin_probe`.
- `model_policy`: default policy plus optional allowed provider/model class.
- `budget_policy`: timeout, max retries, max tool calls, max parallelism, and
  expected usage envelope.
- `artifact_links`: run id, dataset version id, evidence pack id, page id,
  candidate id, or other durable anchors.
- `write_policy`: `none`, `propose`, or `safe_auto` for memory-affecting work.
- `quality_policy`: critic/revision/fail-closed behavior when applicable.

Every response must include:

- normalized status,
- provider,
- model as requested,
- provider-reported model if available,
- latency,
- retry count,
- error class and sanitized error text,
- usage if the provider returns it,
- evidence pack or formatter input linkage,
- output payload or explicit unavailable state.

## Telemetry Contract

Persist a small request ledger, not prompts.

Suggested dataset/table: `aql_zopedia_model_call_events`.

Required fields:

- `model_call_id`
- `created_at_utc`
- `surface`
- `purpose`
- `call_type`
- `provider`
- `requested_model`
- `resolved_model`
- `provider_reported_model`
- `status`
- `error_class`
- `sanitized_error`
- `latency_ms`
- `timeout_seconds`
- `retry_count`
- `usage_prompt_tokens`
- `usage_completion_tokens`
- `usage_total_tokens`
- `cost_units` or `estimated_cost_usd` when available
- `run_id`
- `dataset_name`
- `dataset_version_id`
- `aql_evidence_pack_id`
- `artifact_key`
- `prompt_hash`
- `schema_name`

Rules:

- Do not persist raw prompts or raw user text.
- Use hashes and durable artifact IDs for reconciliation.
- If a provider maps `deepseek-reasoner` to a billing label such as V4 Flash,
  store both requested and provider-reported names when the response exposes it.
- If provider usage is unavailable, record `usage_status=not_reported`.

## Model Policy

The gateway owns model selection.

Initial policy:

- Production scheduled research defaults to the configured provider/model, but
  the gateway records every call.
- Utility extraction/classification may use a cheaper or faster model only when
  the gateway policy explicitly allows it.
- Expensive reasoning models are reserved for research-grade synthesis,
  critique, and complex planning.
- A provider `402`, quota, or auth failure should be classified once at the
  gateway and surfaced to Admin/System Health.
- Separate keys should be supported by surface or environment:
  `prod-pipeline`, `dev-ui`, `local-notebooks`, and external/manual probes.

## Direct-Call Audit

Replace the old "reviewed direct call" standard with an enforceable audit.

Source scan should classify every `.generate_json(` call as one of:

- gateway internals,
- provider adapter internals,
- tests/fakes,
- explicitly temporary migration allowlist.

All product feature modules should call the gateway, not the raw LLM client.

Temporary allowlist entries must have:

- owner,
- reason,
- migration target,
- expiry milestone,
- status.

## Migration Order

### P0: Gateway Design Lock

Deliverables:

- Define the request/response dataclasses or typed dictionaries.
- Define telemetry schema and persistence path.
- Define model policy configuration shape.
- Define source-scan allowlist format.
- Update philosophy, guardrails, and roadmap docs so tagged loaders are no
  longer considered centralized.

Exit criteria:

- A reviewer can decide whether any call is research-grade, formatter-over-AQL,
  or utility.
- The gateway contract can be implemented without changing product behavior.

### P1: Gateway Implementation

Deliverables:

- Implement the gateway around the existing provider adapters.
- Persist request events in Postgres and/or materialized pipeline datasets.
- Add Admin/System Health rollup for provider status, latest failures, and
  surface/model usage.
- Add request IDs to logs for pipeline jobs.

Exit criteria:

- Running any model call from a migrated surface writes a telemetry row.
- DeepSeek balance/quota failures are visible by surface without reading raw job
  logs.

### P2: Trading Agent Migration

Deliverables:

- Move per-ticker AQL calls, final synthesis, and research reviews through the
  gateway.
- Make final synthesis explicitly `formatter_over_aql` and require evidence-pack
  linkage.
- Remove direct final synthesis/review `generate_json` calls from
  `services/trading_agent.py`.
- Record model-call IDs on Trading Agent run, candidate, outcome, and review
  artifacts where relevant.

Exit criteria:

- Trading Agent spend can be reconciled by horizon and purpose.
- No Trading Agent product code owns a raw LLM client.

### P3: Attention/Home Migration

Deliverables:

- Move Home market summary, group synthesis, public-surface review, ticker
  enrichment, and page-summary formatter calls through the gateway.
- Preserve the coverage contract and fail-closed behavior.
- Record model-call IDs in Home coverage and enrichment metadata.

Exit criteria:

- A Home artifact can report which model calls created summary, group context,
  review, and enrichment fields.

### P4: Zopedia And Memory Migration

Deliverables:

- Move Zopedia chat synthesis, memory proposal generation, maintenance learning,
  KG expansion, company memory, and news-business resolution through the
  gateway.
- Preserve typed memory mutation policy.
- Record gateway calls in memory commit/proposal reports.

Exit criteria:

- Zopedia can explain which calls read memory, wrote proposals, committed pages,
  or failed due provider/budget issues.

### P5: Remove Temporary Allowlist

Deliverables:

- Delete or migrate remaining direct product call sites.
- Keep provider adapter calls only inside `services/llm.py` and gateway internals.
- Tighten tests so new product direct calls fail.

Exit criteria:

- Source scan proves no research-grade or formatter-over-AQL product path can
  bypass the gateway.

## Acceptance Criteria

v0.6 is complete only when all of these are true:

- Every research-grade product model call has a gateway telemetry row.
- Every formatter-over-AQL call links to an evidence pack or explicit AQL
  unavailable state.
- Utility calls are labeled and visible, not hidden behind feature modules.
- Trading Agent, Attention/Home, Zopedia chat, company memory, and KG proposals
  no longer call raw LLM clients from product code.
- Admin/System Health can answer: which surface called which provider/model,
  how often, whether it failed, and why.
- The system can distinguish requested model from provider billing/model label
  when providers expose that information.
- Existing materialized product behavior is preserved or deliberately changed
  with a documented contract update.

## Relationship To v0.5

v0.5 remains about current product reliability, Home coverage, retrieval/tool
discovery, and company-memory runtime proof.

v0.6 becomes the required gate before:

- Trading Agent Candidate Package V2 replacement,
- more attention tuning beyond notebook/replay work,
- broad Zopedia memory expansion,
- new user-facing research surfaces,
- provider/model optimization.

If a v0.5 task requires new research-grade LLM behavior, it should either use
the existing engine conservatively or be moved behind the v0.6 gateway work.
