# Agentic Ticker Background (Tavily-Backed) - 2026-04-03

## Goal
Route Home ticker background text through the agentic research path so company/background/recent-news text is evidence-led (including Tavily-backed web research) instead of deterministic template text.

## Problem
`resolve_attention_ticker_background()` previously returned only materialized ticker-background snapshots. Those snapshots were generated from deterministic helper text (`build_company_description` + `summarize_recent_news`), which produced verbose but low-information text.

## Design
1. Keep existing background snapshot contract for UI compatibility.
2. Source symbol-level research from `resolve_attention_research_bundle("symbol::<TICKER>")`.
3. When symbol bundle is absent, build a direct symbol bundle on demand using agentic artifacts + web research.
4. Overlay ticker-background payload fields (`description_text`, `news_summary_lines`, `recent_headlines`, `source_trace`) from the symbol bundle.
5. Preserve materialized-only mode behavior.

## Architecture Changes
- `DataAccessLayer._resolve_symbol_agentic_bundle(symbol, force_refresh)`
  - New direct single-symbol agentic builder.
  - Pulls:
    - web-search news payload (Tavily-enabled path via `search_symbol_news_payload` call chain)
    - attention context payload
    - symbol mover row + bars + filings
  - Runs `build_bottom_up_attention_artifacts(..., research_limit=1)` and returns `symbol::<TICKER>` bundle.
- `resolve_attention_research_bundle()`
  - For symbol bundle IDs, now attempts direct single-symbol bundle generation before expensive multi-symbol live artifact fallback.
- `resolve_attention_ticker_background()`
  - Now overlays base materialized payload with symbol research bundle fields when available.
  - Continues to return the same payload shape expected by app rendering.

## Non-Goals
- No hardcoded company text per ticker.
- No UI-only string patches in `app.py`.
- No changes to prod deployment behavior.

## Verification
- Added regression test:
  - `test_resolve_attention_ticker_background_prefers_agentic_symbol_bundle`
- Re-ran targeted existing test:
  - `test_resolve_attention_research_bundle_rejects_stat_dump_materialized_payload`

Both pass.

## Follow-ups
1. Optionally persist overlaid agentic background snapshots in pipeline output to reduce on-demand synthesis latency.
2. Add observability fields for provider mix (explicit Tavily/SerpApi contribution counts) into `source_trace` for easier QA.

## 2026-04-03 Update (Relevance + Importance Gating)

### Problem Observed
- The symbol background UI still surfaced low-value insider/dividend-equivalent headlines and generic baseline text even with the agentic path enabled.
- Same-day caches preserved noisy payloads after filtering code changes.

### Additional Source-Level Changes
- `services/attention_agentic.py`
  - Added irrelevance filtering for insider/director/dividend-equivalent noise across:
    - web-search news payload ingestion
    - candidate context document assembly
    - search-result document conversion
  - Added provider lineage into news/search docs (`provider`, `search_provider`) for downstream source labeling.
  - Added claim-derived per-document importance metadata:
    - `importance_score`
    - `importance_label`
    - `is_important`
  - Added `important_news_count` to symbol bundles.

### Additional Overlay Changes
- `data_access/layer.py`
  - Background overlay now only surfaces `news/search` evidence items (not generic context/filing/macro snippets).
  - Background headlines now carry provider attribution in source labels, e.g. `"... (via Tavily)"`.
  - If no important relevant news survives filtering, overlay emits a single explicit message:
    - `No relevant catalyst found ... in the latest agentic run.`
  - Description text is tightened to concise high-signal fields; avoids dumping long low-information background strings.
  - Removed fallback from symbol agentic builder to generic `resolve_recent_news()` so Tavily/web path remains authoritative.
  - Bumped cache versions for:
    - `web_search_news` payload cache (`version=2`)
    - `attention_symbol_agentic_bundle` cache (`version=2`)
    - This forces same-day cache invalidation for the new relevance behavior.

### Verification
- `pytest -q streamlit_alpaca_app/tests/test_data_access_query_service.py` -> 28 passed.
- Added/updated regression expectations for:
  - Tavily provider attribution in ticker-background recent headlines.
  - Explicit no-relevant-catalyst message when only irrelevant/low-importance items exist.
- `pytest -q streamlit_alpaca_app/tests/test_services.py -k "...live_attention_research_bundle..."` -> 3 passed.

## 2026-04-03 Update (LLM Search Router: Serp -> Tavily RAG Fallback)

### Requested Behavior
- If SerpApi does not return relevant evidence for the symbol move, use Tavily RAG-style retrieval to recover better context.
- Route this through the agentic layer so an LLM can judge whether to escalate and which Tavily topic to use.

### Implementation
- Added `SEARCH_ROUTER_SCHEMA` and LLM router helper in `services/attention_agentic.py`:
  - `_llm_tavily_route_decision(...)`
  - Produces structured routing JSON: `use_tavily`, `tavily_topic`, `reason`.
