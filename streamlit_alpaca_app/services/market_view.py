from __future__ import annotations

from typing import Any

from services.market import (
    business_focus_catalog,
    commodity_focus_description,
    commodity_focus_options,
    commodity_focus_universe,
)


HORIZONS: tuple[tuple[str, str, str], ...] = (
    ("1d", "1 Day", "return_1d_pct"),
    ("7d", "7 Day", "return_7d_pct"),
    ("1m", "1 Month", "return_1m_pct"),
    ("3m", "3 Month", "return_3m_pct"),
    ("1yr", "1 Year", "return_1y_pct"),
    ("5yr", "5 Year", "return_5y_pct"),
)


def _lenses(
    names: list[str],
    description: Any,
    universe: Any,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name in names:
        symbols = [str(value).upper().strip() for value in universe(name) if str(value).strip()]
        result.append(
            {
                "name": str(name),
                "description": str(description(name) or "").strip(),
                "symbol_count": len(symbols),
                "symbols": symbols[:1500],
            }
        )
    return result


def build_market_lenses_view() -> dict[str, Any]:
    business = business_focus_catalog()
    return {
        "business": [
            {
                "name": str(item.get("name") or ""),
                "description": str(item.get("description") or ""),
                "symbol_count": len(item.get("symbols") or []),
                "symbols": list(item.get("symbols") or [])[:1500],
            }
            for item in business
        ],
        "commodities": _lenses(
            commodity_focus_options(),
            commodity_focus_description,
            commodity_focus_universe,
        ),
        "horizons": [
            {"key": key, "label": label, "column": column}
            for key, label, column in HORIZONS
        ],
    }


__all__ = ["HORIZONS", "build_market_lenses_view"]
