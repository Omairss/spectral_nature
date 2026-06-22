#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[1]
QUESTION_PROBE = APP_ROOT / "scripts" / "zopedia_question_probe.py"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


COMPANY_NAMES = {
    "NVDA": "NVIDIA",
    "CRWV": "CoreWeave",
    "BX": "Blackstone",
    "OBDC": "Blue Owl Capital Corporation",
    "MAIN": "Main Street Capital",
}

FACETS = {
    "business_model": (
        "What does {company} ({ticker}) sell, who pays it, and what is the operating business model? "
        "For financial firms, explain the fund/lending/investment model, fee income, spread income, or carried-interest economics if relevant."
    ),
    "demand_customers": (
        "Is there real customer or capital demand for {company} ({ticker}) now? Name demand signals, customers, AUM/fundraising, originations, backlog, "
        "RPO, usage, adoption, occupancy, or pipeline evidence if available."
    ),
    "fundamentals": (
        "Are {company} ({ticker}) fundamentals good, mixed, or weak? Cover revenue or investment income, operating margin or distributable earnings, "
        "balance sheet, cash flow or dividend coverage, credit quality, capex burden, AUM, NAV, leverage, and recent quarterly direction where relevant."
    ),
    "workforce_attention": (
        "What do employees, hiring, web attention, developer attention, platform usage, or customer engagement signals say about {company} ({ticker})? "
        "If evidence is absent, name the exact missing evidence."
    ),
    "policy_risk": (
        "Is the global, political, regulatory, rate-cycle, credit-cycle, AI infrastructure, and macro environment supportive or hostile for {company} ({ticker})? "
        "Cover policy, regulation, financing conditions, supply constraints, and execution risks."
    ),
}


def _clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _latest_frame(dataset_name: str):
    try:
        from pipeline.jobs.main import _load_latest_materialized_frame

        return _load_latest_materialized_frame(dataset_name)
    except Exception:
        try:
            import pandas as pd

            return pd.DataFrame()
        except Exception:
            return None


def _records_for_symbol(frame: Any, ticker: str, *, symbol_columns: tuple[str, ...], limit: int = 1) -> list[dict[str, Any]]:
    try:
        import pandas as pd

        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return []
        for column in symbol_columns:
            if column not in frame.columns:
                continue
            rows = frame[frame[column].astype(str).str.upper().str.strip().eq(ticker)].copy()
            if rows.empty:
                continue
            return rows.head(limit).to_dict("records")
    except Exception:
        return []
    return []


def _compact_fundamentals_for_ticker(ticker: str) -> list[dict[str, Any]]:
    try:
        import pandas as pd

        frame = _latest_frame("quarterly_fundamentals")
        if not isinstance(frame, pd.DataFrame) or frame.empty or "ticker" not in frame.columns:
            return []
        rows = frame[frame["ticker"].astype(str).str.upper().str.strip().eq(ticker)].copy()
        if rows.empty:
            return []
        if "report_date" in rows.columns:
            rows["_report_date"] = pd.to_datetime(rows["report_date"], errors="coerce")
            rows = rows.sort_values("_report_date", na_position="first")
        metrics = (
            "Total Revenue",
            "Revenue",
            "Operating Income",
            "Net Income",
            "Investment Income",
            "Net Investment Income",
            "Distributable Earnings",
            "Capital Expenditure",
            "Cash from Operating Activities",
            "Free Cash Flow",
            "Total Assets",
            "Total Liabilities",
            "Total Equity",
            "Total Debt",
            "NAV",
            "Net Asset Value",
        )
        rows = rows[rows.get("metric", pd.Series(dtype=str)).astype(str).isin(metrics)]
        if rows.empty:
            return []
        rows["_quarter_key"] = rows.get("year_quarter", pd.Series(index=rows.index, dtype=object)).astype(str)
        if "report_date" in rows.columns:
            rows.loc[rows["_quarter_key"].isin({"", "nan", "None", "NaT"}), "_quarter_key"] = rows["report_date"].astype(str)
        quarter_keys = [key for key in rows["_quarter_key"].dropna().astype(str).drop_duplicates().tolist() if key and key.lower() != "nan"]
        compact: list[dict[str, Any]] = []
        for quarter_key in quarter_keys[-4:]:
            qrows = rows[rows["_quarter_key"].astype(str).eq(quarter_key)]
            record: dict[str, Any] = {"quarter": quarter_key}
            if "report_date" in qrows.columns and not qrows["report_date"].dropna().empty:
                record["report_date"] = str(qrows["report_date"].dropna().iloc[-1])
            for _, row in qrows.iterrows():
                metric = str(row.get("metric") or "").strip()
                if metric:
                    record[metric] = row.get("value")
            revenue = record.get("Total Revenue", record.get("Revenue"))
            operating_income = record.get("Operating Income")
            try:
                if revenue not in (None, "", 0) and operating_income not in (None, ""):
                    record["Operating Margin"] = float(operating_income) / float(revenue)
            except Exception:
                pass
            compact.append(record)
        return compact
    except Exception:
        return []


