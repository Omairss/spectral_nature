from __future__ import annotations

from types import SimpleNamespace

from services import secrets


def test_vault_url_prefers_keyvault_name_when_aliases_are_stale(monkeypatch):
    monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
    monkeypatch.setenv("KEYVAULT_NAME", "spectral-nature-kvault")
    monkeypatch.setenv("AZURE_KEY_VAULT_NAME", "snpipelinekv03130136")
    monkeypatch.setenv("KEY_VAULT_NAME", "snpipelinekv03130136")

    assert secrets._vault_url() == "https://spectral-nature-kvault.vault.azure.net"


def test_vault_url_reads_generated_deployment_env(monkeypatch, tmp_path):
    generated_dir = tmp_path / "infra" / ".generated"
    generated_dir.mkdir(parents=True)
    (generated_dir / "deployment.local.env").write_text(
        "KEYVAULT_NAME=local-generated-vault\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(secrets, "APP_ROOT", tmp_path)
    monkeypatch.delenv("DEPLOYMENT_ENV_FILE", raising=False)
    monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
    monkeypatch.delenv("KEYVAULT_NAME", raising=False)
    monkeypatch.delenv("AZURE_KEY_VAULT_NAME", raising=False)
    monkeypatch.delenv("KEY_VAULT_NAME", raising=False)

    assert secrets._vault_url() == "https://local-generated-vault.vault.azure.net"


def test_resolve_secret_value_uses_generated_vault_with_default_secret(monkeypatch, tmp_path):
    generated_dir = tmp_path / "infra" / ".generated"
    generated_dir.mkdir(parents=True)
    (generated_dir / "deployment.local.env").write_text(
        "KEYVAULT_NAME=local-generated-vault\n",
        encoding="utf-8",
    )

    captured: dict[str, str] = {}

    def fake_read_secret(secret_name: str, vault_url: str) -> dict[str, str]:
        captured["secret_name"] = secret_name
        captured["vault_url"] = vault_url
        return {"value": "postgres://example", "reason": "", "error_type": "", "error_message": ""}

    monkeypatch.setattr(secrets, "APP_ROOT", tmp_path)
    monkeypatch.setattr(secrets, "_read_secret", fake_read_secret)
    monkeypatch.delenv("DEPLOYMENT_ENV_FILE", raising=False)
    monkeypatch.delenv("POSTGRES_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("POSTGRES_CONNECTION_STRING_SECRET", raising=False)
    monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
    monkeypatch.delenv("KEYVAULT_NAME", raising=False)
    monkeypatch.delenv("AZURE_KEY_VAULT_NAME", raising=False)
    monkeypatch.delenv("KEY_VAULT_NAME", raising=False)

    value = secrets.resolve_secret_value(
        ["POSTGRES_CONNECTION_STRING"],
        secret_name_env="POSTGRES_CONNECTION_STRING_SECRET",
        default_secret_name="postgres-connection-string",
    )

    assert value == "postgres://example"
    assert captured == {
        "secret_name": "postgres-connection-string",
        "vault_url": "https://local-generated-vault.vault.azure.net",
    }


def test_postgres_connect_timeout_seconds_is_bounded(monkeypatch):
    monkeypatch.setenv("POSTGRES_CONNECT_TIMEOUT_SECONDS", "0")
    assert secrets.postgres_connect_timeout_seconds() == 1

    monkeypatch.setenv("POSTGRES_CONNECT_TIMEOUT_SECONDS", "120")
    assert secrets.postgres_connect_timeout_seconds() == 60

    monkeypatch.setenv("POSTGRES_CONNECT_TIMEOUT_SECONDS", "7")
    assert secrets.postgres_connect_timeout_seconds() == 7


def test_build_azure_credential_chains_configured_and_default_managed_identity(monkeypatch):
    secrets.build_azure_credential.cache_clear()
    secrets._ensure_writable_azure_cli_config_dir.cache_clear()

    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://metadata.identity")
    monkeypatch.setenv("AZURE_CLIENT_ID", "stale-client-id")
    monkeypatch.setattr(secrets, "EnvironmentCredential", None)
    monkeypatch.setattr(secrets, "AzureCliCredential", None)
    monkeypatch.setattr(secrets, "DefaultAzureCredential", None)

    created_client_ids: list[str | None] = []

    def fake_managed_identity_credential(*, client_id=None):
        created_client_ids.append(client_id)
        return SimpleNamespace(client_id=client_id)

    def fake_chained_token_credential(*credentials):
        return {"credentials": credentials}

    monkeypatch.setattr(secrets, "ManagedIdentityCredential", fake_managed_identity_credential)
    monkeypatch.setattr(secrets, "ChainedTokenCredential", fake_chained_token_credential)

    try:
        credential = secrets.build_azure_credential()
    finally:
        secrets.build_azure_credential.cache_clear()
        secrets._ensure_writable_azure_cli_config_dir.cache_clear()

    assert created_client_ids == ["stale-client-id", None]
    assert [item.client_id for item in credential["credentials"]] == ["stale-client-id", None]
