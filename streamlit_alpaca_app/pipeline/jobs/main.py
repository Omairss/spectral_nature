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

from services.alpaca_api import AlpacaAPI, AlpacaAPIError
from services.config import AppConfig
from services.fred import FredAPIError, load_fred_api_key, load_fred_dashboard
from services.fundamentals import load_quarterly_fundamentals
from services.market import (
    COMMODITY_FOCUS_UNIVERSES,
    DEFAULT_UNIVERSE,
    scan_commodity_regimes,
    scan_correlation_phase_shifts,
    scan_daily_movers,
    scan_momentum_profiles,
)
from services.options import build_option_snapshot_surface, load_option_chain
from services.secrets import resolve_secret_value

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
        "fred_lookback_years": (os.getenv("FRED_LOOKBACK_YEARS") or "10").strip(),
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
                universe_version TEXT,
                asof_time_utc TIMESTAMPTZ
            )
            """
        )
    conn.commit()


def _db_mark_job_start(conn: Any, ctx: JobContext) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO job_runs (
                run_id, job_name, schedule_slot, start_time_utc,
                status, retries, universe_version, asof_time_utc
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                job_name = EXCLUDED.job_name,
                schedule_slot = EXCLUDED.schedule_slot,
                start_time_utc = EXCLUDED.start_time_utc,
                status = EXCLUDED.status,
                retries = EXCLUDED.retries,
                universe_version = EXCLUDED.universe_version,
                asof_time_utc = EXCLUDED.asof_time_utc
            """,
            (
                ctx.run_id,
                ctx.name,
                (os.getenv("SCHEDULE_SLOT") or "").strip() or None,
                _utc_now(),
                "running",
                0,
                ctx.universe_version,
                ctx.asof,
            ),
        )
    conn.commit()


