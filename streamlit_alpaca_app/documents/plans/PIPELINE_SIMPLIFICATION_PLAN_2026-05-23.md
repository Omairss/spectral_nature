# Pipeline Simplification Plan

Date: 2026-05-23

## Objective

Reduce the Zopedia/AQL/attention pipeline to one spine without changing product behavior.

This is a deletion plan. The target is a 10:1 reduction in operational complexity at the pipeline boundary:

- one LLM/research entrypoint instead of Omnibar, AQL, Zopedia, page summary, and attention-specific side paths
- one homepage materialization contract instead of separate visible summary, audio summary, legacy homepage summary, and refresh placeholders
- one renderer per product surface instead of Home v2/Home v3 split renderers
- zero code-generated fallback narratives
- zero compatibility shims in the core path
- zero non-fatal wrappers around required product artifacts
- zero hedged success states for required work
- zero duplicate ownership of the same product behavior across files

The product remains functionally the same when dependencies are healthy. If a required dependency fails, the job or request fails with a typed error. It does not synthesize replacement content, mark required work as "mostly done," or continue through a soft-success path.

## Current Complexity To Remove

Observed code anchors:

- `app.py` is 13,874 lines and owns both routing and heavy product rendering.
- `pipeline/jobs/main.py` is 2,308 lines and dispatches every scheduled job from one file.
- `pipeline/jobs/attention_home_build.py` is 1,290 lines and mixes attention graph build, Zopedia enrichment, homepage summary, audio, ticker snapshots, page summaries, and legacy compatibility frames.
- `services/omnibar_agent.py` is 3,309 lines and still owns the Zopedia planner loop.
- `services/aql_zopedia_engine.py` is a facade that delegates to `omnibar_agent`.
- `data_access/layer.py` is 3,696 lines and still contains legacy attention title detection and fallback summary logic.
- Compatibility shims exist in `services/market_activity_shared.py`, `services/aql/_agentic.py`, `services/aql/chat_log.py`, and `services/aql/scratchpad.py`.
- Fallback generators exist in AQL extraction/writing, attention research, page summaries, and Zopedia learning.

These are the simplification targets. They are not architecture preferences; they are sources of duplicated behavior.

## Non-Negotiable Rules

1. Delete, do not hide.
2. One public product entrypoint for LLM research: `run_aql_zopedia_agent`.
3. One provider boundary for LLM config: `services/zopedia_runtime.py`.
4. One materialized homepage summary row owns text, evidence, status, and audio.
5. One failure contract:
   - `failed`
   - `unavailable`
   - `insufficient_evidence`
   - `skipped_by_contract`
6. No code-written market prose.
7. No "best effort" branch for a required artifact.
8. No UI-triggered heavy analysis except chat and explicit user actions.
9. No product-screen operational controls. Job controls live in Admin.
10. No new user-facing behavior, copy, navigation, or workflow decisions in this cleanup.
11. Product UI copy stays copy-minimal. Do not add generic marketing, trust, access, workspace, or refresh filler while moving renderers.
12. Every module has one owner and one reason to exist.
13. Public APIs are explicit; no compatibility aliases or legacy re-export layers remain in the core path.
14. Imports follow direction. Lower layers never import Streamlit/app modules.
15. No hedged operational success language. Required work either succeeds, fails with a typed status, or is explicitly skipped by contract.

## Valid Fail Conditions

These failures are valid and must fail loudly:

- required secret is missing
- required upstream dataset is missing
- required materialized dataset is empty
- LLM provider is unavailable
- required tool call times out
- ElevenLabs generation fails for a materialized audio artifact
- evidence coverage is below the declared contract
- schema validation fails
- a planner chooses an unsupported tool
- an old import path remains after deletion

Invalid behavior:

- return a synthetic fallback answer
- silently render stale summary text
- render a summary while narrating a different summary
- write empty frames to make a job pass
- swallow a required job-stage exception and continue
- show a product placeholder that implies refresh when the artifact actually failed
- label a required failure as a warning, partial success, best effort, or non-fatal continuation

## Target Architecture

The final shape is:

```text
scheduled job or chat request
  -> typed request contract
  -> AQL/Zopedia engine
  -> tool catalog
  -> evidence pack
  -> judge/critique
  -> typed result
  -> materialized artifact or chat response
  -> thin UI/API renderer
```

Every non-chat LLM surface uses the same path:

- homepage summary
- Broad Economy summary
- attention feed summaries
- ticker deep dives
- page agentic summaries
- Zopedia maintenance
- Zopedia learning
- ElevenLabs summary narration source text

## Codebase Efficiency Contract

The cleanup is not excellent unless future changes become smaller, safer, and easier to localize.

Clean codebase target:

- `app.py` owns bootstrapping, auth/session routing, and high-level page dispatch only.
- `presentation/*` owns rendering and does not start jobs, call LLMs, or mutate durable state.
- `services/*` owns business logic, orchestration, typed contracts, and provider boundaries.
- `data_access/*` owns reads/writes/query contracts only; it does not generate product prose.
- `pipeline/jobs/*` owns scheduled materialization only; job modules do not import Streamlit/app code.
- `scripts/*` owns deploy/run automation only; scripts do not encode product fallback behavior.
- `tests/*` assert canonical contracts and user-visible behavior, not old module names.

