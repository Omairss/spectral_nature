"""Prospective forecasting through the full shared Zopedia research engine."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


QUANTILES = {"type": "object", "properties": {k: {"type": "number"} for k in ["p10", "p50", "p90"]},
             "required": ["p10", "p50", "p90"], "additionalProperties": False}
SCHEMA = {"type": "object", "properties": {
    "hypotheses": {"type": "array", "items": {"type": "string"}},
    "forecasts": {"type": "object", "properties": {
        "annual_revenue_usd": QUANTILES, "stock_total_return": QUANTILES,
        "probability_outperform_spy": {"type": "number", "minimum": 0, "maximum": 1}},
        "required": ["annual_revenue_usd", "stock_total_return", "probability_outperform_spy"], "additionalProperties": False},
    "method_and_assumptions": {"type": "string"},
    "source_ids": {"type": "array", "items": {"type": "string"}},
    "analysis_run_ids": {"type": "array", "items": {"type": "string"}}},
    "required": ["hypotheses", "forecasts", "method_and_assumptions", "source_ids", "analysis_run_ids"],
    "additionalProperties": False}
INSTRUCTIONS = (
    "Investigate the business using only the frozen sources and datasets. Form competing hypotheses and say what "
    "would change your mind. Choose appropriate statistical, ML or financial methods and execute useful calculations. "
    "Keep observations distinct from assumptions. Check historical performance using only earlier data to predict later "
    "observations; keep the future targets untouched. Return forecasts with meaningful uncertainty and reconcile them "
    "with actual program results. Be concise. Use USD for annual revenue and fractions for returns/probabilities."
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--deployment-env", type=Path, required=True)
    parser.add_argument("--tickers", nargs="+", default=["HIMS"])
    parser.add_argument("--output-directory", default="predictions")
    parser.add_argument("--attempt-label", default="first", choices=["first", "recovery"])
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text())
    actor = args.root / "actor"
    if digest(actor / "manifest.json") != protocol["actor_manifest_sha256"]:
        raise ValueError("Frozen forecast manifest changed")
    manifest = json.loads((actor / "manifest.json").read_text())
    for name, key in [("sources.json", "sources_sha256"), ("tasks.json", "tasks_sha256")]:
        if digest(actor / name) != manifest[key]:
            raise ValueError("Frozen actor source/task content changed")
    if digest(Path(__file__)) != protocol["runner_sha256"]:
        raise ValueError("Frozen forecast runner changed")
    app = Path(protocol["app"])
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=app, text=True).strip() != protocol["source_git"]:
        raise ValueError("Frozen research engine source changed")
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=app, text=True):
        raise ValueError("Frozen research source is modified")
    os.environ["DEPLOYMENT_ENV_FILE"] = str(args.deployment_env.resolve())
    sys.path.insert(0, str(app))
    import pandas as pd
    from data_access import DataAccessLayer, QueryService
    from data_access.contracts import DataProvenance, ResolvedPayload
    from services import llm
    from services.agent_tools import build_tool_registry
    from services.aql_zopedia_engine import research_engine_revision, run_aql_zopedia_structured_agent
    from services.llm_execution import LocalResearchExecution, model_call_context
    if research_engine_revision() != protocol["engine_revision"]:
        raise ValueError("Loaded engine identity changed")
    sources = json.loads((actor / "sources.json").read_text())
    tasks = {task["ticker"]: task for task in json.loads((actor / "tasks.json").read_text())}
    data_receipts = {record["dataset"]: record for record in manifest["dataset_receipts"]}
    outputs = (args.root / args.output_directory).resolve()
    if outputs.is_relative_to(actor.resolve()):
        raise ValueError("Predictions must remain outside analyst inputs")
    outputs.mkdir(exist_ok=True)
    execution = LocalResearchExecution(args.root / "accounting", overrides=protocol["runtime_overrides"],
        budget_usd=protocol["budget_usd"], input_usd_per_million=protocol["input_usd_per_million"],
        output_usd_per_million=protocol["output_usd_per_million"],
        request_max_output_tokens=protocol["completion_tokens"],
        max_provider_attempts=protocol["max_provider_attempts"], retain_training_io=True,
        unknown_charge_policy=protocol.get("unknown_charge_policy", "halt"))

    @dataclass(frozen=True)
    class FrozenService(QueryService):
        approved: dict = field(default_factory=dict)

        def list_capabilities(self):
            return {"datasets": {name: {
                "description": (f"Frozen decision-time {name}; {data_receipts[name]['rows']} complete rows. "
                                "Facts contain namespace, concept, label, unit, val, start, end, filed, form, fy, fp, accn, frame. "
                                "Concepts describe available facts. Prices contain date, OHLC, adjusted_close, volume. "
                                "Optional concepts/forms/unit filters select explicitly; omitted filters preserve all rows. "
                                "Use this name in analysis.run_python dataset_refs to compute on full DataFrames."),
                "param_schema": {"type": "object", "properties": {
                    "concepts": {"type": "array", "items": {"type": "string"}},
                    "forms": {"type": "array", "items": {"type": "string"}},
                    "unit": {"type": "string"}}, "additionalProperties": False}}
                for name in self.approved}, "charts": {}}

        def fetch_dataset(self, name, params=None):
            if name not in self.approved:
                raise ValueError("Only this task's frozen dataset names are available")
            params = params or {}
            if set(params) - {"concepts", "forms", "unit"}:
                raise ValueError("Unsupported dataset filter")
            frame = pd.DataFrame(self.approved[name])
            for parameter, column in [("concepts", "concept"), ("forms", "form")]:
                if params.get(parameter):
                    if column not in frame:
                        raise ValueError("Filter does not apply to this dataset")
                    frame = frame[frame[column].isin(params[parameter])]
            if params.get("unit"):
                if "unit" not in frame:
                    raise ValueError("Unit filter does not apply to this dataset")
                frame = frame[frame["unit"] == params["unit"]]
            return ResolvedPayload(frame.copy(), DataProvenance(
                mode="frozen_decision_time", datasets=(name,),
                details={"sha256": data_receipts[name]["sha256"], "rows": len(frame),
                         "decision_cutoff_utc": manifest["frozen_at_utc"], "filters": params}))

    for ticker in args.tickers:
        task = tasks[ticker]
        deadline = datetime.fromisoformat(task["issue_deadline_utc"])
        if datetime.now(timezone.utc) >= deadline:
            raise ValueError("Prospective issue window closed; do not backdate forecasts")
        output = outputs / f"{ticker}.json"
        if output.exists():
            print(json.dumps({"ticker": ticker, "status": "already_recorded_first_attempt"}), flush=True)
            continue
        approved = {}
        for name in task["available_datasets"]:
            path = actor / manifest["datasets"][name]
            if digest(path) != data_receipts[name]["sha256"]:
                raise ValueError("Frozen numerical observations changed")
            approved[name] = json.loads(path.read_text())
        service = FrozenService(DataAccessLayer(), supplied_evidence=sources, approved=approved)
        names = ["tools.search", "system.capabilities", "research.list_supplied_evidence",
                 "research.read_supplied_evidence", "analysis.run_python", "analysis.read_raw_output"]
        names += ["dataset." + name for name in approved]
        catalog = build_tool_registry(service).catalog()
        if set(names) - {tool["name"] for tool in catalog}:
            raise ValueError("A promised source/data/compute capability is absent")
        # Exercise the real evidence/data interfaces before any inference.
        lead = service.read_supplied_evidence(source_id=task["lead_source_id"], max_characters=60000)
        if not lead["text"] or lead["text"] == "null":
            raise ValueError("Lead original is not readable through the shared evidence owner")
        probe = {name: len(service.fetch_dataset(name).payload) for name in approved}
        query = (
            f"As of {task['decision_cutoff_utc']}, investigate {task['company']} ({ticker}), starting with "
            f"source {task['lead_source_id']}. All259 retained articles are available through the supplied-evidence reader. "
            "Form competing hypotheses, choose and execute useful models, and forecast the specified future outcomes. "
            "Use your preferred appropriate statistics/ML; explain key assumptions and falsifiers. "
            "Frozen numerical dataset names: " + ", ".join(approved) + ".\n"
            "Targets: " + json.dumps(task["targets"]) + "\n"
            "Python computation supports numpy, pandas, scipy and sklearn; dataset_refs load full named frames "
            "without copying source rows into code. No future target outcomes are available."
        )
        started = time.monotonic()
        result, error = None, None
        run_id = "agrun_sa_future_20261022_" + ticker
        if args.attempt_label != "first":
            run_id += "_" + args.attempt_label
        with execution.activate(), model_call_context(run_id=run_id):
            try:
                result = run_aql_zopedia_structured_agent(
                    query=query, instructions=INSTRUCTIONS, schema_name="prospective_financial_forecast", schema=SCHEMA,
                    task="prospective_financial_forecast", surface="research.quantitative.prospective",
                    service=service, llm_client=llm.load_llm_client(), force_refresh=True,
                    max_tool_calls=32, review_answer=True, persist_findings=False, write_policy="none",
                    run_timeout_seconds=3600, llm_step_timeout_seconds=900, tool_call_timeout_seconds=150,
                    initial_evidence=[{"title": "Controlled decision-time source collection", **service.supplied_evidence_manifest,
                                       "available_datasets": service.list_capabilities()["datasets"]}],
                    call_type="research_grade", request_context={"allow_web": False, "allowed_tool_names": names,
                        "allowed_source_access": ["supplied", "retained_or_data"], "timezone": "America/Los_Angeles"},
                    progress_callback=lambda event: execution.record("progress", {"ticker": ticker, "event": event}))
            except Exception as exc:
                error = {"type": type(exc).__name__, "message": str(exc)}
        issued = datetime.now(timezone.utc)
        record = {"ticker": ticker, "task_id": task["id"], "run_id": run_id, "attempt_label": args.attempt_label,
                  "issued_at_utc": issued.isoformat(), "before_issue_deadline": issued < deadline,
                  "decision_manifest_sha256": protocol["actor_manifest_sha256"],
                  "protocol_sha256": digest(args.protocol), "source_manifest": service.supplied_evidence_manifest,
                  "complete_input_row_counts": probe, "result": result, "error": error,
                  "elapsed_seconds": time.monotonic() - started, "outcome_status": "pending_future_observation"}
        with output.open("x") as handle:
            json.dump(record, handle, indent=2, ensure_ascii=False, default=str)
        (args.root / "accounting-summary.json").write_text(json.dumps(execution.summary(), indent=2) + "\n")
        print(json.dumps({"ticker": ticker, "status": (result or {}).get("status"), "error": error,
                          "before_issue_deadline": issued < deadline, "elapsed_seconds": record["elapsed_seconds"],
                          "accounting": execution.summary()}), flush=True)
        if execution.summary().get("inference_blocked_by_unknown_charge", bool(execution.summary().get("unknown_attempts"))) or (error and error["type"] in {"ResearchSpendExceeded", "LLMProviderUnavailable"}):
            break


if __name__ == "__main__":
    main()