- Added shared relevance gate:
  - `_search_result_is_relevant(...)`
  - Reused across symbol-news payload and query-result collection.
- Updated `_search_query_results(...)`:
  - SerpApi runs first.
  - If Serp relevance is empty, LLM router determines Tavily fallback topic (`general`/`news`) and Tavily is called as `rag_fallback`.
  - Request rows now include routing metadata (`route_mode`, `route_reason`, `topic`).
- Updated `search_symbol_news_payload(...)`:
  - SerpApi first.
  - Tavily runs only when Serp is unavailable or no relevant Serp evidence passes gating.
  - For Serp-noise/no-result cases, Tavily fallback path uses router-informed topic selection.
- Updated `DataAccessLayer._resolve_web_search_news(...)`:
  - Passes `llm_client=load_llm_client()` into `search_symbol_news_payload`.
  - Bumped web-search cache version to `3`.
- Bumped symbol agentic bundle cache version to `3` to invalidate stale same-day payloads.

### Additional Verification
- `pytest -q streamlit_alpaca_app/tests/test_services.py -k "search_query_results_uses_tavily_rag_fallback_when_serp_is_irrelevant or search_query_results_skips_tavily_when_serp_is_relevant"` -> passing.
- Added coverage for both routing branches:
  1. Serp irrelevance triggers Tavily `topic=general` fallback.
  2. Relevant Serp result suppresses Tavily fallback call.

## 2026-04-03 Update (UI Simplification: 3-Section Output)

### Problem
- Company/ticker background views still emitted multiple verbose blocks (`Recent News Snapshot`, `Primary-Source Narrative`, related DB news, expanded headline dumps), which made quick decision-making difficult.

### Change
- Added compact renderer in `app.py`:
  - `Background - LLM Summary`
  - `What Happened - LLM Summary`
  - `Evidence - Links`
- Removed noisy duplicate narrative blocks from:
  - Home ticker background panel (`_render_home_ticker_background_panel`)
  - Market Opportunity ticker overview path
- Standardized fallback text when no catalyst is found:
  - `No relevant catalyst found in web coverage for <TICKER> in the latest agentic run.`
- Kept evidence output link-first with source/date metadata and deduping.

