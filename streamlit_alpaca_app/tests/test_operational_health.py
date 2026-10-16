from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from services import operational_health as health


@pytest.fixture(autouse=True)
def isolate_publication_reads(monkeypatch):
    monkeypatch.setattr(health, "_published_outputs", lambda: [{"dataset": "retained", "rows": 5}])


def test_concurrent_admin_reads_share_one_cloud_collection():
    health._CACHE.clear()
    calls = []

    def read():
        calls.append(1)
        time.sleep(0.02)
        return {"observed_at": "original observation"}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: health._cached(("parallel-test",), 60, read), range(8)))
    assert len(calls) == 1
    assert all(result["observed_at"] == "original observation" for result in results)


def test_cloud_failure_preserves_database_observations(monkeypatch):
    health._CACHE.clear()
    monkeypatch.setattr(health, "llm_settings_scope", lambda: "development")
    monkeypatch.setattr(health, "_database", lambda hours: {"attempts": [{"attempts": 3, "input_tokens": None}]})
    monkeypatch.setattr(health, "_inventory", lambda: (_ for _ in ()).throw(RuntimeError("Azure telemetry HTTP 403")))
    result = health.build_operational_health_view()
    assert result["database"]["attempts"][0]["input_tokens"] is None
    assert result["database"]["attempts"][0]["attempts"] == 3
    assert result["cloud_unavailable"] == "Azure telemetry HTTP 403"


def test_resource_selection_cannot_escape_environment_inventory(monkeypatch):
    health._CACHE.clear()
    monkeypatch.setattr(health, "llm_settings_scope", lambda: "development")
    monkeypatch.setattr(health, "_database", lambda hours: {})
    monkeypatch.setattr(health, "_inventory", lambda: [{"name": "api-dev", "id": "/allowed", "kind": "Microsoft.App/containerApps"}])
    monkeypatch.setattr(health, "_metrics", lambda *_: pytest.fail("Unlisted resource reached Azure"))
    with pytest.raises(ValueError, match="Unknown telemetry resource"):
        health.build_operational_health_view(resource_name="https://outside.example/metadata")


def test_database_outage_preserves_cloud_observations(monkeypatch):
    health._CACHE.clear()
    monkeypatch.setattr(health, "llm_settings_scope", lambda: "development")
    monkeypatch.setattr(health, "_database", lambda _: (_ for _ in ()).throw(ConnectionError("private DSN")))
    monkeypatch.setattr(health, "_inventory", lambda: [{"name": "api-dev", "id": "/allowed"}])
    monkeypatch.setattr(health, "_metrics", lambda *_: {"resource": "api-dev", "series": [{"metric": "WorkingSetBytes"}]})
    result = health.build_operational_health_view(resource_name="api-dev")
    assert result["metrics"]["series"][0]["metric"] == "WorkingSetBytes"
    assert result["database"]["unavailable"] == {"database": "ConnectionError"}
    assert result["outputs"][0]["rows"] == 5
    assert "private DSN" not in str(result)


def test_allocation_failure_does_not_discard_resource_metrics(monkeypatch):
    def arm(path, params):
        if path.endswith("metricDefinitions"):
            return {"value": [{"name": {"value": "WorkingSetBytes"}}]}
        if path.endswith("metrics"):
            assert "+" not in params["timespan"]
            assert params["autoAdjustTimegrain"] == "true"
            return {"value": [{"name": {"value": "WorkingSetBytes"}, "timeseries": [{"data": [{"timeStamp": "2026-10-15T00:00:00Z", "average": 10}]}]}]}
        raise RuntimeError("allocation unavailable")
    monkeypatch.setattr(health, "_arm_get", arm)
    result = health._metrics({"name": "api-dev", "id": "/allowed", "kind": "Microsoft.App/containerApps"}, 24)
    assert result["series"][0]["points"][0]["average"] == 10
    assert result["allocation_unavailable"] == "RuntimeError"


def test_admin_inventory_excludes_other_environment_apps(monkeypatch):
    monkeypatch.setenv("SYSTEM_HEALTH_AZURE_RESOURCE_GROUP_ID", "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/test")
    monkeypatch.setattr(health, "llm_settings_scope", lambda: "development")
    monkeypatch.setattr(health, "_arm_get", lambda *_: {"value": [
        {"name": "api-dev", "id": "/dev", "type": "Microsoft.App/containerApps"},
        {"name": "api", "id": "/prod", "type": "Microsoft.App/containerApps"},
        {"name": "vault", "id": "/secret", "type": "Microsoft.KeyVault/vaults"},
        {"name": "pipeline-pg", "id": "/database", "type": "Microsoft.DBforPostgreSQL/flexibleServers"},
    ]})
    rows = health._inventory()
    assert {row["name"] for row in rows} == {"api-dev", "pipeline-pg"}
    assert next(row for row in rows if row["name"] == "pipeline-pg")["scope"] == "shared"


def test_health_route_requires_current_admin_membership(monkeypatch):
    from fastapi.testclient import TestClient
    import api.main as api
    from services import api_auth, auth_service

    def principal(admin):
        context = auth_service.UserContext(
            user_id="u", email="qa@example.com", first_name="QA", last_name="User", display_name="QA",
            role="admin" if admin else "investor", portfolio_id="p", portfolio_slug="p", portfolio_name="P",
            membership_role="admin" if admin else "investor", share_fraction=0, can_view_full_portfolio=admin,
        )
        return api_auth.AuthPrincipal(principal_type="user", scopes=(), auth_source="access_token", user_context=context)

    monkeypatch.setattr(health, "build_operational_health_view", lambda **_: {"scope": "development"})
    try:
        api.app.dependency_overrides[api._require_principal] = lambda: principal(False)
        client = TestClient(api.app)
        assert client.get("/v1/admin/health").status_code == 403
        api.app.dependency_overrides[api._require_principal] = lambda: principal(True)
        response = client.get("/v1/admin/health")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
    finally:
        api.app.dependency_overrides.pop(api._require_principal, None)


def test_slow_database_does_not_hold_cloud_or_publication_snapshots(monkeypatch):
    from threading import Event
    health._CACHE.clear()
    release = Event()
    calls = []
    monkeypatch.setattr(health, '_COLLECTION_WAIT_SECONDS', 0.04)
    monkeypatch.setattr(health, 'llm_settings_scope', lambda: 'slow-database-isolation')
    def database(_):
        calls.append(1)
        release.wait(3)
        return {'server_name': 'pipeline', 'attempts': []}
    monkeypatch.setattr(health, '_database', database)
    monkeypatch.setattr(health, '_inventory', lambda: [{'name': 'api', 'id': '/allowed'}])
    monkeypatch.setattr(health, '_metrics', lambda *_: {'resource': 'api', 'series': [{'metric': 'WorkingSetBytes'}]})
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: health.build_operational_health_view(resource_name='api'), range(6)))
        assert time.monotonic() - started < 1
        assert len(calls) == 1
        assert all(result['metrics']['series'][0]['metric'] == 'WorkingSetBytes' for result in results)
        assert all(result['outputs'][0]['rows'] == 5 for result in results)
        assert all(result['database']['unavailable']['database'] == 'Collection still running' for result in results)
    finally:
        release.set()
        future = health._INFLIGHT.get(('database', 'slow-database-isolation', 24))
        if future:
            future.result(timeout=2)
