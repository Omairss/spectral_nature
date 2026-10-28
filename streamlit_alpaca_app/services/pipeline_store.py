from __future__ import annotations

from services.database import open_database_session
from services.work_scope import check_work, WorkCancelled

from dataclasses import asdict, dataclass, field
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import threading
import tempfile
from typing import Any, Iterator
import uuid

import pandas as pd
import requests
from psycopg import OperationalError
from azure.storage.blob import BlobServiceClient

from .secrets import build_azure_credential, resolve_secret_value
from .json_utils import to_jsonable


APP_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_CACHE_ROOT = APP_ROOT / "cache" / "pipeline_store"
PIPELINE_METADATA_CACHE_SECONDS = max(int((os.getenv("PIPELINE_METADATA_CACHE_SECONDS") or "30").strip() or "30"), 0)
PIPELINE_CACHE_MAX_BYTES_DEFAULT = 256 * 1024 * 1024
_METADATA_READ_LOCKS = tuple(threading.RLock() for _ in range(32))
ARM_BASE_URL = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
SUBSCRIPTIONS_API_VERSION = "2020-01-01"
CONTAINERAPP_JOBS_API_VERSION = "2025-07-01"


@dataclass(frozen=True)
class PipelineDataset:
    dataset_name: str
    dataset_version_id: str
    blob_path: str
    asof_time_utc: str
    ingested_at_utc: str
    row_count: int
    partitioning: dict[str, Any] = field(default_factory=dict)


SOURCE_JOB_MAP: dict[str, str] = {
    "equities": "equities-intraday-preload",
    "fred": "macro-fred-daily",
    "commodities": "commodities-regime",
    "options": "options-liquid-universe",
    "news": "news-ingest-and-features",
    "attention": "attention-home-build",
    "taxonomy": "entity-taxonomy-refresh",
    "company_baseline": "company-baseline-prefetch",
    "fundamentals": "fundamentals-quarterly-refresh",
    "derivatives": "equities-intraday-preload",
    "trading_agent": "trading-agent-build",
}

SOURCE_DATASETS: dict[str, list[str]] = {
    "equities": [
        "universe_snapshot",
        "daily_movers",
        "macro_anchor_daily_movers",
        "positions_snapshot",
        "portfolio_timeseries_snapshot",
        "momentum_profiles",
        "price_history",
    ],
    "fred": [
        "fred_summary",
        "fred_observations",
        "fred_series_index",
        "fred_release_index",
        "yield_curve_observations",
        "yield_curve_summary",
        "yield_curve_facts_1d",
    ],
    "commodities": [
        "commodity_regime_summary",
        "commodity_regime_history",
        "commodity_peer_group_membership",
        "commodity_price_expectations",
        "commodity_attention_candidates",
        "commodity_anomaly_events",
        "commodity_attention_rollups",
        "commodity_attention_feed",
    ],
    "options": ["option_expirations", "option_contract_snapshots"],
    "news": [
        "news_articles",
        "news_symbol_map",
        "edgar_filings",
        "edgar_evidence",
        "attention_context_llm",
        "attention_context_bundle",
        "zopedia_business_model_research_plans",
        "zopedia_business_model_search_requests",
        "zopedia_business_model_search_results",
        "zopedia_ticker_business_model_stacks",
        "zopedia_news_business_resolutions",
        "zopedia_company_business_memory_pages",
        "zopedia_memory_commit_report",
    ],
    "attention": [
        "attention_web_search_news",
        "attention_candidates_1d",
        "attention_research_plans",
        "attention_search_requests",
        "attention_search_results",
        "attention_source_documents",
        "attention_evidence_chunks",
        "attention_claims",
        "attention_candidate_graph",
        "attention_event_clusters_1d",
        "macro_release_events_1d",
        "attention_macro_context_1d",
        "macro_causal_graph_edges_v1",
        "macro_relationship_checks_1d",
        "attention_hypotheses_1d",
        "knowledge_graph_update_proposals",
        "attention_ticker_snapshots_1d",
        "daily_market_datapoints_1d",
        "attention_ticker_background_snapshots",
        "attention_ticker_zopedia_enrichments",
        "market_opportunity_feed",
        "page_agentic_summaries",
        "attention_home_snapshots_1d",
        "attention_bundle_snapshots",
        "attention_home_1d",
        "attention_research_bundles",
    ],
    "taxonomy": ["us_equity_listings", "entity_taxonomy_labels", "company_baselines"],
    "company_baseline": ["company_baselines"],
    "fundamentals": ["quarterly_fundamentals"],
    "derivatives": [
        "taxonomy_peer_group_membership",
        "taxonomy_peer_group_catalog",
        "correlation_phase_shift_summary",
        "correlation_phase_shift_history",
        "technical_signals_latest",
        "technical_signal_history",
        "peer_group_membership",
        "price_expectations",
        "attention_candidates",
        "anomaly_events",
        "attention_rollups",
        "attention_feed",
    ],
    "trading_agent": [
        "trading_agent_research_packages",
        "trading_agent_ranked_opportunities",
        "trading_agent_runs",
        "trading_agent_candidates",
        "trading_agent_outcomes",
        "trading_agent_research_reviews",
    ],
}


_DATASET_NAMESPACE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
SYMBOL_HASH_PARTITION_STRATEGY = "symbol_hash_v1"
SYMBOL_HASH_PARTITION_BUCKETS = 64


def symbol_partition_bucket(value: object, *, bucket_count: int = SYMBOL_HASH_PARTITION_BUCKETS) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        raise ValueError("Symbol partition value is required.")
    bounded_count = max(1, min(int(bucket_count), 256))
    bucket = int(hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8], 16) % bounded_count
    width = max(2, len(f"{bounded_count - 1:x}"))
    return f"{bucket:0{width}x}"


def normalize_dataset_namespace(value: object | None = None) -> str:
    """Return the validated materialized-dataset namespace.

    The empty namespace is the existing production storage contract. Explicit
    names are isolated in blob storage, Postgres metadata, and local caches.
    """
    raw = os.getenv("PIPELINE_DATASET_NAMESPACE", "") if value is None else value
    namespace = str(raw or "").strip().lower()
    if namespace in {"", "prod", "production", "default"}:
        return ""
    if not _DATASET_NAMESPACE_PATTERN.fullmatch(namespace):
        raise ValueError(
            "PIPELINE_DATASET_NAMESPACE must start with a lowercase letter or digit "
            "and contain only lowercase letters, digits, underscores, or hyphens."
        )
    return namespace


def dataset_namespace() -> str:
    return normalize_dataset_namespace()


def dataset_blob_path(path: object, *, namespace: object | None = None) -> str:
    logical_path = str(path or "").strip().lstrip("/")
    if not logical_path:
        raise ValueError("Dataset blob path is required.")
    resolved_namespace = normalize_dataset_namespace(namespace)
    if not resolved_namespace:
        return logical_path
    prefix = f"namespaces/{resolved_namespace}/"
    if logical_path.startswith(prefix):
        return logical_path
    if logical_path.startswith("namespaces/"):
        raise ValueError("Dataset blob path belongs to a different namespace.")
    return f"{prefix}{logical_path}"


def logical_dataset_blob_path(path: object, *, namespace: object | None = None) -> str:
    physical_path = str(path or "").strip().lstrip("/")
    resolved_namespace = normalize_dataset_namespace(namespace)
    if not resolved_namespace:
        if physical_path.startswith("namespaces/"):
            raise ValueError("Namespaced blob path cannot be read as production data.")
        return physical_path
    prefix = f"namespaces/{resolved_namespace}/"
    if not physical_path.startswith(prefix):
        raise ValueError("Dataset blob path does not belong to the requested namespace.")
    return physical_path[len(prefix) :]


def dataset_storage_name(dataset_name: object, *, namespace: object | None = None) -> str:
    canonical_name = str(dataset_name or "").strip()
    if not canonical_name:
        raise ValueError("Dataset name is required.")
    resolved_namespace = normalize_dataset_namespace(namespace)
    return f"{resolved_namespace}::{canonical_name}" if resolved_namespace else canonical_name


def dataset_version_id_for_namespace(version_id: object, *, namespace: object | None = None) -> str:
    canonical_id = str(version_id or "").strip()
    if not canonical_id:
        raise ValueError("Dataset version id is required.")
    resolved_namespace = normalize_dataset_namespace(namespace)
    if not resolved_namespace:
        return canonical_id
    prefix = f"{resolved_namespace}__"
    return canonical_id if canonical_id.startswith(prefix) else f"{prefix}{canonical_id}"


def pipeline_job_resource_name(job_name: object) -> str:
    canonical_name = str(job_name or "").strip()
    if not canonical_name:
        raise ValueError("Pipeline job name is required.")
    namespace = dataset_namespace()
    if not namespace:
        return canonical_name
    suffix = "dev" if namespace == "development" else namespace
    return f"{canonical_name}-{suffix}"

def _deployment_env_paths() -> tuple[Path, ...]:
    override = (os.getenv("DEPLOYMENT_ENV_FILE") or "").strip()
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


def _load_deployment_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for env_file in _deployment_env_paths():
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            clean = line.strip()
            if not clean or clean.startswith("#") or "=" not in clean:
                continue
            key, value = clean.split("=", 1)
            values[key.strip()] = value.strip()
        if values:
            return values
    return values


def _pipeline_cache_dir(dataset_name: str, dataset_version_id: str) -> Path:
    dataset_name = dataset_storage_name(dataset_name)
    safe_dataset = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(dataset_name or "").strip())
    safe_version = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(dataset_version_id or "").strip())
    return PIPELINE_CACHE_ROOT / safe_dataset / safe_version


def _metadata_cache_path(dataset_name: str) -> Path:
    dataset_name = dataset_storage_name(dataset_name)
    safe_dataset = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(dataset_name or "").strip())
    return PIPELINE_CACHE_ROOT / "_metadata" / f"{safe_dataset}.json"


def _pipeline_cache_max_bytes() -> int:
    raw = (os.getenv("PIPELINE_CACHE_MAX_BYTES") or "").strip()
    if not raw:
        return PIPELINE_CACHE_MAX_BYTES_DEFAULT
    try:
        return max(int(raw), 0)
    except Exception:
        return PIPELINE_CACHE_MAX_BYTES_DEFAULT


def _pipeline_cache_enabled() -> bool:
    return _pipeline_cache_max_bytes() > 0


def _path_size_bytes(path: Path) -> int:
    try:
        if path.is_file():
            return int(path.stat().st_size)
    except Exception:
        return 0
    if not path.exists():
        return 0
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += int(child.stat().st_size)
            except Exception:
                continue
    except Exception:
        return 0
    return total


def _path_mtime_epoch(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except Exception:
        return 0.0


def _cache_paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _iter_prunable_cache_paths() -> list[Path]:
    if not PIPELINE_CACHE_ROOT.exists():
        return []
    candidates: list[Path] = []
    metadata_dir = PIPELINE_CACHE_ROOT / "_metadata"
    if metadata_dir.exists():
        try:
            for child in metadata_dir.iterdir():
                if child.name.startswith("."):
                    continue
                candidates.append(child)
        except Exception:
            pass
    try:
        for dataset_dir in PIPELINE_CACHE_ROOT.iterdir():
            if dataset_dir.name.startswith(".") or dataset_dir.name == "_metadata" or not dataset_dir.is_dir():
                continue
            try:
                for child in dataset_dir.iterdir():
                    if child.name.startswith("."):
                        continue
                    candidates.append(child)
            except Exception:
                continue
    except Exception:
        return []
    return sorted(candidates, key=lambda path: (_path_mtime_epoch(path), str(path)))


def _remove_empty_cache_dirs(start: Path) -> None:
    current = start
    while current != PIPELINE_CACHE_ROOT and current.exists():
        try:
            next(current.iterdir())
            break
        except StopIteration:
            try:
                current.rmdir()
            except Exception:
                break
            current = current.parent
        except Exception:
            break


def _delete_cache_path(path: Path) -> None:
    if not path.exists():
        return
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
            _remove_empty_cache_dirs(path.parent)
    except Exception:
        return


def _prune_pipeline_cache(*, keep_paths: tuple[Path, ...] = ()) -> None:
    max_bytes = _pipeline_cache_max_bytes()
    if max_bytes <= 0 or not PIPELINE_CACHE_ROOT.exists():
        return
    keep = tuple(path.resolve() for path in keep_paths if path.exists())
    usage = _path_size_bytes(PIPELINE_CACHE_ROOT)
    if usage <= max_bytes:
        return
    for candidate in _iter_prunable_cache_paths():
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if any(_cache_paths_overlap(resolved, keep_path) for keep_path in keep):
            continue
        size_before = _path_size_bytes(candidate)
        _delete_cache_path(candidate)
        usage = max(0, usage - size_before)
        if usage <= max_bytes:
            break


def _read_cached_metadata(dataset_name: str) -> PipelineDataset | None:
    if PIPELINE_METADATA_CACHE_SECONDS <= 0 or not _pipeline_cache_enabled():
        return None
    path = _metadata_cache_path(dataset_name)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    cached_at = float(payload.get("cached_at_epoch") or 0.0)
    if cached_at <= 0 or (time.time() - cached_at) > PIPELINE_METADATA_CACHE_SECONDS:
        return None
    dataset = _coerce_manifest_dataset(dict(payload.get("dataset") or {}))
    if dataset is None or dataset.dataset_name != dataset_name:
        return None
    return dataset


def _write_cached_metadata(dataset_name: str, dataset: PipelineDataset | None) -> None:
    if PIPELINE_METADATA_CACHE_SECONDS <= 0 or dataset is None or not _pipeline_cache_enabled():
        return
    try:
        lock = _METADATA_READ_LOCKS[int.from_bytes(hashlib.sha256(dataset_storage_name(dataset_name).encode()).digest()[:2], "big") % len(_METADATA_READ_LOCKS)]
        with lock:
            path = _metadata_cache_path(dataset_name)
            try:
                prior = json.loads(path.read_text())["dataset"]
            except (OSError, ValueError, KeyError):
                prior = None
            if prior is not None and _publication_generation(prior) > _publication_generation(asdict(dataset)):
                return
            path = _metadata_cache_path(dataset_name)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "cached_at_epoch": time.time(),
                "dataset": {
                    "dataset_name": dataset.dataset_name,
                    "dataset_version_id": dataset.dataset_version_id,
                    "blob_path": dataset.blob_path,
                    "asof_time_utc": dataset.asof_time_utc,
                    "ingested_at_utc": dataset.ingested_at_utc,
                    "row_count": dataset.row_count,
                    "partitioning": dataset.partitioning,
                },
            }
            temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_text(json.dumps(payload), encoding="utf-8")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            max_bytes = _pipeline_cache_max_bytes()
            if _path_size_bytes(path) > max_bytes:
                _delete_cache_path(path)
                return
            _prune_pipeline_cache(keep_paths=(path,))
    except OSError:
        return  # A cache failure cannot invalidate a completed publication.


