# Market Agentic Work Plan - 2026-04-27

## Effort Ranking

| Rank | Task | Effort | Why |
| --- | --- | --- | --- |
| 1 | Consolidated Market Opportunity feed | Low | The Market Explorer already loads movers, momentum, and consistency tables. The work is mostly a shared scoring helper plus one table renderer. |
| 2 | Agentic summaries for Market Explorer, Stock Investigator, and Broad Economy | Medium | This needs one shared summary contract and LLM-backed writing, with fallback behavior when the LLM is unavailable. |
| 3 | Admin-only Trading Agent experiment | High | Trade suggestions need cross-page context, extra evidence, confidence and tail-risk fields, admin-only access, and no automated execution. |

## Design

- Redo correction on 2026-04-27: treat the three TODO items as one architecture slice. The prior hot-path implementation was wrong because Trading Agent rebuilt scanners itself and page summaries could surface raw AQL failures at render time.
- Keep source data in existing loaders and data-access methods.
- Build shared service helpers for market opportunity scoring, page summaries, and trading-agent payloads.
- Page summaries and Trading Agent synthesis must reuse the shared AQL / Chat + Search agent path before rendering UI-friendly summary fields. The JSON summary schemas are formatting adapters, not standalone research agents.
- Page summaries follow the homepage summary pattern: the attention pipeline materializes `page_agentic_summaries`, the data-access layer resolves them, and the UI renders the materialized payload.
- The consolidated Market Opportunity feed follows the same pattern: the attention pipeline materializes `market_opportunity_feed`, the data-access layer selects focus/horizon rows, and the UI renders that materialized table without scanner calls during page load.
- Keep the UI thin: pages pass already-loaded frames and payloads into shared helpers, then render returned structured data.
- Expensive agentic runs must not execute during page render. Page-summary refreshes should run through the attention materialization job, not direct AQL calls in Streamlit.
- Do not auto-trade. The trading agent returns watchlist-style trade candidates with evidence, invalidation, tail risk, and confidence.
- If LLM is unavailable, show structured data and explain that narrative generation is unavailable instead of fabricating trade advice.
- Page-summary and Trading Agent calls use `persist_findings=False` so on-demand UI runs do not pollute the durable chat log.

## Trading Agent Signal Surface Redesign

- Current problem: the candidate card exposes internal scanner fields as unexplained metrics. `Opportunity 88` is a composite rank, `Daily` is a 1-day move, `Feed` is an internal opportunity label, and `Momentum` can show `n/a` when the selected horizon is not one of the hardcoded UI fallback columns.
- Candidate cards should separate model judgment from market scanner context:
  - `Setup call`: direction, confidence, suggested horizon, setup label, hypothesis.
  - `Why surfaced`: market signal rank with a plain explanation that it is a 0-100 relative rank, not expected return or conviction.
  - `Price action`: 1D move, selected horizon return, and momentum pace/acceleration from `momentum_roc_score`.
  - `Trend quality`: convert raw `trend_fit_gap` into plain labels such as consistent, stretched, or noisy; keep the raw value only in an expander.
  - `Evidence chain`: AQL verdict, stock summary headline, attention/news lines, invalidation, and tail risks.
  - `Data quality`: show missing fields and source freshness so `n/a` is explained instead of looking broken.
- Do not render long text labels like `Up / accelerating` inside narrow metric cells. Use readable rows, chips, or a compact table with full labels and help text.
- Rename visible fields:
  - `Opportunity` -> `Market signal rank`.
  - `Daily` -> `1D move`.
  - `Momentum` -> selected horizon label, for example `1M return`.
  - `Trend Gap` -> `Trend quality`.
  - `Feed` -> `Market pattern`.
- Fix source data selection at the same time: carry `selected_horizon_col` and `selected_horizon_label` into the Trading Agent context and use that exact column for candidate cards.

## Guardrails Read Before Redo

