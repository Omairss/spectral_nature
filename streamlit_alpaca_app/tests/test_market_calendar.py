from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from services.alpha_vantage import AlphaVantageError, parse_earnings_csv
from services.market_calendar import (BEA_ICS, SourceResult, merge_calendar, parse_earnings,
    parse_fed_annual, parse_fed_month, parse_ics, project_calendar, source_event)

RAW = Path(__file__).parents[1] / "documents/verification/live-market-calendar-2026-11-01/raw"
NOW = datetime(2026, 11, 1, 18, tzinfo=timezone.utc)
START, END = date(2026, 10, 1), date(2027, 2, 1)


def test_real_earnings_csv_preserves_missing_company_name_and_session_precision():
    rows = parse_earnings_csv((RAW / "alpha-demo.csv").read_text())
    events = parse_earnings(rows)
    assert len(events) == len(rows)
    assert all(e["confirmation_status"] == "expected" and e["scheduled_at_utc"] is None for e in events)
    unnamed = next(row for row in rows if not row["name"])
    assert next(e for e in events if e["ticker"] == unnamed["symbol"])["name"] == unnamed["symbol"] + " earnings"


def test_quota_or_entitlement_json_cannot_be_misread_as_an_empty_calendar():
    with pytest.raises(AlphaVantageError):
        parse_earnings_csv('{"Information":"Request limit reached"}')


def test_official_bea_ics_unfolds_lines_and_preserves_dst():
    events = parse_ics((RAW / "bea.ics").read_bytes(), source="bea", url=BEA_ICS)
    october = next(e for e in events if e["date"] == "2026-10-29" and e["name"].startswith("Personal Income"))
    november = next(e for e in events if e["date"] == "2026-11-25" and e["name"].startswith("Personal Income"))
    assert october["scheduled_at_utc"].endswith("12:30:00+00:00")
    assert november["scheduled_at_utc"].endswith("13:30:00+00:00")
    state = merge_calendar({}, [SourceResult("bea", [october, november])], now=NOW, start=START, end=END)
    public = project_calendar(state, reference=date(2026, 11, 1), past_days=7, future_days=30)
    assert {e["time"] for day in public["days"] for e in day["events"]} == {"08:30"}


def test_fed_annual_and_monthly_schedules_keep_meetings_and_minutes_distinct():
    meetings = parse_fed_annual((RAW / "fed-annual.html").read_bytes())
    december = next(e for e in meetings if e["date"] == "2026-12-09")
    assert december["start_date"] == "2026-12-08" and december["scheduled_at_utc"] is None
    monthly = parse_fed_month((RAW / "fed-monthly.html").read_bytes(), year=2026, month=11, url="https://www.federalreserve.gov/newsevents/2026-november.htm")
    minutes = next(e for e in monthly if e["name"] == "FOMC Minutes")
    assert minutes["date"] == "2026-11-18" and minutes["scheduled_at_utc"] == "2026-11-18T19:00:00+00:00"
    assert not any(e["date"] == "2026-11-04" for e in meetings + monthly)


def test_source_outage_and_missing_rows_retain_original_last_seen():
    event = source_event("bea", "stable", "Release", date(2026, 11, 5), url=BEA_ICS)
    first = merge_calendar({}, [SourceResult("bea", [event])], now=NOW, start=START, end=END)
    failed = merge_calendar(first, [SourceResult("bea", status="failed", error="HTTP 503")], now=NOW.replace(hour=19), start=START, end=END)
    assert failed["events"] == first["events"]
    assert failed["sources"]["bea"]["last_success_at"] == NOW.isoformat()
    missing = merge_calendar(first, [SourceResult("bea", [])], now=NOW.replace(hour=20), start=START, end=END)
    assert missing["events"] == first["events"]


def test_reschedule_uses_source_identity_and_retains_the_old_date():
    event = source_event("bea", "stable", "Release", date(2026, 11, 5), url=BEA_ICS)
    first = merge_calendar({}, [SourceResult("bea", [event])], now=NOW, start=START, end=END)
    updated = {**event, "date": "2026-11-06"}
    result = merge_calendar(first, [SourceResult("bea", [updated])], now=NOW.replace(hour=19), start=START, end=END)
    assert len(result["events"]) == 1
    assert result["events"][0]["previous_dates"][0]["date"] == "2026-11-05"