Import direction:

```text
app -> presentation -> services -> data_access
pipeline/jobs -> services -> data_access
api -> services -> data_access
scripts -> external CLIs or package entrypoints
```

Forbidden import direction:

- `services` importing `app`
- `data_access` importing `presentation` or `app`
- `presentation` importing `pipeline/jobs`
- `pipeline/jobs` importing `app`
- circular imports between service modules

Code efficiency gates:

- one concept has one implementation file and one public name
- old names are deleted, not forwarded through wrappers
- route aliases can remain only at the routing boundary
- each moved module gets ownership tests that run without importing the full Streamlit app
- every phase records removed files, removed functions, deleted lines, remaining grep hits, and replacement owner
- no phase is complete if total production code grows without a named deletion in the same ownership area
- verification starts with ownership-level tests, then shared boundary tests, then final full suite and dev browser proof

Target reductions:

- `app.py`: remove product renderer bodies and duplicate Home renderers
- `pipeline/jobs/main.py`: shrink to dispatch/contract validation only
- `pipeline/jobs/attention_home_build.py`: split into small materialization steps with typed inputs/outputs
- `services/omnibar_agent.py`: deleted after Zopedia ownership move
- compatibility shims: deleted after canonical imports land
- fallback generators: deleted, replaced by typed failure contracts

Acceptance:

- `python scripts/pipeline_simplification_inventory.py --summary` reports one owner for every LLM path, summary path, homepage path, job trigger, and renderer
- `python scripts/pipeline_simplification_inventory.py --imports` reports no forbidden imports or cycles in the touched areas
- `python scripts/pipeline_simplification_inventory.py --deletions` reports net production-line reduction for every phase after Phase 1
- old names such as `omnibar`, `homepage_v2`, `fallback_summary`, and `legacy` are absent from production code except migration comments explicitly scheduled for deletion
- a new engineer can find the owner of homepage summary, Broad Economy summary, Zopedia chat, attention summary, and audio narration from file names and contracts alone

## Specific Suggested Improvements

These are the concrete changes that make the codebase cleaner. They are intentionally file-level, not architectural slogans.

