# Universe Expansion Plan V2

## Objective

Expand the equity pipeline from the current `1000`-name universe to near-full NASDAQ + NYSE common-stock coverage while preserving reliability, leaving room for higher refresh frequency, and keeping the core historical layer stable.

This V2 plan assumes the incremental `price_history` path is now the default steady-state behavior:

- load the latest `price_history` snapshot
- fetch a short recent overlap window
- backfill full history only for missing, thin, or stale symbols
- publish a new merged snapshot

That change removes the biggest blocker to breadth expansion, but it does not by itself make a jump from `1000` to `~5300` symbols safe inside the current single-job shape.

## Current Baseline

Verified baseline as of `2026-03-22` UTC:

- Live intraday equities job: `equities-intraday-preload`
- Schedule: `35 13,16,18,20 * * 1-5` (`4x/day` weekdays)
- Resources: `1 CPU`, `2 GiB`, `3600s` timeout
- Current universe config:
  - `EQUITY_UNIVERSE_TARGET_SIZE=1000`
  - `EQUITY_UNIVERSE_INCLUDE_ETFS=false`
  - `EQUITY_UNIVERSE_INCLUDE_NON_COMMON=false`
  - `EQUITY_UNIVERSE_MIN_PRICE=5`
  - `EQUITY_UNIVERSE_MIN_VOLUME=100000`
  - `EQUITY_UNIVERSE_MIN_DOLLAR_VOLUME=5000000`
- Current incremental history config:
  - `EQUITY_PRICE_INCREMENTAL_LOOKBACK_DAYS=45`
  - `EQUITY_PRICE_FULL_REFRESH_HOURS=168`
  - `EQUITY_PRICE_HISTORY_TOLERANCE_DAYS=7`
- Latest verified materialized breadth:
  - `universe_snapshot`: `1000` symbols
  - `price_history`: `1001` symbols including `SPY`, `1,352,981` rows
  - `price_history` span: `2017-11-15` through `2026-03-20` UTC
  - `momentum_profiles`: `997` symbols
  - `daily_movers`: `999` symbols

Current addressable listing counts from the exchange symbol directories:

- Common stocks only: `5317`
- Common stocks + ETFs: `6550`
- Common stocks + non-common: `6674`
- Common stocks + ETFs + non-common: `7917`

For V2, the target should be the `5317` common-stock universe first. ETFs and non-common securities should remain a separate expansion decision.

## What Changed Since V1

The pipeline no longer asks Alpaca for the full `~10`-year bar history for every symbol on every intraday run.

Normal steady state is now:

- prior snapshot + recent incremental overlap
- dedupe on `symbol + timestamp`
- keep the newest fetched bar when windows overlap

That is the right foundation for broader coverage. The remaining constraint is job shape, not historical bar fetch strategy.

## V2 Principles

1. Keep the core market-data path fast, predictable, and easy to retry.
2. Expand breadth in phases instead of jumping directly from `1000` to `5317`.
3. Treat fundamentals and attention-layer work as separate scaling problems from bar ingestion.
4. Use full-exchange common-stock coverage as the V2 finish line.
5. Do not increase refresh frequency until the broader universe is stable at the existing `4x/day` schedule.

## Practical Reality At Higher Breadth

At `1000` symbols, the live universe already contains a large fallback component, not just high-liquidity names. In the latest verified snapshot:

- `523` symbols were selected directly by liquidity rank
- `390` were included through `liquidity_fallback`
- `87` were included through `pinned_curated`

That means a move toward `5317` is feasible, but it is not just “more of the same liquid universe.” It intentionally pulls in thinner names, which has implications for signal quality, missing data, and runtime.

## Recommended End State

By the end of V2, the pipeline should operate as three logical workloads instead of one oversized intraday job.

### 1. Core Equities Intraday Job

Run `4x/day` at minimum, with room to increase later.

Owns:

- `daily_movers`
- `price_history`
- `momentum_profiles`
- `correlation_phase_shift_summary`
- `correlation_phase_shift_history`
- `technical_signal_history`
- `technical_signals_latest`

This job should be the one we scale to the full `5317` common-stock universe first.

### 2. Attention / Expectations Job

Run after the core job or at a lower cadence.

Owns:

- `peer_group_membership`
- `price_expectations`
- `attention_candidates`
- `anomaly_events`
- `attention_rollups`
- `attention_feed`

This workload depends on the core market-data layer but does not need to be on the same critical path at full breadth.

### 3. Fundamentals Job

Run daily or on a slower bounded cadence.

Owns:

- `quarterly_fundamentals`

This is the clearest candidate to remove from `equities-intraday-preload` before the large breadth jump.

## Phase Plan

### Phase 0: Baseline Stabilization

Status: completed enough to proceed, but still the reference baseline.

Scope:

- Incremental `price_history` is live.
- Current target remains `1000`.
- Existing intraday schedule remains `4x/day`.

Success condition:

- Use this phase as the comparison point for later runtime, coverage, and failure-rate changes.

### Phase 1: Expand To 2000 Symbols

Recommended changes:

- Set `EQUITY_UNIVERSE_TARGET_SIZE=2000`
- Increase job size to `2 CPU / 4 GiB`
- Keep `include_etfs=false`
- Keep `include_non_common=false`
- Keep the current `45`-day incremental lookback and `168`-hour full refresh cadence
- Keep the current weekday `4x/day` schedule

Acceptance gates:

- `universe_snapshot` materializes `2000` symbols
- `price_history` materializes `>= 2001` symbols including the benchmark
- `momentum_profiles` covers at least `98%` of the universe
- `technical_signals_latest` covers at least `98%` of the universe
- No timeouts or OOMs during a `5`-business-day soak
- Intraday job p95 runtime stays below `45` minutes

