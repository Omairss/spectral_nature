# Zopedia Parity Completion Plan

Date: 2026-05-17  
Last updated: 2026-05-19

## Status

**No-go for full Zopedia parity.**

Completed:

- Phase 0 product-shell remediation is implemented and deployed to dev.
- AQL/Zopedia engine spine is now the shared product boundary for chat/agent runs, Attention homepage summaries, page summaries, Trading Agent AQL runs, learning replay, LLM runtime loading, evidence-pack attachment, and ElevenLabs summary audio attachment.

Immediate blocker:

- **Live product-path proof.** The source-level tool contracts, benchmark replay, learning ledger/service, generated evals, safe tool-affordance updates, dev deploy, Azure `zopedia-learning` execution, and replay verification are now verified. The remaining blocker is checking the live dev UI path with the same benchmark query.

Full parity still depends on source traceability, answer-quality evals, automatic memory quality, maintenance durability, EDA quality, and product/browser-path proof.

## 2026-05-19 AQL/Zopedia Engine Spine Fix

Problem fixed:

- Zopedia chat had the full tool/memory/evidence loop, while Attention summaries, live research helpers, page summaries, critique, and TTS/audio paths entered through separate partial workflows.
- That made feature behavior inconsistent and made "is Zopedia wired here?" impossible to answer cleanly.

Source-level change:

- Added `services/aql_zopedia_engine.py` as the product-facing engine boundary.
- Added admin-visible config group **AQL / Zopedia Engine** for shared agent and summary evidence budgets.
- Routed Zopedia chat through `run_aql_zopedia_agent(...)`.
- Routed page-summary AQL calls through the engine instead of calling `run_aql_zopedia_agent` directly.
- Routed Trading Agent AQL calls and Zopedia learning replay through the engine.
- Routed `attention_home_build` homepage summaries through `build_aql_zopedia_attention_home_summary_with_trace(...)`.
- Routed the public `services.attention_home_summary` facade through the same engine wrapper, so future callers do not accidentally bypass it.
- Routed the public `services.aql` summary facade through the same engine wrapper, so `from services.aql import build_attention_agentic_summary...` cannot bypass the engine.
- Routed product LLM loading through `load_aql_zopedia_llm_client(...)`; `services.zopedia_runtime` remains the provider adapter, not the feature entrypoint.
- Routed homepage ElevenLabs audio attachment through `attach_aql_zopedia_summary_audio(...)` so generated summaries and spoken summaries share the engine contract.
- Added guardrail tests that fail if code calls the omnibar agent loop outside the engine, loads the Zopedia LLM outside the engine/runtime adapter, or routes Attention homepage summaries around the engine.
- Removed the old deterministic page-summary fallback narrative path. Page summaries now either produce an engine-grounded summary or return an explicit unavailable/data-gap contract.
- Removed stale direct LLM loader entrypoints from the attention/trading/taxonomy jobs and replaced them with the engine loader.

Important limitation:

- This is the engine-spine correction, not a final parity claim. Some helpers still do narrow LLM formatting/classification over supplied data, but provider access and research-grade orchestration now have a shared boundary. The next proof step is answer-level/browser-path evaluation over chat, Attention homepage, page summaries, and Trading Agent outputs.

Wiring proof added:

- `test_attention_home_facade_calls_engine_runtime`
- `test_aql_package_facade_calls_engine_runtime`
- `test_page_and_trading_default_runners_call_engine`
- `test_product_llm_loading_enters_aql_zopedia_engine_boundary`
- `test_omnibar_agent_loop_is_only_called_by_shared_engine`
- `test_attention_home_job_uses_engine_summary_boundary`

## Product Standard

Build native Spectral Nature Zopedia only if it becomes a genuinely useful memory and research system:

```text
source material
  -> durable memory
  -> maintenance / compaction / link repair
  -> grounded answers
  -> automatic safe memory updates
  -> better future answers
```

If the loop does not feel excellent, grounded, self-correcting, and lower-complexity than the value it adds, do not ship it.

## Current Foundation

Implemented and worth keeping:

- SAA-backed Zopedia pages.
- Source ingest for pasted text, URL text, selectable-text PDFs, readable text/markdown/CSV/JSON uploads, and YouTube transcripts when retrieval is available.
- Source metadata, generated page provenance, source refs, source read-through, page search, page read, source tracing, neighborhood read, and evidence-pack classification.
- Typed mutation controller for safe audited page upserts, page links, metadata patches, rollback, and risky-change escalation.
- Post-answer memory maintainer that can apply safe typed mutations or escalate proposals.
- Scheduled `zopedia-maintenance` job that writes backlinks, compact community/godnode rows, issue rows, reports, and maintenance mutation audit rows.
- DeepSeek global LLM runtime through the shared Zopedia/AQL boundary.
- Reasoning trace capture when the provider returns it.
- Bounded answer judge after draft synthesis.
- Durable Zopedia chat thread/message tables beside the agent run log.
- `analysis.run_python` tool with bounded pandas/numpy/scipy/scikit-learn execution over approved datasets or small inline/uploaded tables.
- Public source citation filtering so synthetic/internal refs such as `eval.local` are not rendered as web citations.
- Phase 0.5 source contracts for empty data/tool failures, market-impact evidence recovery, generated learning evals, and safe tool-affordance memory updates.