| Area | Current Problem | Specific Improvement | Delete/Move | Proof |
|---|---|---|---|---|
| Streamlit app shell | `app.py` owns routing, auth UI, mobile chrome, Home rendering, audio, Broad Economy, and agent calls. | Keep `app.py` as boot/auth/router only. Move rendering into presentation modules and service calls behind typed service functions. | Move `_render_homepage_v2`, `_render_homepage_v3`, `_render_homepage_v2_graph_banner`, `_render_homepage_v3_story_fragment`, homepage audio render helpers, and homepage summary card logic to `presentation/home.py`. Move mobile shell helpers to `presentation/mobile_shell.py`. | `app.py` contains no `_render_homepage_*` function bodies and no ElevenLabs client construction. |
| Mobile/auth chrome | Mobile shell had generic copy and is embedded in `app.py`. | Make mobile chrome a small presentation module with brand, date selector, nav, and auth actions only. | Move `_render_mobile_brand_header`, `_render_mobile_auth_shell`, `_render_mobile_public_home_shell`, `_render_mobile_workspace_shell` to `presentation/mobile_shell.py`; delete generic captions permanently. | `rg "Secure access|Private client access|Market narrative home" streamlit_alpaca_app/app.py streamlit_alpaca_app/presentation/mobile_shell.py` returns no hits. |
| Home summary/audio | Visible summary, narration source, and old homepage payload can diverge. | Create one `services/homepage_summary_contract.py` model and one reader/writer path. UI only renders that contract. | Delete old `homepage_summary` payload reads, `__MARKET_SUMMARY__` special branching after migration, UI async audio generation, and refresh placeholder copy. | Visible summary normalized text hash equals audio source text hash in tests and dev browser proof. |
| Broad Economy summary | Broad Economy summary logic lives inside `pipeline/jobs/main.py` and still uses warning-style failure behavior. | Move Broad Economy materialization to a dedicated macro summary service and macro job module. | Create `services/broad_economy_summary.py`; move macro summary calls from `pipeline/jobs/main.py` into `pipeline/jobs/macro_fred.py`; delete manual/analyze UI buttons and warning continuation paths. | `macro-fred-daily` produces the Broad Economy summary artifact; UI reads artifact only; missing artifact shows typed unavailable state. |
| Zopedia agent ownership | `services/aql_zopedia_engine.py` delegates into `services/omnibar_agent.py`, so the product name and owner are wrong. | Make Zopedia the implementation owner and AQL the stable engine boundary. | Create `services/zopedia_agent.py`; move `_run_zopedia_agent_loop` and helpers there; move `services/omnibar_research.py` to `services/zopedia_research.py`; delete `services/omnibar.py`, `services/omnibar_agent.py`, and `services/omnibar_research.py`. | `rg "omnibar_agent|omnibar_research|run_omnibar_agent|_run_zopedia_agent_loop" streamlit_alpaca_app` has no production hits. |
| Page and trading agent wrappers | `page_agentic_summary.py` and `trading_agent.py` import `run_aql_zopedia_agent` through local wrapper functions. | Replace wrapper imports with the canonical engine call directly or one shared adapter named for Zopedia/AQL. | Delete private `_run_agent`-style wrappers that only forward kwargs. | `rg "from \\.aql_zopedia_engine import run_aql_zopedia_agent" streamlit_alpaca_app/services` shows direct canonical uses or one approved adapter only. |
| Fallback summaries | `fallback_summary` is passed through jobs, services, data access, and company/news helpers, creating invisible alternate prose paths. | Remove fallback prose fields from core payloads. Use typed failure metadata instead. | Delete `fallback_summary` writes/reads in `attention_home_build.py`, `attention_materialized.py`, `data_access/layer.py`, `company.py`, `attention_live_research.py`, `aql/collector.py`, and `data_cache.py`. | `rg "fallback_summary" streamlit_alpaca_app/services streamlit_alpaca_app/data_access streamlit_alpaca_app/pipeline` has no core production hits. |
| AQL fallback writers/extractors | AQL can manufacture claims/events when evidence is thin. | Delete code-generated narrative/event fallback functions. | Delete `_fallback_claims_from_chunks` from `services/aql/extractor.py`; delete `_fallback_event_writer` from `services/aql/writer.py`; remove re-exports from `services/aql/__init__.py` and `services/aql/_agentic.py`. | Missing evidence returns `insufficient_evidence`; no generated claims/events are emitted. |
| Attention live research | Attention has LLM and non-LLM fallback branches mixed with event research. | Separate query construction, evidence retrieval, and result contract. Remove fallback prose helpers. | Delete `_fallback_event_why_text` and fallback event query prose where it creates user-facing explanation; keep only deterministic query construction if it is not user-facing prose. | Attention research tests assert typed gaps, not fallback text. |
| Attention home job | `attention_home_build.py` mixes graph build, enrichment, summary, audio, snapshots, page summaries, and compatibility frames. | Split into stage modules with explicit inputs/outputs. | Create `pipeline/jobs/attention_home.py` plus service modules: `services/attention_home_artifacts.py`, `services/homepage_summary_contract.py`, `services/homepage_audio.py`. Delete empty compatibility frame writes. | Each stage can be unit-tested with fixture frames; required stage failure exits nonzero. |
| Hedged success states | Required stages can be described as warnings, skipped work, non-fatal errors, refresh placeholders, or partial success. | Convert required work to hard typed failures. Reserve caveats for actual analytical uncertainty in the answer, not pipeline execution. | Delete warning-style continuation paths, soft-success status text, and required-stage `try/except` blocks that continue without an explicit `skipped_by_contract`. | `rg "non-fatal|best effort|falling back|fallback unavailable|refreshing from|skipped: missing|partial success|warning-style" streamlit_alpaca_app/services streamlit_alpaca_app/pipeline streamlit_alpaca_app/app.py` has no required-path hits. |
| Pipeline dispatcher | `pipeline/jobs/main.py` is a broad dispatcher plus job logic. | Make `main.py` dispatch only. Move job bodies to named modules. | Create modules listed in Phase 8. Delete job body functions from `main.py`. | `pipeline/jobs/main.py` contains contract registry, argument parsing, and dispatch only. |
| Deploy scripts | Deploy scripts still carry stale targets and warning-style env validation. | Make deployment inventory authoritative and fail before mutation. | Add inventory read helpers; remove guessed prod API target; convert LLM env warnings into validation functions. | `deploy_api_azure.sh --target prod` fails before Azure mutation if prod API app is absent. |
| Tests | Tests still assert old implementation names and fallback behavior. | Tests should assert behavior at canonical boundaries. | Rename/delete `tests/test_omnibar_agent.py`; add `tests/test_zopedia_agent.py`, `tests/test_homepage_summary_contract.py`, `tests/test_pipeline_simplification_inventory.py`, `tests/test_required_job_contracts.py`. | Test names and assertions use Zopedia/AQL/homepage contracts, not Omnibar/fallback names. |
| Documentation | Old plans and docs preserve obsolete side paths and make future agents relearn bad routes. | Keep migration notes, delete or archive stale operational instructions. | Move old side-path docs under an archive folder or delete if superseded. Keep this plan and philosophy as active instructions. | `rg "run_omnibar_agent|Home v2|fallback_summary" streamlit_alpaca_app/documents` returns only archived notes and this plan. |

Measurable improvement targets:

- remove at least 4,000 net production lines by the end of Phases 2-8
- reduce `app.py` by at least 2,500 lines
- reduce `pipeline/jobs/main.py` to under 500 lines
- delete `services/omnibar_agent.py`, `services/omnibar.py`, and `services/omnibar_research.py`
- delete all fallback narrative functions and payload fields from core paths
- keep public behavior stable through the golden suite and dev browser checks
- leave no new wrapper whose only job is preserving an old name

## Priority Order

Execute in this order. Do not start broad rewrites until the inventory and behavior locks are in place.

1. **Inventory first.**
   Build `scripts/pipeline_simplification_inventory.py` and classify every LLM call, summary writer, renderer, job control, fallback helper, compatibility shim, and heavy UI trigger.
   This prevents deleting from memory or guessing ownership.

