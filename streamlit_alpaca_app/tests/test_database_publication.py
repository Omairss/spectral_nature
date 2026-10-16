import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
import pytest

from services import pipeline_store as store


class BlobStore:
    def __init__(self):
        self.objects = {}
        self.lock = threading.Lock()
        self.reads = 0

    def get_blob_client(self, *, container, blob):
        parent = self

        class Blob:
            def download_blob(self, **kwargs):
                with parent.lock:
                    parent.reads += 1
                    if blob not in parent.objects:
                        raise ResourceNotFoundError("Absent")
                    payload, etag = parent.objects[blob]
                return SimpleNamespace(readall=lambda: payload, properties=SimpleNamespace(etag=etag))

            def upload_blob(self, payload, overwrite=False, etag=None, **kwargs):
                with parent.lock:
                    prior = parent.objects.get(blob)
                    if prior and not overwrite:
                        raise ResourceExistsError("Existing")
                    if etag is not None and (not prior or prior[1] != etag):
                        raise ResourceModifiedError("Concurrent update")
                    parent.objects[blob] = (payload, (prior[1] if prior else 0) + 1)

        return Blob()


def manifest(generation):
    return {"dataset_name": "qa_publication", "dataset_version_id": f"version_{generation}",
            "blob_path": f"qa/{generation}.parquet", "dataset_namespace": "development",
            "asof_time_utc": f"2026-10-16T{generation:02d}:00:00Z",
            "ingested_at_utc": f"2026-10-16T{generation:02d}:01:00Z", "row_count": 1}


@pytest.fixture
def blobs(monkeypatch, tmp_path):
    blobs = BlobStore()
    monkeypatch.setenv("PIPELINE_DATASET_NAMESPACE", "development")
    monkeypatch.setattr(store, "PIPELINE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(store, "_blob_service_client", lambda: blobs)
    return blobs


def test_concurrent_publication_never_replaces_newer_generation(blobs):
    with ThreadPoolExecutor(max_workers=6) as workers:
        list(workers.map(store.publish_dataset_manifest, map(manifest, [4, 1, 7, 3, 6, 2])))
    path = store.dataset_blob_path("manifests/qa_publication/latest.json")
    assert json.loads(blobs.objects[path][0])["dataset_version_id"] == "version_7"
    store.publish_dataset_manifest(manifest(0))
    assert json.loads(blobs.objects[path][0])["dataset_version_id"] == "version_7"


def test_publication_and_cached_reads_remain_available_without_sql(blobs, monkeypatch):
    store.publish_dataset_manifest(manifest(4))
    monkeypatch.setattr(store, "_db_connect", lambda: pytest.fail("Published serving tried SQL"))
    store._metadata_cache_path("qa_publication").unlink()
    blobs.reads = 0

    original = store._stable_latest_manifest_metadata

    def slow(name):
        time.sleep(.05)
        return original(name)

    monkeypatch.setattr(store, "_stable_latest_manifest_metadata", slow)
    with ThreadPoolExecutor(max_workers=8) as workers:
        outputs = list(workers.map(lambda _: store.latest_dataset_metadata("qa_publication"), range(8)))
    assert all(item.dataset_version_id == "version_4" for item in outputs)
    assert blobs.reads == 1


def test_cross_environment_publication_is_rejected(blobs):
    wrong = {**manifest(4), "dataset_namespace": "production"}
    with pytest.raises(ValueError):
        store.publish_dataset_manifest(wrong)
    assert not blobs.objects


def test_usage_read_does_not_start_backlog_recovery(monkeypatch):
    monkeypatch.setattr(store, "reconcile_provider_attempt_receipts", lambda **_: pytest.fail("Read started recovery"))
    monkeypatch.setattr(store, "_db_connect", lambda: None)
    result = store.provider_usage_rollup()
    assert result.empty and result.attrs["receipt_reconciliation"]["deferred"]


def test_recovery_retains_pending_receipts_until_successful_sql_commit(monkeypatch):
    from services import llm
    events = {"first.json": {"settings_scope": "qa"}, "second.json": {"settings_scope": "qa"}}
    prefix = "archive/" + store.datetime.now(store.timezone.utc).strftime("%Y-%m-%d") + "/"
    removed = []

    class Container:
        def walk_blobs(self, **kwargs):
            return [SimpleNamespace(name=prefix + name) for name in list(events)]

        def get_blob_client(self, name):
            key = name[len(prefix):]
            return SimpleNamespace(download_blob=lambda **_: SimpleNamespace(readall=lambda: json.dumps({"key": key}).encode()),
                                   delete_blob=lambda **_: (removed.append(key), events.pop(key)))

    archive = SimpleNamespace(root="archive", container=Container(), get=lambda receipt: json.dumps(events[receipt["key"]]).encode())
    monkeypatch.setattr(store, "ImmutableBlobArchive", lambda **_: archive)
    monkeypatch.setattr(llm, "llm_settings_scope", lambda: "qa")
    monkeypatch.setattr(store, "record_provider_attempt", lambda *a, **kw: False)
    assert store.reconcile_provider_attempt_receipts(days=1, limit=1) == {"reconciled": 0, "scan_complete": False}
    assert len(events) == 2 and not removed
    monkeypatch.setattr(store, "record_provider_attempt", lambda *a, **kw: True)
    assert store.reconcile_provider_attempt_receipts(days=1, limit=1) == {"reconciled": 1, "scan_complete": False}
    assert len(events) == 1
    assert store.reconcile_provider_attempt_receipts(days=1, limit=1) == {"reconciled": 1, "scan_complete": True}
    assert not events and len(removed) == 2
