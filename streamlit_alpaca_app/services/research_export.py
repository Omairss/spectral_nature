"""Async research export: query all retained research in a time window, build a
zip archive organised by date/ticker/provider, upload to blob storage, and
return a time-limited download link."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import os
import re
import secrets
import threading
import zipfile
from typing import Any

import pandas as pd

from .saa import storage as saa_storage
from .secrets import build_azure_credential


try:
    from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
except Exception:
    BlobServiceClient = None  # type: ignore[assignment,misc]
    generate_blob_sas = None  # type: ignore[assignment]
    BlobSasPermissions = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Job tracking (in-memory; survives within one process lifetime)
# ---------------------------------------------------------------------------

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()

_EXPORT_CONTAINER = "exports"
_SAS_EXPIRY_HOURS = 24


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.upper() in {"NAN", "<NA>", "NONE", "NULL"} else text


def _slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _coerce_text(text).lower()).strip("-")
    return slug[:max_len] if slug else "untitled"


def _safe_json_list(value: object) -> list[str]:
    raw = _coerce_text(value)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Job lifecycle
# ---------------------------------------------------------------------------


def create_export_job(
    *,
    start_date: str,
    end_date: str,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create a new export job and kick off the background build."""
    job_id = f"exp-{_now_utc().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    job: dict[str, Any] = {
        "job_id": job_id,
        "status": "building",
        "created_at": _now_utc().isoformat(),
        "created_by": created_by,
        "filters": {
            "start_date": start_date,
            "end_date": end_date,
        },
        "progress": {
            "documents_processed": 0,
            "documents_total": 0,
        },
        "download_url": None,
        "expires_at": None,
        "stats": None,
        "error": None,
    }
    with _jobs_lock:
        _jobs[job_id] = job

    thread = threading.Thread(
        target=_build_export,
        args=(job_id, start_date, end_date),
        daemon=True,
    )
    thread.start()
    return _job_summary(job)