Latest verified dev deployment:

- UI revision: `sn-streamlit-ui-dev--0000334`
- UI image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:fc0da73a64d951176913485fd633f11a628ac124214c864cf6fc47a9b50cc972`
- UI smoke check: HTTP 200
- Runtime env verified: `APP_TRACK=development`, `STREAMLIT_MOBILE_UI_ENABLED=true`, `STREAMLIT_LAYOUT_MODE_DEFAULT=desktop`
- Pipeline image: `snpipelineacr03130136.azurecr.io/pipeline-jobs@sha256:3d12cf37389cf5106c073cb8c3ec8b27ab1effe9d9a2cc9759f07e0e2a277a42`
- Pipeline deploy check: `zopedia-learning` created in Azure Container Apps and manually executed after replay verification was added.
- `zopedia-learning` runtime env verified: `LLM_PROVIDER=deepseek`, `LLM_MODEL=deepseek-reasoner`, `LLM_BASE_URL=https://api.deepseek.com`, `LLM_API_KEY_SECRET_NAME=deepseek-api-key`.
- `zopedia-learning` execution: `zopedia-learning-4onryyu`, status `Succeeded`, log counts `threads=7`, `events=2`, `evals=2`, `updates=2`, `verified=2`, `regressed=0`.
- API revision: `sn-api-dev--0000030`
- API image: `snpipelineacr03130136.azurecr.io/api@sha256:324ebfd977faafd5a4d913dd0bdced513263b0a9c592de5dec270b744341aebe`
- API smoke check: `/health` passed.
- No production deployment was performed.

## Latest Failure To Fix

Source thread:

- `zthread_61ef63346b654add`
- Dry-run proposal: `ZOPEDIA_ANTI_FRAGILE_DRY_RUN_BOND_YIELDS_2026-05-19.md`

Failure:

- User asked how bond yields were affecting markets.
- Agent found the yield move, then repeatedly concluded it could not get stock/ETF returns for May 18.
- After user pushback, agent proved `dataset.price_history` had SPY data for May 18 and May 19.
- Later, for sector/statistical analysis, agent treated missing precomputed relationship data as a dead end.
- `analysis.run_python` attempts failed because generated code was flattened into one-line invalid Python and/or lacked explicit input datasets. Fixed on 2026-05-20 by normalizing stringified dataset refs, preserving distinct aliases for repeated dataset loads, and adding one bounded LLM repair pass at the shared analysis tool boundary.
- Agent framed tool/code/input failures as missing market data.
- Agent exposed implementation vocabulary such as tool-call IDs in final answer text.

Root cause:

- The agent had enough capability to answer. It lacked evidence-slot planning, fallback across tool levels, precise failure diagnosis, and a feedback loop that turns user rescue into durable behavior improvement.

## Non-Negotiables

- No hardcoded market/domain routers.
- No hardcoded user-facing narrative.
- No orphan research-grade LLM calls outside AQL/Zopedia.
- No final claims from wiki memory unless the page/source evidence is available or the answer labels it as memory.
- No unlogged memory or KG mutation.
- No unscoped memory. Every durable object needs user, portfolio, or workspace scope.
- No unbounded agent/tool/model loops.
- No raw thread IDs, mutation IDs, eval/debug refs, run IDs, provider names, tool names, or tool-call IDs on the normal product surface.
- No default product panels titled `Admin`, `Mutations`, `Proposals`, `Health`, or `Debug`.
- No prod deploy without explicit permission.
- Do not call Zopedia complete until answer-level evals and browser-path UX checks pass.

## Execution Spine

This is the canonical implementation order.

| Phase | Status | Purpose | Ship Gate |
|---|---|---|---|
| Phase 0: Product Shell Remediation | Implemented in dev; human product pass pending | Make Zopedia feel like a research conversation, not a control panel | Human product pass on dev |
| Phase 0.5: Anti-Fragile Learning Loop | Implemented, deployed to dev, replay-verified | Convert user rescue into durable evals and behavior improvements | Live dev UI benchmark inspection |
| Phase 1: Evidence Backbone | Pending | Make every answer trace to source/chunk/document | Answer -> page -> source trace works |
| Phase 2: Agent Quality Loop | Foundation exists, proof pending | Make Zopedia verify/revise before confidence | Hard-question evals pass |
| Phase 3: Automatic Memory And Maintenance | Foundation exists, proof pending | Let Zopedia update and repair memory safely | Safe writes/rollback/maintenance evals pass |
| Phase 4: EDA And Analysis Quality | Runner exists, proof pending | Let agent perform bounded analysis safely | Analysis evals pass |
| Phase 5: Product Evals And Go/No-Go | Pending | Prove parity and decide ship/no-ship | Full suite passes |