def _db_mark_job_end(conn: Any, ctx: JobContext, status: str, error_summary: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE job_runs
            SET end_time_utc = %s,
                status = %s,
                error_summary = %s
            WHERE run_id = %s
            """,
            (_utc_now(), status, error_summary, ctx.run_id),
        )
    conn.commit()


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
    if frame.empty:
        return ""
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


def _symbols_from_env(limit_default: int = 100) -> list[str]:
    raw = (os.getenv("UNIVERSE_SYMBOLS") or "").strip()
    if raw:
        return [item.strip().upper() for item in raw.split(",") if item.strip()]
    return DEFAULT_UNIVERSE[:limit_default]


def run_equities(ctx: JobContext, conn: Any | None = None) -> None:
    cfg = _alpaca_config()
    if cfg is None:
        print("[warn] APCA credentials missing; skipping equities preload")
        return
    api = AlpacaAPI(cfg)
    symbols = _symbols_from_env(limit_default=100)

    try:
        movers = scan_daily_movers(api, symbols=symbols)
        _persist_dataset("daily_movers", movers, ctx, conn)

        momentum = scan_momentum_profiles(api, symbols=symbols, days=180)
        _persist_dataset("momentum_profiles", momentum, ctx, conn)

        price_lookback_days = max(int(os.getenv("EQUITY_PRICE_LOOKBACK_DAYS", "3650")), 365)
        end = _utc_now()
        start = end - timedelta(days=price_lookback_days)
        bars = api.get_stock_bars(symbols=symbols, start=start, end=end, timeframe="1Day", feed="iex")
        parts: list[pd.DataFrame] = []
        for symbol, frame in bars.items():
            if frame.empty:
                continue
            chunk = frame.copy()
            chunk["symbol"] = symbol
            keep = [col for col in ["symbol", "timestamp", "open", "high", "low", "close", "volume"] if col in chunk.columns]
            parts.append(chunk[keep])
        bars_frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        _persist_dataset("price_history", bars_frame, ctx, conn)

        phase_days = max(int(os.getenv("PHASE_SHIFT_DAYS", "365")), 120)
        phase_corr_window = max(int(os.getenv("PHASE_SHIFT_CORR_WINDOW", "20")), 5)
        phase_roc_window = max(int(os.getenv("PHASE_SHIFT_ROC_WINDOW", "10")), 1)
        phase_momentum_window = max(int(os.getenv("PHASE_SHIFT_MOMENTUM_WINDOW", "63")), 5)
        phase_benchmark = (os.getenv("PHASE_SHIFT_BENCHMARK") or "SPY").strip().upper() or "SPY"

        phase_payload = scan_correlation_phase_shifts(
            api,
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
            fundamentals_parts: list[pd.DataFrame] = []
            for symbol in symbols:
                bundle = load_quarterly_fundamentals(symbol)
                for statement in ("income", "balance", "cashflow"):
                    statement_frame = bundle.get(statement, pd.DataFrame())
                    if statement_frame is None or statement_frame.empty:
                        continue
                    chunk = statement_frame.copy()
                    if "ticker" not in chunk.columns:
                        chunk["ticker"] = symbol
                    if "statement" not in chunk.columns:
                        chunk["statement"] = statement
                    fundamentals_parts.append(chunk)

            fundamentals = pd.concat(fundamentals_parts, ignore_index=True) if fundamentals_parts else pd.DataFrame()
            if not fundamentals.empty:
                dedupe_cols = [col for col in ["ticker", "statement", "metric", "report_date"] if col in fundamentals.columns]
                if dedupe_cols:
                    fundamentals = fundamentals.drop_duplicates(subset=dedupe_cols, keep="last")
            _persist_dataset("quarterly_fundamentals", fundamentals, ctx, conn)
        except Exception as exc:
            print(f"[warn] fundamentals preload skipped: {type(exc).__name__}: {exc}")
    except AlpacaAPIError as exc:
        print(f"[error] equities preload failed: {exc}")


def run_fred(ctx: JobContext, conn: Any | None = None) -> None:
    api_key = load_fred_api_key()
    if not api_key:
        print("[warn] FRED key unavailable; skipping FRED preload")
        return
    try:
        dashboard = load_fred_dashboard(api_key, years=int(os.getenv("FRED_LOOKBACK_YEARS", "10")))
        summary = dashboard.get("summary", pd.DataFrame())
        observations = dashboard.get("observations", pd.DataFrame())

        _persist_dataset("fred_summary", summary, ctx, conn)

        _persist_dataset("fred_observations", observations, ctx, conn)
    except FredAPIError as exc:
        print(f"[error] FRED preload failed: {exc}")


def run_commodities(ctx: JobContext, conn: Any | None = None) -> None:
    cfg = _alpaca_config()
    if cfg is None:
        print("[warn] APCA credentials missing; skipping commodity preload")
        return
    api = AlpacaAPI(cfg)
    symbols = COMMODITY_FOCUS_UNIVERSES.get("Broad Commodity Market", [])
    try:
        payload = scan_commodity_regimes(api, symbols=symbols, commodity_symbols=symbols, days=252)
        summary = payload.get("summary", pd.DataFrame())
        history = payload.get("history", pd.DataFrame())

        _persist_dataset("commodity_regime_summary", summary, ctx, conn)

        _persist_dataset("commodity_regime_history", history, ctx, conn)
    except AlpacaAPIError as exc:
        print(f"[error] commodity preload failed: {exc}")


def run_options(ctx: JobContext, conn: Any | None = None) -> None:
    cfg = _alpaca_config()
    if cfg is None:
        print("[warn] APCA credentials missing; skipping options preload")
        return
    api = AlpacaAPI(cfg)
    symbols = _symbols_from_env(limit_default=25)
    rows: list[dict[str, str]] = []
    snapshot_parts: list[pd.DataFrame] = []
    for symbol in symbols:
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
    cfg = _alpaca_config()
    if cfg is None:
        print("[warn] APCA credentials missing; skipping news preload")
        return
    api = AlpacaAPI(cfg)
    symbols = _symbols_from_env(limit_default=50)
    try:
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
    except AlpacaAPIError as exc:
        print(f"[error] news preload failed: {exc}")


def run_universe_builder(ctx: JobContext, conn: Any | None = None) -> None:
    default = pd.DataFrame({"symbol": DEFAULT_UNIVERSE, "rank": list(range(1, len(DEFAULT_UNIVERSE) + 1))})
    _persist_dataset("universe_snapshot", default, ctx, conn)


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

    status = "success"
    error_summary: str | None = None
    try:
        handler(ctx, db_conn)
    except Exception as exc:
        status = "failed"
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
