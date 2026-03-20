# Attention Layer Implementation Plan

## Goal

Move the platform from "many charts and tables" to "what needs attention now".

In this repo, that means adding a first-class anomaly layer on top of the current compute layer and publishing its output as a ranked attention feed on the Home page.

Current architecture already supports this shape:

- precomputed pipeline datasets in `pipeline/jobs/main.py`
- materialized-first resolution in `data_access/layer.py`
- shared query contracts in `data_access/query_service.py`
- an underused Home entrypoint in `app.py`

The missing piece is a dataset family that answers:

1. What happened?
2. Compared to what expectation?
3. How surprising is it?
4. Why does it matter?
5. Where should the user click next?

## Phase 1 Scope

Start with equities only.

Inputs already available:

- `price_history`
- `momentum_profiles`
- `correlation_phase_shift_summary`
- `technical_signals_latest`
- `technical_signal_history`
- `news_articles`
- `news_symbol_map`
- optional live `positions`

Initial peer definitions:

- business lenses from `services/market.py`
- benchmark linkage from `SPY`
- portfolio holdings as a relevance overlay

Initial horizons:

- `1d`
- `1w`
- `1m`

## Target Architecture

Phase 1 read path:

`pipeline datasets -> compute/anomalies.py -> anomaly datasets -> DAL/query service -> Home feed + drilldowns`

New materialized datasets:

1. `peer_group_membership`
2. `price_expectations`
3. `anomaly_events`
4. `attention_feed`
5. `attention_rollups`

These datasets must follow the same append-only, versioned semantics already described in `PIPELINE_REDESIGN.md`.

## Exact Dataset Schemas

### 1) `peer_group_membership`

Purpose:

- define the peer relationships used in expectation and rollups

Columns:

- `asof_time_utc`: timestamp
- `entity_type`: string
- `entity_id`: string
- `peer_group_id`: string
- `peer_group_name`: string
- `peer_group_type`: string
- `benchmark`: string
- `membership_weight`: float
- `source`: string
- `schema_version`: string

Notes:

- `entity_type` is `symbol` in Phase 1
- `peer_group_type` starts as `business_lens`
- `membership_weight` defaults to `1.0`
- `source` should record `business_focus_universe_v1`

### 2) `price_expectations`

Purpose:

- capture observed vs expected behavior before thresholding into events

Grain:

- one row per `symbol x horizon x asof_time_utc`

Columns:

- `asof_time_utc`: timestamp
- `symbol`: string
- `horizon`: string
- `close`: float
- `observed_return_pct`: float
- `trend_expected_return_pct`: float
- `peer_expected_return_pct`: float
- `benchmark_expected_return_pct`: float
- `blended_expected_return_pct`: float
- `residual_return_pct`: float
- `residual_zscore`: float
- `trend_zscore`: float
- `peer_zscore`: float
- `benchmark_zscore`: float
- `vol_20_ann_pct`: float
- `momentum_score`: float
- `momentum_roc_score`: float
- `correlation_now`: float
- `correlation_roc`: float
- `peer_group_id`: string
- `peer_group_name`: string
- `benchmark`: string
- `trajectory_model_version`: string
- `peer_model_version`: string
- `schema_version`: string

Expectation definition for Phase 1:

- `trend_expected_return_pct`: continuation estimate from trailing trajectory
- `peer_expected_return_pct`: expected move implied by peer basket return
- `benchmark_expected_return_pct`: expected move implied by benchmark beta
- `blended_expected_return_pct`: weighted blend of the three
- `residual_return_pct = observed_return_pct - blended_expected_return_pct`
- `residual_zscore`: residual normalized by rolling residual dispersion

### 3) `anomaly_events`

Purpose:

- convert raw residuals into explainable events

Grain:

- one row per anomaly candidate

Columns:

- `event_id`: string
- `asof_time_utc`: timestamp
- `entity_type`: string
- `entity_id`: string
- `parent_entity_type`: string
- `parent_entity_id`: string
- `horizon`: string
- `anomaly_type`: string
- `direction`: string
- `observed_value`: float
- `expected_value`: float
- `residual_value`: float
- `residual_zscore`: float
- `severity_score`: float
- `impact_score`: float
- `relevance_score`: float
- `confidence_score`: float
- `attention_score`: float
- `persistence_score`: float
- `novelty_score`: float
- `portfolio_exposure_weight`: float
- `peer_group_id`: string
- `peer_group_name`: string
- `benchmark`: string
- `regime_label`: string
- `why_now_code`: string
- `why_now_text`: string
- `supporting_datasets`: string
- `linked_news_count`: int
- `linked_news_ids`: string
- `drilldown_section`: string
- `drilldown_params_json`: string
- `status`: string
- `schema_version`: string

