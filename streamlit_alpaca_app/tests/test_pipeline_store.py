from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services import pipeline_store


class _FakeDownload:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def readall(self) -> bytes:
        return self._payload


class _FakeBlobClient:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def download_blob(self) -> _FakeDownload:
        return _FakeDownload(self._payload)


class _FakeContainerClient:
    def __init__(self, blob_items: list[SimpleNamespace]) -> None:
        self._blob_items = blob_items

    def list_blobs(self, name_starts_with: str = "") -> list[SimpleNamespace]:
        return [item for item in self._blob_items if item.name.startswith(name_starts_with)]


class _FakeBlobServiceClient:
    def __init__(self, payloads: dict[str, bytes], blob_items: list[SimpleNamespace]) -> None:
        self._payloads = payloads
        self._blob_items = blob_items

    def get_container_client(self, container: str) -> _FakeContainerClient:
        assert container == "datasets"
        return _FakeContainerClient(self._blob_items)

    def get_blob_client(self, container: str, blob: str) -> _FakeBlobClient:
        assert container == "datasets"
        return _FakeBlobClient(self._payloads[blob])


class _FakeCredential:
    def get_token(self, scope: str):
        assert scope == pipeline_store.ARM_SCOPE
        return SimpleNamespace(token="token")


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.reason = "reason"

    def json(self) -> dict[str, object]:
        return self._payload


def test_pipeline_store_configured_with_storage_only(monkeypatch):
    monkeypatch.setattr(pipeline_store, "_storage_account_url", lambda: "https://example.blob.core.windows.net")
    monkeypatch.setattr(pipeline_store, "_postgres_connection_string", lambda: "")

    assert pipeline_store.pipeline_store_configured() is True


def test_start_source_refresh_job_uses_arm_without_azure_cli(monkeypatch):
    calls: list[tuple[str, str]] = []

    def _fake_get(url, **kwargs):
        calls.append(("GET", url))
        return _FakeResponse(
            200,
            {
                "value": [
                    {
                        "subscriptionId": "sub-1",
                        "state": "Enabled",
                    }
                ]
            },
        )

    def _fake_post(url, **kwargs):
        calls.append(("POST", url))
        return _FakeResponse(
            202,
            {"name": "attention-home-build-abc"},
        )

    monkeypatch.setenv("PIPELINE_RESOURCE_GROUP", "rg-test")
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    monkeypatch.setattr(pipeline_store, "build_azure_credential", lambda: _FakeCredential())
    monkeypatch.setattr(pipeline_store.requests, "get", _fake_get)
    monkeypatch.setattr(pipeline_store.requests, "post", _fake_post)
    monkeypatch.setattr(pipeline_store.shutil, "which", lambda name: None)

    ok, message = pipeline_store.start_source_refresh_job("attention")

    assert ok is True
    assert "attention-home-build-abc" in message
    assert any(call[0] == "POST" and "/providers/Microsoft.App/jobs/attention-home-build/start" in call[1] for call in calls)


def test_start_source_refresh_job_reports_arm_error_without_azure_cli(monkeypatch):
    monkeypatch.setenv("PIPELINE_RESOURCE_GROUP", "rg-test")
    monkeypatch.setattr(pipeline_store, "build_azure_credential", lambda: _FakeCredential())
    monkeypatch.setattr(
        pipeline_store,
        "_list_azure_subscription_ids",
        lambda headers: ["sub-1"],
    )
    monkeypatch.setattr(
        pipeline_store.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(403, text="forbidden"),
    )
    monkeypatch.setattr(pipeline_store.shutil, "which", lambda name: None)

    ok, message = pipeline_store.start_source_refresh_job("attention")

    assert ok is False
    assert "HTTP 403" in message
    assert "Azure CLI" not in message


def test_record_trading_agent_place_action_is_log_only_for_alpaca(monkeypatch):
    captured: dict[str, object] = {}

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            if "INSERT INTO trading_agent_actions" in str(query):
                captured["query"] = query
                captured["params"] = params

    class _Conn:
        def cursor(self):
            return _Cursor()

        def commit(self):
            captured["committed"] = True

        def rollback(self):
            captured["rolled_back"] = True

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(pipeline_store, "_db_connect", lambda: _Conn())

    ok, message = pipeline_store.record_trading_agent_action(
        candidate={
            "candidate_id": "tag_123",
            "trading_agent_run_id": "run:1w",
            "run_id": "run",
            "horizon_key": "1w",
            "ticker": "AAPL",
        },
        action="place",
        requested_by="admin-user",
        requested_email="admin@example.com",
    )

    assert ok is True
    assert "No broker order was submitted" in message
    params = captured["params"]
    assert params[6] == "place_requested"
    assert params[7] == "log_only"
    assert params[9] == "alpaca"
    assert json.loads(params[11]) == {}


