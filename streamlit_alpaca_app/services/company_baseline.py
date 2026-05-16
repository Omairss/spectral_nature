from __future__ import annotations

import os
import time
from typing import Any

import pandas as pd

from services.company import build_company_description
from services.entity_taxonomy import dashboard_business_lens_from_taxonomy_row, taxonomy_lookup_by_symbol

THIN_NARRATIVE_SENTENCE = (
    "The current narrative is still thin, so the best read comes from the linked price action and recent headlines."
)


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _normalize_symbol(value: object) -> str:
    return _coerce_text(value).upper()


def _company_name_from_row(row: dict[str, Any], symbol: str) -> str:
    for key in ("name", "company_name", "security_name", "asset_name"):
        value = _coerce_text(row.get(key))
        if value and value.upper() != symbol.upper():
            return value
    return symbol


def _baseline_only_description(text: object) -> str:
    cleaned = _coerce_text(text)
    if not cleaned:
        return ""
    return cleaned.replace(THIN_NARRATIVE_SENTENCE, "").strip()


def _request_delay_seconds() -> float:
    raw = _coerce_text(os.getenv("COMPANY_BASELINE_REQUEST_DELAY_SECONDS")) or "0.5"
    try:
        value = float(raw)
    except Exception:
        value = 0.5
    return max(value, 0.0)


def build_company_baseline_frame(
    universe_frame: pd.DataFrame,
    *,
    symbols: list[str] | None = None,
    limit: int = 50,
    asof_time_utc: object = "",
    run_id: str = "",
) -> pd.DataFrame:
    """Build slow-changing company baseline rows for a capped ticker set.

    This intentionally avoids broad daily web search. The description helper uses
    low-cost company metadata, taxonomy labels, and a bounded Wikipedia summary
    lookup when available.
    """
    if not isinstance(universe_frame, pd.DataFrame) or universe_frame.empty or "symbol" not in universe_frame.columns:
        return pd.DataFrame()

    source = universe_frame.copy()
    source["symbol"] = source["symbol"].astype(str).str.upper().str.strip()
    source = source[source["symbol"].ne("")]
    source = source.drop_duplicates(subset=["symbol"], keep="first")

    requested = [_normalize_symbol(symbol) for symbol in list(symbols or []) if _normalize_symbol(symbol)]
    if requested:
        source = source[source["symbol"].isin(set(requested))].copy()

    source = source.head(max(int(limit), 1)).reset_index(drop=True)
    if source.empty:
        return pd.DataFrame()

    ordered_symbols = source["symbol"].astype(str).tolist()
    taxonomy_lookup = taxonomy_lookup_by_symbol(ordered_symbols)
    asof_label = _coerce_text(pd.to_datetime(asof_time_utc, utc=True, errors="coerce").isoformat()) if _coerce_text(asof_time_utc) else ""
    request_delay_seconds = _request_delay_seconds()

    rows: list[dict[str, object]] = []
    source_rows = list(source.iterrows())
    for row_index, (_, row) in enumerate(source_rows):
        row_dict = row.to_dict()
        symbol = _normalize_symbol(row_dict.get("symbol"))
        if not symbol:
            continue
        company_name = _company_name_from_row(row_dict, symbol)
        business_lens = dashboard_business_lens_from_taxonomy_row(taxonomy_lookup.get(symbol))
        description = build_company_description(
            symbol,
            {"name": company_name},
            {},
            {},
            news_payload={"articles": pd.DataFrame()},
            active_lens=business_lens,
        )
        description_text = _baseline_only_description(description)
        rows.append(
            {
                "symbol": symbol,
                "company_name": company_name,
                "business_lens": business_lens,
                "company_background_text": description_text,
                "description_text": description_text,
                "baseline_source": "company_baseline_prefetch",
                "run_id": _coerce_text(run_id),
                "asof_time_utc": asof_label,
            }
        )
        if request_delay_seconds and row_index < len(source_rows) - 1:
            time.sleep(request_delay_seconds)

    return pd.DataFrame(rows)


def deserialize_company_baseline_frame(frame: pd.DataFrame, symbol: str) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns:
        return {}
    target = _normalize_symbol(symbol)
    if not target:
        return {}
    rows = frame.copy()
    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
    match = rows[rows["symbol"] == target].head(1)
    if match.empty:
        return {}
    return match.iloc[0].to_dict()


__all__ = ["build_company_baseline_frame", "deserialize_company_baseline_frame"]
