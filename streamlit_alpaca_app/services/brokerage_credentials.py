from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .secrets import resolve_secret_value


class BrokerageCredentialError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def connection_cipher() -> MultiFernet:
    configured = resolve_secret_value(
        ["ROBINHOOD_MCP_TOKEN_ENCRYPTION_KEY"],
        secret_name_env="ROBINHOOD_MCP_TOKEN_ENCRYPTION_KEY_SECRET_NAME",
        default_secret_name="robinhood-mcp-token-encryption-key",
    )
    keys = [item.strip() for item in str(configured or "").split(",") if item.strip()]
    if not keys:
        raise BrokerageCredentialError(
            "Robinhood connection storage is unavailable because its encryption key is not configured."
        )
    try:
        return MultiFernet([Fernet(key.encode("ascii")) for key in keys])
    except Exception as exc:
        raise BrokerageCredentialError("The Robinhood connection encryption key is invalid.") from exc


def encrypt_secret(value: object) -> str:
    plaintext = str(value or "")
    if not plaintext:
        return ""
    return connection_cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(value: object) -> str:
    ciphertext = str(value or "").strip()
    if not ciphertext:
        return ""
    try:
        return connection_cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise BrokerageCredentialError("Stored Robinhood authorization could not be decrypted.") from exc


__all__ = [
    "BrokerageCredentialError",
    "connection_cipher",
    "decrypt_secret",
    "encrypt_secret",
]