def _get_env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _postgres_connection_string() -> str:
    deployment = _load_deployment_env()
    secret_name = (
        _get_env("POSTGRES_CONNECTION_STRING_SECRET")
        or _get_env("POSTGRES_CONNECTION_STRING_SECRET_NAME")
        or str(deployment.get("POSTGRES_CONNECTION_STRING_SECRET") or "").strip()
        or str(deployment.get("POSTGRES_CONNECTION_STRING_SECRET_NAME") or "").strip()
        or "postgres-connection-string"
    )
    return resolve_secret_value(
        ["POSTGRES_CONNECTION_STRING"],
        default_secret_name=secret_name,
    )


def _storage_account_url() -> str:
    direct = _get_env("AZURE_STORAGE_ACCOUNT_URL")
    if direct:
        return direct
    deployment = _load_deployment_env()
    return (deployment.get("AZURE_STORAGE_ACCOUNT_URL") or deployment.get("STORAGE_URL") or "").strip()


def _storage_container() -> str:
    return _get_env("AZURE_STORAGE_CONTAINER", "datasets") or "datasets"


def _resource_group() -> str:
    direct = _get_env("PIPELINE_RESOURCE_GROUP") or _get_env("RESOURCE_GROUP")
    if direct:
        return direct
    deployment = _load_deployment_env()
    return (deployment.get("PIPELINE_RESOURCE_GROUP") or deployment.get("RESOURCE_GROUP") or "").strip()


def _azure_subscription_ids() -> list[str]:
    deployment = _load_deployment_env()
    out: list[str] = []
    for name in (
        "PIPELINE_SUBSCRIPTION_ID",
        "AZURE_SUBSCRIPTION_ID",
        "ADMIN_SECURITY_SUBSCRIPTION_ID",
    ):
        value = (_get_env(name) or str(deployment.get(name) or "")).strip()
        if value and value not in out:
            out.append(value)
    return out


def _azure_management_headers() -> tuple[dict[str, str] | None, str]:
    credential = build_azure_credential()
    if credential is None:
        return None, "Azure credentials are unavailable."
    try:
        token = credential.get_token(ARM_SCOPE).token
    except Exception as exc:
        return None, f"Azure management token unavailable: {type(exc).__name__}: {exc}"
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }, ""


def _list_azure_subscription_ids(headers: dict[str, str]) -> list[str]:
    out = _azure_subscription_ids()
    try:
        response = requests.get(
            f"{ARM_BASE_URL}/subscriptions?api-version={SUBSCRIPTIONS_API_VERSION}",
            headers=headers,
            timeout=10,
        )
    except Exception:
        return out
    if response.status_code >= 400:
        return out
    try:
        payload = response.json()
    except Exception:
        return out
    if not isinstance(payload, dict):
        return out
    for item in list(payload.get("value") or []):
        if not isinstance(item, dict):
            continue
        subscription_id = str(item.get("subscriptionId") or "").strip()
        state = str(item.get("state") or "").strip().lower()
        if subscription_id and state == "enabled" and subscription_id not in out:
            out.append(subscription_id)
    return out


def _short_http_error(response: requests.Response) -> str:
    message = str(response.text or "").strip().replace("\n", " ")
    if len(message) > 300:
        message = f"{message[:297]}..."
    return f"HTTP {response.status_code}: {message or response.reason or 'request failed'}"


def _start_source_refresh_job_via_arm(job_name: str, resource_group: str) -> tuple[bool, str]:
    headers, header_error = _azure_management_headers()
    if headers is None:
        return False, header_error
    subscription_ids = _list_azure_subscription_ids(headers)
    if not subscription_ids:
        return False, "Azure subscription id is unavailable."

    errors: list[str] = []
    for subscription_id in subscription_ids:
        url = (
            f"{ARM_BASE_URL}/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.App/jobs/{job_name}/start"
            f"?api-version={CONTAINERAPP_JOBS_API_VERSION}"
        )
        try:
            response = requests.post(url, headers=headers, json={}, timeout=30)
        except Exception as exc:
            errors.append(f"{subscription_id}: {type(exc).__name__}: {exc}")
            continue
        if response.status_code in {200, 201, 202}:
            execution = ""
            try:
                payload = response.json()
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                execution = str(payload.get("name") or "").strip()
                if not execution:
                    execution = str(payload.get("id") or "").strip().rsplit("/", 1)[-1]
            return True, f"Triggered `{job_name}` execution `{execution or 'started'}`"
        errors.append(f"{subscription_id}: {_short_http_error(response)}")
        if response.status_code != 404:
            break
    return False, "; ".join(errors) or "Azure management API did not start the job."


def pipeline_store_configured() -> bool:
    return bool(_storage_account_url())


def open_database_connection() -> Any | None:
    """Open the application store; unconfigured or unavailable stores return None."""
    conn_str = _postgres_connection_string()
    if not conn_str:
        return None
    try:
        return open_database_session(conn_str)
    except OperationalError:
        return None


