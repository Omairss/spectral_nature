"""Read-only Admin telemetry, scoped to the current environment.

Cloud reads are cached per resource/window and requested only from Admin. This
module never starts research, changes schemas, or interprets narrative quality.
"""
from __future__ import annotations

from contextlib import closing

from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta, timezone
import os
import re
import threading
import time
from typing import Any, Callable

import requests

from services.json_utils import to_jsonable
from services.llm import llm_settings_scope
from services.pipeline_store import dataset_namespace, open_database_connection
from services.secrets import build_azure_credential, resolve_secret_value
from services.database import database_session_stats, open_database_session
from services.work_scope import WorkScope
from azure.identity import ManagedIdentityCredential

_ARM = "https://management.azure.com"
_CACHE: dict[tuple, tuple[float, Any]] = {}
_LOCK = threading.Lock()
_READ_LOCKS: dict[tuple, threading.Lock] = {}
_COLLECTIONS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="admin_health")
_INFLIGHT: dict[tuple, Future] = {}
_COLLECTION_WAIT_SECONDS = 20.0


def _start_collection(key: tuple, seconds: int, read: Callable[[], Any]) -> Future:
    """One bounded background collection per snapshot identity."""
    with _LOCK:
        for old_key in [item for item, future in _INFLIGHT.items() if future.done()]:
            del _INFLIGHT[old_key]
        if key in _INFLIGHT:
            return _INFLIGHT[key]
        if len(_INFLIGHT) >= 16:
            future = Future()
            future.set_exception(RuntimeError("Telemetry collections are busy"))
            return future
        future = _COLLECTIONS.submit(_cached, key, seconds, read)
        _INFLIGHT[key] = future
        return future


def _cached(key: tuple, seconds: int, read: Callable[[], Any]) -> Any:
    with _LOCK:
        lock = _READ_LOCKS.setdefault(key, threading.Lock())
    with lock:
        prior = _CACHE.get(key)
        if prior and time.monotonic() - prior[0] < seconds:
            return prior[1]
        value = read()
        with _LOCK:
            if len(_CACHE) >= 96:
                oldest = min(_CACHE, key=lambda item: _CACHE[item][0])
                del _CACHE[oldest]
            _CACHE[key] = (time.monotonic(), value)
        return value


