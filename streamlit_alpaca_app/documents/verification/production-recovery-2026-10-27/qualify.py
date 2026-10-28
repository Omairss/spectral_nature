"""Replay captured production inputs for personal review; no product publication.

This offline comparison uses Lambda on-demand GPUs under the owner's shared
Pacific-day research allowance. Production deployment approval does not waive
that policy. Outputs stay private and are personally reviewed in full.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import sys
import time
from urllib.parse import urlparse

# Explicit, already-budgeted Lambda endpoints are required. Never inherit a
# paid production inference endpoint for an offline comparison.
lambda_endpoints = {}
for role in ("FULL", "ECONOMY"):
    endpoint = os.getenv(f"QUALIFICATION_LAMBDA_{role}_BASE_URL", "").strip()
    model = os.getenv(f"QUALIFICATION_LAMBDA_{role}_MODEL", "").strip()
    host = urlparse(endpoint).hostname or ""
    if not endpoint or not model or host in {"api.deepseek.com", "api.openai.com"}:
        raise SystemExit(f"Declare the budgeted Lambda {role.lower()} endpoint/model before qualification; see AGENTS.md.")
    lambda_endpoints[role] = (endpoint, model)

APP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(APP))
OUT = Path.home() / "Library/Application Support/SpectralNature/operations/production-recovery-2026-10-27"
jobs = json.loads((OUT / "jobs.before.json").read_text())
job = next(row for row in jobs if row["name"] == "attention-home-build")
for item in job["properties"]["template"]["containers"][0]["env"]:
    if item.get("value") is not None:
        os.environ[item["name"]] = item["value"]
os.environ.update(
    DEPLOYMENT_ENV_FILE=str(Path.home() / "Documents/code/spectral_nature/streamlit_alpaca_app/infra/.generated/deployment.local.env"),
    APP_TRACK="production-recovery-qualification-20261027", PIPELINE_JOB_NAME="production-recovery-qualification",
    LLM_PROVIDER="deepseek", LLM_BASE_URL=lambda_endpoints["FULL"][0],
    LLM_MODEL=lambda_endpoints["FULL"][1], LLM_API_KEY="lambda-research-endpoint", LLM_TIMEOUT_SECONDS="480",
    LLM_CONTEXT_WINDOW_TOKENS="1048576",
    AQL_ZOPEDIA_AGENT_RUN_TIMEOUT_SECONDS="900", AQL_ZOPEDIA_AGENT_LLM_STEP_TIMEOUT_SECONDS="480",
    ATTENTION_HOME_ZOPEDIA_AGENT_LLM_TIMEOUT_SECONDS="480",
    ATTENTION_ZOPEDIA_WRITE_POLICY="none",
    LLM_POLICY_PROFILE="balanced", LLM_POLICY_NOT_BEFORE="2026-10-27",
    LLM_POLICY_JSON=(APP / "config/inference_policy.json").read_text(),
    LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE="",
)
for production_identity in ("PIPELINE_RUN_ID", "RESEARCH_RUN_BUDGET_USD", "CONTAINER_APP_JOB_EXECUTION_NAME"):
    os.environ.pop(production_identity, None)
document = json.loads(os.environ["LLM_POLICY_JSON"])
for profile in document["profiles"].values():
    if "economy" in profile.get("models", {}):
        profile["models"]["economy"] = {"env_prefix": "QUALIFICATION_ECONOMY_"}
document["best_effort_model"] = {"env_prefix": "QUALIFICATION_ECONOMY_"}
os.environ.update(QUALIFICATION_ECONOMY_LLM_PROVIDER="deepseek",
    QUALIFICATION_ECONOMY_LLM_BASE_URL=lambda_endpoints["ECONOMY"][0],
    QUALIFICATION_ECONOMY_LLM_MODEL=lambda_endpoints["ECONOMY"][1],
    QUALIFICATION_ECONOMY_LLM_API_KEY="lambda-research-endpoint",
    LLM_POLICY_JSON=json.dumps(document))

from pipeline.jobs import attention_home_build as home
from services import llm
from services.aql import events

payload = json.loads((OUT / "frozen-home-payload.json").read_text())
bundles = json.loads((OUT / "frozen-home-bundles.json").read_text())
documents = json.loads((OUT / "attention_source_documents.json").read_text())
intake = [row for row in documents if row.get("document_id", "").startswith("doc::event-news::")]
asof = "2026-10-27T14:50:47.370546+00:00"


def save(name, value):
    path = OUT / (name + ".json")
    path.write_text(json.dumps(value, default=str, indent=2) + "\n")
    path.chmod(0o600)


def editor():
    started = time.monotonic()
    revised, revised_bundles, review = home._review_home_public_surface(
        payload, bundles, enrichment_rows=[], asof_time_utc=asof,
        timeout_seconds=600, source_documents=intake,
    )
    save("candidate-home-editor", {"payload": revised, "bundles": revised_bundles,
                                  "review": review, "seconds": time.monotonic() - started})
    print(json.dumps({"section": "home_editor", "status": review.get("status"),
                      "summary": revised.get("homepage_summary"), "seconds": time.monotonic() - started}), flush=True)


def enrichment():
    # Revisit an actual timed-out morning subject using the complete captured
    # source intake and dated retained company context. No acquisition tools.
    symbol = os.getenv("QUALIFICATION_SYMBOL", "NBIS")
    context = [bundle for bundle in bundles.values() if symbol in (bundle.get("supporting_symbols") or [])
               or bundle.get("symbol") == symbol]
    started = time.monotonic()
    result = home._run_single_zopedia_enrichment(
        symbol, timeout_seconds=600, asof_time_utc=asof,
        initial_evidence=[{"kind": "source_intake", "records": intake},
                          {"kind": "dated_fallible_research", "bundles": context}],
    )
    save("candidate-enrichment-" + symbol, {"result": result, "seconds": time.monotonic() - started})
    print(json.dumps({"section": "enrichment", "symbol": symbol, "status": result["status"],
                      "answer": result["answer_markdown"], "seconds": time.monotonic() - started}), flush=True)


def nomination():
    # Stop after the real nomination call, before downstream acquisition. This
    # isolates the failed allocation stage without starting fresh web research.
    import pandas as pd
    original = events.run_aql_zopedia_structured_agent
    class CapturedNomination(Exception):
        pass
    def capture(**kwargs):
        started = time.monotonic()
        result = original(**kwargs)
        save("candidate-source-nomination", {"result": result, "seconds": time.monotonic() - started})
        print(json.dumps({"section": "source_nomination", "status": result.get("status"),
                          "payload": result.get("payload"), "seconds": time.monotonic() - started}), flush=True)
        raise CapturedNomination()
    events.run_aql_zopedia_structured_agent = capture
    source_frame = pd.DataFrame([{
        "headline": row["title"], "url": row["url"], "content": row["raw_text"],
        "published_at": row["published_at"], "symbols": row.get("provider_symbols"),
        "source": row.get("source_provider"),
    } for row in intake])
    try:
        events.research_source_events(source_frame, market_frame=pd.DataFrame(), universe_frame=None,
                                     asof_time_utc=asof, run_id="recovery-qualification", llm_client=llm.load_llm_client())
    except CapturedNomination:
        pass
    finally:
        events.run_aql_zopedia_structured_agent = original


def news_interpretation():
    import pandas as pd
    from services.aql import news_business_resolution as news
    from services.aql_zopedia_engine import run_aql_zopedia_structured_agent
    source = next(row for row in intake if row['document_id'] == 'doc::event-news::7501919bb69ba1f801d9')
    retained = json.loads((OUT.parent / 'inference-rollout-2026-10-27/zopedia_ticker_business_model_stacks.json').read_text())
    stack = next((row for row in retained if row.get('symbol') == 'NBIS'), {})
    row = pd.Series({'headline': source['title'], 'content': source['raw_text'],
                    'url': source['url'], 'published_at': source['published_at']})
    def frozen_runner(**kwargs):
        # Qualify synthesis on captured inputs, without new web acquisition.
        kwargs.update(max_tool_calls=0, run_timeout_seconds=480,
                      request_context={'allowed_source_access': ['supplied']})
        return run_aql_zopedia_structured_agent(**kwargs)
    started = time.monotonic()
    result = news._zopedia_resolution(zopedia_agent_runner=frozen_runner, llm_client=llm.load_llm_client(),
        symbol='NBIS', company_name=stack.get('company_name', ''), news_row=row,
        baseline={}, fundamentals=[], edgar_rows=[], zopedia_pages=[], business_memory_pages=[],
        business_model_stack=stack, initial_slot_facts={}, data_gaps=[], surface='pipeline.news_business_resolution')
    save('candidate-news-interpretation', {'result': result, 'seconds': time.monotonic()-started})
    print(json.dumps({'section': 'news_interpretation', 'result': result,
                      'seconds': time.monotonic()-started}), flush=True)


sections = json.loads(os.getenv("QUALIFICATION_SECTIONS", '["editor", "enrichment", "nomination"]'))
operations = {"editor": editor, "enrichment": enrichment, "nomination": nomination,
              "news": news_interpretation}
with ThreadPoolExecutor(max_workers=3) as pool:
    for future in [pool.submit(operations[name]) for name in sections]:
        future.result()
