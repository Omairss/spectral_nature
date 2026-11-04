from copy import deepcopy
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from services.alpha_vantage import AlphaVantageClient, AlphaVantageError, AlphaVantageQuotaError
from services.market_calendar import SourceResult, merge_calendar, parse_earnings
from services.market_calendar_reactions import observe_reactions

NOW = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
SESSIONS = ["2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02"]


def report(session="post-market", day="2026-10-29"):
    event = parse_earnings([{"symbol": "IBM", "name": "IBM", "reportDate": day,
        "fiscalDateEnding": "2026-09-30", "timeOfTheDay": session}])[0]
    event["confirmation_status"] = "reported"
    return event


def measure(events, missing=()):
    prices = pd.DataFrame([{"symbol": symbol, "timestamp": day + "T16:00:00Z", "close": value}
        for symbol, values in {"IBM": [100, 110, 99, 200], "SPY": [100, 101, 100, 300]}.items()
        for day, value in zip(SESSIONS, values) if (symbol, day) not in missing])
    state = {"market_sessions": [{"date": day} for day in SESSIONS], "events": deepcopy(events)}
    return observe_reactions(state, prices, as_of=NOW, price_version="fixture")["events"]


def test_pre_post_and_date_only_reports_use_only_the_next_trading_day():
    pre, post, unknown = measure([report("pre-market"), report(), report("")])
    assert pre["market_reaction"]["return_pct"] == pytest.approx(-10)
    assert post["market_reaction"]["return_pct"] == pytest.approx(-10)
    assert unknown["market_reaction"]["return_pct"] == pytest.approx(-10)
    assert post["market_reaction"]["start_session"] == "2026-10-29"
    assert post["market_reaction"]["end_session"] == "2026-10-30"


def test_missing_close_does_not_skip_to_another_session_and_today_is_partial():
    assert "market_reaction" not in measure([report()], missing=[("IBM", "2026-10-30")])[0]
    # Friday after-close cannot use Monday's still-incomplete daily bar.
    assert "market_reaction" not in measure([report(day="2026-10-30")])[0]


def test_price_observation_is_independent_of_expected_status_but_not_future_or_cancelled():
    expected, cancelled, future = report(), report(), report(day="2026-11-02")
    expected["confirmation_status"] = "expected"
    cancelled["cancelled"] = True
    for event in (expected, cancelled, future):
        event["market_reaction"] = {"return_pct": 100}
    expected, cancelled, future = measure([expected, cancelled, future])
    assert expected["confirmation_status"] == "expected"
    assert expected["market_reaction"]["return_pct"] == pytest.approx(-10)
    assert "market_reaction" not in cancelled and "market_reaction" not in future


def test_valid_consolidated_reaction_survives_quote_outage_but_other_feed_does_not():
    good = measure([report()])[0]
    other = deepcopy(good)
    other["market_reaction"]["price_feed"] = "iex"
    state = {"events": [good, other], "market_sessions": [{"date": d} for d in SESSIONS]}
    result = observe_reactions(state, pd.DataFrame(), as_of=NOW, price_version="unavailable")["events"]
    assert result[0]["market_reaction"]["return_pct"] == pytest.approx(-10)
    assert "market_reaction" not in result[1]


def test_macro_uses_benchmark_not_earnings_surprise():
    macro = {**report("pre-market", day="2026-10-28"), "type": "economic", "ticker": None}
    earnings = {**report(), "facts": [["Reported EPS", "10"], ["EPS estimate", "1"]]}
    macro, earnings = measure([macro, earnings])
    assert macro["market_reaction"]["symbol"] == "SPY"
    assert macro["market_reaction"]["return_pct"] == pytest.approx(1)
    assert earnings["market_reaction"]["return_pct"] < 0


def test_reported_date_survives_future_calendar_and_revision_invalidates_reaction():
    actual = measure([report()])[0]
    previous = {"events": [actual]}
    revised = {**report(), "date": "2026-11-03"}
    merged = merge_calendar(previous, [SourceResult("alpha_vantage_history", [revised])],
        now=NOW, start=date(2026, 10, 1), end=date(2027, 1, 1))
    assert "market_reaction" not in merged["events"][0]
    expected = {**report(), "date": "2026-11-05", "confirmation_status": "expected"}
    kept = merge_calendar(previous, [SourceResult("alpha_vantage", [expected])],
        now=NOW, start=date(2026, 10, 1), end=date(2027, 1, 1))
    assert kept["events"][0]["date"] == actual["date"]