def _recent_news_for_ticker(ticker: str) -> list[dict[str, Any]]:
    try:
        import pandas as pd

        frame = _latest_frame("news_articles")
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return []
        mask = pd.Series(False, index=frame.index)
        if "symbols" in frame.columns:
            mask = mask | frame["symbols"].astype(str).str.upper().str.contains(ticker, regex=False, na=False)
        if "symbol" in frame.columns:
            mask = mask | frame["symbol"].astype(str).str.upper().str.strip().eq(ticker)
        rows = frame[mask].head(4)
        out = []
        for _, row in rows.iterrows():
            out.append(
                {
                    "headline": _clean(row.get("headline") or row.get("title")),
                    "summary": _clean(row.get("summary")),
                    "source": _clean(row.get("source")),
                    "published_at": _clean(row.get("published_at")),
                    "url": _clean(row.get("url")),
                }
            )
        return out
    except Exception:
        return []


def _local_fact_pack(ticker: str) -> dict[str, Any]:
    baseline_rows = _records_for_symbol(
        _latest_frame("company_baselines"),
        ticker,
        symbol_columns=("symbol", "ticker", "entity_id"),
        limit=1,
    )
    listing_rows = _records_for_symbol(
        _latest_frame("us_equity_listings"),
        ticker,
        symbol_columns=("symbol", "ticker", "entity_id"),
        limit=1,
    )
    return {
        "ticker": ticker,
        "company_baseline": baseline_rows[:1],
        "listing": listing_rows[:1],
        "compact_fundamentals": _compact_fundamentals_for_ticker(ticker),
        "recent_news": _recent_news_for_ticker(ticker),
    }


