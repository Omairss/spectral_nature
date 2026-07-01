from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
import uuid

import pandas as pd
import requests

from .secrets import build_azure_credential, postgres_connect_timeout_seconds, resolve_secret_value


try:
    import psycopg
except Exception:
    psycopg = None

try:
    from azure.storage.blob import BlobServiceClient
except Exception:
    BlobServiceClient = None


APP_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_CACHE_ROOT = APP_ROOT / "cache" / "pipeline_store"
PIPELINE_METADATA_CACHE_SECONDS = max(int((os.getenv("PIPELINE_METADATA_CACHE_SECONDS") or "30").strip() or "30"), 0)
PIPELINE_CACHE_MAX_BYTES_DEFAULT = 256 * 1024 * 1024
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
        "trading_agent_runs",
        "trading_agent_candidates",
        "trading_agent_outcomes",
        "trading_agent_research_reviews",
    ],
}

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
    safe_dataset = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(dataset_name or "").strip())
    safe_version = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(dataset_version_id or "").strip())
    return PIPELINE_CACHE_ROOT / safe_dataset / safe_version


def _metadata_cache_path(dataset_name: str) -> Path:
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
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    max_bytes = _pipeline_cache_max_bytes()
    if _path_size_bytes(path) > max_bytes:
        _delete_cache_path(path)
        return
    _prune_pipeline_cache(keep_paths=(path,))


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


def _db_connect() -> Any | None:
    conn_str = _postgres_connection_string()
    if not conn_str or psycopg is None:
        return None
    try:
        return psycopg.connect(conn_str, connect_timeout=postgres_connect_timeout_seconds())
    except Exception:
        return None


def _blob_service_client() -> Any | None:
    if BlobServiceClient is None:
        return None
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


def _read_blob_json(blob_path: str) -> dict[str, Any] | None:
    client = _blob_service_client()
    if client is None:
        return None
    try:
        blob = client.get_blob_client(container=_storage_container(), blob=blob_path)
        payload = blob.download_blob().readall()
        if not payload:
            return None
        parsed = json.loads(payload.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else None
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
    return PipelineDataset(
        dataset_name=dataset_name,
        dataset_version_id=dataset_version_id,
        blob_path=blob_path,
        asof_time_utc=asof_time_utc,
        ingested_at_utc=ingested_at_utc,
        row_count=row_count,
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
    return right if right_key > left_key else left


def _stable_latest_manifest_metadata(dataset_name: str) -> PipelineDataset | None:
    latest_manifest = _read_blob_json(f"manifests/{dataset_name}/latest.json")
    if latest_manifest:
        dataset = _coerce_manifest_dataset(latest_manifest)
        if dataset is not None:
            return dataset
    return None


def _latest_manifest_metadata(dataset_name: str) -> PipelineDataset | None:
    stable_latest = _stable_latest_manifest_metadata(dataset_name)
    if stable_latest is not None:
        return stable_latest

    client = _blob_service_client()
    if client is None:
        return None
    try:
        container = client.get_container_client(_storage_container())
        prefix = f"manifests/{dataset_name}/"
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
    cached = _read_cached_metadata(dataset_name)
    if cached is not None:
        return cached

    manifest_dataset = _stable_latest_manifest_metadata(dataset_name)
    db_dataset: PipelineDataset | None = None
    conn = _db_connect()
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
                    (dataset_name,),
                )
                row = cur.fetchone()
            if row:
                db_dataset = PipelineDataset(
                    dataset_name=str(row[0]),
                    dataset_version_id=str(row[1]),
                    blob_path=str(row[2]),
                    asof_time_utc=str(row[3]),
                    ingested_at_utc=str(row[4]),
                    row_count=int(row[5] or 0),
                )
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

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
    conn = _db_connect()
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
                (dataset_name, target_end),
            )
            row = cur.fetchone()
            if not row:
                return None
            return PipelineDataset(
                dataset_name=str(row[0]),
                dataset_version_id=str(row[1]),
                blob_path=str(row[2]),
                asof_time_utc=str(row[3]),
                ingested_at_utc=str(row[4]),
                row_count=int(row[5] or 0),
            )
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


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


def load_latest_dataset_frame(dataset_name: str) -> tuple[pd.DataFrame, PipelineDataset | None]:
    metadata = latest_dataset_metadata(dataset_name)
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


