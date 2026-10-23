"""Native model-only benchmark runs through the shared AQL/Zopedia gateway.

Preparation and inference are separate processes. Inference opens only the
actor package; gold records are written to a separate evaluator directory.
Tool-using forecast investigations use the full shared research engine instead.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


FINQA_INSTRUCTIONS = (
    "Answer the financial question using only the provided report text and tables. "
    "Use the correct financial quantity, period and units. Return an answer and a FinQA calculation program. "
    "Program operations: add, subtract, multiply, divide, exp, greater, table_sum, table_average, "
    "table_max, table_min. Earlier operation results are #0, #1, etc. "
    "Write a flat comma-separated sequence of calls, for example subtract(8, 5), divide(#0, 5); "
    "do not nest calls or assign variables. For program outputs, represent percentage rates as decimal ratios; "
    "the answer string can display percent units. Return only the requested JSON."
)
TATQA_INSTRUCTIONS = (
    "Answer the question using only the provided financial table and paragraphs. "
    "Return the answer as a list of answer strings and its scale: empty, thousand, million, billion or percent. "
    "For a numeric answer, put the numeric value in the list and its scale in the separate field. "
    "For a span or list answer, preserve the relevant source text. Return only the requested JSON."
)
CONVFINQA_INSTRUCTIONS = (
    "Answer the current financial question using the report and previous model answers. "
    "Use the requested financial quantity, period and units. Return an answer and a flat comma-separated "
    "calculation program. Operations: add, subtract, multiply, divide, exp, greater, table_sum, "
    "table_average, table_max, table_min; earlier results are #0, #1, etc. "
    "Do not nest calls or assign variables. Native percentage-rate programs normally return decimal ratios; "
    "answer text may display percent. When a follow-up explicitly asks to convert an already supplied decimal "
    "to percentage units, execute that conversion. Return only the requested JSON."
)
FINANCEBENCH_INSTRUCTIONS = (
    "Answer the financial question using the supplied original report. "
    "Use the correct financial quantity, period and units. Explain the calculation briefly. "
    "Return only the requested JSON."
)
FINQA_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string"}, "program": {"type": "string"}},
    "required": ["answer", "program"], "additionalProperties": False}
TATQA_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "array", "items": {"type": "string"}},
    "scale": {"type": "string", "enum": ["", "thousand", "million", "billion", "percent"]}},
    "required": ["answer", "scale"], "additionalProperties": False}
FINANCEBENCH_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
                       "required": ["answer"], "additionalProperties": False}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report_fields(record: dict) -> dict:
    # Retrieved/gold support rows and all QA annotations are deliberately absent.
    return {key: record[key] for key in ("pre_text", "table_ori", "table", "post_text") if key in record}


def prepare(root: Path) -> None:
    raw = root / "external"
    actor_dir, evaluator_dir = root / "actor", root / "evaluator"
    actor_dir.mkdir(exist_ok=True)
    evaluator_dir.mkdir(mode=0o700, exist_ok=True)
    actors, references, counts = [], [], {}
    finqa = json.loads((raw / "finqa-test.json").read_text())
    for row in finqa:
        actors.append({"id": row["id"], "benchmark": "finqa", "split": "test",
                       "report": report_fields(row), "questions": [row["qa"]["question"]]})
        references.append({"benchmark": "finqa", "id": row["id"], "record": row})
    counts["finqa"] = {"split": "test", "cases": len(finqa), "questions": len(finqa)}
    # The published unlabeled test and labeled test have different IDs and a
    # few different inputs. Use the released labeled snapshot's INPUT fields
    # consistently, without fuzzy joins or any answer fields in actor records.
    tatqa = json.loads((raw / "tatqa-test-gold.json").read_text())
    tatqa_ids = []
    for context in tatqa:
        report = {"table": context["table"]["table"],
                  "paragraphs": [{"order": p["order"], "text": p["text"]} for p in context["paragraphs"]]}
        for question in context["questions"]:
            tatqa_ids.append(question["uid"])
            actors.append({"id": question["uid"], "benchmark": "tatqa", "split": "test",
                           "report": report, "questions": [question["question"]]})
    gold = json.loads((raw / "tatqa-test-gold.json").read_text())
    gold_ids = []
    for context in gold:
        for q in context["questions"]:
            gold_ids.append(q["uid"])
            references.append({"benchmark": "tatqa", "id": q["uid"], "question": q})
    counts["tatqa"] = {"split": "released_labeled_test", "contexts": len(tatqa), "questions": len(tatqa_ids),
                       "published_gold_questions": len(gold_ids),
                       "input_questions_without_published_gold": len(set(tatqa_ids) - set(gold_ids)),
                       "gold_questions_without_input": len(set(gold_ids) - set(tatqa_ids)),
                       "input_source": "Input fields extracted from tatqa_dataset_test_gold.json; labels retained only in evaluator package. The separate unlabeled test has1669 questions and disjoint question IDs."}
    conv = json.loads((raw / "convfinqa-dev.json").read_text())
    for row in conv:
        questions = row["annotation"]["dialogue_break"]
        actors.append({"id": row["id"], "benchmark": "convfinqa", "split": "dev",
                       "report": report_fields(row), "questions": questions})
        references.append({"benchmark": "convfinqa", "id": row["id"], "record": row})
    counts["convfinqa"] = {"split": "dev", "conversations": len(conv),
                           "turns": sum(len(r["annotation"]["dialogue_break"]) for r in conv),
                           "reason": "Public test has no answers. Use the full public development split; model-generated earlier answers only, no gold history."}
    for path, rows in [(actor_dir / "cases.jsonl", actors), (evaluator_dir / "references.jsonl", references)]:
        if path.exists():
            raise ValueError("Retain frozen packages; select a new preparation root")
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        path.chmod(0o600)
    receipt = {"prepared_at_utc": datetime.now(timezone.utc).isoformat(), "counts": counts,
               "actor_sha256": digest(actor_dir / "cases.jsonl"),
               "evaluator_sha256": digest(evaluator_dir / "references.jsonl"),
               "actor_fields": ["id", "benchmark", "split", "report", "questions"],
               "input_policy": "Original report text/tables and questions only. No gold support indices, programs, answers, derivations, mappings or future conversation questions enter a model call."}
    (root / "prepared.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


def run(root: Path, protocol_path: Path, env: Path, benchmarks: list[str], limit: int, concurrency: int) -> None:
    protocol = json.loads(protocol_path.read_text())
    app = Path(protocol["app"])
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=app, text=True).strip() != protocol["source_git"]:
        raise ValueError("Frozen shared-engine source changed")
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=app, text=True):
        raise ValueError("Frozen shared-engine source is modified")
    if digest(Path(__file__)) != protocol["runner_sha256"]:
        raise ValueError("Frozen runner changed")
    package = root / "actor/cases.jsonl"
    if digest(package) != protocol["actor_sha256"]:
        raise ValueError("Frozen actor inputs changed")
    os.environ["DEPLOYMENT_ENV_FILE"] = str(env.resolve())
    sys.path.insert(0, str(app))
    from services import llm
    from services.aql_zopedia_engine import run_aql_zopedia_structured_agent, research_engine_revision
    from services.llm_execution import LocalResearchExecution, model_call_context
    if research_engine_revision() != protocol["engine_revision"]:
        raise ValueError("Loaded engine differs from inspected source")
    execution = LocalResearchExecution(Path(protocol["accounting_root"]), overrides=protocol["runtime_overrides"],
        budget_usd=protocol["budget_usd"], input_usd_per_million=protocol["input_usd_per_million"],
        output_usd_per_million=protocol["output_usd_per_million"],
        request_max_output_tokens=protocol["completion_tokens"],
        max_provider_attempts=protocol["max_provider_attempts"], retain_training_io=True,
        unknown_charge_policy=protocol.get("unknown_charge_policy", "halt"))
    records = [json.loads(line) for line in package.read_text().splitlines()]
    groups = []
    for benchmark in benchmarks:
        rows = [r for r in records if r["benchmark"] == benchmark]
        rows.sort(key=lambda r: hashlib.sha256(r["id"].encode()).hexdigest())
        groups.append(rows[:limit] if limit else rows)
    # Keep broad benchmark coverage progressing while long conversations run.
    selected = [rows[i] for i in range(max(map(len, groups), default=0)) for rows in groups if i < len(rows)]
    stop = threading.Event()

    def investigate(case: dict) -> dict:
        if stop.is_set():
            return {"id": case["id"], "status": "not_started"}
        case_key = hashlib.sha256(case["id"].encode()).hexdigest()[:20]
        folder = root / "predictions" / case["benchmark"] / case_key
        folder.mkdir(parents=True, exist_ok=True)
        history = []
        for turn, question in enumerate(case["questions"]):
            output = folder / f"turn-{turn:02d}.json"
            if output.exists():
                old = json.loads(output.read_text())
                if (old.get("result") or {}).get("status") != "completed":
                    return {"id": case["id"], "status": "retained_failure"}
                history.append({"question": question, "answer": old["result"]["payload"]})
                continue
            started = time.monotonic()
            instructions = {"finqa": FINQA_INSTRUCTIONS, "convfinqa": CONVFINQA_INSTRUCTIONS,
                            "tatqa": TATQA_INSTRUCTIONS, "financebench": FINANCEBENCH_INSTRUCTIONS}[case["benchmark"]]
            instructions = (protocol.get("instructions") or {}).get(case["benchmark"], instructions)
            schema = {"finqa": FINQA_SCHEMA, "convfinqa": FINQA_SCHEMA,
                      "tatqa": TATQA_SCHEMA, "financebench": FINANCEBENCH_SCHEMA}[case["benchmark"]]
            query = json.dumps({"report": case["report"], "earlier_model_answers": history,
                                "question": question}, ensure_ascii=False)
            result, error = None, None
            with execution.activate(), model_call_context(run_id=f"agrun_external_{case['benchmark']}_{case_key}_{turn}"):
                try:
                    result = run_aql_zopedia_structured_agent(query=query, instructions=instructions,
                        schema_name="external_benchmark_answer", schema=schema,
                        task="external_benchmark", surface="research.quantitative.external_benchmark",
                        llm_client=llm.load_llm_client(), max_tool_calls=0, review_answer=False,
                        persist_findings=False, write_policy="none", call_type="research_grade",
                        run_timeout_seconds=protocol["timeout_seconds"], llm_step_timeout_seconds=protocol["timeout_seconds"],
                        request_context={"allow_web": False, "allowed_tool_names": [], "allowed_source_access": ["supplied"]})
                except Exception as exc:
                    error = {"type": type(exc).__name__, "message": str(exc)}
            receipt = {"id": case["id"], "benchmark": case["benchmark"], "split": case["split"],
                       "turn": turn, "issued_at_utc": datetime.now(timezone.utc).isoformat(),
                       "protocol_sha256": digest(protocol_path), "input_sha256": hashlib.sha256(query.encode()).hexdigest(),
                       "result": result, "error": error, "elapsed_seconds": time.monotonic() - started}
            output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False, default=str) + "\n")
            if error or (result or {}).get("status") != "completed":
                summary = execution.summary()
                # Other concurrent requests being pending is normal. Retain
                # individual model failures; stop new calls for unknown charges
                # or an explicit budget/provider block.
                failure_type = (error or {}).get("type") or (result or {}).get("error_type")
                if summary.get("inference_blocked_by_unknown_charge", bool(summary.get("unknown_attempts"))) or failure_type in {"ResearchSpendExceeded", "LLMProviderUnavailable"}:
                    stop.set()
                return {"benchmark": case["benchmark"], "id": case["id"], "status": "failed", "error": error or (result or {}).get("error")}
            history.append({"question": question, "answer": result["payload"]})
        return {"benchmark": case["benchmark"], "id": case["id"], "status": "completed", "turns": len(history)}

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for future in as_completed([pool.submit(investigate, case) for case in selected]):
            print(json.dumps(future.result()), flush=True)
    (root / "accounting-summary.json").write_text(json.dumps(execution.summary(), indent=2) + "\n")
    print(json.dumps(execution.summary()), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["prepare", "run"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--deployment-env", type=Path)
    parser.add_argument("--benchmarks", nargs="+", default=["finqa", "tatqa", "convfinqa"])
    parser.add_argument("--limit", type=int, default=1, help="Distinct cases per benchmark; zero means full split")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    if args.operation == "prepare":
        prepare(args.root)
    else:
        run(args.root, args.protocol, args.deployment_env, args.benchmarks, args.limit, args.concurrency)