def get_export_job(job_id: str) -> dict[str, Any] | None:
    """Return current status of an export job."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return None
    return _job_summary(job)


def _job_summary(job: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "job_id": job["job_id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "filters": job["filters"],
    }
    if job["status"] == "building":
        summary["progress"] = job["progress"]
    elif job["status"] == "ready":
        summary["download_url"] = job["download_url"]
        summary["expires_at"] = job["expires_at"]
        summary["stats"] = job["stats"]
    elif job["status"] == "failed":
        summary["error"] = job["error"]
    return summary


# ---------------------------------------------------------------------------
# Background build
# ---------------------------------------------------------------------------


def _build_export(job_id: str, start_date: str, end_date: str) -> None:
    """Run in a background thread: query data, build zip, upload, update job."""
    try:
        # 1. Query all documents in the time range
        documents_df = saa_storage.search_retained_documents(
            start_date=start_date,
            end_date=end_date,
            limit=5000,
        )

        total = len(documents_df)
        _update_job(job_id, progress={"documents_processed": 0, "documents_total": total})

        if documents_df.empty:
            _update_job(
                job_id,
                status="ready",
                download_url=None,
                expires_at=None,
                stats=_empty_stats(start_date, end_date),
            )
            return

        # 2. Query all evidence chunks in the time range (for summary matching)
        #    We don't include chunks in the export, but we use run_ids from docs
        #    to find matching summaries.

        # 3. Load bundle summaries
        summaries_by_ticker_date = _load_summaries_for_documents(documents_df)

        # 4. Build the zip in memory
        zip_buffer = io.BytesIO()
        manifest_documents: list[dict[str, Any]] = []
        manifest_summaries: list[dict[str, Any]] = []
        provider_counts: dict[str, int] = {}
        tickers_seen: set[str] = set()
        dates_seen: set[str] = set()
        written_summary_keys: set[str] = set()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            folder_root = f"export_{start_date}_to_{end_date}"

            for idx, row in documents_df.iterrows():
                doc = _document_dict(row)
                doc_id = doc["canonical_document_id"]

                # Fetch raw text from blob
                raw_doc = saa_storage.load_retained_document(doc_id)
                if isinstance(raw_doc, dict):
                    doc["raw_text"] = _coerce_text(raw_doc.get("raw_text"))
                else:
                    doc["raw_text"] = ""

                # Determine folder path
                pub_date = _coerce_text(doc.get("published_date"))
                if not pub_date:
                    ts = doc.get("published_at")
                    if ts and not pd.isna(pd.to_datetime(ts, errors="coerce")):
                        pub_date = pd.to_datetime(ts, errors="coerce").strftime("%Y-%m-%d")
                    else:
                        pub_date = "unknown-date"

                ticker = _coerce_text(doc.get("bundle_subject")).upper() or "_macro"
                provider = _coerce_text(doc.get("source_provider")).lower() or "unknown"
                title_slug = _slugify(doc.get("title", ""))
                short_id = _coerce_text(doc_id)[:8]
                filename = f"{title_slug}_{short_id}.json"

                rel_path = f"{folder_root}/{pub_date}/{ticker}/{provider}/{filename}"
                zf.writestr(rel_path, json.dumps(doc, indent=2, ensure_ascii=False, default=str))

                manifest_documents.append({
                    "path": rel_path.removeprefix(f"{folder_root}/"),
                    "canonical_document_id": doc_id,
                    "title": doc.get("title", ""),
                    "ticker": ticker,
                    "provider": provider,
                    "published_at": doc.get("published_at", ""),
                })

                provider_counts[provider] = provider_counts.get(provider, 0) + 1
                if ticker != "_macro":
                    tickers_seen.add(ticker)
                dates_seen.add(pub_date)

                # Write summary.json for this ticker+date if not already written
                summary_key = f"{pub_date}/{ticker}"
                if summary_key not in written_summary_keys:
                    summary = summaries_by_ticker_date.get(summary_key)
                    if summary:
                        summary_path = f"{folder_root}/{pub_date}/{ticker}/summary.json"
                        zf.writestr(summary_path, json.dumps(summary, indent=2, ensure_ascii=False, default=str))
                        manifest_summaries.append({
                            "path": summary_path.removeprefix(f"{folder_root}/"),
                            "bundle_id": summary.get("bundle_id", ""),
                            "ticker": ticker,
                            "date": pub_date,
                        })
                    written_summary_keys.add(summary_key)

                processed = int(idx) + 1 if isinstance(idx, int) else len(manifest_documents)
                if processed % 10 == 0 or processed == total:
                    _update_job(job_id, progress={"documents_processed": processed, "documents_total": total})

            # Write manifest
            manifest = {
                "export_id": job_id,
                "generated_at": _now_utc().isoformat(),
                "filters": {"start_date": start_date, "end_date": end_date},
                "stats": {
                    "total_documents": total,
                    "total_summaries": len(manifest_summaries),
                    "dates": sorted(dates_seen),
                    "tickers": sorted(tickers_seen),
                    "providers": provider_counts,
                },
                "documents": manifest_documents,
                "summaries": manifest_summaries,
            }
            zf.writestr(f"{folder_root}/manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False, default=str))

        # 5. Upload zip to blob storage
        zip_bytes = zip_buffer.getvalue()
        blob_name = f"research-exports/{job_id}.zip"
        download_url, expires_at = _upload_and_generate_sas(blob_name, zip_bytes)

        stats = manifest["stats"]
        stats["zip_size_bytes"] = len(zip_bytes)

        _update_job(
            job_id,
            status="ready",
            download_url=download_url,
            expires_at=expires_at,
            stats=stats,
        )

    except Exception as exc:
        _update_job(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")


def _update_job(job_id: str, **kwargs: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(kwargs)


def _empty_stats(start_date: str, end_date: str) -> dict[str, Any]:
    return {
        "total_documents": 0,
        "total_summaries": 0,
        "dates": [],
        "tickers": [],
        "providers": {},
        "zip_size_bytes": 0,
    }


# ---------------------------------------------------------------------------
# Document serialisation
# ---------------------------------------------------------------------------


def _document_dict(row: Any) -> dict[str, Any]:
    """Convert a DataFrame row from search_retained_documents into a clean dict."""
    return {
        "canonical_document_id": _coerce_text(row.get("canonical_document_id")),
        "canonical_url": _coerce_text(row.get("canonical_url")),
        "url_host": _coerce_text(row.get("url_host")),
        "title": _coerce_text(row.get("title")),
        "display_excerpt": _coerce_text(row.get("display_excerpt")),
        "search_text": _coerce_text(row.get("search_text")),
        "source_kind": _coerce_text(row.get("source_kind")),
        "source_provider": _coerce_text(row.get("source_provider")),
        "search_provider": _coerce_text(row.get("search_provider")),
        "bundle_subject": _coerce_text(row.get("bundle_subject")),
        "published_at": _format_timestamp(row.get("published_at")),
        "published_date": _coerce_text(row.get("published_date")),
        "mentioned_tickers": _safe_json_list(row.get("mentioned_tickers_json")),
        "mentioned_commodities": _safe_json_list(row.get("mentioned_commodities_json")),
        "event_tags": _safe_json_list(row.get("event_tags_json")),
        "mentioned_dates": _safe_json_list(row.get("mentioned_dates_json")),
        "last_run_id": _coerce_text(row.get("last_run_id")),
        "raw_text_chars": int(row.get("raw_text_chars") or 0),
        "raw_text_origin": _coerce_text(row.get("raw_text_origin")),
    }


def _format_timestamp(value: object) -> str:
    if value is None:
        return ""
    try:
        ts = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.notna(ts):
            return ts.isoformat()
    except Exception:
        pass
    return _coerce_text(value)


# ---------------------------------------------------------------------------
# Summary loading
# ---------------------------------------------------------------------------


def _load_summaries_for_documents(documents_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Load bundle summaries and index them by 'date/TICKER' for matching."""
    try:
        from .attention_materialized import deserialize_attention_research_bundles
        from .pipeline_store import PipelineStore

        store = PipelineStore.from_environment()
        frame = store.latest_frame("attention_bundle_snapshots")
        if frame is None or frame.empty:
            frame = store.latest_frame("attention_research_bundles")
        if frame is None or frame.empty:
            return {}

        bundles = deserialize_attention_research_bundles(frame)
    except Exception:
        return {}

    # Index by ticker
    bundles_by_ticker: dict[str, dict[str, Any]] = {}
    for bundle_id, payload in bundles.items():
        symbol = _coerce_text(payload.get("symbol")).upper()
        if symbol:
            bundles_by_ticker[symbol] = payload

    # Match: for each unique ticker+date in the documents, find the best summary
    result: dict[str, dict[str, Any]] = {}
    for _, row in documents_df.iterrows():
        ticker = _coerce_text(row.get("bundle_subject")).upper()
        if not ticker:
            continue
        pub_date = _coerce_text(row.get("published_date"))
        if not pub_date:
            continue
        key = f"{pub_date}/{ticker}"
        if key in result:
            continue
        bundle = bundles_by_ticker.get(ticker)
        if bundle:
            result[key] = bundle

    return result