## High-Bar Review Amendments

Added after review on 2026-05-19.

The plan is acceptable only if every phase satisfies the same proof standard:

- **Source fix:** fix the lowest shared layer that caused the failure. Do not hide bad contracts behind prompts.
- **Behavior proof:** replay the original failure and at least one adjacent scenario.
- **Product proof:** inspect the real rendered dev path, including desktop and mobile, not only unit tests.
- **Trace proof:** saved chat history, live trace, source citations, analysis artifacts, and memory outcomes survive reruns.
- **Scope proof:** every durable object is user/workspace scoped.
- **Regression proof:** add an eval that fails on the old behavior and passes on the new behavior.
- **Operational proof:** deploy to dev when runtime behavior changes, verify runtime env/job status, and record the exact revision.
- **No-hardcoding proof:** scan for hardcoded narrative, hardcoded market routers, orphan LLM calls, and debug refs leaking into product UI.

### Product UX Contract

Zopedia should feel like a research conversation with inspectable evidence, not a control panel.

Required product behavior:

- First viewport centers on active conversation and composer.
- Chat history is a quiet rail/list, not the main content unless explicitly opened.
- Source attach lives with the composer.
- Admin, maintenance, mutation, proposal, and health details are contextual drawers or admin views, not default panels.
- Thinking/tool trace keeps its open/closed state across streaming updates, saved-message rerenders, and page reruns.
- Assistant answers are structured with headings, paragraphs, tables when useful, and citations; no inert wall of Markdown.
- Answer passages are selectable/drillable so a user can ask a follow-up about the exact claim that matters.
- Source cards are only shown when tied to the current answer or selected passage; unrelated search cards must not sit under a completed answer.
- Synthetic/internal refs such as eval URLs never render as public evidence.
- Mobile first viewport shows answer/conversation before secondary evidence or graph surfaces.

### Learning Promotion Rules

Automatic self-improvement is required, but it must be versioned and reversible.

Safe automatic updates:

- Source-backed Zopedia page creation or timestamped fact updates.
- Tool-affordance memory that describes observed capabilities and failure modes.
- Regression eval generation.
- Low-risk planner-contract metadata that narrows forbidden claims or requires evidence slots.

Escalate instead of auto-applying:

- Deletes, merges, broad rewrites, source removals, cross-workspace updates, destructive KG changes, and broad planner behavior changes from a single episode.
- Any change with weak evidence, no rollback path, or conflicting judge output.

Promotion gates:

- Every learned update has a version, evidence refs, before/after state, rollback handle, and replay result.
- A verified update must pass the originating eval and a small adjacent eval set before it is considered durable.
- If the failure recurs, mark the event `regressed`, preserve the prior attempted fix, and generate a stronger proposal rather than stacking hidden prompt patches.

### Golden Scenario Eval Set

Phase 5 cannot be only generic suites. It needs named product scenarios:

- **Bond yields market impact:** yield facts, representative instruments, event-window returns, optional regression, causality caveat.
- **Company research:** fundamentals, recent news, retained company memory, source citations, contradiction handling.
- **Macroeconomics:** local Spectral Nature data first, external query only for missing/current facts, clear data vintage.
- **False premise:** challenge an incorrect user premise without being dismissive, then answer the corrected question.
- **Custom source upload:** file/text source becomes searchable memory, answer cites retained source chunks, and safe memory update is audited.
- **Self-improvement:** a rescued chat creates a learning event, eval, safe update/proposal, replay verification, and regression guard.
- **EDA/scikit analysis:** agent collects data through approved tools, runs bounded analysis, cites artifacts, and explains limits.
- **Maintenance:** stale, duplicate, orphan, weak-source, contradictory, and broken-link pages are detected and either safely fixed or escalated.
- **Mobile UX:** conversation/composer first, no overflow, trace stable, source drilldown usable.

## Phase 0: Product Shell Remediation

Status: **implemented, browser-checked locally, deployed to dev.**

What changed:

- Removed old top Zopedia button wall and deprecated workspace-control render path.
- Added desktop shell with left thread rail, center conversation, and collapsed right context drawer.
- Added mobile shell where conversation and composer appear before secondary panels.
- Replaced history `Open` buttons with row-wide thread actions.
- Hid raw thread IDs from normal UI.
- Moved source attach into the conversation/composer area with `st.chat_input` file upload and compact URL/text attach.
- Kept admin/debug/memory panels collapsed behind admin/context drawers.
- Attached trace, confidence, limitations, and follow-up affordances under the assistant turn.
- Quieted chat avatars and reduced follow-up action weight.

Validation:

