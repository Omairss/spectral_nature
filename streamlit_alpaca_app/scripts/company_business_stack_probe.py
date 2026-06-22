#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable

import pandas as pd


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


_PROGRESS_LOCK = threading.Lock()


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


def _records(frame: pd.DataFrame | None, *, limit: int | None = None) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    out = frame.copy()
    if limit is not None:
        out = out.head(max(int(limit), 0))
    return out.to_dict("records")


def _truncate_records(records: list[dict[str, Any]], *, text_limit: int = 900) -> list[dict[str, Any]]:
    truncated: list[dict[str, Any]] = []
    for row in records:
        clean_row: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, str) and len(value) > text_limit:
                clean_row[key] = value[: text_limit - 3].rstrip() + "..."
            else:
                clean_row[key] = value
        truncated.append(clean_row)
    return truncated


def _progress_value(value: Any, *, limit: int = 800) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _progress_value(item, limit=limit)
            for key, item in value.items()
            if str(key) not in {"reasoning_trace", "render_payload"}
        }
    if isinstance(value, list):
        return [_progress_value(item, limit=limit) for item in value[:20]]
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."
    return value


def _make_progress_callback(
    *,
    symbol: str,
    run_id: str,
    started_at_monotonic: float,
    jsonl_path: Path,
    print_updates: bool,
) -> Callable[[dict[str, Any]], None]:
    high_signal_stages = {
        "start",
        "business_profile_start",
        "business_research_plan_ready",
        "business_research_search_start",
        "business_research_search_complete",
        "business_research_dossier_start",
        "business_research_dossier_complete",
        "business_fact_resolution_start",
        "business_fact_resolution_complete",
        "business_story_synthesis_start",
        "business_profile_complete",
        "tool_catalog_ready",
        "planner_start",
        "planner_reasoning",
        "planner_direct_structured_payload",
        "tool_start",
        "tool_complete",
        "tool_failed",
        "trajectory_monitor_restart",
        "trajectory_monitor_continue",
        "trajectory_monitor_kill",
        "trajectory_monitor_timeout",
        "answer_judge_complete",
        "memory_update_complete",
        "complete",
    }

    def _callback(payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        stage = _clean(payload.get("stage"))
        row = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.monotonic() - started_at_monotonic, 3),
            "run_id": run_id,
            "symbol": symbol,
            **_progress_value(payload),
        }
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with _PROGRESS_LOCK:
            with jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            if print_updates and stage in high_signal_stages:
                message = _clean(payload.get("message"))
                tool = _clean(payload.get("tool_name"))
                preview = _clean(payload.get("result_preview"))
                parts = [
                    symbol,
                    stage,
                    f"tool={tool}" if tool else "",
                    message,
                    f"result={preview[:180]}" if preview else "",
                ]
                print(" | ".join(part for part in parts if part), flush=True)

    return _callback


def _load_input_frames(*, use_db_pages: bool = False) -> dict[str, pd.DataFrame]:
    from pipeline.jobs.main import (
        _company_baselines_with_listing_fallback,
        _load_latest_materialized_frame,
    )

    company_baselines = _load_latest_materialized_frame("company_baselines")
    listings = _load_latest_materialized_frame("us_equity_listings")
    zopedia_parts = [
        _load_latest_materialized_frame("zopedia_pages"),
        _load_latest_materialized_frame("zopedia_company_business_memory_pages"),
    ]
    if use_db_pages:
        try:
            from pipeline.jobs.main import _db_connection, _zopedia_pages_for_news_resolution

            conn = _db_connection()
        except Exception:
            conn = None
        try:
            db_pages = _zopedia_pages_for_news_resolution(conn)
        except Exception:
            db_pages = pd.DataFrame()
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
        if isinstance(db_pages, pd.DataFrame) and not db_pages.empty:
            zopedia_parts.append(db_pages)
    zopedia_pages = pd.concat(
        [part for part in zopedia_parts if isinstance(part, pd.DataFrame) and not part.empty],
        ignore_index=True,
        sort=False,
    ) if any(isinstance(part, pd.DataFrame) and not part.empty for part in zopedia_parts) else pd.DataFrame()
    if not zopedia_pages.empty and "page_id" in zopedia_pages.columns:
        zopedia_pages = zopedia_pages.drop_duplicates(subset=["page_id"], keep="last").reset_index(drop=True)
    return {
        "company_baselines": _company_baselines_with_listing_fallback(company_baselines, listings),
        "fundamentals": _load_latest_materialized_frame("quarterly_fundamentals"),
        "zopedia_pages": zopedia_pages,
        "edgar_evidence": _load_latest_materialized_frame("edgar_evidence"),
        "news": _load_latest_materialized_frame("news_articles"),
        "business_research_results": _load_latest_materialized_frame("zopedia_business_model_search_results"),
    }


