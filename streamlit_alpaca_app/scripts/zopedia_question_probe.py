#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


QUESTIONS: list[dict[str, str]] = [
    {
        "id": "company_fundamentals_vs_narrative",
        "question": "Is NVDA's AI data-center thesis still backed by fundamentals, or is it mostly narrative now?",
    },
    {
        "id": "macro_yields_small_caps",
        "question": "Are lower Treasury yields currently bullish or bearish for small caps, and why might the usual relationship fail this time?",
    },
    {
        "id": "false_macro_claim",
        "question": "A news article says CPI is back above 9% and unemployment is 12%. What should I believe?",
    },
    {
        "id": "wiki_stale_power_claim",
        "question": (
            "Our wiki says data-center power constraints are irrelevant to AI infrastructure. "
            "Recent evidence says the opposite. What should change?"
        ),
    },
    {
        "id": "multi_hop_macro_market_risk",
        "question": (
            "What second-order market risks would appear if oil spikes while credit spreads widen "
            "and AI capex expectations stay high?"
        ),
    },
]


def _clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _tool_names(result: dict[str, Any]) -> list[str]:
    return [
        _clean(call.get("tool_name"))
        for call in list(result.get("tool_calls") or [])
        if isinstance(call, dict) and _clean(call.get("tool_name"))
    ]


def _status_for_run(result: dict[str, Any], *, timeout: bool = False) -> str:
    if timeout:
        return "timeout"
    if _clean(result.get("status")) != "completed":
        return "fail"
    return "completed"


def _single_question(index: int, *, max_tool_calls: int, env_file: Path) -> dict[str, Any]:
    from services.aql_zopedia_engine import run_aql_zopedia_agent

    _load_env_file(env_file)
    os.environ.setdefault("LLM_TIMEOUT_SECONDS", "90")
    item = QUESTIONS[index]
    started = time.monotonic()
    result = run_aql_zopedia_agent(
        query=item["question"],
        task="question_probe",
        surface="zopedia.question_probe",
        max_tool_calls=max_tool_calls,
        force_refresh=False,
        persist_findings=False,
    )
    elapsed = time.monotonic() - started
    tool_names = _tool_names(result)
    evidence_pack = result.get("aql_evidence_pack") or {}
    return {
        "id": item["id"],
        "question": item["question"],
        "status": _status_for_run(result),
        "elapsed_seconds": round(elapsed, 3),
        "agent_status": result.get("status"),
        "confidence": result.get("confidence"),
        "tool_names": tool_names,
        "tool_count": len(tool_names),
        "limitations": result.get("limitations") or [],
        "answer_markdown": _clean(result.get("answer_markdown")),
        "aql_evidence_pack_id": result.get("aql_evidence_pack_id"),
        "evidence_refs": {
            "trace_count": len(evidence_pack.get("trace") or []),
            "zopedia_pages": len(evidence_pack.get("zopedia_pages") or []),
            "source_links": len(evidence_pack.get("source_links") or []),
        },
    }


