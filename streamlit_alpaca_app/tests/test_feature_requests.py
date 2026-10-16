"""Real Postgres checks for ownership, environment isolation, retries and atomic writes.

Enable explicitly with SN_FEEDBACK_DATABASE_TESTS=1. Data is confined to unique QA
namespaces and removed afterwards; these checks do not impersonate a live login.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.feature_requests import create_feature_request_router
from services import api_auth, auth_service, pipeline_store
from services.feature_requests import (
    FeatureRequestConflict, FeatureRequestNotFound, FeatureRequestRepository,
)


def _user(*, admin: bool = False) -> auth_service.UserContext:
    return auth_service.UserContext(
        user_id=str(uuid4()), email="feedback-qa@example.invalid", first_name="QA",
        last_name="Reviewer", display_name="QA Reviewer", role="admin" if admin else "investor",
        portfolio_id=str(uuid4()), portfolio_slug="qa", portfolio_name="QA",
        membership_role="admin" if admin else "investor", share_fraction=0,
    )


@pytest.fixture
def namespace(monkeypatch):
    if os.getenv("SN_FEEDBACK_DATABASE_TESTS") != "1":
        pytest.skip("Real database checks require SN_FEEDBACK_DATABASE_TESTS=1")
    value = "feedback-qa-" + uuid4().hex
    monkeypatch.setenv("PIPELINE_DATASET_NAMESPACE", value)
    connection = pipeline_store.open_database_connection()
    assert connection is not None, "QA database connection unavailable"
    connection.close()
    yield value
    connection = pipeline_store.open_database_connection()
    assert connection is not None
    try:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM app_feature_requests WHERE environment=ANY(%s)", ([value, value + "-other"],))
    finally:
        connection.close()


def test_persistence_retry_and_environment_isolation(namespace, monkeypatch):
    owner, admin = _user(), _user(admin=True)
    request_id, message_id = str(uuid4()), str(uuid4())
    with FeatureRequestRepository.open(owner) as repository:
        first = repository.create_request(request_id=request_id, title="Show market direction",
                                          body="Keep price direction visible in the network.", client="ios", app_version="QA")
        repeated = repository.create_request(request_id=request_id, title=first["title"], body=first["messages"][0]["body"])
        assert repeated["message_count"] == 1
        saved = repository.add_message(request_id, message_id=message_id, body="Use both shape and color.")
        assert len(saved["messages"]) == 2
        assert repository.add_message(request_id, message_id=message_id, body="Use both shape and color.")["message_count"] == 2
        with pytest.raises(FeatureRequestConflict):
            repository.add_message(request_id, message_id=message_id, body="Changed after submission")
    with FeatureRequestRepository.open(owner) as repository:
        assert repository.get_request(request_id)["messages"][1]["body"] == "Use both shape and color."
    monkeypatch.setenv("PIPELINE_DATASET_NAMESPACE", namespace + "-other")
    with FeatureRequestRepository.open(admin) as repository:
        assert repository.list_requests(all_requests=True)["requests"] == []
        with pytest.raises(FeatureRequestNotFound):
            repository.get_request(request_id)
    monkeypatch.setenv("PIPELINE_DATASET_NAMESPACE", namespace)
    with FeatureRequestRepository.open(admin) as repository:
        updated = repository.add_message(request_id, message_id=str(uuid4()), body="The native layout is ready to review.")
        assert updated["messages"][-1]["author_role"] == "admin"
    with FeatureRequestRepository.open(owner) as repository:
        assert repository.get_request(request_id)["message_count"] == 3


def test_api_identity_scope_and_reply_permissions(namespace):
    owner, other, admin = _user(), _user(), _user(admin=True)
    selected = {"user": owner, "type": "user"}

    def principal():
        return api_auth.AuthPrincipal(principal_type=selected["type"], scopes=(),
                                      auth_source="isolated_qa", user_context=selected["user"])

    app = FastAPI()
    app.include_router(create_feature_request_router(principal))
    request_id = str(uuid4())
    with TestClient(app) as client:
        response = client.post("/v1/feature-requests", json={"id": request_id, "title": "Lighter Markets plot",
                               "body": "Show the outer instruments.", "owner_id": other.user_id, "environment": "production"})
        assert response.status_code == 200
        assert response.json()["owner_id"] == owner.user_id
        assert response.headers["cache-control"] == "no-store"
        selected["user"] = other
        assert client.get("/v1/feature-requests").json()["requests"] == []
        assert client.get("/v1/feature-requests?scope=all").status_code == 403
        assert client.get(f"/v1/feature-requests/{request_id}").status_code == 404
        assert client.post(f"/v1/feature-requests/{request_id}/messages", json={"id": str(uuid4()), "body": "Unauthorized"}).status_code == 404
        selected["user"] = admin
        page = client.get("/v1/feature-requests?scope=all").json()
        assert page["can_review_all"] and [r["id"] for r in page["requests"]] == [request_id]
        assert client.post(f"/v1/feature-requests/{request_id}/messages", json={"id": str(uuid4()), "body": "Added an adjustable point count."}).status_code == 200
        selected["user"] = owner
        assert len(client.get(f"/v1/feature-requests/{request_id}").json()["messages"]) == 2
        selected["type"] = "agent"
        assert client.get("/v1/feature-requests").status_code == 403


def test_initial_request_and_message_are_atomic(namespace, monkeypatch):
    owner = _user()
    request_id = str(uuid4())
    with FeatureRequestRepository.open(owner) as repository:
        def fail_insert(*args, **kwargs):
            raise RuntimeError("Injected initial-message failure")
        monkeypatch.setattr(repository, "_insert_message", fail_insert)
        with pytest.raises(RuntimeError, match="initial-message"):
            repository.create_request(request_id=request_id, title="Request atomicity", body="Retain both records together.")
    with FeatureRequestRepository.open(owner) as repository:
        with pytest.raises(FeatureRequestNotFound):
            repository.get_request(request_id)


def test_existing_message_id_cannot_create_an_empty_request(namespace):
    owner = _user()
    original_id, collision_id = str(uuid4()), str(uuid4())
    with FeatureRequestRepository.open(owner) as repository:
        repository.create_request(request_id=original_id, title="Original request", body="Keep this thread intact.")
        repository.add_message(original_id, message_id=collision_id, body="Existing follow-up.")
        with pytest.raises(FeatureRequestConflict):
            repository.create_request(request_id=collision_id, title="Conflicting request", body="Must roll back.")
        with pytest.raises(FeatureRequestNotFound):
            repository.get_request(collision_id)
        assert repository.get_request(original_id)["message_count"] == 2