@pytest.fixture
def provider_store(monkeypatch):
    import services.pipeline_store as store
    values = {}
    def mutate(namespace, key, transform):
        value = transform(deepcopy(values.get((namespace, key), {})))
        values[namespace, key] = value
        return value
    monkeypatch.setattr(store, "mutate_execution_state", mutate)
    monkeypatch.setattr(store, "read_execution_state", lambda ns, key, **kw: deepcopy(values.get((ns, key))))
    monkeypatch.setattr(store, "write_execution_state", lambda ns, key, value: values.update({(ns, key): value}) is None)
    return values


def test_request_allowance_is_shared_between_clients_and_preserves_calendar_capacity(provider_store):
    one, two = AlphaVantageClient("test-key", now=NOW), AlphaVantageClient("test-key", now=NOW)
    for _ in range(12):
        one._reserve("history")
    with pytest.raises(AlphaVantageQuotaError):
        two._reserve("history")
    for _ in range(8):
        two._reserve("calendar")
    with pytest.raises(AlphaVantageQuotaError):
        one._reserve("calendar")


def test_failed_history_keeps_good_rows_and_records_retry_cooldown(provider_store, monkeypatch):
    client = AlphaVantageClient("test-key", now=NOW)
    key = (client.namespace, client._key("history:IBM"))
    provider_store[key] = {"fetched_at": 1, "rows": [{"reportedDate": "2026-10-29"}]}
    def fail(*args, **kwargs):
        raise AlphaVantageError("Provider unavailable")
    monkeypatch.setattr(client, "_fetch", fail)
    with pytest.raises(AlphaVantageError):
        client.history("IBM")
    assert provider_store[key]["rows"] == [{"reportedDate": "2026-10-29"}]
    assert provider_store[key]["fetched_at"] == 1
    assert provider_store[key]["last_attempt_at"] == NOW.timestamp()


def test_current_session_is_eligible_only_after_its_official_close_and_data_delay():
    prices = pd.DataFrame([
        {"symbol": "IBM", "timestamp": "2026-10-30T04:00:00Z", "close": 100},
        {"symbol": "IBM", "timestamp": "2026-11-02T05:00:00Z", "close": 105},
    ])
    state = {"market_sessions": [{"date": d, "close": "16:00"} for d in SESSIONS],
             "events": [report(day="2026-10-30")]}
    # The November DST change makes this 16:15 New York, not 16:15 UTC.
    for minute, colored in [(14, False), (15, True)]:
        result = observe_reactions(deepcopy(state), prices,
            as_of=datetime(2026, 11, 2, 21, minute, tzinfo=timezone.utc), price_version="fixture")
        assert bool(result["events"][0].get("market_reaction")) == colored
    state["market_sessions"][-1].pop("close")
    result = observe_reactions(state, prices,
        as_of=datetime(2026, 11, 2, 23, tzinfo=timezone.utc), price_version="fixture")
    assert "market_reaction" not in result["events"][0]


def test_early_close_and_holiday_use_the_exchange_schedule():
    prices = pd.DataFrame([
        {"symbol": "IBM", "timestamp": d + "T05:00:00Z", "close": close}
        for d, close in [("2026-11-25", 100), ("2026-11-27", 95)]])
    state = {"market_sessions": [{"date": "2026-11-25", "close": "16:00"},
                                 {"date": "2026-11-27", "close": "13:00"}],
             "events": [report(day="2026-11-25")]}
    result = observe_reactions(state, prices,
        as_of=datetime(2026, 11, 27, 18, 15, tzinfo=timezone.utc), price_version="fixture")
    assert result["events"][0]["market_reaction"]["return_pct"] == pytest.approx(-5)


def test_latest_pending_reports_precede_larger_historical_backfill(monkeypatch):
    from services.market_calendar import collect_reported_earnings
    companies = {"LARGE": "Large", "RECENT": "Recent", "OLDER": "Older"}
    checked = []
    monkeypatch.setattr(AlphaVantageClient, "history_snapshot", lambda self, symbols: {})
    def history(self, symbol):
        checked.append(symbol)
        return {"symbol": symbol, "fetched_at": NOW.timestamp(), "rows": []}
    monkeypatch.setattr(AlphaVantageClient, "history", history)
    pending = [{**report(day=day), "ticker": symbol, "id": symbol, "confirmation_status": "expected"}
               for symbol, day in [("RECENT", "2026-10-30"), ("OLDER", "2026-10-29")]]
    collect_reported_earnings("test-key", now=NOW, start=date(2026, 10, 1), end=date(2027, 1, 1),
        companies=companies, ranks={}, priorities={"LARGE": 1000, "OLDER": 100, "RECENT": 1},
        previous_events=pending, batch_size=2)
    assert checked == ["RECENT", "OLDER"]
