# True Bond View Plan (2026-04-06)

## Goal

Define what it would take to build a true bond view across:

- U.S. Treasuries
- corporate bonds
- municipal bonds
- agency / mortgage-linked bonds if desired later

This means bond-level instruments, not ETF proxies.

The user should be able to answer:

1. which specific bonds are moving,
2. what part of the move is Treasury / duration vs spread / credit,
3. how bonds compare across issuer, sector, rating, maturity, coupon, and liquidity,
4. where the best and worst relative-value pockets are.

## Current State

What the app has today:

- symbol-keyed taxonomy centered on exchange-listed instruments
- stock/ETF bar and snapshot flows through Alpaca
- Treasury curve datasets:
  - `yield_curve_observations`
  - `yield_curve_summary`
  - `yield_curve_facts_1d`
- fixed-income ETF labels in taxonomy

What the app does not have:

- bond security master
- CUSIP / ISIN / FIGI identifiers
- issuer-level debt hierarchy
- bond quote / trade feeds
- spread, yield-to-worst, OAS, convexity, duration, accrued-interest analytics
- TRACE/MSRB-style bond history
- liquidity / dealer / inventory modeling

## Why This Is A Different Project

The current system is built around exchange symbols and stock bars.

Evidence in code:

- taxonomy columns are `symbol`-first and do not include bond identifiers such as `CUSIP` or `ISIN`
- `asset_class` collapses mainly to `equity`, `etf`, or `real_estate`
- market data loaders call Alpaca stock endpoints and return bars keyed by symbol

Implication:

- you cannot extend the current market explorer into a true bond blotter just by adding a new lens
- you need a new instrument model, new data ingestion, and new analytics

## What A True Bond View Requires

### 1) Bond security master

Need a persistent bond instrument table keyed by stable identifiers.

Recommended fields:

- `instrument_id`
- `cusip`
- `isin`
- `figi` if available
- `issuer_id`
- `issuer_name`
- `asset_type`
- `subtype`
- `coupon_rate`
- `coupon_type`
- `maturity_date`
- `issue_date`
- `callable_flag`
- `next_call_date`
- `day_count`
- `payment_frequency`
- `currency`
- `seniority`
- `secured_flag`
- `sector`
- `industry`
- `rating_moodys`
- `rating_sp`
- `rating_fitch`
- `amount_outstanding`
- `state`
- `tax_status`

This is mandatory. Without it, corporate and muni bonds are not queryable in a useful way.

### 2) Bond market data ingestion

Need bond-level pricing/trade/quote sources.

For Treasuries:

- official Treasury reference data
- on-the-run issue mapping
- auction calendar
- benchmark curve linkage

For corporates:

- trade history
- quote or evaluated-price feed
- spread inputs vs Treasury curve

For munis:

- trade history
- evaluated pricing
- state / tax / AMT metadata
- callable structure support

At minimum the system needs:

- end-of-day evaluated price
- yield
- spread
- trade timestamp
- volume / size proxy
- quote freshness

### 3) Bond analytics engine

You need bond-specific analytics, not stock momentum analytics.

Required calculations:

- clean price
- dirty price
- accrued interest
- yield to maturity
- yield to worst
- option-adjusted spread where available
- spread to Treasury
- Macaulay / modified duration
- DV01
- convexity
- roll-down
- carry

For callable bonds and munis, the engine must handle:

- yield-to-call
- worst-call logic
- path-dependent call schedules

### 4) Curve and benchmark mapping

Each bond needs a benchmark context.

Examples:

- map Treasury issues to curve tenors / on-the-run buckets
- map corporates to interpolated Treasury benchmark
- map munis to muni curve and taxable/Treasury relative benchmark

Outputs should include:

- `treasury_benchmark_tenor`
- `interpolated_benchmark_yield`
- `spread_to_benchmark_bps`
- `curve_bucket`

### 5) Liquidity model

