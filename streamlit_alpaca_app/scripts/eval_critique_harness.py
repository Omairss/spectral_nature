"""Offline evaluation harness for the AQL critique + judge layer.

Two modes:

1. Cached blob — load a real attention_home_1d row, build the LLM homepage
   summary, then run critique + judge end to end.
     python scripts/eval_critique_harness.py --blob <dataset_version_id>

2. Synthetic scenario — use a hand-crafted home_payload + summary that
   contains a known failure mode, to test whether the agentic critique
   discovers the real catalyst via web search.
     python scripts/eval_critique_harness.py --scenario bno_hormuz
     python scripts/eval_critique_harness.py --scenario tech_squeeze

Source infra/.generated/deployment.local.env first.
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_access.query_service import QueryService  # noqa: E402
from services.aql.critique import critique_home_summary, judge_revise_summary  # noqa: E402
from services.aql.summarizer import build_attention_home_summary  # noqa: E402
from services.llm import load_llm_client  # noqa: E402


CACHE_DIR = ROOT / "cache" / "pipeline_store" / "attention_home_1d"


def _load_cached_home_payload(blob_id: str | None) -> dict:
    if blob_id:
        target = CACHE_DIR / blob_id / "frame.pkl"
    else:
        candidates = sorted(CACHE_DIR.glob("attention_home_1d__*"))
        if not candidates:
            raise SystemExit(f"No cached home_1d blobs in {CACHE_DIR}")
        target = candidates[-1] / "frame.pkl"
    df: pd.DataFrame = pd.read_pickle(target)
    row = df.iloc[0].to_dict()

    def _decode(field: str, default):
        raw = row.get(field)
        if raw in (None, ""):
            return default
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            return default

    payload = {
        "run_id": row.get("run_id") or "eval-run",
        "generated_at_utc": str(row.get("generated_at_utc") or ""),
        "top_events": _decode("top_events_json", []),
        "must_read_movers": _decode("must_read_movers_json", []),
        "unresolved_large_moves": _decode("unresolved_large_moves_json", []),
        "coverage_summary": _decode("coverage_summary_json", {}),
    }
    return payload, target.parent.name


SCENARIOS: dict[str, dict] = {
    "bno_hormuz": {
        "label": "BNO surge with vague catalyst (real cause: Hormuz supply-risk)",
        "summary": {
            "headline": "Market Summary",
            "summary_text": (
                "Oil-linked instruments led the market higher while equities were mixed.\n\n"
                "**Energy bid extends**\n"
                "- BNO surged sharply with no clear catalyst confirmed.\n"
                "- USO traded firmer alongside BNO with no single driver identified.\n"
                "- Oil majors XOM and CVX moved up modestly."
            ),
            "audio_text": (
                "Oil-linked names led the market today, with BNO surging sharply on no "
                "clear catalyst. USO traded firmer alongside it."
            ),
            "event_count": 0,
            "must_read_count": 0,
            "unresolved_count": 2,
            "featured_symbols": ["BNO", "USO", "XOM", "CVX"],
            "stories": [],
        },
        "home_payload": {
            "run_id": "synthetic-bno-hormuz",
            "generated_at_utc": "2026-04-25T18:00:00Z",
            "top_events": [],
            "must_read_movers": [],
            "unresolved_large_moves": [
                {
                    "bundle_id": "symbol::BNO",
                    "symbol": "BNO",
                    "headline": "Brent Oil Fund jumps in extreme trading",
                    "what_changed_text": "BNO traded sharply higher today.",
                    "why_now_text": "",
                    "what_else_moved_text": "USO also moved.",
                    "cause_status": "unresolved",
                    "confidence_label": "Developing",
                    "candidate_score": 130.0,
                    "change_pct": 3.2,
                    "expected_move_pct": 0.6,
                    "surprise_z": 4.1,
                    "sector": "Commodities",
                    "industry": "Crude Oil ETF",
                    "source_label": "Macro anchor",
                    "top_source": "TradingView",
                    "best_authority_rank": 3,
                },
                {
                    "bundle_id": "symbol::USO",
                    "symbol": "USO",
                    "headline": "United States Oil Fund firmer",
                    "what_changed_text": "USO traded higher today.",
                    "why_now_text": "",
                    "what_else_moved_text": "",
                    "cause_status": "unresolved",
                    "confidence_label": "Developing",
                    "candidate_score": 100.0,
                    "change_pct": 2.1,
                    "expected_move_pct": 0.5,
                    "surprise_z": 2.6,
                    "sector": "Commodities",
                    "industry": "Crude Oil ETF",
                    "source_label": "Macro anchor",
                    "top_source": "TradingView",
                    "best_authority_rank": 3,
                },
            ],
        },
    },
    "tech_squeeze": {
        "label": "AI/cloud rally with vague catalyst (real cause: short squeeze pressure)",
        "summary": {
            "headline": "Market Summary",
            "summary_text": (
                "Tech rebounded with no single driver confirmed while broader market chopped.\n\n"
                "**AI and cloud lead the bid**\n"
                "- AI compute names CRWV, NBIS, IREN rallied together with no clear "
                "single catalyst.\n"
                "- Cloud software including ORCL and NTNX outperformed peers without "
                "a confirmed driver.\n"
                "- Heavily-shorted small-cap names also bid."
            ),
            "audio_text": (
                "Tech rebounded today with no clear single catalyst. AI compute "
                "infrastructure names led the bid alongside cloud software."
            ),
            "event_count": 0,
            "must_read_count": 0,
            "unresolved_count": 3,
            "featured_symbols": ["CRWV", "NBIS", "IREN", "ORCL", "NTNX"],
            "stories": [],
        },
        "home_payload": {
            "run_id": "synthetic-tech-squeeze",
            "generated_at_utc": "2026-04-25T18:00:00Z",
            "top_events": [],
            "must_read_movers": [],
            "unresolved_large_moves": [
                {
                    "bundle_id": "symbol::CRWV",
                    "symbol": "CRWV",
                    "headline": "CoreWeave rallies hard",
                    "what_changed_text": "CRWV traded sharply higher today.",
                    "why_now_text": "",
                    "what_else_moved_text": "Other AI infra names also moved.",
                    "cause_status": "unresolved",
                    "confidence_label": "Developing",
                    "candidate_score": 140.0,
                    "change_pct": 11.8,
                    "expected_move_pct": 2.0,
                    "surprise_z": 4.9,
                    "sector": "Information Technology",
                    "industry": "Internet Services & Infrastructure",
                    "source_label": "Macro anchor",
                    "top_source": "TradingView",
                    "best_authority_rank": 3,
                },
                {
                    "bundle_id": "symbol::NBIS",
                    "symbol": "NBIS",
                    "headline": "Nebius up sharply",
                    "what_changed_text": "NBIS rallied today.",
                    "why_now_text": "",
                    "what_else_moved_text": "",
                    "cause_status": "unresolved",
                    "confidence_label": "Developing",
                    "candidate_score": 120.0,
                    "change_pct": 8.4,
                    "expected_move_pct": 1.5,
                    "surprise_z": 4.1,
                    "sector": "Information Technology",
                    "industry": "Internet Services & Infrastructure",
                    "source_label": "Macro anchor",
                    "top_source": "TradingView",
                    "best_authority_rank": 3,
                },
            ],
        },
    },
}


def _format_block(text: str, indent: str = "  ") -> str:
    text = (text or "").strip()
    if not text:
        return f"{indent}<empty>"
    return textwrap.indent(text, indent)


def _print_section(title: str, body: str):
    print()
    print(f"=== {title} ===")
    print(body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blob", default=None, help="Specific dataset_version_id to load")
    parser.add_argument(
        "--scenario",
        default=None,
        choices=sorted(SCENARIOS.keys()),
        help="Synthetic scenario instead of a cached blob",
    )
    parser.add_argument("--max-tool-calls", type=int, default=4)
    args = parser.parse_args()

    llm_client = load_llm_client()
    if llm_client is None:
        raise SystemExit("LLM client not configured. Source infra/.generated/deployment.local.env first.")
    query_service = QueryService.from_environment()

    if args.scenario:
        scenario = SCENARIOS[args.scenario]
        payload = scenario["home_payload"]
        base_summary = scenario["summary"]
        print(f"Synthetic scenario: {args.scenario} — {scenario['label']}")
        print(
            f"  events={len(payload['top_events'])} movers={len(payload['must_read_movers'])} "
            f"unresolved={len(payload['unresolved_large_moves'])}"
        )
        _print_section(
            f"ORIGINAL SUMMARY (synthetic)",
            _format_block(base_summary.get("summary_text")),
        )
    else:
        payload, blob_name = _load_cached_home_payload(args.blob)
        coverage = payload.get("coverage_summary") or {}
        print(f"Loaded {blob_name}")
        print(
            f"  generated_at_utc={payload['generated_at_utc']}  "
            f"events={len(payload['top_events'])} movers={len(payload['must_read_movers'])} "
            f"unresolved={len(payload['unresolved_large_moves'])} candidates={coverage.get('candidate_count')}"
        )

        t0 = time.monotonic()
        base_summary = build_attention_home_summary(payload)
        t_base = time.monotonic() - t0
        _print_section(
            f"ORIGINAL SUMMARY  ({t_base:.1f}s, headline={base_summary.get('headline')!r})",
            _format_block(base_summary.get("summary_text")),
        )

    t1 = time.monotonic()
    critique_result = critique_home_summary(
        summary=base_summary,
        home_payload=payload,
        llm_client=llm_client,
        query_service=query_service,
        max_tool_calls=args.max_tool_calls,
    )
    t_critique = time.monotonic() - t1

    issues = critique_result.get("issues") or []
    tool_calls = critique_result.get("tool_calls") or []
    tool_lines = []
    for call in tool_calls:
        rs = call.get("result_summary") or {}
        tool_lines.append(
            f"- [{call.get('status')}] {call.get('tool_name')} args={call.get('arguments')} "
            f"=> {(rs.get('preview_text') or '')[:160]}"
        )
    issue_lines = []
    for idx, issue in enumerate(issues):
        issue_lines.append(
            f"  [{idx}] {issue.get('severity').upper():6} {issue.get('type'):14} loc={issue.get('location')!r}\n"
            f"        claim:    {issue.get('claim')}\n"
            f"        evidence: {issue.get('evidence')}"
        )
    _print_section(
        f"CRITIQUE  ({t_critique:.1f}s, skipped={critique_result.get('skipped')}, "
        f"tool_calls={len(tool_calls)}, issues={len(issues)})",
        ("Tool calls:\n" + ("\n".join(tool_lines) or "  <none>") + "\n\n"
         "Issues:\n" + ("\n".join(issue_lines) or "  <none>")),
    )

    if not issues:
        _print_section("JUDGE", "  Skipped — no issues to act on.")
        return

    t2 = time.monotonic()
    revised = judge_revise_summary(
        original=base_summary,
        critique=critique_result,
        llm_client=llm_client,
    )
    t_judge = time.monotonic() - t2

    if revised is None:
        _print_section("JUDGE", "  Returned None (LLM call failed or empty output).")
        return

    revisions = revised.get("judge_revisions") or []
    rev_lines = []
    for rev in revisions:
        rev_lines.append(
            f"  - issue {rev.get('issue_index')}: {rev.get('decision')!r} -> "
            f"{(rev.get('rewritten_text') or '')[:140]}"
        )
    _print_section(
        f"REVISED SUMMARY  ({t_judge:.1f}s)",
        _format_block(revised.get("summary_text")),
    )
    _print_section("JUDGE DECISIONS", "\n".join(rev_lines) or "  <none>")
    _print_section("REVISED AUDIO TEXT", _format_block(revised.get("audio_text")))


if __name__ == "__main__":
    main()
