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
from zoneinfo import ZoneInfo
import uuid

APP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(APP))
ROOT = Path.home() / "Library/Application Support/SpectralNature/operations/production-recovery-2026-10-27"
OUT = ROOT.parent / "event-adaptive-reliability-2026-10-28/quality-comparison"
approval = json.loads((OUT / "authorization.json").read_text())
if approval.get("status") not in {"approved", "running"}:
    raise SystemExit("This comparison is not authorized to start or continue; record a fresh explicit owner exception.")
if approval.get("authorization") != "Approve this API comparison up to $10" or approval.get("maximum_total_usd") != 10:
    raise SystemExit("This comparison requires the owner's explicit $10 exception.")
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
    APP_TRACK="event-adaptive-comparison-20261028", PIPELINE_JOB_NAME="offline-output-comparison",
    POSTGRES_CONNECTION_STRING=dsn, LLM_PROVIDER="deepseek", LLM_BASE_URL="https://api.deepseek.com",
    LLM_MODEL="deepseek-v4-pro", LLM_REASONING_EFFORT="high", LLM_THINKING_MODE="enabled",
    LLM_CONTEXT_WINDOW_TOKENS="1048576", LLM_TIMEOUT_SECONDS="480",
    LLM_POLICY_PROFILE="balanced", LLM_POLICY_NOT_BEFORE="2026-10-27",
    LLM_POLICY_JSON=(APP / "config/inference_policy.json").read_text(),
    AQL_ZOPEDIA_AGENT_RUN_TIMEOUT_SECONDS="900", AQL_ZOPEDIA_AGENT_LLM_STEP_TIMEOUT_SECONDS="480",
    ATTENTION_HOME_ZOPEDIA_AGENT_LLM_TIMEOUT_SECONDS="480", ATTENTION_ZOPEDIA_WRITE_POLICY="none",
    AQL_ZOPEDIA_MODEL_TELEMETRY_ENABLED="1",
)
os.environ.update(LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE="", ATTENTION_ZOPEDIA_WRITE_POLICY="none")
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
ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {"cap_microusd": 10_000_000, "attempts": {}}
if ledger.get("cap_microusd") != 10_000_000:
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
    if datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat() != approval["pacific_day"]:
        raise RuntimeError("The approved study day has ended")
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
from services.aql_zopedia_engine import research_engine_revision
from pipeline.jobs import attention_home_build as home
from services.aql import events
import pandas as pd

revision = research_engine_revision()
frozen = json.loads((ROOT / "frozen-home-payload.json").read_text())
bundles = json.loads((ROOT / "frozen-home-bundles.json").read_text())
documents = json.loads((ROOT / "attention_source_documents.json").read_text())
source_frame = pd.DataFrame([{"headline": row["title"], "url": row["url"], "content": row["raw_text"],
    "published_at": row["published_at"], "symbols": row.get("provider_symbols"), "source": row.get("source_provider")}
    for row in documents if row.get("document_id", "").startswith("doc::event-news::")])
market = pd.DataFrame(frozen.get("ticker_snapshots") or [])
plan = json.loads((OUT / "study-plan.json").read_text())
for name, fingerprint in plan["input_sha256"].items():
    if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != fingerprint:
        raise RuntimeError("The reviewed study inputs changed")
approval.update(status="running")
save(OUT / "authorization.json", approval)

# Use the real application nomination path, stopping before paid acquisition.
# This is a controlled repeated-run scenario: identical observations, later as-of,
# and the actual already-completed source investigations from retained production.
prior_artifacts = [{"artifact_id": key, "subject": "event:" + key,
    "task": "source_event_research", "created_at_utc": frozen["generated_at_utc"],
    "result": {"payload": bundle}} for key,bundle in bundles.items() if bundle.get("source_event")]
original_runner = events.run_aql_zopedia_structured_agent
original_latest = events.latest_research_artifact
original_list = events.pipeline_store.list_research_artifacts
class Captured(Exception):
    pass

def nomination(label, prior):
    path = OUT / (label + "-" + revision + ".json")
    if path.exists():
        return
    started = time.monotonic()
    def capture(**kwargs):
        kwargs.update(request_context={"allowed_source_access": ["supplied"]})
        result = original_runner(**kwargs)
        save(path, {"status": result.get("status"), "result": result,
            "engine_revision": revision, "seconds": time.monotonic()-started})
        print(json.dumps({"case": label, "status": result.get("status"), "seconds": time.monotonic()-started}), flush=True)
        raise Captured()
    os.environ["LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE"] = "isolated-comparison"
    events.run_aql_zopedia_structured_agent = capture
    events.latest_research_artifact = lambda **kw: ({"payload": {"events": []}, "artifact_context": {"market_observations": market.to_dict("records")}} if prior else None)
    events.pipeline_store.list_research_artifacts = lambda **kw: prior_artifacts if prior else []
    try:
        events.research_source_events(source_frame, market_frame=market, universe_frame=None,
            asof_time_utc=pd.Timestamp(frozen["generated_at_utc"]) + pd.Timedelta(minutes=5 if prior else 0),
            run_id="offline-event-adaptation", llm_client=client)
    except Captured:
        pass
    finally:
        events.run_aql_zopedia_structured_agent = original_runner
        events.latest_research_artifact = original_latest
        events.pipeline_store.list_research_artifacts = original_list
        os.environ["LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE"] = ""

try:
    from services.aql import business_model_stack as company
    checkpoint_path=OUT / "company-checkpoints.json"
    if hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()!=plan["company_checkpoints_sha256"]:
        raise RuntimeError("Retained company inputs changed")
    saved=json.loads(checkpoint_path.read_text())[0]
    state=saved["state"]
    for label, advance in (("recent-company-reuse",pd.Timedelta(minutes=5)),("aged-company-reuse",pd.Timedelta(days=1))):
        path=OUT / (label+"-"+revision+".json")
        if path.exists():continue
        started=time.monotonic()
        result=company._assess_company_refresh(previous_inputs=state["evidence"],current_inputs=state["evidence"],
            retained_analysis=state["result"],symbol=saved["symbol"],original_asof=state["asof"],
            current_asof=(pd.Timestamp(state["asof"])+advance).isoformat(),freshness_check_due=True,client=client)
        save(path,{"case":label,"symbol":saved["symbol"],"scenario":"Controlled clock advance over unchanged retained inputs; not a claim of live coverage after capture",
            "result":result,"engine_revision":revision,"seconds":time.monotonic()-started})
        print(json.dumps({"case":label,"status":result.get("status"),"seconds":time.monotonic()-started}),flush=True)
    for label, prior in (("heavy-nomination", False), ("unchanged-nomination", True)):
        nomination(label, prior)
    path = OUT / ("complete-home-editor-"+revision+".json")
    if not path.exists():
        started=time.monotonic()
        revised, revised_bundles, review = home._review_home_public_surface(frozen, bundles,
            enrichment_rows=[], asof_time_utc=frozen["generated_at_utc"], timeout_seconds=900, source_documents=documents)
        save(path, {"payload": revised, "bundles": revised_bundles, "review": review,
            "engine_revision": revision, "seconds": time.monotonic()-started})
        print(json.dumps({"case": "complete-home-editor", "status": review.get("status"), "seconds": time.monotonic()-started}), flush=True)
    approval.update(status="outputs_ready_for_personal_review")
finally:
    save(OUT / "authorization.json", approval)
    print(json.dumps({"attempts":len(ledger["attempts"]), "committed_upper_bound_usd":sum(r.get("actual_microusd",r["maximum_microusd"]) for r in ledger["attempts"].values())/1e6}),flush=True)
