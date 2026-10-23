"""Freeze prospective source inputs; outcome resolution is a separate process."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request


RESEARCH = Path.home() / "Library/Application Support/SpectralNature/research"
ROOT = RESEARCH / "2026-10-22-analyst-evaluation/forecast"
# Development investigations selected for different business mechanisms in the
# unlabelled current-article collection, without consulting future outcomes.
COMPANIES = [
    {"ticker": "HIMS", "article_id": "4955180"},
    {"ticker": "GH", "article_id": "4955182"},
    {"ticker": "AMD", "article_id": "4955111"},
    {"ticker": "MAA", "article_id": "4955140"},
    {"ticker": "O", "article_id": "4955134"},
    {"ticker": "GNK", "article_id": "4955139"},
    {"ticker": "LCID", "article_id": "4955183"},
    {"ticker": "HAL", "article_id": "4954495"},
]


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def acquire(url: str, path: Path, user_agent: str) -> dict:
    if path.exists():
        receipt = json.loads(path.with_suffix(path.suffix + ".receipt.json").read_text())
        if sha(path.read_bytes()) != receipt["sha256"]:
            raise ValueError("Frozen download changed")
        return receipt
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read()
    json.loads(raw)
    path.write_bytes(raw)
    receipt = {"url": url, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
               "sha256": sha(raw), "bytes": len(raw), "file": str(path)}
    path.with_suffix(path.suffix + ".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    time.sleep(1)
    return receipt


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    actor, raw_dir = ROOT / "actor", ROOT / "source-downloads"
    actor.mkdir(exist_ok=True)
    raw_dir.mkdir(exist_ok=True)
    if (actor / "manifest.json").exists():
        raise ValueError("Do not mutate a frozen forecast package")
    contact = subprocess.check_output(["git", "config", "user.email"], text=True).strip()
    # Do not print/store the contact header in model context or receipts.
    user_agent = "SpectralNature financial research " + contact
    ticker_receipt = acquire("https://www.sec.gov/files/company_tickers.json", raw_dir / "sec-tickers.json", user_agent)
    tickers = {value["ticker"]: value for value in json.loads((raw_dir / "sec-tickers.json").read_text()).values()}
    receipts = [ticker_receipt]
    acquisition_cutoff = datetime.now(timezone.utc)
    for ticker in [c["ticker"] for c in COMPANIES] + ["SPY"]:
        start = acquisition_cutoff - timedelta(days=365 * 8)
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={int(start.timestamp())}"
               f"&period2={int(acquisition_cutoff.timestamp())}&interval=1d&events=div%2Csplits")
        try:
            receipts.append(acquire(url, raw_dir / f"prices-{ticker}.json", "Mozilla/5.0 SpectralNatureResearch/1.0"))
        except Exception as exc:
            print(json.dumps({"ticker": ticker, "source": "price_history", "error": str(exc)}), flush=True)
    for company in COMPANIES:
        record = tickers[company["ticker"]]
        company.update(cik=int(record["cik_str"]), company=record["title"])
        cik = f"{company['cik']:010d}"
        try:
            receipts.append(acquire(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                                    raw_dir / f"facts-{company['ticker']}.json", user_agent))
        except Exception as exc:
            print(json.dumps({"ticker": company["ticker"], "source": "company_facts", "error": str(exc)}), flush=True)
    freeze = datetime.now(timezone.utc)
    issue_deadline = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    if freeze >= issue_deadline:
        raise ValueError("This cohort's prospective issue window has closed; define a new cohort instead of backdating")
    sources, corpus_receipts = {}, []
    for collection in ["2026-10-19-seeking-alpha-corpus", "2026-10-20-seeking-alpha-foresight"]:
        store = RESEARCH / collection
        manifest = json.loads((store / "acquisition-manifest.json").read_text())
        for article in manifest["articles"]:
            captured = datetime.fromisoformat(article["captured_at_utc"].replace("Z", "+00:00"))
            if captured > freeze:
                raise ValueError("Article was not observed before the decision cutoff")
            body = (store / article["body_file"]).read_bytes()
            if sha(body) != article["body_sha256"]:
                raise ValueError("Original article hash mismatch")
            source_id = "sa_" + str(article["article_id"])
            sources[source_id] = {
                "title": article["title"],
                "metadata": {"url": article["url"],
                             "publication_time_as_displayed": article["publication_time_as_displayed"],
                             "observed_at_utc": article["captured_at_utc"]},
                "content": {"body": body.decode("utf-8")}}
            corpus_receipts.append({"source_id": source_id, "sha256": sha(body), "bytes": len(body),
                                    "observed_at_utc": article["captured_at_utc"]})
    datasets = {}
    dataset_receipts = []
    for company in COMPANIES:
        ticker = company["ticker"]
        path = raw_dir / f"facts-{ticker}.json"
        if not path.exists():
            continue
        raw = json.loads(path.read_text())
        observations, concepts = [], []
        for namespace, facts in raw.get("facts", {}).items():
            for concept, spec in facts.items():
                count = 0
                for unit, records in spec.get("units", {}).items():
                    for record in records:
                        if record.get("filed", "9999") > freeze.date().isoformat() or record.get("end", "9999") > freeze.date().isoformat():
                            continue
                        observations.append({"ticker": ticker, "namespace": namespace, "concept": concept,
                                             "label": spec.get("label", concept), "unit": unit,
                                             **{k: record.get(k) for k in ["val", "start", "end", "filed", "form", "fy", "fp", "accn", "frame"]}})
                        count += 1
                concepts.append({"namespace": namespace, "concept": concept, "label": spec.get("label"),
                                 "description": spec.get("description"), "units": list(spec.get("units", {})), "observations": count})
        for kind, records in [("facts", observations), ("concepts", concepts)]:
            key = f"{kind}_{ticker}"
            target = actor / f"{key}.json"
            target.write_text(json.dumps(records, ensure_ascii=False) + "\n")
            datasets[key] = target.name
            dataset_receipts.append({"dataset": key, "rows": len(records), "sha256": sha(target.read_bytes()), "bytes": target.stat().st_size})
    for ticker in [c["ticker"] for c in COMPANIES] + ["SPY"]:
        path = raw_dir / f"prices-{ticker}.json"
        if not path.exists():
            continue
        chart = json.loads(path.read_text())["chart"]["result"][0]
        quote = chart["indicators"]["quote"][0]
        adjusted = chart["indicators"].get("adjclose", [{}])[0].get("adjclose", [])
        records = []
        for i, timestamp in enumerate(chart.get("timestamp", [])):
            observed = datetime.fromtimestamp(timestamp, timezone.utc)
            if observed > freeze:
                raise ValueError("Future price observation in decision-time package")
            records.append({"ticker": ticker, "date": observed.date().isoformat(),
                            **{k: quote[k][i] for k in ["open", "high", "low", "close", "volume"]},
                            "adjusted_close": adjusted[i] if i < len(adjusted) else None})
        key = f"prices_{ticker}"; target = actor / f"{key}.json"
        target.write_text(json.dumps(records) + "\n"); datasets[key] = target.name
        dataset_receipts.append({"dataset": key, "rows": len(records), "sha256": sha(target.read_bytes()), "bytes": target.stat().st_size})
    (actor / "sources.json").write_text(json.dumps(sources, ensure_ascii=False) + "\n")
    tasks = []
    for company in COMPANIES:
        tasks.append({"id": "sa_prospective_" + company["ticker"], **company,
            "lead_source_id": "sa_" + company["article_id"],
            "decision_cutoff_utc": freeze.isoformat(), "issue_deadline_utc": issue_deadline.isoformat(),
            "targets": {
                "annual_revenue": {"fiscal_year": 2027, "unit": "USD", "resolution": "First publicly reported full-year consolidated revenue for the company's fiscal year ending in calendar2027. Preserve the actual fiscal period; do not substitute net income, ARR or gross billings."},
                "stock_total_return": {"entry_session": "2026-10-23", "exit_session": "2027-01-21", "unit": "fraction", "resolution": "Adjusted-close total return from entry close to exit close; if a specified session is closed, use the first session on/after it. Resolve corporate actions or delisting separately instead of dropping the case."},
                "outperform_spy": {"same_holding_window": True, "resolution": "Stock adjusted-close holding return strictly exceeds SPY's corresponding holding return."}},
            "available_datasets": [k for k in datasets if k.endswith("_" + company["ticker"]) or k == "prices_SPY"]})
    (actor / "tasks.json").write_text(json.dumps(tasks, indent=2) + "\n")
    manifest = {"frozen_at_utc": freeze.isoformat(), "cohort": "prospective_development_october22",
                "issue_deadline_utc": issue_deadline.isoformat(), "companies": COMPANIES,
                "source_count": len(sources), "body_utf8_bytes": sum(x["bytes"] for x in corpus_receipts),
                "sources_sha256": sha((actor / "sources.json").read_bytes()),
                "tasks_sha256": sha((actor / "tasks.json").read_bytes()),
                "datasets": datasets, "dataset_receipts": dataset_receipts,
                "actor_source_fields": {"record": ["title", "metadata", "content"], "metadata": ["url", "publication_time_as_displayed", "observed_at_utc"], "content": ["body"]},
                "input_policy": "Only sources captured before freeze, facts filed and periods ending by freeze, and frozen pre-entry prices. No cohort labels, retrospective reviews, outcomes, return files, reference answers, live web, serving memory or future observations admitted. Full sources available by controlled reader; source-to-compute references retain complete selected datasets.",
                "historical_versions": "Observed current views are valid inputs to NEW future targets; they are not certified original-publication inputs to old historical forecasts.",
                "scoring": "Save forecasts before entry and before target outcomes occur. Separate evaluator later resolves official reported revenue, adjusted-price returns and the binary outperform event. Score median absolute error, quantile losses/80% interval coverage and Brier score, alongside simple predeclared controls and personal hypothesis review. Immature targets remain pending, never counted as correct.",
                "selection": "Eight development issuer investigations with different mechanisms from the unlabelled current-article collection; all259 retained articles are decision-time background. This is not a representative final cohort.",
                "forecast_status": "not_issued"}
    (actor / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (ROOT / "acquisition.json").write_text(json.dumps(receipts, indent=2) + "\n")
    (ROOT / "corpus-receipts.json").write_text(json.dumps(corpus_receipts, indent=2) + "\n")
    print(json.dumps({"source_count": len(sources), "tasks": len(tasks), "datasets": len(datasets), "frozen_at_utc": freeze.isoformat(), "dataset_rows": {x["dataset"]: x["rows"] for x in dataset_receipts}}), flush=True)


if __name__ == "__main__":
    main()