Why this phase exists:

- It gives us a clean scaling step without changing filters or cadence
- It shows whether runtime pressure is mostly in market-data computation or in downstream derivatives

### Phase 2: Expand To 3500 Symbols

Required changes before this phase:

- Split `quarterly_fundamentals` into its own job

Recommended changes:

- Set `EQUITY_UNIVERSE_TARGET_SIZE=3500`
- Increase the core intraday job to `4 CPU / 8 GiB`
- Keep ETFs and non-common shares disabled
- Keep the current schedule at `4x/day`

Acceptance gates:

- `price_history` coverage remains `>= 99%` of expected symbols
- `momentum_profiles` coverage remains `>= 98%`
- The core intraday job completes comfortably inside the schedule window
- The fundamentals job succeeds independently without affecting core intraday freshness
- No more than one retried intraday execution across a `5`-business-day soak

Why this phase exists:

- It separates the first large breadth jump from the first workload split
- It lets us validate whether the attention layer can still stay attached to the core job at mid-scale

### Phase 3: Expand To Full Common-Stock Coverage

Target:

- `EQUITY_UNIVERSE_TARGET_SIZE=5317`
- `EQUITY_UNIVERSE_INCLUDE_ETFS=false`
- `EQUITY_UNIVERSE_INCLUDE_NON_COMMON=false`

Required changes before or during this phase:

- Keep fundamentals split out
- Be prepared to split the attention / expectations datasets out of `equities-intraday-preload` if core runtime is still too high at `3500`
- Keep weekly forced full refresh behavior for stale or missing history

Recommended operating shape:

- Core intraday market-data job remains `4x/day`
- Attention / expectations runs after core completion or on a reduced cadence
- Fundamentals stays daily

Acceptance gates:

- `universe_snapshot` reaches the full common-stock target
- `price_history` covers the full target plus benchmark
- `momentum_profiles` and `technical_signals_latest` each cover at least `98%` of the universe
- Core job p95 runtime stays below `30` minutes
- No timeouts, OOMs, or persistent Alpaca throttling during a `10`-business-day soak

Why this phase is the V2 finish line:

- `5317` common stocks is the practical “almost all NASDAQ + NYSE” target under the current listing filters
- It gives materially broader exchange coverage without mixing in ETFs, warrants, rights, units, preferreds, and other special cases

### Phase 4: Optional Post-V2 Expansion

Only consider this after Phase 3 is stable.

Optional breadth targets:

- `6550` with ETFs included
- `6674` with non-common included
- `7917` with both included

This should be treated as a separate product decision, not an automatic continuation of V2.

## Operational Changes Needed For V2

### 1. Split Heavy Workloads Off The Critical Path

Move these out of the core intraday equities job first:

- `quarterly_fundamentals`

Move these out next if runtime requires it:

- `peer_group_membership`
- `price_expectations`
- `attention_candidates`
- `anomaly_events`
- `attention_rollups`
- `attention_feed`

### 2. Keep Incremental History Settings Stable

Do not widen the incremental overlap unless there is a demonstrated data-quality problem.

Keep:

- `EQUITY_PRICE_INCREMENTAL_LOOKBACK_DAYS=45`
- `EQUITY_PRICE_FULL_REFRESH_HOURS=168`
- `EQUITY_PRICE_HISTORY_TOLERANCE_DAYS=7`

Those settings already give enough overlap to correct normal late or revised bars without turning every run back into a full-history job.

### 3. Add Better Runtime And Coverage Telemetry

Track at minimum:

- total runtime per job
- runtime per stage
- symbol coverage by dataset
- number of symbols requiring full-history backfill
- Alpaca retry counts and rate-limit events
- row counts for `price_history`, `technical_signal_history`, and attention datasets

V2 expansion should be gated on measured coverage and runtime, not only on “did the job finish.”

### 4. Preserve Append-Only Historical Snapshots

Do not change the current snapshot versioning model.

The right behavior remains:

- merge old snapshot + new bars in memory
- publish a new snapshot
- keep older snapshots available for reproducibility and backtesting

## Risks

### Coverage Risk

As breadth approaches the full exchange set, more symbols will be thinly traded, newly listed, or otherwise incomplete relative to the core liquid universe. That increases the odds of:

- missing recent bars
- short history windows
- incomplete momentum or technical coverage

### Runtime Risk

The bar fetch is no longer the biggest structural issue. The remaining risk is the amount of downstream work still coupled to the intraday run.

### Provider Politeness Risk

Broader coverage means more snapshot and bar requests, more retries, and more sensitivity to provider throttling. Incremental history helps a lot, but it does not remove API-budget constraints.

### Signal Quality Risk

Full exchange coverage includes names that are less useful for intraday signal generation. We should expect lower-quality outliers in attention, anomaly, and momentum layers once fallback names dominate the marginal additions.

## Rollback Plan

If a phase is unstable:

- reduce `EQUITY_UNIVERSE_TARGET_SIZE` to the prior stable tier
- keep ETFs and non-common disabled
- keep the incremental history path in place
- revert to the prior stable image digest if needed
- rerun `universe-builder`
- rerun `equities-intraday-preload`

Because datasets are append-only snapshots, rollback should be operationally simple.

## Recommended Next Step

The next practical move is:

1. Split `quarterly_fundamentals` out of the intraday equities job.
2. Increase the core equities target from `1000` to `2000`.
3. Resize the job to `2 CPU / 4 GiB`.
4. Run a `5`-business-day soak before deciding whether to jump to `3500`.

That sequence is the shortest path to broader coverage without turning the current live success into a fragile one.
