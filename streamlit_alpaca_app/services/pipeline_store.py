from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import os
import subprocess
from typing import Any

import pandas as pd


try:
    import psycopg
except Exception:
    psycopg = None

try:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
except Exception:
    DefaultAzureCredential = None
    BlobServiceClient = None


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
    "fundamentals": "equities-intraday-preload",
    "derivatives": "equities-intraday-preload",
}

SOURCE_DATASETS: dict[str, list[str]] = {
    "equities": ["daily_movers", "momentum_profiles", "price_history"],
    "fred": ["fred_summary", "fred_observations"],
    "commodities": ["commodity_regime_summary", "commodity_regime_history"],
    "options": ["option_expirations", "option_contract_snapshots"],
    "news": ["news_articles", "news_symbol_map"],
    "fundamentals": ["quarterly_fundamentals"],
    "derivatives": ["correlation_phase_shift_summary", "correlation_phase_shift_history", "technical_signals_latest", "technical_signal_history"],
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


def _get_env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _postgres_connection_string() -> str:
    return _get_env("POSTGRES_CONNECTION_STRING")


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
    return bool(_postgres_connection_string() and _storage_account_url())


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

    account_key = _get_env("AZURE_STORAGE_ACCOUNT_KEY")
    if account_key:
        try:
            return BlobServiceClient(account_url=account_url, credential=account_key)
        except Exception:
            pass

    if DefaultAzureCredential is None:
        return None
    try:
        credential = DefaultAzureCredential()
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


def latest_dataset_metadata(dataset_name: str) -> PipelineDataset | None:
    conn = _db_connect()
    if conn is None:
        return None
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


def load_latest_dataset_frame(dataset_name: str) -> tuple[pd.DataFrame, PipelineDataset | None]:
    metadata = latest_dataset_metadata(dataset_name)
    if metadata is None:
        return pd.DataFrame(), None
    frame = _read_blob_parquet(metadata.blob_path)
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