A true bond view without liquidity context will be misleading.

Need at least:

- last trade timestamp
- days since trade
- trade-count bucket
- volume bucket
- bid/ask spread if available
- stale-price flag

This is especially important for munis and smaller corporate issues.

### 6) New data model and datasets

Recommended materialized datasets:

1. `bond_security_master`
- one row per bond instrument

2. `bond_quotes_eod`
- one row per bond per as-of date

3. `bond_trades_recent`
- one row per recent trade

4. `bond_analytics_1d`
- one row per bond per as-of date
- price, yield, spread, duration, DV01, convexity, liquidity flags

5. `bond_curve_context_1d`
- benchmark mapping and curve-relative fields

6. `bond_relative_value_1d`
- z-scores and ranking within issuer / rating / sector / maturity buckets

7. `bond_universe_membership_1d`
- links bonds into user-facing sleeves:
  - Treasuries
  - IG corporate
  - HY corporate
  - municipals
  - callable munis
  - short duration
  - long duration

### 7) Search and filtering UX

A true bond view needs bond-native filters.

Required filters:

- issuer
- sector
- rating band
- maturity bucket
- yield range
- spread range
- state
- tax status
- callable vs non-callable
- last-trade freshness
- minimum size / liquidity bucket

The main table should show:

- issuer
- description
- coupon
- maturity
- price
- yield
- spread
- duration
- last trade
- liquidity flag

### 8) Relative value and watchlist logic

This is where the product becomes useful.

Examples:

- cheapest / richest vs issuer curve
- spread movers today
- yield pick-up vs similar rating/maturity peers
- cheap long-muni pockets by state
- off-the-run Treasury dislocations

These views require peer-bucketing logic that does not exist in the current equity/ETF stack.

## Recommended Delivery Phases

### Phase 1: True Treasury bond view

Scope:

- bill / note / bond issues
- on-the-run mapping
- curve, price, yield, DV01, duration

Why first:

- cleanest market structure
- most standardized analytics
- aligns with existing Treasury curve data

Estimated effort:

- roughly `1-2 weeks`

### Phase 2: Corporate bond view

Scope:

- IG and HY corporate issues
- spread-to-Treasury mapping
- issuer / sector / rating buckets
- liquidity flags

Estimated effort:

- roughly `2-4 weeks` after Phase 1

### Phase 3: Municipal bond view

Scope:

- muni identifiers and state/tax context
- callable support
- yield-to-worst
- relative value by state / tax status / duration

Estimated effort:

- roughly `2-4 weeks` after corporate, possibly more

Munis are the hardest part because structure and liquidity are much less uniform.

## Reliability / Complexity

### Treasury-only true bond view

- reliability: medium-high
- complexity: moderate

### Corporate true bond view

- reliability: medium
- complexity: high

### Municipal true bond view

- reliability: medium-low to medium unless data quality is strong
- complexity: very high

## Main Risks

1. stale or sparse bond quotes can create false ranking confidence
2. callable-bond math can be wrong if schedules or conventions are incomplete
3. muni liquidity can make “latest price” screens actively misleading
4. evaluated prices and traded prices can tell different stories
5. licensing and redistribution limits may constrain what can be stored or displayed

## Recommendation

Do not start with “all bonds.”

Best path:

1. build a real Treasury bond view first
2. add corporate bonds second
3. add municipals only after the data model and liquidity handling are proven

If the real ask is a practical market-explorer feature, the fixed-income ETF yield view is the right first product.

If the real ask is a professional bond workstation, this is a separate roadmap with new data vendors, new analytics, and new UI assumptions.

## Bottom Line

A true bond view is possible, but it is not a small extension.

Compared with the ETF-based yield view:

- data requirements are much larger
- analytics are much more specialized
- liquidity handling matters much more
- Treasuries are manageable first
- corporates and especially munis are materially harder

This should be treated as a separate product track, not just another market explorer tab.
