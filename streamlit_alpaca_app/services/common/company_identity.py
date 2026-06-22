from __future__ import annotations

import re
from typing import Any


_INSTRUMENT_SUFFIXES = (
    "class a common stock",
    "class b common stock",
    "class c common stock",
    "common stock",
    "ordinary shares",
    "american depositary shares",
    "american depositary share",
    "ads",
    "adr",
)

_TRAILING_INSTRUMENT_PATTERN = re.compile(
    r"\s*[-|:,]?\s*(?:new\s+)?(?:common\s+stock|ordinary\s+shares|american\s+depositary\s+shares?|ads|adr)\s*$",
    re.IGNORECASE,
)

_TRAILING_CLASS_PATTERN = re.compile(
    r"\s*[-|:,]?\s*class\s+[a-z0-9-]+\s*$",
    re.IGNORECASE,
)

_TRAILING_EXCHANGE_PATTERN = re.compile(
    r"\s*\((?:nasdaq|nyse|amex|otc|tsx|lse)\s*:[^)]+\)\s*$",
    re.IGNORECASE,
)


def clean_company_display_name(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text or text.lower() == "nan":
        return ""
    text = _TRAILING_EXCHANGE_PATTERN.sub("", text).strip()
    lowered = text.lower()
    for suffix in _INSTRUMENT_SUFFIXES:
        if lowered.endswith(suffix):
            text = text[: -len(suffix)].rstrip(" -|:,")
            lowered = text.lower()
            break
    text = _TRAILING_INSTRUMENT_PATTERN.sub("", text).strip()
    text = _TRAILING_CLASS_PATTERN.sub("", text).strip()
    return text.strip(" -|:,")


__all__ = ["clean_company_display_name"]
