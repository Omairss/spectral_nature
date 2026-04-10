from __future__ import annotations

from services import config as config_module
from services import secrets as secrets_module
from pipeline.jobs import main as pipeline_main


def test_load_config_uses_key_vault_secret_names_and_ignores_raw_alpaca_env(monkeypatch):
    monkeypatch.setattr(config_module, "load_dotenv", None)
    monkeypatch.setenv("AZURE_KEY_VAULT_NAME", "spectral-nature-kvault")
    monkeypatch.setenv("APCA_API_KEY_SECRET_NAME", "custom-apca-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY_SECRET_NAME", "custom-apca-secret")
    monkeypatch.setenv("APCA_API_KEY", "raw-key-should-not-be-used")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "raw-secret-should-not-be-used")
    monkeypatch.setattr(
        secrets_module,
        "_get_secret",
        lambda name: {
            "custom-apca-key": "kv-key",
            "custom-apca-secret": "kv-secret",
        }.get(name, ""),
    )

    cfg = config_module.load_config()

    assert cfg is not None
    assert cfg.alpaca_api_key == "kv-key"
    assert cfg.alpaca_secret_key == "kv-secret"


def test_pipeline_alpaca_config_uses_key_vault_secret_names(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_SECRET_NAME", "custom-apca-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY_SECRET_NAME", "custom-apca-secret")
    monkeypatch.setenv("APCA_API_KEY", "raw-key-should-not-be-used")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "raw-secret-should-not-be-used")
    monkeypatch.setattr(
        secrets_module,
        "_get_secret",
        lambda name: {
            "custom-apca-key": "kv-key",
            "custom-apca-secret": "kv-secret",
        }.get(name, ""),
    )

    cfg = pipeline_main._alpaca_config()

    assert cfg is not None
    assert cfg.alpaca_api_key == "kv-key"
    assert cfg.alpaca_secret_key == "kv-secret"
