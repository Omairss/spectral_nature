from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import LinkedAuth


DEFAULT_SQL_SERVER = "spectral-nature-server.database.windows.net"
DEFAULT_SQL_DATABASE = "spectral-nature-db"
DEFAULT_SQL_USERNAME = "sn-sql-db"
DEFAULT_SQL_DRIVER = "{ODBC Driver 17 for SQL Server}"
DEFAULT_SQL_PASSWORD_SECRET_NAME = "legacy-azure-sql-admin-password"
DEFAULT_FRED_SECRET_NAME = "Fred"


@dataclass(frozen=True)
class SqlConnectionConfig:
    server: str
    database: str
    username: str
    password: str
    driver: str


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def sql_connection_config() -> SqlConnectionConfig:
    return SqlConnectionConfig(
        server=_clean(os.getenv("AZURE_SQL_SERVER")) or DEFAULT_SQL_SERVER,
        database=_clean(os.getenv("AZURE_SQL_DATABASE")) or DEFAULT_SQL_DATABASE,
        username=_clean(os.getenv("AZURE_SQL_USERNAME")) or DEFAULT_SQL_USERNAME,
        password=LinkedAuth.get_required_secret_value(
            _clean(os.getenv("AZURE_SQL_PASSWORD_SECRET_NAME")) or DEFAULT_SQL_PASSWORD_SECRET_NAME,
            key_vault_name=_clean(os.getenv("AZURE_SQL_KEY_VAULT_NAME")),
            env_names=["AZURE_SQL_PASSWORD"],
        ),
        driver=_clean(os.getenv("AZURE_SQL_ODBC_DRIVER")) or DEFAULT_SQL_DRIVER,
    )


def build_sql_odbc_connection_string() -> str:
    explicit = _clean(os.getenv("AZURE_SQL_ODBC_CONNECTION_STRING"))
    if explicit:
        return explicit
    config = sql_connection_config()
    return (
        f"DRIVER={config.driver};"
        f"SERVER={config.server};"
        f"DATABASE={config.database};"
        f"UID={config.username};"
        f"PWD={config.password};"
    )


def get_fred_api_key() -> str:
    return LinkedAuth.get_required_secret_value(
        _clean(os.getenv("FRED_KEY_VAULT_SECRET")) or DEFAULT_FRED_SECRET_NAME,
        key_vault_name=_clean(os.getenv("FRED_KEY_VAULT_NAME")),
        env_names=["FRED_API_KEY"],
    )