2. **Golden behavior second.**
   Add the contract tests before changing behavior-adjacent code. The first tests must cover Home summary/audio, Broad Economy summary, Zopedia chat follow-up, Python tool call, mobile auth/home, and one attention build.

3. **Rename ownership at the source.**
   Move Omnibar implementation ownership into Zopedia modules before touching UI polish. The product cannot be clean while `services/omnibar_agent.py` is still the core planner.

4. **Collapse Home summary/audio.**
   Make homepage summary, narration text, evidence, and status one typed artifact. This removes one of the largest live divergence risks.

5. **Extract renderers out of `app.py`.**
   Move Home and mobile renderers into presentation modules after their data contract is stable. Do not move data logic into presentation.

6. **Delete fallback prose.**
   Once typed failures are tested, delete code-written summaries, fallback claims, fallback event prose, and empty compatibility frames.

7. **Split scheduled jobs.**
   Shrink `pipeline/jobs/main.py` only after contracts are in place, so schedules stay identical while ownership becomes explicit.

8. **Delete shims, stale tests, stale docs, and unused config.**
   This is the cleanup dividend. Do not leave old names as wrappers after the canonical modules work.

## First Implementation Slice

The first slice should be small, measurable, and behavior-preserving.

Files to add:

- `streamlit_alpaca_app/scripts/pipeline_simplification_inventory.py`
- `streamlit_alpaca_app/tests/test_pipeline_simplification_inventory.py`
- `streamlit_alpaca_app/tests/test_homepage_summary_contract.py`
- `streamlit_alpaca_app/tests/test_zopedia_agent_boundary.py`

Files to touch:

- `streamlit_alpaca_app/documents/plans/PIPELINE_SIMPLIFICATION_PLAN_2026-05-23.md`
- `streamlit_alpaca_app/documents/learnings.md`
- `streamlit_alpaca_app/documents/mistakes.md`

Files not to touch in the first slice:

- `streamlit_alpaca_app/app.py`
- `streamlit_alpaca_app/services/omnibar_agent.py`
- `streamlit_alpaca_app/pipeline/jobs/main.py`
- `streamlit_alpaca_app/pipeline/jobs/attention_home_build.py`

First-slice deliverables:

- inventory JSON with owners, import paths, and deletion candidates
- failing-or-passing tests that prove the inventory catches old Omnibar, fallback, Home v2/v3, job-control, and direct-provider paths
- no production behavior changes
- no deployment

First-slice proof commands:

```bash
python3 streamlit_alpaca_app/scripts/pipeline_simplification_inventory.py --summary
python3 streamlit_alpaca_app/scripts/pipeline_simplification_inventory.py --imports
PYTHONPATH=streamlit_alpaca_app .codex-venv/bin/pytest -q \
  streamlit_alpaca_app/tests/test_pipeline_simplification_inventory.py \
  streamlit_alpaca_app/tests/test_homepage_summary_contract.py \
  streamlit_alpaca_app/tests/test_zopedia_agent_boundary.py
```

The first slice is complete when the inventory lists every deletion target by owner and replacement. It is not complete if it only prints grep hits.

## Codebase Efficiency Improvements By Class

Specific cleanup classes:

- **Duplicate renderer deletion:** remove old Home v2/v3 function bodies after one renderer preserves the same controls.
- **Duplicate agent deletion:** remove Omnibar files after Zopedia owns the planner implementation.
- **Duplicate artifact deletion:** remove separate homepage summary/audio payloads after one artifact owns both.
- **Fallback prose deletion:** remove code-authored market text and replace it with typed failures.
- **Hedged success deletion:** remove warning/non-fatal/partial-success paths for required work and replace them with typed statuses.
- **Compatibility wrapper deletion:** remove modules that only preserve old import names.
- **Dispatcher thinning:** move job bodies out of `pipeline/jobs/main.py`; leave registry, validation, and dispatch.
- **Layer boundary cleanup:** move Streamlit rendering out of service/data modules and service/data work out of presentation modules.
- **Test cleanup:** delete tests that protect obsolete names; keep tests that protect user-visible behavior.
- **Config cleanup:** delete env/config values that only feed deleted fallback or wrapper paths.
- **Doc cleanup:** archive stale plans that describe old side paths so future agents do not rebuild them.

## Second-Pass Opportunities

This pass looks past the already-known big deletions and names additional cleanup opportunities found in the current codebase.

