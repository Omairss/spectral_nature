"""AQL selects the day's five chart references; datasets own every plotted value."""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any

import pandas as pd

from data_access.query_service import QueryService
from services.aql_zopedia_engine import run_aql_zopedia_structured_agent
from services.llm import remaining_request_timeout
from services.llm_context import serialize_llm_context
from services.alpaca_api import PRICE_HISTORY_ADJUSTMENT
from services.aql.summarizer import build_attention_home_narrative_beats


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
        adjustment = row.get("price_adjustment")
        if not isinstance(adjustment, str) or not adjustment:
            adjustment = PRICE_HISTORY_ADJUSTMENT
        series = {"price": _points(row.get("price_trend_json"))}
        raw_financials = row.get("financial_trends_json")
        try:
            financials = json.loads(raw_financials) if isinstance(raw_financials, str) else raw_financials
        except (ValueError, TypeError):
            financials = {}
        if isinstance(financials, dict):
            series.update({key: _points(value) for key, value in financials.items()})
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
            if metric == "price":
                catalog[key]["price_adjustment"] = adjustment
                if adjustment != "raw":
                    catalog[key]["label"] = f"{symbol} · Adjusted price"
    metadata = {str(row.get("series_id") or ""): row for row in fred_summary.to_dict("records")}
    if {"series_id", "date", "value"}.issubset(fred_observations.columns):
        frame = fred_observations.copy()
        frame["date"] = pd.to_datetime(frame["date"], utc=True, errors="coerce")
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame = frame.dropna(subset=["date", "value"])
        frame = frame[(frame["date"] <= cutoff) & (frame["date"] >= cutoff - pd.Timedelta(days=365 * 3))]
        for series_id, rows in frame.groupby("series_id", sort=False):
            rows = rows.sort_values("date").drop_duplicates("date", keep="last")
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
    records = {
        key: {"title": candidate["label"], "metadata": {
            **{field: value for field, value in candidate.items() if field != "points"},
            "start_date": candidate["points"][0]["date"], "observation_count": len(candidate["points"]),
            "minimum_observation": min(candidate["points"], key=lambda point: point["value"]),
            "maximum_observation": max(candidate["points"], key=lambda point: point["value"]),
            "first_observation": candidate["points"][0],
        }, "content": candidate}
        for key, candidate in catalog.items()
    }
    summary = home.get("homepage_summary") or {}
    brief = {"generated_at_utc": home.get("generated_at_utc"),
             "stories": build_attention_home_narrative_beats(home)}
    brief["homepage_summary"] = {key: summary.get(key) for key in ("headline", "summary_text", "answer_markdown")} if isinstance(summary, dict) else summary
    records["daily_brief"] = {"title": "Today's complete investor brief", "metadata": {
        "kind": "daily_brief", "role": "Dated narrative context for relevance; chart originals establish plotted observations"}, "content": brief}
    # A generation input must not contain the output it is replacing. The old
    # captions otherwise become circular evidence for the next chart selection.
    # Preserve every research field and numeric source; exclude presentation
    # media and the previous chart artifact. The original bundle remains stored.
    home_context = {key: value for key, value in home.items() if key not in {"daily_datapoints", "homepage_summary"}}
    home_context["homepage_summary"] = brief["homepage_summary"]
    records["home_context"] = {"title": "Complete Home research context", "metadata": {
        "kind": "derived_research", "role": "Dated research context; individual chart originals establish plotted observations"}, "content": home_context}
    service = QueryService.from_environment().with_supplied_evidence(records)
    schema = {"type": "object", "additionalProperties": False, "properties": {"datapoints": {"type": "array", "minItems": 5, "maxItems": 5, "items": {
        "type": "object", "additionalProperties": False, "properties": {
            "id": {"type": "string", "enum": list(catalog)}, "title": {"type": "string", "maxLength": 65}, "significance": {"type": "string", "maxLength": 180},
        }, "required": ["id", "title", "significance"],
    }}}, "required": ["datapoints"]}
    context = brief
    selection_query = (
            f"As of {pd.Timestamp(asof).isoformat()}, choose the FIVE most decision-relevant charts for this investor daily brief. "
            "Use the research and chart catalog to decide which questions matter and which complementary series best illuminate them. "
            "For each, explain a concrete finding and why it matters in this context, distinguishing dated observations from your interpretation. "
            "Return its catalog id, a descriptive title, and one specific sentence of significance. "
            "The application renders the exact original history with its units, dates and current values.\n\n"
            f"Daily brief evidence:\n{serialize_llm_context(context)}\n\n"
            f"Supplied evidence manifest: {json.dumps(service.supplied_evidence_manifest)}"
    )
    review_schema = {"type": "object", "additionalProperties": False, "properties": {
        "approved": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}},
    }, "required": ["approved", "issues"]}
    feedback = ""
    selections = []
    review_state: dict[str, Any] = {"status": "not_run", "attempts": []}
    try:
        operation_timeout = max(1, int(os.getenv("DAILY_MARKET_DATAPOINTS_TIMEOUT_SECONDS", "600")))
    except ValueError:
        operation_timeout = 600
    operation_deadline = time.monotonic() + operation_timeout
    for attempt in range(2):
        try:
            initial_evidence = [{"title": "Evidence navigation", **service.supplied_evidence_manifest}]
            if selections:
                # Revision owns the same complete originals already reviewed,
                # rather than purchasing their acquisition again from scratch.
                initial_evidence.append({"title": "Original histories for the preceding chart draft",
                                         "charts": [catalog[item["id"]] for item in selections]})
            result = run_aql_zopedia_structured_agent(
                query=selection_query + feedback,
                schema_name="daily_market_datapoints", schema=schema, task="daily_market_datapoints",
                surface="attention_home.daily_datapoints", service=service,
                initial_evidence=initial_evidence,
                request_context={"allow_web": False, "allowed_source_access": ["supplied"]},
                answer_review_owner="downstream", persist_findings=False, write_policy="none",
                llm_step_timeout_seconds=remaining_request_timeout(180),
                run_timeout_seconds=min(remaining_request_timeout(180), max(0.0, operation_deadline - time.monotonic())),
            )
            if result.get("status") != "completed":
                raise RuntimeError("AQL/Zopedia could not complete the daily datapoint selection: " + str(result.get("error") or "incomplete response"))
            draft = (result.get("payload") or {}).get("datapoints") or []
            ids = [str(item.get("id") or "") for item in draft if isinstance(item, dict)]
            if len(ids) != 5 or len(set(ids)) != 5 or any(key not in catalog for key in ids):
                raise ValueError("Daily datapoints must reference five distinct catalog entries.")
            if any(not str(item.get(key) or "").strip() for item in draft for key in ("title", "significance")):
                raise ValueError("Daily datapoints require a title and significance.")
            selections = draft
            review = run_aql_zopedia_structured_agent(
                query=(
                    "Review this five-chart brief against its exact supplied data and session evidence. "
                    "Read the selected histories and identify incorrect values, dates, periods, trend descriptions or causes. "
                    "\n\n"
                    f"As of: {pd.Timestamp(asof).isoformat()}\nDaily evidence: {serialize_llm_context(context)}\n"
                    f"Draft: {json.dumps(selections, ensure_ascii=False)}\n"
                    f"Exact selected charts: {json.dumps([catalog[key] for key in ids], ensure_ascii=False, default=str)}"
                ),
                schema_name="daily_market_datapoints_review", schema=review_schema, task="daily_market_datapoints_review",
                service=service, request_context={"allow_web": False, "allowed_source_access": ["supplied"]},
                surface="attention_home.daily_datapoints.review", max_tool_calls=0, persist_findings=False, write_policy="none",
                initial_evidence=[{"original_request": selection_query}],
                run_timeout_seconds=min(remaining_request_timeout(120), max(0.0, operation_deadline - time.monotonic())),
            )
            if review.get("status") != "completed":
                raise RuntimeError("AQL/Zopedia could not review the daily datapoint selection: " + str(review.get("error") or "incomplete response"))
            verdict = review.get("payload") or {}
            review_state["attempts"].append(verdict)
            if verdict.get("approved") is True:
                review_state["status"] = "accepted"
                break
            review_state["status"] = "needs_follow_up"
            print(f"[info] daily datapoint semantic review attempt={attempt + 1} issues="
                  + json.dumps(verdict.get("issues") or [], ensure_ascii=False), flush=True)
            feedback = (
                "\n\nConsider these suggested revisions alongside the original sources, "
                "then improve the draft for the original request.\n"
                + json.dumps({"previous_draft": selections, "review": verdict}, ensure_ascii=False)
            )
        except Exception as exc:
            if not selections:
                raise
            review_state.update(status="incomplete", error=f"{type(exc).__name__}: {exc}")
            print("[info] daily datapoint review incomplete; valid draft retained: "
                  + str(review_state["error"])[:1500], flush=True)
            break
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
                     "run_id": run_id, "asof_time_utc": pd.Timestamp(asof).isoformat(), "selection_method": "aql_zopedia",
                     "quality_review_json": json.dumps(review_state, ensure_ascii=False)})
    return pd.DataFrame(rows)
