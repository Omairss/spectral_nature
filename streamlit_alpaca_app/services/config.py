from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


@dataclass(frozen=True)
class AppConfig:
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_trading_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_base_url: str = "https://data.alpaca.markets"


def _clean_credential(value: str | None, placeholders: set[str]) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if cleaned.lower() in placeholders:
        return ""
    return cleaned



def load_config() -> AppConfig | None:
    if load_dotenv is not None:
        app_root = Path(__file__).resolve().parents[1]
        env_file = app_root / ".env"
        load_dotenv(dotenv_path=env_file, override=False)
        load_dotenv(override=False)

    api_placeholders = {"your_key_here"}
    secret_placeholders = {"your_secret_here"}

    api_key = (
        _clean_credential(os.getenv("APCA_API_KEY_ID"), api_placeholders)
        or _clean_credential(os.getenv("APCA_API_KEY"), api_placeholders)
        or _clean_credential(os.getenv("ALPACA_API_KEY"), api_placeholders)
    )
    secret_key = (
        _clean_credential(os.getenv("APCA_API_SECRET_KEY"), secret_placeholders)
        or _clean_credential(os.getenv("ALPACA_SECRET_KEY"), secret_placeholders)
    )

    if not api_key or not secret_key:
        return None

    trading_url = (
        os.getenv("APCA_API_BASE_URL")
        or os.getenv("ALPACA_TRADING_BASE_URL")
        or "https://paper-api.alpaca.markets"
    ).strip()
    data_url = os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").strip()

    return AppConfig(
        alpaca_api_key=api_key,
        alpaca_secret_key=secret_key,
        alpaca_trading_base_url=trading_url,
        alpaca_data_base_url=data_url,
    )