def index_dataset_manifest(conn: Any, manifest: dict[str, Any]) -> None:
    """Index the same committed Blob receipt; PostgreSQL is a recoverable index."""
    if manifest.get("dataset_namespace", "") != dataset_namespace():
        raise ValueError("Dataset receipt belongs to another environment")
    checksum = hashlib.sha256(
        f"{manifest.get('blob_path','')}|{manifest.get('row_count',0)}|{manifest.get('asof_time_utc','')}".encode()
    ).hexdigest()
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO dataset_versions (
            dataset_version_id,dataset_name,universe_version,parameter_hash,asof_time_utc,ingested_at_utc,
            blob_path,row_count,checksum,code_version,schema_version,status,run_id,schema_columns)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ready',%s,%s::jsonb)
            ON CONFLICT(dataset_version_id) DO UPDATE SET
                dataset_name=EXCLUDED.dataset_name,universe_version=EXCLUDED.universe_version,
                parameter_hash=EXCLUDED.parameter_hash,asof_time_utc=EXCLUDED.asof_time_utc,
                ingested_at_utc=EXCLUDED.ingested_at_utc,blob_path=EXCLUDED.blob_path,row_count=EXCLUDED.row_count,
                checksum=EXCLUDED.checksum,code_version=EXCLUDED.code_version,schema_version=EXCLUDED.schema_version,
                status=EXCLUDED.status,run_id=EXCLUDED.run_id,schema_columns=EXCLUDED.schema_columns""",
            (manifest["dataset_version_id"],dataset_storage_name(manifest["dataset_name"]),manifest.get("universe_version"),
             manifest.get("parameter_hash"),manifest["asof_time_utc"],manifest["ingested_at_utc"],manifest["blob_path"],
             int(manifest.get("row_count",0)),checksum,manifest.get("code_version","unknown"),manifest.get("schema_version","v1"),
             manifest["run_id"],json.dumps(manifest.get("schema_columns") or [])))
    conn.commit()


def reconcile_dataset_manifests(conn: Any, *, dataset_names: list[str] | None = None,
                                offset: int = 0, limit: int | None = None) -> dict[str, Any]:
    """Repair missing/stale SQL indexes from current environment Blob receipts."""
    from services.work_scope import work_expired
    names = sorted(set(dataset_names if dataset_names is not None else
                       [name for group in SOURCE_DATASETS.values() for name in group]))
    total = len(names)
    start = min(max(offset, 0), total)
    names = names[start:start + max(limit, 1)] if limit is not None else names[start:]
    indexed = _latest_db_dataset_metadata_map(names)
    result: dict[str, Any] = {"checked": 0, "reconciled": [], "failed": {}, "next_offset": start, "scan_complete": False}
    for name in names:
        if work_expired():
            break
        receipt = _read_blob_json(dataset_blob_path(f"manifests/{name}/latest.json"))
        result["checked"] += 1
        result["next_offset"] = start + result["checked"]
        if not receipt:
            continue
        current = indexed.get(name)
        if current is not None and current.dataset_version_id == receipt.get("dataset_version_id") and (
            _dataset_time_epoch(current.ingested_at_utc) >= _dataset_time_epoch(receipt.get("ingested_at_utc"))
        ):
            continue
        try:
            if receipt.get("dataset_name") != name:
                raise ValueError("Receipt identity differs from its published pointer")
            index_dataset_manifest(conn, receipt)
            result["reconciled"].append(name)
        except Exception as exc:
            conn.rollback()
            result["failed"][name] = type(exc).__name__
    result["scan_complete"] = result["next_offset"] >= total
    if result["scan_complete"]:
        result["next_offset"] = 0
    return result


def retain_job_execution_receipt(receipt: dict[str, Any]) -> str:
    """Keep completion/component state independent of a failed database sink."""
    name = str(receipt["job_name"])
    if not re.fullmatch(r"[a-z0-9_-]+", name):
        raise ValueError("Invalid job receipt name")
    archive = ImmutableBlobArchive(namespace=dataset_namespace(), root="job-executions/" + name)
    stored = archive.put(json.dumps(receipt, sort_keys=True, default=str).encode(), suffix="json", content_type="application/json")
    archive.point(receipt["run_id"] + ".json", stored)
    return archive.point("latest.json", stored)


def latest_job_execution_receipt(job_name: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[a-z0-9_-]+", job_name):
        raise ValueError("Invalid job receipt name")
    archive = ImmutableBlobArchive(namespace=dataset_namespace(), root="job-executions/" + job_name)
    pointer = _read_blob_json(archive.root + "/latest.json")
    return json.loads(archive.get(pointer)) if pointer else None


def _blob_service_client() -> Any | None:
    account_url = _storage_account_url()
    if not account_url:
        return None

    connection_string = _get_env("AZURE_STORAGE_CONNECTION_STRING")
    if connection_string:
        try:
            return BlobServiceClient.from_connection_string(connection_string)
        except Exception:
            pass

    account_key = _get_env("AZURE_STORAGE_ACCOUNT_KEY")
    if account_key:
        try:
            return BlobServiceClient(account_url=account_url, credential=account_key)
        except Exception:
            pass

    credential = build_azure_credential()
    if credential is None:
        return None
    try:
        return BlobServiceClient(account_url=account_url, credential=credential)
    except Exception:
        return None


def _blob_io_options(maximum: float = 10) -> dict[str, Any]:
    from services.work_scope import remaining_work_seconds
    seconds = remaining_work_seconds(maximum)
    return {"timeout": max(1, math.ceil(seconds)), "read_timeout": seconds,
            "connection_timeout": seconds, "retry_total": 0}


class ImmutableBlobArchive:
    """Content-addressed, receipt-verified payloads in the shared dataset store."""

    def __init__(self, *, namespace: str, root: str):
        from azure.storage.blob import ContentSettings
        self._settings = ContentSettings
        self.client = _blob_service_client()
        if self.client is None:
            raise RuntimeError("Blob storage is required for an archive")
        self.container_name = _storage_container()
        self.container = self.client.get_container_client(self.container_name)
        self.root = dataset_blob_path(root, namespace=namespace).rstrip("/")

    def put(self, payload: bytes, *, suffix: str, content_type: str) -> dict[str, Any]:
        from azure.core.exceptions import ResourceExistsError
        digest = hashlib.sha256(payload).hexdigest()
        md5 = hashlib.md5(payload).digest()
        path = f"{self.root}/objects/{digest[:2]}/{digest}.{suffix}"
        blob = self.container.get_blob_client(path)
        try:
            blob.upload_blob(
                payload, overwrite=False, validate_content=True,
                content_settings=self._settings(content_type=content_type, content_md5=md5),
                metadata={"sha256": digest}, max_concurrency=2,
                **_blob_io_options(),
            )
        except ResourceExistsError:
            pass
        props = blob.get_blob_properties(**_blob_io_options())
        if props.size != len(payload) or props.metadata.get("sha256") != digest:
            raise RuntimeError("Immutable archive object verification failed")
        if props.content_settings.content_md5 != md5:
            raise RuntimeError("Archive content MD5 verification failed")
        return {"blob_path": path, "sha256": digest, "bytes": len(payload),
                "etag": str(props.etag), "container": self.container_name,
                "verified_at_utc": datetime.now(timezone.utc).isoformat()}

    def get(self, receipt: dict[str, Any]) -> bytes:
        payload = self.container.get_blob_client(receipt["blob_path"]).download_blob(**_blob_io_options()).readall()
        if hashlib.sha256(payload).hexdigest() != receipt["sha256"]:
            raise RuntimeError("Archive read checksum mismatch")
        return payload

    def point(self, name: str, receipt: dict[str, Any]) -> str:
        """Publish a discoverable mutable pointer to an immutable release."""
        if "/" in name or name in {"", ".", ".."} or not name.endswith(".json"):
            raise ValueError("Archive pointer requires a simple JSON filename")
        payload = json.dumps(receipt, sort_keys=True, default=str).encode()
        md5 = hashlib.md5(payload).digest()
        path = f"{self.root}/{name}"
        blob = self.container.get_blob_client(path)
        blob.upload_blob(payload, overwrite=True, validate_content=True,
            content_settings=self._settings(content_type="application/json", content_md5=md5), **_blob_io_options())
        props = blob.get_blob_properties(**_blob_io_options())
        if props.size != len(payload) or props.content_settings.content_md5 != md5:
            raise RuntimeError("Archive pointer verification failed")
        return path


def _read_blob_parquet(blob_path: str) -> pd.DataFrame:
    client = _blob_service_client()
    if client is None:
        return pd.DataFrame()
    try:
        blob = client.get_blob_client(container=_storage_container(), blob=blob_path)
        payload = blob.download_blob().readall()
        if not payload:
            return pd.DataFrame()
        frame = pd.read_parquet(BytesIO(payload))
        return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
    except Exception:
        return pd.DataFrame()


@contextmanager
def _dataset_parquet_file(dataset: PipelineDataset) -> Iterator[Path]:
    """Stage immutable bytes on disk, avoiding a second full in-memory copy."""
    with tempfile.TemporaryDirectory(prefix="pipeline-parquet-") as temporary:
        cached = _pipeline_cache_enabled()
        path = (_pipeline_cache_dir(dataset.dataset_name, dataset.dataset_version_id) / "frame.parquet"
                if cached else Path(temporary) / "frame.parquet")
        if not path.exists():
            client = _blob_service_client()
            if client is None:
                raise RuntimeError("Dataset storage is unavailable.")
            path.parent.mkdir(parents=True, exist_ok=True)
            pending = path.with_name(path.name + "." + uuid.uuid4().hex + ".pending")
            try:
                blob = client.get_blob_client(container=_storage_container(), blob=dataset.blob_path)
                with pending.open("wb") as output:
                    blob.download_blob(max_concurrency=1).readinto(output)
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)
        try:
            yield path
        finally:
            if cached:
                # A large input may be scanned, but need not remain cached.
                if path.exists() and path.stat().st_size > _pipeline_cache_max_bytes():
                    path.unlink(missing_ok=True)
                else:
                    _prune_pipeline_cache(keep_paths=(path,))


def iter_dataset_frame_batches(
    dataset: PipelineDataset, *, columns: list[str] | None = None,
    symbols: list[str] | None = None, batch_size: int = 65536,
) -> Iterator[pd.DataFrame]:
    """Project/filter before Pandas allocation; keep every matching historical row."""
    import pyarrow.dataset as ds

    with _dataset_parquet_file(dataset) as path:
        source = ds.dataset(path, format="parquet")
        names = source.schema.names
        projection = [column for column in columns if column in names] if columns is not None else None
        predicate = None
        if symbols is not None:
            symbol_column = next((column for column in ("symbol", "ticker") if column in names), None)
            if symbol_column is None:
                raise ValueError("Symbol-filtered dataset requires symbol or ticker.")
            predicate = ds.field(symbol_column).isin(symbols)
        scanner = source.scanner(
            columns=projection, filter=predicate, batch_size=max(int(batch_size), 1),
            batch_readahead=1, fragment_readahead=1, use_threads=False,
        )
        for batch in scanner.to_batches():
            if batch.num_rows:
                yield batch.to_pandas()


def load_dataset_frame(
    dataset: PipelineDataset | None, *, columns: list[str] | None = None, symbols: list[str] | None = None,
) -> pd.DataFrame:
    if dataset is None:
        return pd.DataFrame(columns=columns)
    frames = list(iter_dataset_frame_batches(dataset, columns=columns, symbols=symbols))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)


def dataset_input_versions(dataset_names: list[str]) -> dict[str, PipelineDataset | None]:
    """Pin source versions for retries of one declared public research execution."""
    execution = os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME") or os.getenv("PIPELINE_RUN_ID")
    namespace = "pipeline_inputs:" + dataset_namespace()
    key = str(execution) + ":" + hashlib.sha256(json.dumps(sorted(dataset_names)).encode()).hexdigest()
    owned = bool(execution and os.getenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE"))
    stored = read_execution_state(namespace, key, strict=True) if owned else None
    if stored is None:
        versions = {name: latest_dataset_metadata(name) for name in dataset_names}
        if not owned:
            return versions
        payload = {name: asdict(metadata) if metadata is not None else None for name, metadata in versions.items()}
        if not write_execution_state(namespace, key, payload, immutable=True):
            raise RuntimeError("Pipeline input versions could not be retained.")
        stored = read_execution_state(namespace, key, strict=True)
        if stored is None:
            raise RuntimeError("Pipeline input versions could not be read back.")
    return {name: PipelineDataset(**value) if value is not None else None for name, value in stored.items()}


def _local_frame_cache_path(dataset: PipelineDataset) -> Path:
    return _pipeline_cache_dir(dataset.dataset_name, dataset.dataset_version_id) / "frame.pkl"


def _read_local_frame_cache(dataset: PipelineDataset) -> pd.DataFrame | None:
    if not _pipeline_cache_enabled():
        return None
    path = _local_frame_cache_path(dataset)
    if not path.exists():
        return None
    try:
        frame = pd.read_pickle(path)
    except Exception:
        return None
    return frame if isinstance(frame, pd.DataFrame) else None


def _write_local_frame_cache(dataset: PipelineDataset, frame: pd.DataFrame) -> None:
    if not _pipeline_cache_enabled():
        return
    path = _local_frame_cache_path(dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_pickle(path)
    max_bytes = _pipeline_cache_max_bytes()
    if _path_size_bytes(path) > max_bytes:
        _delete_cache_path(path)
        return
    _prune_pipeline_cache(keep_paths=(path,))


def _local_partition_cache_path(dataset: PipelineDataset, partition_key: str) -> Path:
    safe_key = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(partition_key or "").strip())
    return _pipeline_cache_dir(dataset.dataset_name, dataset.dataset_version_id) / "partitions" / f"{safe_key}.pkl"


def _read_local_partition_cache(dataset: PipelineDataset, partition_key: str) -> pd.DataFrame | None:
    if not _pipeline_cache_enabled():
        return None
    path = _local_partition_cache_path(dataset, partition_key)
    if not path.exists():
        return None
    try:
        frame = pd.read_pickle(path)
    except Exception:
        return None
    return frame if isinstance(frame, pd.DataFrame) else None


def _write_local_partition_cache(dataset: PipelineDataset, partition_key: str, frame: pd.DataFrame) -> None:
    if not _pipeline_cache_enabled():
        return
    path = _local_partition_cache_path(dataset, partition_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_pickle(path)
    if _path_size_bytes(path) > _pipeline_cache_max_bytes():
        _delete_cache_path(path)
        return
    _prune_pipeline_cache(keep_paths=(path,))


def _filter_frame(frame: pd.DataFrame, filters: dict[str, object]) -> pd.DataFrame:
    out = frame.copy()
    for column, expected in filters.items():
        if column not in out.columns:
            return pd.DataFrame(columns=out.columns)
        series = out[column]
        if column == "symbol":
            mask = series.astype(str).str.upper().str.strip() == str(expected or "").upper().strip()
        else:
            mask = series == expected
        out = out[mask].copy()
        if out.empty:
            break
    return out.reset_index(drop=True)


def _read_blob_json(blob_path: str) -> dict[str, Any] | None:
    client = _blob_service_client()
    if client is None:
        return None
    try:
        blob = client.get_blob_client(container=_storage_container(), blob=blob_path)
        payload = blob.download_blob(**_blob_io_options()).readall()
        if not payload:
            return None
        parsed = json.loads(payload.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except WorkCancelled:
        raise
    except Exception:
        return None


def _coerce_manifest_dataset(manifest: dict[str, Any], *, fallback_path: str | None = None) -> PipelineDataset | None:
    dataset_name = str(manifest.get("dataset_name") or "").strip()
    dataset_version_id = str(manifest.get("dataset_version_id") or "").strip()
    blob_path = str(manifest.get("blob_path") or fallback_path or "").strip()
    asof_time_utc = str(manifest.get("asof_time_utc") or "").strip()
    ingested_at_utc = str(manifest.get("ingested_at_utc") or "").strip()
    if not dataset_name or not dataset_version_id or not blob_path:
        return None
    try:
        row_count = int(manifest.get("row_count") or 0)
    except Exception:
        row_count = 0
    partitioning = manifest.get("partitioning")
    return PipelineDataset(
        dataset_name=dataset_name,
        dataset_version_id=dataset_version_id,
        blob_path=blob_path,
        asof_time_utc=asof_time_utc,
        ingested_at_utc=ingested_at_utc,
        row_count=row_count,
        partitioning=dict(partitioning) if isinstance(partitioning, dict) else {},
    )


def _dataset_time_epoch(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = pd.to_datetime(text, utc=True, errors="coerce")
    except Exception:
        return 0.0
    if pd.isna(parsed):
        return 0.0
    try:
        return float(parsed.timestamp())
    except Exception:
        return 0.0


def _newer_dataset(left: PipelineDataset | None, right: PipelineDataset | None) -> PipelineDataset | None:
    if left is None:
        return right
    if right is None:
        return left

    left_key = (
        _dataset_time_epoch(left.ingested_at_utc),
        _dataset_time_epoch(left.asof_time_utc),
        left.dataset_version_id,
    )
    right_key = (
        _dataset_time_epoch(right.ingested_at_utc),
        _dataset_time_epoch(right.asof_time_utc),
        right.dataset_version_id,
    )
    if left.dataset_version_id == right.dataset_version_id and right.partitioning:
        return right
    return right if right_key > left_key else left


def _stable_latest_manifest_metadata(dataset_name: str) -> PipelineDataset | None:
    latest_manifest = _read_blob_json(dataset_blob_path(f"manifests/{dataset_name}/latest.json"))
    if latest_manifest:
        dataset = _coerce_manifest_dataset(latest_manifest)
        if dataset is not None and dataset.dataset_name == dataset_name and (
            "dataset_namespace" not in latest_manifest or latest_manifest["dataset_namespace"] == dataset_namespace()
        ):
            return dataset
    return None


def _publication_generation(value: dict[str, Any]) -> tuple:
    return (_dataset_time_epoch(value.get("asof_time_utc")),
            int(value.get("execution_fence") or 0),
            _dataset_time_epoch(value.get("ingested_at_utc")), str(value.get("dataset_version_id") or ""))


def publish_dataset_manifest(manifest: dict[str, Any]) -> bool:
    """Publish a version receipt and conditionally advance its latest pointer."""
    from .llm_execution import check_execution_ownership
    from .work_scope import check_work
    check_work()
    check_execution_ownership()
    from azure.core import MatchConditions
    from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError, ResourceModifiedError
    from azure.storage.blob import ContentSettings
    dataset = _coerce_manifest_dataset(manifest)
    if dataset is None:
        raise ValueError("A publication requires a complete dataset manifest")
    if manifest.get("dataset_namespace", dataset_namespace()) != dataset_namespace():
        raise ValueError("Publication namespace differs from the active dataset namespace")
    client = _blob_service_client()
    if client is None:
        raise RuntimeError("Dataset storage is unavailable")
    payload = json.dumps(manifest, sort_keys=True, default=str).encode("utf-8")
    settings = ContentSettings(content_type="application/json")
    version = client.get_blob_client(container=_storage_container(), blob=dataset_blob_path(
        f"manifests/{dataset.dataset_name}/{dataset.dataset_version_id}.json"))
    try:
        version.upload_blob(payload, overwrite=False, content_settings=settings, **_blob_io_options())
    except ResourceExistsError:
        if version.download_blob(**_blob_io_options()).readall() != payload:
            raise ValueError("A dataset version is immutable; publish changed data under a new version")
    latest = client.get_blob_client(container=_storage_container(), blob=dataset_blob_path(
        f"manifests/{dataset.dataset_name}/latest.json"))

    for _ in range(8):
        check_work()
        check_execution_ownership()
        try:
            download = latest.download_blob(**_blob_io_options())
            current = json.loads(download.readall())
            etag = download.properties.etag
        except ResourceNotFoundError:
            current, etag = None, None
        if current is not None and _publication_generation(current) >= _publication_generation(manifest):
            return current.get("dataset_version_id") == manifest["dataset_version_id"]
        try:
            if etag is None:
                latest.upload_blob(payload, overwrite=False, content_settings=settings, **_blob_io_options())
            else:
                latest.upload_blob(payload, overwrite=True, content_settings=settings,
                                   etag=etag, match_condition=MatchConditions.IfNotModified, **_blob_io_options())
            _write_cached_metadata(dataset.dataset_name, dataset)
            return True
        except (ResourceExistsError, ResourceModifiedError):
            continue
    raise RuntimeError("Concurrent publication did not settle within its retry allowance")


def _latest_manifest_metadata(dataset_name: str) -> PipelineDataset | None:
    stable_latest = _stable_latest_manifest_metadata(dataset_name)
    if stable_latest is not None:
        return stable_latest

    client = _blob_service_client()
    if client is None:
        return None
    try:
        container = client.get_container_client(_storage_container())
        prefix = dataset_blob_path(f"manifests/{dataset_name}/")
        blobs = list(container.list_blobs(name_starts_with=prefix))
    except Exception:
        return None

    if not blobs:
        return None

    def _sort_key(blob: Any) -> tuple[datetime, str]:
        last_modified = getattr(blob, "last_modified", None)
        if isinstance(last_modified, datetime):
            modified = last_modified
        else:
            modified = datetime.min.replace(tzinfo=timezone.utc)
        return modified, str(getattr(blob, "name", ""))

    candidates = sorted(blobs, key=_sort_key, reverse=True)[:5]
    for blob in candidates:
        manifest = _read_blob_json(str(getattr(blob, "name", "")))
        if not manifest:
            continue
        dataset = _coerce_manifest_dataset(manifest)
        if dataset is not None:
            return dataset
    return None


def latest_dataset_metadata(dataset_name: str) -> PipelineDataset | None:
    check_work()
    cached = _read_cached_metadata(dataset_name)
    if cached is not None:
        return cached

    lock = _METADATA_READ_LOCKS[int.from_bytes(hashlib.sha256(dataset_storage_name(dataset_name).encode()).digest()[:2], "big") % len(_METADATA_READ_LOCKS)]
    with lock:
        cached = _read_cached_metadata(dataset_name)
        return cached if cached is not None else _uncached_latest_dataset_metadata(dataset_name)


def _uncached_latest_dataset_metadata(dataset_name: str) -> PipelineDataset | None:
    manifest_dataset = _stable_latest_manifest_metadata(dataset_name)
    if manifest_dataset is not None:
        # Publication completes before its optional SQL index. A valid stable
        # pointer is sufficient to serve a completed artifact during SQL load.
        _write_cached_metadata(dataset_name, manifest_dataset)
        return manifest_dataset
    db_dataset: PipelineDataset | None = None
    conn = open_database_connection()
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT dataset_name, dataset_version_id, blob_path, asof_time_utc,
                           ingested_at_utc, row_count
                    FROM dataset_versions
                    WHERE dataset_name = %s AND status = 'ready'
                    ORDER BY ingested_at_utc DESC
                    LIMIT 1
                    """,
                    (dataset_storage_name(dataset_name),),
                )
                row = cur.fetchone()
            if row:
                db_dataset = PipelineDataset(
                    dataset_name=dataset_name,
                    dataset_version_id=str(row[1]),
                    blob_path=str(row[2]),
                    asof_time_utc=str(row[3]),
                    ingested_at_utc=str(row[4]),
                    row_count=int(row[5] or 0),
                )
        except Exception:
            pass
        finally:
            conn.close()

    if db_dataset is None and manifest_dataset is None:
        manifest_dataset = _latest_manifest_metadata(dataset_name)

    dataset = _newer_dataset(db_dataset, manifest_dataset)
    _write_cached_metadata(dataset_name, dataset)
    return dataset