| Opportunity | Current Anchor | Why It Matters | Specific Improvement | Proof |
|---|---|---|---|---|
| API route names still expose Omnibar | `api/main.py` imports `omnibar as omnibar_service` and exposes `/v1/omnibar/resolve` plus `/v1/omnibar/suggestions`. | The backend product spine can be Zopedia while the API still teaches every client the old concept. That keeps the vestige alive outside Streamlit. | Add canonical `/v1/zopedia/resolve` and `/v1/zopedia/suggestions` handlers backed by the Zopedia/AQL service. Keep `/v1/omnibar/*` only as a routing-boundary alias during migration, with no Omnibar service import. | `rg "/v1/omnibar|omnibar_service" streamlit_alpaca_app/api streamlit_alpaca_app/app.py` returns only explicit route-alias comments or no hits after migration. |
| Auth scopes still use Omnibar names | `services/api_auth.py` defines `SCOPE_OMNIBAR_RESOLVE = "omnibar:resolve"` and tests assert it. | Scope names are durable API surface. Keeping old scopes makes future agent keys and docs depend on the old endpoint vocabulary. | Introduce `SCOPE_ZOPEDIA_RESOLVE = "zopedia:resolve"`. Map old scope values only at auth-boundary migration if existing keys need continuity, then rotate/delete the old scope. | `rg "SCOPE_OMNIBAR|omnibar:resolve" streamlit_alpaca_app/services streamlit_alpaca_app/tests streamlit_alpaca_app/api` returns no core hits. |
| Data access still owns LLM loading | `data_access/layer.py` imports `load_llm_client` / `load_embedding_client` and calls them inside query resolution. | Data access should not decide model use. This violates the intended import direction and makes query reads capable of hidden synthesis. | Move those LLM-backed query paths into services with explicit contracts. `data_access/layer.py` should return stored/query data only, or call a service-provided dependency injected from above. | `rg "load_llm_client\\(|load_embedding_client\\(" streamlit_alpaca_app/data_access` returns no hits. |
| Boundary test scans the wrong symbol | `tests/test_zopedia_native_llm_boundary.py` checks for `load_zopedia_llm_client`, but the actual public loader is `load_aql_zopedia_llm_client`. | A test that scans the wrong string gives false confidence and lets orphan Zopedia/AQL LLM calls spread. | Fix the scan target, then tighten the allowlist. Also scan direct `.generate_json(` calls and require each allowed use to be a named engine mode or tool implementation. | The test fails before cleanup on real offenders and passes only after the owner paths are reduced. |
| Evidence provenance still says Omnibar | `services/aql/evidence_pack.py` defaults `surface="omnibar_agent"` and `services/saa/storage.py` writes `source_provider="omnibar_agent"`. | Even if code paths are renamed, persisted evidence and retained memory still tell the system that Omnibar produced them. | Change new writes to `zopedia_agent` / `aql_zopedia`. Add a read/display normalization for older rows if historical data must remain readable. | `rg "omnibar_agent" streamlit_alpaca_app/services streamlit_alpaca_app/data_access streamlit_alpaca_app/pipeline` returns no new-write paths. |
| Agent tool catalog depends on old research module | `services/agent_tools.py` imports `omnibar_research` and delegates both research and Zopedia tools to it. | This makes the tool catalog conceptually Zopedia but operationally Omnibar. It also keeps a large research module as an implicit dependency. | Split into `services/zopedia_research.py` and `services/research_tools.py`, then have `agent_tools.py` dispatch to those canonical modules. Keep `agent_tools.py` as schema/dispatch only. | `rg "omnibar_research" streamlit_alpaca_app/services streamlit_alpaca_app/tests` returns no hits. |
| Lazy legacy agent namespace keeps old modules reachable | `services/agents/__init__.py` exposes `_LEGACY_SUBMODULES` including `omnibar`, `omnibar_agent`, and `omnibar_research`. | This is an escape hatch. Even if direct imports are cleaned, lazy access can pull old modules back into new code. | Replace the lazy namespace with explicit exports for chat history, scratchpad, and shared agent services. Delete legacy submodule loading. | `rg "_LEGACY_SUBMODULES|omnibar_agent|omnibar_research" streamlit_alpaca_app/services/agents streamlit_alpaca_app/tests` returns no hits. |
| Presentation still loads data and emits fallback payloads | `presentation/attention_content.py` calls cached data loaders and returns `fallback_summary` structures. | Presentation should render already-resolved contracts. Data loading and unavailable-state decisions belong in services. | Move `_load_recent_news_payloads` and `_load_attention_context_payloads` into a service. Presentation receives typed payloads and never fabricates `fallback_summary`. | `rg "_load_.*cached|fallback_summary" streamlit_alpaca_app/presentation` returns no hits. |
| Market-event fallback is separate from the named fallback cleanup | `services/attention_market_events.py` still builds a generic fallback event when no themed event is found. | This is code-generated market narrative outside the AQL/Zopedia contract. It can create confident-looking market events from thin evidence. | Replace fallback event construction with `insufficient_evidence` / empty typed event output, depending on the downstream artifact contract. | `rg "_build_fallback_event|driving the latest attention move|Cause remains unresolved" streamlit_alpaca_app/services/attention_market_events.py` returns no hits. |
| Trading Agent still has timeout-generated fallback suggestions | `services/trading_agent.py` returns `_fallback_trading_agent_suggestions(...)` when AQL times out. | That is a synthetic product answer after the required research path failed. It violates the no-soft-success rule. | Return typed `unavailable` / `failed` with the safe error text and no generated watchlist candidates. Let the scheduled job fail if the artifact is required. | `rg "_fallback_trading_agent_suggestions|AQL agent failed" streamlit_alpaca_app/services/trading_agent.py` has no generated-suggestion path. |
| Source ingestion lives in Streamlit | `app.py` browses URLs/YouTube, extracts text, and calls `ingest_zopedia_source` with an LLM client. | UI should collect inputs; service should own source resolution, transcript fallback, evidence metadata, and model use. | Create `services/zopedia_source_ingestion.py` with URL, file, text, and YouTube ingestion entrypoints. UI calls the service and renders the result. | `rg "browse_page\\(|load_aql_zopedia_llm_client\\(surface=\"zopedia.ui.ingest_source\"" streamlit_alpaca_app/app.py` returns no hits. |
| Internal API docs still teach Omnibar | `app.py` API reference lists `/v1/omnibar/*`. | Docs are executable product memory for users and future agents. Leaving old endpoint names makes the cleanup incomplete even if code works. | Update active docs to canonical Zopedia endpoints after route migration. Archive old SN2/Omnibar docs instead of leaving them in active plan indexes. | `rg "/v1/omnibar|omnibar:resolve" streamlit_alpaca_app/documents/README.md streamlit_alpaca_app/documents/plans streamlit_alpaca_app/app.py` returns only archived docs. |
| Allowlist tests protect old direct JSON calls | `tests/test_zopedia_native_llm_boundary.py` allows many service files to call `.generate_json(` directly. | This freezes the current sprawl instead of forcing one orchestration spine. | Replace broad allowlists with mode-level contracts: Zopedia/AQL engine, approved low-level extraction tools, and provider adapter only. Every other service requests a named engine/tool mode. | `rg "\\.generate_json\\(" streamlit_alpaca_app/services streamlit_alpaca_app/pipeline streamlit_alpaca_app/app.py` is explainable by a short allowlist tied to engine modes. |
| Runtime cleanup should include stale compiled artifacts if tracked | `services/__pycache__`, `presentation/__pycache__`, `api/__pycache__`, and `pipeline/jobs/__pycache__` exist in the tree. | If any compiled artifacts are tracked, they create noise and false change surface. If ignored, no action is needed. | Verify tracking. Remove tracked cache artifacts and keep `.gitignore` authoritative. | `git ls-files '*__pycache__*' '*.pyc'` returns no tracked artifacts. |