```text
streamlit_alpaca_app/.venv/bin/python -m py_compile streamlit_alpaca_app/app.py

PYTHONPATH=streamlit_alpaca_app streamlit_alpaca_app/.venv/bin/pytest -q \
  streamlit_alpaca_app/tests/test_chat_log.py \
  streamlit_alpaca_app/tests/test_omnibar_agent.py \
  streamlit_alpaca_app/tests/test_zopedia_memory.py \
  streamlit_alpaca_app/tests/test_zopedia_native_llm_boundary.py

46 passed

NODE_PATH=/tmp/spectral-pw/node_modules \
  node scripts/mobile_render_check.mjs \
  --url http://127.0.0.1:8512 \
  --out /tmp/spectral-mobile-check-zopedia-shell-final-20260519 \
  --mode both \
  --sections "Home,Zopedia,Market Explorer,Stock Investigator,Broad Economy,Portfolio"

Render check passed.
```

Manual screenshots inspected:

- `/tmp/spectral-zopedia-shell-final-20260519/desktop-zopedia-empty.png`
- `/tmp/spectral-zopedia-shell-final-20260519/desktop-zopedia-existing.png`
- `/tmp/spectral-mobile-check-zopedia-shell-final-20260519/mobile-zopedia.png`

Remaining Phase 0 gate:

- Human product pass on live dev shell.
- Confirm the thinking trace stays open when the user opens it, including during streaming updates and saved-message rerenders.
- Confirm assistant text is not a dense blob and can support passage-level drilldown.
- Confirm history, source attach, and admin/debug surfaces do not dominate the first viewport.
- If the live shell still feels cluttered, fix UI before adding backend features.

## Phase 0.5: Anti-Fragile Learning Loop

Status: **implemented, deployed to dev, and replay-verified; live UI benchmark inspection still pending.**

Implemented on 2026-05-19:

- `dataset.daily_movers` retries on-demand when explicit symbol filters are lost by a materialized snapshot, including partial ETF/common-stock misses.
- `dataset.event_significance` returns insufficient-observation diagnostics instead of bare empty rows.
- `macro_relationship_checks_1d` is labeled as a precomputed relationship artifact and points to primitive-data fallback.
- Shared query results carry user-safe empty-result messages into the planner.
- `analysis.run_python` returns precise failure categories and the planner gets one repair pass before final-answering after code/input/runtime failures.
- Market-impact answers cannot final-answer without observed market data; the agent recovers through `research.market_impact_map` and `dataset.daily_movers`.
- `services/zopedia_learning.py` adds the learning ledger service, thread detector, critic, generated eval writer, safe tool-affordance memory update path, and replay helper.
- Pipeline job wiring adds `zopedia-learning`; deploy script now creates that scheduled job with LLM env propagation.
- Dev DB learning dry run scanned 5 threads, detected 2 events, generated 2 evals, and applied 2 safe tool-affordance memory updates.
- Dev Azure `zopedia-learning` execution `zopedia-learning-4onryyu` succeeded after deployment and reported `threads=7`, `events=2`, `evals=2`, `updates=2`, `verified=2`, `regressed=0`.

Benchmark replay result:

- Query: `What is the impact of the bond market today?`
- Tool path: `research.prefetched_context`, `dataset.yield_curve_facts_1d`, `research.market_impact_map`, `dataset.daily_movers`.
- Final answer used 2Y/10Y/30Y yield facts plus observed TLT, SPY, IWM, XLF, and HYG moves.
- Required evidence slots hit: broad equity (`SPY`), growth/small-cap (`IWM`), financial/credit (`XLF`, `HYG`), and rates (`2Y`, `10Y`).
- Forbidden unavailable-data claims: none.

Goal:

- Every agent struggle should make future agent runs harder to break.

Closed loop:

```text
chat thread / tool trace / final answer
  -> friction detector
  -> episode critic
  -> root-cause classification
  -> improvement proposal
  -> regression eval case
  -> safe contract/memory/tool update
  -> replay verification
```

This is not prompt hardcoding. It is a learning system around runtime evidence, tool schemas, source traces, and eval gates.

### Source-Level Tool Contract Fixes

The latest bond-yield failure exposed bad tool contracts, not only bad agent behavior. Phase 0.5 must fix these at the source so the agent is not learning around ambiguous tools.

Required fixes:

- `dataset.daily_movers`: when explicit symbols are requested and the materialized snapshot returns rows before filtering but zero rows after filtering, retry through the on-demand path unless the caller requested a materialized-only result. The result must include structured metadata such as `empty_reason`, `materialized_rows_seen`, `filtered_symbols_missing`, `fallback_attempted`, and `next_tool_hint`.
- `dataset.event_significance`: if the event window has too few pre/post observations, return a structured insufficient-observations status with required and available observation counts. Do not return a bare empty table that can be mistaken for missing price data.
- `macro_relationship_checks_1d`: label this as a precomputed relationship artifact, not a general relationship engine. An empty result must include `empty_reason=no_precomputed_relationship_rows` and a fallback hint to primitive yield observations, price histories, and `analysis.run_python`.
- `analysis.run_python`: preserve multiline generated code, require explicit dataset refs, run syntax validation before execution, allow one internal repair loop for code-generation or input-contract failures, and return precise failure categories such as `analysis_code_error`, `analysis_input_missing`, or `analysis_runtime_error`. Status: source boundary fixed for malformed generated code, stringified dataset refs, duplicate dataset aliases, and one internal LLM repair pass; remaining proof is browser-path replay on live dev.
- Shared tool-result wrapper: supported research tools must return machine-readable empty-result metadata and a user-safe explanation. A bare `rows=[]` is not enough for agentic research.
- If the fixed contracts still leave too much orchestration burden on the agent, add first-class non-hardcoded tools for `dataset.event_window_returns` and `research.market_impact_basket`. These tools should derive baskets from available universe metadata or LLM-selected evidence slots, not hardcoded narrative routes.

Acceptance gates for these source fixes:

- ETF `daily_movers(... force_refresh=false)` no longer leads the agent to claim ETF or stock data is unavailable.
- `event_significance` insufficient-window cases report the exact observation gap and point to event-window returns or price history.
- Empty `macro_relationship_checks_1d` results trigger primitive-data fallback rather than a dead end.
- Rejected `analysis.run_python` calls are repaired once or surfaced with a precise failure category.
- The bond-yield regression eval fails if the final answer says data is unavailable after only checking summary/precomputed tools.

### Agent Capabilities Required

- **Evidence-slot planning:** before tool calls, the agent names the evidence slots needed for a good answer.
- **Tool-affordance reasoning:** distinguish summary datasets, primitive time-series datasets, precomputed relationship datasets, source memory, and analysis tools.
- **Fallback across tool levels:** if a filtered/summary dataset returns zero rows, try the primitive dataset or analysis path before saying unavailable.
- **Representative selection:** choose a small defensible basket from the economic channel without hardcoding the conclusion.
- **Computation competence:** calculate event-window returns and run small regressions from explicit input datasets.
- **Failure diagnosis:** distinguish data outage, wrong tool, input-contract failure, code-generation failure, tool timeout, and model synthesis failure.
- **Answer discipline:** separate observed relationship from causality, cap confidence when causal proof is absent, and hide implementation vocabulary.

### Evidence Plan Contract

Every research-grade answer should carry an internal `evidence_plan` object:

```json
{
  "question": "string",
  "answer_type": "company|macro|market_impact|source_trace|analysis|other",
  "required_slots": [
    {
      "slot": "macro_move",
      "why_needed": "string",
      "acceptable_sources": ["dataset.yield_curve_facts_1d", "dataset.yield_curve_observations"],
      "status": "missing|filled|failed",
      "evidence_refs": []
    }
  ],
  "fallbacks_attempted": [
    {
      "failed_path": "dataset.daily_movers",
      "fallback_path": "dataset.price_history",
      "outcome": "filled|failed"
    }
  ],
  "missing_slots": [],
  "cannot_claim": [],
  "confidence_cap": "low|medium|high",
  "confidence_cap_reason": "string"
}
```

Minimum evidence slots for the bond-yield market-impact class:

- Macro move and date.
- Representative instruments.
- Observed event-window returns.
- Optional statistical context if user asks to isolate effects.
- Causality caveat.

The slots are not a hardcoded router. They are an auditable internal plan that the judge can verify.

### Data Model

Add a new learning ledger:

```sql
CREATE TABLE IF NOT EXISTS saa_zopedia_learning_events (
    event_id TEXT PRIMARY KEY,
    user_key TEXT NOT NULL,
    thread_id TEXT,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    root_cause TEXT NOT NULL,
    original_question TEXT,
    failed_claim TEXT,
    correction_summary TEXT,
    successful_path_json JSONB,
    evidence_plan_json JSONB,
    proposed_change_type TEXT,
    proposal_id TEXT,
    mutation_id TEXT,
    eval_case_path TEXT,
    eval_status TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at_utc TIMESTAMPTZ NOT NULL,
    updated_at_utc TIMESTAMPTZ NOT NULL,
    metadata_json JSONB
);

CREATE TABLE IF NOT EXISTS saa_zopedia_learning_event_evidence (
    evidence_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    payload_json JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL
);
```

Allowed `status` values:

- `detected`
- `triaged`
- `proposal_created`
- `eval_generated`
- `safe_update_applied`
- `verified`
- `rejected`
- `regressed`

Allowed `trigger_type` values:

- `user_correction`
- `user_rescue`
- `answer_contradicted_tool_trace`
- `judge_revised_answer`
- `tool_path_later_succeeded`
- `repeated_low_confidence`
- `analysis_failure_misreported`
- `implementation_vocabulary_exposed`

Allowed `root_cause` values:

- `tool_mismatch`
- `premature_unavailable_claim`
- `missing_fallback`
- `input_contract_failure`
- `code_generation_failure`
- `model_synthesis_failure`
- `source_trace_gap`
- `confidence_overreach`
- `ui_or_answer_surface_leak`

### Service Boundary

Create `services/zopedia_learning.py`.

Required functions:

- `bootstrap_zopedia_learning_storage(conn, commit=True) -> None`
- `detect_learning_events_for_thread(thread_id, user_key, conn=None) -> list[dict]`
- `critique_learning_event(event, thread, run_payloads, llm_client) -> dict`
- `build_regression_eval_case(event, critique) -> dict`
- `persist_regression_eval_case(eval_case, *, base_dir) -> str`
- `build_tool_affordance_update(event, critique) -> dict`
- `apply_safe_learning_update(event, update, conn=None) -> dict`
- `run_zopedia_learning_job(limit=25, conn=None) -> dict`
- `replay_learning_eval(eval_case_path, conn=None) -> dict`

No UI or agent code should write learning rows directly. All writes go through this service.

### Episode Critic Output

The critic must return structured JSON:

```json
{
  "event_id": "string",
  "root_cause": "tool_mismatch|premature_unavailable_claim|missing_fallback|input_contract_failure|code_generation_failure|model_synthesis_failure|source_trace_gap|confidence_overreach|ui_or_answer_surface_leak",
  "failed_assumption": "string",
  "failed_claim": "string",
  "successful_path": [
    {"tool": "dataset.price_history", "arguments": {"ticker": "SPY", "days": 5}, "why_it_worked": "string"}
  ],
  "should_try_earlier": ["string"],
  "forbidden_future_claims": ["string"],
  "required_future_evidence": ["string"],
  "proposed_change_type": "eval_only|tool_affordance_memory|planner_contract|safe_memory_update|manual_review",
  "confidence": 0.0
}
```

### Learning State Machine

Transitions:

```text
detected
  -> triaged
  -> proposal_created
  -> eval_generated
  -> safe_update_applied
  -> verified
```

Rejection paths:

```text
detected|triaged|proposal_created|eval_generated
  -> rejected

verified
  -> regressed
```

Rules:

- `detected -> triaged`: critic produced valid structured output.
- `triaged -> proposal_created`: proposed change is non-empty and auditable.
- `proposal_created -> eval_generated`: regression case persisted.
- `eval_generated -> safe_update_applied`: only if update is low-risk, source-backed, and rollback/audit exists.
- `safe_update_applied -> verified`: replay eval passes.
- Any destructive, broad planner, cross-workspace, or low-confidence update becomes a proposal, not an automatic mutation.
- Verified improvements are rechecked by nightly regression. A repeated failure marks the event `regressed`.

### Feedback Mechanisms

- **Turn-level judge:** checks final answer against evidence slots, source coverage, confidence, and forbidden answer patterns before display.
- **Thread-level critic:** reviews whole conversations for failed assumptions, user rescue, repeated clarification, and eventual successful tool paths.
- **Learning event ledger:** stores each failure/correction with thread ID, run IDs, failed claim, successful evidence path, proposed fix, and rollback/proposal state.
- **Regression eval factory:** converts learning events into replayable evals with required tool patterns, forbidden claims, answer requirements, and confidence rules.
- **Tool-affordance memory:** durable Zopedia pages describing what tools can answer, common failure modes, and fallback paths learned from real use.
- **Planner-contract proposals:** recurring failures update the agent contract through reviewable proposals, not hidden branches.
- **Nightly learning job:** mines recent chat logs, runs critics, creates evals, applies safe updates, and records pass/fail.
- **Pre-answer cannot gate:** before saying data is unavailable, check whether another primitive tool or analysis path can answer.
- **Outcome measurement:** if a failure recurs after a verified update, mark the prior fix insufficient and escalate.
- **Human feedback hooks:** correction, thumbs-down, "this is what I meant", and accepted-answer actions all create learning events.

### Regression Eval Contract

Persist generated evals under:

- `streamlit_alpaca_app/documents/architecture/new_features/zopedia/eval_cases/generated/`

Tests should load them from:

- `streamlit_alpaca_app/tests/test_zopedia_learning_evals.py`

Eval case shape:

```yaml
name: bond_yields_market_impact_price_history_recovery
thread_source: zthread_61ef63346b654add
question: "How are bond yields affecting the markets?"
required_tool_patterns:
  - "dataset.yield_curve_facts_1d OR dataset.yield_curve_observations"
  - "dataset.price_history for at least SPY, QQQ, XLF"
forbidden_answer_patterns:
  - "stock data is unavailable"
  - "cannot query actual stocks"
  - "daily movers returned empty, so no market data"
required_answer_claims:
  - "distinguishes observed ETF returns from causal proof"
  - "states yield curve direction"
  - "compares broad market, growth, and financials"
confidence_rule:
  - "max medium unless causal/statistical analysis succeeds"
```