def _database(hours: int, *, maintenance: bool = False) -> dict:
    scope = llm_settings_scope()
    namespace = dataset_namespace()
    result: dict[str, Any] = {"scope": scope, "receipt_source": "database",
                              "observed_at": datetime.now(timezone.utc).isoformat(),
                              "client_connections": database_session_stats()}
    queries = {
        "attempts": ("""SELECT provider, requested_model AS model, surface, purpose,
            count(*) FILTER (WHERE status <> 'not_sent') AS attempts,
            count(DISTINCT model_call_id) AS operations,
            count(*) FILTER (WHERE status='failure') AS failures,
            count(*) FILTER (WHERE status='started') AS unfinished,
            count(*) FILTER (WHERE status <> 'not_sent' AND (usage_json IS NULL OR usage_json='null'::jsonb OR usage_json='{}'::jsonb)) AS unknown_usage,
            sum((usage_json->>'prompt_tokens')::bigint) AS input_tokens,
            max((usage_json->>'prompt_tokens')::bigint) AS peak_input_tokens,
            sum((usage_json->>'completion_tokens')::bigint) AS output_tokens,
            sum(coalesce(usage_json->>'prompt_cache_hit_tokens', usage_json->'prompt_tokens_details'->>'cached_tokens')::bigint) AS cached_tokens,
            sum((usage_json->'completion_tokens_details'->>'reasoning_tokens')::bigint) AS reasoning_tokens,
            sum((metadata_json->'estimated_charge'->>'usd_min')::numeric) AS estimated_usd_min,
            sum((metadata_json->'estimated_charge'->>'usd_max')::numeric) AS estimated_usd_max,
            count(*) FILTER (WHERE status <> 'not_sent' AND metadata_json->'estimated_charge'->>'status' IS DISTINCT FROM 'estimated') AS unpriced_attempts,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
            FROM aql_zopedia_provider_attempts
            WHERE settings_scope=%s AND started_at_utc >= now()-(%s*interval '1 hour')
            GROUP BY 1,2,3,4 ORDER BY attempts DESC""", (scope, hours)),
        "usage_trend": ("""SELECT date_trunc('hour',started_at_utc) AS time,
            count(*) FILTER (WHERE status <> 'not_sent') AS attempts, count(*) FILTER (WHERE status='failure') AS failures,
            sum((usage_json->>'prompt_tokens')::bigint) AS input_tokens,
            sum((usage_json->>'completion_tokens')::bigint) AS output_tokens
            FROM aql_zopedia_provider_attempts
            WHERE settings_scope=%s AND started_at_utc >= now()-(%s*interval '1 hour')
            GROUP BY 1 ORDER BY 1""", (scope, hours)),
        "connections": ("""SELECT coalesce(datname,'background') AS database,
            coalesce(nullif(application_name,''),'unlabelled') AS application,
            coalesce(state,'background') AS state, wait_event_type, count(*) AS connections,
            max(greatest(0,extract(epoch FROM clock_timestamp()-xact_start))) FILTER (WHERE xact_start IS NOT NULL) AS oldest_transaction_seconds
            FROM pg_stat_activity GROUP BY 1,2,3,4 ORDER BY connections DESC""", ()),
        "capacity": ("SELECT name, setting::integer AS value FROM pg_settings WHERE name IN ('max_connections','superuser_reserved_connections','reserved_connections')", ()),
        "learning": ("SELECT status, count(*) AS episodes, max(updated_at_utc) AS updated_at FROM saa_zopedia_learning_events GROUP BY status ORDER BY status", ()),
        "components": ("""SELECT DISTINCT ON(job_name) job_name,status,progress_stage,
            progress_message,start_time_utc,end_time_utc,platform_execution_name
            FROM job_runs WHERE CASE WHEN %s='' THEN position('::' in job_name)=0
                ELSE starts_with(job_name,%s) END
            ORDER BY job_name,start_time_utc DESC""", (namespace, namespace + "::")),
    }
    if maintenance:
        queries = {key: value for key, value in queries.items() if key in {"attempts", "components"}}
        queries["reports"] = ("""SELECT run_id, created_at_utc, status,
            summary_json->'semantic'->>'status' AS research_status,
            jsonb_array_length(coalesce(summary_json->'semantic'->'articles','[]'::jsonb)) AS visited_articles,
            jsonb_array_length(coalesce(summary_json->'semantic'->'mutation_ids','[]'::jsonb)) AS edits,
            (SELECT coalesce(jsonb_agg(jsonb_build_object(
                'page_id',article->'page_id','status',article->'status','error',article->'error')), '[]'::jsonb)
             FROM jsonb_array_elements(coalesce(summary_json->'semantic'->'articles','[]'::jsonb)) article) AS articles
            FROM saa_zopedia_maintenance_reports ORDER BY created_at_utc DESC LIMIT 20""", ())
        dsn = resolve_secret_value(["ZOPEDIA_MAINTENANCE_POSTGRES_CONNECTION_STRING"],
            secret_name_env="ZOPEDIA_MAINTENANCE_POSTGRES_CONNECTION_STRING_SECRET_NAME")
        if not dsn:
            raise ValueError("Maintenance telemetry database is not configured")
        conn = open_database_session(dsn, application_name="spectral:maintenance-health", workload="interactive")
    else:
        conn = open_database_connection()
    if conn is None:
        return {**result, "unavailable": {"database": "Connection unavailable"}}
    result["server_name"] = str(conn.info.host).split(".", 1)[0]
    with closing(conn):
        with conn.pinned_connection():
            deadline = time.monotonic() + 18.0
            for name, (query, params) in queries.items():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result[name] = []
                    result.setdefault("unavailable", {})[name] = "Collection time budget exhausted"
                    continue
                try:
                    with WorkScope(min(deadline, time.monotonic() + 8)).activate(), conn.cursor() as cur:
                        cur.execute(query, params)
                        names = [column.name for column in cur.description]
                        result[name] = [dict(zip(names, row)) for row in cur.fetchall()]
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    result[name] = []
                    result.setdefault("unavailable", {})[name] = type(exc).__name__
    return to_jsonable(result)


