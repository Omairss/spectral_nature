from __future__ import annotations

from services import admin_security_status


def test_resource_group_name_ignores_pipeline_resource_group_without_explicit_override(monkeypatch):
    monkeypatch.delenv("ADMIN_SECURITY_RESOURCE_GROUP", raising=False)
    monkeypatch.setenv("PIPELINE_RESOURCE_GROUP", "wrong-pipeline-rg")

    assert admin_security_status._resource_group_name() == admin_security_status.DEFAULT_RESOURCE_GROUP


def test_resolve_subscription_id_prefers_matching_resources(monkeypatch):
    monkeypatch.delenv("ADMIN_SECURITY_SUBSCRIPTION_ID", raising=False)
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    monkeypatch.setattr(
        admin_security_status,
        "_list_enabled_subscriptions",
        lambda **_: [
            {"subscription_id": "sub-a", "display_name": "A"},
            {"subscription_id": "sub-b", "display_name": "B"},
        ],
    )
    monkeypatch.setattr(
        admin_security_status,
        "_subscription_candidate_score",
        lambda subscription_id, **_: (3, 1, 1) if subscription_id == "sub-b" else (1, 0, 0),
    )

    resolved = admin_security_status._resolve_subscription_id(
        headers={},
        resource_group="spectral-nature-2",
        sql_server_name="spectral-nature-server",
        key_vault_name="spectral-nature-kvault",
    )

    assert resolved == "sub-b"


def test_resolve_subscription_id_ignores_unresolved_fallback_ids(monkeypatch):
    monkeypatch.delenv("ADMIN_SECURITY_SUBSCRIPTION_ID", raising=False)
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    monkeypatch.setattr(
        admin_security_status,
        "_list_enabled_subscriptions",
        lambda **_: [
            {"subscription_id": "sub-a", "display_name": "A"},
            {"subscription_id": "sub-b", "display_name": "B"},
        ],
    )

    def fake_resource_exists(url: str, *, headers: dict[str, str]) -> bool:
        return False

    def fake_resolve_top_level_resource(*, subscription_id: str, **kwargs):
        if subscription_id == "sub-b":
            return {
                "id": "/subscriptions/sub-b/resourceGroups/spectral-nature-2/providers/Microsoft.Sql/servers/spectral-nature-server",
                "name": kwargs["resource_name"],
                "resource_group": "spectral-nature-2",
                "resolved": True,
            }
        return {
            "id": f"/subscriptions/{subscription_id}/resourceGroups/wrong-rg/providers/Microsoft.Sql/servers/{kwargs['resource_name']}",
            "name": kwargs["resource_name"],
            "resource_group": "wrong-rg",
            "resolved": False,
        }

    monkeypatch.setattr(admin_security_status, "_resource_exists", fake_resource_exists)
    monkeypatch.setattr(admin_security_status, "_resolve_top_level_resource", fake_resolve_top_level_resource)

    resolved = admin_security_status._resolve_subscription_id(
        headers={},
        resource_group="wrong-rg",
        sql_server_name="spectral-nature-server",
        key_vault_name="spectral-nature-kvault",
    )

    assert resolved == "sub-b"


def test_resolve_top_level_resource_falls_back_to_subscription_listing(monkeypatch):
    def fake_management_get(url: str, *, headers: dict[str, str]):
        if url.endswith(
            "/subscriptions/sub-1/resourceGroups/wrong-rg/providers/Microsoft.Sql/servers/spectral-nature-server"
            "?api-version=2023-08-01"
        ):
            return None, "HTTP 404: not found"
        if url.endswith("/subscriptions/sub-1/providers/Microsoft.Sql/servers?api-version=2023-08-01"):
            return {
                "value": [
                    {
                        "name": "spectral-nature-server",
                        "id": "/subscriptions/sub-1/resourceGroups/spectral-nature-2/providers/Microsoft.Sql/servers/spectral-nature-server",
                    }
                ]
            }, ""
        return {"value": []}, ""

    monkeypatch.setattr(admin_security_status, "_management_get", fake_management_get)

    resource = admin_security_status._resolve_top_level_resource(
        subscription_id="sub-1",
        headers={},
        resource_group_hint="wrong-rg",
        provider_path="Microsoft.Sql/servers",
        api_version=admin_security_status.SQL_API_VERSION,
        resource_name="spectral-nature-server",
    )

    assert resource["id"].endswith("/resourceGroups/spectral-nature-2/providers/Microsoft.Sql/servers/spectral-nature-server")
    assert resource["resource_group"] == "spectral-nature-2"


def test_resource_status_record_marks_sql_database_healthy():
    record = admin_security_status._resource_status_record(
        resource_type="sql_database",
        resource_name="spectral-nature-db",
        resource_id="/subscriptions/test/resourceGroups/rg/providers/Microsoft.Sql/servers/server/databases/spectral-nature-db",
        diagnostics={
            "diagnostics_enabled": True,
            "setting_names": ["sn-sql-db-observability"],
            "workspace_ids": ["/subscriptions/test/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/la"],
            "enabled_log_categories": ["Errors", "Deadlocks"],
            "enabled_metric_categories": ["Basic"],
            "enabled_log_count": 2,
            "enabled_metric_count": 1,
            "error": "",
        },
        audit={
            "audit_enabled": True,
            "audit_workspace_id": "/subscriptions/test/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/la",
            "audit_actions": ["FAILED_DATABASE_AUTHENTICATION_GROUP"],
            "audit_state": "Enabled",
            "azure_monitor_target_enabled": True,
            "error": "",
        },
        expected_workspace_id="/subscriptions/test/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/la",
    )

    assert record["status"] == "healthy"
    assert record["workspace_status"] == "ok"
    assert record["audit_enabled"] is True
    assert record["diagnostics_enabled"] is True


def test_resource_status_record_marks_workspace_mismatch_partial():
    record = admin_security_status._resource_status_record(
        resource_type="key_vault",
        resource_name="spectral-nature-kvault",
        resource_id="/subscriptions/test/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/spectral-nature-kvault",
        diagnostics={
            "diagnostics_enabled": True,
            "setting_names": ["sn-keyvault-diag"],
            "workspace_ids": ["/subscriptions/test/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/other"],
            "enabled_log_categories": ["AuditEvent"],
            "enabled_metric_categories": ["AllMetrics"],
            "enabled_log_count": 1,
            "enabled_metric_count": 1,
            "error": "",
        },
        expected_workspace_id="/subscriptions/test/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/la",
    )

    assert record["status"] == "partial"
    assert record["workspace_status"] == "mismatch"


def test_get_admin_cloud_security_status_returns_unavailable_without_credentials(monkeypatch):
    monkeypatch.setattr(admin_security_status, "build_azure_credential", lambda: None)
    monkeypatch.setattr(admin_security_status, "_STATUS_CACHE", {"expires_at": 0.0, "cache_key": "", "value": None})

    payload = admin_security_status.get_admin_cloud_security_status(force_refresh=True)

    assert payload["available"] is False
    assert "credentials" in payload["error"].lower()
