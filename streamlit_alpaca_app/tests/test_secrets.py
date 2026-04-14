from __future__ import annotations

from types import SimpleNamespace

from services import secrets


def test_vault_url_prefers_keyvault_name_when_aliases_are_stale(monkeypatch):
    monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
    monkeypatch.setenv("KEYVAULT_NAME", "spectral-nature-kvault")
    monkeypatch.setenv("AZURE_KEY_VAULT_NAME", "snpipelinekv03130136")
    monkeypatch.setenv("KEY_VAULT_NAME", "snpipelinekv03130136")

    assert secrets._vault_url() == "https://spectral-nature-kvault.vault.azure.net"


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