Allowed `anomaly_type` values in Phase 1:

- `price_residual`
- `momentum_acceleration`
- `momentum_deceleration`
- `correlation_break`
- `decoupling`
- `technical_regime_shift`
- `news_confirmed_move`

Allowed `status` values:

- `active`
- `cooling`
- `resolved`

Scoring guidance:

- `severity_score`: how abnormal the residual is
- `impact_score`: how much this should matter if true
- `relevance_score`: whether this matters to the current user context
- `confidence_score`: whether multiple signals agree
- `attention_score`: final ranking score shown in feed

Recommended Phase 1 formula:

- `severity_score = min(abs(residual_zscore) / 4.0, 1.0) * 100`
- `impact_score = weighted(portfolio_exposure, liquidity proxy, peer breadth, move size)`
- `relevance_score = 100 if symbol is held, else 70 if in active lens, else 40`
- `confidence_score = weighted(technical confirmation, peer confirmation, news confirmation, persistence)`
- `attention_score = 0.40 * severity_score + 0.25 * impact_score + 0.20 * relevance_score + 0.15 * confidence_score`

### 4) `attention_feed`

Purpose:

- UI-ready ranked cards for Home

Grain:

- one row per feed card

Columns:

- `feed_rank`: int
- `event_id`: string
- `asof_time_utc`: timestamp
- `card_type`: string
- `title`: string
- `subtitle`: string
- `entity_type`: string
- `entity_id`: string
- `attention_score`: float
- `severity_score`: float
- `impact_score`: float
- `why_now_text`: string
- `expected_vs_observed_text`: string
- `next_best_action`: string
- `drilldown_section`: string
- `drilldown_params_json`: string
- `linked_news_count`: int
- `status`: string
- `schema_version`: string

Examples:

- "NVDA is outperforming its peer basket by 4.8% over 5 trading days"
- "Copper-linked names are decoupling from SPY"
- "Portfolio attention: AAPL move is unexplained by peers and benchmark"

### 5) `attention_rollups`

Purpose:

- higher-order summary by lens, portfolio, and market structure

Grain:

- one row per `rollup_type x rollup_id x asof_time_utc`

Columns:

- `asof_time_utc`: timestamp
- `rollup_type`: string
- `rollup_id`: string
- `rollup_name`: string
- `active_event_count`: int
- `high_priority_event_count`: int
- `top_event_id`: string
- `top_attention_score`: float
- `breadth_positive`: int
- `breadth_negative`: int
- `mean_residual_zscore`: float
- `net_attention_score`: float
- `summary_text`: string
- `schema_version`: string

Initial `rollup_type` values:

- `portfolio`
- `business_lens`
- `market`

## Phase 1 Expectation Models

### Trajectory expectation

Use current price and technical derivatives as inputs:

- trailing `1w`, `1m`, `3m` returns
- `momentum_score`
- `momentum_roc_score`
- `vol_20_ann_pct`
- `channel_position`

Simple initial model:

- `trend_expected_return_pct = 0.20 * return_1w_pct + 0.35 * return_1m_pct + 0.45 * return_3m_pct`
- volatility-adjusted and clipped by recent dispersion

### Peer expectation

For each symbol:

- map to a peer group from `BUSINESS_FOCUS_UNIVERSES`
- compute equal-weight peer basket return excluding the symbol
- optionally estimate a rolling beta to peer basket

Simple initial model:

- `peer_expected_return_pct = peer_beta * peer_group_return_pct`

### Benchmark expectation

Use existing `correlation_phase_shift_summary` outputs.

Simple initial model:

- estimate `benchmark_beta` from recent return history
- `benchmark_expected_return_pct = benchmark_beta * benchmark_return_pct`

### Blending

Recommended Phase 1 blend:

- `0.40 * trend_expected_return_pct`
- `0.35 * peer_expected_return_pct`
- `0.25 * benchmark_expected_return_pct`

Adjust weights later through replay evaluation.

## Linking News To Anomalies

Current news ingestion is useful but not enough for event linking.

Required additions:

- stable `article_uid`
- normalized `symbol`
- `published_at`
- optional `sentiment`
- optional `headline_topic`

Phase 1 `article_uid` rule:

- hash of `url` if present
- else hash of `headline + published_at + source`

News confirmation logic:

- event is `news_confirmed_move` if there is at least one recent article for the same symbol within a configurable lookback window
- news adds to `confidence_score`, not directly to `severity_score`

## First `compute/anomalies.py` API

This is the first concrete API to build.