# ---------------------------------------------------------------------------
# Blob upload + SAS URL generation
# ---------------------------------------------------------------------------


def _blob_service_client() -> Any | None:
    if BlobServiceClient is None:
        return None
    account_url = (os.getenv("AZURE_STORAGE_ACCOUNT_URL") or "").strip()
    if not account_url:
        return None
    credential = build_azure_credential()
    if credential is None:
        return None
    try:
        return BlobServiceClient(account_url=account_url, credential=credential)
    except Exception:
        return None


def _storage_account_name() -> str:
    url = (os.getenv("AZURE_STORAGE_ACCOUNT_URL") or "").strip()
    # Extract account name from https://{account}.blob.core.windows.net
    if ".blob." in url:
        return url.split("//")[1].split(".blob.")[0] if "//" in url else ""
    return ""


def _upload_and_generate_sas(blob_name: str, data: bytes) -> tuple[str | None, str | None]:
    """Upload zip to blob storage and return a SAS download URL + expiry."""
    client = _blob_service_client()
    if client is None:
        # Fallback: no blob storage available, job completes but no download URL
        return None, None

    container = _EXPORT_CONTAINER
    try:
        container_client = client.get_container_client(container)
        if not container_client.exists():
            container_client.create_container()
    except Exception:
        pass

    try:
        blob_client = client.get_blob_client(container=container, blob=blob_name)
        blob_client.upload_blob(data, overwrite=True, content_settings=_zip_content_settings())
    except Exception as exc:
        raise RuntimeError(f"Failed to upload export zip: {exc}") from exc

    # Generate SAS URL
    expiry = _now_utc() + timedelta(hours=_SAS_EXPIRY_HOURS)
    account_name = _storage_account_name()
    credential = build_azure_credential()

    if generate_blob_sas is not None and account_name and credential:
        try:
            # Try user delegation key first
            delegation_key = client.get_user_delegation_key(
                key_start_time=_now_utc() - timedelta(minutes=5),
                key_expiry_time=expiry,
            )
            sas_token = generate_blob_sas(
                account_name=account_name,
                container_name=container,
                blob_name=blob_name,
                user_delegation_key=delegation_key,
                permission=BlobSasPermissions(read=True),
                expiry=expiry,
            )
            download_url = f"{blob_client.url}?{sas_token}"
            return download_url, expiry.isoformat()
        except Exception:
            pass

        # Fallback: try account key SAS
        account_key = (os.getenv("AZURE_STORAGE_ACCOUNT_KEY") or "").strip()
        if account_key:
            try:
                sas_token = generate_blob_sas(
                    account_name=account_name,
                    container_name=container,
                    blob_name=blob_name,
                    account_key=account_key,
                    permission=BlobSasPermissions(read=True),
                    expiry=expiry,
                )
                download_url = f"{blob_client.url}?{sas_token}"
                return download_url, expiry.isoformat()
            except Exception:
                pass

    # Uploaded but could not generate SAS — return the blob URL without SAS
    return blob_client.url, expiry.isoformat()


def _zip_content_settings() -> Any:
    try:
        from azure.storage.blob import ContentSettings
        return ContentSettings(content_type="application/zip")
    except Exception:
        return None
