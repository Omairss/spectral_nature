# Yield View Plan (2026-04-06)

## Goal

Add a `Yield View` under `Market Opportunity` / market explorer that works like `Commodity Section`, but for fixed income:

- Treasuries
- corporate bonds
- municipal bonds
- aggregate/core bond funds
- inflation-linked bonds
- floating-rate / loan / CLO proxies

The view should answer:

1. which yield-sensitive groups are moving,
2. whether moves are duration-led or credit-led,
3. whether the curve is steepening/flattening or bull/bear shifting,
4. which fixed-income sleeves are benefiting or getting pressured.

## Current State

What already exists:

- `app.py` exposes only three market views today: `Markets`, `Broad Markets`, `Commodity Section`.
- `Commodity Section` already has a dedicated renderer and curated reference model.
- `macro-fred-daily` already materializes:
  - `yield_curve_observations`
  - `yield_curve_summary`
  - `yield_curve_facts_1d`
- DAL resolvers already expose those Treasury datasets.
- taxonomy already classifies many fixed-income ETFs and funds under labels such as:
  - `Municipal Bond ETF`
  - `Municipal Bond Closed-End Fund`
  - `Investment Grade Corporate Bond ETF`
  - `High Yield Corporate Bond ETF`
  - `Intermediate US Treasury ETF`
  - `Core Bond ETF`

Implication:

- Treasury data is present.
- fixed-income ETF grouping is partially present.
- there is no yield-specific UI, fixed-income reference model, or fixed-income scanner output yet.

## Recommendation

Build this in two stages.

### Stage 1: Fixed-income ETF Yield View

Recommended first release.

Use ETFs and fixed-income funds as tradable proxies, not individual bonds.

Why:

- fits the current Alpaca bar-based scanner architecture,
- aligns with existing market explorer patterns,
- avoids unreliable bond-level quote coverage,
- still gives the user Treasury / corporate / muni / HY / TIPS / core-bond visibility.

### Stage 2: Richer spread / causal layer

After the first usable view ships, add:

- Treasury curve regime labels,
- credit-vs-duration decomposition,
- muni-vs-Treasury and credit-vs-Treasury relative views,
- optional attention/homepage integration.

## What Needs To Change

### 1) New market-view branch

Add `Yield View` to the `Market View` segmented control and route it to a dedicated renderer, parallel to `Commodity Section`.

Likely files:

- `app.py`

### 2) Fixed-income universe builder

Create taxonomy-backed helpers similar to the commodity helpers in `services/market.py`.

Needed helpers:

- `yield_focus_options()`
- `yield_focus_description(name)`
- `yield_focus_universe(name)`
- `default_yield_universe_symbols()`
- `yield_proxy_profile(symbol)`

Use taxonomy first, with config-backed overrides only where taxonomy is weak.

Suggested focus buckets:

- `Broad Fixed Income`
- `Treasuries`
- `Government`
- `Investment Grade Corporate`
- `High Yield`
- `Municipal`
- `Aggregate / Core`
- `TIPS / Inflation-Linked`
- `Floating Rate / Loans / CLO`

### 3) Versioned config, not hardcoded lists

Do not repeat the commodity pattern of long inline dictionaries for the durable version.

Add a versioned config file, for example:

- `config/yield_view_profile.v1.yaml`

Contents:

- focus buckets
- taxonomy match rules
- optional manual symbol overrides
- display names / descriptions
- benchmark / reference basket rules
- relationship edges
- scoring weights

### 4) Yield/fixed-income scanner

Add a scanner that combines:

- ETF/fund price action from Alpaca
- Treasury curve facts from `yield_curve_facts_1d`
- Treasury history from `yield_curve_observations`

Recommended output datasets:

1. `yield_market_summary`
- one row per symbol
- momentum, relative strength, duration sensitivity, credit sensitivity, drawdown, regime label

2. `yield_market_history`
- one row per symbol per day
- normalized price, relative path, rolling beta/correlation to Treasury reference, spread proxy metrics

3. `yield_market_facts_1d`
- one row per run
- curve shape, bull/bear steepener/flattening label, front-end vs long-end move, credit-vs-rates breadth summary

### 5) Yield-specific UI

Add a renderer similar in shape to `_render_commodity_experiment(...)`, but focused on:

- key metrics:
  - 10Y yield
  - 2s10s
  - 3m10y
  - bull/bear steepener or flattener
- tables:
  - duration winners
  - credit winners
  - muni strength / weakness
  - spread-stress names
- charts:
  - Treasury curve snapshot and recent change
  - fixed-income heatmap by sleeve
  - relative leadership map: duration vs credit
  - selected fund detail panel

### 6) Optional relationship graph

Commodity has a dependency graph. Yield view can also benefit from a lighter relationship model.

Examples:

- `front_end_rates -> short_duration_treasuries`
- `long_end_rates -> long_duration_treasuries`
- `credit_spreads -> high_yield`
- `credit_spreads -> investment_grade_corporate`
- `muni_relative_value -> municipal_bonds`
- `breakevens / inflation -> tips`

This should also be config-backed.

## Reliability / Complexity

### Reliable and reasonable now

- Treasury yield view
- Treasury + bond ETF proxy view
- broad fixed-income sleeve view using ETFs/funds

### Higher risk / not recommended for v1

- individual bond inventory view
- CUSIP-level corporate/municipal bond analytics
- live bond quote/dealer depth style interface

Why not:

- the current app is built around Alpaca bars and materialized macro datasets,
- individual bond coverage is incomplete and messy,
- muni and corporate bond microstructure is much less uniform than ETF proxy data.

## Estimated Effort

### V1: Treasury + fixed-income ETF view

Roughly `1-3 days` of focused work.

Main tasks:

- add market-view branch
- add taxonomy/config-based fixed-income universe helpers
- add fixed-income scanner
- add first UI section

### V2: Better decomposition and relationship layer

Roughly `2-4 more days`.

Main tasks:

- spread-vs-duration decomposition
- yield regime labels
- relationship graph
- better provenance and precompute outputs

### Individual-bond experience

Separate project.

Not recommended as an extension of the current market explorer architecture.

## Suggested Implementation Order

1. Add `Yield View` UI branch.
2. Add taxonomy-backed fixed-income focus helpers.
3. Build `Broad Fixed Income` + `Treasuries` + `Municipal` + `Investment Grade Corporate` + `High Yield` buckets.
4. Build summary/history/facts outputs.
5. Render Treasury curve + fixed-income heatmap + leadership tables.
6. Add config-backed relationship graph if the first cut proves useful.

## Acceptance Criteria

- `Yield View` appears beside `Markets`, `Broad Markets`, and `Commodity Section`.
- user can switch among Treasury / muni / corporate / HY / broad fixed-income buckets.
- Treasury facts come from materialized yield datasets when available.
- ETF/fund groupings come from taxonomy + config, not scattered hardcoded lists in UI code.
- view degrades honestly when Treasury data or market bars are stale/missing.
- v1 clearly states it is a fixed-income proxy view, not an individual bond blotter.

## Bottom Line

Yes, this is very doable.

If the target is:

- a market-explorer-style `yield view` using Treasury data plus fixed-income ETFs/funds,

then the cost is moderate and fits the current architecture well.

If the target is:

- a true bond-market screen across individual corporates, Treasuries, and munis,

that is a different and much larger product because the current data model is not built for bond-level market structure.