def test_explicit_cancellation_is_retained_but_not_rendered():
    event = source_event("bea", "stable", "Release", date(2026, 11, 5), url=BEA_ICS, cancelled=True)
    state = merge_calendar({}, [SourceResult("bea", [event])], now=NOW, start=START, end=END)
    assert len(state["events"]) == 1
    assert not any(day["events"] for day in project_calendar(state, reference=NOW.date())["days"])


def test_empty_days_remain_continuous_and_all_same_day_earnings_survive():
    events = [source_event("alpha_vantage", str(i), "Issuer earnings", date(2026, 11, 5), url="https://www.alphavantage.co", ticker=str(i)) for i in range(20)]
    state = merge_calendar({}, [SourceResult("alpha_vantage", events)], now=NOW, start=START, end=END)
    public = project_calendar(state, reference=NOW.date())
    assert len(public["days"]) == 98
    assert len(next(day for day in public["days"] if day["id"] == "2026-11-05")["events"]) == 20


def test_replay_reads_its_own_retained_publication(monkeypatch):
    import json
    import services.pipeline_store as store
    from services.market_calendar import load_public_calendar
    state = merge_calendar({}, [], now=NOW, start=START, end=END)
    def historical(dataset, target):
        assert dataset == "market_event_calendar" and target == "2026-11-01"
        return pd.DataFrame([{"calendar_json": json.dumps(state)}]), None
    monkeypatch.setattr(store, "load_dataset_frame_asof", historical)
    monkeypatch.setattr(store, "load_latest_dataset_frame", lambda *a, **kw: pytest.fail("Replay read the current calendar"))
    assert load_public_calendar(target_date="2026-11-01")["reference_date"] == "2026-11-01"


def test_ics_latest_sequence_wins_even_when_the_source_lists_it_first():
    content = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:stable\r\nSUMMARY:Release\r\nDTSTART:20261106T133000Z\r\nSEQUENCE:2\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:stable\r\nSUMMARY:Release\r\nDTSTART:20261105T133000Z\r\nSEQUENCE:1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    events = parse_ics(content, source="bea", url=BEA_ICS)
    assert len(events) == 1 and events[0]["date"] == "2026-11-06"


def test_fred_release_dates_include_future_schedule_and_preserve_pagination(monkeypatch):
    from services.fred import FREDClient
    client = FREDClient("test-key")
    calls = []
    def request(path, params):
        calls.append(params)
        assert path == "release/dates"
        assert params["include_release_dates_with_no_data"] == "true"
        assert "realtime_end" not in params
        return {"count": 2, "release_dates": [{"date": "2026-12-10" if params["offset"] == 0 else "2026-11-10", "release_id": 10}]}
    monkeypatch.setattr(client, "_request_v1", request)
    result = client.get_release_dates(10, start=date(2026, 11, 1), end=date(2026, 11, 30))
    assert result == [{"date": "2026-11-10", "release_id": 10}]
    assert [c["offset"] for c in calls] == [0, 1]


def test_calendar_storage_failure_is_optional_for_the_home_reader(monkeypatch):
    import services.pipeline_store as store
    from services.market_calendar import load_public_calendar
    def unavailable(*args, **kwargs):
        raise ConnectionError("Store unavailable")
    monkeypatch.setattr(store, "load_latest_dataset_frame", unavailable)
    assert load_public_calendar() is None


def test_fed_wrong_month_page_cannot_invent_events_in_another_month():
    with pytest.raises(ValueError, match="period"):
        parse_fed_month((RAW / "fed-monthly.html").read_bytes(), year=2027, month=1, url="https://www.federalreserve.gov/newsevents/2027-january.htm")


def test_bls_recovery_keeps_fallback_dates_outside_its_actual_records():
    fallback = source_event("fred_bls", "one", "Release", date(2026, 11, 5), url="https://fred.stlouisfed.org")
    later = source_event("fred_bls", "two", "Release", date(2026, 12, 5), url="https://fred.stlouisfed.org")
    initial = merge_calendar({}, [SourceResult("fred_bls", [fallback, later])], now=NOW, start=START, end=END)
    official = source_event("bls", "official", "Release", date(2026, 11, 5), url="https://www.bls.gov", instant=datetime(2026, 11, 5, 13, 30, tzinfo=timezone.utc))
    recovered = merge_calendar(initial, [SourceResult("bls", [official])], now=NOW, start=START, end=END)
    assert len(recovered["events"]) == 3
    days = {d["id"]: d["events"] for d in project_calendar(recovered, reference=NOW.date())["days"]}
    assert len(days["2026-11-05"]) == 1 and days["2026-11-05"][0]["time"] == "08:30"
    assert len(days["2026-12-05"]) == 1
