"""Owner-approved retained-input comparison; strict study cap, no publication.

This explicit exception does not change AGENTS.md's normal Lambda-only policy.
Requires the private approval record and an isolated local PostgreSQL DSN.
Every actual HTTP attempt reserves the provider's full maximum token charge,
including nested repair/retry calls. Unknown outcomes keep their reservation.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import fcntl
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlparse
import uuid

APP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(APP))
ROOT = Path.home() / "Library/Application Support/SpectralNature/operations/production-recovery-2026-10-27"
OUT = ROOT / "api-qualification-20261028"
approval = json.loads((OUT / "authorization.json").read_text())
if approval.get("authorization") != "Approve this API comparison up to $15" or approval.get("maximum_total_usd") != 15:
    raise SystemExit("This comparison requires the owner's explicit $15 exception.")
study_lock = (OUT / "study.lock").open("a")
fcntl.flock(study_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
dsn = os.environ.get("QUALIFICATION_LOCAL_DSN", "")
if not dsn.startswith("host=/"):
    raise SystemExit("Declare an isolated local socket PostgreSQL DSN; never use the production database.")

jobs = json.loads((ROOT / "jobs.before.json").read_text())
job = next(row for row in jobs if row["name"] == "attention-home-build")
for item in job["properties"]["template"]["containers"][0]["env"]:
    if item.get("value") is not None:
        os.environ[item["name"]] = item["value"]
os.environ.update(
    DEPLOYMENT_ENV_FILE=str(Path.home() / "Documents/code/spectral_nature/streamlit_alpaca_app/infra/.generated/deployment.local.env"),
    APP_TRACK="offline-approved-20261028", PIPELINE_JOB_NAME="offline-output-comparison",
    POSTGRES_CONNECTION_STRING=dsn, LLM_PROVIDER="deepseek", LLM_BASE_URL="https://api.deepseek.com",
    LLM_MODEL="deepseek-v4-pro", LLM_REASONING_EFFORT="high", LLM_THINKING_MODE="enabled",
    LLM_CONTEXT_WINDOW_TOKENS="1048576", LLM_TIMEOUT_SECONDS="480",
    LLM_POLICY_PROFILE="balanced", LLM_POLICY_NOT_BEFORE="2026-10-27",
    LLM_POLICY_JSON=(APP / "config/inference_policy.json").read_text(),
    AQL_ZOPEDIA_AGENT_RUN_TIMEOUT_SECONDS="900", AQL_ZOPEDIA_AGENT_LLM_STEP_TIMEOUT_SECONDS="480",
    ATTENTION_HOME_ZOPEDIA_AGENT_LLM_TIMEOUT_SECONDS="480", ATTENTION_ZOPEDIA_WRITE_POLICY="none",
    AQL_ZOPEDIA_MODEL_TELEMETRY_ENABLED="1",
)
for key in ("PIPELINE_RUN_ID", "RESEARCH_RUN_BUDGET_USD", "CONTAINER_APP_JOB_EXECUTION_NAME"):
    os.environ.pop(key, None)

import requests
from services import llm, llm_execution, pipeline_store
from services.database import database_workload

# Isolate all run state, credit reservations, telemetry, and artifact checkpoints.
pipeline_store._postgres_connection_string = lambda: dsn
with database_workload("interactive"):
    with pipeline_store.open_database_connection() as conn:
        pipeline_store._ensure_execution_store(conn)


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


ledger_path = OUT / "strict-study-ledger.json"
ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {"cap_microusd": 15_000_000, "attempts": {}}
if ledger.get("cap_microusd") != 15_000_000:
    raise SystemExit("Unexpected study ledger cap.")
lock = threading.Lock()
send = requests.Session.send
prices = json.loads((APP / "services/llm_prices.json").read_text())["models"]
api_key = llm._resolve_api_key()
if not api_key:
    raise SystemExit("Existing DeepSeek credential is unavailable.")
os.environ["LLM_API_KEY"] = api_key


def strict_send(session, request, **kwargs):
    host = urlparse(request.url).hostname
    if request.method == "GET" and host == "api.deepseek.com" and urlparse(request.url).path.endswith("/user/balance"):
        return send(session, request, **kwargs)
    if host != "api.deepseek.com" or not urlparse(request.url).path.endswith("/chat/completions"):
        raise RuntimeError("Offline comparison allows supplied sources and approved inference only.")
    payload = json.loads(request.body)
    model = payload["model"]
    price = prices.get("deepseek:" + model)
    if request.method != "POST" or price is None or payload.get("stream") or payload.get("tools") or payload.get("n", 1) != 1:
        raise RuntimeError("Unpriced or externally acquiring request rejected.")
    # Reserve more than any legal response can cost; do not lower model quality,
    # context or output capacity to fit this exception. Peak rates are the bound.
    maximum = (Decimal(1_048_576) * Decimal(str(price["offpeak"]["input"]))
        + Decimal(393_216) * Decimal(str(price["offpeak"]["output"]))) * Decimal(str(price.get("peak_multiplier", 1)))
    amount = int(maximum.to_integral_value(rounding=ROUND_CEILING))
    identity = uuid.uuid4().hex
    started = datetime.now(timezone.utc)
    with lock:
        committed = sum(row.get("actual_microusd", row["maximum_microusd"]) for row in ledger["attempts"].values())
        if committed + amount > ledger["cap_microusd"]:
            raise RuntimeError("Approved comparison headroom cannot cover another complete request.")
        ledger["attempts"][identity] = {"model": model, "maximum_microusd": amount,
            "started_at_utc": started.isoformat(), "status": "pending",
            "input_sha256": hashlib.sha256(request.body if isinstance(request.body, bytes) else request.body.encode()).hexdigest()}
        save(ledger_path, ledger)
    try:
        response = send(session, request, **kwargs)
        data = response.json()
        # Retain complete paid outputs before the engine checks child deadlines.
        save(OUT / ("transport-" + identity + ".json"), data)
        charge = llm_execution.estimated_charge(provider="deepseek", model=model, usage=data.get("usage"), started_at=started)
        with lock:
            row = ledger["attempts"][identity]
            row.update(status="known" if charge.get("status") == "estimated" else "unknown",
                       http_status=response.status_code, usage=data.get("usage"), charge=charge)
            if charge.get("status") == "estimated":
                row["actual_microusd"] = int((Decimal(str(charge["usd_max"])) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
            save(ledger_path, ledger)
        return response
    except BaseException as exc:
        with lock:
            ledger["attempts"][identity].update(status="unknown", error_type=type(exc).__name__)
            save(ledger_path, ledger)
        raise


requests.Session.send = strict_send
client = llm.client_from_config(llm.LLMConfig(provider="deepseek", api_key=api_key,
    model="deepseek-v4-pro", base_url="https://api.deepseek.com", timeout_seconds=480,
    reasoning_effort="high", thinking_mode="enabled"))
from services.aql_zopedia_engine import run_aql_zopedia_agent
from pipeline.jobs import attention_home_build as home
from services.aql_zopedia_engine import research_engine_revision
revision = research_engine_revision()
mode = os.getenv("QUALIFICATION_MODE", "composition")
selected_symbols = json.loads(os.getenv("QUALIFICATION_CASES", '["NBIS","CLS","NVDA","KNSA","NIB"]'))
frozen_payload = json.loads((ROOT / "frozen-home-payload.json").read_text())
backgrounds = json.loads((ROOT / "attention_ticker_background_snapshots.json").read_text())

inputs = json.loads((ROOT / "exact-enrichment-inputs.json").read_text())
source_documents = json.loads((ROOT / "attention_source_documents.json").read_text())
cases = {}
for symbol in ("NBIS", "CLS", "NVDA"):
    evidence = inputs[symbol]["initial_evidence"]
    if symbol == "CLS":
        captured = json.loads((ROOT / "live-primary-CLS-600.json").read_text())
        evidence = evidence + [{"kind": "previous_attempt_collected_sources",
            "captured_asof_time_utc": captured["asof_time_utc"], "agent_result": captured["result"]["agent_result"]}]
    if symbol == "NBIS":
        evidence = evidence + [row for row in source_documents if "musk" in row.get("title", "").lower()]
    cases[symbol] = evidence
for symbol in ("KNSA", "NIB", "PNR"):
    captured = json.loads((ROOT / f"live-primary-{symbol}-600.json").read_text())
    inputs[symbol] = {"asof_time_utc": captured["asof_time_utc"]}
    cases[symbol] = [{"kind": "previous_attempt_collected_sources",
        "captured_asof_time_utc": captured["asof_time_utc"], "agent_result": captured["result"]["agent_result"]}]
for symbol in cases:
    cases[symbol] += [{"kind": "retained_instrument_background", "record": row}
                     for row in backgrounds if row.get("symbol") == symbol]
cases["HOME"] = []
cases = {symbol: cases[symbol] for symbol in selected_symbols}
if mode in {"primary", "final"}:
    from services import aql_zopedia_engine as engine
    from services.aql.collector import research_attention_candidate
    original_structured = engine.run_aql_zopedia_structured_agent
    def supplied_only(**kwargs):
        kwargs.setdefault("max_tool_calls", 8)
        kwargs.update(request_context={**(kwargs.get("request_context") or {}), "allowed_source_access": ["supplied"]})
        return original_structured(**kwargs)
    engine.run_aql_zopedia_structured_agent = supplied_only


def compare(symbol):
    path = OUT / f"{mode}-budget-{target}-{symbol}-{revision}.json"
    if path.exists():
        return
    started = time.monotonic()
    try:
        if symbol == "HOME":
            bundles = json.loads((ROOT / "frozen-home-bundles.json").read_text())
            payload, bundles, review = home._review_home_public_surface(frozen_payload, bundles,
                enrichment_rows=[], asof_time_utc=frozen_payload["generated_at_utc"], timeout_seconds=600,
                source_documents=source_documents)
            result = {"status": review.get("status"), "payload": payload, "bundles": bundles, "review": review}
        elif mode in {"primary", "final"}:
            candidate = next(row for row in frozen_payload["event_candidates_1d"] if row["symbol"] == symbol)
            result = research_attention_candidate(candidate, peer_symbols=[], documents=cases[symbol],
                llm_client=client, timeout_seconds=600)
        else:
            result = run_aql_zopedia_agent(query=home._build_zopedia_enrichment_query(symbol,
            asof_text=inputs[symbol]["asof_time_utc"]),
            task="attention_ticker_enrichment", surface="attention_home.zopedia_ticker_enrichment",
            force_refresh=True, max_tool_calls=0, run_timeout_seconds=900, llm_step_timeout_seconds=480,
            initial_evidence=cases[symbol], persist_findings=False, write_policy="none", llm_client=client,
                request_context={"asof_time_utc": inputs[symbol]["asof_time_utc"], "allowed_source_access": ["supplied"]})
    except Exception as exc:
        result = {"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}
    save(path, {"symbol": symbol, "target_usd": target, "engine_revision": revision,
        "result": result, "seconds": time.monotonic()-started})
    print(json.dumps({"symbol": symbol, "target_usd": target, "status": result.get("status"),
        "seconds": round(time.monotonic()-started, 1)}), flush=True)


approval.update(status="running", paid_calls_started="see strict-study-ledger.json")
save(OUT / "authorization.json", approval)
for target in ("0", "10"):
    os.environ.update(RESEARCH_RUN_BUDGET_USD=target, PIPELINE_RUN_ID="approved-comparison-" + target)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(compare, cases))
approval.update(status="outputs_ready_for_personal_review")
save(OUT / "authorization.json", approval)
print(json.dumps({"status": "outputs_ready", "attempts": len(ledger["attempts"]),
    "committed_usd": sum(row.get("actual_microusd", row["maximum_microusd"]) for row in ledger["attempts"].values())/1e6}), flush=True)