```python
from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class ExpectationConfig:
    residual_lookback_days: int = 252
    horizons: tuple[str, ...] = ("1d", "1w", "1m")
    trend_weight: float = 0.40
    peer_weight: float = 0.35
    benchmark_weight: float = 0.25
    min_history_rows: int = 63
    schema_version: str = "v1"


@dataclass(frozen=True)
class AttentionConfig:
    residual_zscore_threshold: float = 2.0
    high_priority_threshold: float = 75.0
    news_lookback_days: int = 3
    persistence_periods: int = 2
    schema_version: str = "v1"


def build_peer_group_membership(*, asof_time_utc: pd.Timestamp) -> pd.DataFrame:
    """Return the symbol-to-peer-group mapping used by expectation models."""


def build_price_expectations(
    price_history: pd.DataFrame,
    momentum_profiles: pd.DataFrame,
    correlation_phase_shift_summary: pd.DataFrame,
    peer_group_membership: pd.DataFrame,
    *,
    config: ExpectationConfig,
) -> pd.DataFrame:
    """
    Return the `price_expectations` dataset.

    Expected inputs:
    - price_history: symbol, timestamp, close
    - momentum_profiles: symbol, return_1w_pct, return_1m_pct, return_3m_pct,
      momentum_score, momentum_roc_score
    - correlation_phase_shift_summary: symbol, benchmark, correlation_now, correlation_roc
    - peer_group_membership: entity_id, peer_group_id, peer_group_name, benchmark
    """


def detect_anomaly_events(
    price_expectations: pd.DataFrame,
    technical_signals_latest: pd.DataFrame | None = None,
    news_symbol_map: pd.DataFrame | None = None,
    positions: pd.DataFrame | None = None,
    *,
    config: AttentionConfig,
) -> pd.DataFrame:
    """
    Return the `anomaly_events` dataset.

    This function thresholds expectation residuals and enriches them with
    technical, news, and portfolio relevance signals.
    """


def build_attention_rollups(
    anomaly_events: pd.DataFrame,
    peer_group_membership: pd.DataFrame,
) -> pd.DataFrame:
    """Return the `attention_rollups` dataset."""


def build_attention_feed(
    anomaly_events: pd.DataFrame,
    attention_rollups: pd.DataFrame,
    *,
    top_n: int = 20,
) -> pd.DataFrame:
    """Return the UI-ready `attention_feed` dataset."""
```

## Repo Changes By File

### New file

- `compute/anomalies.py`

### Pipeline

Update `pipeline/jobs/main.py`:

- after `price_history`, `momentum_profiles`, `correlation_phase_shift_summary`, and `technical_signals_latest` are produced
- build and persist:
  - `peer_group_membership`
  - `price_expectations`
  - `anomaly_events`
  - `attention_rollups`
  - `attention_feed`

### Pipeline store

Update `services/pipeline_store.py`:

- add the new datasets to `SOURCE_DATASETS["derivatives"]`
- optionally add an `attention` source group later if this becomes a separate job

### DAL

Update `data_access/layer.py` with:

- `resolve_attention_feed`
- `resolve_attention_rollups`
- `resolve_anomaly_events`

These should be `materialized_first` just like current market derivatives.

### Query layer

Update `data_access/query_service.py`:

- add dataset capabilities for:
  - `attention_feed`
  - `attention_rollups`
  - `anomaly_events`
- optionally add a chart capability for an anomaly timeline later

### UI

Update `app.py`:

- replace the current passive Home page with:
  - `Top Attention Now`
  - `Portfolio Attention`
  - `Lens Rollups`
  - `Recently Resolved`

Feed card minimum fields:

- title
- why now
- expected vs observed
- confidence
- next click

## Delivery Sequence

### PR 1

- add `compute/anomalies.py`
- add unit tests for expectation and event scoring logic

### PR 2

- wire anomaly dataset generation into `pipeline/jobs/main.py`
- materialize new datasets

### PR 3

- expose new datasets through `data_access/layer.py` and `data_access/query_service.py`

### PR 4

- build Home attention feed in `app.py`

### PR 5

- add replay evaluation and false-positive review
- optionally add user feedback signals such as `watch`, `dismiss`, and `important`

## Phase 1 Success Criteria

- Home page answers "where should I look first?" without forcing table exploration
- every feed card cites `observed vs expected`
- held positions rank above non-held symbols when scores are otherwise similar
- major decoupling and correlation-break moves appear in the top feed
- feed can be reproduced from materialized datasets for a historical `asof_time_utc`

## Recommended Non-Goals For Phase 1

- no LLM-written market commentary in the core ranking loop
- no options anomalies yet
- no macro anomaly fusion yet
- no adaptive learning-to-rank yet

Keep Phase 1 deterministic and inspectable.
