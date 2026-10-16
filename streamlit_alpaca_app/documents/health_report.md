# Spectral Nature Health Report

## Current assessment — October 15, 2026

Checked approximately 19:44–19:51 PDT. The application is available but research is degraded. All eight application revisions are ready; production Home returns HTTP 200 with eight story groups. Latest numerical ingestion, news and brokerage sync executions succeeded. No health incidents are currently open.

All four Home chart-selection calls exceeded the model context limit, leaving zero daily charts. Learning critics also exceeded the limit while their jobs reported success. Latest Trading Agent output has four successful horizons and a failed three-month horizon; two earlier executions exited 137 with memory above their 2 GiB allocation. Afternoon PostgreSQL connection exhaustion interrupted several consumers, including monitoring, and left final Home publication missing from database metadata despite successful Blob publication.

See the [current investigation](architecture/overall/audits/SYSTEM_HEALTH_2026-10-15.md) for exact errors, recovery, retained output and repair priorities. Assessments below are historical context, not current health declarations.

## Historical monitoring update — September 19, 2026

Updated: 2026-09-19 16:07 PDT

The historical assessment below remains useful context, but it is not a live health declaration. Production now runs `system-health` every 15 minutes. It evaluates persisted Market Overview and Trading Agent job outcomes, Market Overview freshness, Trading Agent horizon availability, and recent provider billing/quota/authentication failures. Incident state is durable in pipeline Postgres; it sends one alert when an issue opens and one recovery alert when it clears.

Deployment verification completed at 2026-09-19 23:06 UTC. The first successful monitor execution opened two current incidents and sent one email. A second unchanged-state execution completed with `open=2`, `opened=0`, and `emails=0`, confirming alert deduplication. The initial delivery failure exposed an Azure Communication Services configuration issue: the verified `spectral-nature.com` sender domain was not linked to `sn-comm-03130136`. That link is now present, so the existing `no-reply@spectral-nature.com` sender is valid.

Trading Agent funding recovery, 2026-09-19 18:32 PDT: DeepSeek billing is
working again. Manual production execution `trading-agent-build-brn7jc1`
succeeded, but it started while `attention-home-build-19emeub` was still
rebuilding the upstream opportunity feed and therefore consumed the previous
2026-08-14 snapshot. After the Attention/Home execution succeeded and
published the 2026-09-20 feed and page summaries, clean execution
`trading-agent-build-kk3qd1p` succeeded in 11m12s, including container startup.
It completed six of eight
company research packages, failed closed for VICR and HONA, completed all five
horizon syntheses, and persisted 11 candidates. All 11 new outcome rows are
`missing_entry` because the run occurred on Saturday after the latest Friday
price bar; this run does not provide a win rate or a backtest result.

The recovery exposed a scheduling race. `attention-home-build` starts at
minute 20 and the Trading Agent starts at minute 35, but the recovered
Attention/Home execution took 24m38s and did not publish its core feed until
19m32s after start. A fixed 15-minute offset can make a healthy Trading Agent
consume the prior opportunity snapshot. The downstream job needs an explicit
upstream-version/freshness gate or a schedule with enough measured margin;
job success alone is not a coherent-snapshot guarantee.

Generated: 2026-08-10 10:38 PDT

Scope: live Azure Container Apps jobs, deployed UI/API containers, current materialized dataset pointers, and local repo state.

Recovery update, 2026-08-10 11:02 PDT: the shared DeepSeek client now translates HTTP 402 `insufficient_balance` into: "DeepSeek account has insufficient funds. Add credit in DeepSeek billing before retrying the pipeline." The production scheduled jobs use the isolated release image `pipeline-jobs:202608101755-provider-error`, built from the deployed `v0.6` commit plus only this provider-error change. The next Attention/Home run is scheduled for 2026-08-10 18:20 UTC; its normal 18-22 minute runtime keeps expected recovery inside two hours, so no overlapping manual execution was started.

Trading Agent update, 2026-08-10 21:30 PDT: provider funding and the upstream
Market Opportunity feed recovered, but the 20:35 UTC Trading Agent run was
still product-unhealthy. All five horizons exceeded the old 300-second
deadline and persisted 20 price-only fallback candidates with zero research
reviews. Azure marked the execution successful because the fallback exception
was swallowed. The local redesign now researches a balanced pool of unique
companies once, reuses completed Zopedia/AQL packages across horizons, and
fails closed without fallback candidates. This redesign is implemented and
verified locally but is not yet represented in the production dataset rows.

Trading Agent recovery, 2026-08-10 23:10 PDT: shared research V2 is deployed
to the production `trading-agent-build` job. Verification execution
`trading-agent-build-ob5zvqr` succeeded in 25m33s. It researched eight unique
companies once, completed seven packages, isolated one DeepSeek empty-content
failure for LUNR, produced five successful horizon runs and 13 grounded
candidates, and emitted no fallback rows. This reduced the maximum research
work from 30 repeated company packages to eight unique packages. The job is
now pinned to the follow-up image digest
`sha256:3e6d744b4eda950e4a6e947a3840b433a84a4245dd45e7bed01f193642fdd7a0`,
which also prevents generated watchlist text from instructing users to trade.

