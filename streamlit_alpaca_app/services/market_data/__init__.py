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

__all__ = [
    "fred",
    "fundamentals",
    "market",
    "options",
    "treasury_yields",
    "universe",
]
