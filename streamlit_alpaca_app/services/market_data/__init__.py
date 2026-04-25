"""Market data namespace.

This package is the stable import seam for market data, fundamentals,
options, macro, universe, anomaly, and signal APIs while the underlying
modules are migrated out of their legacy locations.
"""
from __future__ import annotations

from services import fred as fred  # noqa: F401
from services import fundamentals as fundamentals  # noqa: F401
from services import market as market  # noqa: F401
from services import options as options  # noqa: F401
from services import treasury_yields as treasury_yields  # noqa: F401
from services import universe as universe  # noqa: F401
from compute.anomalies import (
    AttentionConfig,
    ExpectationConfig,
    HORIZON_PERIODS,
    build_attention_candidates,
    build_attention_feed,
    build_attention_rollups,
    build_commodity_peer_group_membership,
    build_peer_group_membership,
    build_price_expectations,
    build_taxonomy_peer_group_catalog,
    detect_anomaly_events,
    filter_attention_events,
    normalize_horizon,
    normalize_horizons,
)
from services.market import (
    build_correlation_phase_shifts_from_bars,
    build_momentum_profiles_from_bars,
    commodity_reference_universe,
    default_commodity_proxy_symbols,
    scan_commodity_regimes,
    scan_correlation_phase_shifts,
    scan_daily_movers,
    scan_momentum_profiles,
)

__all__ = [
    "AttentionConfig",
    "ExpectationConfig",
    "HORIZON_PERIODS",
    "build_attention_candidates",
    "build_attention_feed",
    "build_attention_rollups",
    "build_commodity_peer_group_membership",
    "build_correlation_phase_shifts_from_bars",
    "build_momentum_profiles_from_bars",
    "build_peer_group_membership",
    "build_price_expectations",
    "build_taxonomy_peer_group_catalog",
    "commodity_reference_universe",
    "default_commodity_proxy_symbols",
    "detect_anomaly_events",
    "filter_attention_events",
    "fred",
    "fundamentals",
    "market",
    "normalize_horizon",
    "normalize_horizons",
    "options",
    "scan_commodity_regimes",
    "scan_correlation_phase_shifts",
    "scan_daily_movers",
    "scan_momentum_profiles",
    "treasury_yields",
    "universe",
]