def dataset_metadata_asof(dataset_name: str, target_date: str) -> PipelineDataset | None:
    """Load the most recent dataset version whose asof_time_utc falls on or before *target_date*.

    *target_date* should be an ISO date string like ``"2026-04-20"``.  The query
    finds the newest ``ready`` version where the asof timestamp is within the
    target calendar day (UTC) or before it.
    """
    conn = open_database_connection()
    if conn is None:
        return None
    target_end = f"{target_date}T23:59:59.999999+00:00"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dataset_name, dataset_version_id, blob_path, asof_time_utc,
                       ingested_at_utc, row_count
                FROM dataset_versions
                WHERE dataset_name = %s AND status = 'ready'
                  AND asof_time_utc <= %s
                ORDER BY asof_time_utc DESC
                LIMIT 1
                """,
                (dataset_storage_name(dataset_name), target_end),
            )
            row = cur.fetchone()
            if not row:
                return None
            return PipelineDataset(
                dataset_name=dataset_name,
                dataset_version_id=str(row[1]),
                blob_path=str(row[2]),
                asof_time_utc=str(row[3]),
                ingested_at_utc=str(row[4]),
                row_count=int(row[5] or 0),
            )
    except Exception:
        return None
    finally:
        conn.close()


def load_dataset_frame_asof(dataset_name: str, target_date: str) -> tuple[pd.DataFrame, PipelineDataset | None]:
    """Load the dataset frame closest to (but not after) *target_date*.

    Uses ``dataset_metadata_asof`` for discovery, then the same blob/cache
    read path as ``load_latest_dataset_frame``.
    """
    metadata = dataset_metadata_asof(dataset_name, target_date)
    if metadata is None:
        return pd.DataFrame(), None
    cached = _read_local_frame_cache(metadata)
    if cached is not None:
        if not (cached.empty and int(metadata.row_count or 0) > 0):
            return cached, metadata
        _delete_cache_path(_local_frame_cache_path(metadata))
    frame = _read_blob_parquet(metadata.blob_path)
    if isinstance(frame, pd.DataFrame) and (not frame.empty or int(metadata.row_count or 0) <= 0):
        try:
            _write_local_frame_cache(metadata, frame)
        except Exception:
            pass
    return frame, metadata


def load_latest_dataset_frame(
    dataset_name: str, *, columns: list[str] | None = None, symbols: list[str] | None = None,
) -> tuple[pd.DataFrame, PipelineDataset | None]:
    metadata = latest_dataset_metadata(dataset_name)
    if metadata is None:
        return pd.DataFrame(), None
    if columns is not None or symbols is not None:
        return load_dataset_frame(metadata, columns=columns, symbols=symbols), metadata
    cached = _read_local_frame_cache(metadata)
    if cached is not None:
        if not (cached.empty and int(metadata.row_count or 0) > 0):
            return cached, metadata
        _delete_cache_path(_local_frame_cache_path(metadata))
    frame = _read_blob_parquet(metadata.blob_path)
    if isinstance(frame, pd.DataFrame) and (not frame.empty or int(metadata.row_count or 0) <= 0):
        try:
            _write_local_frame_cache(metadata, frame)
        except Exception:
            pass
    return frame, metadata


def load_latest_dataset_frame_slice(
    dataset_name: str,
    *,
    filters: dict[str, object],
) -> tuple[pd.DataFrame, PipelineDataset | None]:
    """Load a bounded exact-match slice without downloading a large global artifact.

    Symbol-hash partitions are preferred. A cached full frame remains usable, and small
    legacy artifacts may still be scanned. Large unpartitioned artifacts fail closed so
    callers can use their existing on-demand source rather than pulling millions of rows.
    """
    normalized_filters = {str(key).strip(): value for key, value in filters.items() if str(key).strip()}
    if not normalized_filters:
        raise ValueError("At least one dataset slice filter is required.")
    metadata = latest_dataset_metadata(dataset_name)
    if metadata is None:
        return pd.DataFrame(), None

    cached = _read_local_frame_cache(metadata)
    if cached is not None:
        return _filter_frame(cached, normalized_filters), metadata

    partitioning = dict(metadata.partitioning or {})
    strategy = str(partitioning.get("strategy") or "").strip()
    partition_column = str(partitioning.get("column") or "").strip()
    paths = partitioning.get("paths")
    if (
        strategy == SYMBOL_HASH_PARTITION_STRATEGY
        and partition_column == "symbol"
        and isinstance(paths, dict)
        and "symbol" in normalized_filters
    ):
        try:
            bucket_count = int(partitioning.get("bucket_count") or SYMBOL_HASH_PARTITION_BUCKETS)
            bucket = symbol_partition_bucket(normalized_filters["symbol"], bucket_count=bucket_count)
        except (TypeError, ValueError):
            return pd.DataFrame(), metadata
        blob_path = str(paths.get(bucket) or "").strip()
        if not blob_path:
            return pd.DataFrame(), metadata
        partition_frame = _read_local_partition_cache(metadata, bucket)
        if partition_frame is None:
            partition_frame = _read_blob_parquet(blob_path)
            if not partition_frame.empty:
                try:
                    _write_local_partition_cache(metadata, bucket, partition_frame)
                except Exception:
                    pass
        return _filter_frame(partition_frame, normalized_filters), metadata

    max_full_scan_rows = max(int(os.getenv("PIPELINE_SLICE_FULL_SCAN_MAX_ROWS", "250000")), 0)
    if int(metadata.row_count or 0) > max_full_scan_rows:
        return pd.DataFrame(), metadata
    frame, resolved_metadata = load_latest_dataset_frame(dataset_name)
    return _filter_frame(frame, normalized_filters), resolved_metadata


def recent_dataset_metadata(dataset_name: str, *, limit: int = 8) -> list[PipelineDataset]:
    dataset_key = str(dataset_name or "").strip()
    if not dataset_key:
        return []
    capped_limit = max(1, min(int(limit or 8), 25))
    conn = open_database_connection()
    if conn is None:
        latest = latest_dataset_metadata(dataset_key)
        return [latest] if latest is not None else []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dataset_name, dataset_version_id, blob_path, asof_time_utc,
                       ingested_at_utc, row_count
                FROM dataset_versions
                WHERE dataset_name = %s AND status = 'ready'
                ORDER BY ingested_at_utc DESC
                LIMIT %s
                """,
                (dataset_storage_name(dataset_key), capped_limit),
            )
            rows = cur.fetchall()
        out: list[PipelineDataset] = []
        for row in rows or []:
            out.append(
                PipelineDataset(
                    dataset_name=dataset_key,
                    dataset_version_id=str(row[1]),
                    blob_path=str(row[2]),
                    asof_time_utc=str(row[3]),
                    ingested_at_utc=str(row[4]),
                    row_count=int(row[5] or 0),
                )
            )
        if out:
            return out
        latest = latest_dataset_metadata(dataset_key)
        return [latest] if latest is not None else []
    except Exception:
        latest = latest_dataset_metadata(dataset_key)
        return [latest] if latest is not None else []
    finally:
        conn.close()


def load_recent_dataset_frames(dataset_name: str, *, limit: int = 8,
                               exclude_versions: set[str] | None = None) -> list[tuple[pd.DataFrame, PipelineDataset]]:
    out: list[tuple[pd.DataFrame, PipelineDataset]] = []
    for metadata in recent_dataset_metadata(dataset_name, limit=limit):
        if metadata.dataset_version_id in (exclude_versions or ()):
            continue
        cached = _read_local_frame_cache(metadata)
        if cached is not None:
            out.append((cached, metadata))
            continue
        frame = _read_blob_parquet(metadata.blob_path)
        if isinstance(frame, pd.DataFrame):
            try:
                _write_local_frame_cache(metadata, frame)
            except Exception:
                pass
            out.append((frame, metadata))
    return out


TRADING_AGENT_ACTION_COLUMNS = [
    "action_id",
    "candidate_id",
    "trading_agent_run_id",
    "run_id",
    "horizon_key",
    "ticker",
    "action",
    "execution_mode",
    "status",
    "broker",
    "broker_order_id",
    "created_at_utc",
    "requested_by",
    "requested_email",
    "notes",
]


def _bootstrap_trading_agent_actions(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT attname FROM pg_attribute WHERE attrelid=to_regclass('trading_agent_actions') AND attnum>0 AND NOT attisdropped")
        columns = {row[0] for row in cur.fetchall()}
        if {"execution_mode", "broker", "broker_order_id", "order_payload", "dataset_namespace"}.issubset(columns):
            return
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('trading_agent_actions_schema_v1'))")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trading_agent_actions (
                action_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                trading_agent_run_id TEXT,
                run_id TEXT,
                horizon_key TEXT,
                ticker TEXT,
                action TEXT NOT NULL,
                execution_mode TEXT NOT NULL DEFAULT 'log_only',
                status TEXT NOT NULL,
                broker TEXT,
                broker_order_id TEXT,
                order_payload JSONB,
                created_at_utc TIMESTAMPTZ NOT NULL,
                requested_by TEXT,
                requested_email TEXT,
                notes TEXT,
                candidate_payload JSONB,
                dataset_namespace TEXT NOT NULL DEFAULT ''
            )
            """
        )
        cur.execute(
            """
            ALTER TABLE trading_agent_actions
            ADD COLUMN IF NOT EXISTS execution_mode TEXT NOT NULL DEFAULT 'log_only'
            """
        )
        cur.execute(
            """
            ALTER TABLE trading_agent_actions
            ADD COLUMN IF NOT EXISTS broker TEXT
            """
        )
        cur.execute(
            """
            ALTER TABLE trading_agent_actions
            ADD COLUMN IF NOT EXISTS broker_order_id TEXT
            """
        )
        cur.execute(
            """
            ALTER TABLE trading_agent_actions
            ADD COLUMN IF NOT EXISTS order_payload JSONB
            """
        )
        cur.execute(
            """
            ALTER TABLE trading_agent_actions
            ADD COLUMN IF NOT EXISTS dataset_namespace TEXT NOT NULL DEFAULT ''
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trading_agent_actions_candidate_created
            ON trading_agent_actions (candidate_id, created_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trading_agent_actions_run_created
            ON trading_agent_actions (run_id, created_at_utc DESC)
            """
        )
    conn.commit()


def _normalize_trading_agent_action(action: object) -> tuple[str, str]:
    text = str(action or "").strip().lower()
    if text in {"approve", "approved", "place", "placed", "place_requested", "request_place"}:
        return "approved", "pending_entry"
    if text in {"reject", "rejected"}:
        return "rejected", "logged"
    return text or "unknown", "logged"


def record_trading_agent_action(
    *,
    candidate: dict[str, Any],
    action: str,
    requested_by: str = "",
    requested_email: str = "",
    notes: str = "",
) -> tuple[bool, str]:
    """Persist an admin Trading Agent decision."""
    if not isinstance(candidate, dict) or not str(candidate.get("candidate_id") or "").strip():
        return False, "Missing Trading Agent candidate id."
    action_value, status_value = _normalize_trading_agent_action(action)
    if action_value not in {"approved", "rejected"}:
        return False, f"Unsupported Trading Agent action: {action}"
    owner_id = str(requested_by or "").strip()
    if not owner_id:
        return False, "A signed-in owner is required."
    if action_value == "approved" and str(candidate.get("stance") or "").lower() not in {"long", "short"}:
        return False, "Only a directional call can be approved for tracking."
    conn = open_database_connection()
    if conn is None:
        return False, "Postgres is unavailable, so the Trading Agent action was not logged."
    created_at = datetime.now(timezone.utc)
    action_id = str(uuid.uuid4())
    try:
        _bootstrap_trading_agent_actions(conn)
        from .trading_call_tracking import ensure_schema, record_approval
        ensure_schema(conn)
        conn.commit()
        with conn.cursor() as cur:
            identity = json.dumps([dataset_namespace(), owner_id, candidate["candidate_id"]])
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (identity,))
            cur.execute("""SELECT action,action_id,created_at_utc,candidate_payload,execution_mode,horizon_key,ticker
                FROM trading_agent_actions WHERE dataset_namespace=%s AND requested_by=%s AND candidate_id=%s
                ORDER BY created_at_utc DESC,action_id DESC LIMIT 1""",
                (dataset_namespace(), owner_id, candidate["candidate_id"]))
            previous = cur.fetchone()
            if previous and previous[0] in {"approved", "place_requested"}:
                if action_value != "approved":
                    return False, "This call is already approved and tracked."
                if previous[4] in {"log_only", "paper_tracking"}:
                    snapshot = previous[3] if isinstance(previous[3], dict) else json.loads(previous[3])
                    snapshot = {**snapshot, "candidate_id": candidate["candidate_id"]}
                    snapshot["horizon_key"] = snapshot.get("horizon_key") or previous[5]
                    snapshot["ticker"] = snapshot.get("ticker") or previous[6]
                    record_approval(cur, candidate=snapshot, owner_id=owner_id, action_id=previous[1],
                                    approved_at=previous[2], namespace=dataset_namespace(), source="retained_log_approval")
                conn.commit()
                return True, "Candidate approved."
            if previous and previous[0] == "rejected" and action_value == "rejected":
                conn.commit()
                return True, "Candidate rejected."
            cur.execute(
                """
                INSERT INTO trading_agent_actions (
                    action_id, candidate_id, trading_agent_run_id, run_id,
                    horizon_key, ticker, action, execution_mode, status,
                    broker, broker_order_id, order_payload, created_at_utc,
                    requested_by, requested_email, notes, candidate_payload,
                    dataset_namespace
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s,
                    %s, %s, %s, %s::jsonb, %s
                )
                """,
                (
                    action_id,
                    str(candidate.get("candidate_id") or "").strip(),
                    str(candidate.get("trading_agent_run_id") or "").strip() or None,
                    str(candidate.get("run_id") or "").strip() or None,
                    str(candidate.get("horizon_key") or "").strip() or None,
                    str(candidate.get("ticker") or "").upper().strip() or None,
                    action_value,
                    "paper_tracking" if action_value == "approved" else "log_only",
                    status_value,
                    None,
                    None,
                    json.dumps({}, ensure_ascii=True, sort_keys=True),
                    created_at,
                    str(requested_by or "").strip() or None,
                    str(requested_email or "").strip() or None,
                    str(notes or "").strip() or None,
                    json.dumps(candidate, ensure_ascii=True, sort_keys=True, default=str),
                    dataset_namespace(),
                ),
            )
            if action_value == "approved":
                record_approval(cur, candidate=candidate, owner_id=owner_id, action_id=action_id,
                                approved_at=created_at, namespace=dataset_namespace())
        conn.commit()
    except Exception as exc:
        return False, f"Trading Agent action log failed: {type(exc).__name__}: {exc}"
    finally:
        conn.close()

    if action_value == "approved":
        return True, "Candidate approved."
    return True, "Candidate rejected."


def trading_agent_actions_table(*, limit: int = 500) -> pd.DataFrame:
    columns = list(TRADING_AGENT_ACTION_COLUMNS)
    conn = open_database_connection()
    if conn is None:
        return pd.DataFrame(columns=columns)
    try:
        _bootstrap_trading_agent_actions(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT action_id, candidate_id, trading_agent_run_id, run_id,
                       horizon_key, ticker, action, execution_mode, status,
                       broker, broker_order_id, created_at_utc,
                       requested_by, requested_email, notes
                FROM trading_agent_actions
                WHERE dataset_namespace = %s
                ORDER BY created_at_utc DESC
                LIMIT %s
                """,
                (dataset_namespace(), max(int(limit), 1)),
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=columns)
        out = pd.DataFrame(rows, columns=columns)
        out["created_at_utc"] = pd.to_datetime(out["created_at_utc"], errors="coerce", utc=True)
        return out
    except Exception:
        return pd.DataFrame(columns=columns)
    finally:
        conn.close()


