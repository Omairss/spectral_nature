# Attention Feed Business-First Live Trail - 2026-05-27

## Original Goal

The attention/news stack should read like a business analyst, not a technical/statistical mover recap.

The system should use Zopedia/AQL to connect live news and market moves to what the company sells, demand, customers, peers, fundamentals, employee/workforce signals, web attention, global trends, policy, rates, liquidity, and related first/second-degree businesses. It should not hardcode prose, blacklist phrases, or fill Home with "no clear catalyst" style absence text.

## Current Direction

Keep one shared agent spine:

- AQL finds and organizes current evidence for attention candidates.
- Zopedia/AQL enriches tickers/events with business context.
- A final Zopedia/AQL public-surface review edits the Home payload before persistence.
- UI renders materialized rows only; debug traces stay behind admin/debug surfaces.

## Changes Deployed To Dev Pipeline

Latest pipeline image:

- `snpipelineacr03130136.azurecr.io/pipeline-jobs:20260527005120`
- Digest: `sha256:5cd30b21ec1655c8a42bb245d3d0e5b57e5c8456f7956118e119eece58129402`

Latest dev UI image:

- `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:bb706be51826262b6365366b8a865c5ee266fb1ba1ffcda1451e52d2de184fb9`
- Dev revision: `sn-streamlit-ui-dev--0000388`
- Smoke check: HTTP 200

Important runtime knobs now live on `attention-home-build`:

- `ATTENTION_HOME_SEARCH_BACKFILL_MAX_WORKERS=8`
- `AQL_CANDIDATE_RESEARCH_MAX_WORKERS=4`
- `ATTENTION_HOME_ZOPEDIA_ENRICHMENT_MAX_WORKERS=8`
- `PAGE_AGENTIC_SUMMARY_MAX_WORKERS=4`
- `ATTENTION_HOME_SURFACE_QUALITY_TIMEOUT_SECONDS=300`

## What Was Fixed At Source

- Search backfill is no longer a silent sequential loop.
- AQL candidate research runs independent candidates concurrently.
- Ticker Zopedia enrichment runs concurrently with a longer background budget.
- Home serialization now waits until after the public Home surface is semantically reviewed by Zopedia/AQL.
- The reviewer rewrites or drops public Home items when the draft says absence instead of analysis.
- The reviewer owns every display field: title/headline, what changed, why, what else moved, affected assets, business context, surface summary, and bundle copies.
- Natural-language review notes are no longer serialized into the public payload; review state is stored as booleans/enums/counts.
- The reviewer is semantic, not a phrase blacklist. It receives the current item text, bundle text, source summary, business context, and Zopedia enrichment.

## Targeted Probe Before Full Job

Using the previous run artifact, the new public-surface review saw 9 Home items.

Important result:

- Before: `Space Industrials Surge on Contract Wins and Sector Optimism; LUNR Declines Without Clear Catalyst`
- After targeted review: `Space Industrials Rally; LUNR Declines Amid Sector Divergence`
- It cleared the unsupported why text and added a watch-next evidence slot.

The first probe still preserved the bad phrase, so the prompt/apply contract was tightened. The second probe removed it. This confirmed the fix is agent-native and not a renderer mask.

## Fresh Run Evaluated

Execution:

- `attention-home-build-f4u1mhl`
- Run id: `dc8f5b06-ec39-40a9-b892-6197758ada5c`
- Materialized as-of: `2026-05-27T06:41:15Z`

Observed checkpoints:

- Inputs loaded: 999 movers, 73-symbol shortlist.
- Search backfill: 36/36 completed with 8 workers.
- AQL candidate research: 12 candidates completed with 4 workers.
- Event bundles completed.
- Macro verification completed.
- Business context attached for 9 symbols.
- Public Home surface review: reviewed 8, changed 7, dropped 1.

## Qualitative Result

Accepted direction, with one source-level fix applied after inspection.

Good examples from the evaluated payload:

- Space: changed from a vague sector rally into `Space stocks rally on NASA/IPO anticipation; LUNR lags`, tying the move to the NASA event and SpaceX IPO speculation.
- Semiconductors: Micron's UBS upgrade and AI-memory demand now explain why power management, memory, and packaging peers moved together.
- Data centers: Modine is tied to Airedale cooling products and a $4B capacity agreement, with VIAV described as a sympathy move in data-center infrastructure.

Problems found during artifact-wide inspection:

- `affected_assets_summary_text` still carried absence prose for one energy cluster.
- `public_surface_review_note` stored an internal unresolved-cause sentence in the public payload.
- Debug-only quality review JSON can still quote rejected bad sentences; that is acceptable only while it stays out of public rows.

Fix after inspection:

- Expanded the Zopedia/AQL public-surface schema to include affected-assets, business-context, surface-summary, and cause-status fields.
- The apply step now clears stale `surface_*` and review-note fields and writes only public-safe display text.
- Targeted replay of the latest Home payload produced clean public text: no `no clear catalyst`, `no clear driver`, raw tool dump, evidence dump, stale Iran/Qatar/Hormuz, or unresolved-cause body prose in the reviewed Home payload.

## Current Judgment

The implementation is now on track with the original ask for the Home attention surface: business-first, Zopedia/AQL-native, no deterministic fallback prose, no phrase-blacklist product fix, no public raw tool dumps, and no stale-news current driver. The next scheduled `attention-home-build` run will publish the stronger post-inspection contract from image `20260527005120`.

## Homepage Load Detour - 2026-05-27

User report:

- Home spent a long time on `Loading today's narrative home`.

Root cause:

- The deployed UI logs showed `AQL candidate research starting candidates=40 workers=4 timeout=90s` from the Streamlit process after selecting Home.
- `resolve_attention_home_1d()` could reject a materialized snapshot for legacy/stat-dump text and then fall through to `_resolve_live_attention_artifacts()`.
- That made a precomputed Home page start live AQL research during render.

Fix:

- `resolve_attention_home_1d()` now returns materialized state only.
- If the materialized Home snapshot is missing or rejected by the quality gate, it returns an empty materialized payload with a warning.
- It no longer calls `_resolve_live_attention_artifacts()` from the Home render path.

Verification:

- Targeted deterministic check: `pytest streamlit_alpaca_app/tests/test_data_access_query_service.py -k attention_home_1d -q` passed.
- The UI must be redeployed for this fix because the bug is in the Streamlit/data-access image, not the scheduled pipeline image.

## Sidebar Collapse Trap - 2026-05-27

User report:

- After collapsing the side navigation, there was no reliable way to open it again.

Decision:

- First attempt made the desktop sidebar fixed. That was wrong because it fought Streamlit's layout shell and distorted the page.
- Corrected decision: keep Streamlit's native sidebar collapse/reopen controls visible and start expanded by default.

Fix:

- `st.set_page_config()` now starts with `initial_sidebar_state="expanded"`.
- The force-open sidebar CSS was removed.
- Native Streamlit controls remain available, so a collapsed sidebar can be reopened without reversing or distorting the layout.

Verification:

- `python3 -m py_compile streamlit_alpaca_app/app.py` passed.

Correction after live review:

- The first Home v3 performance fix reintroduced the wrong selected-detail pattern. It placed selected research/details outside the group flow, which made the page resemble the old right-rail layout.
- Home v3 now keeps research and ticker details nested inline inside the opened narrative group.
- The click path no longer calls the attention-bundle resolver or ticker-background loader. It reveals precomputed job artifacts: bundle snapshots, ticker background snapshots, and Zopedia enrichment rows.
- Native Streamlit sidebar controls remain visible; the force-open sidebar CSS was removed.

## Stale Route Deletion - 2026-05-27 21:09 UTC

User report:

- Dev still exposed the old layout and the sidebar reopen control was not visible.

Fix:

- Public unauthenticated Home now uses the same Home v3 renderer as authenticated Home.
- `Home v2` and `Experiment` were removed from admin navigation and runtime route branches.
- Old right-rail Home renderers, the old Home ticker-background panel, and old v2/experiment selection handlers were removed from `app.py`.
- Streamlit header controls are no longer hidden, so the native sidebar reopen button can render.
- Added `documents/depricated.md` to track remaining compatibility names and stale paths.

Verification:

- `python3 -m py_compile streamlit_alpaca_app/app.py streamlit_alpaca_app/views/_shared.py` passed.

## Stale Helper Deletion - 2026-05-27 21:19 UTC

User report:

- Old Home naming still created confusion after the route deletion.

Fix:

- Deleted `services/homepage_v2.py`.
- Moved the two still-used shared helpers into `services/homepage_support.py`.
- Deleted unused Home v2 market-digest/detail-state helpers and their tests.
- Deleted stale private Home v2 presentation helpers from `presentation/attention_content.py`.
- Deleted the stale `HOMEPAGE_V2_RAIL_REFACTOR_PLAN.md` doc because it described the rejected right-rail layout.
- Updated `documents/depricated.md`, `learnings.md`, and `mistakes.md`.

Verification:

- `python3 -m py_compile streamlit_alpaca_app/app.py streamlit_alpaca_app/views/_shared.py streamlit_alpaca_app/views/experiments.py streamlit_alpaca_app/services/homepage_support.py streamlit_alpaca_app/presentation/attention_content.py` passed.
- `streamlit_alpaca_app/.venv/bin/python -m pytest streamlit_alpaca_app/tests/test_services.py -k "homepage_bundle_symbol_lookup or homepage_editorial_links" streamlit_alpaca_app/tests/test_presentation_attention_content.py streamlit_alpaca_app/tests/test_auth_store.py -q` passed for the selected helper tests.
- Deployed to dev as `sn-streamlit-ui-dev--0000400`.
- Active dev image is `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:f1bb70c442dd54e5e894e61dc117d69f29a58253e5580fc8a97488bbdfdfbd21` with 100% traffic.
- Dev root returned HTTP 200.

## Permanent Compact Sidebar And Home Load Tax - 2026-05-27 23:xx UTC

User report:

- The left pane disappeared after collapse in prod, then the forced permanent pane was too wide.
- Home showed a long `Loading today's narrative home` spinner even though the page is precomputed.

Fix:

- Sidebar is now permanent and compact at `18rem`, not a broad `25rem` content column.
- Local screenshot confirmed the compact rail is visible and Home content is not squeezed.
- The Home delay was traced to dataset metadata discovery, not JSON deserialization or live AQL research: cached frame read/deserialization was ~1ms, while metadata lookup could spend seconds listing blob manifests.
- `latest_dataset_metadata()` now uses Postgres metadata first when available.
- Blob fallback now checks `manifests/{dataset}/latest.json` before listing the manifest directory.
- Pipeline manifest uploads now also write that stable `latest.json` pointer for future runs.
- Metadata cache default increased from 30s to 300s to avoid repeated metadata round trips during normal Streamlit reruns.

Verification:

- `python3 -m py_compile streamlit_alpaca_app/app.py streamlit_alpaca_app/services/pipeline_store.py` passed.
- Targeted checks passed: `test_latest_dataset_metadata_prefers_db_without_blob_listing`, `test_latest_dataset_metadata_uses_stable_latest_manifest_before_listing`, and `test_upload_manifest_writes_stable_latest_pointer`.
- UI deployed to dev as `sn-streamlit-ui-dev--0000403`, then promoted to prod as `sn-streamlit-ui--0000076`.
- Prod UI image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:aeb68c432dee85cb1fdd3fddb17f81aa40869afc5614144d74b34eb76e7a74d9`.
- Prod screenshot confirmed the compact permanent sidebar and rendered Home V3.
- Pipeline image deployed to scheduled jobs as `snpipelineacr03130136.azurecr.io/pipeline-jobs:20260527165056`.

## Sparse Group Summaries And Missing Company Background Research - 2026-05-28 00:xx UTC

User report:

- Home was only showing two group attention summaries.
- Company Background did not show the organized Zopedia/AQL research.

Findings:

- Latest saved Home payload had exactly `top_events=2`, `must_read_movers=0`, and `unresolved_large_moves=1`, despite `event_candidates_1d=70`.
- The public-surface review saw only six public items, changed three, dropped three, and had no replacement pool on the rendered surface.
- Latest `attention_ticker_zopedia_enrichments` metadata said row_count=16 and the blob had 16 rows, but a poisoned local empty cache made the reader return zero rows until the cache path was invalidated.
- `attention_ticker_background_snapshots` did not consume `attention_ticker_zopedia_enrichments`, so Company Background stayed generic even when Zopedia enrichment existed for a ticker.
- The ticker enrichment critic rejected allowed watch-next-only partial answers, which erased useful research rows and made the surface look emptier than the acquisition work.

Fix:

- `load_latest_dataset_frame()` and `load_dataset_frame_asof()` now invalidate empty local caches when metadata says the dataset version has rows, and they do not rewrite empty caches for non-empty metadata after a failed parquet read.
- `attention_ticker_zopedia_enrichments` is now registered as an Attention dataset.
- `build_attention_ticker_background_snapshot_frame()` now accepts the Zopedia enrichment frame, carries completed enrichment text into `company_background_text`, `context_story_text`, source trace, confidence, status, and limitations.
- Home V3 background rendering now prefers the materialized Zopedia enrichment text when the background row carries it, and avoids rendering the same enrichment twice.
- The public Home quality gate now treats a concrete `watch_next_text` as useful context for partial top-event cards instead of dropping the item solely because the why field was cleared.
- The ticker-enrichment critic now allows one concise What To Watch sentence when it names the next concrete evidence target without absence prose.

Verification:

- `python -m py_compile` passed for `pipeline_store.py`, `attention_ticker_snapshots.py`, `attention_home_build.py`, and `app.py`.
- Latest Zopedia enrichment read now returns 16 rows instead of the poisoned empty cache.
- A targeted background replay for IREN/CLSK carries Zopedia enrichment into `company_background_text` and source trace.
- Focused tests passed: `test_latest_dataset_metadata_prefers_db_without_blob_listing`, `test_upload_manifest_writes_stable_latest_pointer`, and `test_build_attention_ticker_background_snapshot_frame_serializes_replay_fields`.

## Public Surface Over-Pruning - 2026-05-28 03:xx UTC

User report:

- Relevant stories still failed to make it to Home, and the page could look sparse even after search backfill found coverage.

Findings:

- The live `attention-home-build-ppwkfi5` run found search-backed news for all 63 shortlisted symbols.
- Zopedia ticker enrichment completed only 6 of 16 rows, but the larger failure was later in the public Home review.
- The public-surface review saw 14 items and returned `reviewed=14 changed=5 dropped=9`, leaving `top_events=0`, `must_read=0`, and `unresolved=5`.
- That run was stopped before it could replace the latest saved Home snapshot.
- The root design error was treating "not supported enough for top/must-read" as "delete this item." For a Home feed, useful observed moves with business, sector, peer, macro, source, or watch-next value should be routed, not erased.

Fix:

- Strengthened the public-surface prompt so the LLM reviewer keeps useful partial/unresolved items publishable instead of setting `publish=false` just because the driver is thin.
- Changed `_apply_home_surface_quality_items()` so `publish=false` from the reviewer is not blindly destructive.
- Useful observed moves now route into `unresolved_large_moves` with unsupported why fields cleared.
- Items are only dropped when they have no useful observed move, business context, source context, sector/peer/macro context, or watch-next value.
- Verified the source behavior with a targeted synthetic item: an APLD-like useful move marked `publish=false` now becomes one unresolved Home item instead of disappearing.
- Deployed patched pipeline image `snpipelineacr03130136.azurecr.io/pipeline-jobs:20260527195229`.
- Started fresh Home build execution `attention-home-build-mnhw2o3` for qualitative verification.

## Must-Read Candidate Starvation - 2026-05-28 05:xx UTC

User report:

- Relevant items still were not making it to the homepage.
- The page could show only a small number of group summaries even though the job had many candidates and search results.

Findings:

- `attention-home-build-mnhw2o3` succeeded but wrote a sparse public payload: `top_events=2`, `must_read_movers=0`, `unresolved_large_moves=5`, `event_candidates_1d=63`.
- A later run on the over-pruning fix had better search and research coverage, but logs still showed `top_events=1`, `must_read=0`, `unresolved=5` before persistence.
- Root cause was in `_build_home_payload()`: the wide top-event review pool marked all symbols in those draft group events as absorbed before individual mover cards were built.
- When public review later demoted or rejected group events, there was no individual replacement pool left for must-read.
- Generic fallback company background also still existed in `build_company_description()`, which could turn failed enrichment into fake background copy.

Fix:

- Removed the early absorbed-symbol skip for individual candidate cards.
- Let individual movers survive alongside top-event candidates until after public review and final display caps.
- Expanded must-read eligibility to keep supported, continuation, or partial candidates with same-day/source/evidence context.
- Removed the generic "publicly traded company/current narrative thin" fallback from company background construction. Empty background is now allowed when no substantive source-backed context exists.
- Deployed pipeline image `snpipelineacr03130136.azurecr.io/pipeline-jobs:20260527212343`.
- Ran focused Home execution `attention-home-build-8zslesn`; it succeeded.

Verification:

- Targeted synthetic `_build_home_payload()` replay with a group event plus two individual movers produced `top_events=1`, `must_read_movers=1`, `unresolved_large_moves=1`.
- Focused job logs showed search-backed news hits for `63/63` shortlisted symbols.
- AQL candidate research completed for `12/12` symbols.
- Persisted Home snapshot `attention_home_snapshots_1d__20260528T043654Z__ce5ce563` now has `top_events=3`, `must_read_movers=6`, `unresolved_large_moves=5`, `event_candidates_1d=63`, `entity_master=63`.
- Public surface review processed 17 items, changed 17, and dropped 0.
- Artifact scan found zero instances of the old bad public phrases: `no clear catalyst`, `no catalyst`, `no relevant business news`, `Company background is not available`, `current narrative is still thin`, `publicly traded company`, and `I collected tool output`.

Remaining gap:

- Ticker Zopedia enrichment quality is still weak in this run: `4/16` completed, with several failed or failed quality review rows.
- The Home feed now keeps relevant observed moves visible, but some unresolved cards still have generic titles like "moves" or "rises"; those should be improved by the shared AQL/Zopedia surface-writing contract, not by deterministic phrase patches.

## Home V3 Duplicate Plotly Key Fix - 2026-05-28 05:xx UTC

User report:

- Home V3 crashed with `StreamlitDuplicateElementId` from `_render_homepage_v3_materialized_ticker_background()` at the nested ticker price chart.
- After the chart key fix, Home V3 also crashed with `StreamlitDuplicateElementKey` from `_render_compact_background_sections()` because repeated evidence link buttons reused the same key.
- After the evidence-link fix, Home V3 crashed with `StreamlitDuplicateElementId` from `_render_overview_fundamentals()` because repeated ticker fundamentals rendered identical Plotly statement charts.

Finding:

- The materialized ticker background price chart was rendered without an explicit `key`.
- The same ticker/background chart can appear inside more than one group expander, producing identical autogenerated Plotly IDs.
- The compact background renderer generated evidence link keys from only the evidence index, label, and URL. If the same evidence appeared under the same ticker in two Home groups, Streamlit saw the same button key twice.
- The fundamentals renderer was shared and did not accept a caller key prefix, so repeated Home group ticker fundamentals could collide exactly like the background chart.
- I initially fixed each traceback too narrowly. The actual class is repeated Streamlit components inside nested materialized Home sections.

Fix:

- `_render_homepage_v3_materialized_ticker_background()` now requires a `key_prefix`.
- Home V3 passes a stable key prefix built from run token, bundle id/index, and ticker.
- The price chart uses that prefix for `st.plotly_chart(..., key=...)`.
- `_render_compact_background_sections()` now accepts a `key_prefix`.
- Home V3 passes the story/ticker prefix through to compact background evidence links; Stock Investigator passes a page-specific prefix.
- `_render_overview_fundamentals()` and `_render_fundamental_statement_charts()` now accept a `key_prefix`, and Home V3 passes run/bundle/ticker context through to each statement chart.
- Home V3 bundle panel prefixes now include run token plus beat index, not only bundle id.
- The Home graph banner and older repeated attention-card micro chart now have explicit Plotly keys.
- Every `st.plotly_chart` call in `app.py` and every module under `views/` now has an explicit `key`.

Verification:

- `python -m py_compile app.py views/_shared.py views/stock_investigator.py` passed.
- `python -m py_compile app.py views/_shared.py views/stock_investigator.py views/access_admin.py views/portfolio.py views/market_explorer.py views/option_strategizer.py views/experiments.py` passed.
- AST scan across `app.py` and `views/*.py` found zero `st.plotly_chart` calls without a `key`.
- UI dev deployed as `sn-streamlit-ui-dev--0000405`.
- Dev image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:8ffa194d7146292b3c4f09453ba605e11ad90f6eca4bbebbb8bb4ee662db4407`.
- Dev root HTTP status returned `200`.
- Evidence-link key fix deployed to dev as `sn-streamlit-ui-dev--0000406`.
- Dev image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:cfb81f77eacd3592960df5c6f40f28703e423c846e4096bff812a2a455e85265`.
- Dev root HTTP status returned `200`.
- Promoted the same digest to prod as `sn-streamlit-ui--0000077`.
- Prod root HTTP status returned `200`.
- Full audited Plotly-key fix deployed to dev as `sn-streamlit-ui-dev--0000408`.
- Dev image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:280576d130083b7f2a7b065d4e4c8d592e18445a9be4fdc46ccd96955b230238`.
- Dev root HTTP status returned `200`.
- Promoted the same digest to prod as `sn-streamlit-ui--0000078`.
- Prod root HTTP status returned `200`.

## Home Enrichment Coverage / No Fallback Fix - 2026-05-28

User report:

- Home V3 rendered low-information cards such as `UMC rises` and weak `REMX` research.
- UMC showed stale/background evidence as if it were useful research: old Newser/Taipei Times/StockTitan rows plus a stale StocksToTrade headline.
- The visible Company Background slot was not the organized Zopedia/AQL research the user asked for.

Findings:

- Latest persisted Home snapshot: `attention_home_snapshots_1d__20260528T043654Z__ce5ce563`.
- UMC was visible in top events and must-read movers, but its ticker enrichment row had `status=failed`.
- The UMC Zopedia thread did collect a plausible current lead, then failed at the final LLM JSON step: `LLM returned non-JSON content`.
- REMX was visible on Home, but ticker enrichment was only capped to the first 15 extracted story symbols while ticker backgrounds were materialized for 63 symbols. REMX did not get enrichment attached.
- `attention_ticker_background_snapshots` then filled `company_background_text` from deterministic description/news snippets when `zopedia_enrichment_text` was empty.

Fix:

- Raised default `ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LIMIT` from 15 to 80.
- Raised default `ATTENTION_HOME_ZOPEDIA_ENRICHMENT_MAX_WORKERS` from 8 to 16.
- Added `ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LLM_RETRIES`, default 2.
- Changed `_build_zopedia_enrichment_frame()` to use `collect_attention_ticker_symbols(payload, bundle_map)` so enrichment covers the same ticker universe that Home can render.
- Added bounded retry for retryable Zopedia LLM failures. If the same AQL/Zopedia contract still fails, enrichment is empty and failed.
- Added no hardcoded fallback: failed/missing enrichment no longer fills Company Background with deterministic description/news snippets.
- Attached successful ticker enrichment back into the Home payload and bundle map before the public surface review, so semantic review and routing can use the organized Zopedia text.
- Tightened current-driver gating: source count, old background links, and undated/stale claims no longer count as a supported current driver.

Targeted verification:

- `py_compile` passed for `pipeline/jobs/attention_home_build.py`, `services/attention_ticker_snapshots.py`, and `app.py`.
- Latest payload replay with new symbol collection returned 63 enrichment candidates under the default limit of 80.
- UMC and REMX are both in the enrichment candidate set.
- Replay confirmed failed/absent enrichment does not get converted into public background prose.
- Follow-up renderer audit found that old saved rows could still contain pre-fix fallback text in `company_background_text`.
- Home V3 ticker background now only renders background/evidence when Zopedia enrichment is actually attached through `zopedia_enrichment_text` or the source trace. Old fallback-only rows are treated as empty.

Deployment:

- Pipeline deployed with `ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LIMIT=80`, `ATTENTION_HOME_ZOPEDIA_ENRICHMENT_MAX_WORKERS=16`, and `ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LLM_RETRIES=2`.
- Pipeline image: `snpipelineacr03130136.azurecr.io/pipeline-jobs@sha256:cd47002976c9dd2b6af012bb1456dab5e418185aa174fa2bcd23af52d95bae20`.
- UI dev deployed as `sn-streamlit-ui-dev--0000410`.
- UI dev image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:d010d62be8c5dc0078dbdbd4098c92a208ce8c3d851c22adac5c06e914dc11d3`.
- UI dev root HTTP status returned `200`.
- Initial UI prod promotion: `sn-streamlit-ui--0000079`, root HTTP `200`.
- Follow-up UI hardening deployed to dev as `sn-streamlit-ui-dev--0000411`.
- Follow-up UI image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:470dd2a837e86faa7e028dfcfcbb58bb358dab16f8d3bec00125de5d26fe4d47`.
- Promoted the same hardened UI image to prod as `sn-streamlit-ui--0000080`.
- Prod root HTTP status returned `200`.

## Zopedia Failure Taxonomy - 2026-05-28

Question:

- Why are Zopedia enrichment rows failing?

Cached-run scan:

- Local cached `attention_ticker_zopedia_enrichments` contains 15 runs / 240 rows.
- Failure-like rows: 94.
- Latest cached run `attention_ticker_zopedia_enrichments__20260528T043654Z__ce5ce563` had 12 failure-like rows out of 16.
- This latest run predates the deployed retry/limit/timeout fixes, but it identifies the failure classes.

Overlapping failure categories:

- `no_recent_evidence`: 34 rows.
- `wander_or_not_followed`: 30 rows.
- `timeout`: 22 rows.
- `tool_budget_exhausted`: 19 rows.
- `quality_reject_missing_driver`: 18 rows.
- `unsupported_claims`: 12 rows.
- `llm_empty`: 6 rows.
- `zopedia_index_gap`: 6 rows.
- `tool_bug`: 5 rows.
- `llm_non_json`: 3 rows.

Latest-run examples:

- `UMC`: Zopedia found a plausible Bloomberg/current-driver lead, then final LLM JSON failed. This is a harness/model-format failure; the deployed retry now retries this class twice and then discards if still bad.
- `RBRK`: `dataset.recent_news` threw `IndexError`, and the thread only had price move + generic articles. This is a tool bug plus weak recovery.
- `AEIS`: thread did not use Zopedia tools before budget got low. This is planner/trajectory weakness.
- `ONTO`, `OKTA`, `TTMI`, `AMBA`: searches returned empty/stale evidence or exhausted budget before a current driver was found.
- `S`: quality monitor rejected body copy that said the move was inferred from sector moves with no company-specific evidence. This rejection is correct; upstream evidence collection was weak.
- `GFS`, `VPG`: Zopedia search found no relevant retained pages or only broad macro pages. This is an index/recall gap, not a display problem.
- `__MARKET_SUMMARY__`: quality monitor rejected unsupported company-name expansions and unsupported sector claims. The summary context needs explicit entity names and completed ticker packages.

Conclusion:

- The product failure was not hardcoded text. It was failed/missed enrichment plus renderer fallback leakage.
- The remaining Zopedia failure source is upstream agent reliability: weak current evidence recall, too much serial wandering, low per-thread budget for hard tickers, some tool bugs, and final JSON instability.
- Quality review is doing the right thing by failing weak absence prose. The next design work should improve the AQL/Zopedia harness and evidence acquisition, not loosen the public copy standard.

## Root Fix Pass - 2026-05-28

Root causes fixed:

- Shared LLM JSON parsing now has one schema-bound repair pass. A malformed final answer is repaired by the model under the same JSON schema, not converted into fallback prose.
- Zopedia ticker enrichment still retries retryable LLM failures up to 2 times at the AQL/Zopedia contract boundary, then discards the row if it cannot produce a clean answer.
- `investigator.recent_news` now reads both bare DataFrames and wrapped materialized payloads like `{"articles": DataFrame}`. The tool no longer reports "No recent news" simply because the data was wrapped with provenance.
- Zopedia tool argument coercion now tolerates a recoverable planner encoding error where a list is supplied as `json_value` under `value_kind=object`. The schema-normalization layer can then coerce it correctly.
- Per-ticker background enrichment now defaults to 18 max tool calls instead of the shallow 10-call cap. This is still bounded, but it is a background-job budget rather than an interactive shortcut.
- Enrichment rows now write `failure_categories_json` so Admin/system health can roll up `llm_structured_output_failure`, `tool_contract_failure`, `timeout`, `tool_budget_exhausted`, `quality_rejected`, and `no_current_evidence`.

Targeted verification:

- `python3 -m py_compile` passed for the patched LLM adapter, agent tools, agent harness, and attention-home job.
- `pytest test_llm_deepseek.py test_agent_tools.py test_omnibar_agent.py -q` passed: 53 tests.
- Targeted pipeline materialization tests passed for Home output, news backfill, array-symbol payloads, and Zopedia market-summary audio: 4 tests.

Deployment state:

- Not yet redeployed after this root-fix pass at the time of this note. The next deploy should be a pipeline/dev-targeted deploy first, because the primary changes affect scheduled enrichment and shared Zopedia runtime behavior.

## Deep Root Fix Pass - 2026-05-28

User issue:

- Home and ticker detail showed stale/low-information background for UMC/REMX/APLD/MNTS.
- Zopedia enrichment failures were being converted into weak background copy or watch-only rows.
- The agent was seeing shallow current headlines but not opening the source before synthesis.

Root causes fixed:

- `DataAccessLayer.resolve_recent_news()` now uses materialized company identity even during web refresh, so weak search seeds recover from ticker-only input.
- Company names are normalized before search. Exchange suffixes such as `- Class A Common Stock` are stripped before connector queries.
- Current-news resolvers no longer return fallback summaries when there is no recent article row. Summary-only rows are not current evidence.
- `search_symbol_news_payload()` applies the same company-name cleanup internally, so callers outside `DataAccessLayer` get the same recall improvement.
- Ticker background snapshots no longer call deterministic `build_company_description()` when Zopedia enrichment is missing. Empty background is preferred over fake background.
- Bundle-to-background overlay no longer promotes headline/what-changed move recaps into the company background slot.
- `investigator.recent_news` now includes URLs and flags headline-only summaries so the shared agent knows to open sources before treating them as verified.
- Ticker-enrichment quality review now receives compact tool evidence/source links/provenance. The critic no longer rejects supported claims simply because the answer lacks inline citations.
- Failure taxonomy now recognizes restart loops, low-signal loops, budget depletion, and no-substantive-evidence states instead of writing `unknown_failure`.
- Confidence is capped from `high` to `medium` when the accepted enrichment itself records source-quality limitations such as unverified official filings or financial-blog-only support.

Targeted verification:

- `py_compile` passed for `data_access/layer.py`, `services/aql/collector.py`, `services/common/company_identity.py`, `services/agent_tools.py`, `services/attention_ticker_snapshots.py`, and `pipeline/jobs/attention_home_build.py`.
- Focused tests passed: recent-news identity refresh, summary-only suppression, materialized web-news fallback, ticker-background snapshot rendering, and recent-news tool payload handling.
- Broader targeted suite passed: 68 tests covering LLM JSON repair, agent tools, Omnibar/Zopedia argument repair, business-model stack fail-closed behavior, recent-news fallback, and ticker background rendering.

Live probes:

- `resolve_recent_news(MNTS, force_refresh=True)` returned 3 current SerpAPI rows from 2026-05-26/27.
- `resolve_recent_news(APLD, force_refresh=True)` returned a current MarketBeat row from 2026-05-22.
- `resolve_recent_news(UMC, force_refresh=True)` returned zero rows instead of stale Newser/Taipei Times/StockTitan filler.
- `resolve_recent_news(REMX, force_refresh=True)` returned zero rows instead of fallback prose.
- `research.open_page` successfully opened the MNTS current source and extracted business details: Q1 service revenue, Vigoride 7, government contracts, runway, debt cleanup, and Vigoride 8 watch item.
- MNTS enrichment after harness fixes completed with a substantive business story. It opened current source pages, tied the move to revenue/contracts plus space-sector peer rotation, and kept source-quality caveats in limitations.
- UMC enrichment failed closed with no answer text after the agent exhausted low-signal loops and found no substantive current evidence.

Deployment state:

- Not redeployed yet after this deep root-fix pass. Deploy dev pipeline and dev UI next because the fixes touch scheduled enrichment, shared data access, shared search, and UI-loaded ticker background code.

Dev deployment completed:

- Pipeline dev jobs rebuilt and updated to `snpipelineacr03130136.azurecr.io/pipeline-jobs:20260528025504`, digest `sha256:fa096f10789bc856e6cb79f4d3040f967b18b919630a5e552b48348f993072fd`.
- Dev UI rebuilt and updated to revision `sn-streamlit-ui-dev--0000413`, image digest `sha256:1ab57a0fa403659bc9bc17bb862f990cd065acde14a28c267d592c0356b33200`.
- Dev UI root smoke check returned HTTP 200.
- `attention-home-build` retains `LLM_PROVIDER=deepseek`, `LLM_MODEL=deepseek-reasoner`, `LLM_API_KEY_SECRET_NAME=deepseek-api-key`, `ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LIMIT=80`, `ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LLM_RETRIES=2`, `ATTENTION_SYMBOL_NEWS_MAX_AGE_DAYS=7`, and `ATTENTION_SYMBOL_NEWS_INCLUDE_UNDATED=0`.
- Post-deploy targeted data probe: MNTS returned 3 current web-search rows, APLD returned 1 current web-search row, UMC returned 0 rows, and REMX returned 0 rows. This preserves recall for current evidence without showing stale filler.

## Manual Refresh After DeepSeek Balance Top-Up - 2026-05-28

Context:

- At ~9:30 PDT, Home still looked stale because `news-ingest-and-features` had succeeded at 9:05 PDT, but `attention-home-build` failed at 9:20 PDT.
- Root failure from Log Analytics: DeepSeek `402 Insufficient Balance` during the Home public-surface quality review. Upstream news freshness was not enough because the materialized Home snapshot was not refreshed.

Manual rerun:

- Started execution `attention-home-build-ttzr2xt`, run id `a3583772-2262-462b-94f8-53f0f99f6edd`.
- Execution window: `2026-05-28T16:34:30Z` to `2026-05-28T17:26:24Z`.
- Status: `Succeeded`.
- Materialized snapshot as-of: `2026-05-28T16:34:59Z`.

What completed:

- Search-backed news backfill: 67 candidates, 35 symbols with hits.
- AQL candidate research: 12/12 completed.
- Zopedia ticker enrichment: 68 rows including market summary; 36 completed, 30 failed, 2 failed quality review.
- Zopedia market summary: completed; ElevenLabs narration failed nonfatally due quota.
- Public Home review: completed; reviewed 15, changed 14, dropped 1.
- Persisted datasets included `attention_home_1d`, `attention_home_snapshots_1d`, `attention_ticker_zopedia_enrichments`, `attention_ticker_background_snapshots`, `attention_research_bundles`, `attention_web_search_news`, `market_opportunity_feed`, and `page_agentic_summaries`.

Fresh Home payload:

- `top_events=0`, `must_read_movers=7`, `unresolved_large_moves=5`.
- Summary is materially better: enterprise software/cloud AI infrastructure led by SNOW, MDB, NOW, NTNX, HEI, ASTC/space-defense; not the old stale Iran/Qatar story.
- Examples of useful enriched rows: SNOW, ASTC, AVAV, RCAT, KTOS, HEI.

Quality gaps still present:

- Unresolved cards still show placeholder-style titles/body such as `LITE moves`, `SIDU moves`, `P moves`, `NOW moves`, `MSTR moves`.
- Some group backgrounds stitch multiple ticker enrichments together instead of writing one coherent group synthesis.
- Page-agentic summaries returned `ok` for SATL/WOLF/MXL, but `unavailable` for AUR and Market Explorer All Market / 1 Month.
- Many Zopedia failures are still `unknown_failure`; failure categories need better root labels and Admin rollup.

Verification notes:

- Direct Azure blob checks confirmed fresh nonzero parquet blobs, e.g. `attention_home_1d` size 74 KB and `attention_ticker_zopedia_enrichments` size 48 KB, both modified around `17:26Z`.
- Local `python3` without `pyarrow` made blob reads look empty. Use `.venv/bin/python` or direct blob metadata for verification.
