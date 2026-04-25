# Signal Extraction Layer

**Date:** 2026-04-20
**Status:** Planned
**Goal:** Build a dense, LLM-consumable signal vocabulary on top of existing precomputed pipeline data — no new API calls required.

---

## Problem

The homepage agentic summary needs macro and market structure context to produce useful narratives. The raw data exists in the pipeline store (10-year price bars, FRED observations, momentum profiles, correlation phase shifts), but:

1. **Market Explorer** computes signals (slopes, R², z-scores, regimes) inline and returns DataFrames tuned for UI rendering — not structured for LLM consumption
2. **FRED** only extracts shallow summaries (`latest_value`, `prev_delta`, `yoy_delta`, `yoy_pct`) — no trend dynamics, acceleration, or regime classification
3. **The homepage build** reads `price_history` with a 120-day window and ignores the derivative datasets (`momentum_profiles`, `correlation_phase_shift_summary`) entirely

## What Already Exists in the Pipeline Store

| Dataset | Source | History | Refresh |
|---------|--------|---------|---------|
| `price_history` | Alpaca API bars | 3650 days (10yr) | Incremental per run |
| `momentum_profiles` | `build_momentum_profiles_from_bars()` | Derived from `price_history` | Per run |
| `correlation_phase_shift_summary` | `build_correlation_phase_shifts_from_bars()` | Derived from `price_history` | Per run |
| `correlation_phase_shift_history` | Same as above | Time series | Per run |
| `fred_summary` | `build_fred_series_summary()` | Latest + deltas | Per run |
| `fred_observations` | FRED API | Up to 10yr | Per run |

**No new API calls are needed.** All source data is already fetched and cached.

---

## Design

### New module: `compute/signal_extraction.py`

A stateless module that takes a time series and returns a compact signal dict. Both FRED and market data flow through the same extractor.

#### Core function

```python
def extract_series_signals(
    series: pd.Series,                    # values indexed by date
    *,
    windows: tuple[int, ...] = (5, 21, 63),  # short / medium / long
    zscore_lookback: int = 504,              # 2yr for equity, adapted for FRED
    frequency: str = "daily",                # "daily" | "monthly" | "quarterly" | "weekly"
) -> dict[str, object]:
```

#### Output schema (per series)

```python
{
    # Identity
    "series_id": "AAPL",           # symbol or FRED series ID
    "category": "equity",          # "equity" | "macro_anchor" | "inflation" | "labor" | etc.

    # Level
    "latest": 182.5,
    "latest_date": "2026-04-18",

    # Trend (linear fit over long window)
    "trend_dir": "rising",         # "rising" | "falling" | "flat"
    "trend_slope": 0.0012,         # log-slope (annualized where applicable)
    "trend_r2": 0.87,              # fit quality: 0 = noise, 1 = straight line
    "trend_accel": "decelerating", # slope-of-slope: "accelerating" | "decelerating" | "steady"
    "trend_accel_value": -0.0003,  # second derivative of log-slope

    # Rate of change (multi-window)
    "roc_short_pct": 1.2,          # % change over short window
    "roc_mid_pct": 4.8,            # % change over medium window
    "roc_long_pct": 12.1,          # % change over long window

    # Momentum dynamics
    "roc_of_roc": -0.05,           # acceleration/deceleration of momentum
    "momentum_score": 0.0012,      # log-slope over long window (same as Market Explorer)

    # Distribution context
    "zscore": 1.4,                 # current value vs own 2yr distribution

    # Regime
    "regime": "expanding_decelerating",  # categorical bucket (see below)
    "regime_duration": 3,                # periods in current regime
}
```

#### Regime classification

Derived from trend direction + acceleration:

| Trend | Acceleration | Regime |
|-------|-------------|--------|
| Rising | Accelerating | `expanding_accelerating` |
| Rising | Decelerating | `expanding_decelerating` |
| Rising | Steady | `expanding_steady` |
| Falling | Accelerating (getting worse faster) | `contracting_accelerating` |
| Falling | Decelerating (contraction slowing) | `contracting_decelerating` |
| Falling | Steady | `contracting_steady` |
| Flat | Any | `stable` |

#### Frequency adaptation

FRED series have different frequencies. The windows adapt:

| Frequency | Short | Medium | Long | Z-score lookback |
|-----------|-------|--------|------|-----------------|
| Daily | 5 (1w) | 21 (1m) | 63 (3m) | 504 (2yr) |
| Weekly | 4 (1m) | 13 (3m) | 52 (1yr) | 104 (2yr) |
| Monthly | 1 (1m) | 3 (3m) | 12 (1yr) | 24 (2yr) |
| Quarterly | 1 (1q) | 2 (2q) | 4 (1yr) | 8 (2yr) |

