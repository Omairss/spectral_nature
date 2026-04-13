from __future__ import annotations

import getpass
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

try:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
except Exception:
    DefaultAzureCredential = None
    SecretClient = None


DEFAULT_KEY_VAULT_NAME = "spectral-nature-kvault"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def _default_key_vault_name() -> str:
    return (
        _clean(os.getenv("KEYVAULT_NAME"))
        or _clean(os.getenv("AZURE_KEY_VAULT_NAME"))
        or _clean(os.getenv("KEY_VAULT_NAME"))
        or DEFAULT_KEY_VAULT_NAME
    )


@lru_cache(maxsize=8)
def _build_secret_client(key_vault_name: str):
    if SecretClient is None or DefaultAzureCredential is None:
        return None
    try:
        return SecretClient(
            vault_url=f"https://{key_vault_name}.vault.azure.net",
            credential=DefaultAzureCredential(),
        )
    except Exception:
        return None


def get_secret_value(
    secret_name: str,
    *,
    key_vault_name: str = "",
    env_names: list[str] | tuple[str, ...] | None = None,
) -> str:
    for env_name in list(env_names or []):
        value = _clean(os.getenv(env_name))
        if value:
            return value

    resolved_secret_name = _clean(secret_name)
    if not resolved_secret_name:
        return ""

    client = _build_secret_client(_clean(key_vault_name) or _default_key_vault_name())
    if client is None:
        return ""
    try:
        return _clean(client.get_secret(resolved_secret_name).value)
    except Exception:
        return ""


def get_required_secret_value(
    secret_name: str,
    *,
    key_vault_name: str = "",
    env_names: list[str] | tuple[str, ...] | None = None,
) -> str:
    value = get_secret_value(
        secret_name,
        key_vault_name=key_vault_name,
        env_names=env_names,
    )
    if value:
        return value
    env_hint = ", ".join(list(env_names or []))
    raise RuntimeError(
        f"Missing required secret '{secret_name}'. "
        f"Checked env vars [{env_hint}] and Azure Key Vault '{_clean(key_vault_name) or _default_key_vault_name()}'."
    )


def get_creds_old_v1(name: str = "test"):
    if _clean(os.getenv("ALLOW_LEGACY_PLAINTEXT_SECRETS")).lower() not in _TRUE_VALUES:
        raise RuntimeError(
            "Local plaintext secret-file loading is disabled. "
            "Set ALLOW_LEGACY_PLAINTEXT_SECRETS=1 only for controlled migration work."
        )

    file_path = Path(__file__).resolve().parents[1] / "secrets" / f"{name}.json"
    with file_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
        return data.get("u"), data.get("p")


def get_creds(KV_NAME: str = DEFAULT_KEY_VAULT_NAME, retreive: list[str] | None = None):
    return [
        get_required_secret_value(secret_name, key_vault_name=KV_NAME)
        for secret_name in list(retreive or ["rh-username", "rh-pswd"])
    ]


def resolve_robinhood_credentials(
    *,
    username: str = "",
    password: str = "",
    key_vault_name: str = DEFAULT_KEY_VAULT_NAME,
    prompt_if_missing: bool = True,
) -> tuple[str, str]:
    resolved_username = _clean(username) or get_secret_value(
        "rh-username",
        key_vault_name=key_vault_name,
        env_names=["RH_USERNAME"],
    )
    resolved_password = _clean(password) or get_secret_value(
        "rh-pswd",
        key_vault_name=key_vault_name,
        env_names=["RH_PASSWORD"],
    )

    if not resolved_password and prompt_if_missing and sys.stdin.isatty():
        resolved_password = _clean(getpass.getpass("Robinhood password: "))

    if not resolved_username:
        raise RuntimeError(
            "Robinhood username is required. Pass --username or set RH_USERNAME/Key Vault secret 'rh-username'."
        )
    if not resolved_password:
        raise RuntimeError(
            "Robinhood password is required. Set RH_PASSWORD, use Key Vault secret 'rh-pswd', or run in a TTY for a secure prompt."
        )
    return resolved_username, resolved_password
