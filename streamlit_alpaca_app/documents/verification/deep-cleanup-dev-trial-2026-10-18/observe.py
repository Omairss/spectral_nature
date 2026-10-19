"""Bounded operator observation; never deploys, starts research or changes product data.

Executed as a reviewed job command against the installed candidate. This is
verification code, excluded from application packaging and imports.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import time

import requests

from services.database import database_workload
from services import llm
from services.operational_health import _database
from services.pipeline_store import _azure_management_headers, _blob_service_client
from services.work_scope import WorkScope


def observe_http(target):
    environment, base, path, expected = target
    started = time.monotonic()
    result = {"environment": environment, "path": path, "expected_status": expected}
    try:
        response = requests.get(base + path, timeout=(5, 25), allow_redirects=False)
        result.update(status=response.status_code, bytes=len(response.content),
                      sha256=hashlib.sha256(response.content).hexdigest(),
                      cache_control=response.headers.get("cache-control"))
        if path == "/api/home" and response.ok:
            payload = response.json()
            result["publication"] = {key: payload.get(key) for key in
                                     ("status", "view_date", "as_of", "run_id", "coverage")}
            result["publication"].update(beats=len(payload.get("beats", [])),
                                          datapoints=len(payload.get("daily_datapoints", [])),
                                          audio=(payload.get("summary") or {}).get("audio_available"))
    except requests.RequestException as error:
        result["unavailable"] = type(error).__name__
    finally:
        result["seconds"] = round(time.monotonic() - started, 3)
    return result


def observe_database(environment, hours):
    os.environ["APP_TRACK"] = environment
    os.environ["PIPELINE_DATASET_NAMESPACE"] = "development" if environment == "development" else ""
    try:
        with database_workload("background"), WorkScope(time.monotonic() + 45).activate():
            result = _database(hours)
            if llm.load_prompt_overrides(force=True):
                result["reasoning_settings_sha256"] = hashlib.sha256(
                    json.dumps(llm._PROMPT_OVERRIDES, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            else:
                result.setdefault("unavailable", {})["reasoning_settings"] = "Overrides unavailable"
            return result
    except Exception as error:
        return {"scope": environment, "unavailable": {"database": type(error).__name__}}
    finally:
        os.environ["APP_TRACK"] = "development"
        os.environ["PIPELINE_DATASET_NAMESPACE"] = "development"


def observe_job(target):
    resource, headers = target
    result = {"name": resource["name"], "environment": resource["environment"]}
    try:
        response = requests.get("https://management.azure.com" + resource["id"] +
                                "/executions?api-version=2025-07-01", headers=headers, timeout=(5, 20))
        response.raise_for_status()
        executions = sorted(response.json()["value"],
                            key=lambda row: row["properties"].get("startTime", ""), reverse=True)
        result["latest"] = [{"name": row["name"],
                             **{key: row["properties"].get(key) for key in ("status", "startTime", "endTime")},
                             "images": [item["image"] for item in
                                        row["properties"].get("template", {}).get("containers", [])]}
                            for row in executions[:5]]
    except requests.RequestException as error:
        result["unavailable"] = type(error).__name__
    return result


def main():
    spec = json.loads(os.environ["CLEANUP_TRIAL_SPEC"])
    start = datetime.fromisoformat(spec["started_at"]).timestamp()
    end = datetime.fromisoformat(spec["ends_at"]).timestamp()
    interval = int(spec["interval_seconds"])
    if not 0 < end - start <= 48 * 3600 or interval < 300:
        raise ValueError("Observation requires an explicit bounded window and cadence")
    if time.time() >= end:
        raise ValueError("Observation window already expired")
    os.environ["APP_TRACK"] = "development"
    os.environ["PIPELINE_DATASET_NAMESPACE"] = "development"
    client = _blob_service_client()
    if client is None:
        raise RuntimeError("Observation storage unavailable")
    container = client.get_container_client(os.getenv("AZURE_STORAGE_CONTAINER", "datasets"))
    prefix = "namespaces/development/verification/cleanup-trial/" + spec["trial_id"] + "/"
    targets = [(environment, base, path, status)
               for environment, base in spec["frontends"].items()
               for path, status in [("/health", 200), ("/api/home", 200),
                                    ("/api/auth/session", 200), ("/api/admin/health", 401),
                                    ("/api/trading-agent", 401)]]
    samples = 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        while time.time() < end:
            slot = max(0, int((time.time() - start) // interval))
            observed = datetime.now(timezone.utc).isoformat()
            http = list(executor.map(observe_http, targets))
            database = {environment: observe_database(environment, 1)
                        for environment in spec["frontends"]}
            headers, _ = _azure_management_headers()
            executions = (list(executor.map(observe_job, [(row, headers) for row in spec["jobs"]]))
                          if headers else [{"unavailable": "Management identity unavailable"}])
            failed_executions = [{"job": row["name"], **execution}
                                 for row in executions for execution in row.get("latest", [])
                                 if execution["status"] in {"Failed", "Canceled"}
                                 and execution.get("startTime")
                                 and datetime.fromisoformat(execution["startTime"]).timestamp() >= start]
            snapshot = {"trial_id": spec["trial_id"], "slot": slot, "observed_at": observed,
                        "candidate": spec["candidate"], "http": http, "database": database,
                        "executions": executions,
                        "execution_flags": failed_executions,
                        "http_flags": [row for row in http if row.get("status") != row["expected_status"]],
                        "collection_gaps": {**{key: value["unavailable"] for key, value in database.items()
                                               if value.get("unavailable")},
                                            **{row.get("name", "jobs"): row["unavailable"] for row in executions
                                               if row.get("unavailable")}}}
            name = f"{prefix}samples/{slot:04d}/{observed}.json"
            container.upload_blob(name, json.dumps(snapshot, default=str), overwrite=False,
                                  metadata={key: str(len(snapshot[key])) for key in
                                            ("http_flags", "execution_flags", "collection_gaps")},
                                  timeout=30, connection_timeout=5, read_timeout=30)
            samples += 1
            print(json.dumps({"trial_id": spec["trial_id"], "slot": slot, "blob": name,
                              "http_flags": len(snapshot["http_flags"]),
                              "execution_flags": len(failed_executions),
                              "collection_gaps": snapshot["collection_gaps"]}), flush=True)
            if spec.get("sample_limit") and samples >= spec["sample_limit"]:
                return
            time.sleep(max(0, min(start + (slot + 1) * interval, end) - time.time()))
    blobs = list(container.list_blobs(name_starts_with=prefix + "samples/", include=["metadata"]))
    slots = {int(blob.name.removeprefix(prefix + "samples/").split("/", 1)[0]) for blob in blobs}
    expected = set(range(math.ceil((end - start) / interval)))
    summary = {"trial_id": spec["trial_id"], "status": "observation_complete",
               "started_at": spec["started_at"], "ends_at": spec["ends_at"],
               "missing_slots": sorted(expected - slots), "observed_slots": len(slots),
               "flagged_observations": {key: [blob.name for blob in blobs
                                              if (blob.metadata or {}).get(key) != "0"]
                                        for key in ("http_flags", "execution_flags", "collection_gaps")},
               "database_window": {environment: observe_database(environment, 48)
                                   for environment in spec["frontends"]},
               "promotion": "Requires complete coverage and review; observer never promotes"}
    container.upload_blob(prefix + "observation-complete.json", json.dumps(summary, default=str),
                          overwrite=False, timeout=30, connection_timeout=5, read_timeout=30)
    print(json.dumps({key: summary[key] for key in
                      ("trial_id", "status", "missing_slots", "observed_slots")}), flush=True)


if __name__ == "__main__":
    main()