### Validation + Deploy
- `python -m py_compile streamlit_alpaca_app/app.py` passed.
- `pytest -q streamlit_alpaca_app/tests/test_data_access_query_service.py -k "resolve_attention_ticker_background"` -> 2 passed.
- Deployed to dev:
  - revision: `sn-streamlit-ui-dev--0000124`
  - image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:6a5dd92a2a88eebb8c83615a7a78b2ab620270d9de9447b17b5fe3bf5bcf2304`
  - smoke: HTTP 200

## 2026-04-03 Update (LLM-Driven Gating + Serp AI Overview + Header Cleanup)

### What Changed
- UI section labels were simplified per request:
  - `Background`
  - `What Happened`
  - `Evidence`
  - Removed explicit `LLM` wording from titles.
- Search relevance gating is now LLM-driven (schema-based) instead of primarily heuristic:
  - Added `SEARCH_RELEVANCE_SCHEMA` and `_llm_search_relevance_flags(...)`.
  - Heuristic gating remains only as fallback when LLM is unavailable or fails.
- Router now supports LLM-selected Tavily query text:
  - `SEARCH_ROUTER_SCHEMA` now includes `tavily_query`.
  - `_llm_tavily_route_decision(...)` returns `(use_tavily, topic, tavily_query, reason)`.
- SerpApi AI Overview path added:
  - `SerpAPISearchClient.search_ai_overview(...)` in `services/web_research.py`.
  - Included in symbol-news candidate set for LLM gating when available.
- Importance gate was loosened slightly to reduce false negatives on legitimate company developments:
  - base threshold lowered from `0.58` to `0.50`
  - authority-based inclusion path added for high-authority (`authority_rank <= 1`) evidence.
- Cache versions bumped to invalidate stale same-day payloads:
  - `web_search_news`: `version=4`
  - `attention_symbol_agentic_bundle`: `version=4`

### Verification
- `python -m py_compile` for updated app/search/layer modules passed.
- `pytest -q streamlit_alpaca_app/tests/test_services.py -k "search_query_results_uses_tavily_rag_fallback_when_serp_is_irrelevant or search_query_results_skips_tavily_when_serp_is_relevant or build_live_attention_research_bundle_prefers_same_day_news_and_separates_background_context or build_live_attention_research_bundle_filters_irrelevant_roundups_and_marks_unresolved or build_live_attention_research_bundle_emits_tight_display_excerpts"` -> 5 passed.
- `pytest -q streamlit_alpaca_app/tests/test_data_access_query_service.py` -> 28 passed.

## 2026-04-03 Update (Pre-Deploy Live Ticker QA + Router/Fallback Tightening)

### Why
- Pre-deploy live checks were requested across small-cap names to confirm relevance quality before shipping.
- During QA, `source=serpapi` with `rows=0` exposed a corner case where the LLM router could skip Tavily even when Serp had no relevant items.

### Code Changes
- `services/attention_agentic.py`
  - Refined LLM relevance prompt to down-rank low-signal items:
    - insider/form-4/dividend-equivalent noise
    - routine ex-dividend updates
    - isolated analyst target-only notes
    - generic stock up/down recaps without a concrete catalyst
  - Refined router prompt to prefer Tavily when Serp evidence is sparse/low-signal.
  - Router decision is now consulted whenever both Serp and Tavily are available (not only when Serp relevance is zero).
  - Added deterministic safety guard to satisfy product policy:
    - if Serp relevant count is zero, force Tavily fallback on.
- `tests/test_web_research.py`
  - Added Serp AI Overview parsing tests:
    - parses `ai_overview` into normalized `WebSearchResult`
    - returns `None` when overview payload is absent

### Live QA Results (real credentials)
- Verified live symbol-news retrieval with LLM gating on:
  - `APLS`, `IRDM`, `CABA`, `APTO`, `BMY`
- Output now favors concrete catalysts over noisy insider chatter.
- `BMY` surfaced pipeline/clinical update headlines in top results during QA.

### Environment Note
- Tavily key in the current environment returns:
  - `status=432` usage-limit exceeded
- Behavior is graceful:
  - no provider error text dumped into the UI
  - fallback no-catalyst messaging remains concise when no relevant evidence is available

### Verification
- `python -m py_compile services/attention_agentic.py services/web_research.py tests/test_web_research.py` passed.
- `pytest -q tests/test_web_research.py tests/test_services.py -k "search_query_results_uses_tavily_rag_fallback_when_serp_is_irrelevant or search_query_results_skips_tavily_when_serp_is_relevant or search_ai_overview"` -> 4 passed.
- `pytest -q tests/test_services.py -k "search_query_results_uses_tavily_rag_fallback_when_serp_is_irrelevant or search_query_results_skips_tavily_when_serp_is_relevant"` -> 2 passed.

## 2026-04-03 Update (VSAT No-Catalyst Root Cause + Infra Fix)

### Root Cause
- Dev UI app uses Key Vault `spectral-nature-kvault`.
- That vault was missing:
  - `serpapi-api-key`
  - `tavily-api-key`
  - `azure-openai-api-key`
- Result: provider clients and LLM gating were unavailable in the running app process, causing repeated no-catalyst fallbacks for symbols like `VSAT`.

### Fix Applied
- Synced missing secrets from `snpipelinekv03130136` into `spectral-nature-kvault`:
  - `serpapi-api-key`
  - `tavily-api-key`
  - `azure-openai-api-key`
- Bumped cache versions to invalidate stale no-result payloads:
  - `DataAccessLayer._resolve_web_search_news` -> `version=5`
  - `DataAccessLayer._resolve_symbol_agentic_bundle` -> `version=5`

### Post-Fix Sanity
- Config load check against `spectral-nature-kvault` now reports:
  - `serp_cfg=True`
  - `tav_cfg=True`
  - `llm_cfg=True`

## 2026-04-03 Update (Company Background Quality)

### Problem
- Background text could degrade into template text like:
  - `... is being tracked here as an individual company narrative inside the market dashboard.`
- Company names also leaked listing suffixes (`Class A Common Stock`) into background text.

### Changes
- `services/company.py`
  - Added company-name normalization to strip listing instrument suffixes.
  - Replaced the template fallback phrase with:
    - Wikipedia summary fallback (short extract) when available.
    - Neutral company fallback (`<name> (<symbol>) is a publicly traded company.`) when no summary is available.
- `services/attention_ticker_snapshots.py`
  - Added `company_background_text` field to ticker background snapshot rows.
- `data_access/layer.py`
  - Added runtime guard to detect and replace legacy template background text.
  - Preserves `company_background_text` during symbol-bundle overlay so `Background` can stay company-focused even when `What Happened` is catalyst-focused.
- `app.py`
  - `Background` now prefers: `llm_summary` -> `context_story` -> `company_background_text` -> `description`.
  - `What Happened` now prefers: `llm_headline` -> first summary line -> `description`.

### Verification
- `pytest -q tests/test_services.py -k "build_company_description_"` -> 4 passed.
- `pytest -q tests/test_data_access_query_service.py -k "resolve_attention_ticker_background or resolve_attention_research_bundle"` -> 7 passed.

## 2026-04-03 Update (VSAT Evidence Drop Fix)

### Problem
- `VSAT` still showed no-catalyst fallback even after provider secrets were restored.
- Root mechanism: many web rows had empty snippets, and evidence chunking dropped those rows because `raw_text` was empty.

### Code Fix
- Added `_evidence_text(snippet, headline)` fallback in `services/attention_agentic.py` so headline text is used when snippets are missing.
- Applied this fallback in:
  - web news payload rows (`summary`/`description`)
  - candidate news document assembly (`raw_text`)
  - search-result document assembly (`raw_text`)
- This preserves valid headline-only evidence for claim extraction and importance scoring.

### Validation
- `python -m py_compile services/attention_agentic.py` passed.
- `pytest -q tests/test_services.py -k "search_query_results_uses_tavily_rag_fallback_when_serp_is_irrelevant or search_query_results_skips_tavily_when_serp_is_relevant"` -> 2 passed.
- End-to-end on-demand query:
  - `attention_ticker_background(ticker=VSAT, force_refresh=true)`
  - now returns non-empty evidence with `important_news_count=1`, `relevant_news_count=1`.

### Deploy
- Dev UI deployed after fix:
  - revision `sn-streamlit-ui-dev--0000128`
  - image `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:805f634f64dcae4dc136eda95ed194f2ca7715f544341c4fc8fb22cc188a3d81`
  - smoke check HTTP 200

## 2026-04-03 Update (Default Dashboard Path Fix: Materialized vs On-Demand Bundle Selection)

### Root Cause (Actual Dashboard End State)
- `force_refresh=false` (default dashboard path) was resolving symbol bundles from materialized snapshots first.
- For names like `BMY`/`VSAT`, those snapshots had weak SEC-only context and no meaningful web catalyst, which produced:
  - `No relevant catalyst found ...`
  - empty evidence links
- `force_refresh=true` looked correct because it exercised on-demand symbol bundle resolution.

### Source Fix
- Updated `DataAccessLayer.resolve_attention_research_bundle(...)` to avoid blindly trusting materialized symbol bundles when web catalyst signal is missing.
- Added `_bundle_web_signal_score(...)` and used it to route symbol bundles:
  - keep materialized when it has meaningful web signal
  - otherwise resolve on-demand symbol bundle and prefer it when stronger (or on force refresh)
- This keeps behavior generic and non-hardcoded while honoring LLM-driven importance signals already in bundle fields.

### Tests Added
- `test_resolve_attention_research_bundle_symbol_prefers_on_demand_when_materialized_has_no_web_signal`
- `test_resolve_attention_research_bundle_symbol_keeps_materialized_when_web_signal_exists`

### Validation
- Local (with app Key Vault env):
  - `resolve_attention_ticker_background(BMY, force_refresh=false)` -> non-fallback, non-empty evidence.
  - `resolve_attention_ticker_background(VSAT, force_refresh=false)` -> non-fallback, non-empty evidence.
- Live dev container (revision `0000130`) via `az containerapp exec`:
  - `BMY`, `force_refresh=false`: `fallback=False`, `recent=1`
  - `VSAT`, `force_refresh=false`: `fallback=False`, `recent=1`

### Deploy
- Dev UI deployed:
  - revision `sn-streamlit-ui-dev--0000130`
  - image `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:38a6438668239992c8fc311c592349c80740726ee3005c27e4a4f3e8c2f4509a`
  - smoke check HTTP 200

## 2026-04-03 Update (Precomputed-Only Click Path)

### Request
- Serve ticker background content from precomputed materialized datasets on click.
- Avoid live web/LLM calls in normal click path.

### Code Change
- Added precomputed-only switch for symbol bundles in `DataAccessLayer.resolve_attention_research_bundle(...)`:
  - new helper: `_precomputed_symbol_bundles_only()` (default `true`)
  - env override: `ATTENTION_SYMBOL_BUNDLE_PRECOMPUTED_ONLY=false` to re-enable live fallback for non-refresh reads
  - when precomputed-only is on and `force_refresh=false`, symbol bundle resolution returns materialized bundle (or empty) and skips `_resolve_symbol_agentic_bundle(...)`

### Validation
- Unit tests:
  - `test_resolve_attention_research_bundle_symbol_precomputed_only_skips_on_demand`
  - existing symbol-bundle routing tests still pass
- Dev runtime timing (`force_refresh=false`) after deploy:
  - precomputed read now returns in seconds (no long on-demand path)

### Deploy
- Dev UI deployed:
  - revision `sn-streamlit-ui-dev--0000131`
  - image `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:cbce5827ed745be80bbbf2b4789fdba4715fe43e5c76637615627ef97edeb79a`
  - smoke check HTTP 200

### Operational Follow-Up
- Manually triggered `attention-home-build` execution `attention-home-build-y7suuqy` to refresh precomputed bundle/text coverage immediately, instead of waiting for the next cron window.
