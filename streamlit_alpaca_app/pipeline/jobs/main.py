from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
import hashlib
import json
import os
import uuid
from typing import Any

import pandas as pd

from compute.anomalies import (
    AttentionConfig,
    ExpectationConfig,
    build_attention_candidates,
    build_attention_feed,
    build_attention_rollups,
    build_commodity_peer_group_membership,
    build_peer_group_membership,
    build_price_expectations,
    build_taxonomy_peer_group_catalog,
    filter_attention_events,
    normalize_horizons,
)
from compute.portfolio import build_portfolio_timeseries
from services.alpaca_api import AlpacaAPI, AlpacaAPIError
from pipeline.jobs.attention_home_build import run_attention_home_build as run_attention_home_build_job
from services.attention_home_1d import resolve_macro_anchor_symbols
from services.attention_context_llm import (
    build_attention_context_narratives,
    build_edgar_evidence,
    merge_attention_context_with_llm,
)
from services.config import AppConfig
from services.edgar import DEFAULT_EDGAR_FORMS, EdgarAPIError, EdgarClient, build_attention_context_bundle
from services.entity_taxonomy import (
    bootstrap_entity_taxonomy_tables,
    build_entity_taxonomy_snapshot,
    deactivate_missing_taxonomy_symbols,
    fetch_entity_taxonomy_frame,
    upsert_entity_taxonomy_frame,
)
from services.fred import FredAPIError, load_fred_api_key, load_fred_dashboard
from services.llm import LLMAPIError, load_llm_client
from services.market import (
    build_correlation_phase_shifts_from_bars,
    build_momentum_profiles_from_bars,
    default_commodity_universe_symbols,
    scan_commodity_regimes,
    scan_daily_movers,
    scan_momentum_profiles,
)
from services.options import build_option_snapshot_surface, load_option_chain
from services.pipeline_store import load_latest_dataset_frame
from services.secrets import resolve_secret_value
from services.simfin_refresh import build_quarterly_fundamentals_frame, simfin_refresh_configured
from services.treasury_yields import TreasuryYieldError, load_treasury_yield_datasets
from services.universe import build_liquidity_ranked_equity_universe, load_us_equity_listings

try:
    from services.signals import build_signal_frame, summarize_signal_frame
except Exception:
    build_signal_frame = None
    summarize_signal_frame = None


try:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
except Exception:
    DefaultAzureCredential = None
    BlobServiceClient = None

try:
    import psycopg
except Exception:
    psycopg = None


@dataclass(frozen=True)
class JobContext:
    name: str
    run_id: str
    asof: datetime
    universe_version: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str, indent=2)


def _dataset_version_id(dataset_name: str, ctx: JobContext) -> str:
    return f"{dataset_name}__{ctx.asof.strftime('%Y%m%dT%H%M%SZ')}__{ctx.run_id[:8]}"


