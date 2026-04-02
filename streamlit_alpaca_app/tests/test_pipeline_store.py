from __future__ import annotations

import json
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


def test_pipeline_store_configured_with_storage_only(monkeypatch):
    monkeypatch.setattr(pipeline_store, "_storage_account_url", lambda: "https://example.blob.core.windows.net")
    monkeypatch.setattr(pipeline_store, "_postgres_connection_string", lambda: "")

    assert pipeline_store.pipeline_store_configured() is True


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