def start_source_refresh_job(source: str) -> tuple[bool, str]:
    source_key = str(source or "").strip().lower()
    canonical_job_name = SOURCE_JOB_MAP.get(source_key)
    if not canonical_job_name:
        return False, f"Unknown source: {source}"
    job_name = pipeline_job_resource_name(canonical_job_name)

    resource_group = _resource_group()
    if not resource_group:
        return False, "Missing resource group. Set PIPELINE_RESOURCE_GROUP or infra/.generated/deployment.local.env."

    arm_ok, arm_message = _start_source_refresh_job_via_arm(job_name, resource_group)
    if arm_ok:
        return arm_ok, arm_message

    if not shutil.which("az"):
        return False, arm_message

    cmd = [
        "az",
        "containerapp",
        "job",
        "start",
        "--name",
        job_name,
        "--resource-group",
        resource_group,
        "--query",
        "name",
        "-o",
        "tsv",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
    except Exception as exc:
        return False, f"{arm_message}; Azure CLI fallback failed: {exc}"

    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "job start failed").strip()
        return False, f"{arm_message}; Azure CLI fallback failed: {message}"

    execution = (proc.stdout or "").strip()
    if not execution:
        execution = "started"
    return True, f"Triggered `{job_name}` execution `{execution}`"


def start_container_app_job(job_name: str) -> tuple[bool, str]:
    """Start one explicitly named Container Apps Job through managed identity.

    The caller owns the allowlist/configuration decision. This boundary accepts
    only a single Azure resource name and never forwards an execution template,
    prompt, credential, or other caller-controlled environment value.
    """
    resolved_job_name = str(job_name or "").strip()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?", resolved_job_name):
        return False, "Invalid Container Apps Job resource name."
    resource_group = _resource_group()
    if not resource_group:
        return False, "Missing resource group configuration."
    return _start_source_refresh_job_via_arm(resolved_job_name, resource_group)


def latest_job_status_table() -> pd.DataFrame:
    columns = [
        "job_name",
        "run",
        "status",
        "progress_stage",
        "progress_pct",
        "heartbeat_time_utc",
        "start_time_utc",
        "end_time_utc",
        "message",
    ]
    job_names = sorted(set(SOURCE_JOB_MAP.values()))

    conn = open_database_connection()
    if conn is not None:
        rows: list[dict[str, str]] = []
        try:
            with conn.cursor() as cur:
                for job_name in job_names:
                    try:
                        cur.execute(
                            """
                            SELECT run_id, status, progress_stage, progress_pct, heartbeat_time_utc,
                                   start_time_utc, end_time_utc, progress_message, error_summary
                            FROM job_runs
                            WHERE job_name = %s
                            ORDER BY start_time_utc DESC
                            LIMIT 1
                            """,
                            (dataset_storage_name(job_name),),
                        )
                        row = cur.fetchone()
                        if row:
                            status = _normalize_job_status_label(row[1])
                            progress_message = str(row[7] or "")
                            error_summary = str(row[8] or "")
                            message = progress_message or error_summary
                            if status == "Failed":
                                message = error_summary or progress_message
                            rows.append(
                                {
                                    "job_name": job_name,
                                    "run": str(row[0] or "N/A"),
                                    "status": status,
                                    "progress_stage": str(row[2] or ""),
                                    "progress_pct": float(row[3]) if row[3] is not None else None,
                                    "heartbeat_time_utc": str(row[4] or ""),
                                    "start_time_utc": str(row[5] or ""),
                                    "end_time_utc": str(row[6] or ""),
                                    "message": message,
                                }
                            )
                            continue
                    except Exception:
                        pass

                    cur.execute(
                        """
                        SELECT run_id, status, start_time_utc, end_time_utc, error_summary
                        FROM job_runs
                        WHERE job_name = %s
                        ORDER BY start_time_utc DESC
                        LIMIT 1
                        """,
                        (dataset_storage_name(job_name),),
                    )
                    row = cur.fetchone()
                    if not row:
                        rows.append(
                            {
                                "job_name": job_name,
                                "run": "N/A",
                                "status": "NoRuns",
                                "progress_stage": "",
                                "progress_pct": None,
                                "heartbeat_time_utc": "",
                                "start_time_utc": "",
                                "end_time_utc": "",
                                "message": "No executions found.",
                            }
                        )
                        continue
                    rows.append(
                        {
                            "job_name": job_name,
                            "run": str(row[0] or "N/A"),
                            "status": _normalize_job_status_label(row[1]),
                            "progress_stage": "",
                            "progress_pct": None,
                            "heartbeat_time_utc": "",
                            "start_time_utc": str(row[2] or ""),
                            "end_time_utc": str(row[3] or ""),
                            "message": str(row[4] or ""),
                        }
                    )
            return pd.DataFrame(rows, columns=columns)
        except Exception as exc:
            rows = [
                {
                    "job_name": "N/A",
                    "run": "N/A",
                    "status": "Error",
                    "progress_stage": "",
                    "progress_pct": None,
                    "heartbeat_time_utc": "",
                    "start_time_utc": "",
                    "end_time_utc": "",
                    "message": f"Postgres query failed: {exc}",
                }
            ]
            return pd.DataFrame(rows, columns=columns)
        finally:
            conn.close()
    rows: list[dict[str, str]] = [
        {
            "job_name": job_name,
            "run": "N/A",
            "status": "Unavailable",
            "progress_stage": "",
            "progress_pct": None,
            "heartbeat_time_utc": "",
            "start_time_utc": "",
            "end_time_utc": "",
            "message": "Postgres job metadata unavailable. Configure Key Vault/Postgres connection for tracker.",
        }
        for job_name in job_names
    ]
    return pd.DataFrame(rows, columns=columns)


def _normalize_job_status_label(status: object) -> str:
    text = str(status or "").strip().lower()
    if text in {"running", "in_progress", "started"}:
        return "Running"
    if text in {"success", "succeeded", "completed", "complete"}:
        return "Succeeded"
    if text in {"failed", "failure", "error"}:
        return "Failed"
    if text in {"warning", "warn"}:
        return "Warning"
    if not text:
        return "Unknown"
    return str(status)


def job_run_history(*, days: int = 7) -> pd.DataFrame:
    """Return recent job_runs rows for timeline and failure visualizations."""
    columns = [
        "job_name", "run_id", "status", "start_time_utc", "end_time_utc",
        "error_summary", "progress_stage",
    ]
    conn = open_database_connection()
    if conn is None:
        return pd.DataFrame(columns=columns)
    try:
        with conn.cursor() as cur:
            namespace = dataset_namespace()
            namespace_filter = "job_name LIKE %s" if namespace else "job_name NOT LIKE %s"
            cur.execute(
                f"""
                SELECT job_name, run_id, status, start_time_utc, end_time_utc,
                       error_summary, progress_stage
                FROM job_runs
                WHERE start_time_utc >= NOW() - INTERVAL '%s days'
                  AND {namespace_filter}
                ORDER BY start_time_utc DESC
                """,
                (days, f"{namespace}::%" if namespace else "%::%"),
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows, columns=columns)
        if dataset_namespace():
            df["job_name"] = df["job_name"].astype(str).str.split("::", n=1).str[-1]
        df["status"] = df["status"].apply(_normalize_job_status_label)
        for col in ["start_time_utc", "end_time_utc"]:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
        return df
    except Exception:
        return pd.DataFrame(columns=columns)
    finally:
        conn.close()


def dataset_version_history(*, days: int = 7) -> pd.DataFrame:
    """Return recent dataset_versions rows for row-count visualization."""
    columns = [
        "dataset_name", "row_count", "ingested_at_utc", "run_id",
    ]
    conn = open_database_connection()
    if conn is None:
        return pd.DataFrame(columns=columns)
    try:
        with conn.cursor() as cur:
            namespace = dataset_namespace()
            namespace_filter = "dataset_name LIKE %s" if namespace else "dataset_name NOT LIKE %s"
            cur.execute(
                f"""
                SELECT dataset_name, row_count, ingested_at_utc, run_id
                FROM dataset_versions
                WHERE ingested_at_utc >= NOW() - INTERVAL '%s days'
                  AND {namespace_filter}
                ORDER BY ingested_at_utc DESC
                """,
                (days, f"{namespace}::%" if namespace else "%::%"),
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows, columns=columns)
        if dataset_namespace():
            df["dataset_name"] = df["dataset_name"].astype(str).str.split("::", n=1).str[-1]
        df["row_count"] = pd.to_numeric(df["row_count"], errors="coerce").fillna(0).astype(int)
        df["ingested_at_utc"] = pd.to_datetime(df["ingested_at_utc"], errors="coerce", utc=True)
        return df
    except Exception:
        return pd.DataFrame(columns=columns)
    finally:
        conn.close()


def latest_dataset_status_table() -> pd.DataFrame:
    """Return the latest known snapshot for each materialized dataset."""
    columns = [
        "dataset_name",
        "dataset_version_id",
        "row_count",
        "ingested_at_utc",
        "run_id",
        "age_hours",
    ]
    conn = open_database_connection()
    if conn is None:
        return pd.DataFrame(columns=columns)
    try:
        with conn.cursor() as cur:
            namespace = dataset_namespace()
            namespace_filter = "dataset_name LIKE %s" if namespace else "dataset_name NOT LIKE %s"
            cur.execute(
                f"""
                SELECT DISTINCT ON (dataset_name)
                    dataset_name, dataset_version_id, row_count, ingested_at_utc, run_id
                FROM dataset_versions
                WHERE {namespace_filter}
                ORDER BY dataset_name, ingested_at_utc DESC NULLS LAST
                """,
                (f"{namespace}::%" if namespace else "%::%",),
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows, columns=columns[:-1])
        if dataset_namespace():
            df["dataset_name"] = df["dataset_name"].astype(str).str.split("::", n=1).str[-1]
        df["row_count"] = pd.to_numeric(df["row_count"], errors="coerce").fillna(0).astype(int)
        df["ingested_at_utc"] = pd.to_datetime(df["ingested_at_utc"], errors="coerce", utc=True)
        now = pd.Timestamp.now(tz="UTC")
        df["age_hours"] = ((now - df["ingested_at_utc"]).dt.total_seconds() / 3600.0).round(1)
        return df[columns]
    except Exception:
        return pd.DataFrame(columns=columns)
    finally:
        conn.close()


_CURRENT_FEED_POINTER_HEALTH_DATASETS: tuple[str, ...] = (
    "attention_home_1d",
    "attention_home_snapshots_1d",
    "page_agentic_summaries",
    "market_opportunity_feed",
    "trading_agent_runs",
    "trading_agent_candidates",
    "trading_agent_outcomes",
    "trading_agent_research_reviews",
)