## Executive Summary

The dashboard is reachable, but its dashboard-facing generation layers are unhealthy.

Raw market data is current today. Universe, equities, price history, macro, commodities, options, news, fundamentals, Zopedia maintenance, and Zopedia learning jobs have recent successful executions. The failure is downstream: the Attention/Home job has failed every scheduled run since 2026-07-23 16:20 UTC, so the Home/attention datasets are frozen at 2026-07-23. The Trading Agent job is also product-unhealthy: it exits successfully, but writes fresh `error` rows with zero candidates.

The original common root cause was LLM provider failure. DeepSeek funding has
since recovered. Attention/Home produced a fresh Market Opportunity feed, but
the Trading Agent then exposed a separate architecture failure: repeated
per-horizon research exceeds its deadline and continues running after the
timeout while fallback rows hide the failure.

## Health Status

| Area | Status | Evidence | Product Impact |
| --- | --- | --- | --- |
| Public Streamlit UI | Reachable | `torres-cap.com`, `www.torres-cap.com`, `sn-streamlit-ui`, and `sn-streamlit-ui-dev` return HTTP 200. | UI shell loads, but content can still be stale or empty because materialized datasets are stale. |
| API containers | Degraded cold start, then healthy | `sn-api` and `sn-api-dev` have min replicas `0`. Initial `/health` checks timed out after 20s; follow-up `/health` returned `{"status":"ok"}`. | API is available after wake-up, but health checks with short timeouts can falsely report downtime. |
| Raw equity data | Healthy | `daily_movers` and `price_history` as-of 2026-08-10 16:35 UTC; `equities-intraday-preload` succeeded at 16:35 UTC. | Fresh market moves are available to downstream jobs. |
| News ingestion | Healthy operationally | `news_articles` as-of 2026-08-10 16:05 UTC; recent `news-ingest-and-features` runs succeeded. | Fresh news exists, but Attention/Home is not converting it into dashboard narratives. |
| Macro/FRED | Mostly healthy data, unhealthy summary | `macro-fred-daily` succeeded at 11:00 UTC; `page_agentic_summaries` has one fresh row but status `unavailable`. | Broad Economy data exists, but the generated page summary is unavailable due insufficient grounded AQL evidence. |
| Attention/Home generation | Critical | `attention-home-build` failed every scheduled run from 2026-07-23 16:20 UTC through 2026-08-10 16:20 UTC. Latest successful artifact is 2026-07-23 14:20 UTC. | Home dashboard, attention feed, event clusters, market opportunity feed, and related summaries are stale. |
| Attention evidence acquisition | Critical | Latest logs: search-backed news backfill completed `100/100` with `hits=0`; AQL candidate research mostly had `documents=0` and `claims=0`. | Even after provider recovery, narrative quality will remain weak unless evidence acquisition returns usable sources. |
| Zopedia enrichment inside Attention | Critical | Latest logs: `attention-home-build zopedia enrichment done: 0/13 completed`, `zopedia market summary: failed`. | Ticker context and market summary are missing from Home cards. |
| Group synthesis | Critical | Latest logs: `group synthesis: status=skipped source_items=0`. | Cluster cards can duplicate per-ticker sections or produce sparse, non-synthesized narratives. |
| Trading Agent | Recovered; monitor package reliability | Shared research V2 completed 7/8 unique company packages, all five horizon syntheses, and 13 grounded candidates in production. The LUNR package retained an explicit DeepSeek empty-content failure. | The dashboard has genuine researched candidates. Recall is bounded by the configured scout and research-pool limits rather than repeated horizon calls. |
| Entity Taxonomy | Unhealthy/stale | `entity_taxonomy_labels` latest ready artifact is 2026-06-01. The 2026-08-01 refresh failed on first LLM batch with DeepSeek HTTP 402 after writing fresh listings. | Newer symbols and taxonomy shifts are not classified; attention grouping and theme labels can be stale. |
| Dataset pointers | Unhealthy drift | Attention datasets have DB-vs-manifest drift. Example: `attention_home_1d` DB pointer is 2026-07-03, manifest/latest reader is 2026-07-23. | Different consumers may see different stale versions depending on whether they use DB or manifest. |
| Pipeline release state | Trading Agent V2 isolated in production | Only `trading-agent-build` is pinned to `pipeline-jobs@sha256:3e6d744b...`; other jobs retain their prior images. Local repo still has separate uncommitted Attention/AQL work. | The Trading Agent redesign is live without repointing unrelated scheduled jobs. |

## Freshness Snapshot

Current as of 2026-08-10 17:35 UTC:

| Dataset | Latest As-Of | Rows | Age | Health |
| --- | --- | ---: | ---: | --- |
| `universe_snapshot` | 2026-08-10 13:20 UTC | 1,000 | 4.2h | Healthy |
| `daily_movers` | 2026-08-10 16:35 UTC | 999 | 1.0h | Healthy |
| `price_history` | 2026-08-10 16:35 UTC | 1,424,203 | 1.0h | Healthy |
| `news_articles` | 2026-08-10 16:05 UTC | 50 | 1.5h | Healthy |
| `quarterly_fundamentals` | 2026-08-10 12:00 UTC | 187,704 | 5.6h | Healthy |
| `page_agentic_summaries` | 2026-08-10 11:00 UTC | 1 | 6.6h | Fresh but unavailable |
| `trading_agent_runs` | 2026-08-10 20:35 UTC | 5 | Current run | All fallback after five 300-second timeouts |
| `trading_agent_candidates` | 2026-08-10 20:35 UTC | 20 | Current run | Price-only fallback rows; not genuine agent output |
| `attention_home_1d` | 2026-07-23 14:20 UTC | 1 | 435.2h | Stale |
| `attention_candidates_1d` | 2026-07-23 14:20 UTC | 100 | 435.2h | Stale |
| `attention_event_clusters_1d` | 2026-07-23 14:20 UTC | 11 | 435.2h | Stale |
| `attention_web_search_news` | 2026-07-23 14:20 UTC | 0 | 435.2h | Stale and empty |
| `attention_source_documents` | 2026-07-23 14:20 UTC | 4 | 435.2h | Stale and thin |
| `market_opportunity_feed` | 2026-07-23 14:20 UTC | 6,186 | 435.2h | Stale |
| `entity_taxonomy_labels` | 2026-06-01 09:00 UTC | 6,613 | 1,688.6h | Stale |
| `company_baselines` | 2026-08-01 10:15 UTC | 50 | 223.3h | Fresh for monthly cadence, but low coverage |

## Failure Chain

1. Fresh market and news jobs run successfully.
2. Attention/Home starts with current inputs: latest log says `movers=999`, `shortlist=500`, `holdings=0`.
3. Search-backed backfill returns zero current news hits for 100 candidates.
4. AQL candidate research finds almost no persisted evidence: most candidates have `documents=0` and `claims=0`.
5. Zopedia enrichment fails for all sampled tickers; market summary fails.
6. Group synthesis skips because there are no source items.
7. Public-surface quality review calls DeepSeek and fails with HTTP 402.
8. The job exits failed before publishing new Home/attention datasets.
9. The UI keeps rendering the latest available materialized artifacts, which are stale.

## What Is Healthy

- Azure scheduled job infrastructure is present and schedules are active.
- Raw market, price, news, macro, commodity, options, and fundamentals ingestion jobs are producing current artifacts.
- Zopedia maintenance and learning jobs are executing successfully as jobs.
- The public Streamlit UI is reachable.
- The DeepSeek provider can now complete a minimal direct JSON call with the deployed provider settings.

## What Needs Immediate Recovery

1. Verify the scheduled 2026-08-10 18:20 UTC `attention-home-build` completes now that the DeepSeek round trip passes. Start a manual run only if that execution does not start or cannot complete inside the two-hour recovery window.
2. Verify the rerun publishes same-day `attention_home_1d`, `attention_candidates_1d`, `attention_event_clusters_1d`, `attention_web_search_news`, `attention_source_documents`, and `market_opportunity_feed`.
3. Treat `search-backed news backfill hits=0` as a separate blocker. If the rerun still produces zero hits, inspect Serper/search provider env and the Zopedia/AQL search boundary.
4. Monitor shared-research V2 over subsequent scheduled runs. The first
   production verification already confirmed completed grounded packages,
   no repeated horizon research, five successful horizon runs, and no
   fallback rows.
5. Rerun `entity-taxonomy-refresh` after provider recovery and confirm `entity_taxonomy_labels` moves off 2026-06-01.
6. Resolve attention dataset pointer drift so DB and manifest latest agree. The system should not let two readers select different stale artifacts.
7. Promote or intentionally discard the large local uncommitted change set before any production pipeline image update. Current prod jobs are still on the 2026-07-02 image.

## Recommended Health Gates

Before declaring the dashboard healthy again:

- Latest `attention-home-build` execution status is `Succeeded`.
- `attention_home_1d.asof_time_utc` is today.
- `market_opportunity_feed.asof_time_utc` is today.
- `attention_web_search_news.row_count > 0` or the job records an explicit no-news explanation with searched providers and queries.
- `attention_source_documents.row_count` is materially above the current stale count of 4.
- Zopedia enrichment completes for at least most configured ticker targets, not `0/13`.
- Group synthesis runs with nonzero source items.
- Trading Agent has completed package rows and non-error horizon runs; zero
  candidates must be an explicit model judgment, not provider failure or a
  timeout fallback.
- `entity_taxonomy_labels` has a post-recovery artifact.
- DB latest and manifest latest are in sync for all dashboard-facing datasets.