## Execution Plan

### Phase 0: Inventory The Current Code Paths

Map the current call graph before deleting code.

Actions:

1. Add `scripts/pipeline_simplification_inventory.py`.
2. Use AST/static analysis where possible; use `rg` only as an input to the inventory.
3. Find every LLM client call, tool catalog call, summary generator, audio generator, materialized summary writer, UI heavy-action trigger, route renderer, compatibility shim, and fallback helper.
4. Classify each hit as:
   - canonical AQL/Zopedia path
   - old Omnibar path
   - direct provider call
   - code-generated fallback
   - UI-triggered heavy path
   - job-triggered materialization path
   - renderer path
   - compatibility shim
   - config/deploy path
5. Emit `artifacts/pipeline_simplification_inventory.json`.
6. Use the inventory as the deletion checklist for Phases 2-10.

Acceptance:

- every touchpoint is named with file, function, owning surface, and target replacement
- every user-facing generated text path is assigned to one materialized artifact or chat response
- every renderer is assigned to one presentation owner
- every compatibility shim is assigned to a deletion phase
- no phase begins deletion against an unclassified touchpoint

### Phase 1: Lock Behavior Before Deletion

Create a golden behavior suite before deleting anything.

Required fixtures:

- latest homepage run with Zopedia market summary and audio
- Broad Economy summary run
- one company query with fundamentals, recent news, and retained memory
- one macro query with internal FRED data plus live query gap
- one attention feed build
- one Zopedia maintenance run
- one Zopedia learning run
- one chat follow-up thread
- one Python analysis tool call
- one mobile login/home render path

Assertions:

- visible answer text matches before/after for healthy dependencies
- audio source text hash matches visible summary text hash
- same tool coverage is preserved
- same materialized datasets are produced
- no product route loses a control or section
- no fallback prose appears
- failures produce typed errors and nonzero job/request failure where required
- mobile/auth screens do not reintroduce generic filler captions
- ownership-level tests can run without importing the full Streamlit app where the phase touches extracted modules

Deliverable:

- `tests/test_pipeline_simplification_contract.py`
- `scripts/pipeline_simplification_probe.py`
- `scripts/pipeline_simplification_inventory.py`

### Phase 2: Make AQL/Zopedia The Only Agent Owner

Move implementation ownership out of Omnibar.

Actions:

1. Create `services/zopedia_agent.py`.
2. Move `_run_zopedia_agent_loop` and its private helper functions from `services/omnibar_agent.py` into `services/zopedia_agent.py`.
3. Keep `run_aql_zopedia_agent` as the only public entrypoint.
4. Rename Omnibar-specific internal names to Zopedia names.
5. Delete `services/omnibar_agent.py` after imports are moved.
6. Delete `services/omnibar.py` after moving any remaining public imports to Zopedia names.
7. Move reusable research tools from `services/omnibar_research.py` into `services/zopedia_research.py`.
8. Delete `services/omnibar_research.py` after imports are moved.
9. Delete all tests that assert Omnibar implementation names; replace with Zopedia boundary tests.

Acceptance:

- `rg "omnibar_agent|omnibar_research|run_omnibar_agent|_run_zopedia_agent_loop" streamlit_alpaca_app` returns no production hits.
- all chat, homepage, attention, summary, maintenance, and learning calls enter through `run_aql_zopedia_agent` or `load_aql_zopedia_llm_client`.
- ownership tests import `services/zopedia_agent.py` without importing Streamlit.

