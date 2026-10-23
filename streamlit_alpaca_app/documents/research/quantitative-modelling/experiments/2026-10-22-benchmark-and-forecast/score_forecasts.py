"""Score locked forecasts against primary-agent-reviewed outcomes. No model calls.

Resolution records are evaluator-only. This command never acquires or invents
labels, changes a prediction, or exposes outcomes to the research engine.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def continuous_score(quantiles: dict, actual: float) -> dict:
    q = [float(quantiles[k]) for k in ("p10", "p50", "p90")]
    if not all(math.isfinite(v) for v in [actual, *q]):
        raise ValueError("Forecasts and observations must be finite")
    losses = {name: (level - int(actual < value)) * (actual - value)
              for name, level, value in zip(("p10", "p50", "p90"), (.1, .5, .9), q)}
    ordered = q[0] <= q[1] <= q[2]
    interval = (q[2] - q[0] + 10 * max(q[0] - actual, 0) + 10 * max(actual - q[2], 0)) if ordered else None
    return {"actual": actual, "absolute_median_error": abs(q[1] - actual),
            "pinball_loss": losses, "ordered_quantiles": ordered,
            "central_80_percent_coverage": q[0] <= actual <= q[2] if ordered else None,
            "interval_score_80_percent": interval,
            "weighted_interval_score": (.5 * abs(q[1] - actual) + .1 * interval) / 1.5 if ordered else None}


def checked_observation(record: dict, *, spec_sha: str, target: str, now: datetime) -> float:
    if record.get("reviewer") != "primary_agent_personal_review" or record.get("spec_sha256") != spec_sha:
        raise ValueError("Outcomes require primary-agent personal review against this frozen target specification")
    if record.get("target") != target or record.get("status") != "resolved":
        raise ValueError("Outcome target/status mismatch")
    reviewed = datetime.fromisoformat(record["reviewed_at_utc"])
    available = datetime.fromisoformat(record["first_public_at_utc"])
    if any(v.tzinfo is None for v in (reviewed, available)) or not available <= reviewed <= now:
        raise ValueError("Outcome publication/review clocks are invalid")
    period_end = date.fromisoformat(record["period_end"])
    if period_end > available.date():
        raise ValueError("The actual period was not complete when this result became available")
    if not record.get("resolution_note") or not record.get("sources"):
        raise ValueError("A resolution needs source records and a personal explanation of the measurement")
    for source in record["sources"]:
        path = Path(source["retained_file"])
        if not source.get("url") or digest(path) != source["sha256"]:
            raise ValueError("Missing or changed outcome source")
    value = float(record["value"])
    if not math.isfinite(value):
        raise ValueError("Outcome must be finite")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forecast-root", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--resolutions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    spec_sha = digest(args.spec)
    now = datetime.now(timezone.utc)
    results = []
    for company in spec["companies"]:
        ticker = company["ticker"]
        prediction_path = args.forecast_root / "predictions" / f"{ticker}.json"
        row = {"ticker": ticker, "targets": {}, "status": "prediction_not_recorded"}
        results.append(row)
        if not prediction_path.exists():
            continue
        prediction = json.loads(prediction_path.read_text())
        row.update(prediction_sha256=digest(prediction_path), issued_at_utc=prediction["issued_at_utc"],
                   status="pending_outcomes", before_issue_deadline=prediction["before_issue_deadline"])
        payload = ((prediction.get("result") or {}).get("payload") or {}).get("forecasts") or {}
        resolution_path = args.resolutions / f"{ticker}.json"
        resolution = json.loads(resolution_path.read_text()) if resolution_path.exists() else {}
        if resolution and (resolution.get("prediction_sha256") != row["prediction_sha256"] or
                           resolution.get("cik") != company["cik"]):
            raise ValueError("Resolution must identify this exact prediction and issuer")
        resolved_values = {}
        for target in ("annual_revenue_usd", "stock_total_return", "spy_total_return"):
            record = resolution.get(target)
            if not record or record.get("status") != "resolved":
                row["targets"][target] = {"status": "pending", "score": None}
                continue
            actual = checked_observation(record, spec_sha=spec_sha, target=target, now=now)
            if target == "annual_revenue_usd":
                if record.get("fiscal_year") != 2027 or record.get("unit") != "USD":
                    raise ValueError("Revenue must be the specified fiscal2027 consolidated USD actual")
                if date.fromisoformat(record["period_end"]).year != 2027:
                    raise ValueError("Revenue period must end in calendar2027")
            else:
                if (record.get("entry_session") != spec["returns"]["entry_session"] or
                    record.get("exit_session") != spec["returns"]["exit_session"] or record.get("unit") != "fraction"):
                    raise ValueError("Return target window/unit differs from the locked question")
                if date.fromisoformat(record["period_end"]) < date.fromisoformat(spec["returns"]["exit_session"]):
                    raise ValueError("Return observation ends before the specified exit")
            resolved_values[target] = actual
            score = continuous_score(payload[target], actual) if target in payload else None
            row["targets"][target] = {"status": "resolved", "score": score,
                                      "observation": actual, "forecast_present": target in payload}
        if {"stock_total_return", "spy_total_return"} <= resolved_values.keys():
            actual = int(resolved_values["stock_total_return"] > resolved_values["spy_total_return"])
            probability = payload.get("probability_outperform_spy")
            valid = isinstance(probability, (int, float)) and math.isfinite(probability) and 0 <= probability <= 1
            row["targets"]["outperform_spy"] = {"status": "resolved", "actual": actual,
                "probability": probability, "valid_probability": valid,
                "brier_score": (probability - actual) ** 2 if valid else None,
                "constant_half_probability_brier": .25}
        else:
            row["targets"]["outperform_spy"] = {"status": "pending", "score": None}
        if not prediction["before_issue_deadline"]:
            row["status"] = "late_prediction_retained_separately"
        elif prediction.get("error") or not payload:
            row["status"] = "failed_prediction_retained"
        elif all(v["status"] == "resolved" for v in row["targets"].values()):
            row["status"] = "resolved"
    output = {"scored_at_utc": now.isoformat(), "ground_truth_spec_sha256": spec_sha,
              "ground_truth_owner": "primary_agent_personal_review", "model_judges_used": False,
              "cohort_size": len(results), "results": results,
              "aggregation": "Different units are not pooled. Missing/failed/late predictions and pending outcomes remain explicit. Qualitative hypothesis assessment is separate."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"cohort_size": len(results), "statuses": {r["ticker"]: r["status"] for r in results}}))


if __name__ == "__main__":
    main()
