from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

import pandas as pd

from .secrets import build_azure_credential, resolve_secret_value


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
    "fundamentals": "equities-intraday-preload",
    "derivatives": "equities-intraday-preload",
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
    "fred": ["fred_summary", "fred_observations", "yield_curve_observations", "yield_curve_summary", "yield_curve_facts_1d"],
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
        "attention_ticker_snapshots_1d",
        "attention_ticker_background_snapshots",
        "attention_home_snapshots_1d",
        "attention_bundle_snapshots",
        "attention_home_1d",
        "attention_research_bundles",
    ],
    "taxonomy": ["us_equity_listings", "entity_taxonomy_labels"],
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
}


def _load_deployment_env() -> dict[str, str]:
    env_file = Path(__file__).resolve().parents[1] / "infra" / "deployment.outputs.env"
    values: dict[str, str] = {}
    if not env_file.exists():
        return values
    for line in env_file.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _pipeline_cache_dir(dataset_name: str, dataset_version_id: str) -> Path:
    safe_dataset = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(dataset_name or "").strip())
    safe_version = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(dataset_version_id or "").strip())
    return PIPELINE_CACHE_ROOT / safe_dataset / safe_version


def _metadata_cache_path(dataset_name: str) -> Path:
    safe_dataset = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(dataset_name or "").strip())
    return PIPELINE_CACHE_ROOT / "_metadata" / f"{safe_dataset}.json"


def _read_cached_metadata(dataset_name: str) -> PipelineDataset | None:
    if PIPELINE_METADATA_CACHE_SECONDS <= 0:
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
    if PIPELINE_METADATA_CACHE_SECONDS <= 0 or dataset is None:
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


def _get_env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _postgres_connection_string() -> str:
    return resolve_secret_value(
        ["POSTGRES_CONNECTION_STRING"],
        secret_name_env="POSTGRES_CONNECTION_STRING_SECRET",
        default_secret_name="postgres-connection-string",
    )


def _storage_account_url() -> str:
    direct = _get_env("AZURE_STORAGE_ACCOUNT_URL")
    if direct:
        return direct
    deployment = _load_deployment_env()
    return (deployment.get("STORAGE_URL") or "").strip()


def _storage_container() -> str:
    return _get_env("AZURE_STORAGE_CONTAINER", "datasets") or "datasets"


def _resource_group() -> str:
    direct = _get_env("PIPELINE_RESOURCE_GROUP")
    if direct:
        return direct
    deployment = _load_deployment_env()
    return (deployment.get("RESOURCE_GROUP") or "").strip()


def pipeline_store_configured() -> bool:
    return bool(_storage_account_url())


def _db_connect() -> Any | None:
    conn_str = _postgres_connection_string()
    if not conn_str or psycopg is None:
        return None
    try:
        return psycopg.connect(conn_str)
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
    path = _local_frame_cache_path(dataset)
    if not path.exists():
        return None
    try:
        frame = pd.read_pickle(path)
    except Exception:
        return None
    return frame if isinstance(frame, pd.DataFrame) else None


def _write_local_frame_cache(dataset: PipelineDataset, frame: pd.DataFrame) -> None:
    path = _local_frame_cache_path(dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_pickle(path)


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


def _latest_manifest_metadata(dataset_name: str) -> PipelineDataset | None:
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

    conn = _db_connect()
    if conn is None:
        dataset = _latest_manifest_metadata(dataset_name)
        _write_cached_metadata(dataset_name, dataset)
        return dataset
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
            if not row:
                dataset = _latest_manifest_metadata(dataset_name)
                _write_cached_metadata(dataset_name, dataset)
                return dataset
            dataset = PipelineDataset(
                dataset_name=str(row[0]),
                dataset_version_id=str(row[1]),
                blob_path=str(row[2]),
                asof_time_utc=str(row[3]),
                ingested_at_utc=str(row[4]),
                row_count=int(row[5] or 0),
            )
            _write_cached_metadata(dataset_name, dataset)
            return dataset
    except Exception:
        dataset = _latest_manifest_metadata(dataset_name)
        _write_cached_metadata(dataset_name, dataset)
        return dataset
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_latest_dataset_frame(dataset_name: str) -> tuple[pd.DataFrame, PipelineDataset | None]:
    metadata = latest_dataset_metadata(dataset_name)
    if metadata is None:
        return pd.DataFrame(), None
    cached = _read_local_frame_cache(metadata)
    if cached is not None:
        return cached, metadata
    frame = _read_blob_parquet(metadata.blob_path)
    if isinstance(frame, pd.DataFrame):
        try:
            _write_local_frame_cache(metadata, frame)
        except Exception:
            pass
    return frame, metadata


def start_source_refresh_job(source: str) -> tuple[bool, str]:
    source_key = str(source or "").strip().lower()
    job_name = SOURCE_JOB_MAP.get(source_key)
    if not job_name:
        return False, f"Unknown source: {source}"

    resource_group = _resource_group()
    if not resource_group:
        return False, "Missing resource group. Set PIPELINE_RESOURCE_GROUP or infra/deployment.outputs.env."

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
        return False, f"Failed to run Azure CLI: {exc}"

    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "job start failed").strip()
        return False, message

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