### Job Wiring

Add job:

- Name: `zopedia-learning`
- Entry point: `pipeline.jobs.main.run_zopedia_learning_job`
- Service: `services.zopedia_learning.run_zopedia_learning_job`
- Schedule: nightly in dev; production only after eval gates pass.

Job output:

```json
{
  "threads_scanned": 0,
  "events_detected": 0,
  "events_triaged": 0,
  "evals_generated": 0,
  "safe_updates_applied": 0,
  "verified": 0,
  "rejected": 0,
  "regressed": 0
}
```

### Phase 0.5 Acceptance Gates

Required for completion:

- Latest bond-yield thread becomes a persisted learning event.
- Generated eval fixture exists for the bond-yield failure.
- Empty tool results carry structured `empty_reason`, fallback metadata, and user-safe explanations.
- ETF `daily_movers` symbol requests retry the on-demand path when materialized filtering loses all requested symbols.
- `event_significance` and `macro_relationship_checks_1d` empty states are diagnosed as insufficient/precomputed coverage, not missing market data.
- Replay of original question uses yield data and `dataset.price_history` for representative instruments before answering.
- Final answer does not claim stock/ETF data is unavailable after only `daily_movers`.
- Final answer does not expose tool-call IDs, run IDs, provider names, or debug refs.
- If statistical analysis is requested and fails, answer reports exact failure category.
- Safe tool-affordance or planner update is auditable and reversible.
- Nightly learning job runs once in dev and records counts.
- Targeted tests pass.

Phase 0.5 is done only when the original thread can be replayed and resolved in one assistant answer after tool use, with at most one internal repair loop.

Current gate state on 2026-05-19:

- Passed locally/headlessly: source-level empty-result contracts, benchmark replay, generated eval fixtures, safe learning update path, pipeline job wrapper, deploy-script job creation path, and targeted tests.
- Passed against dev DB from local runtime: `zopedia-learning` scanned 5 threads, detected 2 learning events, generated 2 evals, and applied 2 safe tool-affordance memory updates.
- Passed in deployed dev runtime: UI revision `sn-streamlit-ui-dev--0000334`, pipeline image `sha256:3d12cf37389cf5106c073cb8c3ec8b27ab1effe9d9a2cc9759f07e0e2a277a42`, API revision `sn-api-dev--0000030`, and Azure `zopedia-learning` execution `zopedia-learning-4onryyu` succeeded with `threads=7`, `events=2`, `evals=2`, `updates=2`, `verified=2`, `regressed=0`.
- Still open: run the original bond-yield question through the live dev Zopedia page and confirm the rendered answer stays clean.

## Phase 1: Evidence Backbone

Status: pending.

Goal:

- Every useful answer can trace back to source evidence.

Build:

- Richer source viewer polish.
- Source/page inspection from answer citations.
- API/headless upload endpoint only if non-Streamlit clients need direct file upload.
- OCR or scanned-PDF support only if there is a reliable parser path.
- Source trace path: answer -> Zopedia page -> retained source/chunk/document.

Done when:

- Pasted/uploaded source becomes searchable memory.
- Generated pages trace back to original source/chunk through read tools.
- Answers cite source evidence, not only generated wiki text.
- Source inspection works without exposing debug/internal refs as citations.

## Phase 2: Agent Quality Loop

Status: foundation implemented; eval depth and behavioral proof incomplete.

Already implemented:

- Shared Zopedia/AQL LLM boundary.
- DeepSeek reasoning trace capture when returned by the provider.
- Answer judge after draft synthesis.
- Final-answer revision path when judge returns `revise` or `insufficient`.
- Confidence cap when evidence is thin.
- Planner/tool access to page search, page read, source read, maintenance reports, proposals, mutations, rollback, and bounded analysis.

Still required:

- Expand answer-level evals around critic/judge.
- Make confidence depend on measured source coverage, not tone.
- False-premise detection.
- Page search -> page read -> source read behavior in planner contract and evals.
- Bounded tool/read/model budgets.
- Reasoning/tool trace that is useful and stable in UI, API, and saved chat history.

Done when:

- Hard questions trigger more evidence or honest gaps.
- False information is challenged.
- Confidence is tied to evidence coverage.
- The answer judge can change the final answer before any surface renders it.

## Phase 3: Automatic Memory And Maintenance

Status: mutation, proposal, rollback, reflection, and maintenance-job paths exist; production-grade memory quality is not proven.

Already implemented:

- Post-answer memory reflection can decide `no_action`, `apply_mutation`, or `propose_change`.
- Typed mutation API applies safe page upserts, metadata patches, and links.
- Risky/unsupported mutation types become review proposals.
- Mutation audit rows include evidence refs, before/after page state, actor/source context, and rollback hints.
- Rollback API can restore audited page mutations.
- `zopedia-maintenance` job exists and calls the maintenance service.
- Maintenance snapshot produces backlinks, communities/godnode rows, issue reports, mutation audits, proposals, and persisted reports.