def _latest_db_dataset_metadata_map(dataset_names: list[str]) -> dict[str, PipelineDataset]:
    names = [str(name).strip() for name in dataset_names if str(name).strip()]
    if not names:
        return {}
    conn = open_database_connection()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (dataset_name)
                    dataset_name, dataset_version_id, blob_path, asof_time_utc,
                    ingested_at_utc, row_count
                FROM dataset_versions
                WHERE dataset_name = ANY(%s) AND status = 'ready'
                ORDER BY dataset_name, ingested_at_utc DESC NULLS LAST
                """,
                ([dataset_storage_name(name) for name in names],),
            )
            rows = cur.fetchall()
        out: dict[str, PipelineDataset] = {}
        for row in rows or []:
            stored_name = str(row[0])
            canonical_name = stored_name.split("::", 1)[-1] if dataset_namespace() else stored_name
            dataset = PipelineDataset(
                dataset_name=canonical_name,
                dataset_version_id=str(row[1]),
                blob_path=str(row[2]),
                asof_time_utc=str(row[3]),
                ingested_at_utc=str(row[4]),
                row_count=int(row[5] or 0),
            )
            out[canonical_name] = dataset
        return out
    except Exception:
        return {}
    finally:
        conn.close()


def _same_dataset_pointer(left: PipelineDataset | None, right: PipelineDataset | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return (
        str(left.dataset_version_id or "") == str(right.dataset_version_id or "")
        and str(left.blob_path or "") == str(right.blob_path or "")
    )


def _dataset_pointer_source(dataset: PipelineDataset | None, db_dataset: PipelineDataset | None, manifest_dataset: PipelineDataset | None) -> str:
    if dataset is None:
        return ""
    if _same_dataset_pointer(dataset, manifest_dataset):
        return "manifest"
    if _same_dataset_pointer(dataset, db_dataset):
        return "db"
    return "unknown"


def latest_dataset_pointer_drift(dataset_names: list[str] | tuple[str, ...] | None = None) -> pd.DataFrame:
    """Compare DB and stable blob-manifest latest pointers for current-feed datasets."""
    columns = [
        "dataset_name",
        "health",
        "reader_source",
        "reader_dataset_version_id",
        "db_dataset_version_id",
        "manifest_dataset_version_id",
        "db_ingested_at_utc",
        "manifest_ingested_at_utc",
        "db_row_count",
        "manifest_row_count",
        "db_blob_path",
        "manifest_blob_path",
    ]
    names = [str(name).strip() for name in list(dataset_names or _CURRENT_FEED_POINTER_HEALTH_DATASETS) if str(name).strip()]
    if not names:
        return pd.DataFrame(columns=columns)

    db_by_name = _latest_db_dataset_metadata_map(names)
    rows: list[dict[str, Any]] = []
    for dataset_name in names:
        db_dataset = db_by_name.get(dataset_name)
        manifest_dataset = _stable_latest_manifest_metadata(dataset_name)
        reader_dataset = _newer_dataset(db_dataset, manifest_dataset)
        if db_dataset is not None and manifest_dataset is not None:
            health = "drift" if not _same_dataset_pointer(db_dataset, manifest_dataset) else "in_sync"
        elif db_dataset is not None:
            health = "db_only"
        elif manifest_dataset is not None:
            health = "manifest_only"
        else:
            health = "missing"
        rows.append(
            {
                "dataset_name": dataset_name,
                "health": health,
                "reader_source": _dataset_pointer_source(reader_dataset, db_dataset, manifest_dataset),
                "reader_dataset_version_id": str(reader_dataset.dataset_version_id) if reader_dataset else "",
                "db_dataset_version_id": str(db_dataset.dataset_version_id) if db_dataset else "",
                "manifest_dataset_version_id": str(manifest_dataset.dataset_version_id) if manifest_dataset else "",
                "db_ingested_at_utc": str(db_dataset.ingested_at_utc) if db_dataset else "",
                "manifest_ingested_at_utc": str(manifest_dataset.ingested_at_utc) if manifest_dataset else "",
                "db_row_count": int(db_dataset.row_count) if db_dataset else pd.NA,
                "manifest_row_count": int(manifest_dataset.row_count) if manifest_dataset else pd.NA,
                "db_blob_path": str(db_dataset.blob_path) if db_dataset else "",
                "manifest_blob_path": str(manifest_dataset.blob_path) if manifest_dataset else "",
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _json_object_from_cell(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def latest_home_market_coverage_health(dataset_name: str = "attention_home_1d") -> pd.DataFrame:
    """Inspect the latest materialized Home coverage contract for Admin health."""
    columns = [
        "dataset_name",
        "health",
        "dataset_version_id",
        "asof_time_utc",
        "generated_at_utc",
        "coverage_status",
        "market_breadth_symbol_count",
        "macro_anchor_move_count",
        "sector_rotation_count",
        "yield_curve_fact_count",
        "structural_signal_count",
        "coverage_gaps",
        "summary_status",
        "issue",
    ]
    dataset_key = str(dataset_name or "attention_home_1d").strip() or "attention_home_1d"
    try:
        frame, metadata = load_latest_dataset_frame(dataset_key)
    except Exception as exc:
        return pd.DataFrame(
            [
                {
                    "dataset_name": dataset_key,
                    "health": "error",
                    "issue": f"{type(exc).__name__}: {exc}",
                }
            ],
            columns=columns,
        )
    if metadata is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(
            [
                {
                    "dataset_name": dataset_key,
                    "health": "missing",
                    "issue": "Latest Home dataset is unavailable.",
                }
            ],
            columns=columns,
        )

    row = frame.iloc[0]
    coverage_summary = _json_object_from_cell(row.get("coverage_summary_json"))
    market_coverage = coverage_summary.get("market_coverage")
    if not isinstance(market_coverage, dict):
        market_coverage = {}
    homepage_summary = _json_object_from_cell(row.get("homepage_summary_json"))
    legacy_broad_market_key = "broad_" + "ta" + "pe"
    broad_market = market_coverage.get("broad_market")
    if not isinstance(broad_market, dict):
        broad_market = market_coverage.get(legacy_broad_market_key) if isinstance(market_coverage.get(legacy_broad_market_key), dict) else {}
    rates_macro = market_coverage.get("rates_and_macro") if isinstance(market_coverage.get("rates_and_macro"), dict) else {}
    structural = market_coverage.get("structural_signals") if isinstance(market_coverage.get("structural_signals"), dict) else {}
    breadth = broad_market.get("market_breadth") if isinstance(broad_market.get("market_breadth"), dict) else {}
    macro_anchor_moves = broad_market.get("macro_anchor_moves") if isinstance(broad_market.get("macro_anchor_moves"), list) else []
    sector_rotation = broad_market.get("sector_rotation") if isinstance(broad_market.get("sector_rotation"), list) else []
    yield_curve_facts = rates_macro.get("yield_curve_facts") if isinstance(rates_macro.get("yield_curve_facts"), list) else []
    structural_signal_count = sum(
        len(values)
        for values in (
            structural.get("market") if isinstance(structural.get("market"), list) else [],
            structural.get("macro") if isinstance(structural.get("macro"), list) else [],
            structural.get("cross_series") if isinstance(structural.get("cross_series"), list) else [],
        )
    )
    gaps = [
        str(item).strip()
        for item in list(market_coverage.get("coverage_gaps") or coverage_summary.get("market_coverage_gaps") or [])
        if str(item).strip()
    ]
    coverage_status = str(market_coverage.get("status") or coverage_summary.get("market_coverage_status") or "").strip().lower()
    if not market_coverage:
        health = "missing"
        issue = "Latest Home snapshot does not include the market coverage contract."
    elif coverage_status in {"ok", "complete"} and not gaps:
        health = "ok"
        issue = ""
    elif coverage_status in {"unavailable", "failed", "missing"}:
        health = "unavailable"
        issue = "Home market coverage is unavailable."
    else:
        health = "partial"
        issue = "Home market coverage is incomplete."
    if gaps and not issue:
        issue = "Home market coverage has gaps."

    result = {
        "dataset_name": dataset_key,
        "health": health,
        "dataset_version_id": str(getattr(metadata, "dataset_version_id", "") or ""),
        "asof_time_utc": str(getattr(metadata, "asof_time_utc", "") or ""),
        "generated_at_utc": str(row.get("generated_at_utc") or ""),
        "coverage_status": coverage_status or ("missing" if not market_coverage else ""),
        "market_breadth_symbol_count": int(breadth.get("total_symbols") or coverage_summary.get("market_breadth_symbol_count") or 0),
        "macro_anchor_move_count": len(macro_anchor_moves),
        "sector_rotation_count": len(sector_rotation),
        "yield_curve_fact_count": len(yield_curve_facts),
        "structural_signal_count": int(structural_signal_count),
        "coverage_gaps": ", ".join(gaps),
        "summary_status": str(homepage_summary.get("summary_status") or homepage_summary.get("status") or "").strip(),
        "issue": issue,
    }
    return pd.DataFrame([result], columns=columns)


def _connector_telemetry_enabled() -> bool:
    raw = _get_env("CONNECTOR_TELEMETRY_ENABLED")
    if raw:
        return raw.lower() not in {"0", "false", "no", "off", "disabled"}
    return bool(_get_env("PIPELINE_JOB_NAME") or _get_env("APP_TRACK"))


def _ensure_connector_call_events_table(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS connector_call_events (
                id BIGSERIAL PRIMARY KEY,
                provider TEXT NOT NULL,
                operation TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at_utc TIMESTAMPTZ NOT NULL,
                duration_ms DOUBLE PRECISION,
                http_status INTEGER,
                result_count INTEGER,
                error_type TEXT,
                error_summary TEXT,
                job_name TEXT,
                run_id TEXT,
                metadata_json JSONB,
                created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_connector_call_events_started
            ON connector_call_events (started_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_connector_call_events_provider_status
            ON connector_call_events (provider, status, started_at_utc DESC)
            """
        )


def record_connector_call(
    *,
    provider: str,
    operation: str,
    status: str,
    started_at_utc: datetime | None = None,
    duration_ms: float | None = None,
    http_status: int | None = None,
    result_count: int | None = None,
    error_type: str = "",
    error_summary: str = "",
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Best-effort connector telemetry for Admin > System Health."""
    if not _connector_telemetry_enabled():
        return False
    normalized_provider = _clean_text(provider).lower() or "unknown"
    normalized_operation = _clean_text(operation) or "request"
    normalized_status = _clean_text(status).lower() or "unknown"
    timestamp = started_at_utc or datetime.now(timezone.utc)
    safe_error_summary = _clean_text(error_summary)[:800]
    metadata_dict = dict(metadata or {})
    safe_metadata = json.dumps(metadata_dict, ensure_ascii=False, sort_keys=True, default=str)
    linked_run_id = _clean_text(metadata_dict.get("run_id")) or _get_env("PIPELINE_RUN_ID")
    conn = open_database_connection()
    if conn is None:
        return False
    try:
        _ensure_connector_call_events_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO connector_call_events (
                    provider, operation, status, started_at_utc, duration_ms, http_status,
                    result_count, error_type, error_summary, job_name, run_id, metadata_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    normalized_provider,
                    normalized_operation,
                    normalized_status,
                    timestamp,
                    float(duration_ms) if duration_ms is not None else None,
                    int(http_status) if http_status is not None else None,
                    int(result_count) if result_count is not None else None,
                    _clean_text(error_type)[:120],
                    safe_error_summary,
                    _get_env("PIPELINE_JOB_NAME") or _get_env("APP_TRACK"),
                    linked_run_id,
                    safe_metadata,
                ),
            )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def connector_call_rollup(*, days: int = 7) -> pd.DataFrame:
    """Return connector call success/failure counts from telemetry."""
    columns = [
        "provider",
        "operation",
        "call_count",
        "success_count",
        "failure_count",
        "result_count",
        "avg_duration_ms",
        "last_call_at_utc",
        "last_error_summary",
    ]
    conn = open_database_connection()
    if conn is None:
        return pd.DataFrame(columns=columns)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_regclass('public.connector_call_events')
                """
            )
            if not cur.fetchone()[0]:
                return pd.DataFrame(columns=columns)
            cur.execute(
                """
                WITH recent AS (
                    SELECT *
                    FROM connector_call_events
                    WHERE started_at_utc >= NOW() - (%s::text || ' days')::interval
                ),
                latest_errors AS (
                    SELECT DISTINCT ON (provider, operation)
                        provider, operation, error_summary
                    FROM recent
                    WHERE status <> 'success' AND COALESCE(error_summary, '') <> ''
                    ORDER BY provider, operation, started_at_utc DESC
                )
                SELECT
                    r.provider,
                    r.operation,
                    COUNT(*)::int AS call_count,
                    SUM(CASE WHEN r.status = 'success' THEN 1 ELSE 0 END)::int AS success_count,
                    SUM(CASE WHEN r.status <> 'success' THEN 1 ELSE 0 END)::int AS failure_count,
                    COALESCE(SUM(r.result_count), 0)::int AS result_count,
                    ROUND(AVG(r.duration_ms)::numeric, 1)::float AS avg_duration_ms,
                    MAX(r.started_at_utc) AS last_call_at_utc,
                    COALESCE(MAX(le.error_summary), '') AS last_error_summary
                FROM recent r
                LEFT JOIN latest_errors le
                  ON le.provider = r.provider AND le.operation = r.operation
                GROUP BY r.provider, r.operation
                ORDER BY failure_count DESC, call_count DESC, provider, operation
                """,
                (max(int(days), 1),),
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows, columns=columns)
        for column in ("call_count", "success_count", "failure_count", "result_count"):
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)
        df["avg_duration_ms"] = pd.to_numeric(df["avg_duration_ms"], errors="coerce")
        df["last_call_at_utc"] = pd.to_datetime(df["last_call_at_utc"], errors="coerce", utc=True)
        return df
    except Exception:
        return pd.DataFrame(columns=columns)
    finally:
        conn.close()


_EXECUTION_SCHEMA_READY: set[tuple[str, str]] = set()
_EXECUTION_SCHEMA_LOCK = threading.Lock()


def _ensure_execution_store(conn: Any) -> None:
    identity = (str(conn.info.host), str(conn.info.dbname))
    with _EXECUTION_SCHEMA_LOCK:
        if identity in _EXECUTION_SCHEMA_READY:
            return
        with conn.cursor() as cur:
            cur.execute("CREATE SEQUENCE IF NOT EXISTS aql_zopedia_execution_fence")
            cur.execute("""CREATE TABLE IF NOT EXISTS aql_zopedia_execution_state (
                namespace TEXT NOT NULL, state_key TEXT NOT NULL, payload JSONB NOT NULL,
                updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY(namespace,state_key))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS aql_zopedia_provider_attempts (
                attempt_id TEXT PRIMARY KEY, model_call_id TEXT, root_run_id TEXT,
                parent_run_id TEXT, settings_scope TEXT, surface TEXT, purpose TEXT,
                provider TEXT, requested_model TEXT, reported_model TEXT, operation TEXT,
                status TEXT, http_status INTEGER, started_at_utc TIMESTAMPTZ NOT NULL,
                duration_ms DOUBLE PRECISION, usage_json JSONB, metadata_json JSONB)""")
            cur.execute("""CREATE INDEX IF NOT EXISTS idx_aql_provider_attempts_scope_time
                ON aql_zopedia_provider_attempts(settings_scope,started_at_utc DESC)""")
            cur.execute("""CREATE INDEX IF NOT EXISTS idx_aql_provider_attempts_root
                ON aql_zopedia_provider_attempts(root_run_id,started_at_utc)""")
        conn.commit()
        _EXECUTION_SCHEMA_READY.add(identity)


