"""Production change qualification; retain outputs for direct human review.

Uses captured production source inputs. The perturbations are explicitly
fictional acceptance scenarios, never written to the product's datasets/memory.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import os
import sys
import time

APP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(APP))
ROOT = Path.home() / "Library/Application Support/SpectralNature/operations"
OUT = ROOT / "inference-rollout-2026-10-27"
captured = json.loads((ROOT / "deepseek-cost-audit-2026-10-26/news-job.json").read_text())
for item in captured["properties"]["template"]["containers"][0]["env"]:
    if item.get("value") is not None:
        os.environ[item["name"]] = item["value"]
os.environ.update(APP_TRACK="cost-qualification-20261027", PIPELINE_JOB_NAME="production-cost-qualification",
                  LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE="", LLM_TIMEOUT_SECONDS="600",
                  AQL_ZOPEDIA_AGENT_RUN_TIMEOUT_SECONDS="900")
# Explicit path, independent of the Git worktree's missing deployment outputs.
os.environ["DEPLOYMENT_ENV_FILE"] = str(Path.home() / "Documents/code/spectral_nature/streamlit_alpaca_app/infra/.generated/deployment.local.env")

import pandas as pd
from services import llm
from services.aql import business_model_stack as company

client = llm.load_llm_client()
assert client and client.config.provider == "deepseek"
client = llm.client_from_config(replace(client.config, model="deepseek-v4-pro", thinking_mode="enabled", reasoning_effort="high"))
stacks = json.loads((OUT / "zopedia_ticker_business_model_stacks.json").read_text())
sources = json.loads((OUT / "zopedia_business_model_search_results.json").read_text())
fundamentals = pd.DataFrame(json.loads((OUT / "quarterly_fundamentals.json").read_text()))


def save(name, value):
    path = OUT / (os.getenv("QUALIFICATION_PREFIX", "") + name + ".json")
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")
    path.chmod(0o600)


def dossier(symbol):
    row = next(r for r in stacks if r["symbol"] == symbol)
    records = [r for r in sources if r["symbol"] == symbol]
    kwargs = dict(zopedia_agent_runner=None, llm_client=client, symbol=symbol,
        company_name=row["company_name"], query_plan=json.loads(row["research_query_plan_json"]),
        baseline={"symbol": symbol, "company_name": row["company_name"]},
        fundamentals=company._fundamental_rows(fundamentals, symbol, limit=10), edgar_rows=[], recent_news_rows=[],
        zopedia_pages=[], initial_slot_facts={}, business_research_evidence={}, combined_research_rows=records)
    started = time.monotonic()
    result, warnings = company._zopedia_research_dossier(**kwargs)
    value = {"symbol": symbol, "profile": os.environ["LLM_POLICY_PROFILE"], "result": result,
             "warnings": warnings, "seconds": time.monotonic() - started, "source_rows": len(records)}
    save("dossier-" + value["profile"] + "-" + symbol, value)
    print(json.dumps({k: value[k] for k in ["symbol", "profile", "warnings", "seconds", "source_rows"]}), flush=True)


sections = json.loads(os.getenv("QUALIFICATION_SECTIONS", '["dossiers", "refresh"]'))
for profile in (json.loads(os.getenv("QUALIFICATION_PROFILES", '["baseline", "balanced"]')) if "dossiers" in sections else []):
    os.environ["LLM_POLICY_PROFILE"] = profile
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(dossier, json.loads(os.getenv("QUALIFICATION_SYMBOLS", '["MRNA", "TEAM", "XP"]'))))


def refresh(case):
    symbol, name, change = case
    row = next(r for r in stacks if r["symbol"] == symbol)
    previous = {"company_name": row["company_name"], "observed_research": json.loads(row["research_dossier_json"])}
    current = deepcopy(previous)
    current["new_input"] = change
    result = company._assess_company_refresh(previous_inputs=previous, current_inputs=current,
        retained_analysis=row, symbol=symbol, original_asof=row["asof_time_utc"], client=client)
    save("refresh-" + symbol + "-" + name, {"case": case, "result": result})
    print(json.dumps({"symbol": symbol, "case": name, "status": result.get("status"), "payload": result.get("payload")}), flush=True)


cases = [
    ("MRNA", "duplicate", "A new syndicated article repeats the previously reported Phase 3 results exactly. No new observation or source correction."),
    ("MRNA", "material", "Company reports that a previously unreported safety signal has paused the cancer therapy trial."),
    ("MRNA", "correction", "The original company source corrects the recurrence hazard ratio from 0.58 to 0.78; all other figures unchanged."),
    ("TEAM", "duplicate", "A second outlet restates the same Q4 FY26 revenue, Cloud growth and RPO numbers already in the retained company analysis. No new facts."),
    ("TEAM", "earnings", "A newly released Q1 FY27 report shows Cloud revenue growth slowing to 12% and full-year revenue guidance cut by 15%."),
    ("TEAM", "unresolved", "A credible new report alleges an undisclosed error in reported RPO; the company has not responded and the underlying filing is not yet accessible."),
    ("XP", "duplicate", "A fresh publication date is attached to an unchanged copy of the prior earnings release, with identical reporting period and figures."),
    ("XP", "material", "The regulator announces a new rule restricting XP's main distribution commissions starting next month."),
]
if "refresh" in sections:
    os.environ["LLM_POLICY_PROFILE"] = "balanced"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(refresh, cases))


if "narrative" in sections:
    os.environ["LLM_POLICY_PROFILE"] = "balanced"
    row = next(r for r in stacks if r["symbol"] == "TEAM")
    dossier_result = json.loads((OUT / "final-dossier-balanced-TEAM.json").read_text())["result"]
    result = company._zopedia_stack_synthesis(zopedia_agent_runner=None, llm_client=client,
        symbol="TEAM", company_name=row["company_name"], baseline={"symbol": "TEAM", "company_name": row["company_name"]},
        fundamentals=company._fundamental_rows(fundamentals, "TEAM", limit=10), edgar_rows=[], recent_news_rows=[],
        zopedia_pages=[], research_dossier=dossier_result, business_research_evidence={}, initial_slot_facts={}, slot_gaps=[])
    save("final-company-analysis-TEAM", result)
    print(json.dumps({"narrative": "TEAM", "result": result}), flush=True)


if "planning" in sections:
    os.environ["LLM_POLICY_PROFILE"] = "balanced"
    row = next(r for r in stacks if r["symbol"] == "TEAM")
    from services.llm_execution import research_work_context
    os.environ["LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE"] = "production-cost-qualification"
    prior = {"company:TEAM:ticker_business_model_research_dossier": {
        "payload": json.loads(row["research_dossier_json"]), "created_at_utc": row["asof_time_utc"]}}
    with research_work_context("qualify-planner-with-retained-research", prior=prior):
        result, warnings = company._zopedia_research_query_plan(zopedia_agent_runner=None, llm_client=client,
            symbol="TEAM", company_name=row["company_name"], baseline={"symbol": "TEAM", "company_name": row["company_name"]},
            fundamentals=company._fundamental_rows(fundamentals, "TEAM", limit=10), edgar_rows=[], recent_news_rows=[],
            zopedia_pages=[], initial_slot_facts=json.loads(row["slot_facts_json"]), max_queries=24)
    save("final-acquisition-plan-TEAM", {"queries": result, "warnings": warnings})
    print(json.dumps({"plan": "TEAM", "queries": result, "warnings": warnings}), flush=True)