def _parameter_hash(ctx: JobContext) -> str:
    payload = {
        "job_name": ctx.name,
        "universe_version": ctx.universe_version,
        "universe_symbols": (os.getenv("UNIVERSE_SYMBOLS") or "").strip(),
        "equity_universe_target_size": (os.getenv("EQUITY_UNIVERSE_TARGET_SIZE") or "1000").strip(),
        "equity_universe_include_etfs": (os.getenv("EQUITY_UNIVERSE_INCLUDE_ETFS") or "false").strip(),
        "equity_universe_include_non_common": (os.getenv("EQUITY_UNIVERSE_INCLUDE_NON_COMMON") or "false").strip(),
        "equity_price_lookback_days": (os.getenv("EQUITY_PRICE_LOOKBACK_DAYS") or "3650").strip(),
        "equity_price_incremental_lookback_days": (os.getenv("EQUITY_PRICE_INCREMENTAL_LOOKBACK_DAYS") or "45").strip(),
        "equity_price_full_refresh_hours": (os.getenv("EQUITY_PRICE_FULL_REFRESH_HOURS") or "168").strip(),
        "momentum_lookback_days": (os.getenv("MOMENTUM_LOOKBACK_DAYS") or "3650").strip(),
        "phase_shift_days": (os.getenv("PHASE_SHIFT_DAYS") or "365").strip(),
        "phase_shift_benchmark": (os.getenv("PHASE_SHIFT_BENCHMARK") or "SPY").strip(),
        "fred_lookback_years": (os.getenv("FRED_LOOKBACK_YEARS") or "10").strip(),
        "treasury_yield_lookback_years": (os.getenv("TREASURY_YIELD_LOOKBACK_YEARS") or "3").strip(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _code_version() -> str:
    return (
        (os.getenv("CODE_VERSION") or "").strip()
        or (os.getenv("IMAGE_TAG") or "").strip()
        or (os.getenv("GIT_SHA") or "").strip()
        or "unknown"
    )


def _db_connection() -> Any | None:
    conn_str = resolve_secret_value(
        ["POSTGRES_CONNECTION_STRING"],
        secret_name_env="POSTGRES_CONNECTION_STRING_SECRET",
        default_secret_name="postgres-connection-string",
    )
    if not conn_str or psycopg is None:
        return None
    try:
        return psycopg.connect(conn_str)
    except Exception as exc:
        print(f"[warn] postgres connection unavailable; continuing without db sink: {exc}")
        return None


def _db_bootstrap(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dataset_versions (
                dataset_version_id TEXT PRIMARY KEY,
                dataset_name TEXT NOT NULL,
                universe_version TEXT,
                parameter_hash TEXT,
                asof_time_utc TIMESTAMPTZ NOT NULL,
                ingested_at_utc TIMESTAMPTZ NOT NULL,
                blob_path TEXT NOT NULL,
                row_count BIGINT NOT NULL,
                checksum TEXT,
                code_version TEXT,
                schema_version TEXT,
                status TEXT NOT NULL DEFAULT 'ready',
                run_id TEXT NOT NULL,
                schema_columns JSONB
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dataset_versions_name_asof
            ON dataset_versions (dataset_name, asof_time_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dataset_versions_lookup
            ON dataset_versions (dataset_name, universe_version, parameter_hash, asof_time_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS job_runs (
                run_id TEXT PRIMARY KEY,
                job_name TEXT NOT NULL,
                schedule_slot TEXT,
                start_time_utc TIMESTAMPTZ NOT NULL,
                end_time_utc TIMESTAMPTZ,
                status TEXT NOT NULL,
                retries INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT,
                progress_stage TEXT,
                progress_message TEXT,
                progress_pct DOUBLE PRECISION,
                heartbeat_time_utc TIMESTAMPTZ,
                universe_version TEXT,
                asof_time_utc TIMESTAMPTZ
            )
            """
        )
        cur.execute(
            """
            ALTER TABLE job_runs
            ADD COLUMN IF NOT EXISTS progress_stage TEXT
            """
        )
        cur.execute(
            """
            ALTER TABLE job_runs
            ADD COLUMN IF NOT EXISTS progress_message TEXT
            """
        )
        cur.execute(
            """
            ALTER TABLE job_runs
            ADD COLUMN IF NOT EXISTS progress_pct DOUBLE PRECISION
            """
        )
        cur.execute(
            """
            ALTER TABLE job_runs
            ADD COLUMN IF NOT EXISTS heartbeat_time_utc TIMESTAMPTZ
            """
        )
    conn.commit()
    bootstrap_entity_taxonomy_tables(conn)


def _db_mark_job_start(conn: Any, ctx: JobContext) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO job_runs (
                run_id, job_name, schedule_slot, start_time_utc,
                status, retries, progress_stage, progress_message, progress_pct,
                heartbeat_time_utc, universe_version, asof_time_utc
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                job_name = EXCLUDED.job_name,
                schedule_slot = EXCLUDED.schedule_slot,
                start_time_utc = EXCLUDED.start_time_utc,
                status = EXCLUDED.status,
                retries = EXCLUDED.retries,
                progress_stage = EXCLUDED.progress_stage,
                progress_message = EXCLUDED.progress_message,
                progress_pct = EXCLUDED.progress_pct,
                heartbeat_time_utc = EXCLUDED.heartbeat_time_utc,
                universe_version = EXCLUDED.universe_version,
                asof_time_utc = EXCLUDED.asof_time_utc
            """,
            (
                ctx.run_id,
                ctx.name,
                (os.getenv("SCHEDULE_SLOT") or "").strip() or None,
                _utc_now(),
                "Running",
                0,
                "starting",
                "Job started.",
                0.0,
                _utc_now(),
                ctx.universe_version,
                ctx.asof,
            ),
        )
    conn.commit()


def _db_mark_job_end(conn: Any, ctx: JobContext, status: str, error_summary: str | None = None) -> None:
    normalized = _normalize_job_status(status)
    stage = "completed" if normalized == "Succeeded" else "failed"
    progress_pct = 100.0 if normalized == "Succeeded" else None
    progress_message = "Completed successfully." if normalized == "Succeeded" else (error_summary or "Job failed.")
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE job_runs
            SET end_time_utc = %s,
                status = %s,
                error_summary = %s,
                progress_stage = %s,
                progress_message = %s,
                progress_pct = COALESCE(%s, progress_pct),
                heartbeat_time_utc = %s
            WHERE run_id = %s
            """,
            (_utc_now(), normalized, error_summary, stage, progress_message, progress_pct, _utc_now(), ctx.run_id),
        )
    conn.commit()


def _normalize_job_status(status: object) -> str:
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
    return str(status).strip()


def _db_update_job_progress(
    conn: Any,
    ctx: JobContext,
    *,
    stage: str,
    message: str,
    progress_pct: float | None = None,
    status: str = "Running",
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE job_runs
            SET status = %s,
                progress_stage = %s,
                progress_message = %s,
                progress_pct = COALESCE(%s, progress_pct),
                heartbeat_time_utc = %s
            WHERE run_id = %s
            """,
            (_normalize_job_status(status), stage, message[:4000], progress_pct, _utc_now(), ctx.run_id),
        )
    conn.commit()


def _job_progress(
    ctx: JobContext,
    conn: Any | None,
    *,
    stage: str,
    message: str,
    progress_pct: float | None = None,
    status: str = "Running",
) -> None:
    suffix = f" progress={progress_pct:.1f}%" if isinstance(progress_pct, (int, float)) else ""
    print(f"[info] job_progress stage={stage}{suffix} message={message}")
    if conn is None:
        return
    try:
        _db_update_job_progress(
            conn,
            ctx,
            stage=stage,
            message=message,
            progress_pct=progress_pct,
            status=status,
        )
    except Exception as exc:
        print(f"[warn] failed to record job progress stage={stage}: {exc}")


def _db_upsert_dataset_version(conn: Any, manifest: dict, ctx: JobContext) -> None:
    checksum = hashlib.sha256(
        f"{manifest.get('blob_path','')}|{manifest.get('row_count',0)}|{manifest.get('asof_time_utc','')}".encode("utf-8")
    ).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dataset_versions (
                dataset_version_id, dataset_name, universe_version, parameter_hash,
                asof_time_utc, ingested_at_utc, blob_path, row_count,
                checksum, code_version, schema_version, status, run_id, schema_columns
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s::jsonb
            )
            ON CONFLICT (dataset_version_id) DO UPDATE SET
                dataset_name = EXCLUDED.dataset_name,
                universe_version = EXCLUDED.universe_version,
                parameter_hash = EXCLUDED.parameter_hash,
                asof_time_utc = EXCLUDED.asof_time_utc,
                ingested_at_utc = EXCLUDED.ingested_at_utc,
                blob_path = EXCLUDED.blob_path,
                row_count = EXCLUDED.row_count,
                checksum = EXCLUDED.checksum,
                code_version = EXCLUDED.code_version,
                schema_version = EXCLUDED.schema_version,
                status = EXCLUDED.status,
                run_id = EXCLUDED.run_id,
                schema_columns = EXCLUDED.schema_columns
            """,
            (
                manifest["dataset_version_id"],
                manifest["dataset_name"],
                manifest.get("universe_version"),
                _parameter_hash(ctx),
                manifest["asof_time_utc"],
                manifest["ingested_at_utc"],
                manifest["blob_path"],
                int(manifest.get("row_count", 0)),
                checksum,
                _code_version(),
                "v1",
                "ready",
                manifest.get("run_id") or ctx.run_id,
                json.dumps(manifest.get("schema_columns", [])),
            ),
        )
    conn.commit()


def _db_dataset_is_fresh(conn: Any, dataset_name: str, max_age_hours: float) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(ingested_at_utc)
                FROM dataset_versions
                WHERE dataset_name = %s AND status = 'ready'
                """,
                (dataset_name,),
            )
            row = cur.fetchone()
    except Exception:
        return False

    latest = row[0] if row else None
    if latest is None:
        return False
    age_hours = (_utc_now() - latest).total_seconds() / 3600.0
    return age_hours < max_age_hours


def _blob_client() -> Any | None:
    account_url = (os.getenv("AZURE_STORAGE_ACCOUNT_URL") or "").strip()
    if not account_url or DefaultAzureCredential is None or BlobServiceClient is None:
        return None
    credential = DefaultAzureCredential()
    return BlobServiceClient(account_url=account_url, credential=credential)


def _upload_bytes(path: str, payload: bytes, content_type: str) -> None:
    container = (os.getenv("AZURE_STORAGE_CONTAINER") or "datasets").strip() or "datasets"
    client = _blob_client()
    if client is None:
        print(f"[warn] blob client unavailable; skip upload path={path}")
        return
    blob = client.get_blob_client(container=container, blob=path)
    blob.upload_blob(payload, overwrite=True, content_type=content_type)
    print(f"[info] uploaded blob://{container}/{path} bytes={len(payload)}")


def _upload_frame(dataset_name: str, frame: pd.DataFrame, ctx: JobContext) -> str:
    asof_slug = ctx.asof.strftime("%Y-%m-%dT%H-%M-%SZ")
    dt_slug = ctx.asof.strftime("%Y-%m-%d")
    path = (
        f"datasets/{dataset_name}/dt={dt_slug}/asof={asof_slug}/"
        f"universe={ctx.universe_version}/part-{ctx.run_id[:8]}.parquet"
    )
    buffer = BytesIO()
    frame.to_parquet(buffer, index=False)
    _upload_bytes(path, buffer.getvalue(), "application/octet-stream")
    return path


def _upload_manifest(dataset_name: str, path: str, frame: pd.DataFrame, ctx: JobContext) -> dict | None:
    if not path:
        return None
    dataset_version_id = _dataset_version_id(dataset_name, ctx)
    manifest = {
        "dataset_version_id": dataset_version_id,
        "dataset_name": dataset_name,
        "run_id": ctx.run_id,
        "asof_time_utc": ctx.asof.isoformat(),
        "ingested_at_utc": _utc_now().isoformat(),
        "universe_version": ctx.universe_version,
        "blob_path": path,
        "row_count": int(len(frame)),
        "schema_columns": list(frame.columns),
    }
    manifest_path = f"manifests/{dataset_name}/{dataset_version_id}.json"
    _upload_bytes(manifest_path, _to_json(manifest).encode("utf-8"), "application/json")
    return manifest


def _persist_dataset(dataset_name: str, frame: pd.DataFrame, ctx: JobContext, conn: Any | None) -> None:
    path = _upload_frame(dataset_name, frame, ctx)
    manifest = _upload_manifest(dataset_name, path, frame, ctx)
    if manifest and conn is not None:
        _db_upsert_dataset_version(conn, manifest, ctx)
    _job_progress(
        ctx,
        conn,
        stage="persist_dataset",
        message=f"Persisted dataset `{dataset_name}` rows={len(frame)}.",
    )


def _alpaca_config() -> AppConfig | None:
    key = resolve_secret_value(
        ["APCA_API_KEY", "APCA_API_KEY_ID"],
        secret_name_env="APCA_API_KEY_SECRET",
        default_secret_name="apca-api-key",
    )
    secret = resolve_secret_value(
        ["APCA_API_SECRET_KEY"],
        secret_name_env="APCA_API_SECRET_KEY_SECRET",
        default_secret_name="apca-api-secret-key",
    )
    if not key or not secret:
        return None
    return AppConfig(
        alpaca_api_key=key,
        alpaca_secret_key=secret,
        alpaca_trading_base_url=(os.getenv("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets").strip(),
        alpaca_data_base_url=(os.getenv("ALPACA_DATA_BASE_URL") or "https://data.alpaca.markets").strip(),
    )


def _symbols_from_env() -> list[str]:
    raw = (os.getenv("UNIVERSE_SYMBOLS") or "").strip()
    if raw:
        return [item.strip().upper() for item in raw.split(",") if item.strip()]
    return []


def _edgar_forms_from_env() -> list[str]:
    raw = (os.getenv("EDGAR_FORMS") or "").strip()
    if raw:
        forms = [item.strip().upper() for item in raw.split(",") if item.strip()]
        return forms or list(DEFAULT_EDGAR_FORMS)
    return list(DEFAULT_EDGAR_FORMS)


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or ("true" if default else "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _parse_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = int(default)
    return max(value, minimum)


def _parse_float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = float(default)
    return max(value, minimum)


def _equity_universe_target_size(default: int = 1000) -> int:
    return _parse_int_env("EQUITY_UNIVERSE_TARGET_SIZE", default, minimum=1)


def _symbols_from_snapshot(frame: pd.DataFrame, *, limit: int | None = None) -> list[str]:
    if frame.empty or "symbol" not in frame.columns:
        return []
    symbols = [
        str(symbol).upper().strip()
        for symbol in frame["symbol"].tolist()
        if str(symbol).strip()
    ]
    deduped = list(dict.fromkeys(symbols))
    if limit is not None and limit > 0:
        return deduped[:limit]
    return deduped


def _load_latest_equity_universe_snapshot(target_size: int) -> pd.DataFrame:
    max_age_hours = _parse_float_env("EQUITY_UNIVERSE_MAX_AGE_HOURS", 24.0, minimum=0.0)
    try:
        frame, metadata = load_latest_dataset_frame("universe_snapshot")
    except Exception as exc:
        print(f"[warn] failed to load latest universe_snapshot: {type(exc).__name__}: {exc}")
        return pd.DataFrame()
    if frame.empty or "symbol" not in frame.columns:
        return pd.DataFrame()

    snapshot_symbols = _symbols_from_snapshot(frame)
    minimum_acceptable_size = _parse_int_env(
        "EQUITY_UNIVERSE_MIN_ACCEPTABLE_SIZE",
        max(int(target_size * 0.5), 100),
        minimum=1,
    )
    if len(snapshot_symbols) < minimum_acceptable_size:
        print(
            f"[info] latest universe_snapshot is too small for target_size={target_size}: "
            f"{len(snapshot_symbols)} symbol(s) < {minimum_acceptable_size}"
        )
        return pd.DataFrame()

    if metadata is not None and max_age_hours > 0:
        asof = pd.to_datetime(getattr(metadata, "asof_time_utc", None), utc=True, errors="coerce")
        if pd.notna(asof):
            age_hours = max((pd.Timestamp(_utc_now()) - asof).total_seconds() / 3600.0, 0.0)
            if age_hours > max_age_hours:
                print(
                    f"[info] latest universe_snapshot is stale for target_size={target_size}: "
                    f"age_hours={age_hours:.1f} > max_age_hours={max_age_hours:.1f}"
                )
                return pd.DataFrame()
    return frame.copy()


def _build_equity_universe_snapshot(api: AlpacaAPI, *, target_size: int) -> pd.DataFrame:
    return build_liquidity_ranked_equity_universe(
        api,
        target_size=target_size,
        include_etfs=_parse_bool_env("EQUITY_UNIVERSE_INCLUDE_ETFS", False),
        include_non_common=_parse_bool_env("EQUITY_UNIVERSE_INCLUDE_NON_COMMON", False),
        min_price=_parse_float_env("EQUITY_UNIVERSE_MIN_PRICE", 5.0, minimum=0.0),
        min_volume=_parse_float_env("EQUITY_UNIVERSE_MIN_VOLUME", 100_000.0, minimum=0.0),
        min_dollar_volume=_parse_float_env("EQUITY_UNIVERSE_MIN_DOLLAR_VOLUME", 5_000_000.0, minimum=0.0),
        feed=(os.getenv("EQUITY_UNIVERSE_FEED") or "iex").strip() or "iex",
    )


def _resolve_equity_symbols(api: AlpacaAPI, ctx: JobContext, conn: Any | None = None) -> list[str]:
    explicit = _symbols_from_env()
    if (os.getenv("UNIVERSE_SYMBOLS") or "").strip():
        print(f"[info] using explicit UNIVERSE_SYMBOLS override with {len(explicit)} symbol(s)")
        return explicit

    target_size = _equity_universe_target_size()
    snapshot = _load_latest_equity_universe_snapshot(target_size)
    if not snapshot.empty:
        symbols = _symbols_from_snapshot(snapshot, limit=target_size)
        if symbols:
            print(f"[info] using universe_snapshot with {len(symbols)} symbol(s)")
            return symbols

    try:
        rebuilt = _build_equity_universe_snapshot(api, target_size=target_size)
    except Exception as exc:
        print(f"[warn] failed to build expanded equity universe: {type(exc).__name__}: {exc}")
        rebuilt = pd.DataFrame()

    rebuilt_symbols = _symbols_from_snapshot(rebuilt, limit=target_size)
    if rebuilt_symbols:
        print(f"[info] rebuilt equity universe inline with {len(rebuilt_symbols)} symbol(s)")
        try:
            _persist_dataset("universe_snapshot", rebuilt, ctx, conn)
        except Exception as exc:
            print(f"[warn] failed to persist rebuilt universe_snapshot: {type(exc).__name__}: {exc}")
        return rebuilt_symbols

    raise RuntimeError("unable to resolve any equity universe symbols from explicit env, snapshot, or live rebuild")


def _symbols_from_latest_universe(limit: int) -> list[str]:
    target_limit = max(int(limit), 1)
    snapshot = _load_latest_equity_universe_snapshot(target_limit)
    return _symbols_from_snapshot(snapshot, limit=target_limit) if not snapshot.empty else []


def _news_symbol_map_from_frame(news: pd.DataFrame) -> pd.DataFrame:
    if news.empty:
        return pd.DataFrame(columns=["headline", "published_at", "source", "url", "symbols"])
    if "symbols" in news.columns:
        mapped = news[[col for col in ["headline", "published_at", "source", "url", "symbols"] if col in news.columns]].copy()
        mapped["symbols"] = mapped["symbols"].apply(lambda x: ",".join(x) if isinstance(x, list) else str(x))
        return mapped
    return news.copy()


def _load_attention_news_map(api: AlpacaAPI, symbols: list[str]) -> pd.DataFrame:
    try:
        frame, _ = load_latest_dataset_frame("news_symbol_map")
    except Exception as exc:
        print(f"[warn] failed to load latest news_symbol_map snapshot: {type(exc).__name__}: {exc}")
        frame = pd.DataFrame()

    if not frame.empty:
        print(f"[info] using materialized news_symbol_map rows={len(frame)}")
        return frame

    try:
        live_news = api.get_news(symbols=symbols, limit=50)
        mapped = _news_symbol_map_from_frame(live_news)
        if not mapped.empty:
            print(f"[info] using live news fallback rows={len(mapped)}")
        return mapped
    except Exception as exc:
        print(f"[warn] live news fallback unavailable: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["headline", "published_at", "source", "url", "symbols"])


def _load_latest_attention_seed(limit: int) -> pd.DataFrame:
    target_limit = max(int(limit), 1)
    for dataset_name in ("attention_candidates", "attention_feed"):
        try:
            frame, _ = load_latest_dataset_frame(dataset_name)
        except Exception as exc:
            print(f"[warn] failed to load {dataset_name} for attention context: {type(exc).__name__}: {exc}")
            continue
        if frame.empty or "entity_id" not in frame.columns:
            continue
        out = frame.copy()
        if "entity_type" in out.columns:
            entity_type = out["entity_type"].astype(str).str.lower()
            out = out[entity_type.eq("symbol") | entity_type.eq("")].copy()
        out["entity_id"] = out["entity_id"].astype(str).str.upper().str.strip()
        out = out[out["entity_id"].ne("")].copy()
        if out.empty:
            continue
        if "attention_score" in out.columns:
            out["attention_score"] = pd.to_numeric(out["attention_score"], errors="coerce")
            out = out.sort_values("attention_score", ascending=False, na_position="last")
        return out.head(target_limit).reset_index(drop=True)

    fallback_symbols = _symbols_from_latest_universe(target_limit)
    if not fallback_symbols:
        return pd.DataFrame(columns=["entity_id"])
    return pd.DataFrame({"entity_id": fallback_symbols})


def _load_latest_materialized_frame(dataset_name: str) -> pd.DataFrame:
    try:
        frame, _ = load_latest_dataset_frame(dataset_name)
    except Exception:
        frame = pd.DataFrame()
    return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def _load_attention_positions(api: AlpacaAPI) -> pd.DataFrame:
    try:
        positions = api.get_positions()
        if not positions.empty:
            print(f"[info] loaded positions for attention overlay rows={len(positions)}")
        return positions
    except Exception as exc:
        print(f"[warn] positions unavailable for attention overlay: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["symbol", "market_value"])


def _build_portfolio_timeseries_snapshot(api: AlpacaAPI, *, period: str = "5Y") -> pd.DataFrame:
    try:
        frame = build_portfolio_timeseries(api, period)
        if not frame.empty:
            print(f"[info] built portfolio_timeseries_snapshot rows={len(frame)} period={period}")
        return frame
    except Exception as exc:
        print(f"[warn] portfolio history unavailable for snapshot: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["timestamp", "portfolio"])


def _normalize_symbol(value: object) -> str:
    text = str(value or "").upper().strip()
    return "" if not text or text == "NAN" else text


def _parse_attention_horizons_env() -> tuple[str, ...]:
    raw = (os.getenv("ATTENTION_HORIZONS") or "1d,1w,1mo,3mo,1yr").strip()
    parsed = normalize_horizons([token.strip() for token in raw.split(",") if token.strip()])
    return parsed or normalize_horizons(["1d", "1w", "1mo", "3mo", "1yr"])


def _parse_attention_thresholds_env() -> dict[str, float]:
    raw = (os.getenv("ATTENTION_RESIDUAL_ZSCORE_THRESHOLDS") or "").strip()
    if not raw:
        return {}

    parsed: dict[str, float] = {}
    for token in raw.split(","):
        item = str(token or "").strip()
        if ":" not in item:
            continue
        horizon, value = item.split(":", 1)
        normalized = normalize_horizons([horizon])
        if not normalized:
            continue
        try:
            parsed[normalized[0]] = max(float(value), 0.0)
        except Exception:
            continue
    return parsed


def _load_stock_bars_frame(api: AlpacaAPI, symbols: list[str], *, days: int) -> pd.DataFrame:
    normalized_symbols = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
    if not normalized_symbols:
        return pd.DataFrame()

    end = _utc_now()
    start = end - timedelta(days=max(int(days), 30))
    bars = api.get_stock_bars(normalized_symbols, start=start, end=end, timeframe="1Day", feed="iex")
    parts: list[pd.DataFrame] = []
    for symbol, frame in bars.items():
        if frame.empty:
            continue
        chunk = frame.copy()
        chunk["symbol"] = str(symbol).upper().strip()
        keep = [col for col in ["symbol", "timestamp", "open", "high", "low", "close", "volume"] if col in chunk.columns]
        parts.append(chunk[keep])
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _prepare_price_history_frame(
    frame: pd.DataFrame,
    *,
    allowed_symbols: set[str] | None = None,
) -> pd.DataFrame:
    columns = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)

    out = frame.copy()
    if "symbol" not in out.columns:
        out["symbol"] = ""
    out["symbol"] = out["symbol"].apply(AlpacaAPI._normalize_symbol)
    if allowed_symbols is not None:
        out = out[out["symbol"].isin(allowed_symbols)].copy()

    if "timestamp" not in out.columns:
        out["timestamp"] = pd.NaT
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    for col in columns[2:]:
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out[columns].dropna(subset=["symbol", "timestamp", "close"])
    if out.empty:
        return pd.DataFrame(columns=columns)

    out = out.sort_values(["symbol", "timestamp"]).drop_duplicates(subset=["symbol", "timestamp"], keep="last")
    return out.reset_index(drop=True)


def _bars_to_price_history_frame(bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for symbol, frame in bars.items():
        if frame is None or frame.empty:
            continue
        chunk = frame.copy()
        chunk["symbol"] = AlpacaAPI._normalize_symbol(symbol)
        parts.append(chunk)
    if not parts:
        return _prepare_price_history_frame(pd.DataFrame())
    return _prepare_price_history_frame(pd.concat(parts, ignore_index=True))


def _price_history_frame_to_bars(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    prepared = _prepare_price_history_frame(frame)
    if prepared.empty:
        return {}

    bars: dict[str, pd.DataFrame] = {}
    for symbol, chunk in prepared.groupby("symbol", sort=True):
        bars[str(symbol)] = chunk.drop(columns=["symbol"]).reset_index(drop=True)
    return bars


def _build_equity_price_history_snapshot(
    api: AlpacaAPI,
    symbols: list[str],
    *,
    benchmark: str,
    history_days: int,
    incremental_lookback_days: int,
    full_refresh_hours: float,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    required_symbols = sorted(
        {
            AlpacaAPI._normalize_symbol(symbol)
            for symbol in [*symbols, benchmark]
            if str(symbol).strip()
        }
    )
    if not required_symbols:
        return {}, _prepare_price_history_frame(pd.DataFrame())

    end = _utc_now()
    history_days = max(int(history_days), 1)
    incremental_lookback_days = max(int(incremental_lookback_days), 5)
    history_cutoff = pd.Timestamp(end) - pd.Timedelta(days=history_days)
    full_start = end - timedelta(days=history_days)
    incremental_start = end - timedelta(days=incremental_lookback_days)

    try:
        latest_frame, latest_metadata = load_latest_dataset_frame("price_history")
    except Exception as exc:
        print(f"[warn] failed to load latest price_history snapshot: {type(exc).__name__}: {exc}")
        latest_frame, latest_metadata = pd.DataFrame(), None

    existing = _prepare_price_history_frame(latest_frame, allowed_symbols=set(required_symbols))
    coverage_view = existing.copy()
    if not existing.empty:
        existing = existing[existing["timestamp"] >= history_cutoff].reset_index(drop=True)

    full_refresh_due = False
    if latest_metadata is not None and full_refresh_hours > 0:
        latest_asof = pd.to_datetime(getattr(latest_metadata, "asof_time_utc", None), utc=True, errors="coerce")
        if pd.notna(latest_asof):
            age_hours = max((pd.Timestamp(end) - latest_asof).total_seconds() / 3600.0, 0.0)
            full_refresh_due = age_hours >= float(full_refresh_hours)
            if full_refresh_due:
                print(
                    "[info] price_history full refresh triggered: "
                    f"age_hours={age_hours:.1f} >= full_refresh_hours={full_refresh_hours:.1f}"
                )

    try:
        history_tolerance_days = max(int(os.getenv("EQUITY_PRICE_HISTORY_TOLERANCE_DAYS", "7")), 0)
    except Exception:
        history_tolerance_days = 7
    coverage_cutoff = history_cutoff + pd.Timedelta(days=history_tolerance_days)

    if full_refresh_due or existing.empty:
        full_history_symbols = required_symbols
    else:
        earliest_by_symbol = coverage_view.groupby("symbol")["timestamp"].min()
        full_history_symbols = [
            symbol
            for symbol in required_symbols
            if symbol not in earliest_by_symbol.index or earliest_by_symbol[symbol] > coverage_cutoff
        ]

    if full_history_symbols:
        print(f"[info] price_history full-history fetch for {len(full_history_symbols)} symbol(s)")
        full_history_bars = api.get_stock_bars(
            full_history_symbols,
            start=full_start,
            end=end,
            timeframe="1Day",
            feed="iex",
        )
    else:
        full_history_bars = {}

    incremental_bars = api.get_stock_bars(
        required_symbols,
        start=incremental_start,
        end=end,
        timeframe="1Day",
        feed="iex",
    )

    merged_parts = [part for part in [existing, _bars_to_price_history_frame(full_history_bars), _bars_to_price_history_frame(incremental_bars)] if not part.empty]
    merged = _prepare_price_history_frame(pd.concat(merged_parts, ignore_index=True) if merged_parts else pd.DataFrame())
    if not merged.empty:
        merged = merged[merged["timestamp"] >= history_cutoff].reset_index(drop=True)

    bars = _price_history_frame_to_bars(merged)
    print(
        "[info] price_history snapshot ready: "
        f"symbols={len(bars)} rows={len(merged)} incremental_lookback_days={incremental_lookback_days} history_days={history_days}"
    )
    return bars, merged


def _build_quarterly_fundamentals_snapshot(symbols: list[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    refresh_days = max(int(os.getenv("SIMFIN_REFRESH_DAYS", "1")), 0)
    prefer_upstream = str(os.getenv("SIMFIN_REFRESH_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"}
    if prefer_upstream and not simfin_refresh_configured():
        print("[info] fundamentals refresh falling back to local SimFin files: SIMFIN_API_KEY not configured")
    fundamentals, details = build_quarterly_fundamentals_frame(
        symbols,
        prefer_upstream=prefer_upstream,
        refresh_days=refresh_days,
    )
    return fundamentals, details


def _build_treasury_yield_snapshots(*, asof_time_utc: datetime | pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    years = max(int(os.getenv("TREASURY_YIELD_LOOKBACK_YEARS", "3")), 1)
    payload = load_treasury_yield_datasets(years=years, end_date=asof_time_utc)
    return (
        payload.get("yield_curve_observations", pd.DataFrame()),
        payload.get("yield_curve_summary", pd.DataFrame()),
        payload.get("yield_curve_facts_1d", pd.DataFrame()),
    )


def _primary_peer_group_snapshot(
    peer_group_membership: pd.DataFrame,
    *,
    fallback_names: set[str],
) -> pd.DataFrame:
    required = {"entity_id", "peer_group_id", "peer_group_name"}
    if peer_group_membership.empty or not required.issubset(set(peer_group_membership.columns)):
        return pd.DataFrame(columns=["entity_id", "peer_group_id", "peer_group_name", "benchmark"])

    frame = peer_group_membership.copy()
    frame["entity_id"] = frame["entity_id"].astype(str).str.upper().str.strip()
    frame["peer_group_name"] = frame["peer_group_name"].astype(str)
    frame["_fallback_rank"] = frame["peer_group_name"].isin(fallback_names).astype(int)
    frame = frame.sort_values(["entity_id", "_fallback_rank", "peer_group_name"]).drop_duplicates(subset=["entity_id"], keep="first")
    keep = [col for col in ["entity_id", "peer_group_id", "peer_group_name", "benchmark"] if col in frame.columns]
    return frame[keep].reset_index(drop=True)


def _commodity_regime_signals(summary: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "commodity_regime"}
    if summary.empty or not required.issubset(set(summary.columns)):
        return pd.DataFrame(columns=["symbol", "regime"])
    frame = summary[["symbol", "commodity_regime"]].copy()
    frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
    frame = frame.rename(columns={"commodity_regime": "regime"})
    return frame.drop_duplicates(subset=["symbol"], keep="last").reset_index(drop=True)


def _decorate_commodity_anomaly_events(anomaly_events: pd.DataFrame) -> pd.DataFrame:
    if anomaly_events.empty:
        return anomaly_events

    out = anomaly_events.copy()
    out["entity_type"] = "commodity_symbol"
    out["parent_entity_type"] = "commodity_focus"
    out["peer_group_name"] = out["peer_group_name"].fillna("Broad Commodity Market").astype(str)
    out["drilldown_section"] = "Market Opportunity"
    out["drilldown_params_json"] = [
        json.dumps(
            {
                "commodity_focus": str(row.get("peer_group_name") or "Broad Commodity Market").strip() or "Broad Commodity Market",
                "horizon": str(row.get("horizon") or "").strip(),
                "market_view": "Commodity Section",
                "ticker": str(row.get("entity_id") or "").upper().strip(),
            },
            sort_keys=True,
        )
        for _, row in out.iterrows()
    ]
    return out


def _decorate_commodity_attention_rollups(attention_rollups: pd.DataFrame) -> pd.DataFrame:
    if attention_rollups.empty:
        return attention_rollups

    out = attention_rollups.copy()
    rollup_types = out["rollup_type"].astype(str).str.lower()
    market_mask = rollup_types == "market"
    portfolio_mask = rollup_types == "portfolio"
    focus_mask = rollup_types == "business_lens"

    out.loc[market_mask, "rollup_type"] = "commodity_market"
    out.loc[market_mask, "rollup_id"] = "commodity_market"
    out.loc[market_mask, "rollup_name"] = "Commodities"

    out.loc[portfolio_mask, "rollup_id"] = "commodity_portfolio"
    out.loc[portfolio_mask, "rollup_name"] = "Commodity Portfolio"

    out.loc[focus_mask, "rollup_type"] = "commodity_focus"

    def _metric_int(value: object) -> int:
        numeric = pd.to_numeric(value, errors="coerce")
        return int(numeric) if pd.notna(numeric) else 0

    def _metric_float(value: object) -> float:
        numeric = pd.to_numeric(value, errors="coerce")
        return float(numeric) if pd.notna(numeric) else 0.0

    out["summary_text"] = [
        (
            f"{str(row.get('rollup_name') or '').strip() or 'Rollup'} has "
            f"{_metric_int(row.get('active_event_count'))} active anomaly event(s); "
            f"top score is {_metric_float(row.get('top_attention_score')):.1f}."
        )
        for _, row in out.iterrows()
    ]
    return out.sort_values(["net_attention_score", "active_event_count"], ascending=False, na_position="last").reset_index(drop=True)


def run_equities(ctx: JobContext, conn: Any | None = None) -> None:
    _job_progress(ctx, conn, stage="starting", message="Starting equities preload.", progress_pct=1.0)
    cfg = _alpaca_config()
    if cfg is None:
        print("[warn] APCA credentials missing; skipping equities preload")
        return
    api = AlpacaAPI(cfg)
    symbols = AlpacaAPI._normalize_symbols(_resolve_equity_symbols(api, ctx, conn))
    _job_progress(
        ctx,
        conn,
        stage="equity_universe_ready",
        message=f"Resolved equity universe symbols={len(symbols)}.",
        progress_pct=8.0,
    )

    try:
        _job_progress(ctx, conn, stage="scan_movers", message="Scanning daily movers and positions.", progress_pct=12.0)
        movers = scan_daily_movers(api, symbols=symbols)
        _persist_dataset("daily_movers", movers, ctx, conn)
        macro_symbols = resolve_macro_anchor_symbols(symbols)
        macro_movers = scan_daily_movers(api, symbols=macro_symbols) if macro_symbols else pd.DataFrame()
        _persist_dataset("macro_anchor_daily_movers", macro_movers, ctx, conn)
        positions = _load_attention_positions(api)
        _persist_dataset("positions_snapshot", positions, ctx, conn)
        _job_progress(
            ctx,
            conn,
            stage="portfolio_history",
            message="Building portfolio history snapshot.",
            progress_pct=18.0,
        )
        portfolio_timeseries = _build_portfolio_timeseries_snapshot(api, period="5Y")
        _persist_dataset("portfolio_timeseries_snapshot", portfolio_timeseries, ctx, conn)

        momentum_lookback_days = max(int(os.getenv("MOMENTUM_LOOKBACK_DAYS", "3650")), 365)
        phase_days = max(int(os.getenv("PHASE_SHIFT_DAYS", "365")), 120)
        phase_corr_window = max(int(os.getenv("PHASE_SHIFT_CORR_WINDOW", "20")), 5)
        phase_roc_window = max(int(os.getenv("PHASE_SHIFT_ROC_WINDOW", "10")), 1)
        phase_momentum_window = max(int(os.getenv("PHASE_SHIFT_MOMENTUM_WINDOW", "63")), 5)
        phase_benchmark = AlpacaAPI._normalize_symbol((os.getenv("PHASE_SHIFT_BENCHMARK") or "SPY").strip().upper() or "SPY")

        price_lookback_days = max(int(os.getenv("EQUITY_PRICE_LOOKBACK_DAYS", "3650")), 365)
        incremental_lookback_days = max(int(os.getenv("EQUITY_PRICE_INCREMENTAL_LOOKBACK_DAYS", "45")), 5)
        full_refresh_hours = max(float(os.getenv("EQUITY_PRICE_FULL_REFRESH_HOURS", "168")), 0.0)
        phase_history_days = max(phase_days, phase_momentum_window + phase_corr_window + phase_roc_window + 30)
        effective_history_days = max(price_lookback_days, momentum_lookback_days, phase_history_days)

        _job_progress(
            ctx,
            conn,
            stage="price_history",
            message=f"Building equity price history snapshot days={effective_history_days} benchmark={phase_benchmark}.",
            progress_pct=28.0,
        )
        bars, bars_frame = _build_equity_price_history_snapshot(
            api,
            symbols,
            benchmark=phase_benchmark,
            history_days=effective_history_days,
            incremental_lookback_days=incremental_lookback_days,
            full_refresh_hours=full_refresh_hours,
        )
        _persist_dataset("price_history", bars_frame, ctx, conn)

        _job_progress(ctx, conn, stage="market_derivatives", message="Building momentum and phase-shift derivatives.", progress_pct=45.0)
        momentum = build_momentum_profiles_from_bars(bars, symbols=symbols)
        _persist_dataset("momentum_profiles", momentum, ctx, conn)

        phase_payload = build_correlation_phase_shifts_from_bars(
            bars,
            symbols=symbols,
            benchmark=phase_benchmark,
            days=phase_days,
            corr_window=phase_corr_window,
            roc_window=phase_roc_window,
            momentum_window=phase_momentum_window,
        )
        phase_summary = phase_payload.get("summary", pd.DataFrame())
        phase_history = phase_payload.get("history", pd.DataFrame())

        for frame in (phase_summary, phase_history):
            if frame is None or frame.empty:
                continue
            frame["phase_days"] = phase_days
            frame["phase_corr_window"] = phase_corr_window
            frame["phase_roc_window"] = phase_roc_window
            frame["phase_momentum_window"] = phase_momentum_window
            frame["phase_benchmark"] = phase_benchmark

        _persist_dataset("correlation_phase_shift_summary", phase_summary, ctx, conn)
        _persist_dataset("correlation_phase_shift_history", phase_history, ctx, conn)

        technical_history = pd.DataFrame()
        technical_latest = pd.DataFrame()
        _job_progress(ctx, conn, stage="technical_signals", message="Building technical signal derivatives.", progress_pct=58.0)
        if build_signal_frame is not None and summarize_signal_frame is not None:
            technical_history_parts: list[pd.DataFrame] = []
            technical_latest_rows: list[dict[str, object]] = []
            for symbol in symbols:
                raw_frame = bars.get(symbol, pd.DataFrame())
                if raw_frame.empty:
                    continue
                signal_frame = build_signal_frame(raw_frame)
                if signal_frame.empty:
                    continue
                signal_chunk = signal_frame.copy()
                signal_chunk["symbol"] = symbol
                technical_history_parts.append(signal_chunk)

                latest_summary = summarize_signal_frame(signal_frame)
                if latest_summary:
                    latest_summary["symbol"] = symbol
                    latest_summary["asof_time_utc"] = pd.to_datetime(signal_frame["timestamp"].max(), utc=True, errors="coerce")
                    technical_latest_rows.append(latest_summary)

            technical_history = pd.concat(technical_history_parts, ignore_index=True) if technical_history_parts else pd.DataFrame()
            technical_latest = pd.DataFrame(technical_latest_rows)
            _persist_dataset("technical_signal_history", technical_history, ctx, conn)
            _persist_dataset("technical_signals_latest", technical_latest, ctx, conn)
        else:
            print("[warn] signals module unavailable; skipping technical derivatives preload")

        try:
            _job_progress(ctx, conn, stage="attention_derivatives", message="Building attention and anomaly datasets.", progress_pct=72.0)
            peer_group_membership = build_peer_group_membership(asof_time_utc=pd.Timestamp(ctx.asof), symbols=symbols)
            taxonomy_peer_group_catalog = build_taxonomy_peer_group_catalog(peer_group_membership)
            expectation_config = ExpectationConfig(
                horizons=_parse_attention_horizons_env(),
                min_history_rows=max(int(os.getenv("ATTENTION_MIN_HISTORY_ROWS", "21")), 5),
                schema_version=(os.getenv("ATTENTION_SCHEMA_VERSION") or "v1").strip() or "v1",
            )
            attention_config = AttentionConfig(
                residual_zscore_threshold=max(float(os.getenv("ATTENTION_RESIDUAL_ZSCORE_THRESHOLD", "2.0")), 0.5),
                residual_zscore_thresholds=_parse_attention_thresholds_env() or None,
                min_attention_score=max(float(os.getenv("ATTENTION_MIN_ATTENTION_SCORE", "0")), 0.0),
                high_priority_threshold=max(float(os.getenv("ATTENTION_HIGH_PRIORITY_THRESHOLD", "75.0")), 1.0),
                news_lookback_days=max(int(os.getenv("ATTENTION_NEWS_LOOKBACK_DAYS", "3")), 0),
                persistence_periods=max(int(os.getenv("ATTENTION_PERSISTENCE_PERIODS", "2")), 1),
                schema_version=(os.getenv("ATTENTION_SCHEMA_VERSION") or "v1").strip() or "v1",
            )
            news_symbol_map = _load_attention_news_map(api, symbols)
            price_expectations = build_price_expectations(
                bars_frame,
                momentum,
                phase_summary,
                peer_group_membership,
                config=expectation_config,
            )
            attention_candidates = build_attention_candidates(
                price_expectations,
                technical_signals_latest=technical_latest,
                news_symbol_map=news_symbol_map,
                positions=positions,
                config=attention_config,
            )
            anomaly_events = filter_attention_events(
                attention_candidates,
                config=attention_config,
                statuses=["active", "cooling"],
            )
            attention_rollups = build_attention_rollups(
                anomaly_events,
                peer_group_membership,
                high_priority_threshold=attention_config.high_priority_threshold,
            )
            attention_feed = build_attention_feed(
                anomaly_events,
                attention_rollups,
                top_n=max(int(os.getenv("ATTENTION_FEED_TOP_N", "20")), 1),
            )

            _persist_dataset("peer_group_membership", peer_group_membership, ctx, conn)
            _persist_dataset("taxonomy_peer_group_membership", peer_group_membership, ctx, conn)
            _persist_dataset("taxonomy_peer_group_catalog", taxonomy_peer_group_catalog, ctx, conn)
            _persist_dataset("price_expectations", price_expectations, ctx, conn)
            _persist_dataset("attention_candidates", attention_candidates, ctx, conn)
            _persist_dataset("anomaly_events", anomaly_events, ctx, conn)
            _persist_dataset("attention_rollups", attention_rollups, ctx, conn)
            _persist_dataset("attention_feed", attention_feed, ctx, conn)
        except Exception as exc:
            print(f"[warn] anomaly layer skipped: {type(exc).__name__}: {exc}")

        try:
            _job_progress(ctx, conn, stage="fundamentals", message="Refreshing quarterly fundamentals when stale.", progress_pct=90.0)
            fundamentals_min_refresh_hours = max(float(os.getenv("FUNDAMENTALS_MIN_REFRESH_HOURS", "24")), 1.0)
            fundamentals_fresh = bool(conn is not None and _db_dataset_is_fresh(conn, "quarterly_fundamentals", fundamentals_min_refresh_hours))
            if fundamentals_fresh:
                print(f"[info] fundamentals preload skipped: latest snapshot is < {fundamentals_min_refresh_hours:g}h old")
            else:
                fundamentals, details = _build_quarterly_fundamentals_snapshot(symbols)
                provider = str(details.get("provider") or "local").strip() or "local"
                data_dir = str(details.get("data_dir") or "").strip()
                if data_dir:
                    print(f"[info] fundamentals source={provider} data_dir={data_dir}")
                else:
                    print(f"[info] fundamentals source={provider}")
                _persist_dataset("quarterly_fundamentals", fundamentals, ctx, conn)
        except Exception as exc:
            print(f"[warn] fundamentals preload skipped: {type(exc).__name__}: {exc}")
    except AlpacaAPIError as exc:
        print(f"[error] equities preload failed: {exc}")


def run_fred(ctx: JobContext, conn: Any | None = None) -> None:
    _job_progress(ctx, conn, stage="starting", message="Starting FRED and Treasury yield preload.", progress_pct=1.0)
    api_key = load_fred_api_key()
    if api_key:
        try:
            _job_progress(ctx, conn, stage="fred_dashboard", message="Loading FRED dashboard snapshots.", progress_pct=15.0)
            dashboard = load_fred_dashboard(api_key, years=int(os.getenv("FRED_LOOKBACK_YEARS", "10")))
            summary = dashboard.get("summary", pd.DataFrame())
            observations = dashboard.get("observations", pd.DataFrame())
            series_index = dashboard.get("series_index", pd.DataFrame())
            release_index = dashboard.get("release_index", pd.DataFrame())

            _persist_dataset("fred_summary", summary, ctx, conn)
            _persist_dataset("fred_observations", observations, ctx, conn)
            _persist_dataset("fred_series_index", series_index, ctx, conn)
            _persist_dataset("fred_release_index", release_index, ctx, conn)
        except FredAPIError as exc:
            print(f"[error] FRED preload failed: {exc}")
    else:
        print("[warn] FRED key unavailable; skipping FRED preload")

    try:
        _job_progress(ctx, conn, stage="treasury_yields", message="Loading Treasury yield datasets.", progress_pct=65.0)
        yield_observations, yield_summary, yield_facts = _build_treasury_yield_snapshots(asof_time_utc=ctx.asof)
        _persist_dataset("yield_curve_observations", yield_observations, ctx, conn)
        _persist_dataset("yield_curve_summary", yield_summary, ctx, conn)
        _persist_dataset("yield_curve_facts_1d", yield_facts, ctx, conn)
    except TreasuryYieldError as exc:
        print(f"[error] Treasury yield preload failed: {exc}")


def run_commodities(ctx: JobContext, conn: Any | None = None) -> None:
    _job_progress(ctx, conn, stage="starting", message="Starting commodities regime preload.", progress_pct=1.0)
    cfg = _alpaca_config()
    if cfg is None:
        print("[warn] APCA credentials missing; skipping commodity preload")
        return
    api = AlpacaAPI(cfg)
    symbols = default_commodity_universe_symbols()
    if not symbols:
        print("[warn] commodity preload skipped: taxonomy did not return any commodity symbols")
        return
    try:
        _job_progress(ctx, conn, stage="commodity_regimes", message=f"Scanning commodity regimes symbols={len(symbols)}.", progress_pct=12.0)
        payload = scan_commodity_regimes(api, symbols=symbols, commodity_symbols=symbols, days=252)
        summary = payload.get("summary", pd.DataFrame())
        history = payload.get("history", pd.DataFrame())

        _persist_dataset("commodity_regime_summary", summary, ctx, conn)
        _persist_dataset("commodity_regime_history", history, ctx, conn)

        try:
            _job_progress(ctx, conn, stage="commodity_attention", message="Building commodity attention derivatives.", progress_pct=55.0)
            peer_group_membership = build_commodity_peer_group_membership(asof_time_utc=pd.Timestamp(ctx.asof), symbols=symbols)
            primary_membership = _primary_peer_group_snapshot(
                peer_group_membership,
                fallback_names={"Broad Commodity Market"},
            )
            expectation_config = ExpectationConfig(
                horizons=_parse_attention_horizons_env(),
                min_history_rows=max(int(os.getenv("ATTENTION_MIN_HISTORY_ROWS", "21")), 5),
                schema_version=(os.getenv("ATTENTION_SCHEMA_VERSION") or "v1").strip() or "v1",
            )
            attention_config = AttentionConfig(
                residual_zscore_threshold=max(float(os.getenv("ATTENTION_RESIDUAL_ZSCORE_THRESHOLD", "2.0")), 0.5),
                residual_zscore_thresholds=_parse_attention_thresholds_env() or None,
                min_attention_score=max(float(os.getenv("ATTENTION_MIN_ATTENTION_SCORE", "0")), 0.0),
                high_priority_threshold=max(float(os.getenv("ATTENTION_HIGH_PRIORITY_THRESHOLD", "75.0")), 1.0),
                news_lookback_days=max(int(os.getenv("ATTENTION_NEWS_LOOKBACK_DAYS", "3")), 0),
                persistence_periods=max(int(os.getenv("ATTENTION_PERSISTENCE_PERIODS", "2")), 1),
                schema_version=(os.getenv("ATTENTION_SCHEMA_VERSION") or "v1").strip() or "v1",
            )

            price_lookback_days = max(
                int(os.getenv("COMMODITY_PRICE_LOOKBACK_DAYS", os.getenv("EQUITY_PRICE_LOOKBACK_DAYS", "3650"))),
                252,
            )
            benchmark_symbols = (
                primary_membership.get("benchmark", pd.Series(dtype=str))
                .dropna()
                .astype(str)
                .str.upper()
                .str.strip()
                .tolist()
            )
            bars_frame = _load_stock_bars_frame(api, [*symbols, *benchmark_symbols], days=price_lookback_days)
            momentum_lookback_days = max(
                int(os.getenv("COMMODITY_MOMENTUM_LOOKBACK_DAYS", os.getenv("MOMENTUM_LOOKBACK_DAYS", "3650"))),
                252,
            )
            momentum = scan_momentum_profiles(api, symbols=symbols, days=momentum_lookback_days)
            news_symbol_map = _load_attention_news_map(api, symbols)
            positions = _load_attention_positions(api)

            phase_summary = summary.copy()
            if not phase_summary.empty:
                phase_summary["symbol"] = phase_summary["symbol"].astype(str).str.upper().str.strip()
                phase_summary = phase_summary.merge(
                    primary_membership.rename(columns={"entity_id": "symbol"})[["symbol", "benchmark"]],
                    on="symbol",
                    how="left",
                )
                keep = [col for col in ["symbol", "benchmark", "correlation_now", "correlation_roc"] if col in phase_summary.columns]
                phase_summary = phase_summary[keep].drop_duplicates(subset=["symbol"], keep="last")

            commodity_signals = _commodity_regime_signals(summary)
            price_expectations = build_price_expectations(
                bars_frame,
                momentum,
                phase_summary,
                peer_group_membership,
                config=expectation_config,
            )
            attention_candidates = build_attention_candidates(
                price_expectations,
                technical_signals_latest=commodity_signals,
                news_symbol_map=news_symbol_map,
                positions=positions,
                config=attention_config,
            )
            anomaly_events = filter_attention_events(
                attention_candidates,
                config=attention_config,
                statuses=["active", "cooling"],
            )
            attention_candidates = _decorate_commodity_anomaly_events(attention_candidates)
            anomaly_events = _decorate_commodity_anomaly_events(anomaly_events)
            attention_rollups = build_attention_rollups(
                anomaly_events,
                peer_group_membership,
                high_priority_threshold=attention_config.high_priority_threshold,
            )
            attention_rollups = _decorate_commodity_attention_rollups(attention_rollups)
            attention_feed = build_attention_feed(
                anomaly_events,
                attention_rollups,
                top_n=max(int(os.getenv("ATTENTION_FEED_TOP_N", "20")), 1),
            )

            _persist_dataset("commodity_peer_group_membership", peer_group_membership, ctx, conn)
            _persist_dataset("commodity_price_expectations", price_expectations, ctx, conn)
            _persist_dataset("commodity_attention_candidates", attention_candidates, ctx, conn)
            _persist_dataset("commodity_anomaly_events", anomaly_events, ctx, conn)
            _persist_dataset("commodity_attention_rollups", attention_rollups, ctx, conn)
            _persist_dataset("commodity_attention_feed", attention_feed, ctx, conn)
        except Exception as exc:
            print(f"[warn] commodity anomaly layer skipped: {type(exc).__name__}: {exc}")
    except AlpacaAPIError as exc:
        print(f"[error] commodity preload failed: {exc}")


def run_options(ctx: JobContext, conn: Any | None = None) -> None:
    _job_progress(ctx, conn, stage="starting", message="Starting options preload.", progress_pct=1.0)
    cfg = _alpaca_config()
    if cfg is None:
        print("[warn] APCA credentials missing; skipping options preload")
        return
    api = AlpacaAPI(cfg)
    symbols = _symbols_from_env() or _symbols_from_latest_universe(25)
    if not symbols:
        try:
            symbols = _resolve_equity_symbols(api, ctx, conn)[:25]
        except Exception as exc:
            print(f"[warn] options preload could not resolve dynamic symbols: {type(exc).__name__}: {exc}")
            symbols = []
    if not symbols:
        print("[warn] options preload skipped: no symbols available")
        _persist_dataset("option_expirations", pd.DataFrame(columns=["symbol", "expiration"]), ctx, conn)
        _persist_dataset("option_contract_snapshots", pd.DataFrame(), ctx, conn)
        return
    rows: list[dict[str, str]] = []
    snapshot_parts: list[pd.DataFrame] = []
    total_symbols = max(len(symbols), 1)
    for index, symbol in enumerate(symbols, start=1):
        _job_progress(
            ctx,
            conn,
            stage="option_symbol",
            message=f"Loading option chain for {symbol} ({index}/{total_symbols}).",
            progress_pct=round(5.0 + 70.0 * (index - 1) / total_symbols, 1),
        )
        try:
            expirations, _, _ = load_option_chain(api, symbol)
            for expiration in expirations[:8]:
                rows.append({"symbol": symbol, "expiration": expiration})

            snapshots = build_option_snapshot_surface(api, symbol, max_contracts=1200)
            if not snapshots.empty:
                chunk = snapshots.copy()
                chunk["symbol"] = symbol
                snapshot_parts.append(chunk)
        except Exception as exc:
            print(f"[warn] option chain skipped symbol={symbol} reason={type(exc).__name__}")
    frame = pd.DataFrame(rows)
    _persist_dataset("option_expirations", frame, ctx, conn)

    option_snapshots = pd.concat(snapshot_parts, ignore_index=True) if snapshot_parts else pd.DataFrame()
    _persist_dataset("option_contract_snapshots", option_snapshots, ctx, conn)


def run_news(ctx: JobContext, conn: Any | None = None) -> None:
    _job_progress(ctx, conn, stage="starting", message="Starting news and attention preload.", progress_pct=1.0)
    cfg = _alpaca_config()
    if cfg is None:
        print("[warn] APCA credentials missing; skipping news preload")
        return
    api = AlpacaAPI(cfg)
    symbols = _symbols_from_env() or _symbols_from_latest_universe(50)
    if not symbols:
        seed_symbols = _load_latest_attention_seed(50)
        symbols = [
            _normalize_symbol(value)
            for value in seed_symbols.get("entity_id", pd.Series(dtype=str)).tolist()
            if _normalize_symbol(value)
        ]
    if not symbols:
        try:
            symbols = _resolve_equity_symbols(api, ctx, conn)[:50]
        except Exception as exc:
            print(f"[warn] news preload could not resolve dynamic symbols: {type(exc).__name__}: {exc}")
            symbols = []
    try:
        _job_progress(ctx, conn, stage="news_ingest", message=f"Fetching news for symbols={len(symbols)}.", progress_pct=10.0)
        news = api.get_news(symbols=symbols, limit=50)
        if not news.empty:
            if "symbols" in news.columns:
                mapped = news[[col for col in ["headline", "published_at", "source", "url", "symbols"] if col in news.columns]].copy()
                mapped["symbols"] = mapped["symbols"].apply(lambda x: ",".join(x) if isinstance(x, list) else str(x))
            else:
                mapped = news.copy()
        else:
            mapped = pd.DataFrame()

        _persist_dataset("news_articles", news, ctx, conn)

        _persist_dataset("news_symbol_map", mapped, ctx, conn)
        attention_seed = _load_latest_attention_seed(_parse_int_env("ATTENTION_CONTEXT_SYMBOL_LIMIT", 80, minimum=1))
        existing_edgar_filings = _load_latest_materialized_frame("edgar_filings")
        context_symbols = [
            str(symbol).upper().strip()
            for symbol in attention_seed.get("entity_id", pd.Series(dtype=str)).tolist()
            if str(symbol).strip()
        ]
        edgar_filings = EdgarClient().load_recent_filings(
            context_symbols,
            days=_parse_int_env("EDGAR_LOOKBACK_DAYS", 120, minimum=1),
            forms=_edgar_forms_from_env(),
            max_filings_per_symbol=_parse_int_env("EDGAR_MAX_FILINGS_PER_SYMBOL", 4, minimum=1),
            fetch_document_text=_parse_bool_env("EDGAR_FETCH_DOCUMENT_TEXT", True),
            max_document_fetches_per_symbol=_parse_int_env("EDGAR_MAX_DOCUMENT_FETCHES_PER_SYMBOL", 2, minimum=0),
            existing_frame=existing_edgar_filings,
        )
        attention_context = build_attention_context_bundle(attention_seed, edgar_filings, asof_time_utc=ctx.asof)
        edgar_evidence = build_edgar_evidence(pd.DataFrame(), None, asof_time_utc=ctx.asof)
        attention_context_llm = build_attention_context_narratives(
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            None,
            asof_time_utc=ctx.asof,
        )
        try:
            _job_progress(
                ctx,
                conn,
                stage="news_llm_enrichment",
                message=f"Building attention context for symbols={len(context_symbols)}.",
                progress_pct=48.0,
            )
            llm_client = load_llm_client()
            if llm_client is None:
                print("[warn] attention LLM enrichment skipped: missing LLM configuration")
            else:
                edgar_evidence = build_edgar_evidence(
                    edgar_filings,
                    llm_client,
                    existing_frame=_load_latest_materialized_frame("edgar_evidence"),
                    asof_time_utc=ctx.asof,
                )
                attention_context_llm = build_attention_context_narratives(
                    attention_seed,
                    edgar_filings,
                    edgar_evidence,
                    llm_client,
                    existing_frame=_load_latest_materialized_frame("attention_context_llm"),
                    asof_time_utc=ctx.asof,
                )
                attention_context = merge_attention_context_with_llm(attention_context, attention_context_llm)
        except LLMAPIError as exc:
            print(f"[warn] attention LLM enrichment skipped: {exc}")

        _job_progress(ctx, conn, stage="persist_attention_context", message="Persisting attention context datasets.", progress_pct=72.0)
        _persist_dataset("edgar_filings", edgar_filings, ctx, conn)
        _persist_dataset("edgar_evidence", edgar_evidence, ctx, conn)
        _persist_dataset("attention_context_llm", attention_context_llm, ctx, conn)
        _persist_dataset("attention_context_bundle", attention_context, ctx, conn)
    except AlpacaAPIError as exc:
        print(f"[error] news preload failed: {exc}")
    except EdgarAPIError as exc:
        print(f"[error] EDGAR attention context failed: {exc}")


def run_attention_home(ctx: JobContext, conn: Any | None = None) -> None:
    run_attention_home_build_job(
        ctx,
        conn,
        persist_dataset_fn=_persist_dataset,
        job_progress_fn=_job_progress,
        load_materialized_frame_fn=_load_latest_materialized_frame,
    )


def _build_us_equity_listings_snapshot() -> pd.DataFrame:
    listings = load_us_equity_listings(
        include_etfs=_parse_bool_env("TAXONOMY_INCLUDE_ETFS", True),
        include_non_common=_parse_bool_env("TAXONOMY_INCLUDE_NON_COMMON", False),
        timeout=_parse_int_env("TAXONOMY_LISTINGS_TIMEOUT_SECONDS", 30, minimum=5),
    )
    if not listings.empty:
        return listings
    fallback = _load_latest_materialized_frame("us_equity_listings")
    if not fallback.empty:
        print(f"[warn] using fallback us_equity_listings snapshot rows={len(fallback)}")
    return fallback


def _taxonomy_progress_pct(payload: dict[str, Any]) -> float | None:
    event = str(payload.get("event") or "").strip()
    allow_unknown = bool(payload.get("allow_unknown"))
    total_batches = max(int(payload.get("total_batches") or 0), 0)
    batch_index = max(int(payload.get("batch_index") or 0), 0)
    retry_total = max(int(payload.get("retry_total") or 0), 0)
    retry_index = max(int(payload.get("retry_index") or 0), 0)

    if event == "snapshot_prepare":
        return 5.0
    if event == "repair_prepare":
        return 82.0
    if event in {"repair_retry_prepare", "repair_retry_complete"} and retry_total > 0:
        ratio = min(max(retry_index / retry_total, 0.0), 1.0)
        if event == "repair_retry_prepare":
            ratio = min(max((retry_index - 1) / retry_total, 0.0), 1.0)
        return round(95.0 + ratio * 3.0, 1)
    if event == "snapshot_finalize":
        return 96.0
    if event == "snapshot_complete":
        return 99.0
    if event in {"classify_start", "batch_start", "batch_complete", "classify_complete"} and total_batches > 0:
        ratio = min(max(batch_index / total_batches, 0.0), 1.0)
        if event == "classify_complete":
            ratio = 1.0
        if allow_unknown:
            return round(10.0 + ratio * 70.0, 1)
        return round(82.0 + ratio * 13.0, 1)
    return None


def _taxonomy_progress_message(payload: dict[str, Any]) -> tuple[str, str]:
    event = str(payload.get("event") or "").strip()
    allow_unknown = bool(payload.get("allow_unknown"))
    pass_label = "initial_llm_pass" if allow_unknown else "repair_llm_pass"
    batch_index = int(payload.get("batch_index") or 0)
    total_batches = int(payload.get("total_batches") or 0)
    total_classified = int(payload.get("total_classified") or 0)
    total_symbols = int(payload.get("total_symbols") or 0)
    first_symbol = str(payload.get("first_symbol") or "").strip()
    last_symbol = str(payload.get("last_symbol") or "").strip()

    if event == "snapshot_prepare":
        return (
            "prepare_snapshot",
            "Prepared taxonomy snapshot "
            f"listings={int(payload.get('listing_count') or 0)} "
            f"existing_dynamic={int(payload.get('existing_dynamic_count') or 0)} "
            f"unresolved={int(payload.get('unresolved_count') or 0)}.",
        )
    if event == "classify_start":
        return (
            pass_label,
            f"Starting {'initial' if allow_unknown else 'repair'} taxonomy LLM pass "
            f"symbols={total_symbols} batches={total_batches}.",
        )
    if event == "batch_start":
        return (
            pass_label,
            f"Running {'initial' if allow_unknown else 'repair'} taxonomy LLM batch "
            f"{batch_index}/{total_batches} first={first_symbol or 'n/a'} last={last_symbol or 'n/a'}.",
        )
    if event == "batch_complete":
        return (
            pass_label,
            f"Completed {'initial' if allow_unknown else 'repair'} taxonomy LLM batch "
            f"{batch_index}/{total_batches}; classified={total_classified}/{total_symbols}.",
        )
    if event == "batch_failed":
        return (
            pass_label,
            f"Failed {'initial' if allow_unknown else 'repair'} taxonomy LLM batch "
            f"{batch_index}/{total_batches}: {str(payload.get('error') or 'unknown error')[:300]}",
        )
    if event == "classify_complete":
        return (
            pass_label,
            f"Completed {'initial' if allow_unknown else 'repair'} taxonomy LLM pass "
            f"rows={int(payload.get('row_count') or 0)}.",
        )
    if event == "repair_prepare":
        return (
            "repair_prepare",
            f"Preparing repair pass for {int(payload.get('repair_target_count') or 0)} unresolved symbols.",
        )
    if event == "repair_retry_prepare":
        return (
            "repair_retry",
            "Retrying repair taxonomy classification "
            f"pass={int(payload.get('retry_index') or 0)}/{int(payload.get('retry_total') or 0)} "
            f"batch_size={int(payload.get('batch_size') or 0)} "
            f"targets={int(payload.get('repair_target_count') or 0)}.",
        )
    if event == "repair_retry_complete":
        return (
            "repair_retry",
            "Completed repair retry pass "
            f"{int(payload.get('retry_index') or 0)}/{int(payload.get('retry_total') or 0)}; "
            f"retry_rows={int(payload.get('retry_rows') or 0)} "
            f"remaining_unresolved={int(payload.get('unresolved_count') or 0)}.",
        )
    if event == "snapshot_finalize":
        return (
            "finalize_snapshot",
            "Finalizing taxonomy snapshot "
            f"initial_rows={int(payload.get('initial_llm_rows') or 0)} "
            f"repair_rows={int(payload.get('repair_rows') or 0)} "
            f"repair_retry_rows={int(payload.get('repair_retry_rows') or 0)}.",
        )
    if event == "snapshot_complete":
        return (
            "snapshot_complete",
            f"Built taxonomy snapshot rows={int(payload.get('row_count') or 0)}.",
        )
    if event == "snapshot_failed":
        return (
            "snapshot_failed",
            f"Taxonomy snapshot failed reason={str(payload.get('reason') or 'unknown')} "
            f"unresolved={int(payload.get('unresolved_count') or 0)}.",
        )
    return ("running", f"Taxonomy progress update: {json.dumps(payload, default=str)}")


def _make_taxonomy_progress_callback(ctx: JobContext, conn: Any | None):
    def _callback(payload: dict[str, Any]) -> None:
        stage, message = _taxonomy_progress_message(payload)
        _job_progress(
            ctx,
            conn,
            stage=stage,
            message=message,
            progress_pct=_taxonomy_progress_pct(payload),
        )

    return _callback


def run_entity_taxonomy(ctx: JobContext, conn: Any | None = None) -> None:
    _job_progress(ctx, conn, stage="starting", message="Starting entity taxonomy refresh.", progress_pct=1.0)
    listings = _build_us_equity_listings_snapshot()
    if listings.empty:
        print("[warn] taxonomy refresh skipped: no NASDAQ/NYSE listings available")
        _persist_dataset("us_equity_listings", listings, ctx, conn)
        _persist_dataset("entity_taxonomy_labels", pd.DataFrame(), ctx, conn)
        return

    listings["symbol"] = listings.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().str.strip()
    listings = listings[listings["symbol"].ne("")].drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
    print(f"[info] taxonomy refresh listings={len(listings)}")
    _job_progress(
        ctx,
        conn,
        stage="listings_ready",
        message=f"Built NASDAQ/NYSE listing universe rows={len(listings)}.",
        progress_pct=4.0,
    )
    _persist_dataset("us_equity_listings", listings, ctx, conn)

    existing = fetch_entity_taxonomy_frame()
    print(f"[info] taxonomy refresh existing_rows={len(existing)}")
    llm_client = None
    try:
        llm_client = load_llm_client()
    except LLMAPIError as exc:
        print(f"[warn] taxonomy LLM classification unavailable: {exc}")

    llm_batch_size = _parse_int_env("TAXONOMY_LLM_BATCH_SIZE", 25, minimum=1)
    llm_max_symbols_env = (os.getenv("TAXONOMY_LLM_MAX_SYMBOLS") or "").strip()
    try:
        llm_max_symbols = int(llm_max_symbols_env) if llm_max_symbols_env else None
    except Exception:
        llm_max_symbols = None
    print(
        f"[info] taxonomy refresh llm_enabled={llm_client is not None} "
        f"batch_size={llm_batch_size} max_symbols={llm_max_symbols if llm_max_symbols is not None else 'all'}"
    )
    _job_progress(
        ctx,
        conn,
        stage="classification_setup",
        message=(
            f"Preparing taxonomy classification existing_rows={len(existing)} "
            f"llm_enabled={llm_client is not None} batch_size={llm_batch_size}."
        ),
        progress_pct=8.0,
    )

    snapshot = build_entity_taxonomy_snapshot(
        listings,
        existing_frame=existing,
        llm_client=llm_client,
        llm_batch_size=llm_batch_size,
        llm_max_symbols=llm_max_symbols,
        progress_callback=_make_taxonomy_progress_callback(ctx, conn),
    )
    source_counts = (
        snapshot["source_of_truth"].fillna("unknown").astype(str).value_counts().sort_index().to_dict()
        if isinstance(snapshot, pd.DataFrame) and not snapshot.empty and "source_of_truth" in snapshot.columns
        else {}
    )
    print(
        f"[info] taxonomy refresh snapshot_rows={len(snapshot)} "
        f"llm_rows={int(snapshot['source_of_truth'].eq('llm_taxonomy').sum()) if 'source_of_truth' in snapshot.columns else 0} "
        f"sources={json.dumps(source_counts, sort_keys=True)}"
    )
    _job_progress(
        ctx,
        conn,
        stage="persist_taxonomy_snapshot",
        message=f"Persisting final taxonomy snapshot rows={len(snapshot)}.",
        progress_pct=99.0,
    )
    _persist_dataset("entity_taxonomy_labels", snapshot, ctx, conn)

    if conn is not None:
        upsert_entity_taxonomy_frame(conn, snapshot)
        deactivate_missing_taxonomy_symbols(conn, snapshot["symbol"].tolist())


def run_universe_builder(ctx: JobContext, conn: Any | None = None) -> None:
    _job_progress(ctx, conn, stage="starting", message="Starting universe builder.", progress_pct=1.0)
    cfg = _alpaca_config()
    if cfg is None:
        existing = _load_latest_equity_universe_snapshot(_equity_universe_target_size())
        if existing.empty:
            print("[warn] APCA credentials missing and no existing universe snapshot found; persisting empty universe snapshot")
            _persist_dataset("universe_snapshot", pd.DataFrame(columns=["symbol", "rank"]), ctx, conn)
            return
        _persist_dataset("universe_snapshot", existing, ctx, conn)
        return

    api = AlpacaAPI(cfg)
    try:
        universe = _build_equity_universe_snapshot(api, target_size=_equity_universe_target_size())
    except Exception as exc:
        print(f"[warn] expanded universe build failed; attempting latest snapshot fallback: {type(exc).__name__}: {exc}")
        universe = pd.DataFrame()

    if universe.empty:
        universe = _load_latest_equity_universe_snapshot(_equity_universe_target_size())
    if universe.empty:
        print("[warn] no universe data available; persisting empty universe snapshot")
        universe = pd.DataFrame(columns=["symbol", "rank"])
    _job_progress(ctx, conn, stage="persist_universe", message=f"Persisting universe snapshot rows={len(universe)}.", progress_pct=90.0)
    _persist_dataset("universe_snapshot", universe, ctx, conn)


def main() -> None:
    job_name = (os.getenv("PIPELINE_JOB_NAME") or "equities-intraday-preload").strip()
    ctx = JobContext(
        name=job_name,
        run_id=str(uuid.uuid4()),
        asof=_utc_now(),
        universe_version=(os.getenv("UNIVERSE_VERSION") or datetime.now(timezone.utc).strftime("%Y%m%d")).strip(),
    )
    print(f"[info] job={ctx.name} run_id={ctx.run_id} asof={ctx.asof.isoformat()}")

    dispatch = {
        "universe-builder": run_universe_builder,
        "equities-intraday-preload": run_equities,
        "macro-fred-daily": run_fred,
        "commodities-regime": run_commodities,
        "options-liquid-universe": run_options,
        "news-ingest-and-features": run_news,
        "attention-home-build": run_attention_home,
        "entity-taxonomy-refresh": run_entity_taxonomy,
    }

    handler = dispatch.get(ctx.name)
    if handler is None:
        print(f"[error] unknown PIPELINE_JOB_NAME={ctx.name}")
        raise SystemExit(2)

    db_conn = _db_connection()
    if db_conn is not None:
        try:
            _db_bootstrap(db_conn)
            _db_mark_job_start(db_conn, ctx)
        except Exception as exc:
            print(f"[warn] failed to initialize postgres tracking: {exc}")
            try:
                db_conn.close()
            except Exception:
                pass
            db_conn = None

    status = "Succeeded"
    error_summary: str | None = None
    try:
        handler(ctx, db_conn)
    except Exception as exc:
        status = "Failed"
        error_summary = f"{type(exc).__name__}: {exc}"[:4000]
        raise
    finally:
        if db_conn is not None:
            try:
                _db_mark_job_end(db_conn, ctx, status=status, error_summary=error_summary)
            except Exception as exc:
                print(f"[warn] failed to finalize postgres run status: {exc}")
            try:
                db_conn.close()
            except Exception:
                pass

    print("[info] completed")


if __name__ == "__main__":
    main()
