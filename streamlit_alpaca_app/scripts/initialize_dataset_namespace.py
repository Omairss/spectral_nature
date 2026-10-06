#!/usr/bin/env python3
"""Initialize one materialized-dataset namespace from another namespace's latest snapshots."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.pipeline_store import (  # noqa: E402
    _blob_service_client,
    _storage_container,
    dataset_blob_path,
    dataset_version_id_for_namespace,
    logical_dataset_blob_path,
    normalize_dataset_namespace,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy each selected latest dataset snapshot into an isolated dataset namespace."
    )
    parser.add_argument("--source", default="production", help="Source namespace. Default: production")
    parser.add_argument("--destination", required=True, help="Destination namespace, such as development")
    parser.add_argument(
        "--dataset",
        action="append",
        dest="datasets",
        help="Dataset to copy. Repeat to select multiple; omit to copy every latest dataset.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace already initialized destination snapshots.")
    parser.add_argument("--dry-run", action="store_true", help="List the copy without writing blobs.")
    return parser


def _latest_manifest_paths(container: Any, *, source_namespace: str) -> list[str]:
    prefix = dataset_blob_path("manifests/", namespace=source_namespace)
    return sorted(
        str(blob.name)
        for blob in container.list_blobs(name_starts_with=prefix)
        if str(blob.name).endswith("/latest.json")
    )


def _load_manifest(container: Any, path: str) -> dict[str, Any]:
    payload = container.get_blob_client(path).download_blob().readall()
    manifest = json.loads(payload.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"Manifest is not an object: {path}")
    return manifest


def _copy_blob(container: Any, source_path: str, destination_path: str) -> None:
    source = container.get_blob_client(source_path)
    destination = container.get_blob_client(destination_path)
    with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as stream:
        source.download_blob(max_concurrency=4).readinto(stream)
        stream.seek(0)
        destination.upload_blob(stream, overwrite=True, max_concurrency=4)


def initialize_namespace(
    *,
    source_namespace: str,
    destination_namespace: str,
    selected_datasets: set[str] | None,
    overwrite: bool,
    dry_run: bool,
) -> dict[str, Any]:
    client = _blob_service_client()
    if client is None:
        raise RuntimeError("Azure Blob Storage is not configured or credentials are unavailable.")
    container_name = _storage_container()
    container = client.get_container_client(container_name)
    latest_paths = _latest_manifest_paths(container, source_namespace=source_namespace)
    if not latest_paths:
        raise RuntimeError(f"No latest manifests found in source namespace {source_namespace or 'production'}.")

    copied: list[str] = []
    skipped: list[str] = []
    discovered: set[str] = set()
    initialized_at = datetime.now(timezone.utc).isoformat()

    for latest_path in latest_paths:
        manifest = _load_manifest(container, latest_path)
        dataset_name = str(manifest.get("dataset_name") or "").strip()
        if not dataset_name:
            raise ValueError(f"Manifest has no dataset_name: {latest_path}")
        discovered.add(dataset_name)
        if selected_datasets and dataset_name not in selected_datasets:
            continue

        destination_latest_path = dataset_blob_path(
            f"manifests/{dataset_name}/latest.json",
            namespace=destination_namespace,
        )
        destination_latest = container.get_blob_client(destination_latest_path)
        if destination_latest.exists() and not overwrite:
            skipped.append(dataset_name)
            print(f"[skip] {dataset_name}: destination latest manifest already exists", flush=True)
            continue

        source_data_path = str(manifest.get("blob_path") or "").strip()
        source_version_id = str(manifest.get("dataset_version_id") or "").strip()
        if not source_data_path or not source_version_id:
            raise ValueError(f"Manifest is missing blob_path or dataset_version_id: {latest_path}")
        logical_data_path = logical_dataset_blob_path(source_data_path, namespace=source_namespace)
        destination_data_path = dataset_blob_path(logical_data_path, namespace=destination_namespace)
        destination_version_id = dataset_version_id_for_namespace(
            source_version_id,
            namespace=destination_namespace,
        )
        destination_version_path = dataset_blob_path(
            f"manifests/{dataset_name}/{destination_version_id}.json",
            namespace=destination_namespace,
        )

        destination_manifest = dict(manifest)
        destination_manifest.update(
            {
                "dataset_name": dataset_name,
                "dataset_namespace": destination_namespace,
                "dataset_version_id": destination_version_id,
                "blob_path": destination_data_path,
                "initialized_at_utc": initialized_at,
                "initialized_from_namespace": source_namespace or "production",
                "source_dataset_version_id": source_version_id,
                "source_blob_path": source_data_path,
            }
        )
        print(
            f"[copy] {dataset_name}: {source_data_path} -> {destination_data_path}",
            flush=True,
        )
        if not dry_run:
            _copy_blob(container, source_data_path, destination_data_path)
            payload = json.dumps(destination_manifest, ensure_ascii=False, default=str, indent=2).encode("utf-8")
            container.get_blob_client(destination_version_path).upload_blob(
                payload,
                overwrite=True,
                content_type="application/json",
            )
            destination_latest.upload_blob(payload, overwrite=True, content_type="application/json")
        copied.append(dataset_name)

    missing = sorted((selected_datasets or set()) - discovered)
    if missing:
        raise RuntimeError(f"Selected datasets have no source latest manifest: {', '.join(missing)}")
    return {
        "container": container_name,
        "source_namespace": source_namespace or "production",
        "destination_namespace": destination_namespace,
        "copied_count": len(copied),
        "skipped_count": len(skipped),
        "copied_datasets": copied,
        "skipped_datasets": skipped,
        "dry_run": dry_run,
    }


def main() -> None:
    args = _parser().parse_args()
    source_namespace = normalize_dataset_namespace(args.source)
    destination_namespace = normalize_dataset_namespace(args.destination)
    if not destination_namespace:
        raise SystemExit("Destination must be a non-production namespace.")
    if source_namespace == destination_namespace:
        raise SystemExit("Source and destination namespaces must differ.")
    selected = {str(name).strip() for name in list(args.datasets or []) if str(name).strip()} or None
    result = initialize_namespace(
        source_namespace=source_namespace,
        destination_namespace=destination_namespace,
        selected_datasets=selected,
        overwrite=bool(args.overwrite),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