def _coverage_counts(coverage: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for payload in coverage.values():
        if not isinstance(payload, dict):
            continue
        status = _clean(payload.get("status")) or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _json_loads(value: object, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = _clean(value)
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _summarize_stack_frame(frames: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    stack_frame = frames.get("zopedia_ticker_business_model_stacks", pd.DataFrame())
    rows: list[dict[str, Any]] = []
    for row in _records(stack_frame):
        coverage = _json_loads(row.get("slot_coverage_json"), {})
        facts = _json_loads(row.get("slot_facts_json"), {})
        query_plan = _json_loads(row.get("research_query_plan_json"), [])
        dossier = _json_loads(row.get("research_dossier_json"), {})
        source_inventory = dossier.get("source_inventory") if isinstance(dossier, dict) else []
        source_scope_counts = Counter(
            _clean(item.get("evidence_scope")) or "unknown"
            for item in list(source_inventory or [])
            if isinstance(item, dict)
        )
        source_status_counts = Counter(
            _clean(item.get("source_status")) or "unknown"
            for item in list(source_inventory or [])
            if isinstance(item, dict)
        )
        rows.append(
            {
                "symbol": row.get("symbol"),
                "company_name": row.get("company_name"),
                "status": row.get("status"),
                "confidence": row.get("confidence"),
                "business_memory_page_id": row.get("business_memory_page_id"),
                "warnings": _json_loads(row.get("synthesis_warnings_json"), []),
                "slot_gaps": _json_loads(row.get("slot_gaps_json"), []),
                "coverage_counts": _coverage_counts(coverage if isinstance(coverage, dict) else {}),
                "dossier_source_count": len(dossier.get("source_inventory") or []) if isinstance(dossier, dict) else 0,
                "dossier_finding_count": (
                    sum(len(items) for items in (dossier.get("slot_findings") or {}).values())
                    if isinstance(dossier, dict) and isinstance(dossier.get("slot_findings"), dict)
                    else 0
                ),
                "dossier_source_scope_counts": dict(source_scope_counts),
                "dossier_source_status_counts": dict(source_status_counts),
                "planned_queries": len(query_plan) if isinstance(query_plan, list) else 0,
                "source_intents": sorted(
                    {
                        _clean(item.get("source_intent"))
                        for item in list(query_plan or [])
                        if isinstance(item, dict) and _clean(item.get("source_intent"))
                    }
                ),
                "fact_slots": {
                    slot: len(items)
                    for slot, items in (facts.items() if isinstance(facts, dict) else [])
                    if isinstance(items, list) and items
                },
                "slot_facts": facts,
                "business_story_markdown": row.get("business_story_markdown"),
            }
        )
    return rows


def _run_one_ticker(
    symbol: str,
    *,
    input_frames: dict[str, pd.DataFrame],
    args: argparse.Namespace,
    run_id: str,
    asof: datetime,
) -> dict[str, Any]:
    from services.aql.business_model_stack import build_ticker_business_model_stack_frames
    from services.aql.config import _load_search_clients
    from services.aql_zopedia_engine import load_aql_zopedia_llm_client

    started = time.monotonic()
    progress_callback = _make_progress_callback(
        symbol=symbol,
        run_id=run_id,
        started_at_monotonic=started,
        jsonl_path=Path(args.progress_jsonl),
        print_updates=not bool(args.quiet_progress),
    )
    serp_client = None
    tavily_client = None
    execute_research = not args.no_research
    if execute_research:
        try:
            serp_client, tavily_client = _load_search_clients()
        except Exception:
            execute_research = False
    try:
        llm_client = load_aql_zopedia_llm_client(surface="company_business_stack_probe")
    except Exception:
        llm_client = None
    try:
        frames = build_ticker_business_model_stack_frames(
            symbols=[symbol],
            company_baselines_frame=input_frames.get("company_baselines"),
            fundamentals_frame=input_frames.get("fundamentals"),
            zopedia_pages_frame=input_frames.get("zopedia_pages"),
            edgar_evidence_frame=input_frames.get("edgar_evidence"),
            news_frame=input_frames.get("news"),
            research_results_frame=input_frames.get("business_research_results"),
            serp_client=serp_client,
            tavily_client=tavily_client,
            execute_research=execute_research,
            max_research_queries=args.max_research_queries,
            max_search_results_per_query=args.max_search_results_per_query,
            llm_client=llm_client,
            run_id=run_id,
            asof_time_utc=asof,
            write_policy=args.write_policy,
            limit=1,
            progress_callback=progress_callback,
        )
        status = "completed"
        error = ""
    except Exception as exc:
        frames = {}
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    return {
        "symbol": symbol,
        "status": status,
        "error": error,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "frames": frames,
    }


def _concat_frames(results: list[dict[str, Any]]) -> dict[str, pd.DataFrame]:
    by_name: dict[str, list[pd.DataFrame]] = {}
    for result in results:
        for name, frame in dict(result.get("frames") or {}).items():
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                by_name.setdefault(name, []).append(frame)
    return {
        name: pd.concat(parts, ignore_index=True, sort=False).reset_index(drop=True)
        for name, parts in by_name.items()
        if parts
    }


def _markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Company Business Stack Probe",
        "",
        f"Run ID: `{data['run_id']}`",
        f"Started UTC: `{data['started_at_utc']}`",
        f"Tickers: `{', '.join(data['tickers'])}`",
        "",
        "## Results",
        "",
        "| Ticker | Status | Seconds | Stack Status | Confidence | Queries | Requests | Results | Opened Pages |",
        "| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    stack_by_symbol = {row.get("symbol"): row for row in data.get("stack_summaries") or []}
    for result in data.get("ticker_runs") or []:
        symbol = _clean(result.get("symbol"))
        stack = stack_by_symbol.get(symbol, {})
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{symbol}`",
                    f"**{_clean(result.get('status'))}**",
                    f"{float(result.get('elapsed_seconds') or 0.0):.3f}",
                    _clean(stack.get("status")),
                    _clean(stack.get("confidence")),
                    str(stack.get("planned_queries") or 0),
                    str(data.get("frame_counts", {}).get("zopedia_business_model_search_requests", 0)),
                    str(data.get("frame_counts", {}).get("zopedia_business_model_search_results", 0)),
                    str(data.get("opened_pages_by_symbol", {}).get(symbol, 0)),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Stack Summaries", ""])
    for stack in data.get("stack_summaries") or []:
        lines.extend(
            [
                f"### {_clean(stack.get('symbol'))} — {_clean(stack.get('company_name'))}",
                "",
                f"Status: `{_clean(stack.get('status'))}` / `{_clean(stack.get('confidence'))}`",
                "",
        f"Coverage: `{json.dumps(stack.get('coverage_counts') or {}, sort_keys=True)}`",
        "",
        f"Dossier: `{stack.get('dossier_source_count') or 0}` source lead(s), `{stack.get('dossier_finding_count') or 0}` finding(s)",
        "",
        f"Source scopes: `{json.dumps(stack.get('dossier_source_scope_counts') or {}, sort_keys=True)}`",
        "",
        f"Source statuses: `{json.dumps(stack.get('dossier_source_status_counts') or {}, sort_keys=True)}`",
        "",
        f"Source intents: `{', '.join(stack.get('source_intents') or [])}`",
                "",
                "Warnings:",
                "",
                json.dumps(stack.get("warnings") or [], indent=2, default=str),
                "",
                "Gaps:",
                "",
                json.dumps(stack.get("slot_gaps") or [], indent=2, default=str),
                "",
                "Story:",
                "",
                _clean(stack.get("business_story_markdown")) or "_No stack story accepted._",
                "",
                "Facts:",
                "",
                json.dumps(stack.get("slot_facts") or {}, indent=2, default=str)[:12000],
                "",
            ]
        )
    return "\n".join(lines)


def _write_report(
    *,
    data: dict[str, Any],
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = _clean(data.get("run_id")) or "company-business-stack-probe"
    json_path = output_dir / f"{run_id}.json"
    md_path = output_dir / f"{run_id}.md"
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    md_path.write_text(_markdown_report(data), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the ticker business-model stack path against live Zopedia/AQL inputs.")
    parser.add_argument("--tickers", nargs="+", default=["OBDC", "MAIN"])
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--max-research-queries", type=int, default=24)
    parser.add_argument("--max-search-results-per-query", type=int, default=4)
    parser.add_argument("--no-research", action="store_true")
    parser.add_argument("--use-db-pages", action="store_true")
    parser.add_argument("--write-policy", default="propose")
    parser.add_argument("--tag", default="")
    parser.add_argument("--progress-jsonl", default="")
    parser.add_argument("--quiet-progress", action="store_true")
    parser.add_argument(
        "--env-file",
        default=str(APP_ROOT / "infra" / ".generated" / "deployment.local.env"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(APP_ROOT / "documents" / "architecture" / "new_features" / "zopedia" / "company_business_probes"),
    )
    args = parser.parse_args()

    _load_env_file(Path(args.env_file))
    os.environ.setdefault("ZOPEDIA_TICKER_BUSINESS_RESEARCH_BUDGET_SECONDS", "3600")
    tickers = [_clean(item).upper() for item in args.tickers if _clean(item)]
    run_id = args.tag or f"company-business-stack-probe-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    output_dir = Path(args.output_dir)
    if not _clean(args.progress_jsonl):
        args.progress_jsonl = str(output_dir / f"{run_id}.progress.jsonl")
    started_at = datetime.now(timezone.utc)
    input_frames = _load_input_frames(use_db_pages=args.use_db_pages)
    results: list[dict[str, Any]] = []
    max_workers = min(max(int(args.max_workers), 1), len(tickers) or 1)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="business-stack-probe") as pool:
        futures = {
            pool.submit(
                _run_one_ticker,
                symbol,
                input_frames=input_frames,
                args=args,
                run_id=run_id,
                asof=started_at,
            ): symbol
            for symbol in tickers
        }
        for future in as_completed(futures):
            row = future.result()
            print(
                json.dumps(
                    {
                        "symbol": row.get("symbol"),
                        "status": row.get("status"),
                        "elapsed_seconds": row.get("elapsed_seconds"),
                        "error": row.get("error"),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            results.append(row)
    results.sort(key=lambda row: tickers.index(row["symbol"]) if row.get("symbol") in tickers else 999)
    frames = _concat_frames(results)
    search_results = frames.get("zopedia_business_model_search_results", pd.DataFrame())
    opened_pages_by_symbol: dict[str, int] = {}
    if isinstance(search_results, pd.DataFrame) and not search_results.empty and {"symbol", "opened_page"}.issubset(search_results.columns):
        opened = search_results[search_results["opened_page"].astype(bool)].copy()
        if not opened.empty:
            opened_pages_by_symbol = {
                str(symbol): int(count)
                for symbol, count in opened.groupby("symbol").size().to_dict().items()
            }
    report_frames = {
        name: _truncate_records(_records(frame, limit=250))
        for name, frame in frames.items()
    }
    data = {
        "run_id": run_id,
        "started_at_utc": started_at.isoformat(),
        "tickers": tickers,
        "args": {
            "max_workers": args.max_workers,
            "max_research_queries": args.max_research_queries,
            "max_search_results_per_query": args.max_search_results_per_query,
            "no_research": args.no_research,
            "use_db_pages": args.use_db_pages,
            "write_policy": args.write_policy,
            "progress_jsonl": args.progress_jsonl,
        },
        "ticker_runs": [
            {key: value for key, value in result.items() if key != "frames"}
            for result in results
        ],
        "frame_counts": {name: int(len(frame)) for name, frame in frames.items()},
        "opened_pages_by_symbol": opened_pages_by_symbol,
        "stack_summaries": _summarize_stack_frame(frames),
        "frames": report_frames,
    }
    json_path, md_path = _write_report(data=data, output_dir=output_dir)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "progress_jsonl": str(args.progress_jsonl)}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