def _published_outputs() -> list[dict[str, Any]]:
    """Observe Blob publication independently of its PostgreSQL index."""
    from concurrent.futures import ThreadPoolExecutor
    from services.pipeline_store import _stable_latest_manifest_metadata
    names = ("attention_home_1d", "daily_market_datapoints_1d", "attention_ticker_snapshots_1d",
             "zopedia_news_business_resolutions", "trading_agent_runs", "trading_agent_outcomes")
    def read(name: str) -> dict[str, Any]:
        try:
            item = _stable_latest_manifest_metadata(name)
            return {"dataset": name, "source": "Blob", **({
                "version": item.dataset_version_id, "asof": item.asof_time_utc,
                "published_at": item.ingested_at_utc, "rows": item.row_count,
            } if item is not None else {"status": "unavailable"})}
        except Exception as exc:
            return {"dataset": name, "source": "Blob", "status": type(exc).__name__}
    with ThreadPoolExecutor(max_workers=4) as executor:
        return list(executor.map(read, names))


def _arm_get(path: str, params: dict | None = None) -> dict:
    client_id = os.getenv("SYSTEM_HEALTH_METRICS_CLIENT_ID", "").strip()
    credential = ManagedIdentityCredential(client_id=client_id) if client_id else build_azure_credential()
    if credential is None:
        raise RuntimeError("Azure telemetry identity unavailable")
    token = credential.get_token(_ARM + "/.default").token
    response = requests.get(_ARM + path, params=params,
                            headers={"Authorization": "Bearer " + token}, timeout=(5, 15))
    if not response.ok:
        raise RuntimeError(f"Azure telemetry HTTP {response.status_code}")
    return response.json()


def _resource_group() -> str:
    value = os.getenv("SYSTEM_HEALTH_AZURE_RESOURCE_GROUP_ID", "").rstrip("/")
    if not re.fullmatch(r"/subscriptions/[a-fA-F0-9-]+/resourceGroups/[\w().-]+", value):
        raise RuntimeError("Azure telemetry resource group is not configured")
    return value


def _inventory() -> list[dict]:
    group = _resource_group()
    development = llm_settings_scope() != "production"
    resources = _arm_get(group + "/resources", {"api-version": "2021-04-01"}).get("value", [])
    output = []
    for item in resources:
        kind = item["type"].lower()
        if kind not in {"microsoft.app/containerapps", "microsoft.app/jobs", "microsoft.dbforpostgresql/flexibleservers"}:
            continue
        if kind.startswith("microsoft.app/") and (("dev" in item["name"]) != development):
            continue
        output.append({"id": item["id"], "name": item["name"], "kind": item["type"],
                       "scope": "shared" if "postgresql" in kind else llm_settings_scope()})
    return sorted(output, key=lambda row: (row["kind"], row["name"]))