def recent_dataset_metadata(dataset_name: str, *, limit: int = 8) -> list[PipelineDataset]:
    dataset_key = str(dataset_name or "").strip()
    if not dataset_key:
        return []
    capped_limit = max(1, min(int(limit or 8), 25))
    conn = _db_connect()
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
                (dataset_key, capped_limit),
            )
            rows = cur.fetchall()
        out: list[PipelineDataset] = []
        for row in rows or []:
            out.append(
                PipelineDataset(
                    dataset_name=str(row[0]),
                    dataset_version_id=str(row[1]),
                    blob_path=str(row[2]),
                    asof_time_utc=str(row[3]),
                    ingested_at_utc=str(row[4]),
                    row_count=int(row[5] or 0),
                )
            )
        return out
    except Exception:
        latest = latest_dataset_metadata(dataset_key)
        return [latest] if latest is not None else []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_recent_dataset_frames(dataset_name: str, *, limit: int = 8) -> list[tuple[pd.DataFrame, PipelineDataset]]:
    out: list[tuple[pd.DataFrame, PipelineDataset]] = []
    for metadata in recent_dataset_metadata(dataset_name, limit=limit):
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
                candidate_payload JSONB
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
    if text in {"place", "placed", "place_requested", "request_place"}:
        return "place_requested", "logged"
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
    """Persist an admin Trading Agent decision.

    `place` is intentionally logged as a review action. This function does not
    submit broker orders.
    """
    if not isinstance(candidate, dict) or not str(candidate.get("candidate_id") or "").strip():
        return False, "Missing Trading Agent candidate id."
    action_value, status_value = _normalize_trading_agent_action(action)
    if action_value not in {"place_requested", "rejected"}:
        return False, f"Unsupported Trading Agent action: {action}"
    conn = _db_connect()
    if conn is None:
        return False, "Postgres is unavailable, so the Trading Agent action was not logged."
    created_at = datetime.now(timezone.utc)
    action_id = str(uuid.uuid4())
    try:
        _bootstrap_trading_agent_actions(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trading_agent_actions (
                    action_id, candidate_id, trading_agent_run_id, run_id,
                    horizon_key, ticker, action, execution_mode, status,
                    broker, broker_order_id, order_payload, created_at_utc,
                    requested_by, requested_email, notes, candidate_payload
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s,
                    %s, %s, %s, %s::jsonb
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
                    "log_only",
                    status_value,
                    "alpaca",
                    None,
                    json.dumps({}, ensure_ascii=True, sort_keys=True),
                    created_at,
                    str(requested_by or "").strip() or None,
                    str(requested_email or "").strip() or None,
                    str(notes or "").strip() or None,
                    json.dumps(candidate, ensure_ascii=True, sort_keys=True, default=str),
                ),
            )
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f"Trading Agent action log failed: {type(exc).__name__}: {exc}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if action_value == "place_requested":
        return True, "Place decision logged for review. No broker order was submitted."
    return True, "Reject decision logged."


def trading_agent_actions_table(*, limit: int = 500) -> pd.DataFrame:
    columns = list(TRADING_AGENT_ACTION_COLUMNS)
    conn = _db_connect()
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
                ORDER BY created_at_utc DESC
                LIMIT %s
                """,
                (max(int(limit), 1),),
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
        try:
            conn.close()
        except Exception:
            pass


def start_source_refresh_job(source: str) -> tuple[bool, str]:
    source_key = str(source or "").strip().lower()
    job_name = SOURCE_JOB_MAP.get(source_key)
    if not job_name:
        return False, f"Unknown source: {source}"

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

    conn = _db_connect()
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
                            (job_name,),
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
                        (job_name,),
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
            try:
                conn.close()
            except Exception:
                pass
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
    conn = _db_connect()
    if conn is None:
        return pd.DataFrame(columns=columns)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_name, run_id, status, start_time_utc, end_time_utc,
                       error_summary, progress_stage
                FROM job_runs
                WHERE start_time_utc >= NOW() - INTERVAL '%s days'
                ORDER BY start_time_utc DESC
                """,
                (days,),
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows, columns=columns)
        df["status"] = df["status"].apply(_normalize_job_status_label)
        for col in ["start_time_utc", "end_time_utc"]:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
        return df
    except Exception:
        return pd.DataFrame(columns=columns)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def dataset_version_history(*, days: int = 7) -> pd.DataFrame:
    """Return recent dataset_versions rows for row-count visualization."""
    columns = [
        "dataset_name", "row_count", "ingested_at_utc", "run_id",
    ]
    conn = _db_connect()
    if conn is None:
        return pd.DataFrame(columns=columns)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dataset_name, row_count, ingested_at_utc, run_id
                FROM dataset_versions
                WHERE ingested_at_utc >= NOW() - INTERVAL '%s days'
                ORDER BY ingested_at_utc DESC
                """,
                (days,),
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows, columns=columns)
        df["row_count"] = pd.to_numeric(df["row_count"], errors="coerce").fillna(0).astype(int)
        df["ingested_at_utc"] = pd.to_datetime(df["ingested_at_utc"], errors="coerce", utc=True)
        return df
    except Exception:
        return pd.DataFrame(columns=columns)
    finally:
        try:
            conn.close()
        except Exception:
            pass


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
    conn = _db_connect()
    if conn is None:
        return pd.DataFrame(columns=columns)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (dataset_name)
                    dataset_name, dataset_version_id, row_count, ingested_at_utc, run_id
                FROM dataset_versions
                ORDER BY dataset_name, ingested_at_utc DESC NULLS LAST
                """
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows, columns=columns[:-1])
        df["row_count"] = pd.to_numeric(df["row_count"], errors="coerce").fillna(0).astype(int)
        df["ingested_at_utc"] = pd.to_datetime(df["ingested_at_utc"], errors="coerce", utc=True)
        now = pd.Timestamp.now(tz="UTC")
        df["age_hours"] = ((now - df["ingested_at_utc"]).dt.total_seconds() / 3600.0).round(1)
        return df[columns]
    except Exception:
        return pd.DataFrame(columns=columns)
    finally:
        try:
            conn.close()
        except Exception:
            pass


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
    conn = _db_connect()
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
                (names,),
            )
            rows = cur.fetchall()
        out: dict[str, PipelineDataset] = {}
        for row in rows or []:
            dataset = PipelineDataset(
                dataset_name=str(row[0]),
                dataset_version_id=str(row[1]),
                blob_path=str(row[2]),
                asof_time_utc=str(row[3]),
                ingested_at_utc=str(row[4]),
                row_count=int(row[5] or 0),
            )
            out[dataset.dataset_name] = dataset
        return out
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


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
    safe_metadata = json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True, default=str)
    conn = _db_connect()
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
                    _get_env("PIPELINE_RUN_ID"),
                    safe_metadata,
                ),
            )
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


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
    conn = _db_connect()
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
        try:
            conn.close()
        except Exception:
            pass


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
    conn = _db_connect()
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
        try:
            conn.close()
        except Exception:
            pass
