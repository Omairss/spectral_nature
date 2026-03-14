from __future__ import annotations

from functools import lru_cache
import os


try:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
except Exception:
    DefaultAzureCredential = None
    SecretClient = None


def _clean(value: str | None, placeholders: set[str] | None = None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    blocked = {item.lower() for item in (placeholders or set())}
    if cleaned.lower() in blocked:
        return ""
    return cleaned


def _vault_url() -> str:
    explicit = (os.getenv("AZURE_KEY_VAULT_URL") or "").strip()
    if explicit:
        return explicit
    name = (os.getenv("AZURE_KEY_VAULT_NAME") or os.getenv("KEY_VAULT_NAME") or "").strip()
    if not name:
        return ""
    return f"https://{name}.vault.azure.net"


@lru_cache(maxsize=128)
def _get_secret(secret_name: str) -> str:
    if not secret_name:
        return ""
    vault_url = _vault_url()
    if not vault_url or DefaultAzureCredential is None or SecretClient is None:
        return ""
    try:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_url, credential=credential)
        return str(client.get_secret(secret_name).value or "").strip()
    except Exception:
        return ""


def resolve_secret_value(
    env_names: list[str],
    *,
    secret_name_env: str | None = None,
    default_secret_name: str | None = None,
    placeholders: set[str] | None = None,
) -> str:
    for env_name in env_names:
        value = _clean(os.getenv(env_name), placeholders=placeholders)
        if value:
            return value

    secret_name = ""
    if secret_name_env:
        secret_name = (os.getenv(secret_name_env) or "").strip()
    if not secret_name and default_secret_name:
        secret_name = default_secret_name

    secret_value = _clean(_get_secret(secret_name), placeholders=placeholders)
    if secret_value:
        return secret_value

    return ""