def test_load_deployment_env_prefers_generated_local_file(monkeypatch, tmp_path):
    generated_dir = tmp_path / "infra" / ".generated"
    generated_dir.mkdir(parents=True)
    (generated_dir / "deployment.local.env").write_text(
        "PIPELINE_RESOURCE_GROUP=rg-generated\nAZURE_STORAGE_ACCOUNT_URL=https://generated.blob.core.windows.net\n",
        encoding="utf-8",
    )
    legacy_file = tmp_path / "infra" / "deployment.outputs.env"
    legacy_file.write_text(
        "RESOURCE_GROUP=rg-legacy\nSTORAGE_URL=https://legacy.blob.core.windows.net\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(pipeline_store, "APP_ROOT", tmp_path)
    monkeypatch.delenv("DEPLOYMENT_ENV_FILE", raising=False)

    loaded = pipeline_store._load_deployment_env()

    assert loaded["PIPELINE_RESOURCE_GROUP"] == "rg-generated"
    assert loaded["AZURE_STORAGE_ACCOUNT_URL"] == "https://generated.blob.core.windows.net"


def test_load_deployment_env_uses_legacy_file_as_fallback(monkeypatch, tmp_path):
    infra_dir = tmp_path / "infra"
    infra_dir.mkdir(parents=True)
    (infra_dir / "deployment.outputs.env").write_text(
        "RESOURCE_GROUP=rg-legacy\nSTORAGE_URL=https://legacy.blob.core.windows.net\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(pipeline_store, "APP_ROOT", tmp_path)
    monkeypatch.delenv("DEPLOYMENT_ENV_FILE", raising=False)

    loaded = pipeline_store._load_deployment_env()

    assert loaded["RESOURCE_GROUP"] == "rg-legacy"
    assert loaded["STORAGE_URL"] == "https://legacy.blob.core.windows.net"


def test_latest_dataset_metadata_falls_back_to_manifest_when_db_unavailable(monkeypatch):
    manifest = {
        "dataset_name": "attention_feed",
        "dataset_version_id": "attention_feed__20260320T012554Z__16918b7a",
        "blob_path": "datasets/attention_feed/dt=2026-03-20/asof=2026-03-20T01-25-54Z/universe=20260313/part-16918b7a.parquet",
        "asof_time_utc": "2026-03-20T01:25:54Z",
        "ingested_at_utc": "2026-03-20T01:26:02Z",
        "row_count": 3,
    }
    manifest_path = "manifests/attention_feed/attention_feed__20260320T012554Z__16918b7a.json"
    blob_items = [
        SimpleNamespace(
            name=manifest_path,
            last_modified=datetime(2026, 3, 20, 1, 26, 2, tzinfo=timezone.utc),
        )
    ]
    payloads = {
        manifest_path: json.dumps(manifest).encode("utf-8"),
    }

    monkeypatch.setattr(pipeline_store, "_db_connect", lambda: None)
    monkeypatch.setattr(pipeline_store, "_blob_service_client", lambda: _FakeBlobServiceClient(payloads, blob_items))

    metadata = pipeline_store.latest_dataset_metadata("attention_feed")

    assert metadata is not None
    assert metadata.dataset_name == "attention_feed"
    assert metadata.dataset_version_id == manifest["dataset_version_id"]
    assert metadata.blob_path == manifest["blob_path"]
    assert metadata.row_count == 3


def test_load_latest_dataset_frame_reads_parquet_via_manifest_fallback(monkeypatch):
    manifest = {
        "dataset_name": "attention_feed",
        "dataset_version_id": "attention_feed__20260320T012554Z__16918b7a",
        "blob_path": "datasets/attention_feed/dt=2026-03-20/asof=2026-03-20T01-25-54Z/universe=20260313/part-16918b7a.parquet",
        "asof_time_utc": "2026-03-20T01:25:54Z",
        "ingested_at_utc": "2026-03-20T01:26:02Z",
        "row_count": 2,
    }
    frame = pd.DataFrame(
        {
            "feed_rank": [1, 2],
            "entity_id": ["ABBV", "TSLA"],
            "attention_score": [63.6, 53.7],
        }
    )
    parquet_buffer = BytesIO()
    frame.to_parquet(parquet_buffer, index=False)

    manifest_path = "manifests/attention_feed/attention_feed__20260320T012554Z__16918b7a.json"
    data_path = manifest["blob_path"]
    blob_items = [
        SimpleNamespace(
            name=manifest_path,
            last_modified=datetime(2026, 3, 20, 1, 26, 2, tzinfo=timezone.utc),
        )
    ]
    payloads = {
        manifest_path: json.dumps(manifest).encode("utf-8"),
        data_path: parquet_buffer.getvalue(),
    }

    monkeypatch.setattr(pipeline_store, "_db_connect", lambda: None)
    monkeypatch.setattr(pipeline_store, "_blob_service_client", lambda: _FakeBlobServiceClient(payloads, blob_items))

    loaded, metadata = pipeline_store.load_latest_dataset_frame("attention_feed")

    assert metadata is not None
    assert metadata.dataset_version_id == manifest["dataset_version_id"]
    assert loaded["entity_id"].tolist() == ["ABBV", "TSLA"]
    assert loaded["attention_score"].tolist() == [63.6, 53.7]


def test_latest_dataset_metadata_uses_local_metadata_cache(monkeypatch, tmp_path):
    manifest = {
        "dataset_name": "attention_feed",
        "dataset_version_id": "attention_feed__20260320T012554Z__16918b7a",
        "blob_path": "datasets/attention_feed/part-16918b7a.parquet",
        "asof_time_utc": "2026-03-20T01:25:54Z",
        "ingested_at_utc": "2026-03-20T01:26:02Z",
        "row_count": 3,
    }
    manifest_path = "manifests/attention_feed/attention_feed__20260320T012554Z__16918b7a.json"
    blob_items = [
        SimpleNamespace(
            name=manifest_path,
            last_modified=datetime(2026, 3, 20, 1, 26, 2, tzinfo=timezone.utc),
        )
    ]
    payloads = {manifest_path: json.dumps(manifest).encode("utf-8")}

    monkeypatch.setattr(pipeline_store, "PIPELINE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(pipeline_store, "PIPELINE_METADATA_CACHE_SECONDS", 3600)
    monkeypatch.setattr(pipeline_store, "_db_connect", lambda: None)
    monkeypatch.setattr(pipeline_store, "_blob_service_client", lambda: _FakeBlobServiceClient(payloads, blob_items))

    first = pipeline_store.latest_dataset_metadata("attention_feed")

    monkeypatch.setattr(
        pipeline_store,
        "_blob_service_client",
        lambda: (_ for _ in ()).throw(AssertionError("metadata cache should avoid blob lookup")),
    )

    second = pipeline_store.latest_dataset_metadata("attention_feed")

    assert first is not None
    assert second is not None
    assert second.dataset_version_id == first.dataset_version_id


def test_load_latest_dataset_frame_uses_local_frame_cache(monkeypatch, tmp_path):
    metadata = pipeline_store.PipelineDataset(
        dataset_name="attention_feed",
        dataset_version_id="attention_feed__20260320T012554Z__16918b7a",
        blob_path="datasets/attention_feed/part-16918b7a.parquet",
        asof_time_utc="2026-03-20T01:25:54Z",
        ingested_at_utc="2026-03-20T01:26:02Z",
        row_count=2,
    )
    frame = pd.DataFrame(
        {
            "feed_rank": [1, 2],
            "entity_id": ["ABBV", "TSLA"],
            "attention_score": [63.6, 53.7],
        }
    )

    monkeypatch.setattr(pipeline_store, "PIPELINE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(pipeline_store, "latest_dataset_metadata", lambda dataset_name: metadata)
    monkeypatch.setattr(pipeline_store, "_read_blob_parquet", lambda blob_path: frame.copy())

    first, first_meta = pipeline_store.load_latest_dataset_frame("attention_feed")

    monkeypatch.setattr(
        pipeline_store,
        "_read_blob_parquet",
        lambda blob_path: (_ for _ in ()).throw(AssertionError("frame cache should avoid blob download")),
    )

    second, second_meta = pipeline_store.load_latest_dataset_frame("attention_feed")

    assert first_meta == metadata
    assert second_meta == metadata
    assert first["entity_id"].tolist() == ["ABBV", "TSLA"]
    assert second["entity_id"].tolist() == ["ABBV", "TSLA"]


def test_prune_pipeline_cache_removes_oldest_entries_when_limit_exceeded(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline_store, "PIPELINE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(pipeline_store, "_pipeline_cache_max_bytes", lambda: 150)

    old_dir = tmp_path / "attention_feed" / "attention_feed__old"
    old_dir.mkdir(parents=True)
    (old_dir / "frame.pkl").write_bytes(b"a" * 90)

    new_dir = tmp_path / "attention_feed" / "attention_feed__new"
    new_dir.mkdir(parents=True)
    keep_path = new_dir / "frame.pkl"
    keep_path.write_bytes(b"b" * 90)

    old_mtime = datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp()
    new_mtime = datetime(2026, 4, 2, tzinfo=timezone.utc).timestamp()
    os.utime(old_dir, (old_mtime, old_mtime))
    os.utime(old_dir / "frame.pkl", (old_mtime, old_mtime))
    os.utime(new_dir, (new_mtime, new_mtime))
    os.utime(keep_path, (new_mtime, new_mtime))

    pipeline_store._prune_pipeline_cache(keep_paths=(keep_path,))

    assert keep_path.exists()
    assert not old_dir.exists()
    assert pipeline_store._path_size_bytes(tmp_path) <= 150


def test_write_local_frame_cache_skips_oversize_frame(monkeypatch, tmp_path):
    metadata = pipeline_store.PipelineDataset(
        dataset_name="attention_feed",
        dataset_version_id="attention_feed__20260320T012554Z__16918b7a",
        blob_path="datasets/attention_feed/part-16918b7a.parquet",
        asof_time_utc="2026-03-20T01:25:54Z",
        ingested_at_utc="2026-03-20T01:26:02Z",
        row_count=2,
    )
    frame = pd.DataFrame({"value": ["x" * 256 for _ in range(128)]})

    monkeypatch.setattr(pipeline_store, "PIPELINE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(pipeline_store, "_pipeline_cache_max_bytes", lambda: 128)

    pipeline_store._write_local_frame_cache(metadata, frame)

    assert not pipeline_store._local_frame_cache_path(metadata).exists()
