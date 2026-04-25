# Market Data Architecture

Market Data owns raw and derived market datasets used by Attention, AQL, agents, data pipelines, and UI.

## Ownership

This module owns:

- price and volume data
- universe and liquidity selection
- fundamentals and statement-derived metrics
- options surfaces
- macro and rate datasets
- ownership-derived metrics
- anomaly detection and signal extraction

## Current Code

- `services/market.py`
- `services/fundamentals.py`
- `services/options.py`
- `services/fred.py`
- `services/treasury_yields.py`
- `services/universe.py`
- `compute/anomalies.py`
- `compute/fundamentals.py`
- `compute/signals.py`
- `compute/signal_extraction.py`
- `compute/treasury_yields.py`
- `compute/ownership.py`

## Target Contract

Consumers should call market data through stable loaders and compute APIs. Attention, AQL, agents, and UI should not duplicate raw market transforms.

Current stable imports live in `services.market_data` and include:

- anomaly detection: `detect_anomaly_events`
- attention candidates: `build_price_expectations`, `build_attention_candidates`, `build_attention_feed`, `build_attention_rollups`
- peer groups: `build_peer_group_membership`, `build_commodity_peer_group_membership`
- correlation formation and breaks: `build_correlation_phase_shifts_from_bars`, `scan_correlation_phase_shifts`
- regimes and momentum: `scan_commodity_regimes`, `scan_momentum_profiles`, `build_momentum_profiles_from_bars`

## Boundary Rules

- Market Data may call vendor clients, data access, compute transforms, and runtime config.
- Market Data should not call AQL, agents, or UI.
- Attention-specific event language belongs in Attention or AQL, not Market Data.
- UI chart rendering belongs in presentation or UI layers.

## Migration Steps

1. Use `services.market_data` as the stable namespace while legacy files remain in place.
2. Move Plotly rendering out of market/fundamental services when a neutral chart model exists.
3. Move reusable anomaly and signal extraction functions behind explicit public exports.
4. Retarget Attention and agents to public market data APIs. Status: started for pipeline and agent anomaly checks.
5. Move files under `services/market_data/` after callers use the namespace.