def read_execution_state(namespace: str, state_key: str, *, strict: bool = False) -> dict[str, Any] | None:
    """Read an owned checkpoint; unavailable persistence is distinct from a hit."""
    conn = open_database_connection()
    if conn is None:
        if strict:
            raise RuntimeError("Research execution store is unavailable.")
        return None
    with closing(conn):
        _ensure_execution_store(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM aql_zopedia_execution_state WHERE namespace=%s AND state_key=%s",
                        (namespace, state_key))
            row = cur.fetchone()
        return dict(row[0]) if row else None


def claim_execution_state(namespace: str, state_key: str, *, owner: str, seconds: float) -> int:
    """Short SQL transaction; a lease never holds a connection during inference."""
    conn = open_database_connection()
    if conn is None:
        raise RuntimeError("Research execution store is unavailable.")
    with closing(conn):
        _ensure_execution_store(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO aql_zopedia_execution_state(namespace,state_key,payload)
                VALUES (%s,%s,jsonb_build_object('owner',%s::text,'fence',nextval('aql_zopedia_execution_fence'),'expires_at',
                    clock_timestamp()+%s*interval '1 second'))
                ON CONFLICT(namespace,state_key) DO UPDATE
                SET payload=excluded.payload,updated_at_utc=now()
                WHERE (aql_zopedia_execution_state.payload->>'expires_at')::timestamptz < clock_timestamp()
                RETURNING (payload->>'fence')::bigint""", (namespace, state_key, owner, max(float(seconds), 1)))
            row = cur.fetchone()
            claimed = int(row[0]) if row else 0
        conn.commit()
        return claimed


def release_execution_state(namespace: str, state_key: str, *, owner: str) -> None:
    conn = open_database_connection()
    if conn is None:
        return
    with closing(conn):
        with conn.cursor() as cur:
            cur.execute("""DELETE FROM aql_zopedia_execution_state
                WHERE namespace=%s AND state_key=%s AND payload->>'owner'=%s""",
                (namespace, state_key, owner))
        conn.commit()


def renew_execution_state(namespace: str, state_key: str, *, owner: str, seconds: float) -> bool:
    conn = open_database_connection()
    if conn is None:
        raise RuntimeError("Execution ownership store is unavailable.")
    with closing(conn):
        with conn.cursor() as cur:
            cur.execute("""UPDATE aql_zopedia_execution_state SET payload=jsonb_set(payload,
                '{expires_at}',to_jsonb(clock_timestamp()+%s*interval '1 second')),updated_at_utc=now()
                WHERE namespace=%s AND state_key=%s AND payload->>'owner'=%s
                  AND (payload->>'expires_at')::timestamptz > clock_timestamp()
                RETURNING state_key""", (seconds, namespace, state_key, owner))
            renewed = cur.fetchone() is not None
        conn.commit()
        return renewed


def mutate_execution_state(namespace: str, state_key: str, transform) -> dict[str, Any]:
    """Serialize an arithmetic state update; never perform remote work in transform."""
    key = (namespace, state_key)
    return mutate_execution_states([key], lambda values: {key: transform(values[key])})[key]


def mutate_execution_states(keys: list[tuple[str, str]], transform) -> dict[tuple[str, str], dict[str, Any]]:
    """Lock related accounting rows in a stable order and commit them together.

    Account credit and a logical run's allowance must either both admit a
    request or neither do so. The transform is local arithmetic only.
    """
    keys = sorted(set(keys))
    conn = open_database_connection()
    if conn is None:
        raise RuntimeError("Execution accounting store is unavailable.")
    with closing(conn):
        _ensure_execution_store(conn)
        with conn.cursor() as cur:
            values = {}
            for namespace, state_key in keys:
                cur.execute("""INSERT INTO aql_zopedia_execution_state(namespace,state_key,payload)
                    VALUES (%s,%s,'{}'::jsonb) ON CONFLICT DO NOTHING""", (namespace, state_key))
                cur.execute("""SELECT payload FROM aql_zopedia_execution_state
                    WHERE namespace=%s AND state_key=%s FOR UPDATE""", (namespace, state_key))
                values[(namespace, state_key)] = dict(cur.fetchone()[0])
            updated = transform(values)
            if set(updated) != set(keys):
                raise ValueError("An accounting mutation must return all locked rows.")
            updated = {key: to_jsonable(value) for key, value in updated.items()}
            for (namespace, state_key), value in updated.items():
                cur.execute("""UPDATE aql_zopedia_execution_state SET payload=%s::jsonb,updated_at_utc=now()
                    WHERE namespace=%s AND state_key=%s""",
                    (json.dumps(value, allow_nan=False, ensure_ascii=False, default=str), namespace, state_key))
        conn.commit()
        return updated


def write_execution_state(namespace: str, state_key: str, payload: dict[str, Any], *, immutable: bool = False) -> bool:
    conn = open_database_connection()
    if conn is None:
        return False
    with closing(conn):
        _ensure_execution_store(conn)
        conflict = "DO NOTHING" if immutable else "DO UPDATE SET payload=excluded.payload,updated_at_utc=now()"
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO aql_zopedia_execution_state(namespace,state_key,payload)
                VALUES (%s,%s,%s::jsonb) ON CONFLICT(namespace,state_key) """ + conflict,
                        (namespace, state_key, json.dumps(to_jsonable(payload), ensure_ascii=False, allow_nan=False, default=str)))
        conn.commit()
        return True


def read_execution_states(namespace: str, state_keys: list[str]) -> dict[str, dict[str, Any]]:
    if not state_keys:
        return {}
    conn = open_database_connection()
    if conn is None:
        return {}
    with closing(conn):
        _ensure_execution_store(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT state_key,payload FROM aql_zopedia_execution_state WHERE namespace=%s AND state_key=ANY(%s)",
                        (namespace, state_keys))
            return {row[0]: dict(row[1]) for row in cur.fetchall()}


def retain_research_checkpoint(*, namespace: str, input_key: str, artifact: dict[str, Any],
                               subject: str, task: str) -> dict[str, Any] | None:
    """Publish an immutable result and its replay/latest pointers atomically."""
    conn = open_database_connection()
    if conn is None:
        return None
    with closing(conn):
        _ensure_execution_store(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO aql_zopedia_execution_state(namespace,state_key,payload)
                VALUES (%s,%s,%s::jsonb) ON CONFLICT(namespace,state_key) DO NOTHING""",
                (namespace, artifact["artifact_id"], json.dumps(to_jsonable(artifact), ensure_ascii=False, allow_nan=False, default=str)))
            cur.execute("SELECT payload FROM aql_zopedia_execution_state WHERE namespace=%s AND state_key=%s",
                        (namespace, artifact["artifact_id"]))
            stored = dict(cur.fetchone()[0])
            pointer = json.dumps({"artifact_id": stored["artifact_id"], "created_at_utc": stored["created_at_utc"]})
            pointers = [(namespace + ":inputs", input_key, pointer)]
            if subject:
                pointers.append((namespace + ":latest", subject + ":" + task, pointer))
            cur.executemany("""INSERT INTO aql_zopedia_execution_state(namespace,state_key,payload)
                VALUES (%s,%s,%s::jsonb) ON CONFLICT(namespace,state_key)
                DO UPDATE SET payload=excluded.payload,updated_at_utc=now()""", pointers)
        conn.commit()
        return stored


def write_execution_states(namespace: str, values: dict[str, dict[str, Any]]) -> bool:
    if not values:
        return True
    conn = open_database_connection()
    if conn is None:
        return False
    with closing(conn):
        _ensure_execution_store(conn)
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO aql_zopedia_execution_state(namespace,state_key,payload)
                VALUES (%s,%s,%s::jsonb) ON CONFLICT(namespace,state_key) DO NOTHING""",
                [(namespace, key, json.dumps(to_jsonable(value), ensure_ascii=False, allow_nan=False, default=str)) for key, value in values.items()])
        conn.commit()
        return True


def retain_provider_response(namespace: str, key: str, *, value: dict[str, Any]) -> dict[str, Any]:
    """Retain complete paid output privately, independently of child adoption.

    Exact-request replay belongs to its original logical run. It grants no
    research acceptance, publication permission or knowledge-write authority.
    """
    artifact_id = hashlib.sha256((key + value["attempt_id"]).encode()).hexdigest()
    reference = {"namespace": namespace, "key": artifact_id, "input_key": key}
    try:
        if write_execution_state(namespace, artifact_id, value, immutable=True):
            if value.get("replayable_after_cancellation") and not write_execution_state(
                    namespace + ":inputs", key, {"artifact_id": artifact_id}):
                raise RuntimeError("Late response pointer awaits recovery")
            return {**reference, "stored": "database"}
    except Exception:
        pass
    archive = ImmutableBlobArchive(namespace=namespace.split(":", 1)[1], root="provider-responses")
    receipt = archive.put(json.dumps(to_jsonable(value), sort_keys=True, allow_nan=False).encode(),
                          suffix="json", content_type="application/json")
    return {**reference, "stored": "archive", "receipt": receipt}


def record_provider_attempt(event: dict[str, Any], *, archive_on_failure: bool = True) -> bool:
    """One actual HTTP attempt. Logical gateway events never enter this ledger."""
    if not _model_call_telemetry_enabled():
        return False
    conn = None
    try:
        conn = open_database_connection()
        if conn is None:
            raise RuntimeError("Provider attempt database is unavailable")
        _ensure_execution_store(conn)
        fields = ("attempt_id", "model_call_id", "root_run_id", "parent_run_id", "settings_scope",
                  "surface", "purpose", "provider", "requested_model", "reported_model", "operation",
                  "status", "http_status", "started_at_utc", "duration_ms")
        with conn.cursor() as cur:
            cur.execute("INSERT INTO aql_zopedia_provider_attempts (" + ",".join(fields)
                        + ",usage_json,metadata_json) VALUES (" + ",".join(["%s"] * len(fields))
                        + ",%s::jsonb,%s::jsonb) ON CONFLICT(attempt_id) DO UPDATE SET "
                        "reported_model=EXCLUDED.reported_model,status=EXCLUDED.status,http_status=EXCLUDED.http_status,"
                        "duration_ms=EXCLUDED.duration_ms,usage_json=EXCLUDED.usage_json,metadata_json=EXCLUDED.metadata_json "
                        "WHERE aql_zopedia_provider_attempts.status='started' AND EXCLUDED.status != 'started'",
                        (*[event.get(field) for field in fields],
                         json.dumps(event.get("usage"), default=str),
                         json.dumps(event.get("metadata") or {}, default=str)))
        conn.commit()
        from .llm_execution import reconcile_provider_reservation
        if not (event.get("metadata") or {}).get("reservation_settled"):
            reconcile_provider_reservation(event)
        retained = (event.get("metadata") or {}).get("retained_response") or {}
        if retained.get("stored") == "archive" and retained.get("receipt"):
            archive = ImmutableBlobArchive(namespace=retained["namespace"].split(":", 1)[1], root="provider-responses")
            value = json.loads(archive.get(retained["receipt"]))
            if not write_execution_state(retained["namespace"], retained["key"], value, immutable=True):
                raise RuntimeError("Complete paid response awaits database recovery")
            if value.get("replayable_after_cancellation") and not write_execution_state(
                    retained["namespace"] + ":inputs", retained["input_key"], {"artifact_id": retained["key"]}):
                raise RuntimeError("Late response pointer awaits database recovery")
        return True
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()
    if not archive_on_failure:
        return False
    # The archive is independent of Postgres. Keep the full metering receipt,
    # never prompts or credentials, until the spend reader reconciles it.
    timestamp = pd.to_datetime(event["started_at_utc"], utc=True)
    archive = ImmutableBlobArchive(namespace=event["settings_scope"],
                                   root="provider-attempt-outbox/" + timestamp.strftime("%Y-%m-%d"))
    receipt = archive.put(json.dumps(event, sort_keys=True, default=str).encode(),
                          suffix="json", content_type="application/json")
    # Reconciliation of a start receipt must not delete a later final receipt.
    suffix = ".started.json" if event.get("status") == "started" else ".json"
    path = archive.point(event["attempt_id"] + suffix, receipt)
    print(f"[warn] Provider attempt retained for database reconciliation: {path}", flush=True)
    return True


def reconcile_provider_budget_reservations(*, limit: int = 25) -> dict[str, Any]:
    """Settle known late receipts against their original run/account identity.

    Scan outstanding obligations, including older ones, rather than hoping a
    final receipt appears in a recent-date window. Unknown usage stays held.
    """
    from .llm import llm_settings_scope
    from .llm_execution import reconcile_provider_reservation
    from .database import database_workload
    from .work_scope import work_expired
    with database_workload("interactive"):
        conn = open_database_connection()
        if conn is None:
            raise RuntimeError("Provider accounting recovery requires its database")
        with closing(conn):
            _ensure_execution_store(conn)
            with conn.cursor() as cur:
                cur.execute("""WITH outstanding AS (
                    SELECT DISTINCT attempt.key AS attempt_id
                    FROM aql_zopedia_execution_state s
                    CROSS JOIN LATERAL jsonb_each(CASE WHEN s.namespace='research_run_budget'
                        THEN s.payload->'attempts' ELSE s.payload->'reservations' END) attempt
                    WHERE s.namespace IN ('research_run_budget','provider_budget')
                      AND attempt.value->>'status' IN ('pending','unknown')
                ) SELECT row_to_json(a) FROM outstanding o
                    JOIN aql_zopedia_provider_attempts a ON a.attempt_id=o.attempt_id
                    WHERE a.settings_scope=%s AND a.status <> 'started'
                      AND a.metadata_json->'budget_reservation' IS NOT NULL
                      AND (a.metadata_json->'estimated_charge'->>'status'='estimated'
                           OR a.status='not_sent' OR a.http_status=402)
                    ORDER BY a.started_at_utc LIMIT %s""", (llm_settings_scope(), max(1, limit)))
                events = [dict(row[0]) for row in cur.fetchall()]
            conn.commit()
    settled = 0
    for event in events:
        if work_expired():
            return {"settled": settled, "scan_complete": False}
        reconcile_provider_reservation({**event, "metadata": event.get("metadata_json") or {}})
        settled += 1
    return {"settled": settled, "scan_complete": len(events) < max(1, limit)}


