# Market Opportunity Precompute Recovery

## Problem

Presentation mode exposed false empty states for some symbols such as `VRDN`:

- next-week forecast showed "Not enough history" even when `technical_signal_history` existed
- recent news was empty when `news_articles` had no symbol rows
- company overview degraded to generic text because `asset_metadata` is not materialized

## Root Cause

Two different gaps were conflated:

1. `attention-home-build` only seeded ticker background/news snapshots from materialized `news_articles`
2. `resolve_forecast_next_week()` returned early in `materialized_only` mode instead of using already-materialized signal history

## Fix

1. Search-backed news backfill in `pipeline/jobs/attention_home_build.py`
   - for shortlisted symbols missing materialized news, call the existing `search_symbol_news_payload()` path
   - materialize those search results into `attention_web_search_news`
   - merge those rows into the ticker background snapshot build so homepage/home-detail snapshots retain the recovered context

2. Materialized-only resolver recovery in `data_access/layer.py`
   - `resolve_forecast_next_week()` now computes from materialized `technical_signal_history` when present
   - `resolve_recent_news()` now falls back to `attention_web_search_news`, then ticker background snapshots
   - `resolve_asset_metadata()` now falls back to materialized company names from `universe_snapshot` or ticker background snapshots

## Expected Outcome

- symbols with price/signal history no longer show false forecast-empty states in presentation mode
- symbols missing `news_articles` can still show search-backed news/context after the attention build job reruns
- company overview text regains a real company name and news themes instead of generic placeholder text

## Deployment Verification

- Pipeline image built and deployed to the shared Azure job as `pipeline-jobs:20260331062752`
- Azure execution `attention-home-build-w3vrpxq` succeeded on `2026-03-31`
- attention datasets rolled to the `2026-03-31T06:33:18Z` build set:
  - `attention_web_search_news__20260331T063318Z__2b02baf3`
  - `attention_ticker_background_snapshots__20260331T063318Z__2b02baf3`
  - `attention_home_1d__20260331T063318Z__2b02baf3`
- `VRDN` now resolves on the materialized path with:
  - search-backed news present in `attention_web_search_news`
  - background snapshot present in `attention_ticker_background_snapshots`
  - computed next-week forecast from `technical_signal_history` with `analog_count=80` and `up_probability=0.512`
