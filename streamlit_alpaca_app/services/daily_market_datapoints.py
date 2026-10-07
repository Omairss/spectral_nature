"""AQL selects the day's five chart references; datasets own every plotted value."""
from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from services.aql_zopedia_engine import run_aql_zopedia_structured_agent


def _points(raw: object) -> list[dict[str, Any]]:
    try:
        points = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    if not isinstance(points, list):
        return []
    normalized = {}
    for point in points:
        if not isinstance(point, dict):
            continue
        observed = pd.to_datetime(point.get("date"), utc=True, errors="coerce")
        try:
            value = float(point.get("value"))
        except (TypeError, ValueError):
            continue
        if pd.notna(observed) and math.isfinite(value):
            date = observed.date().isoformat()
            normalized[date] = {"date": date, "value": value, "period": str(point.get("period") or "")}
    return [normalized[key] for key in sorted(normalized)]


def build_daily_datapoint_catalog(*, snapshots: pd.DataFrame, fred_observations: pd.DataFrame, fred_summary: pd.DataFrame, asof: object) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    cutoff = pd.to_datetime(asof, utc=True)
    for row in snapshots.to_dict("records"):
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        series = {"price": _points(row.get("price_trend_json"))}
        raw_financials = row.get("financial_trends_json")
        try:
            financials = json.loads(raw_financials) if isinstance(raw_financials, str) else raw_financials
        except (ValueError, TypeError):
            financials = {}
        if isinstance(financials, dict):
            series.update({key: _points(value) for key, value in financials.items() if key in {"revenue", "net_income", "free_cash_flow"}})
        for metric, points in series.items():
            points = [point for point in points if pd.Timestamp(point["date"], tz="UTC") <= cutoff]
            if len(points) < 2:
                continue
            key = f"stock:{symbol}:{metric}"
            catalog[key] = {
                "id": key, "kind": "price" if metric == "price" else "fundamental", "symbol": symbol,
                "label": f"{symbol} · {metric.replace('_', ' ').title()}", "unit": "USD",
                "points": points, "date": points[-1]["date"], "value": points[-1]["value"],
                "period": points[-1].get("period", ""), "change_1d_pct": row.get("price_change_1d_pct") if metric == "price" else None,
                "source": "price_history" if metric == "price" else "quarterly_fundamentals",
            }
    metadata = {str(row.get("series_id") or ""): row for row in fred_summary.to_dict("records")}
    if {"series_id", "date", "value"}.issubset(fred_observations.columns):
        frame = fred_observations.copy()
        frame["date"] = pd.to_datetime(frame["date"], utc=True, errors="coerce")
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame = frame.dropna(subset=["date", "value"])
        frame = frame[(frame["date"] <= cutoff) & (frame["date"] >= cutoff - pd.Timedelta(days=365 * 3))]
        for series_id, rows in frame.groupby("series_id", sort=False):
            rows = rows.sort_values("date").drop_duplicates("date", keep="last").tail(20)
            if len(rows) < 2:
                continue
            meta = metadata.get(str(series_id), {})
            points = [{"date": row["date"].date().isoformat(), "value": float(row["value"])} for row in rows.to_dict("records")]
            key = f"fred:{series_id}"
            catalog[key] = {
                "id": key, "kind": "macro", "series_id": str(series_id), "symbol": "",
                "label": str(meta.get("indicator") or meta.get("title") or series_id),
                "unit": str(meta.get("units_short") or meta.get("units") or ""),
                "points": points, "date": points[-1]["date"], "value": points[-1]["value"],
                "period": str(meta.get("frequency_short") or ""), "source": "fred_observations",
            }
    return catalog


