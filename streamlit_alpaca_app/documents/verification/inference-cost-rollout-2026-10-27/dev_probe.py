"""Exact-image development qualification, including a real Azure replica retry."""
from datetime import datetime, timezone
from dataclasses import replace
from zoneinfo import ZoneInfo
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
import uuid

root = Path("/app")
files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in root.rglob("*") if p.is_file()}
fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
if fingerprint != os.environ["EXPECTED_RUNTIME_INVENTORY_SHA256"]:
    raise RuntimeError("Complete packaged application does not match accepted Git runtime")
print(json.dumps({"probe": "package", "all_packaged_files_checked": True, "files": len(files),
                  "runtime_inventory_sha256": fingerprint, "source_revision": os.getenv("GIT_SHA")}), flush=True)

import pipeline.jobs.main
import services.aql.business_model_stack
print(json.dumps({"probe": "worker_entrypoint", "status": "imported"}), flush=True)

from services import llm
from services.aql_zopedia_gateway import generate_json_via_aql_zopedia_gateway
from services.inference_policy import execute_with_fallback, policy

client = llm.load_llm_client()
if not client:
    raise RuntimeError("Managed-identity provider credential was not resolved")
client = llm.client_from_config(replace(client.config,
    model=os.environ["EXPECTED_FULL_MODEL"], thinking_mode="enabled", reasoning_effort="high"))
activation = os.environ.get("QUALIFICATION_ACTIVATION_DATE", "")
if activation:
    os.environ["LLM_POLICY_NOT_BEFORE"] = activation
    print(json.dumps({"probe": "activation", "date_pacific": datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat(),
                      "not_before": activation, "effective_profile": policy()["profile"]}), flush=True)
    os.environ["LLM_POLICY_NOT_BEFORE"] = ""
context = SimpleNamespace(name="news-ingest-and-features", run_id=uuid.uuid4().hex,
                          started_at_monotonic=time.monotonic(), degraded_components={})
schema = {"type": "object", "additionalProperties": False,
          "properties": {"summary": {"type": "string"},
                         "open_questions": {"type": "array", "items": {"type": "string"}}},
          "required": ["summary", "open_questions"]}


def handler(context, connection):
    active = policy()["profile"]
    tasks = [("pipeline.ticker_business_model_stack.research_plan", "agent.research_step")]
    if active == "baseline":
        tasks = [("pipeline.ticker_business_model_stack", "ticker_business_model_stack.direct")]
    for surface, purpose in tasks:
        result = generate_json_via_aql_zopedia_gateway(llm_client=client, surface=surface, purpose=purpose,
            call_type="admin_probe", system_prompt="Summarize the supplied observations and preserve their reporting periods. Return JSON.",
            user_prompt=os.environ["PROBE_EVIDENCE_JSON"], schema_name="inference_deployment_probe", schema=schema,
            deadline_monotonic=time.monotonic() + 240, metadata={"qualification": True})
        print(json.dumps({"probe": "provider", "profile": active, "payload": result,
                          "response_metadata": getattr(result, "response_metadata", {})}), flush=True)
    if active == "balanced":
        print(json.dumps({"probe": "intentional_candidate_failure", "execution": os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME")}), flush=True)
        raise RuntimeError("Intentional development-only failure to qualify the real Azure retry")
    print(json.dumps({"probe": "fallback_completed", "profile": active,
                      "execution": os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME")}), flush=True)
    return {"profile": active}


execute_with_fallback(handler, context, None)