### Phase 3: Delete Code-Generated Narrative Fallbacks

Remove fallback prose generation. Keep typed failures.

Actions:

1. Delete `_fallback_answer` in the Zopedia agent path.
2. Delete `_fallback_claims_from_chunks` in `services/aql/extractor.py`.
3. Delete `_fallback_event_writer` in `services/aql/writer.py`.
4. Delete attention fallback why/what text functions in `services/attention_live_research.py`.
5. Delete `fallback_summary` usage from `data_access/layer.py` and related storage payloads.
6. Replace fallback return payloads with typed failures:
   - missing evidence -> `insufficient_evidence`
   - missing LLM -> `unavailable`
   - tool failure -> `failed`
7. Update renderers to display the typed state only when product already has an unavailable-state pattern.
8. Remove fallback-specific tests and add failure-contract tests.

Acceptance:

- `rg "fallback_answer|fallback_claims|fallback_event|fallback_summary|fallback_why|fallback_what_else" streamlit_alpaca_app/services streamlit_alpaca_app/data_access streamlit_alpaca_app/pipeline` returns no core-path hits.
- no user-facing answer is assembled from code templates.

### Phase 4: Make Required Job Outputs Required

Turn non-fatal required stages into hard failures.

Actions in `pipeline/jobs/attention_home_build.py`:

1. Signal extraction failure fails `attention-home-build`.
2. Zopedia market summary failure fails `attention-home-build`.
3. ElevenLabs audio generation failure fails `attention-home-build`.
4. Zopedia ticker enrichment failure fails `attention-home-build` for every ticker inside the artifact contract.
5. Page agentic summary failure fails `attention-home-build`.
6. Empty required frames fail before write.
7. Remove writes of empty compatibility frames.

Actions in `pipeline/jobs/main.py`:

1. Database tracking failure fails jobs that require tracking.
2. Missing upstream datasets fail dependent jobs.
3. Remove fallback universe/news/listing rebuilds from downstream jobs.
4. Each job declares required inputs and required outputs in one contract map.

Acceptance:

- `rg "non-fatal|skipped: missing|falling back|fallback unavailable|pd.DataFrame\\(\\)" streamlit_alpaca_app/pipeline/jobs` has no required-output paths.
- a missing required input exits nonzero and records failed status.
- successful jobs produce the exact required output list.

### Phase 5: Collapse Homepage Into One Materialized Contract

The homepage has one materialized summary source.

Actions:

1. Create a typed `homepage_summary_v1` contract:
   - `run_id`
   - `asof`
   - `status`
   - `answer_markdown`
   - `answer_text_hash`
   - `audio_text`
   - `audio_text_hash`
   - `audio_base64`
   - `audio_mime_type`
   - `evidence_pack_json`
   - `tool_calls_json`
   - `quality_review_json`
2. Migrate current `__MARKET_SUMMARY__` row into this contract.
3. Delete old `homepage_summary` payload usage.
4. Delete UI async audio generation for homepage summary.
5. Delete `Zopedia Summary is refreshing from the latest data.` branch.
6. UI reads only `homepage_summary_v1`.

Acceptance:

- visible homepage summary hash equals audio text hash source after markdown-to-text normalization.
- prod Home renders summary and audio from one artifact.
- if artifact is missing, Home displays the existing unavailable-state component, not refresh prose.
- homepage renderer imports the typed contract, not job internals or legacy helper payloads.

### Phase 6: Collapse Home v2/Home v3 Rendering

Keep features, delete duplicate renderers.

Actions:

1. Extract homepage rendering into `presentation/home.py`.
2. Move every unique Home v2/Home v3 widget into one renderer.
3. Preserve current route labels during cleanup.
4. Both route labels call the same renderer with route metadata only.
5. Delete `_render_homepage_v2`.
6. Delete `_render_homepage_v3`.
7. Delete `services/homepage_v2.py` after helper names become route-neutral. Completed 2026-05-27; shared helpers moved to `services/homepage_support.py`.
8. Delete Home v2-specific session-state keys after state migration.

Acceptance:

- Home and Home v2 retain the same user-visible controls that existed before cleanup.
- screenshots before/after match for the same selected state, except duplicated implementation details are gone.
- `rg "homepage_v2|Home v2|_render_homepage_v2|_render_homepage_v3" streamlit_alpaca_app/app.py streamlit_alpaca_app/services` has no implementation hits after route alias migration.
- `rg "Secure access|Private client access|Market narrative home|Zopedia Summary is refreshing" streamlit_alpaca_app/app.py streamlit_alpaca_app/presentation` returns no product-renderer hits.

### Phase 7: Delete Compatibility Shims

Remove import-path compatibility code from the core path.

Actions:

1. Delete `services/market_activity_shared.py`.
2. Delete `services/aql/_agentic.py`.
3. Delete `services/aql/chat_log.py`.
4. Delete `services/aql/scratchpad.py`.
5. Delete backward-compatible exports from `services/aql/__init__.py`.
6. Update all imports to canonical modules.

Acceptance:

- importing the app, API, and every job succeeds.
- `rg "Backward-compatible|compatibility|shim|legacy implementation" streamlit_alpaca_app/services streamlit_alpaca_app/pipeline streamlit_alpaca_app/data_access` returns no core-path hits.

### Phase 8: Split The Pipeline Job Dispatcher

Make job ownership explicit without changing schedules.

Actions:

1. Replace the monolithic `pipeline/jobs/main.py` dispatcher with a tiny dispatcher.
2. Move each job into its own module:
   - `jobs/universe_builder.py`
   - `jobs/equities_intraday.py`
   - `jobs/macro_fred.py`
   - `jobs/commodities_regime.py`
   - `jobs/options_liquid_universe.py`
   - `jobs/news_ingest.py`
   - `jobs/attention_home.py`
   - `jobs/trading_agent.py`
   - `jobs/entity_taxonomy.py`
   - `jobs/company_baseline.py`
   - `jobs/fundamentals_refresh.py`
   - `jobs/zopedia_maintenance.py`
   - `jobs/zopedia_learning.py`
3. Add a `JobContract` for each job:
   - required env
   - required secrets
   - required input datasets
   - required output datasets
   - timeout
   - materialized artifact contract when the job calls AQL/Zopedia
4. Dispatcher validates contract before running.
5. Delete per-job ad hoc env parsing where a contract owns it.

Acceptance:

- schedules and job names remain unchanged.
- each job can be run directly in tests.
- unknown job still exits `2`.
- missing contract dependency exits nonzero before work starts.
- each job module can be imported and unit-tested without importing the full dispatcher.

### Phase 9: Collapse Deploy Scripts Around Actual Inventory

Keep deploy behavior, remove stale targets.

Actions:

1. Make deploy scripts read actual container app inventory from `infra/.generated/deployment.local.env`.
2. Remove default prod API target `sn-api` unless the app exists.
3. If prod API is not provisioned, `deploy_api_azure.sh --target prod` fails immediately with "prod API app is not provisioned" before mutation.
4. Convert repeated LLM env var warnings into contract validation.
5. Add `scripts/deploy_all_dev.sh` and `scripts/promote_all_prod.sh` wrappers that call the existing deploy scripts in the correct order.

Acceptance:

- no deploy script mutates a guessed resource.
- prod promotion deploys exact dev image digests.
- missing target fails before attempting update.

### Phase 10: Delete Orphan Tests, Scripts, And Config

Clean tests and scripts after code deletion.

Actions:

1. Delete tests that assert removed Omnibar names.
2. Delete tests that assert fallback prose.
3. Delete config params used only by deleted fallback paths.
4. Delete stale docs that describe old side paths.
5. Keep eval scripts only if they call the canonical Zopedia/AQL endpoint.

Acceptance:

- `rg "omnibar|fallback|legacy|Home v2|homepage_v2" streamlit_alpaca_app/tests streamlit_alpaca_app/scripts streamlit_alpaca_app/documents` returns only migration notes and this plan.
- full targeted suite passes.

## Required Verification Gates

Every phase must pass these before moving on:

```bash
python3 -m py_compile streamlit_alpaca_app/app.py streamlit_alpaca_app/pipeline/jobs/main.py
PYTHONPATH=streamlit_alpaca_app .codex-venv/bin/pytest -q \
  streamlit_alpaca_app/tests/test_zopedia_native_llm_boundary.py \
  streamlit_alpaca_app/tests/test_pipeline_jobs.py \
  streamlit_alpaca_app/tests/test_data_access_query_service.py \
  streamlit_alpaca_app/tests/test_app_job_trigger_inventory.py
```

Final gate:

```bash
PYTHONPATH=streamlit_alpaca_app .codex-venv/bin/pytest -q
bash streamlit_alpaca_app/scripts/deploy_pipeline_azure.sh
bash streamlit_alpaca_app/scripts/deploy_ui_azure.sh --target dev
az containerapp job start --name attention-home-build --resource-group sn-pipeline-rg-03130136
```

Then verify:

- dev Home renders the same features
- mobile Home and mobile login remain copy-minimal
- prod is not touched until explicitly approved
- generated homepage summary/audio come from the same artifact
- Broad Economy summary is generated by `macro-fred-daily`
- Zopedia chat still supports follow-up, memory, tools, and Python analysis
- inventory reports no forbidden imports, cycles, or duplicate owners in touched areas
- Admin still owns job controls

## Definition Of Done

This cleanup is complete only when:

- all LLM calls route through AQL/Zopedia
- all non-chat generated summaries are materialized by jobs
- homepage summary and audio have one source artifact
- no core production file contains fallback narrative generation
- no core production file contains Omnibar implementation names
- no core production file contains backward-compatible AQL shims
- every scheduled job has a typed contract
- missing required inputs fail jobs
- UI/API renderers do not run hidden heavy analysis
- touched areas have one owner per concept and no forbidden imports
- the cleanup produces net production-code reduction, not rearranged growth
- old compatibility tests and configs are gone
- dev deployment and live browser verification pass

The codebase ends smaller, not just rearranged. The expected deletion targets are thousands of lines across `omnibar_agent.py`, duplicate homepage render paths, AQL fallback helpers, attention fallback helpers, compatibility shims, and stale tests.
