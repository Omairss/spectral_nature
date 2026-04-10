from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .secrets import resolve_secret_value

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


DEFAULT_ALPACA_API_KEY_SECRET_NAME = "apca-api-key"
DEFAULT_ALPACA_API_SECRET_KEY_SECRET_NAME = "apca-api-secret-key"


def _env_secret_name(env_names: tuple[str, ...], default: str) -> str:
    for env_name in env_names:
        value = (os.getenv(env_name) or "").strip()
        if value:
            return value
    return default


def alpaca_secret_name_settings() -> tuple[str, str]:
    return (
        _env_secret_name(("APCA_API_KEY_SECRET_NAME", "APCA_API_KEY_SECRET"), DEFAULT_ALPACA_API_KEY_SECRET_NAME),
        _env_secret_name(
            ("APCA_API_SECRET_KEY_SECRET_NAME", "APCA_API_SECRET_KEY_SECRET"),
            DEFAULT_ALPACA_API_SECRET_KEY_SECRET_NAME,
        ),
    )



def load_config() -> AppConfig | None:
    if load_dotenv is not None:
        app_root = Path(__file__).resolve().parents[1]
        env_file = app_root / ".env"
        load_dotenv(dotenv_path=env_file, override=False)
        load_dotenv(override=False)

    api_placeholders = {"your_key_here"}
    secret_placeholders = {"your_secret_here"}
    api_key_secret_name, secret_key_secret_name = alpaca_secret_name_settings()

    api_key = resolve_secret_value(
        [],
        default_secret_name=api_key_secret_name,
        placeholders=api_placeholders,
    )
    secret_key = resolve_secret_value(
        [],
        default_secret_name=secret_key_secret_name,
        placeholders=secret_placeholders,
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