- `documents/mistakes.md`: 32, 37, 38, 39.
- `documents/learnings.md`: 49, 50, 51, 52, 53, 54.
- Short reference: `documents/reference/AGENTIC_MARKET_TASK_GUARDRAILS.md`.

## Redo Strategy

- Market Opportunity: attention job materializes `market_opportunity_feed`; data access selects focus and horizon; Streamlit renders the table. No mover/momentum scanner calls on the main feed render path.
- Page summaries: attention job materializes `page_agentic_summaries`; UI resolves by context signature, ticker fallback, or latest surface lookup. AQL runs inside the job, not the panel constructor.
- Trading Agent: admin-only page builds context from materialized market/page-summary outputs, then runs one explicit AQL-backed synthesis only when the user clicks `Run Trading Agent`.
- Reliability: deterministic market-feed materialization should fail the attention job if it breaks; page-summary AQL failures degrade into materialized fallback summaries with data gaps. Candidate research, event bundle writing, macro verification, and summary steps are bounded by job-level timeouts so one slow search path cannot block persistence.

## Progress

- [x] Rank TODO items by effort.
- [x] Replace multiple Market Explorer opportunity tables with one consolidated opportunity feed.
- [x] Add page-level summaries to Market Explorer, Stock Investigator, and Broad Economy.
- [x] Add admin-only Trading Agent experiment view.
- [x] Move page summaries to the attention materialization job and stop direct AQL calls during page render.
- [x] Move the Market Opportunity feed to the attention materialization job and stop render-time mover/momentum scans for the main Market Explorer feed.
- [x] Redo Trading Agent context loading so it consumes materialized market/page-summary outputs before explicit synthesis.
- [x] Add regression coverage for materialized feed lookup, latest page-summary lookup, AQL exception fallback, and numpy-array list coercion.
- [x] Add regression coverage that scheduled page-summary timeouts materialize `fallback` rows, not `error` rows.
- [x] Deploy corrected UI/pipeline changes to dev only.
- [x] Verify a fresh attention job materializes `market_opportunity_feed` and `page_agentic_summaries` on the corrected image.
- [x] Improve Trading Agent result UI so candidate identity, long/watch setups, short/avoid setups, source summaries, and AQL trace are readable without raw JSON.
- [x] Update Trading Agent synthesis prompt so short/avoid setups are explicitly considered but still gated by evidence.
- [x] Add Trading Agent visual signal cards with score breakdown bars, selected-horizon price action, trend-quality labels, evidence checklist, and missing-signal notes.

## Dev Verification

- UI dev image: `sn-streamlit-ui-dev--0000279`, digest `sha256:36e6c4ffabc9bcda6b1e0928f5b5cb3544115dd8c95b85328e88f85b303456c8`.
- Final pipeline image: `pipeline-jobs:20260427181242`, digest `sha256:f746fe382e2bd7f2787ebb24ea14e84e86c4d43e35e4bd82df0b96fdcaa376c9`.
- Final attention job execution: `attention-home-build-cniubxs`, status `Succeeded`, start `2026-04-28T01:22:38Z`, end `2026-04-28T02:04:08Z`.
- Materialized outputs from run `a357310a-9d97-4112-b445-f4ae3d2eb6c7` at `2026-04-28T01:23:04.866011Z`:
  - `market_opportunity_feed`: 6,240 rows.
  - `page_agentic_summaries`: 6 rows; 3 `ok`, 3 `fallback`, 0 `error`.
- The final run used the corrected pipeline image that includes `data_access`, so shared AQL summary code is available inside the job container.
- Local regression after timeout-fallback fix: `19 passed in 0.99s`.
- Trading Agent UI refinement local checks:
  - `.venv/bin/python -m py_compile app.py services/trading_agent.py`
  - `.venv/bin/python -m pytest -q tests/test_trading_agent.py` -> 4 passed.
- Trading Agent UI refinement dev deploy:
  - `scripts/which_deploy.sh --check ui` -> passed.
  - `scripts/deploy_ui_azure.sh --target dev` -> deployed `sn-streamlit-ui-dev--0000280`.
  - Image digest: `sha256:0bedac653a496b85478d94f298c4d3c7ffd5b74e9d0b6df47ee608152a0aa302`.
  - Root smoke check: HTTP 200.