Still required:

- Prove safe source-backed updates commit automatically.
- Prove risky updates escalate.
- Prove rollback works in realistic cases.
- Prove maintenance detects stale, duplicate, orphan, weak-source, and contradictory pages.
- Prove future answers use changed memory.

Done when:

- New evidence can update memory automatically.
- Risky updates wait for review.
- Safe maintenance changes can apply automatically.
- Risky maintenance changes escalate with preview.
- Every mutation can be inspected and reversed.
- Future answers use changed memory.

## Phase 4: EDA And Analysis Quality

Status: runner implemented; product-quality evals pending.

Current implemented slice:

- `scikit-learn` runtime dependency.
- `services/zopedia_analysis.py` bounded child-process runner.
- AST validation, import controls, timeout, memory/output limits, structured inputs, metrics, tables, charts, artifacts, and best-effort durable persistence.
- `analysis.run_python` exposed through shared agent tool catalog and normal tool path.
- Thinking trace renders compact analysis metrics/tables/charts.

Still required:

- Prevent generated code flattening from producing invalid one-line scripts.
- Add one internal repair loop for rejected/failed analysis.
- Require explicit dataset refs for analysis.
- Surface precise failure categories to the user.
- Cite analysis artifacts in final answers.

Done when:

- Analysis results are cited as evidence.
- Useful analysis findings can become memory through the automatic memory loop.
- Product evals confirm the agent can collect data, reason over it, adapt, and state limitations.

## Phase 5: Product Evals And Go/No-Go

Status: pending.

Required eval suites:

- UI shell eval: first viewport, button count, hidden admin, no raw IDs, row-as-action, source attach in composer, stable trace expansion, structured answer text, passage drilldown, no unrelated source cards.
- Anti-fragile learning eval: rescued thread -> learning event -> eval -> verified future answer.
- Company research eval: fundamentals + recent news + retained history.
- Macro research eval: local data first, external query only when local data is missing.
- False-information eval: challenge bad assumptions.
- Source trace eval: answer -> page -> retained chunk/source.
- Mutation eval: safe update auto-commits, risky update escalates, rollback works.
- Maintenance eval: stale/duplicate/orphan/source-weak cases detected.
- Analysis eval: EDA/scikit run -> cited answer -> durable artifact.
- No-hardcoding scan.
- Parity comparison against original Zopedia core flows.

Go if:

- Phase 0 product shell passes human/browser-path review.
- Phase 0.5 anti-fragile learning proof passes.
- Source-backed memory works end to end.
- Answers cite page/source evidence.
- Critic/judge catches unsupported claims.
- Safe mutations auto-commit with rollback.
- Risky mutations escalate.
- Company, macro, false-premise, staleness, mutation, maintenance, and analysis evals pass.
- Dev deploy confirms real UI/API/job paths.

No-go if:

- Answers sound good but skip evidence.
- Wiki pages cannot trace to source.
- Mutations are unlogged or not reversible.
- Confidence is not tied to evidence coverage.
- User corrections do not become durable learning events/evals.
- UI hides the research path or looks like a control panel.
- DeepSeek breaks structured tool/JSON reliability.
- Result feels worse than original Zopedia.

## Defer

Do not lead with:

- full graph explorer
- complex admin config UI
- destructive automatic deletion
- large-scale document conversion
- broad multi-agent framework unrelated to the memory engine

Add them only after the core product loop works.

## Immediate Next Step

Finish Phase 0.5 verification:

1. Run the original bond-yield question through the live dev Zopedia page and confirm the UI answer stays clean: no unavailable-data claim, no tool/debug IDs, structured text, stable trace.
2. Only then move to Phase 1 source-trace hardening.

## 2026-05-20 Dev Deployment Evidence

The AQL/Zopedia engine spine was deployed to all dev surfaces that execute product LLM work:

- UI: `sn-streamlit-ui-dev--0000335`, image digest `sha256:df6b9fb6768d8875e6abbdc9287dddfbe749b633c7ed4ede93a1ac961fd8dcde`, root status `200`.
- API: `sn-api-dev--0000031`, image digest `sha256:8d3cdd4ed35bd6aefb2add5d5f2d870c5323e72c7bb928115bde2cd6c438a2b2`, `/health` passed.
- Pipeline jobs: image digest `sha256:e4f2e02c7d3ce7033e3680db88dcc47b9ef82460d5740a6fd09608132a4f3a23`.
- Scheduled jobs refreshed on the new image include `attention-home-build`, `trading-agent-build`, `zopedia-maintenance`, and `zopedia-learning`.

This matters because the change is not just library code: the UI chat path, API/runtime service imports, attention homepage job, trading-agent job, maintenance job, and learning job all run on deployed dev containers.