def _metrics(resource: dict, hours: int) -> dict:
    kind = resource["kind"].lower()
    names = (["cpu_percent", "memory_percent", "active_connections", "connections_failed", "storage_percent"]
             if "postgresql" in kind else ["UsageNanoCores", "UsageBytes"] if kind.endswith("jobs")
             else ["UsageNanoCores", "WorkingSetBytes", "Replicas", "RestartCount"])
    definitions = _arm_get(resource["id"] + "/providers/microsoft.insights/metricDefinitions", {"api-version": "2018-01-01"})
    available = {row["name"]["value"] for row in definitions.get("value", [])}
    names = [name for name in names if name in available]
    if not names:
        return {"resource": resource["name"], "series": [], "unavailable": "No supported metrics"}
    end = datetime.now(timezone.utc)
    interval = "PT1H" if hours > 48 else "PT15M" if hours > 3 else "PT1M"
    data = _arm_get(resource["id"] + "/providers/microsoft.insights/metrics", {
        "api-version": "2018-01-01", "metricnames": ",".join(names),
        "timespan": (end-timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ") + "/" + end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval": interval, "aggregation": "Average,Maximum",
        "autoAdjustTimegrain": "true",
    })
    series = []
    for metric in data.get("value", []):
        for stream in metric.get("timeseries", []):
            points = [{"time": row["timeStamp"], "average": row.get("average"), "maximum": row.get("maximum")}
                      for row in stream.get("data", []) if row.get("average") is not None or row.get("maximum") is not None]
            series.append({"metric": metric["name"]["value"], "unit": metric.get("unit"),
                           "dimensions": stream.get("metadatavalues", []), "points": points})
    result = {"resource": resource["name"], "observed_at": end.isoformat(), "series": series,
              "aggregation_scope": "resource"}
    if kind.startswith("microsoft.app/"):
        try:
            definition = _arm_get(resource["id"], {"api-version": "2025-07-01"})
            template = definition.get("properties", {}).get("template", {})
            result["allocation"] = {
                "containers": [{"name": item["name"], **item.get("resources", {})}
                               for item in template.get("containers", [])],
                "replicas": template.get("scale", {}),
            }
        except Exception as exc:
            result["allocation_unavailable"] = type(exc).__name__
    return result


def build_operational_health_view(*, hours: int = 24, resource_name: str = "") -> dict:
    hours = min(max(int(hours), 1), 168)
    scope = llm_settings_scope()
    def collect_database():
        try:
            return _database(hours)
        except Exception as exc:
            return {"scope": scope, "observed_at": datetime.now(timezone.utc).isoformat(),
                    "unavailable": {"database": type(exc).__name__}}
    deadline = time.monotonic() + _COLLECTION_WAIT_SECONDS
    pending = {
        "database": _start_collection(("database", scope, hours), 60, collect_database),
        "outputs": _start_collection(("outputs", dataset_namespace()), 60, _published_outputs),
        "resources": _start_collection(("resources", scope), 1800, _inventory),
    }
    if (os.getenv("ZOPEDIA_MAINTENANCE_POSTGRES_CONNECTION_STRING") or
            os.getenv("ZOPEDIA_MAINTENANCE_POSTGRES_CONNECTION_STRING_SECRET_NAME")):
        def collect_maintenance():
            try:
                return _database(hours, maintenance=True)
            except Exception as exc:
                return {"unavailable": {"database": type(exc).__name__}}
        pending["maintenance"] = _start_collection(("maintenance", scope, hours), 60, collect_maintenance)
    result: dict = {"hours": hours, "scope": scope}
    observed: set[str] = set()
    while True:
        for name, future in list(pending.items()):
            if name in observed or not future.done():
                continue
            observed.add(name)
            try:
                result[name] = future.result()
            except Exception as exc:
                if name == "database":
                    result[name] = {"scope": scope, "observed_at": datetime.now(timezone.utc).isoformat(),
                                    "unavailable": {"database": type(exc).__name__}}
                elif name == "outputs":
                    result[name] = []
                    result["outputs_unavailable"] = type(exc).__name__
                else:
                    result[name] = [] if name == "resources" else {"series": [], "resource": resource_name}
                    result["cloud_unavailable"] = str(exc)
            if name == "resources" and resource_name and not result.get("cloud_unavailable"):
                selected = next((row for row in result[name] if row["name"] == resource_name), None)
                if selected is None:
                    raise ValueError("Unknown telemetry resource")
                def collect_metrics(resource=selected):
                    try:
                        return _metrics(resource, hours)
                    except Exception as exc:
                        return {"resource": resource["name"], "series": [], "unavailable": str(exc)}
                pending["metrics"] = _start_collection(("metrics", scope, selected["id"], hours), 600, collect_metrics)
        remaining = deadline - time.monotonic()
        unfinished = [future for name, future in pending.items() if name not in observed]
        if not unfinished or remaining <= 0:
            break
        wait(unfinished, timeout=remaining, return_when=FIRST_COMPLETED)
    if "maintenance" in pending and "maintenance" not in result:
        result["maintenance"] = {"unavailable": {"database": "Collection still running"}}
    if "database" not in result:
        result["database"] = {"scope": scope, "observed_at": datetime.now(timezone.utc).isoformat(),
                              "unavailable": {"database": "Collection still running"}}
    if "outputs" not in result:
        result["outputs"] = []
        result["outputs_unavailable"] = "Collection still running"
    if "resources" not in result:
        result["resources"] = []
        result["cloud_unavailable"] = "Resource collection still running"
    if resource_name and "metrics" in pending and "metrics" not in result:
        result["metrics"] = {"resource": resource_name, "series": [], "unavailable": "Collection still running"}
    return result