- Trading Agent short/avoid rollout dev deploy:
  - `scripts/which_deploy.sh --check ui && scripts/deploy_ui_azure.sh --target dev` -> deployed `sn-streamlit-ui-dev--0000281`.
  - Image digest: `sha256:8fe20c89280ef48d68a0046dcecb0e739e890490b84627fa5194fdcbeb313675`.
  - Root smoke check: HTTP 200.
- Trading Agent short/avoid prod promotion:
  - `scripts/which_deploy.sh --check ui && scripts/deploy_ui_azure.sh --target prod --promote-from dev` -> deployed `sn-streamlit-ui--0000063`.
  - Image digest: `sha256:8fe20c89280ef48d68a0046dcecb0e739e890490b84627fa5194fdcbeb313675`.
  - Root smoke check: HTTP 200.
- Trading Agent visual signal card local checks:
  - `.venv/bin/python -m py_compile app.py services/trading_agent.py`
  - `.venv/bin/python -m pytest -q tests/test_trading_agent.py` -> 4 passed.
- Trading Agent visual signal card dev deploy:
  - `scripts/which_deploy.sh --check ui && scripts/deploy_ui_azure.sh --target dev` -> deployed `sn-streamlit-ui-dev--0000282`.
  - Image digest: `sha256:77344441a7b7b0d0addad95492f31d31a527ed735bfc23d803b9ad4d872b3a72`.
  - Root smoke check: HTTP 200.
- Stock Investigator company-context fallback fix:
  - Cause: aggregate recent-news coverage text was being selected as narrative context, then duplicated into Background and What Happened.
  - `.venv/bin/python -m py_compile app.py`
  - `.venv/bin/python -m pytest -q tests/test_services.py::test_summarize_recent_news_uses_mixed_when_sentiment_is_blank tests/test_data_access_query_service.py::test_resolve_attention_ticker_background_keeps_materialized_context_when_bundle_has_no_web_headlines` -> 2 passed.
  - `scripts/which_deploy.sh --check ui && scripts/deploy_ui_azure.sh --target dev` -> deployed `sn-streamlit-ui-dev--0000283`.
  - Image digest: `sha256:a4ec93a96bb50e92f6ff7d3104e4df0d9335e9ccdcab9f0be2ec2ac0bb73fc61`.
  - Root smoke check: HTTP 200.

## Proposed Ticker Context Architecture

- Split Stock Investigator context into two persisted layers:
  - `Company baseline`: slow-changing identity facts for as many tradable symbols as practical. Source candidates: Wikipedia summary, exchange/universe metadata, taxonomy labels, role hints, business lens, and later SEC/company-description fields. Refresh monthly with the taxonomy/entity job or a sibling monthly job.
  - `Today relevance`: fast-changing catalyst/context facts. Source candidates: daily `news_articles`, attention context bundles, search-backed relevant-news rows, price/signal movement, and AQL summaries for active symbols only. Refresh in `news-ingest-and-features` and `attention-home-build`.
- UI contract:
  - `Background` should always prefer `Company baseline`; if missing, show an explicit baseline-missing state and optionally trigger a bounded on-demand lookup for that ticker.
  - `What Happened` should prefer `Today relevance`; if no relevant catalyst exists, say no relevant catalyst is available instead of reusing generic article-count metadata.
  - Article-count/tone summaries remain metadata and evidence context, not narrative body text.
- Cost control:
  - Do not search the web daily for the entire universe.
  - Do prefetch cheap baseline summaries monthly across the broad symbol universe.
  - Do daily/news/search enrichment only for active symbols: attention candidates, portfolio/holdings/watchlist names, high market-opportunity ranks, and recently viewed tickers.
  - Cache on-demand misses back into the baseline/relevance stores so manual Stock Investigator usage improves future runs.
