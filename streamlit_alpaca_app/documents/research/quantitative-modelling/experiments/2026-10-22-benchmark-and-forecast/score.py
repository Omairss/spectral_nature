"""Evaluator-only scoring with pinned publisher implementations.

This process never calls a model. It opens the evaluator package only after
predictions have been retained. Native reference agreement is kept separate
from personal financial review and subsequent forecast outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from datetime import datetime, timezone


def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--public-output", type=Path)
    args = parser.parse_args()
    raw = args.root / "external"
    sys.path.insert(0, str(args.root / "scoring-dependencies"))
    finqa = module("native_finqa_metric", raw / "finqa-evaluate.py")
    conv = module("native_convfinqa_metric", raw / "convfinqa-general-utils.py")
    module("tatqa_utils", raw / "tatqa-utils.py")
    tatqa = module("native_tatqa_metric", raw / "tatqa-metric.py")
    references = [json.loads(line) for line in (args.package / "evaluator/references.jsonl").read_text().splitlines()]
    reference_by_key = {(x["benchmark"], x["id"]): x for x in references}
    predictions = {}
    unreadable = []
    for path in sorted((args.package / "predictions").rglob("turn-*.json")):
        try:
            row = json.loads(path.read_text())
        except json.JSONDecodeError:
            unreadable.append(str(path.relative_to(args.package)))
            continue
        predictions[(row["benchmark"], row["id"], row["turn"])] = row
    totals = {"finqa": sum(r["benchmark"] == "finqa" for r in references),
              "tatqa": sum(r["benchmark"] == "tatqa" for r in references),
              "convfinqa": sum(len(r["record"]["annotation"]["dialogue_break"]) for r in references if r["benchmark"] == "convfinqa")}
    details = []
    summary = {}
    for benchmark, metric in [("finqa", finqa), ("convfinqa", conv)]:
        if not totals[benchmark]:
            continue
        results = []
        for (name, identity, turn), prediction in predictions.items():
            if name != benchmark:
                continue
            reference = reference_by_key[(name, identity)]["record"]
            payload = (prediction.get("result") or {}).get("payload") or {}
            program = payload.get("program", "")
            gold = reference["qa"]["exe_ans"] if name == "finqa" else reference["annotation"]["exe_ans_list"][turn]
            error, calculated, valid = None, None, False
            try:
                tokens = metric.program_tokenization(program)
                invalid, calculated = metric.eval_program(tokens, reference["table"])
                valid = invalid == 0
            except Exception as exc:
                error = str(exc)
            correct = valid and calculated == gold
            row = {"benchmark": benchmark, "id": identity, "turn": turn, "program": program,
                   "valid_program": valid, "calculated_value": calculated, "reference_value": gold,
                   "native_execution_match": correct, "native_metric_error": error,
                   "model_status": (prediction.get("result") or {}).get("status")}
            results.append(row); details.append(row)
        count = len(results)
        correct = sum(row["native_execution_match"] for row in results)
        summary[benchmark] = {"expected_questions_or_turns": totals[benchmark], "attempted": count,
                              "correct_native_executions": correct, "valid_programs": sum(r["valid_program"] for r in results),
                              "accuracy_on_attempted": correct / count if count else None,
                              "accuracy_on_full_split_missing_as_unanswered": correct / totals[benchmark],
                              "coverage": count / totals[benchmark], "inference_complete": count == totals[benchmark],
                              "metric": "Pinned publisher program tokenization/execution; exact comparison to native execution value. No semantic program-equivalence metric claimed."}
        if benchmark == "convfinqa":
            expected_conversations = sum(r["benchmark"] == benchmark for r in references)
            attempted_conversations = len({r["id"] for r in results})
            summary[benchmark].update(expected_conversations=expected_conversations,
                attempted_conversations=attempted_conversations,
                all_conversations_attempted=attempted_conversations == expected_conversations)
    attempted_tatqa = tatqa.TaTQAEmAndF1()
    full_tatqa = tatqa.TaTQAEmAndF1()
    count = 0
    for reference in references:
        if reference["benchmark"] != "tatqa":
            continue
        prediction = predictions.get(("tatqa", reference["id"], 0))
        payload = ((prediction or {}).get("result") or {}).get("payload") or {}
        answer, scale = payload.get("answer"), payload.get("scale")
        full_tatqa(reference["question"], answer, scale)
        if prediction is not None:
            count += 1
            attempted_tatqa(reference["question"], answer, scale)
    if totals["tatqa"]:
        attempted = attempted_tatqa.get_overall_metric() if count else (None,) * 4
        full = full_tatqa.get_overall_metric()
        summary["tatqa"] = {"expected_questions_or_turns": totals["tatqa"], "attempted": count,
                         "coverage": count / totals["tatqa"], "inference_complete": count == totals["tatqa"],
                         "attempted_metrics": dict(zip(["exact_match", "f1", "scale"], attempted[:3])),
                         "full_denominator_metrics_missing_as_unanswered": dict(zip(["exact_match", "f1", "scale"], full[:3])),
                         "metric": "Pinned publisher TaTQAEmAndF1; matching official labeled-test input/reference snapshot. Operation accuracy is not claimed because the answer schema does not request an operation label."}
    result = {"scored_at_utc": datetime.now(timezone.utc).isoformat(), "benchmarks": summary,
              "scope": "Current frozen native model-only condition. Partial coverage is not full-benchmark completion. Reference agreement does not certify financial reasoning or forecasting skill.",
              "unreadable_in_progress_files": unreadable,
              "metric_sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [raw / "finqa-evaluate.py", raw / "convfinqa-general-utils.py", raw / "tatqa-metric.py", raw / "tatqa-utils.py"]}}
    (args.package / "evaluator/native-score-details.json").write_text(json.dumps(details, indent=2, default=str) + "\n")
    (args.package / "evaluator/native-scores.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    if args.public_output:
        args.public_output.write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