def _probe_question(*, ticker: str, facet: str, question: str, args: argparse.Namespace) -> dict[str, Any]:
    env = os.environ.copy()
    for key, value in _load_env_file(Path(args.env_file)).items():
        env.setdefault(key, value)
    env.setdefault("LLM_TIMEOUT_SECONDS", str(args.llm_timeout_seconds))
    started = time.monotonic()
    cmd = [
        sys.executable,
        str(QUESTION_PROBE),
        "--env-file",
        str(args.env_file),
        "--question-id",
        f"{ticker.lower()}_{facet}",
        "--question",
        question,
        "--max-tool-calls",
        str(args.max_tool_calls),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=args.facet_timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ticker": ticker,
            "facet": facet,
            "status": "timeout",
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "answer_markdown": "",
            "limitations": [f"Facet timed out after {args.facet_timeout_seconds}s."],
            "stdout": _clean(exc.stdout)[-2000:] if exc.stdout else "",
            "stderr": _clean(exc.stderr)[-2000:] if exc.stderr else "",
        }
    row: dict[str, Any]
    try:
        row = json.loads(completed.stdout)
    except Exception:
        row = {
            "status": "fail",
            "answer_markdown": "",
            "limitations": ["Probe did not return valid JSON."],
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
    row["ticker"] = ticker
    row["facet"] = facet
    row["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if completed.returncode != 0 and row.get("status") == "completed":
        row["status"] = "fail"
    return row


def _build_question(ticker: str, facet: str) -> str:
    company = COMPANY_NAMES.get(ticker, ticker)
    fact_pack = _local_fact_pack(ticker)
    return (
        f"{ticker} ticker-specific business-memory facet. "
        + FACETS[facet].format(ticker=ticker, company=company)
        + "\n\nLocal pipeline compact facts JSON:\n"
        + json.dumps(fact_pack, ensure_ascii=True, default=str)[:12000]
        + "\n\n"
        + "The local JSON is typed pipeline state, not a user guess. Use it to identify the company and available local metrics. "
        + "If Zopedia or retained evidence is empty, call live AQL research tools before concluding that no business evidence exists. "
        + " Return concise output with exactly these headings: VERDICT, FACTS, GAPS, SOURCE REFS. "
        + "Evidence labels are not analysis; say what the evidence means. No stock-chart or price-technical commentary."
    )


def _markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Company Business Memory Probe",
        "",
        f"Run ID: `{data['run_id']}`",
        f"Started UTC: `{data['started_at_utc']}`",
        f"Tickers: `{', '.join(data['tickers'])}`",
        f"Facets: `{', '.join(data['facets'])}`",
        "",
        "## Summary",
        "",
        "| Ticker | Facet | Status | Confidence | Tools | Seconds |",
        "| --- | --- | --- | --- | --- | ---: |",
    ]
    for row in data["results"]:
        tools = ", ".join(row.get("tool_names") or [])
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{_clean(row.get('ticker'))}`",
                    f"`{_clean(row.get('facet'))}`",
                    f"**{_clean(row.get('status'))}**",
                    _clean(row.get("confidence")) or "",
                    tools,
                    f"{float(row.get('elapsed_seconds') or 0.0):.3f}",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Answers", ""])
    for ticker in data["tickers"]:
        lines.extend([f"### {ticker}", ""])
        for row in data["results"]:
            if row.get("ticker") != ticker:
                continue
            lines.extend(
                [
                    f"#### {_clean(row.get('facet'))}",
                    "",
                    _clean(row.get("answer_markdown")) or "_No answer returned._",
                    "",
                    "Limitations:",
                    "",
                    json.dumps(row.get("limitations") or [], indent=2, default=str),
                    "",
                ]
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ticker business-memory facets through AQL/Zopedia in parallel.")
    parser.add_argument("--tickers", nargs="+", default=["NVDA", "CRWV", "BX", "OBDC", "MAIN"])
    parser.add_argument("--facets", nargs="+", default=list(FACETS))
    parser.add_argument("--max-workers", type=int, default=6)
    parser.add_argument("--max-tool-calls", type=int, default=16)
    parser.add_argument("--facet-timeout-seconds", type=int, default=3600)
    parser.add_argument("--llm-timeout-seconds", type=int, default=600)
    parser.add_argument("--tag", default="")
    parser.add_argument(
        "--env-file",
        default=str(APP_ROOT / "infra" / ".generated" / "deployment.local.env"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(APP_ROOT / "documents" / "architecture" / "new_features" / "zopedia" / "company_business_probes"),
    )
    args = parser.parse_args()

    tickers = [_clean(item).upper() for item in args.tickers if _clean(item)]
    facets = [_clean(item) for item in args.facets if _clean(item) in FACETS]
    run_id = args.tag or f"company-business-probe-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    jobs = [(ticker, facet, _build_question(ticker, facet)) for ticker in tickers for facet in facets]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.max_workers))) as pool:
        future_map = {
            pool.submit(_probe_question, ticker=ticker, facet=facet, question=question, args=args): (ticker, facet)
            for ticker, facet, question in jobs
        }
        for future in as_completed(future_map):
            ticker, facet = future_map[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {
                    "ticker": ticker,
                    "facet": facet,
                    "status": "fail",
                    "answer_markdown": "",
                    "limitations": [f"{type(exc).__name__}: {exc}"],
                }
            print(
                json.dumps(
                    {
                        "ticker": row.get("ticker"),
                        "facet": row.get("facet"),
                        "status": row.get("status"),
                        "elapsed_seconds": row.get("elapsed_seconds"),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            results.append(row)
    order = {(ticker, facet): idx for idx, (ticker, facet, _) in enumerate(jobs)}
    results.sort(key=lambda row: order.get((row.get("ticker"), row.get("facet")), 9999))
    data = {
        "run_id": run_id,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "tickers": tickers,
        "facets": facets,
        "max_tool_calls": args.max_tool_calls,
        "results": results,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{run_id}.json"
    md_path = output_dir / f"{run_id}.md"
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    md_path.write_text(_markdown_report(data), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
