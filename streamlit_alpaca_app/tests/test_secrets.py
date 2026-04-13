from __future__ import annotations

from services import secrets


def test_vault_url_prefers_keyvault_name_when_aliases_are_stale(monkeypatch):
    monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
    monkeypatch.setenv("KEYVAULT_NAME", "spectral-nature-kvault")
    monkeypatch.setenv("AZURE_KEY_VAULT_NAME", "snpipelinekv03130136")
    monkeypatch.setenv("KEY_VAULT_NAME", "snpipelinekv03130136")

    assert secrets._vault_url() == "https://spectral-nature-kvault.vault.azure.net"
