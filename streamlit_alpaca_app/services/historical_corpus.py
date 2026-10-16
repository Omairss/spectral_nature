"""Offline historical acquisition and frozen frames using shared source/store owners.

No interpretation, entity classification or market narrative is generated here.
The editable collection specification supplies research seeds and dated anchors.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import unescape
from io import BytesIO
from pathlib import Path
from threading import Lock, BoundedSemaphore
from typing import Any
from urllib.parse import urlparse
import gzip
import hashlib
import itertools
import json
import os
import re
import time
import zipfile
import tempfile
import fcntl
import traceback

import pandas as pd
import requests
from lxml import html, etree

from services.alpaca_api import AlpacaAPI, AlpacaAPIError
from services.config import load_config
from services.fred import FREDClient, load_fred_api_key
from services.pipeline_store import ImmutableBlobArchive
from services.treasury_yields import parse_treasury_rate_feed, TREASURY_XML_BASE_URL


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                      separators=(",", ":")).encode()


def date_available_at(values, timezone_name: str) -> pd.Series:
    dates = pd.to_datetime(values, utc=True, errors="coerce")
    if isinstance(values.dtype, pd.DatetimeTZDtype):
        dates = dates.dt.tz_convert(timezone_name)
    # Source date-only releases become eligible after the source's local date
    # ends. UTC midnight can precede a late US release or completed daily bar.
    local_midnight = dates.dt.tz_localize(None).dt.normalize() + pd.Timedelta(days=1)
    return local_midnight.dt.tz_localize(timezone_name).dt.tz_convert("UTC")


def normalize_historical_availability(frame: pd.DataFrame, dataset: str) -> pd.DataFrame:
    """Shared source-date contract, also used when reading earlier receipts."""
    if frame.empty:
        return frame
    frame = frame.copy()
    zone, dates = None, None
    if dataset.startswith("stock_bars_"):
        zone, dates = "America/New_York", pd.to_datetime(frame["observation_time"], utc=True)
    elif dataset == "macro_vintage_observations":
        zone, dates = "America/Chicago", frame["realtime_start"].astype(str)
    elif dataset.startswith("daily_treasury_"):
        zone, dates = "America/New_York", frame["date"].astype(str).str[:10]
    elif dataset == "corporate_actions" and "process_date" in frame:
        zone, dates = "America/New_York", frame["process_date"].astype(str).str[:10]
    elif dataset.startswith("company_") and "Publish Date" in frame:
        zone = "America/New_York"
        dates = frame[["Publish Date", "Restated Date"]].max(axis=1).astype(str).str[:10]
    elif dataset == "sec_company_facts" and "filed" in frame:
        zone, dates = "America/New_York", frame["filed"].astype(str)
    if dates is not None:
        frame["available_at"] = date_available_at(dates, zone)
        frame["availability_timezone"] = zone
        frame["availability_policy"] = "after_source_local_date_ends"
    return frame


def clean_html(value: Any) -> str:
    source = str(value or "").strip()
    if not source:
        return ""
    tree = html.fromstring(source)
    etree.strip_elements(tree, "script", "style", "noscript", "nav", "footer", "aside", "form", "button", with_tail=False)
    for node in tree.iter():
        if node.tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "section"}:
            node.tail = "\n" + (node.tail or "")
    return unescape(tree.text_content()).replace("\x00", "").strip()


class SourcePacing:
    def __init__(self):
        self.lock = Lock()
        self.next: dict[str, float] = {}
        self.limits = {"alpaca.markets": .50, "stlouisfed.org": .55,
                       "algolia.com": .40, "sec.gov": .55,
                       "gdeltproject.org": .20}

    def wait(self, host: str):
        interval = next((v for k, v in self.limits.items() if host.endswith(k)), .25)
        with self.lock:
            now = time.monotonic()
            delay = max(0., self.next.get(host, now) - now)
            self.next[host] = now + delay + interval
        if delay:
            time.sleep(delay)


class HistoricalCorpus:
    schema_version = 1

    def __init__(self, specification: dict[str, Any], state_dir: Path):
        self.spec = specification
        self.state = state_dir
        self.state.mkdir(parents=True, exist_ok=True)
        self.archive = ImmutableBlobArchive(namespace=specification["namespace"], root="historical_context")
        self.pacing = SourcePacing()
        self.alpaca = AlpacaAPI(load_config())
        if self.alpaca.config is None:
            raise RuntimeError("Existing Alpaca credentials unavailable")
        self.fred = FREDClient(load_fred_api_key(), before_request=lambda: self.pacing.wait("api.stlouisfed.org"))
        self.lock = Lock()
        self.gdelt_slots = BoundedSemaphore(4)
        self.unsupported_symbols: set[str] = set()
        unavailable = self.state / "provider_unavailable_symbols.json"
        if unavailable.exists():
            self.unsupported_symbols.update(json.loads(unavailable.read_text()))
        for path in (self.state / "tasks").glob("*/*.json"):
            result = json.loads(path.read_text())
            self.unsupported_symbols.update(result.get("coverage", {}).get("unavailable_symbols", []))
        self.start = pd.Timestamp(specification["start"], tz="UTC")
        self.end = pd.Timestamp(specification["end"], tz="UTC")
        if self.end > pd.Timestamp.now(tz="UTC"):
            raise ValueError("Collection cutoff cannot be in the future")
        self.spec_hash = hashlib.sha256(json_bytes(specification)).hexdigest()
        self.normalizer_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def raw(self, value: Any, *, source: str, request: dict[str, Any]) -> dict[str, Any]:
        return self.archive.put(gzip.compress(json_bytes({
            "source": source, "request": request, "acquired_at_utc": utcnow(), "payload": value,
        }), mtime=0), suffix="json.gz", content_type="application/gzip")

    def table(self, frame: pd.DataFrame, *, dataset: str, visibility: str = "time_filtered") -> dict[str, Any]:
        frame = normalize_historical_availability(frame, dataset)
        if "observation_time" in frame and not frame.empty:
            observed = pd.to_datetime(frame["observation_time"], utc=True, errors="coerce")
            frame = frame[(observed >= self.start) & (observed < self.end)].copy()
        for col in frame.select_dtypes(include="object").columns:
            if any(isinstance(v, (dict, list, tuple)) for v in frame[col].dropna().head(200)):
                frame[col] = frame[col].map(lambda v: json_bytes(v).decode() if isinstance(v, (dict, list, tuple)) else v)
        buf = BytesIO()
        frame.to_parquet(buf, index=False, compression="zstd")
        receipt = self.archive.put(buf.getvalue(), suffix="parquet", content_type="application/vnd.apache.parquet")
        receipt.update(dataset=dataset, rows=len(frame), columns=list(frame.columns), visibility=visibility)
        receipt["normalizer_sha256"] = self.normalizer_sha256
        receipt["availability_contract_version"] = 2
        if dataset.startswith("daily_treasury_"):
            receipt["treasury_schema_version"] = 2
        for column in ["observation_time", "observation_date", "date", "published_at", "available_at"]:
            if column in frame and not frame.empty:
                values = pd.to_datetime(frame[column], utc=True, errors="coerce").dropna()
                if not values.empty:
                    receipt.setdefault("time_bounds", {})[column] = [values.min().isoformat(), values.max().isoformat()]
        return receipt

    def get(self, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> requests.Response:
        host = urlparse(url).hostname or ""
        for attempt in range(4):
            self.pacing.wait(host)
            try:
                response = requests.get(url, params=params, headers={
                    "User-Agent": self.spec["user_agent"], **(headers or {})}, timeout=(15, 90))
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 3:
                    raise RuntimeError(f"Source transport unavailable: {host}") from None
                time.sleep(2 ** attempt)
                continue
            if response.status_code < 400:
                return response
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                delay = response.headers.get("Retry-After", "")
                time.sleep(min(float(delay) if delay.isdigit() else 2 ** attempt, 45))
                continue
            raise RuntimeError(f"Source HTTP {response.status_code}: {host}")
        raise RuntimeError("Source retry contract exhausted")

    def _receipt_path(self, task: dict[str, Any]) -> Path:
        key = hashlib.sha256(json_bytes({"spec": self.spec_hash, "task": task})).hexdigest()
        path = self.state / "tasks" / key[:2] / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _save(self, path: Path, value: Any):
        # Unique sibling temporaries keep atomic publication safe even when
        # independent owners publish the same discovery/status receipt.
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json_bytes(value))
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def run_task(self, task: dict[str, Any]) -> dict[str, Any]:
        path = self._receipt_path(task)
        # Scope ownership to the exact frozen-spec/task identity. Process death
        # releases the OS lock; repeated launchers resume the same checkpoint.
        with path.with_suffix(".lock").open("a") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            return self._run_task(task, path)

    def _run_task(self, task: dict[str, Any], path: Path) -> dict[str, Any]:
        if path.exists():
            old = json.loads(path.read_text())
            if old["status"] == "complete":
                return old
        started = utcnow()
        try:
            outputs, details = getattr(self, "collect_" + task["kind"])(task)
            result = {"task": task, "status": "complete", "outputs": outputs,
                      "coverage": details, "started_at_utc": started, "finished_at_utc": utcnow()}
        except Exception as exc:
            # Never serialize authenticated request URLs or credential-bearing exceptions.
            result = {"task": task, "status": "failed", "outputs": [],
                      "error_type": type(exc).__name__, "started_at_utc": started, "finished_at_utc": utcnow()}
            result["error_trace"] = [{"module": Path(f.filename).name, "function": f.name, "line": f.lineno}
                for f in traceback.extract_tb(exc.__traceback__)]
            if isinstance(exc, RuntimeError) and str(exc).startswith("Source HTTP"):
                result["error"] = str(exc)
            if isinstance(exc, AlpacaAPIError):
                result["error"] = {"status_code": exc.status_code, "response": exc.payload}
        self._save(path, result)
        return result

    def alpaca_pages(self, task: dict[str, Any], path: str, params: dict[str, Any], normalize):
        base = self._receipt_path(task).with_suffix("")
        base.mkdir(exist_ok=True)
        saved = sorted(base.glob("*.json"))
        outputs: list[dict[str, Any]] = []
        coverage: dict[str, Any] = {"pages": 0, "rows": 0}
        token = None
        for page_path in saved:
            page = json.loads(page_path.read_text())
            outputs.extend(page["outputs"])
            coverage["pages"] += 1
            coverage["rows"] += page["rows"]
            token = page["next_page_token"]
        if saved and not token:
            return outputs, coverage
        pages = self.alpaca.iter_market_data_pages(path, params, page_token=token)
        while True:
            self.pacing.wait("data.alpaca.markets")
            try:
                payload = next(pages)
            except StopIteration:
                break
            query = {**params, **({"page_token": token} if token else {})}
            raw = self.raw(payload, source="alpaca", request={"path": path, "params": query})
            frame, dataset, visibility = normalize(payload)
            frame["raw_sha256"] = raw["sha256"]
            parts = [raw, self.table(frame, dataset=dataset, visibility=visibility)]
            outputs.extend(parts)
            token = payload.get("next_page_token")
            page = {"outputs": parts, "rows": len(frame), "next_page_token": token}
            self._save(base / f"{coverage['pages']:06d}.json", page)
            coverage["pages"] += 1
            coverage["rows"] += len(frame)
            if not token:
                return outputs, coverage
        return outputs, coverage

    def collect_catalog(self, task):
        self.pacing.wait("api.alpaca.markets")
        params = {"status": task["status"], "asset_class": task["asset_class"]}
        data = self.alpaca._request("GET", "https://api.alpaca.markets", "/v2/assets", params=params)
        raw = self.raw(data, source="alpaca_assets", request=params)
        frame = pd.DataFrame(data)
        frame["catalog_asof_utc"] = utcnow()
        return [raw, self.table(frame, dataset="instrument_catalog", visibility="catalog_asof_only")], {"assets": len(frame)}

    @staticmethod
    def normalize_bars(payload, *, dataset, adjustment):
        rows = [{"symbol": symbol, **bar} for symbol, bars in (payload.get("bars") or {}).items() for bar in bars]
        frame = pd.DataFrame(rows).rename(columns={"t": "observation_time", "o": "open", "h": "high",
            "l": "low", "c": "close", "v": "volume", "n": "trade_count", "vw": "vwap"})
        if not frame.empty:
            frame["observation_time"] = pd.to_datetime(frame["observation_time"], utc=True)
            frame["available_at"] = frame["observation_time"].dt.floor("D") + pd.Timedelta(days=1)
            frame = frame.drop_duplicates(["symbol", "observation_time"]).sort_values(["symbol", "observation_time"])
        frame["adjustment"] = adjustment
        frame["availability_policy"] = "conservative_next_utc_day"
        return frame, dataset, "outcomes_only" if adjustment != "raw" else "time_filtered"

    def collect_bars(self, task):
        remaining = [s for s in task["symbols"] if s not in self.unsupported_symbols]
        params = {"symbols": ",".join(remaining), "start": self.start.isoformat(),
                  "end": self.end.isoformat(), "timeframe": "1Day", "adjustment": task["adjustment"],
                  "feed": task.get("feed", "sip"), "asof": task.get("asof", self.spec["end"][:10]), "sort": "asc", "limit": 10000}
        dataset = "stock_bars_raw" if task["adjustment"] == "raw" else "stock_bars_adjusted_outcomes"
        while remaining:
            params["symbols"] = ",".join(remaining)
            try:
                outputs, coverage = self.alpaca_pages(task, "/v2/stocks/bars", params,
                    lambda p: self.normalize_bars(p, dataset=dataset, adjustment=task["adjustment"]))
                break
            except AlpacaAPIError as exc:
                message = (exc.payload or {}).get("message", "") if isinstance(exc.payload, dict) else ""
                prefix = "invalid symbol: "
                rejected = message[len(prefix):].strip() if message.startswith(prefix) else None
                if exc.status_code != 400 or rejected not in remaining:
                    raise
                # A provider-rejected catalog identifier must not discard the
                # other valid instruments in the same request. Retain the gap.
                with self.lock:
                    self.unsupported_symbols.add(rejected)
                    self._save(self.state / "provider_unavailable_symbols.json", sorted(self.unsupported_symbols))
                remaining.remove(rejected)
        else:
            outputs, coverage = [], {"pages": 0, "rows": 0}
        coverage.update(requested_symbols=len(task["symbols"]), feed=params["feed"], adjustment=task["adjustment"], symbol_mapping_asof=params["asof"])
        coverage["unavailable_symbols"] = sorted(set(task["symbols"]) - set(remaining))
        return outputs, coverage

    def collect_news(self, task):
        def normalize(payload):
            frame = pd.DataFrame(payload.get("news") or [])
            if not frame.empty:
                frame["published_at"] = pd.to_datetime(frame["created_at"], utc=True, format="ISO8601")
                frame["updated_at"] = pd.to_datetime(frame["updated_at"], utc=True, errors="coerce", format="ISO8601")
                frame["available_at"] = frame[["published_at", "updated_at"]].max(axis=1)
                frame["body_text"] = frame["content"].map(clean_html)
                frame["body_available"] = frame["body_text"].str.len() > 0
                frame["body_sha256"] = frame["body_text"].map(lambda x: hashlib.sha256(x.encode()).hexdigest())
                frame = frame.drop_duplicates("id")
            return frame, "news_documents", "version_time_filtered"
        return self.alpaca_pages(task, "/v1beta1/news", {"start": task["start"], "end": task["end"],
            "sort": "asc", "limit": 50, "include_content": "true"}, normalize)

    def collect_actions(self, task):
        def normalize(payload):
            rows = [{"action_type": kind, **row} for kind, values in (payload.get("corporate_actions") or {}).items() for row in values]
            frame = pd.DataFrame(rows)
            if "process_date" in frame:
                frame["available_at"] = pd.to_datetime(frame["process_date"], utc=True, errors="coerce") + pd.Timedelta(days=1)
                frame["availability_policy"] = "provider_process_date_plus_one_day"
            return frame, "corporate_actions", "process_time_filtered"
        return self.alpaca_pages(task, "/v1/corporate-actions", {
            "start": task["start"], "end": task["end"], "limit": 1000,
            "data_quality": "all", "sort": "asc", "region": "all"}, normalize)

    def collect_fred(self, task):
        series_id = task["series_id"]
        self.pacing.wait("api.stlouisfed.org")
        metadata = self.fred.get_series_metadata(series_id)
        outputs = [self.raw(metadata, source="fred_series", request={"series_id": series_id})]
        count = 0
        pages = self.fred.iter_observation_pages(series_id, self.spec["start"],
            observation_end=self.spec["end"][:10], realtime_start="1776-07-04",
            realtime_end=self.spec["end"][:10]) if task.get("vintage", True) else self.fred.iter_observation_pages(
                series_id, self.spec["start"], observation_end=self.spec["end"][:10])
        while True:
            self.pacing.wait("api.stlouisfed.org")
            try:
                payload = next(pages)
            except StopIteration:
                break
            raw = self.raw(payload, source="alfred" if task.get("vintage", True) else "fred_current_history",
                request={"path": "series/observations", "series_id": series_id,
                    **{key: payload[key] for key in ["observation_start", "observation_end", "realtime_start", "realtime_end", "offset", "limit", "sort_order", "output_type"] if key in payload}})
            frame = pd.DataFrame(payload.get("observations") or [])
            if not frame.empty:
                frame["observation_date"] = pd.to_datetime(frame["date"], utc=True)
                frame["numeric_value"] = pd.to_numeric(frame["value"], errors="coerce")
                frame["missing_value"] = frame["numeric_value"].isna()
                frame["available_at"] = pd.to_datetime(frame["realtime_start"], utc=True) + pd.Timedelta(days=1)
                frame["series_id"] = series_id
                frame["units"] = metadata.get("units")
                frame["frequency"] = metadata.get("frequency")
                frame["seasonal_adjustment"] = metadata.get("seasonal_adjustment")
                frame["availability_policy"] = "vintage_date_plus_one_day"
                frame["raw_sha256"] = raw["sha256"]
                frame = frame.drop_duplicates(["date", "realtime_start", "realtime_end"])
            count += len(frame)
            outputs.extend([raw, self.table(frame,
                dataset="macro_vintage_observations" if task.get("vintage", True) else "macro_latest_observations",
                visibility="time_filtered" if task.get("vintage", True) else "revised_history_only")])
        outputs.append(self.table(pd.DataFrame([metadata]), dataset="macro_series_catalog", visibility="catalog_asof_only"))
        return outputs, {"series_id": series_id, "revision_observations": count}

    def collect_treasury(self, task):
        params = {"data": task["data"], "field_tdr_date_value": str(task["year"])}
        response = self.get(TREASURY_XML_BASE_URL, params=params)
        raw = self.archive.put(response.content, suffix="xml", content_type="application/xml")
        frame = parse_treasury_rate_feed(response.text,
            date_field="INDEX_DATE" if task["data"] == "daily_treasury_bill_rates" else "NEW_DATE")
        if "date" in frame:
            frame["date"] = pd.to_datetime(frame["date"], utc=True)
            frame = frame[(frame["date"] >= self.start) & (frame["date"] < self.end)].copy()
            frame["available_at"] = frame["date"].dt.floor("D") + pd.Timedelta(days=1)
        frame["raw_sha256"] = raw["sha256"]
        return [raw, self.table(frame, dataset=task["data"])], {"rows": len(frame), "year": task["year"]}

    def collect_crypto(self, task):
        self.pacing.wait("api.alpaca.markets")
        catalog = self.alpaca._request("GET", "https://api.alpaca.markets", "/v2/assets", params={"asset_class": "crypto", "status": "active"})
        raw = self.raw(catalog, source="alpaca_crypto_catalog", request={"asset_class": "crypto", "status": "active"})
        symbols = sorted(set(task["symbols"]) | {a["symbol"] for a in catalog})
        params = {"symbols": ",".join(symbols), "start": self.start.isoformat(), "end": self.end.isoformat(),
                  "timeframe": "1Day", "limit": 10000, "sort": "asc"}
        outputs, coverage = self.alpaca_pages(task, "/v1beta3/crypto/us/bars", params,
            lambda p: self.normalize_bars(p, dataset="crypto_daily_bars", adjustment="raw"))
        return [raw, self.table(pd.DataFrame(catalog), dataset="crypto_instrument_catalog", visibility="catalog_asof_only"), *outputs], coverage

    def collect_gdelt(self, task):
        with self.gdelt_slots:
            date = task["date"].replace("-", "")
            url = f"https://data.gdeltproject.org/events/{date}.export.CSV.zip"
            response = self.get(url)
            raw = self.archive.put(response.content, suffix="csv.zip", content_type="application/zip")
            with zipfile.ZipFile(BytesIO(response.content)) as archive:
                member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
                frame = pd.read_csv(archive.open(member), sep="\t", header=None, names=self.spec["gdelt_columns"],
                                    dtype=str, keep_default_na=False)
            if len(frame.columns) != len(self.spec["gdelt_columns"]):
                raise ValueError("GDELT source schema changed")
            frame["available_at"] = pd.to_datetime(frame["DATEADDED"], format="%Y%m%d", utc=True,
                                                   errors="coerce") + pd.Timedelta(days=1)
            frame["observation_date"] = pd.to_datetime(frame["SQLDATE"], format="%Y%m%d", utc=True, errors="coerce")
            for col in ["GoldsteinScale", "NumMentions", "NumSources", "NumArticles", "AvgTone",
                        "Actor1Geo_Lat", "Actor1Geo_Long", "Actor2Geo_Lat", "Actor2Geo_Long",
                        "ActionGeo_Lat", "ActionGeo_Long"]:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
            frame["raw_sha256"] = raw["sha256"]
            frame = frame.drop_duplicates("GLOBALEVENTID")
            return [raw, self.table(frame, dataset="global_news_events")], {"rows": len(frame),
                "source": "GDELT_1_daily", "article_bodies": False, "date": task["date"]}

    def collect_hn(self, task):
        """Exhaust indexed matches by recursively splitting capped time ranges."""
        outputs: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        checkpoint = self._receipt_path(task).with_suffix("")
        checkpoint.mkdir(exist_ok=True)
        def collect(start: int, end: int):
            page_path = checkpoint / f"{start:012d}_{end:012d}.json"
            if page_path.exists():
                page = json.loads(page_path.read_text())
                outputs.extend(page["outputs"])
                seen_ids.update(page.get("ids", []))
                if page.get("children"):
                    for lower, upper in page["children"]:
                        collect(lower, upper)
                return
            params = {"query": task.get("query", ""), "tags": task["tags"], "hitsPerPage": 1000,
                      "numericFilters": f"created_at_i>={start},created_at_i<{end}", "page": 0}
            payload = self.get("https://hn.algolia.com/api/v1/search_by_date", params=params).json()
            raw = self.raw(payload, source="hacker_news_algolia", request=params)
            outputs.append(raw)
            total = int(payload.get("nbHits", 0))
            if total > 1000:
                if end - start <= 1:
                    raise RuntimeError("HN result cap remains within one second")
                midpoint = (start + end) // 2
                self._save(page_path, {"outputs": [raw], "rows": 0,
                    "children": [[start, midpoint], [midpoint, end]]})
                collect(start, midpoint)
                collect(midpoint, end)
                return
            rows = []
            for hit in payload.get("hits", []):
                identity = str(hit["objectID"])
                if identity in seen_ids:
                    continue
                seen_ids.add(identity)
                row = {k: v for k, v in hit.items() if k != "_highlightResult"}
                row["body_text"] = clean_html(hit.get("comment_text") or hit.get("story_text"))
                row["published_at"] = pd.Timestamp(hit["created_at"])
                row["acquired_at_utc"] = utcnow()
                row["engagement_time"] = "acquisition_time_only"
                row["historical_body_version"] = "unverified_current_archive"
                row["raw_sha256"] = raw["sha256"]
                rows.append(row)
            table = self.table(pd.DataFrame(rows), dataset="public_discussion", visibility="historical_body_version_unverified")
            outputs.append(table)
            self._save(page_path, {"outputs": [raw, table], "rows": len(rows), "ids": [str(r["objectID"]) for r in rows]})
        collect(int(pd.Timestamp(task["start"]).timestamp()), int(pd.Timestamp(task["end"]).timestamp()))
        return outputs, {"rows": len(seen_ids), "query": task.get("query", ""),
                         "tags": task["tags"], "index_exhausted": True}

    def collect_document(self, task):
        response = self.get(task["url"])
        kind = "pdf" if response.content.startswith(b"%PDF") else "html"
        raw = self.archive.put(response.content, suffix=kind, content_type=response.headers.get("content-type", "application/octet-stream"))
        if kind == "pdf":
            import pypdf
            body = "\n\n".join(p.extract_text() or "" for p in pypdf.PdfReader(BytesIO(response.content)).pages)
            extracted = {"body_text": body, "extractor": f"pypdf/{pypdf.__version__}", "extraction_status": "complete" if body.strip() else "empty"}
        else:
            from services.web_research import extract_source_document
            from services.aql_zopedia_engine import load_aql_zopedia_llm_client

            client = load_aql_zopedia_llm_client(surface="historical_corpus")
            if client is None:
                raise RuntimeError("Source body cleaning model unavailable")
            extracted = extract_source_document(response.content, url=task["url"], llm_client=client)
            body = extracted["body_text"]
            trace = self.raw({"blocks": extracted.pop("preselection_blocks", []), "selected_block_indices": extracted.get("selected_block_indices"), "model": extracted.get("selection_model")}, source="document_cleaning", request={"url": task["url"], "raw_sha256": raw["sha256"]})
            extracted["cleaning_trace_sha256"] = trace["sha256"]
        frame = pd.DataFrame([{"url": task["url"], "title": task.get("title"), **extracted,
            "published_at": task.get("published_at"), "acquired_at_utc": utcnow(),
            "historical_body_version": "current_source_version", "raw_sha256": raw["sha256"]}])
        return [raw, *([trace] if kind == "html" else []), self.table(frame, dataset="primary_documents", visibility="historical_body_version_unverified")], {"characters": len(body), "extraction_status": extracted["extraction_status"]}

    def collect_csv(self, task):
        response = self.get(task["url"])
        raw = self.archive.put(gzip.compress(response.content, mtime=0), suffix="csv.gz", content_type="application/gzip")
        frame = pd.read_csv(BytesIO(response.content), low_memory=False)
        if task.get("date_column") in frame:
            col = task["date_column"]
            values = frame[col].astype(str)
            date_format = "%Y%m%d" if values.str.fullmatch(r"\d{8}").all() else None
            frame[col] = pd.to_datetime(values, utc=True, errors="coerce", format=date_format)
            frame = frame[(frame[col] >= self.start) & (frame[col] < self.end)].copy()
        frame["raw_sha256"] = raw["sha256"]
        return [raw, self.table(frame, dataset=task["dataset"], visibility="revised_history_only")], {"rows": len(frame)}

    def collect_sec_reference(self, task):
        headers = {"User-Agent": os.environ["SEC_USER_AGENT"]} if os.environ.get("SEC_USER_AGENT") else None
        payload = self.get("https://www.sec.gov/files/company_tickers_exchange.json", headers=headers).json()
        raw = self.raw(payload, source="sec_reference", request={})
        frame = pd.DataFrame(payload["data"], columns=payload["fields"])
        return [raw, self.table(frame, dataset="sec_company_reference", visibility="catalog_asof_only")], {"rows": len(frame), "ciks": sorted(frame["cik"].unique().tolist())}

    def collect_sec_company(self, task):
        from services.edgar import EdgarClient, EdgarAPIError
        client = EdgarClient(user_agent=os.environ.get("SEC_USER_AGENT"), pause_seconds=0,
            before_request=lambda: self.pacing.wait("data.sec.gov"))
        outputs, filings = [], []
        cik = task["cik"]
        for name, payload in client.iter_submission_pages(cik, start=self.spec["start"][:10], end=self.spec["end"][:10]):
            raw = self.raw(payload, source="sec_submissions", request={"cik": cik, "file": name})
            recent = (payload.get("filings") or {}).get("recent", {}) if "filings" in payload else payload
            frame = pd.DataFrame(recent)
            if not frame.empty:
                frame["observation_date"] = pd.to_datetime(frame["filingDate"], utc=True, errors="coerce")
                frame = frame[(frame["observation_date"] >= self.start) & (frame["observation_date"] < self.end)].copy()
                frame["available_at"] = pd.to_datetime(frame["acceptanceDateTime"], utc=True, errors="coerce", format="ISO8601") if "acceptanceDateTime" in frame else frame["observation_date"] + pd.Timedelta(days=1)
                frame["cik"] = cik
                frame["raw_sha256"] = raw["sha256"]
                filings.append(frame)
            outputs.extend([raw, self.table(frame, dataset="sec_filing_index")])
        facts_status = "complete"
        try:
            payload = client.get_company_facts(cik)
        except EdgarAPIError as exc:
            if "status=404" not in str(exc):
                raise
            payload = {}
            facts_status = "source_has_no_company_facts"
        raw = self.raw(payload, source="sec_companyfacts", request={"cik": cik})
        rows = []
        for taxonomy, concepts in payload.get("facts", {}).items():
            for concept, values in concepts.items():
                for unit, observations in values.get("units", {}).items():
                    rows += [{"cik": cik, "taxonomy": taxonomy, "concept": concept, "unit": unit, **value} for value in observations]
        frame = pd.DataFrame(rows)
        if not frame.empty:
            frame["available_at"] = pd.to_datetime(frame["filed"], utc=True, errors="coerce") + pd.Timedelta(days=1)
            frame["observation_date"] = pd.to_datetime(frame["end"], utc=True, errors="coerce")
            frame = frame[(frame["available_at"] >= self.start) & (frame["available_at"] < self.end)].copy()
            frame["raw_sha256"] = raw["sha256"]
        outputs.extend([raw, self.table(frame, dataset="sec_company_facts", visibility="version_time_filtered")])
        return outputs, {"cik": cik, "filings": sum(len(f) for f in filings), "facts": len(frame), "facts_status": facts_status, "filing_bodies": "not_in_index_or_companyfacts"}

    def collect_npm(self, task):
        from urllib.parse import quote
        url = f"https://api.npmjs.org/downloads/range/{task['start']}:{task['end']}/{quote(task['package'], safe='@')}"
        payload = self.get(url).json()
        raw = self.raw(payload, source="npm_downloads", request=task)
        frame = pd.DataFrame(payload.get("downloads") or [])
        if not frame.empty:
            frame["date"] = pd.to_datetime(frame["day"], utc=True)
            frame["package"] = task["package"]
            frame["raw_sha256"] = raw["sha256"]
            frame["historical_value_version"] = "current_archive_statistics"
        return [raw, self.table(frame, dataset="developer_adoption_downloads", visibility="revised_history_only")], {"days": len(frame), "package": task["package"]}

    def collect_local_fundamentals(self, task):
        path = Path(task["path"])
        payload = path.read_bytes()
        raw = self.archive.put(gzip.compress(payload, mtime=0), suffix="csv.gz", content_type="application/gzip")
        frame = pd.read_csv(BytesIO(payload), sep=";", low_memory=False)
        for col in ["Report Date", "Publish Date", "Restated Date"]:
            frame[col] = pd.to_datetime(frame[col], utc=True, errors="coerce")
        frame = frame[(frame["Report Date"] >= self.start) & (frame["Report Date"] < self.end)].copy()
        frame["available_at"] = frame[["Publish Date", "Restated Date"]].max(axis=1) + pd.Timedelta(days=1)
        frame["raw_sha256"] = raw["sha256"]
        frame["historical_value_version"] = "retained_local_simfin_snapshot"
        return [raw, self.table(frame, dataset=task["dataset"], visibility="version_time_filtered")], {"rows": len(frame), "companies": frame["Ticker"].nunique()}

    def binance_listing(self, prefix: str, *, delimiter: str | None = None):
        marker = ""
        while True:
            params = {"prefix": prefix, "max-keys": 1000, **({"delimiter": delimiter} if delimiter else {}), **({"marker": marker} if marker else {})}
            response = self.get("https://s3-ap-northeast-1.amazonaws.com/data.binance.vision", params=params)
            root = etree.fromstring(response.content)
            ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
            yield root, self.archive.put(response.content, suffix="xml", content_type="application/xml")
            if root.findtext("s:IsTruncated", namespaces=ns) != "true":
                break
            next_marker = root.findtext("s:NextMarker", namespaces=ns)
            if not next_marker or next_marker == marker:
                raise RuntimeError("Binance archive listing did not advance")
            marker = next_marker

    def collect_binance_catalog(self, task):
        outputs, symbols = [], []
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        for root, raw in self.binance_listing(task["prefix"], delimiter="/"):
            outputs.append(raw)
            symbols += [p.text.removeprefix(task["prefix"]).strip("/") for p in root.findall("s:CommonPrefixes/s:Prefix", ns)]
        frame = pd.DataFrame({"symbol": sorted(set(symbols)), "archive_prefix": task["prefix"], "catalog_asof_utc": utcnow()})
        outputs.append(self.table(frame, dataset="crypto_exchange_archive_catalog", visibility="catalog_asof_only"))
        return outputs, {"symbols": sorted(set(symbols)), "archive_prefix": task["prefix"]}

    def collect_binance_series(self, task):
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        interval = "1d/" if task["archive_type"] == "klines" else ""
        prefix = task["prefix"] + task["symbol"] + "/" + interval
        outputs, keys = [], []
        for root, raw in self.binance_listing(prefix):
            outputs.append(raw)
            keys += [p.text for p in root.findall("s:Contents/s:Key", ns)]
        keys = [key for key in keys if key.endswith(".zip") and self.start.strftime("%Y-%m") <= key.removesuffix(".zip")[-7:] <= self.end.strftime("%Y-%m")]
        checkpoint = self._receipt_path(task).with_suffix("")
        checkpoint.mkdir(exist_ok=True)
        rows = 0
        for key in sorted(keys):
            page_path = checkpoint / (hashlib.sha256(key.encode()).hexdigest() + ".json")
            if page_path.exists():
                page = json.loads(page_path.read_text())
            else:
                response = self.get("https://data.binance.vision/" + key)
                raw = self.archive.put(response.content, suffix="csv.zip", content_type="application/zip")
                with zipfile.ZipFile(BytesIO(response.content)) as archive:
                    payload = archive.read(archive.namelist()[0])
                if task["archive_type"] == "fundingRate":
                    frame = pd.read_csv(BytesIO(payload))
                    frame["observation_time"] = pd.to_datetime(pd.to_numeric(frame["calc_time"]), unit="ms", utc=True)
                    frame["available_at"] = frame["observation_time"] + pd.Timedelta(days=1)
                    dataset = "crypto_funding_rates"
                else:
                    names = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
                    frame = pd.read_csv(BytesIO(payload), names=names, header=None)
                    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="coerce")
                    frame = frame[frame["open_time"].notna()].copy()
                    # Spot archives changed from milliseconds to microseconds
                    # in Jan 2025; infer from epoch magnitude, not event dates.
                    unit = "us" if frame["open_time"].median() >= 10**14 else "ms"
                    frame["observation_time"] = pd.to_datetime(frame["open_time"], unit=unit, utc=True)
                    frame["available_at"] = frame["observation_time"].dt.floor("D") + pd.Timedelta(days=1)
                    for col in names[1:]:
                        frame[col] = pd.to_numeric(frame[col], errors="coerce")
                    dataset = "crypto_exchange_daily_bars"
                frame = frame[(frame["observation_time"] >= self.start) & (frame["observation_time"] < self.end)].copy()
                frame["symbol"] = task["symbol"]
                frame["venue"] = "binance_spot" if task["archive_type"] == "klines" else "binance_usdm_futures"
                frame["raw_sha256"] = raw["sha256"]
                page = {"outputs": [raw, self.table(frame, dataset=dataset)], "rows": len(frame)}
                self._save(page_path, page)
            outputs.extend(page["outputs"])
            rows += page["rows"]
        return outputs, {"rows": rows, "files": len(keys), "symbol": task["symbol"], "resolution": "daily" if task["archive_type"] == "klines" else "funding_interval", "current_month": "not_available_until_source_publishes_monthly_archive"}

    def build_tasks(self, groups: set[str]) -> list[dict[str, Any]]:
        families: dict[str, list[dict[str, Any]]] = defaultdict(list)
        months = list(pd.date_range(self.start.normalize().replace(day=1), self.end, freq="MS"))
        for n, start in enumerate(months):
            end = min(months[n + 1] if n + 1 < len(months) else self.end, self.end)
            if start >= end:
                continue
            if "social" in groups:
                families["social"].append({"kind": "hn", "tags": "story", "start": start.isoformat(), "end": end.isoformat()})
                for query in self.spec["discussion_queries"]:
                    families["social"].append({"kind": "hn", "tags": "comment", "query": query,
                        "start": start.isoformat(), "end": end.isoformat()})
            if "social_full" in groups:
                families["social_full"].append({"kind": "hn", "tags": "comment", "start": start.isoformat(), "end": end.isoformat()})
        anchors = [pd.Timestamp(e["onset"], tz="UTC") for e in self.spec["episodes"]]
        for family in ["social", "social_full"]:
            if family in families:
                families[family].sort(key=lambda task: min(abs((pd.Timestamp(task["start"]) - a).days) for a in anchors))
        days = list(pd.date_range(self.start, self.end.normalize() - pd.Timedelta(days=1), freq="D"))
        # Collection order follows the requested frames; every baseline day stays
        # scheduled. This is acquisition priority, not a learned selection rule.
        days.sort(key=lambda d: min(abs((d - a).days) for a in anchors))
        if "news" in groups:
            families["news"] = [{"kind": "news", "start": d.isoformat(),
                "end": min(d + pd.Timedelta(days=1), self.end).isoformat()} for d in days]
        if "macro" in groups:
            families["macro"] += [{"kind": "fred", "series_id": s} for s in self.spec["fred_series"]]
            for year in range(self.start.year, self.end.year + 1):
                for data in ["daily_treasury_yield_curve", "daily_treasury_real_yield_curve", "daily_treasury_bill_rates"]:
                    families["macro"].append({"kind": "treasury", "year": year, "data": data})
        if "markets" in groups:
            for status in ["active", "inactive"]:
                families["markets"].append({"kind": "catalog", "status": status, "asset_class": "us_equity"})
            # Captured provider catalogs are input artifacts, not ticker exceptions.
            priority = {s for e in self.spec["episodes"] for s in e.get("diagnostic_entities", [])}
            # Catalogs also contain numeric escrow/CUSIP identifiers. Keep every
            # identifier scheduled, but let conventional market symbols progress
            # before the provider validates those special instruments individually.
            symbols = sorted(set(self.spec["market_symbols"]), key=lambda s: (s not in priority, not s[0].isalpha(), s))
            for start in range(0, len(symbols), 100):
                for adjustment in ["raw", "all"]:
                    families["markets"].append({"kind": "bars", "symbols": symbols[start:start + 100], "adjustment": adjustment})
            for year in range(self.start.year, self.end.year + 1):
                families["markets"].append({"kind": "actions", "start": f"{year}-01-01", "end": min(f"{year}-12-31", self.spec["end"][:10])})
            families["markets"].append({"kind": "crypto", "symbols": self.spec["crypto_symbols"]})
        if "gdelt" in groups:
            families["gdelt"] = [{"kind": "gdelt", "date": d.date().isoformat()} for d in days]
        if "public" in groups:
            families["public"] = [{"kind": "sec_reference", "catalog_version": 2}]
            families["public"] += [{"kind": "document", "normalizer_version": 4, **doc} for doc in self.spec["primary_documents"]]
            families["public"] += [{"kind": "csv", **source} for source in self.spec["public_csv"]]
        if "crypto_archive" in groups:
            for prefix, archive_type in [("data/spot/monthly/klines/", "klines"), ("data/futures/um/monthly/fundingRate/", "fundingRate")]:
                task = {"kind": "binance_catalog", "prefix": prefix}
                families["crypto_archive"].append(task)
                path = self._receipt_path(task)
                if path.exists():
                    result = json.loads(path.read_text())
                    for symbol in result.get("coverage", {}).get("symbols", []):
                        families["crypto_archive"].append({"kind": "binance_series", "prefix": prefix, "archive_type": archive_type, "symbol": symbol})
        if "sec" in groups:
            task = {"kind": "sec_reference", "catalog_version": 2}
            families["sec"].append(task)
            path = self._receipt_path(task)
            if path.exists():
                result = json.loads(path.read_text())
                for cik in result.get("coverage", {}).get("ciks", []):
                    families["sec"].append({"kind": "sec_company", "cik": int(cik)})
        # Broad domains progress together instead of waiting behind all market history.
        tasks = [task for row in itertools.zip_longest(*families.values()) for task in row if task]
        return list({json_bytes(task): task for task in tasks}.values())

    def publish(self, tasks: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
        results = []
        for task in tasks:
            path = self._receipt_path(task)
            if path.exists():
                results.append(json.loads(path.read_text()))
            else:
                pages = sorted(path.with_suffix("").glob("*.json"))
                outputs = [output for p in pages for output in json.loads(p.read_text())["outputs"]]
                results.append({"task": task, "status": "running" if pages else "pending", "outputs": outputs})
        totals = Counter(r["status"] for r in results)
        rows = Counter()
        objects = {}
        for result in results:
            for output in result["outputs"]:
                objects[output["sha256"]] = output
                if output.get("dataset"):
                    rows[output["dataset"]] += output["rows"]
        manifest = {"schema_version": self.schema_version, "spec_sha256": self.spec_hash,
            "published_at_utc": utcnow(), "label": label, "task_counts": dict(totals),
            "dataset_rows": dict(rows), "unique_payload_bytes": sum(o["bytes"] for o in objects.values()),
            "collection_specification": self.spec, "tasks": results,
            "status": "complete" if totals.get("complete", 0) == len(tasks) else "partial"}
        receipt = self.archive.put(gzip.compress(json_bytes(manifest), mtime=0), suffix="manifest.json.gz", content_type="application/gzip")
        summary = {k: manifest[k] for k in ["status", "published_at_utc", "task_counts", "dataset_rows", "unique_payload_bytes"]}
        summary["manifest"] = receipt
        self._save(self.state / f"{label}_latest_receipt.json", summary)
        print(json.dumps({"publication": summary}, default=str), flush=True)
        return summary

    def run(self, *, groups: set[str], workers: int = 8, extra_tasks: list[dict[str, Any]] | None = None):
        tasks = list({json_bytes(t): t for t in self.build_tasks(groups) + (extra_tasks or [])}.values())
        label = "-".join(sorted(groups))
        self.publish(tasks, label=label)
        finished = 0
        last_publication = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.run_task, task): task for task in tasks}
            for future in as_completed(futures):
                result = future.result()
                finished += 1
                print(json.dumps({"finished": finished, "total": len(tasks), "kind": result["task"]["kind"],
                    "status": result["status"], "coverage": result.get("coverage"), "error": result.get("error", result.get("error_type"))}), flush=True)
                if time.monotonic() - last_publication > 60:
                    self.publish(tasks, label=label)
                    last_publication = time.monotonic()
        expanded = self.build_tasks(groups) + (extra_tasks or [])
        if {json_bytes(t) for t in expanded} - {json_bytes(t) for t in tasks}:
            return self.run(groups=groups, workers=workers, extra_tasks=extra_tasks)
        return self.publish(tasks, label=label)