def _run_parent(args: argparse.Namespace) -> dict[str, Any]:
    env_file = Path(args.env_file)
    _load_env_file(env_file)
    run_id = args.tag or f"zopedia-question-probe-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    started_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(QUESTIONS):
        print(f"[zopedia-question-probe] {index + 1}/{len(QUESTIONS)} {item['id']}", flush=True)
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--single-index",
            str(index),
            "--max-tool-calls",
            str(args.max_tool_calls),
            "--env-file",
            str(env_file),
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=args.question_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            rows.append(
                {
                    "id": item["id"],
                    "question": item["question"],
                    "status": "timeout",
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "tool_names": [],
                    "tool_count": 0,
                    "limitations": [f"Question timed out after {args.question_timeout_seconds}s."],
                    "answer_markdown": "",
                    "stderr": "",
                }
            )
            continue
        if completed.returncode != 0:
            rows.append(
                {
                    "id": item["id"],
                    "question": item["question"],
                    "status": "fail",
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "tool_names": [],
                    "tool_count": 0,
                    "limitations": [f"Child process exited {completed.returncode}."],
                    "answer_markdown": "",
                    "stderr": completed.stderr[-4000:],
                    "stdout": completed.stdout[-4000:],
                }
            )
            continue
        try:
            rows.append(json.loads(completed.stdout))
        except json.JSONDecodeError:
            rows.append(
                {
                    "id": item["id"],
                    "question": item["question"],
                    "status": "fail",
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "tool_names": [],
                    "tool_count": 0,
                    "limitations": ["Child process did not return valid JSON."],
                    "answer_markdown": "",
                    "stderr": completed.stderr[-4000:],
                    "stdout": completed.stdout[-4000:],
                }
            )
    return {
        "run_id": run_id,
        "started_at_utc": started_at,
        "question_timeout_seconds": args.question_timeout_seconds,
        "max_tool_calls": args.max_tool_calls,
        "results": rows,
    }


def _write_reports(data: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = _clean(data.get("run_id")) or "zopedia-question-probe"
    json_path = output_dir / f"{run_id}.json"
    md_path = output_dir / f"{run_id}.md"
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lines = [
        "# Zopedia Difficult Question Probe",
        "",
        f"Run ID: `{run_id}`",
        f"Started UTC: `{data.get('started_at_utc')}`",
        f"Per-question timeout: `{data.get('question_timeout_seconds')}s`",
        f"Max tool calls: `{data.get('max_tool_calls')}`",
        "",
        "## Summary",
        "",
        "| Question | Status | Tools | Evidence refs | Seconds |",
        "| --- | --- | --- | --- | ---: |",
    ]
    for row in list(data.get("results") or []):
        refs = row.get("evidence_refs") or {}
        ref_text = ", ".join(f"{key}={value}" for key, value in refs.items()) if refs else ""
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{_clean(row.get('id'))}`",
                    f"**{_clean(row.get('status'))}**",
                    ", ".join(row.get("tool_names") or []),
                    ref_text,
                    f"{float(row.get('elapsed_seconds') or 0.0):.3f}",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Raw Answers", ""])
    for row in list(data.get("results") or []):
        lines.extend(
            [
                f"### {_clean(row.get('id'))}",
                "",
                f"Question: {_clean(row.get('question'))}",
                "",
                f"Status: **{_clean(row.get('status'))}**",
                "",
                f"Tools: {', '.join(row.get('tool_names') or []) or 'none'}",
                "",
                "Answer:",
                "",
                _clean(row.get("answer_markdown")) or "_No answer returned._",
                "",
                "Limitations:",
                "",
                json.dumps(row.get("limitations") or [], indent=2, default=str),
                "",
            ]
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run five difficult Zopedia questions through the live agent.")
    parser.add_argument(
        "--env-file",
        default=str(APP_ROOT / "infra" / ".generated" / "deployment.local.env"),
        help="Optional env file with dev LLM/DB settings.",
    )
    parser.add_argument("--tag", default="", help="Optional run id.")
    parser.add_argument("--single-index", type=int, default=None, help="Run one question and print JSON only.")
    parser.add_argument("--max-tool-calls", type=int, default=10)
    parser.add_argument("--question-timeout-seconds", type=int, default=240)
    parser.add_argument(
        "--output-dir",
        default=str(APP_ROOT / "documents" / "architecture" / "new_features" / "zopedia" / "question_probes"),
    )
    args = parser.parse_args()

    if args.single_index is not None:
        result = _single_question(args.single_index, max_tool_calls=args.max_tool_calls, env_file=Path(args.env_file))
        print(json.dumps(result, sort_keys=True, default=str))
        return 0 if result.get("status") == "completed" else 1

    data = _run_parent(args)
    json_path, md_path = _write_reports(data, Path(args.output_dir))
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
