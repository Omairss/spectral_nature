from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


try:
    from azure.identity import (
        AzureCliCredential,
        ChainedTokenCredential,
        DefaultAzureCredential,
        EnvironmentCredential,
        ManagedIdentityCredential,
    )
    from azure.keyvault.secrets import SecretClient
except Exception:
    AzureCliCredential = None
    ChainedTokenCredential = None
    DefaultAzureCredential = None
    EnvironmentCredential = None
    ManagedIdentityCredential = None
    SecretClient = None


APP_ROOT = Path(__file__).resolve().parents[1]


def _clean(value: str | None, placeholders: set[str] | None = None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    blocked = {item.lower() for item in (placeholders or set())}
    if cleaned.lower() in blocked:
        return ""
    return cleaned


def _deployment_env_paths() -> tuple[Path, ...]:
    override = _clean(os.getenv("DEPLOYMENT_ENV_FILE"))
    candidates: list[Path] = []
    if override:
        override_path = Path(override)
        if not override_path.is_absolute():
            override_path = APP_ROOT / override_path
        candidates.append(override_path)
    candidates.extend(
        (
            APP_ROOT / "infra" / ".generated" / "deployment.local.env",
            APP_ROOT / "infra" / "deployment.outputs.env",
        )
    )

    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        unique_paths.append(path)
    return tuple(unique_paths)


def _local_deployment_env() -> dict[str, str]:
    for env_file in _deployment_env_paths():
        if not env_file.exists():
            continue
        values: dict[str, str] = {}
        for line in env_file.read_text(encoding="utf-8").splitlines():
            clean = line.strip()
            if not clean or clean.startswith("#") or "=" not in clean:
                continue
            key, value = clean.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key.removeprefix("export ").strip()
            if key:
                values[key] = value.strip().strip("'\"")
        if values:
            return values
    return {}


def _runtime_env_value(name: str) -> str:
    return _clean(os.getenv(name)) or _clean(_local_deployment_env().get(name))


def runtime_env_value(name: str) -> str:
    """Read process env, then generated local deployment env.

    Local commands should see the same non-secret deployment settings that
    secret resolution already uses; otherwise provider/model defaults can be
    paired with a Key Vault secret from a different provider.
    """
    return _runtime_env_value(name)


def _runtime_env_value_with_source(
    name: str,
    *,
    placeholders: set[str] | None = None,
) -> tuple[str, str, bool]:
    raw = os.getenv(name)
    value = _clean(raw, placeholders=placeholders)
    if value:
        return value, "env", False
    blocked = bool((raw or "").strip())

    local_raw = _local_deployment_env().get(name)
    local_value = _clean(local_raw, placeholders=placeholders)
    if local_value:
        return local_value, "deployment_env", blocked
    blocked = blocked or bool((local_raw or "").strip())
    return "", "", blocked


def _vault_url() -> str:
    explicit = _runtime_env_value("AZURE_KEY_VAULT_URL")
    if explicit:
        return explicit
    name = (
        _runtime_env_value("KEYVAULT_NAME")
        or _runtime_env_value("AZURE_KEY_VAULT_NAME")
        or _runtime_env_value("KEY_VAULT_NAME")
        or ""
    ).strip()
    if not name:
        return ""
    return f"https://{name}.vault.azure.net"


def _vault_name() -> str:
    explicit = _runtime_env_value("AZURE_KEY_VAULT_URL").rstrip("/")
    if explicit:
        host = explicit.replace("https://", "").replace("http://", "").split("/", 1)[0].strip()
        return host.replace(".vault.azure.net", "")
    return (
        _runtime_env_value("KEYVAULT_NAME")
        or _runtime_env_value("AZURE_KEY_VAULT_NAME")
        or _runtime_env_value("KEY_VAULT_NAME")
        or ""
    ).strip()


def _running_in_azure_runtime() -> bool:
    return any(
        (os.getenv(name) or "").strip()
        for name in (
            "IDENTITY_ENDPOINT",
            "IDENTITY_HEADER",
            "MSI_ENDPOINT",
            "MSI_SECRET",
            "WEBSITE_SITE_NAME",
            "CONTAINER_APP_NAME",
        )
    )


def _managed_identity_client_id() -> str:
    for env_name in ("PIPELINE_MANAGED_IDENTITY_CLIENT_ID", "MANAGED_IDENTITY_CLIENT_ID", "AZURE_CLIENT_ID"):
        value = (os.getenv(env_name) or "").strip()
        if value:
            return value
    return ""


@lru_cache(maxsize=1)
def _ensure_writable_azure_cli_config_dir() -> str:
    explicit = (os.getenv("AZURE_CONFIG_DIR") or "").strip()
    if explicit:
        return explicit

    source_dir = Path.home() / ".azure"
    if not source_dir.exists() or not source_dir.is_dir():
        return ""

    target_dir = Path(tempfile.gettempdir()) / f"codex-azure-config-{os.getuid()}"
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
    os.environ["AZURE_CONFIG_DIR"] = str(target_dir)
    return str(target_dir)


@lru_cache(maxsize=1)
def build_azure_credential():
    if EnvironmentCredential is not None:
        has_env_credential = all(
            (os.getenv(name) or "").strip()
            for name in ("AZURE_CLIENT_ID", "AZURE_TENANT_ID")
        ) and any(
            (os.getenv(name) or "").strip()
            for name in ("AZURE_CLIENT_SECRET", "AZURE_CLIENT_CERTIFICATE_PATH")
        )
        if has_env_credential:
            try:
                return EnvironmentCredential()
            except Exception:
                pass

    if ManagedIdentityCredential is not None and _running_in_azure_runtime():
        managed_identity_credentials: list[Any] = []
        configured_client_id = _managed_identity_client_id()
        if configured_client_id:
            try:
                managed_identity_credentials.append(ManagedIdentityCredential(client_id=configured_client_id))
            except Exception:
                pass
        try:
            managed_identity_credentials.append(ManagedIdentityCredential())
        except Exception:
            pass
        if len(managed_identity_credentials) == 1:
            return managed_identity_credentials[0]
        if len(managed_identity_credentials) > 1 and ChainedTokenCredential is not None:
            try:
                return ChainedTokenCredential(*managed_identity_credentials)
            except Exception:
                pass

    if AzureCliCredential is not None:
        config_dir = _ensure_writable_azure_cli_config_dir()
        if config_dir:
            try:
                return AzureCliCredential(process_timeout=10)
            except Exception:
                pass

    if DefaultAzureCredential is None:
        return None

    try:
        return DefaultAzureCredential(exclude_managed_identity_credential=not _running_in_azure_runtime())
    except Exception:
        return None


@lru_cache(maxsize=128)
def _read_secret(secret_name: str, vault_url: str) -> dict[str, str]:
    if not secret_name:
        return {"value": "", "reason": "secret_name_missing", "error_type": "", "error_message": ""}
    if not vault_url:
        return {"value": "", "reason": "vault_url_missing", "error_type": "", "error_message": ""}
    if SecretClient is None:
        return {"value": "", "reason": "azure_sdk_unavailable", "error_type": "", "error_message": ""}

    credential = build_azure_credential()
    if credential is None:
        return {"value": "", "reason": "azure_credentials_unavailable", "error_type": "", "error_message": ""}

    try:
        client = SecretClient(vault_url=vault_url, credential=credential)
        return {
            "value": str(client.get_secret(secret_name).value or "").strip(),
            "reason": "",
            "error_type": "",
            "error_message": "",
        }
    except Exception as exc:
        message = str(exc or "").strip().replace("\n", " ")
        if len(message) > 240:
            message = f"{message[:237]}..."
        return {
            "value": "",
            "reason": "key_vault_lookup_failed",
            "error_type": type(exc).__name__,
            "error_message": message,
        }


def _get_secret(secret_name: str) -> str:
    return str(_read_secret(secret_name, _vault_url()).get("value") or "").strip()


@lru_cache(maxsize=256)
def get_secret_value_from_vault(
    secret_name: str,
    *,
    vault_name: str = "",
    vault_url: str = "",
) -> str:
    if not secret_name:
        return ""
    resolved_vault_url = (vault_url or "").strip()
    if not resolved_vault_url:
        cleaned_vault_name = (vault_name or "").strip()
        if cleaned_vault_name:
            resolved_vault_url = f"https://{cleaned_vault_name}.vault.azure.net"
        else:
            resolved_vault_url = _vault_url()
    return str(_read_secret(secret_name, resolved_vault_url).get("value") or "").strip()


def describe_secret_resolution(
    env_names: list[str],
    *,
    secret_name_env: str | None = None,
    default_secret_name: str | None = None,
    placeholders: set[str] | None = None,
) -> dict[str, Any]:
    blocked_env_names: list[str] = []
    for env_name in env_names:
        value, source, blocked = _runtime_env_value_with_source(env_name, placeholders=placeholders)
        if value:
            return {
                "resolved": True,
                "value": value,
                "source": source,
                "env_name": env_name,
                "blocked_env_names": blocked_env_names,
                "secret_name": "",
                "secret_name_source": "",
                "vault_name": "",
                "vault_url": "",
                "reason": "",
                "error_type": "",
                "error_message": "",
            }
        if blocked:
            blocked_env_names.append(env_name)

    secret_name = ""
    secret_name_source = ""
    if secret_name_env:
        secret_name, secret_name_source, blocked = _runtime_env_value_with_source(secret_name_env)
        if secret_name:
            secret_name_source = (
                secret_name_env if secret_name_source == "env" else f"deployment_env:{secret_name_env}"
            )
        elif blocked:
            blocked_env_names.append(secret_name_env)
    if not secret_name and default_secret_name:
        secret_name = default_secret_name
        secret_name_source = "default"

    resolved_vault_url = _vault_url()
    try:
        result = _read_secret(secret_name, resolved_vault_url)
    except Exception as exc:
        result = {
            "value": "",
            "reason": "key_vault_lookup_failed",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    value = _clean(str(result.get("value") or ""), placeholders=placeholders)
    if value:
        return {
            "resolved": True,
            "value": value,
            "source": "key_vault",
            "env_name": "",
            "blocked_env_names": blocked_env_names,
            "secret_name": secret_name,
            "secret_name_source": secret_name_source,
            "vault_name": _vault_name(),
            "vault_url": resolved_vault_url,
            "reason": "",
            "error_type": "",
            "error_message": "",
        }

    reason = str(result.get("reason") or "")
    if not reason and secret_name:
        reason = "secret_value_missing"

    return {
        "resolved": False,
        "value": "",
        "source": "",
        "env_name": "",
        "blocked_env_names": blocked_env_names,
        "secret_name": secret_name,
        "secret_name_source": secret_name_source,
        "vault_name": _vault_name(),
        "vault_url": resolved_vault_url,
        "reason": reason,
        "error_type": str(result.get("error_type") or ""),
        "error_message": str(result.get("error_message") or ""),
    }


def resolve_secret_value(
    env_names: list[str],
    *,
    secret_name_env: str | None = None,
    default_secret_name: str | None = None,
    placeholders: set[str] | None = None,
) -> str:
    details = describe_secret_resolution(
        env_names,
        secret_name_env=secret_name_env,
        default_secret_name=default_secret_name,
        placeholders=placeholders,
    )
    return str(details.get("value") or "")


def postgres_connect_timeout_seconds(default: int = 5) -> int:
    raw = _runtime_env_value("POSTGRES_CONNECT_TIMEOUT_SECONDS") or str(default)
    try:
        parsed = int(str(raw).strip())
    except Exception:
        parsed = default
    return min(max(parsed, 1), 60)
