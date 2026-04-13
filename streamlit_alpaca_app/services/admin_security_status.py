from __future__ import annotations

import os
import time
from typing import Any

import requests

from .secrets import build_azure_credential


ARM_SCOPE = "https://management.azure.com/.default"
ARM_BASE_URL = "https://management.azure.com"
SUBSCRIPTIONS_API_VERSION = "2020-01-01"
RESOURCE_GROUPS_API_VERSION = "2021-04-01"
SQL_API_VERSION = "2023-08-01"
KEY_VAULT_API_VERSION = "2023-07-01"
DIAGNOSTIC_SETTINGS_API_VERSION = "2021-05-01-preview"
DEFAULT_RESOURCE_GROUP = "spectral-nature-2"
DEFAULT_SQL_SERVER = "spectral-nature-server"
DEFAULT_KEY_VAULT_NAME = "spectral-nature-kvault"
DEFAULT_SQL_DATABASES = ("spectral-nature-db", "master")
_CACHE_TTL_SECONDS = 120
_STATUS_CACHE: dict[str, Any] = {"expires_at": 0.0, "cache_key": "", "value": None}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _resource_group_name() -> str:
    return _clean(os.getenv("ADMIN_SECURITY_RESOURCE_GROUP")) or DEFAULT_RESOURCE_GROUP


def _sql_server_name() -> str:
    raw = _clean(os.getenv("ADMIN_SECURITY_SQL_SERVER")) or _clean(os.getenv("AZURE_SQL_SERVER"))
    if raw and "." in raw:
        raw = raw.split(".", 1)[0].strip()
    return raw or DEFAULT_SQL_SERVER


def _key_vault_name() -> str:
    return (
        _clean(os.getenv("ADMIN_SECURITY_KEY_VAULT_NAME"))
        or _clean(os.getenv("KEYVAULT_NAME"))
        or _clean(os.getenv("AZURE_KEY_VAULT_NAME"))
        or _clean(os.getenv("KEY_VAULT_NAME"))
        or DEFAULT_KEY_VAULT_NAME
    )


def _configured_sql_database_names() -> list[str]:
    raw = _clean(os.getenv("ADMIN_SECURITY_SQL_DATABASES"))
    if not raw:
        return list(DEFAULT_SQL_DATABASES)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(DEFAULT_SQL_DATABASES)


def _expected_workspace_id() -> str:
    return (
        _clean(os.getenv("ADMIN_SECURITY_LOG_ANALYTICS_WORKSPACE_ID"))
        or _clean(os.getenv("LOG_ANALYTICS_WORKSPACE_RESOURCE_ID"))
        or ""
    )


def _cache_key() -> str:
    return "|".join(
        [
            _resource_group_name(),
            _sql_server_name(),
            _key_vault_name(),
            ",".join(_configured_sql_database_names()),
            _expected_workspace_id(),
        ]
    )


def _resource_name_from_id(resource_id: object) -> str:
    raw = _clean(resource_id)
    if not raw:
        return ""
    return raw.rstrip("/").split("/")[-1]