def reconcile_provider_attempt_receipts(*, days: int = 7, limit: int = 1000) -> dict[str, Any]:
    """Drain durable receipts after SQL recovery, with attempt-ID idempotency."""
    from datetime import timedelta
    from azure.core.exceptions import ResourceNotFoundError
    from services.work_scope import work_expired, WorkCancelled
    from .llm import llm_settings_scope
    scope = llm_settings_scope()
    archive = ImmutableBlobArchive(namespace=scope, root="provider-attempt-outbox")
    processed = reconciled = 0
    for offset in range(max(int(days), 1)):
        date = (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")
        prefix = archive.root + "/" + date + "/"
        if work_expired():
            return {"reconciled": reconciled, "scan_complete": False}
        for blob in archive.container.walk_blobs(name_starts_with=prefix, delimiter="/", **_blob_io_options()):
            name = blob.name[len(prefix):]
            if "/" in name or not name.endswith(".json"):
                continue
            if processed >= limit or work_expired():
                return {"reconciled": reconciled, "scan_complete": False}
            processed += 1
            pointer = archive.container.get_blob_client(blob.name)
            try:
                receipt = json.loads(pointer.download_blob(**_blob_io_options()).readall())
                event = json.loads(archive.get(receipt))
                if event.get("settings_scope") != scope:
                    raise ValueError("Provider receipt environment does not match its archive")
                if not record_provider_attempt(event, archive_on_failure=False):
                    return {"reconciled": reconciled, "scan_complete": False}
                # SQL commits before deletion. A competing reader or interrupted
                # delete can repeat the insert safely under the attempt ID.
                pointer.delete_blob(**_blob_io_options())
                reconciled += 1
            except ResourceNotFoundError:
                continue
            except WorkCancelled:
                return {"reconciled": reconciled, "scan_complete": False}
    return {"reconciled": reconciled, "scan_complete": True}


def list_research_artifacts(*, namespace: str, subjects: list[str] | None = None,
                            limit: int = 20) -> list[dict[str, Any]]:
    """List latest public research within the caller's declared environment."""
    conn = open_database_connection()
    if conn is None:
        raise RuntimeError("Completed investigations store is unavailable")
    with closing(conn):
        _ensure_execution_store(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT a.payload FROM aql_zopedia_execution_state p
                JOIN aql_zopedia_execution_state a
                  ON a.namespace=%s AND a.state_key=p.payload->>'artifact_id'
                WHERE p.namespace=%s AND (%s::text[] IS NULL OR a.payload->>'subject'=ANY(%s))
                ORDER BY p.updated_at_utc DESC LIMIT %s""",
                        (namespace, namespace + ":latest", subjects or None, subjects or None, min(max(limit, 1), 100)))
            return [dict(row[0]) for row in cur.fetchall()]


def provider_usage_rollup(*, days: int = 7) -> pd.DataFrame:
    """Attempt-based spend view with explicit unpriced and missing-usage counts."""
    reconciliation = {"scan_complete": None, "deferred": True}
    conn = open_database_connection()
    if conn is None:
        frame = pd.DataFrame()
        frame.attrs.update(database_available=False, receipt_reconciliation=reconciliation)
        return frame
    with closing(conn):
        _ensure_execution_store(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT settings_scope,surface,purpose,provider,reported_model,
                count(*) AS http_attempts,
                count(*) FILTER (WHERE status='failure') AS failed_attempts,
                count(*) FILTER (WHERE usage_json IS NULL OR usage_json='null'::jsonb OR usage_json='{}'::jsonb) AS missing_usage_attempts,
                sum((usage_json->>'prompt_tokens')::bigint) AS input_tokens,
                sum((usage_json->>'completion_tokens')::bigint) AS output_tokens,
                sum(coalesce((usage_json->>'prompt_cache_hit_tokens')::bigint,
                             (usage_json->'prompt_tokens_details'->>'cached_tokens')::bigint)) AS cached_input_tokens,
                sum((usage_json->'completion_tokens_details'->>'reasoning_tokens')::bigint) AS reasoning_output_tokens,
                count(*) FILTER (WHERE metadata_json->'estimated_charge'->>'status'='estimated') AS priced_attempts,
                sum((metadata_json->'estimated_charge'->>'usd_min')::numeric) AS estimated_usd_min,
                sum((metadata_json->'estimated_charge'->>'usd_max')::numeric) AS estimated_usd_max
                FROM aql_zopedia_provider_attempts
                WHERE started_at_utc >= now() - (%s * interval '1 day')
                GROUP BY settings_scope,surface,purpose,provider,reported_model
                ORDER BY estimated_usd_max DESC NULLS LAST""", (max(int(days), 1),))
            columns = [column.name for column in cur.description]
            frame = pd.DataFrame(cur.fetchall(), columns=columns)
            frame.attrs["receipt_reconciliation"] = reconciliation
            return frame




def _model_call_telemetry_enabled() -> bool:
    raw = _get_env("AQL_ZOPEDIA_MODEL_TELEMETRY_ENABLED") or _get_env("MODEL_CALL_TELEMETRY_ENABLED")
    if raw:
        return raw.lower() not in {"0", "false", "no", "off", "disabled"}
    return bool(_get_env("PIPELINE_JOB_NAME") or _get_env("APP_TRACK"))


def _ensure_model_call_events_table(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS aql_zopedia_model_call_events (
                id BIGSERIAL PRIMARY KEY,
                model_call_id TEXT NOT NULL,
                surface TEXT NOT NULL,
                purpose TEXT NOT NULL,
                call_type TEXT NOT NULL,
                provider TEXT,
                requested_model TEXT,
                resolved_model TEXT,
                provider_reported_model TEXT,
                status TEXT NOT NULL,
                started_at_utc TIMESTAMPTZ NOT NULL,
                duration_ms DOUBLE PRECISION,
                error_type TEXT,
                error_summary TEXT,
                schema_name TEXT,
                prompt_hash TEXT,
                schema_hash TEXT,
                job_name TEXT,
                run_id TEXT,
                metadata_json JSONB,
                created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_aql_zopedia_model_call_events_started
            ON aql_zopedia_model_call_events (started_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_aql_zopedia_model_call_events_surface_status
            ON aql_zopedia_model_call_events (surface, purpose, status, started_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_aql_zopedia_model_call_events_provider_model
            ON aql_zopedia_model_call_events (provider, resolved_model, started_at_utc DESC)
            """
        )


def record_model_call(
    *,
    model_call_id: str,
    surface: str,
    purpose: str,
    call_type: str,
    provider: str = "",
    requested_model: str = "",
    resolved_model: str = "",
    provider_reported_model: str = "",
    status: str,
    started_at_utc: datetime | None = None,
    duration_ms: float | None = None,
    error_type: str = "",
    error_summary: str = "",
    schema_name: str = "",
    prompt_hash: str = "",
    schema_hash: str = "",
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Best-effort AQL/Zopedia model telemetry for Admin > System Health."""
    if not _model_call_telemetry_enabled():
        return False
    timestamp = started_at_utc or datetime.now(timezone.utc)
    metadata_payload = dict(metadata or {})
    event_run_id = (
        _clean_text(metadata_payload.get("run_id"))
        or _clean_text(metadata_payload.get("parent_run_id"))
        or _get_env("PIPELINE_RUN_ID")
    )
    safe_metadata = json.dumps(metadata_payload, ensure_ascii=False, sort_keys=True, default=str)
    conn = open_database_connection()
    if conn is None:
        return False
    try:
        _ensure_model_call_events_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aql_zopedia_model_call_events (
                    model_call_id, surface, purpose, call_type, provider, requested_model,
                    resolved_model, provider_reported_model, status, started_at_utc, duration_ms,
                    error_type, error_summary, schema_name, prompt_hash, schema_hash, job_name,
                    run_id, metadata_json
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s::jsonb
                )
                """,
                (
                    _clean_text(model_call_id)[:120],
                    _clean_text(surface)[:180],
                    _clean_text(purpose)[:180],
                    _clean_text(call_type)[:80],
                    _clean_text(provider).lower()[:80],
                    _clean_text(requested_model)[:180],
                    _clean_text(resolved_model)[:180],
                    _clean_text(provider_reported_model)[:180],
                    _clean_text(status).lower()[:80],
                    timestamp,
                    float(duration_ms) if duration_ms is not None else None,
                    _clean_text(error_type)[:120],
                    _clean_text(error_summary)[:800],
                    _clean_text(schema_name)[:180],
                    _clean_text(prompt_hash)[:96],
                    _clean_text(schema_hash)[:96],
                    _get_env("PIPELINE_JOB_NAME") or _get_env("APP_TRACK"),
                    event_run_id[:180],
                    safe_metadata,
                ),
            )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def model_call_rollup(*, days: int = 7) -> pd.DataFrame:
    """Return AQL/Zopedia model call success/failure counts from telemetry."""
    columns = [
        "surface",
        "purpose",
        "call_type",
        "provider",
        "resolved_model",
        "call_count",
        "success_count",
        "failure_count",
        "avg_duration_ms",
        "last_call_at_utc",
        "last_error_summary",
    ]
    conn = open_database_connection()
    if conn is None:
        return pd.DataFrame(columns=columns)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.aql_zopedia_model_call_events')")
            if not cur.fetchone()[0]:
                return pd.DataFrame(columns=columns)
            cur.execute(
                """
                WITH recent AS (
                    SELECT *
                    FROM aql_zopedia_model_call_events
                    WHERE started_at_utc >= NOW() - (%s::text || ' days')::interval
                ),
                latest_errors AS (
                    SELECT DISTINCT ON (surface, purpose, call_type, provider, resolved_model)
                        surface, purpose, call_type, provider, resolved_model, error_summary
                    FROM recent
                    WHERE status <> 'success' AND COALESCE(error_summary, '') <> ''
                    ORDER BY surface, purpose, call_type, provider, resolved_model, started_at_utc DESC
                )
                SELECT
                    r.surface,
                    r.purpose,
                    r.call_type,
                    COALESCE(r.provider, '') AS provider,
                    COALESCE(r.resolved_model, '') AS resolved_model,
                    COUNT(*)::int AS call_count,
                    SUM(CASE WHEN r.status = 'success' THEN 1 ELSE 0 END)::int AS success_count,
                    SUM(CASE WHEN r.status <> 'success' THEN 1 ELSE 0 END)::int AS failure_count,
                    ROUND(AVG(r.duration_ms)::numeric, 1)::float AS avg_duration_ms,
                    MAX(r.started_at_utc) AS last_call_at_utc,
                    COALESCE(MAX(le.error_summary), '') AS last_error_summary
                FROM recent r
                LEFT JOIN latest_errors le
                  ON le.surface = r.surface
                 AND le.purpose = r.purpose
                 AND le.call_type = r.call_type
                 AND COALESCE(le.provider, '') = COALESCE(r.provider, '')
                 AND COALESCE(le.resolved_model, '') = COALESCE(r.resolved_model, '')
                GROUP BY r.surface, r.purpose, r.call_type, r.provider, r.resolved_model
                ORDER BY failure_count DESC, call_count DESC, surface, purpose
                """,
                (max(int(days), 1),),
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows, columns=columns)
        for column in ("call_count", "success_count", "failure_count"):
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)
        df["avg_duration_ms"] = pd.to_numeric(df["avg_duration_ms"], errors="coerce")
        df["last_call_at_utc"] = pd.to_datetime(df["last_call_at_utc"], errors="coerce", utc=True)
        return df
    except Exception:
        return pd.DataFrame(columns=columns)
    finally:
        conn.close()


def retained_connector_evidence_health(*, days: int = 7) -> pd.DataFrame:
    """Summarize retained evidence rows by connector/provider as a fallback signal."""
    columns = [
        "provider",
        "evidence_rows",
        "document_rows",
        "chunk_rows",
        "provider_error_rows",
        "last_seen_at_utc",
    ]
    conn = open_database_connection()
    if conn is None:
        return pd.DataFrame(columns=columns)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH evidence AS (
                    SELECT
                        COALESCE(NULLIF(lower(search_provider), ''), NULLIF(lower(source_provider), ''), 'unknown') AS provider,
                        'document' AS row_kind,
                        GREATEST(first_seen_at_utc, last_seen_at_utc) AS seen_at,
                        lower(COALESCE(title, '') || ' ' || COALESCE(display_excerpt, '') || ' ' || COALESCE(search_text, '')) AS text_blob
                    FROM saa_documents
                    WHERE GREATEST(first_seen_at_utc, last_seen_at_utc) >= NOW() - (%s::text || ' days')::interval
                    UNION ALL
                    SELECT
                        COALESCE(NULLIF(lower(search_provider), ''), NULLIF(lower(source_provider), ''), 'unknown') AS provider,
                        'chunk' AS row_kind,
                        asof_time_utc AS seen_at,
                        lower(COALESCE(title, '') || ' ' || COALESCE(display_excerpt, '') || ' ' || COALESCE(chunk_text, '') || ' ' || COALESCE(search_text, '')) AS text_blob
                    FROM saa_evidence_chunks
                    WHERE asof_time_utc >= NOW() - (%s::text || ' days')::interval
                )
                SELECT
                    provider,
                    COUNT(*)::int AS evidence_rows,
                    SUM(CASE WHEN row_kind = 'document' THEN 1 ELSE 0 END)::int AS document_rows,
                    SUM(CASE WHEN row_kind = 'chunk' THEN 1 ELSE 0 END)::int AS chunk_rows,
                    SUM(
                        CASE
                            WHEN text_blob LIKE '%%request failed%%'
                              OR text_blob LIKE '%%usage limit%%'
                              OR text_blob LIKE '%%rate limit%%'
                              OR text_blob LIKE '%%unauthorized%%'
                              OR text_blob LIKE '%%forbidden%%'
                              OR text_blob LIKE '%%timeout%%'
                            THEN 1 ELSE 0
                        END
                    )::int AS provider_error_rows,
                    MAX(seen_at) AS last_seen_at_utc
                FROM evidence
                GROUP BY provider
                ORDER BY provider_error_rows DESC, evidence_rows DESC, provider
                """,
                (max(int(days), 1), max(int(days), 1)),
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows, columns=columns)
        for column in ("evidence_rows", "document_rows", "chunk_rows", "provider_error_rows"):
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)
        df["last_seen_at_utc"] = pd.to_datetime(df["last_seen_at_utc"], errors="coerce", utc=True)
        return df
    except Exception:
        return pd.DataFrame(columns=columns)
    finally:
        conn.close()
