from __future__ import annotations

from pathlib import Path
import os
from typing import Any

import pandas as pd

from compute.fundamentals import load_quarterly_fundamentals
from .secrets import get_secret_value_from_vault, resolve_secret_value


try:
    import simfin as sf
except Exception:
    sf = None


SIMFIN_ENV_NAMES = ["SIMFIN_API_KEY"]
SIMFIN_ALT_VAULT_NAMES = ["SIMFIN_KEY_VAULT_NAME", "SIMFIN_VAULT_NAME"]
SIMFIN_ALT_VAULT_URLS = ["SIMFIN_KEY_VAULT_URL", "SIMFIN_VAULT_URL"]
SIMFIN_ALT_SECRET_NAMES = ["SIMFIN_API_KEY_SECRET", "SIMFIN_SECRET_NAME"]


def load_simfin_api_key() -> str:
    direct = resolve_secret_value(
        SIMFIN_ENV_NAMES,
        secret_name_env="SIMFIN_API_KEY_SECRET",
        default_secret_name="SimFinAPI",
    )
    if direct:
        return direct

    vault_name = ""
    for env_name in SIMFIN_ALT_VAULT_NAMES:
        vault_name = (os.getenv(env_name) or "").strip()
        if vault_name:
            break
    if not vault_name:
        vault_name = "spectral-nature-kvault"

    vault_url = ""
    for env_name in SIMFIN_ALT_VAULT_URLS:
        vault_url = (os.getenv(env_name) or "").strip()
        if vault_url:
            break

    secret_names: list[str] = []
    for env_name in SIMFIN_ALT_SECRET_NAMES:
        secret_name = (os.getenv(env_name) or "").strip()
        if secret_name and secret_name not in secret_names:
            secret_names.append(secret_name)
    for default_name in ["SimFinAPI", "simfin-api-key"]:
        if default_name not in secret_names:
            secret_names.append(default_name)

    for secret_name in secret_names:
        value = str(
            get_secret_value_from_vault(
                secret_name,
                vault_name=vault_name,
                vault_url=vault_url,
            )
            or ""
        ).strip()
        if value:
            return value
    return ""


def simfin_refresh_configured() -> bool:
    return bool(sf is not None and load_simfin_api_key())


def simfin_data_dir() -> str:
    explicit = (os.getenv("SIMFIN_REFRESH_DATA_DIR") or "").strip()
    if explicit:
        return str(Path(explicit).expanduser())
    app_root = Path(__file__).resolve().parents[1]
    return str((app_root / "cache" / "data" / "simfin_refresh").resolve())


def refresh_simfin_quarterly_cache(
    *,
    refresh_days: int = 1,
    data_dir: str | None = None,
) -> dict[str, Any]:
    if sf is None:
        raise RuntimeError("simfin package is not installed")

    api_key = load_simfin_api_key()
    if not api_key:
        raise RuntimeError("SIMFIN_API_KEY is not configured")

    target_dir = str(data_dir or simfin_data_dir()).strip()
    Path(target_dir).mkdir(parents=True, exist_ok=True)

    sf.set_api_key(api_key)
    sf.set_data_dir(target_dir)

    # These calls refresh the local CSV cache in SimFin's expected on-disk format.
    sf.load_income(variant="quarterly", market="us", refresh_days=max(int(refresh_days), 0))
    sf.load_balance(variant="quarterly", market="us", refresh_days=max(int(refresh_days), 0))
    sf.load_cashflow(variant="quarterly", market="us", refresh_days=max(int(refresh_days), 0))

    return {
        "provider": "simfin",
        "data_dir": target_dir,
        "refresh_days": max(int(refresh_days), 0),
    }


def build_quarterly_fundamentals_frame(
    symbols: list[str],
    *,
    prefer_upstream: bool = True,
    refresh_days: int = 1,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    normalized_symbols = list(dict.fromkeys(str(symbol or "").upper().strip() for symbol in symbols if str(symbol or "").strip()))
    details: dict[str, Any] = {"provider": "local", "data_dir": "", "refresh_days": None}
    data_dir: str | None = None

    if prefer_upstream and simfin_refresh_configured():
        details = refresh_simfin_quarterly_cache(refresh_days=refresh_days)
        data_dir = str(details.get("data_dir") or "").strip() or None

    fundamentals_parts: list[pd.DataFrame] = []
    for symbol in normalized_symbols:
        bundle = load_quarterly_fundamentals(symbol, data_dir=data_dir)
        for statement in ("income", "balance", "cashflow"):
            statement_frame = bundle.get(statement, pd.DataFrame())
            if statement_frame is None or statement_frame.empty:
                continue
            chunk = statement_frame.copy()
            if "ticker" not in chunk.columns:
                chunk["ticker"] = symbol
            if "statement" not in chunk.columns:
                chunk["statement"] = statement
            fundamentals_parts.append(chunk)

    fundamentals = pd.concat(fundamentals_parts, ignore_index=True) if fundamentals_parts else pd.DataFrame()
    if not fundamentals.empty:
        dedupe_cols = [col for col in ["ticker", "statement", "metric", "report_date"] if col in fundamentals.columns]
        if dedupe_cols:
            fundamentals = fundamentals.drop_duplicates(subset=dedupe_cols, keep="last")

    return fundamentals, details


__all__ = [
    "build_quarterly_fundamentals_frame",
    "load_simfin_api_key",
    "refresh_simfin_quarterly_cache",
    "simfin_data_dir",
    "simfin_refresh_configured",
]