def _resource_group_from_id(resource_id: object) -> str:
    raw = _clean(resource_id)
    if not raw:
        return ""
    parts = [part for part in raw.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part.lower() == "resourcegroups" and index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _list_enabled_categories(items: object, *, field_name: str = "category") -> list[str]:
    enabled: list[str] = []
    for item in list(items or []):
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled")):
            continue
        value = _clean(item.get(field_name))
        if value:
            enabled.append(value)
    return sorted(set(enabled))


def _credential_error_payload(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "error": reason,
        "summary": {
            "resource_count": 0,
            "healthy_count": 0,
            "audit_enabled_count": 0,
            "audit_expected_count": 0,
            "diagnostics_enabled_count": 0,
            "diagnostics_expected_count": 0,
            "workspace_mismatch_count": 0,
            "error_count": 1,
        },
        "resources": [],
    }


def _management_headers() -> dict[str, str] | None:
    credential = build_azure_credential()
    if credential is None:
        return None
    try:
        token = credential.get_token(ARM_SCOPE).token
    except Exception:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def _management_get(url: str, *, headers: dict[str, str]) -> tuple[dict[str, Any] | None, str]:
    try:
        response = requests.get(url, headers=headers, timeout=10)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if response.status_code >= 400:
        message = _clean(response.text)
        if len(message) > 240:
            message = f"{message[:237]}..."
        return None, f"HTTP {response.status_code}: {message or 'request failed'}"
    try:
        payload = response.json()
    except Exception:
        return None, "Invalid JSON response from Azure management API."
    if not isinstance(payload, dict):
        return None, "Unexpected Azure management API payload."
    return payload, ""


def _management_list(url: str, *, headers: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
    items: list[dict[str, Any]] = []
    next_url = url
    page_count = 0
    while next_url:
        page_count += 1
        if page_count > 12:
            return items, "Azure management API pagination exceeded the safety limit."
        payload, error = _management_get(next_url, headers=headers)
        if error:
            return items, error
        if not isinstance(payload, dict):
            return items, "Unexpected Azure management list payload."
        for item in list(payload.get("value") or []):
            if isinstance(item, dict):
                items.append(item)
        next_url = _clean(payload.get("nextLink"))
    return items, ""


def _resource_payload(
    payload: dict[str, Any] | None,
    *,
    fallback_id: str = "",
    fallback_name: str = "",
) -> dict[str, Any]:
    resource_id = _clean((payload or {}).get("id")) or _clean(fallback_id)
    return {
        "id": resource_id,
        "name": _clean((payload or {}).get("name")) or _clean(fallback_name) or _resource_name_from_id(resource_id),
        "resource_group": _resource_group_from_id(resource_id),
        "resolved": bool(_clean((payload or {}).get("id"))),
    }


def _explicit_subscription_id() -> str:
    return _clean(os.getenv("ADMIN_SECURITY_SUBSCRIPTION_ID")) or _clean(os.getenv("AZURE_SUBSCRIPTION_ID"))


def _list_enabled_subscriptions(*, headers: dict[str, str]) -> list[dict[str, str]]:
    payload, error = _management_get(
        f"{ARM_BASE_URL}/subscriptions?api-version={SUBSCRIPTIONS_API_VERSION}",
        headers=headers,
    )
    if error or not isinstance(payload, dict):
        return []
    enabled: list[dict[str, str]] = []
    for item in list(payload.get("value") or []):
        if not isinstance(item, dict):
            continue
        subscription_id = _clean(item.get("subscriptionId"))
        if not subscription_id or _clean(item.get("state")).lower() != "enabled":
            continue
        enabled.append(
            {
                "subscription_id": subscription_id,
                "display_name": _clean(item.get("displayName") or item.get("subscriptionName")),
            }
        )
    return enabled


def _resource_exists(url: str, *, headers: dict[str, str]) -> bool:
    _, error = _management_get(url, headers=headers)
    return not error


def _resolve_top_level_resource(
    *,
    subscription_id: str,
    headers: dict[str, str],
    resource_group_hint: str,
    provider_path: str,
    api_version: str,
    resource_name: str,
) -> dict[str, str]:
    cleaned_name = _clean(resource_name)
    cleaned_group = _clean(resource_group_hint)
    if not cleaned_name or not subscription_id:
        return {"id": "", "name": cleaned_name, "resource_group": cleaned_group}

    hinted_id = ""
    if cleaned_group:
        hinted_id = (
            f"/subscriptions/{subscription_id}/resourceGroups/{cleaned_group}"
            f"/providers/{provider_path}/{cleaned_name}"
        )
        hinted_payload, hinted_error = _management_get(
            f"{ARM_BASE_URL}{hinted_id}?api-version={api_version}",
            headers=headers,
        )
        if not hinted_error and isinstance(hinted_payload, dict):
            return _resource_payload(hinted_payload, fallback_id=hinted_id, fallback_name=cleaned_name)

    items, error = _management_list(
        f"{ARM_BASE_URL}/subscriptions/{subscription_id}/providers/{provider_path}?api-version={api_version}",
        headers=headers,
    )
    if error:
        return _resource_payload(None, fallback_id=hinted_id, fallback_name=cleaned_name)

    matches = [item for item in items if _clean(item.get("name")).lower() == cleaned_name.lower()]
    if not matches:
        return _resource_payload(None, fallback_id=hinted_id, fallback_name=cleaned_name)

    if cleaned_group:
        for item in matches:
            if _resource_group_from_id(item.get("id")).lower() == cleaned_group.lower():
                return _resource_payload(item, fallback_name=cleaned_name)
    return _resource_payload(matches[0], fallback_name=cleaned_name)


def _subscription_candidate_score(
    *,
    subscription_id: str,
    headers: dict[str, str],
    resource_group: str,
    sql_server_name: str,
    key_vault_name: str,
) -> tuple[int, int, int]:
    resource_group_exists = _resource_exists(
        f"{ARM_BASE_URL}/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"?api-version={RESOURCE_GROUPS_API_VERSION}",
        headers=headers,
    )
    sql_server = _resolve_top_level_resource(
        subscription_id=subscription_id,
        headers=headers,
        resource_group_hint=resource_group,
        provider_path="Microsoft.Sql/servers",
        api_version=SQL_API_VERSION,
        resource_name=sql_server_name,
    )
    key_vault = _resolve_top_level_resource(
        subscription_id=subscription_id,
        headers=headers,
        resource_group_hint=resource_group,
        provider_path="Microsoft.KeyVault/vaults",
        api_version=KEY_VAULT_API_VERSION,
        resource_name=key_vault_name,
    )
    sql_server_exists = bool(sql_server.get("resolved"))
    key_vault_exists = bool(key_vault.get("resolved"))
    return (
        int(resource_group_exists) + int(sql_server_exists) + int(key_vault_exists),
        int(sql_server_exists),
        int(key_vault_exists),
    )


def _resolve_subscription_id(
    *,
    headers: dict[str, str],
    resource_group: str,
    sql_server_name: str,
    key_vault_name: str,
) -> str:
    explicit = _explicit_subscription_id()
    if explicit:
        return explicit

    subscriptions = _list_enabled_subscriptions(headers=headers)
    if not subscriptions:
        return ""
    if len(subscriptions) == 1:
        return subscriptions[0]["subscription_id"]

    best_subscription_id = ""
    best_score = (0, 0, 0)
    for item in subscriptions:
        subscription_id = item["subscription_id"]
        score = _subscription_candidate_score(
            subscription_id=subscription_id,
            headers=headers,
            resource_group=resource_group,
            sql_server_name=sql_server_name,
            key_vault_name=key_vault_name,
        )
        if score > best_score:
            best_score = score
            best_subscription_id = subscription_id
    return best_subscription_id or subscriptions[0]["subscription_id"]


def _discover_database_names(
    *,
    sql_server_id: str,
    headers: dict[str, str],
) -> list[str]:
    explicit = _configured_sql_database_names()
    cleaned_server_id = _clean(sql_server_id)
    if not cleaned_server_id:
        return explicit
    items, error = _management_list(
        f"{ARM_BASE_URL}{cleaned_server_id}/databases?api-version={SQL_API_VERSION}",
        headers=headers,
    )
    if error:
        return explicit
    names = [_clean(item.get("name")) for item in items if _clean(item.get("name"))]
    return names or explicit


def _diagnostic_settings_status(resource_id: str, *, headers: dict[str, str]) -> dict[str, Any]:
    url = f"{ARM_BASE_URL}{resource_id}/providers/microsoft.insights/diagnosticSettings?api-version={DIAGNOSTIC_SETTINGS_API_VERSION}"
    payload, error = _management_get(url, headers=headers)
    if error:
        return {
            "error": error,
            "settings": [],
            "setting_names": [],
            "workspace_ids": [],
            "enabled_log_categories": [],
            "enabled_metric_categories": [],
            "enabled_log_count": 0,
            "enabled_metric_count": 0,
            "diagnostics_enabled": False,
        }

    settings = [item for item in list((payload or {}).get("value") or []) if isinstance(item, dict)]
    workspace_ids: list[str] = []
    enabled_log_categories: list[str] = []
    enabled_metric_categories: list[str] = []
    for setting in settings:
        props = setting.get("properties") if isinstance(setting.get("properties"), dict) else setting
        workspace_id = _clean(props.get("workspaceId"))
        if workspace_id:
            workspace_ids.append(workspace_id)
        enabled_log_categories.extend(_list_enabled_categories(props.get("logs")))
        enabled_metric_categories.extend(_list_enabled_categories(props.get("metrics")))
    return {
        "error": "",
        "settings": settings,
        "setting_names": sorted({_clean(item.get("name")) for item in settings if _clean(item.get("name"))}),
        "workspace_ids": sorted(set(workspace_ids)),
        "enabled_log_categories": sorted(set(enabled_log_categories)),
        "enabled_metric_categories": sorted(set(enabled_metric_categories)),
        "enabled_log_count": len(set(enabled_log_categories)),
        "enabled_metric_count": len(set(enabled_metric_categories)),
        "diagnostics_enabled": bool(enabled_log_categories or enabled_metric_categories),
    }


def _audit_status(resource_id: str, *, headers: dict[str, str]) -> dict[str, Any]:
    url = f"{ARM_BASE_URL}{resource_id}/auditingSettings/Default?api-version={SQL_API_VERSION}"
    payload, error = _management_get(url, headers=headers)
    if error:
        return {
            "error": error,
            "audit_enabled": False,
            "audit_workspace_id": "",
            "audit_actions": [],
            "audit_state": "",
            "azure_monitor_target_enabled": False,
        }
    props = payload.get("properties") if isinstance(payload.get("properties"), dict) else payload
    audit_actions = [str(item).strip() for item in list(props.get("auditActionsAndGroups") or []) if str(item).strip()]
    return {
        "error": "",
        "audit_enabled": _clean(props.get("state")).lower() == "enabled" and bool(props.get("isAzureMonitorTargetEnabled")),
        "audit_workspace_id": _clean(props.get("logAnalyticsWorkspaceResourceId")),
        "audit_actions": audit_actions,
        "audit_state": _clean(props.get("state")),
        "azure_monitor_target_enabled": bool(props.get("isAzureMonitorTargetEnabled")),
    }


def _workspace_status(
    *,
    expected_workspace_id: str,
    workspace_ids: list[str],
) -> str:
    if not workspace_ids:
        return "missing"
    if expected_workspace_id and any(item != expected_workspace_id for item in workspace_ids):
        return "mismatch"
    return "ok"


def _resource_status_record(
    *,
    resource_type: str,
    resource_name: str,
    resource_id: str,
    diagnostics: dict[str, Any],
    expected_workspace_id: str,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audit_payload = audit or {
        "audit_enabled": False,
        "audit_workspace_id": "",
        "audit_actions": [],
        "audit_state": "",
        "azure_monitor_target_enabled": False,
        "error": "",
    }
    workspace_ids = sorted(
        {
            *[item for item in list(diagnostics.get("workspace_ids") or []) if _clean(item)],
            *([_clean(audit_payload.get("audit_workspace_id"))] if _clean(audit_payload.get("audit_workspace_id")) else []),
        }
    )
    workspace_status = _workspace_status(expected_workspace_id=expected_workspace_id, workspace_ids=workspace_ids)
    diagnostics_enabled = bool(diagnostics.get("diagnostics_enabled"))
    audit_enabled = bool(audit_payload.get("audit_enabled"))
    errors = [item for item in [_clean(diagnostics.get("error")), _clean(audit_payload.get("error"))] if item]

    if errors:
        status = "error"
    elif resource_type in {"sql_server", "sql_database"}:
        if audit_enabled and diagnostics_enabled and workspace_status == "ok":
            status = "healthy"
        elif audit_enabled or diagnostics_enabled:
            status = "partial"
        else:
            status = "missing"
    else:
        if diagnostics_enabled and workspace_status == "ok":
            status = "healthy"
        elif diagnostics_enabled:
            status = "partial"
        else:
            status = "missing"

    return {
        "resource_type": resource_type,
        "resource_name": resource_name,
        "resource_id": resource_id,
        "status": status,
        "audit_enabled": audit_enabled,
        "audit_state": _clean(audit_payload.get("audit_state")),
        "audit_workspace_id": _clean(audit_payload.get("audit_workspace_id")),
        "audit_action_count": len(list(audit_payload.get("audit_actions") or [])),
        "diagnostics_enabled": diagnostics_enabled,
        "diagnostic_setting_names": list(diagnostics.get("setting_names") or []),
        "workspace_ids": workspace_ids,
        "workspace_status": workspace_status,
        "enabled_log_categories": list(diagnostics.get("enabled_log_categories") or []),
        "enabled_metric_categories": list(diagnostics.get("enabled_metric_categories") or []),
        "enabled_log_count": int(diagnostics.get("enabled_log_count") or 0),
        "enabled_metric_count": int(diagnostics.get("enabled_metric_count") or 0),
        "error": " | ".join(errors),
    }


def _summarize_resources(resources: list[dict[str, Any]]) -> dict[str, int]:
    audit_expected = sum(1 for item in resources if item.get("resource_type") in {"sql_server", "sql_database"})
    return {
        "resource_count": len(resources),
        "healthy_count": sum(1 for item in resources if item.get("status") == "healthy"),
        "audit_enabled_count": sum(1 for item in resources if bool(item.get("audit_enabled"))),
        "audit_expected_count": audit_expected,
        "diagnostics_enabled_count": sum(1 for item in resources if bool(item.get("diagnostics_enabled"))),
        "diagnostics_expected_count": len(resources),
        "workspace_mismatch_count": sum(1 for item in resources if item.get("workspace_status") == "mismatch"),
        "error_count": sum(1 for item in resources if item.get("status") == "error"),
    }


def _load_admin_cloud_security_status() -> dict[str, Any]:
    headers = _management_headers()
    if headers is None:
        return _credential_error_payload("Azure management credentials are unavailable.")

    resource_group = _resource_group_name()
    sql_server_name = _sql_server_name()
    key_vault_name = _key_vault_name()
    subscription_id = _resolve_subscription_id(
        headers=headers,
        resource_group=resource_group,
        sql_server_name=sql_server_name,
        key_vault_name=key_vault_name,
    )
    if not subscription_id:
        return _credential_error_payload("Azure subscription could not be resolved for admin security status.")

    expected_workspace_id = _expected_workspace_id()
    sql_server = _resolve_top_level_resource(
        subscription_id=subscription_id,
        headers=headers,
        resource_group_hint=resource_group,
        provider_path="Microsoft.Sql/servers",
        api_version=SQL_API_VERSION,
        resource_name=sql_server_name,
    )
    key_vault = _resolve_top_level_resource(
        subscription_id=subscription_id,
        headers=headers,
        resource_group_hint=resource_group,
        provider_path="Microsoft.KeyVault/vaults",
        api_version=KEY_VAULT_API_VERSION,
        resource_name=key_vault_name,
    )
    sql_server_id = _clean(sql_server.get("id")) or (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Sql/servers/{sql_server_name}"
    )
    key_vault_id = _clean(key_vault.get("id")) or (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.KeyVault/vaults/{key_vault_name}"
    )
    database_names = _discover_database_names(
        sql_server_id=sql_server_id,
        headers=headers,
    )

    resources: list[dict[str, Any]] = []
    server_diagnostics = _diagnostic_settings_status(sql_server_id, headers=headers)
    server_audit = _audit_status(sql_server_id, headers=headers)
    if not expected_workspace_id:
        expected_workspace_id = _clean(server_audit.get("audit_workspace_id")) or next(
            (item for item in list(server_diagnostics.get("workspace_ids") or []) if _clean(item)),
            "",
        )
    resources.append(
        _resource_status_record(
            resource_type="sql_server",
            resource_name=sql_server_name,
            resource_id=sql_server_id,
            diagnostics=server_diagnostics,
            audit=server_audit,
            expected_workspace_id=expected_workspace_id,
        )
    )

    for database_name in database_names:
        database_id = f"{sql_server_id}/databases/{database_name}"
        resources.append(
            _resource_status_record(
                resource_type="sql_database",
                resource_name=database_name,
                resource_id=database_id,
                diagnostics=_diagnostic_settings_status(database_id, headers=headers),
                audit=_audit_status(database_id, headers=headers),
                expected_workspace_id=expected_workspace_id,
            )
        )

    resources.append(
        _resource_status_record(
            resource_type="key_vault",
            resource_name=key_vault_name,
            resource_id=key_vault_id,
            diagnostics=_diagnostic_settings_status(key_vault_id, headers=headers),
            expected_workspace_id=expected_workspace_id,
        )
    )

    return {
        "available": True,
        "error": "",
        "subscription_id": subscription_id,
        "resource_group": _clean(sql_server.get("resource_group")) or resource_group,
        "configured_resource_group": resource_group,
        "sql_server_name": sql_server_name,
        "sql_server_resource_group": _clean(sql_server.get("resource_group")),
        "key_vault_name": key_vault_name,
        "key_vault_resource_group": _clean(key_vault.get("resource_group")),
        "expected_workspace_id": expected_workspace_id,
        "summary": _summarize_resources(resources),
        "resources": resources,
    }


def get_admin_cloud_security_status(*, force_refresh: bool = False) -> dict[str, Any]:
    cache_key = _cache_key()
    now = time.time()
    if (
        not force_refresh
        and _STATUS_CACHE.get("cache_key") == cache_key
        and float(_STATUS_CACHE.get("expires_at") or 0.0) > now
        and isinstance(_STATUS_CACHE.get("value"), dict)
    ):
        return dict(_STATUS_CACHE["value"])

    value = _load_admin_cloud_security_status()
    _STATUS_CACHE.update(
        {
            "cache_key": cache_key,
            "expires_at": now + _CACHE_TTL_SECONDS,
            "value": dict(value),
        }
    )
    return value


__all__ = [
    "get_admin_cloud_security_status",
]
