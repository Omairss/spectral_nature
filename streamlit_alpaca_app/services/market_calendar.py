"""Source-backed market schedules, retained independently from brief inference."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from calendar import month_abbr, month_name
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from icalendar import Calendar
from lxml import html
import pandas as pd
import requests

from services.alpha_vantage import AlphaVantageClient, AlphaVantageQuotaError, earnings_calendar
from services.fred import FREDClient

DATASET = "market_event_calendar"
NY = ZoneInfo("America/New_York")
FED_ANNUAL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FED_MONTHLY = "https://www.federalreserve.gov/newsevents/{year}-{month}.htm"
BEA_ICS = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"


def identity(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


@dataclass
class SourceResult:
    source: str
    events: list[dict] = field(default_factory=list)
    status: str = "available"
    error: str = ""
    coverage: dict = field(default_factory=dict)


class ScheduleFetchError(RuntimeError):
    pass


def source_event(source: str, key: str, name: str, day: date, *, url: str,
                 instant: datetime | None = None, **fields) -> dict:
    if instant is not None and instant.tzinfo is None:
        raise ValueError("A scheduled instant needs its source timezone")
    return {
        "id": identity(source, key), "source": source, "source_event_id": key,
        "source_url": url, "name": name, "date": day.isoformat(),
        "scheduled_at_utc": instant.astimezone(timezone.utc).isoformat() if instant else None,
        "timing_precision": "exact" if instant else "date", "timing_label": None,
        "confirmation_status": "scheduled", "type": "economic", "ticker": None,
        "short_label": name, "importance": 0.7, "facts": [], **fields,
    }


def parse_ics(content: bytes, *, source: str, url: str) -> list[dict]:
    calendar = Calendar.from_ical(content)
    events = []
    for item in calendar.walk("VEVENT"):
        if "RRULE" in item or "RECURRENCE-ID" in item:
            raise ValueError("Recurring source events require explicit occurrence expansion")
        key, name = clean(item.get("UID")), clean(item.get("SUMMARY"))
        when = item.decoded("DTSTART")
        if not key or not name or not isinstance(when, date):
            raise ValueError("Incomplete calendar event")
        instant = when if isinstance(when, datetime) else None
        if instant is not None and instant.tzinfo is None:
            raise ValueError("Calendar source omitted its event timezone")
        day = instant.astimezone(NY).date() if instant else when
        updated = item.decoded("LAST-MODIFIED") if "LAST-MODIFIED" in item else item.decoded("DTSTAMP") if "DTSTAMP" in item else None
        event = source_event(source, key, name, day, url=str(item.get("URL") or url), instant=instant,
                             cancelled=clean(item.get("STATUS")).upper() == "CANCELLED",
                             source_updated_at=updated.isoformat() if updated else None,
                             source_sequence=int(item.get("SEQUENCE", 0)))
        events.append(event)
    if not events:
        raise ValueError("Calendar contains no events")
    latest = {}
    for event in events:
        existing = latest.get(event["id"])
        if not existing or (event["source_sequence"], event.get("source_updated_at") or "") > (existing["source_sequence"], existing.get("source_updated_at") or ""):
            latest[event["id"]] = event
    return list(latest.values())


def _classes(node, name: str):
    return node.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," ' + name + ' ")]')


def _month_number(value: str) -> int:
    return next(i for i in range(1, 13) if value.lower() in {month_name[i].lower(), month_abbr[i].lower()})


def parse_fed_annual(content: bytes) -> list[dict]:
    tree, events = html.fromstring(content), []
    for panel in _classes(tree, "panel"):
        headings = _classes(panel, "panel-heading")
        heading = clean(headings[0].text_content()) if headings else ""
        year_match = re.fullmatch(r"(\d{4}) FOMC Meetings", heading)
        if not year_match:
            continue
        year = int(year_match[1])
        for row in _classes(panel, "fomc-meeting"):
            months, days = _classes(row, "fomc-meeting__month"), _classes(row, "fomc-meeting__date")
            if not months or not days:
                raise ValueError("FOMC row is missing its dates")
            month_text, day_text = clean(months[0].text_content()), clean(days[0].text_content())
            numbers = re.findall(r"\d+", day_text)
            if not numbers:
                raise ValueError("FOMC row contains no day")
            # Source month/day cells are date fields, not inferred news text.
            month = _month_number(month_text.split("/")[-1])
            end = date(year, month, int(numbers[-1]))
            start_month = _month_number(month_text.split("/")[0])
            start = date(year, start_month, int(numbers[0]))
            events.append(source_event("fed", f"fomc:{year}:{month_name[month]}", "FOMC meeting", end,
                url=FED_ANNUAL, type="fomc", short_label="FOMC", importance=1.0,
                start_date=start.isoformat(), facts=[["Meeting", start.strftime("%b %-d") + "–" + end.strftime("%b %-d")]]))
    if not events:
        raise ValueError("FOMC calendar structure was not recognized")
    return events


def parse_fed_month(content: bytes, *, year: int, month: int, url: str) -> list[dict]:
    tree, events = html.fromstring(content), []
    title = " ".join(tree.xpath('//head/title/text()'))
    if f"Calendar: {month_name[month]} {year}" not in title:
        raise ValueError("Fed monthly page does not match its requested calendar period")
    # Use the publisher's FOMC section; do not classify headlines or infer event dates.
    for heading in _classes(tree, "cal-nojs__rowTitle"):
        if clean(heading.text_content()) != "FOMC Meetings":
            continue
        for row in heading.itersiblings():
            if "cal-nojs__rowTitle" in row.get("class", ""):
                break
            for entry in [row, *_classes(row, "row")]:
                times, names, days = _classes(entry, "col-xs-2"), _classes(entry, "col-xs-7"), _classes(entry, "col-xs-3")
                if len(times) != 1 or len(names) != 1 or len(days) != 1:
                    continue
                paragraphs = names[0].xpath('./p')
                if not paragraphs:
                    continue
                name = clean(paragraphs[0].text_content())
                context = " ".join(clean(p.text_content()) for p in paragraphs[1:]).strip()
                day_text = clean(days[0].text_content())
                if not re.fullmatch(r"\d{1,2}(?:\s*[-–]\s*\d{1,2})?", day_text):
                    raise ValueError("Unrecognized Fed monthly day")
                day = date(year, month, int(re.findall(r"\d+", day_text)[-1]))
                clock = clean(times[0].text_content()).replace(".", "").upper()
                instant = datetime.combine(day, datetime.strptime(clock, "%I:%M %p").time(), NY) if clock else None
                key = f"fomc:{year}:{month_name[month]}" if name.casefold() == "fomc meeting" else f"{year}:{month}:{name}:{context}"
                events.append(source_event("fed", key, name, day,
                    url=url, instant=instant, type="fomc", short_label=name, importance=1.0,
                    facts=[["Meeting", context]] if context else []))
    return list({e["id"]: e for e in events}.values())


def parse_earnings(rows: list[dict], ranks: dict[str, float] | None = None) -> list[dict]:
    events = []
    for row in rows:
        symbol, period = row["symbol"], row["fiscalDateEnding"]
        session = row.get("timeOfTheDay", "").strip()
        # The source supplies a session, never an invented earnings-release clock time.
        session = {"pre-market": "Before market open", "post-market": "After market close"}.get(session, session or None)
        events.append(source_event("alpha_vantage", f"{symbol}:{period}", (row["name"] or symbol) + " earnings",
            date.fromisoformat(row["reportDate"]), url="https://www.alphavantage.co/documentation/#earnings-calendar",
            type="earnings", ticker=symbol, short_label=symbol, fiscal_period=period,
            confirmation_status="expected", timing_precision="session" if session else "date", timing_label=session,
            report_session=row.get("timeOfTheDay") or None,
            source_observed_at=row.get("_fetched_at"),
            importance=(ranks or {}).get(symbol, 0.5),
            facts=[["Fiscal period", period]] + ([["EPS estimate", row["estimate"] + " " + row.get("currency", "")]] if row.get("estimate") else [])))
    return events


def collect_reported_earnings(api_key: str, *, now: datetime, start: date, end: date,
                              companies: dict[str, str], ranks: dict[str, float],
                              previous_events: list[dict], priorities: dict[str, float] | None = None,
                              batch_size: int = 4) -> SourceResult:
    """Progressive free backfill; verified reports supersede expected dates."""
    if not api_key:
        return SourceResult("alpha_vantage_history", status="not_configured")
    client = AlphaVantageClient(api_key, now=now)
    snapshots = client.history_snapshot(list(companies))
    today = now.astimezone(NY).date().isoformat()
    reported = {e["id"] for e in previous_events if e.get("confirmation_status") == "reported"}
    pending = {e["ticker"] for e in previous_events if e.get("type") == "earnings" and e["id"] not in reported
               and e.get("confirmation_status") != "reported" and start.isoformat() <= e["date"] < today}
    due = [s for s in companies
           if now.timestamp() - float(snapshots.get(s, {}).get("last_attempt_at", 0)) >= 86400
           and (not snapshots.get(s, {}).get("fetched_at") or
                now.timestamp() - float(snapshots[s]["fetched_at"]) >= (86400 if s in pending else 30 * 86400))]
    due.sort(key=lambda s: (-(priorities or ranks).get(s, 0), s not in pending,
                           float(snapshots.get(s, {}).get("fetched_at", 0)), s))
    attempted, failures, limited = 0, {}, False
    for symbol in due[:batch_size]:
        try:
            attempted += 1
            snapshots[symbol] = client.history(symbol)
        except AlphaVantageQuotaError:
            limited = True
            break
        except Exception as exc:
            failures[symbol] = type(exc).__name__
    events = []
    for symbol, snapshot in snapshots.items():
        if not snapshot.get("fetched_at"):
            continue
        checked = datetime.fromtimestamp(snapshot["fetched_at"], timezone.utc).isoformat()
        for row in snapshot.get("rows", []):
            if not start.isoformat() <= row["reportedDate"] <= min(end.isoformat(), today):
                continue
            event = parse_earnings([{"symbol": symbol, "name": companies[symbol],
                "reportDate": row["reportedDate"], "fiscalDateEnding": row["fiscalDateEnding"],
                "timeOfTheDay": row.get("reportTime", ""), "estimate": row.get("estimatedEPS", ""),
                "_fetched_at": checked}], ranks)[0]
            event.update(confirmation_status="reported", source_url="https://www.alphavantage.co/documentation/#earnings")
            event["facts"] = [["Fiscal period", row["fiscalDateEnding"]]] + [
                [label, row[key]] for key, label in [("reportedEPS", "Reported EPS"), ("estimatedEPS", "EPS estimate")]
                if row.get(key) not in (None, "None", "", "NaN")]
            events.append(event)
    coverage = {"companies": len(companies), "cached_companies": sum(bool(v.get("fetched_at")) for v in snapshots.values()),
                "attempted": attempted, "pending_companies": max(0, len(due) - attempted),
                "request_allowance_reached": limited, "failures": failures}
    return SourceResult("alpha_vantage_history", events, status="available" if events or not failures else "failed",
                        error="Partial company history unavailable" if failures else "", coverage=coverage)


def fetch(url: str) -> bytes:
    try:
        response = requests.get(url, timeout=(10, 25))
        if response.status_code != 200:
            raise ScheduleFetchError(f"HTTP {response.status_code}")
        if len(response.content) > 8_000_000:
            raise ValueError("Calendar response exceeds size limit")
        return response.content
    except requests.RequestException as exc:
        raise ScheduleFetchError(type(exc).__name__) from None


def collect_sources(start: date, end: date, *, alpha_key: str = "", fred_key: str = "",
                    ranks: dict[str, float] | None = None) -> list[SourceResult]:
    def bea():
        return parse_ics(fetch(BEA_ICS), source="bea", url=BEA_ICS)

    def fed():
        coverage = {}
        try:
            events = {e["id"]: e for e in parse_fed_annual(fetch(FED_ANNUAL))}
            coverage["annual"] = "available"
        except (ScheduleFetchError, ValueError):
            events = {}
            coverage["annual"] = "unavailable"
        months = {(day.year, day.month) for day in (start + timedelta(days=i) for i in range((end-start).days+1))}
        for year, month in sorted(months):
            url = FED_MONTHLY.format(year=year, month=date(year, month, 1).strftime("%B").lower())
            try:
                for event in parse_fed_month(fetch(url), year=year, month=month, url=url):
                    old = events.get(event["id"], {})
                    events[event["id"]] = {**old, **event,
                        "facts": old.get("facts", []) + event.get("facts", [])}
                coverage[f"{year}-{month:02}"] = "available"
            except (ScheduleFetchError, ValueError):
                # The annual meeting dates survive an unpublished monthly page.
                coverage[f"{year}-{month:02}"] = "unavailable"
        if not events:
            raise ScheduleFetchError("Fed schedules unavailable")
        return SourceResult("fed", list(events.values()), coverage={"schedules": coverage})

    def bls():
        return parse_ics(fetch(BLS_ICS), source="bls", url=BLS_ICS)

    def fred():
        if not fred_key:
            return SourceResult("fred_bls", status="not_configured")
        return FREDClient(fred_key).get_source_release_calendar(22, start=start, end=end)

    def alpha():
        if not alpha_key:
            return SourceResult("alpha_vantage", status="not_configured")
        return parse_earnings(earnings_calendar(alpha_key), ranks)

    def run(source, loader):
        try:
            result = loader()
            if isinstance(result, SourceResult):
                result.events = [e for e in result.events if start.isoformat() <= e["date"] <= end.isoformat()]
                return result
            if source == "fred_bls":
                result = [source_event(source, f"{r['release_id']}:{r['date']}", r["release_name"],
                    date.fromisoformat(r["date"]), url=r.get("link") or f"https://fred.stlouisfed.org/release?rid={r['release_id']}",
                    short_label=r["release_name"], confirmation_status="scheduled") for r in result]
            return SourceResult(source, [e for e in result if start.isoformat() <= e["date"] <= end.isoformat()])
        except Exception as exc:
            # The detail may contain provider credentials. Retain only a safe error category.
            return SourceResult(source, status="failed", error=str(exc) if isinstance(exc, ScheduleFetchError) else type(exc).__name__)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda item: run(*item), [("bea", bea), ("fed", fed), ("bls", bls), ("alpha_vantage", alpha)]))
    if next(r for r in results if r.source == "bls").status != "available":
        results.append(run("fred_bls", fred))
    return results


def merge_calendar(previous: dict, sources: list[SourceResult], *, now: datetime, start: date, end: date) -> dict:
    observed = now.astimezone(timezone.utc).isoformat()
    events = {e["id"]: dict(e) for e in previous.get("events", []) if start.isoformat() <= e["date"] <= end.isoformat()}
    status = dict(previous.get("sources", {}))
    for source in sources:
        prior_status = status.get(source.source, {})
        status[source.source] = {"status": source.status, "attempted_at": observed,
            "last_success_at": observed if source.status == "available" else prior_status.get("last_success_at"),
            "event_count": len(source.events), "error": source.error, "coverage": source.coverage}
        if source.status != "available":
            continue
        for event in source.events:
            old = events.get(event["id"], {})
            if old.get("confirmation_status") == "reported" and event.get("confirmation_status") == "expected":
                continue
            history = list(old.get("previous_dates", []))
            if old and old.get("date") != event["date"]:
                history.append({"date": old["date"], "scheduled_at_utc": old.get("scheduled_at_utc"), "revised_at": observed})
            events[event["id"]] = {**event, "previous_dates": history,
                                   "last_seen_at": event.get("source_observed_at") or observed}
            if old.get("market_reaction") and all(old.get(k) == event.get(k) for k in
                    ("date", "scheduled_at_utc", "report_session", "type", "ticker", "confirmation_status", "cancelled")):
                events[event["id"]]["market_reaction"] = old["market_reaction"]
    return {"schema_version": 1, "as_of": observed, "window_start": start.isoformat(), "window_end": end.isoformat(),
            "sources": status, "events": sorted(events.values(), key=lambda e: (e["date"], e.get("scheduled_at_utc") or "", e["id"]))}


def calendar_from_frame(frame: pd.DataFrame) -> dict:
    if frame.empty or "calendar_json" not in frame:
        return {}
    return json.loads(str(frame.iloc[-1]["calendar_json"]))


def project_calendar(state: dict, *, reference: date, past_days: int = 30, future_days: int = 90) -> dict | None:
    if not state:
        return None
    start, end = reference - timedelta(days=past_days), reference + timedelta(days=future_days)
    days = {day.isoformat(): {"id": day.isoformat(), "label": day.strftime("%a, %-d %b"), "events": []}
            for day in (start + timedelta(days=i) for i in range((end-start).days+1))}
    # Preserve fallback history. Suppress only exact publisher/title/date duplicates
    # when an official timed record is also available; never infer equivalence.
    official = {(e["date"], clean(e["name"]).casefold()) for e in state.get("events", [])
                if e["source"] == "bls" and not e.get("cancelled")}
    for row in state.get("events", []):
        if row.get("cancelled") or row["date"] not in days:
            continue
        if row["source"] == "fred_bls" and (row["date"], clean(row["name"]).casefold()) in official:
            continue
        clock = datetime.fromisoformat(row["scheduled_at_utc"]).astimezone(NY).strftime("%H:%M") if row.get("scheduled_at_utc") else None
        days[row["date"]]["events"].append({
            "id": row["id"], "name": row["name"], "ticker": row.get("ticker"), "short_label": row.get("short_label"),
            "type": row["type"], "time": clock, "importance": row.get("importance", 0.5),
            "summary": None, "context": None, "reaction": None, "source_url": row["source_url"],
            "source_name": row["source"], "confirmation_status": row["confirmation_status"],
            "timing_label": row.get("timing_label"), "timing_precision": row["timing_precision"],
            "facts": row.get("facts", []), "source_as_of": row.get("last_seen_at"),
            "market_reaction": row.get("market_reaction"),
        })
    for day in days.values():
        day["events"].sort(key=lambda e: (-e["importance"], e.get("time") or "99:99", e["name"], e["id"]))
    return {"reference_date": reference.isoformat(), "as_of": state["as_of"], "days": list(days.values())}


def load_public_calendar(*, target_date: str = "") -> dict | None:
    from services.pipeline_store import load_dataset_frame_asof, load_latest_dataset_frame
    try:
        frame, _ = load_dataset_frame_asof(DATASET, target_date) if target_date else load_latest_dataset_frame(DATASET)
        reference = date.fromisoformat(target_date) if target_date else datetime.now(NY).date()
        return project_calendar(calendar_from_frame(frame), reference=reference)
    except Exception as exc:
        print(f"[warn] retained calendar unavailable: {type(exc).__name__}")
        return None