### Cross-series signals: `extract_cross_series_signals()`

For equity-vs-benchmark and equity-vs-commodity pairs. Reads directly from the existing `correlation_phase_shift_summary` and enriches:

```python
{
    "symbol": "AAPL",
    "benchmark": "SPY",

    # Already in pipeline store
    "correlation_now": 0.72,
    "correlation_roc": -0.15,
    "phase_regime": "Decoupling leader",
    "decoupling_score": 85.2,

    # New: enrichment from signal extraction
    "correlation_trend_dir": "falling",     # is correlation systematically declining?
    "correlation_r2": 0.65,                 # is the correlation decline consistent?
    "beta_divergence": 0.3,                 # current beta vs trailing beta gap
}
```

---

## Integration points

### 1. Refactor Market Explorer primitives → `compute/signal_extraction.py`

Move these private functions out of `services/market.py` into the shared compute module:

| Function | Currently in | Role |
|----------|-------------|------|
| `_log_slope()` | `market.py` | Trend slope |
| `_trend_r2()` | `market.py` | Trend consistency |
| `_window_return_pct()` | `market.py` | Rate of change |
| `_zscore()` | `market.py` | Distribution context |
| `_rolling_compound()` | `market.py` | Compounding momentum |
| `_rolling_beta()` | `market.py` | Beta calculation |
| `_sparkline()` | `market.py` | UI-only, stays in market.py |
| `_phase_regime()` | `market.py` | Stays, called from cross-series |
| `_commodity_regime()` | `market.py` | Stays, called from cross-series |

`market.py` imports from `compute/signal_extraction.py` instead of defining its own. No behavior change for Market Explorer UI.

### 2. Wire into attention home build

In `pipeline/jobs/attention_home_build.py`, `build_attention_home_output_frames()` already loads:
- `price_history` (line 719)
- `fred_summary` (line 525)

Add loads for:
- `momentum_profiles` — already persisted by the main pipeline job
- `correlation_phase_shift_summary` — already persisted
- `fred_observations` — already persisted

Then call `extract_series_signals()` over each, producing two new signal frames:
- `market_signals` — one row per symbol with the signal dict
- `fred_signals` — one row per FRED series with the signal dict

These get passed into `build_bottom_up_attention_artifacts()` → available to the LLM prompt.

### 3. LLM prompt update

In `services/attention_agentic.py`, add a new prompt section:

```
## Market Structure Signals
{formatted_market_signals}

## Macro Regime Signals
{formatted_fred_signals}

Use these signals to identify:
- Trend inflections (accelerating → decelerating transitions)
- Regime changes (expanding → contracting)
- Extreme z-scores (values > 2.0 or < -2.0)
- Correlation breaks (falling correlation + rising momentum = decoupling)
- Cross-asset narratives (e.g., "inflation decelerating while labor contracting")
```

The signal dicts are compact enough that 40 equity signals + 36 FRED signals ≈ 3-4K tokens in the prompt.

---

## File changes

| File | Change |
|------|--------|
| `compute/signal_extraction.py` | **New.** Core extraction functions + `extract_series_signals()` + `extract_cross_series_signals()` |
| `services/market.py` | Import primitives from `compute/signal_extraction.py` instead of defining locally. No behavior change. |
| `compute/fred.py` | Add `build_fred_signal_dicts()` that calls `extract_series_signals()` per FRED indicator |
| `pipeline/jobs/attention_home_build.py` | Load `momentum_profiles`, `correlation_phase_shift_summary`, `fred_observations` from pipeline store. Run signal extraction. Pass results to artifacts builder. |
| `services/attention_agentic.py` | Add market + macro signal sections to the LLM prompt |
| `services/attention_home_summary.py` | Accept signal dicts in the summary payload; format for prompt |

---

## What this does NOT change

- **Market Explorer UI** — still renders the same DataFrames, just imports primitives from `compute/` instead of local `_` functions
- **Pipeline main job** — already computes and persists everything needed; no changes
- **API calls** — zero new external calls; all data reads from pipeline store
- **FRED dashboard UI** — unchanged; signal extraction is a parallel output, not a replacement

---

## Implementation order

1. `compute/signal_extraction.py` — the core module with `extract_series_signals()`, tests
2. Refactor `services/market.py` — import shared primitives, verify no behavior change
3. `compute/fred.py` — add `build_fred_signal_dicts()` using the shared extractor
4. Wire into `pipeline/jobs/attention_home_build.py` — load datasets, extract signals, pass to summary builder
5. Update LLM prompt in `services/attention_agentic.py` — format and inject signals
6. Test end-to-end with a pipeline run and verify the agentic summary references macro/market context