def select_daily_market_datapoints(*, home: dict[str, Any], catalog: dict[str, dict[str, Any]], asof: object, run_id: str) -> pd.DataFrame:
    if len(catalog) < 5:
        raise ValueError("Daily datapoint selection requires at least five source-backed chart candidates.")
    candidates = [{
        **{key: value for key, value in candidate.items() if key != "points"},
        "chart_range": {"start": candidate["points"][0]["date"], "end": candidate["points"][-1]["date"], "observation_count": len(candidate["points"])},
        "observations": candidate["points"],
    } for candidate in catalog.values()]
    schema = {"type": "object", "additionalProperties": False, "properties": {"datapoints": {"type": "array", "minItems": 5, "maxItems": 5, "items": {
        "type": "object", "additionalProperties": False, "properties": {
            "id": {"type": "string", "enum": list(catalog)}, "title": {"type": "string"}, "significance": {"type": "string"},
        }, "required": ["id", "title", "significance"],
    }}}, "required": ["datapoints"]}
    context = {"homepage_summary": home.get("homepage_summary"), "market_coverage": home.get("market_coverage") or (home.get("coverage_summary") or {}).get("market_coverage")}
    evidence_fields = ("event_title", "headline", "symbol", "change_pct", "surface_summary_text", "surface_what_changed_text", "surface_why_text", "business_context_text", "watch_next_text", "surface_cause_status", "cause_status", "confidence_label", "same_day_evidence_count", "supporting_symbols")
    for section in ("top_events", "must_read_movers"):
        context[section] = [{key: str(item[key])[:1200] if isinstance(item.get(key), str) else item[key] for key in evidence_fields if key in item} for item in (home.get(section) or [])[:16] if isinstance(item, dict)]
    if isinstance(context.get("homepage_summary"), dict):
        context["homepage_summary"] = {key: value for key, value in context["homepage_summary"].items() if key in {"summary_text", "answer_markdown", "headline"}}
    selection_query = (
            f"As of {pd.Timestamp(asof).isoformat()}, choose the FIVE most crucial datapoints for today's informed investor daily brief. "
            "Rank by decision relevance to the session's evidence, not absolute price, popularity, or largest percentage move alone. "
            "Choose the five CHARTS that best explain what matters, not another top-movers leaderboard. "
            "Compare prices against corporate revenue, profitability, cash-generation trends and macro conditions. "
            "A relevant financial or macro trend can be more consequential than another price mover, even if it was not released today. "
            "Choose complementary evidence and distinct decision questions; avoid correlated versions of one story or repeating the brief's headlines. "
            "Every mover's price is already visible elsewhere on Home. Prefer charts that add material explanation beyond those price labels. "
            "The chosen chart must directly visualize the principal quantitative insight in its significance: if that insight is cash generation or revenue growth, choose that financial series. "
            "Use only catalog ids. Never invent a number, source, reporting period, release date, or cause. "
            "Quarterly financials and slow macro releases are background conditions unless evidence establishes a fresh release. "
            "Respect each observation's actual date; do not describe stale changes as today's move. "
            "Partial causes and plausible business channels are not proven causes. No universal ranking or 'only' claim from this bounded candidate catalog. "
            "Historical financial performance cannot establish or rule out the cause of today's repricing. Do not infer that a move was not a revenue, earnings, or demand event because a past quarter improved. "
            "Do not infer solvency, financing dependence, or balance-sheet strength from an income or cash-flow series alone. "
            "Check trend descriptions against every supplied observation: endpoint growth does not establish consecutive increases, and a range does not establish a direction. "
            "For seasonal quarterly businesses, compare the same fiscal quarter across years before claiming deterioration or acceleration. "
            "A sequential fall from a seasonal peak is not evidence of a structural collapse; consider the complete history and do not cherry-pick the comparator. "
            "The observations and chart_range are exactly the full displayed history. State explicit reporting periods or dates when discussing a subset of that history. "
            "Return a concise descriptive title (at most 65 characters) and one specific sentence of significance (at most 180 characters) for each. "
            "Significance connects a concrete chart finding to a documented decision in today's brief; it is not a chart-window inventory or a recap of endpoints alone. "
            "Dates and ranges are already visible in the chart. Include only the comparison periods needed to understand the insight, not metadata about what is displayed. "
            "Titles describe the insight without repeating the price or latest value already shown by the chart. "
            "No generic market commentary, process language, disclaimer, or evidence-gap filler. "
            "The application will render the exact referenced time series, units, dates and current values.\n\n"
            f"Daily brief evidence:\n{json.dumps(context, ensure_ascii=False, default=str)}\n\n"
            f"Candidate catalog:\n{json.dumps(candidates, ensure_ascii=False, default=str)}\n\n"
            "Before submitting, verify every cited date/value is actually present in its selected chart. "
            "Use period-matched financial comparisons, distinguish background from a new release, and avoid unsupported causes."
    )
    review_schema = {"type": "object", "additionalProperties": False, "properties": {
        "approved": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}},
    }, "required": ["approved", "issues"]}
    feedback = ""
    for attempt in range(2):
        result = run_aql_zopedia_structured_agent(
            query=selection_query + feedback,
            schema_name="daily_market_datapoints", schema=schema, task="daily_market_datapoints",
            surface="attention_home.daily_datapoints", max_tool_calls=0, persist_findings=False, write_policy="none",
        )
        if result.get("status") != "completed":
            raise RuntimeError("AQL/Zopedia could not complete the daily datapoint selection.")
        selections = (result.get("payload") or {}).get("datapoints") or []
        ids = [str(item.get("id") or "") for item in selections if isinstance(item, dict)]
        if len(ids) != 5 or len(set(ids)) != 5 or any(key not in catalog for key in ids):
            raise ValueError("Daily datapoints must reference five distinct catalog entries.")
        review = run_aql_zopedia_structured_agent(
            query=(
                "Review this five-chart investor brief against its exact supplied data and session evidence. "
                "Approve only if the chosen charts add complementary decision value and their titles and significance are accurate. "
                "Read the complete selected histories: verify each date, endpoint, percentage, sequence and reporting period. "
                "Every cited dated value must be inside that chart's displayed observations, not a neighboring series. "
                "Check same-period prior-year behavior before accepting a structural trend claim about seasonal quarterly data. "
                "Reject cherry-picked seasonal peaks, overstated direction/consecutive counts, invented causes, unsupported rankings, "
                "and stale observations presented as new releases. Evidence of a business channel is not proof of causation. "
                "Check negative causal claims as carefully as positive ones: old financial growth cannot rule out new revenue, demand, guidance, or earnings information behind today's price move. "
                "Require a clear connection to the day's decision context, but do not require each background series to have been released today. "
                "A significance sentence must explain why the quantitative finding matters in that context, not merely list endpoints or describe the chart window. "
                "Return approved=false with specific actionable issues when revision is needed; otherwise approved=true and no issues. "
                "Judge meaning, not preferred wording. Do not manufacture objections.\n\n"
                f"As of: {pd.Timestamp(asof).isoformat()}\nDaily evidence: {json.dumps(context, ensure_ascii=False, default=str)}\n"
                f"Draft: {json.dumps(selections, ensure_ascii=False)}\n"
                f"Exact selected charts: {json.dumps([catalog[key] for key in ids], ensure_ascii=False, default=str)}"
            ),
            schema_name="daily_market_datapoints_review", schema=review_schema, task="daily_market_datapoints_review",
            surface="attention_home.daily_datapoints.review", max_tool_calls=0, persist_findings=False, write_policy="none",
        )
        verdict = review.get("payload") or {}
        if review.get("status") != "completed":
            raise RuntimeError("AQL/Zopedia could not review the daily datapoint selection.")
        if verdict.get("approved") is True and not verdict.get("issues"):
            break
        print(f"[info] daily datapoint semantic review attempt={attempt + 1} issues="
              + json.dumps(verdict.get("issues") or [], ensure_ascii=False), flush=True)
        feedback = (
            "\n\nRevise this exact prior draft against its review. Preserve supported choices and comparisons; "
            "correct the rejected interpretations without introducing unrelated claims. Replace a chart only if "
            "it cannot support a useful decision-relevant finding. Do not restart the selection from scratch.\n"
            + json.dumps({"previous_draft": selections, "review": verdict}, ensure_ascii=False)
        )
    else:
        raise RuntimeError("Daily datapoint selection did not pass its source-backed semantic review: "
                           + json.dumps(verdict.get("issues") or [], ensure_ascii=False))
    rows = []
    for rank, item in enumerate(selections, 1):
        title = str(item.get("title") or "").strip()
        significance = str(item.get("significance") or "").strip()
        if not title or not significance:
            raise ValueError("Daily datapoints require a title and significance.")
        candidate = dict(catalog[item["id"]])
        points = candidate.pop("points")
        rows.append({**candidate, "rank": rank, "title": title[:100], "significance": significance[:260],
                     "points_json": json.dumps(points, ensure_ascii=False), "home_run_id": str(home.get("run_id") or ""),
                     "run_id": run_id, "asof_time_utc": pd.Timestamp(asof).isoformat(), "selection_method": "aql_zopedia"})
    return pd.DataFrame(rows)
