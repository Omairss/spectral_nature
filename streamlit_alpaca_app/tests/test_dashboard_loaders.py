from __future__ import annotations

import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from presentation import dashboard_loaders


def test_latest_page_agentic_summary_cache_token_uses_dataset_version(monkeypatch):
    class Metadata:
        dataset_version_id = "page_agentic_summaries__run_2"
        asof_time_utc = "2026-06-22T20:20:00Z"

    monkeypatch.setattr(
        dashboard_loaders,
        "latest_dataset_metadata",
        lambda dataset_name: Metadata() if dataset_name == "page_agentic_summaries" else None,
    )

    assert dashboard_loaders._latest_page_agentic_summary_cache_token() == "page_agentic_summaries__run_2"


def test_load_ticker_snapshot_profile_can_skip_live_fallback(monkeypatch):
    monkeypatch.setattr(dashboard_loaders, "_load_attention_ticker_snapshot_map_cached", lambda force_refresh=False: {})
    monkeypatch.setattr(
        dashboard_loaders,
        "_load_universe_security_name_map",
        lambda force_refresh=False: {"CVX": "Chevron Corporation"},
    )
    monkeypatch.setattr(
        dashboard_loaders,
        "_load_attention_ticker_snapshot_cached",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("snapshot fallback should not run")),
    )
    monkeypatch.setattr(
        dashboard_loaders,
        "_load_asset_metadata_cached",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("asset fallback should not run")),
    )
    monkeypatch.setattr(
        dashboard_loaders,
        "_load_price_history_cached",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("price fallback should not run")),
    )
    monkeypatch.setattr(
        dashboard_loaders,
        "_load_public_price_history_cached",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("public price fallback should not run")),
    )

    profile = dashboard_loaders._load_ticker_snapshot_profile(
        object(),
        "CVX",
        allow_live_fallback=False,
    )

    assert profile == {
        "symbol": "CVX",
        "company_name": "Chevron Corporation",
        "market_cap_label": "n/a",
        "sparkline_data_uri": "",
    }


def test_is_stale_fallback_background_payload_true_when_fallback_has_hidden_headlines():
    payload = {
        "description_text": "No relevant business news found in web coverage for BMY.",
        "recent_headlines": [],
        "source_trace": {
            "headline_count": 6,
            "relevant_news_count": 0,
        },
    }
    assert dashboard_loaders._is_stale_fallback_background_payload(payload) is True


def test_is_stale_fallback_background_payload_false_for_legit_no_news():
    payload = {
        "description_text": "No relevant business news found in web coverage for IRDM.",
        "recent_headlines": [],
        "source_trace": {
            "headline_count": 0,
            "relevant_news_count": 0,
        },
    }
    assert dashboard_loaders._is_stale_fallback_background_payload(payload) is False


def test_load_attention_ticker_background_cached_bypasses_stale_memoized_payload(monkeypatch):
    stale_payload = {
        "description_text": "No relevant business news found in web coverage for BMY.",
        "recent_headlines": [],
        "source_trace": {"headline_count": 6, "relevant_news_count": 0},
    }
    refreshed_payload = {
        "description_text": "BMY retained context text",
        "recent_headlines": [{"headline": "BMY item", "url": "https://example.com/bmy"}],
        "source_trace": {"headline_count": 6, "relevant_news_count": 1},
    }
    calls: list[bool] = []
    monkeypatch.setattr(dashboard_loaders, "_load_attention_ticker_background_memoized", lambda cfg, ticker: stale_payload)

    def _fake_uncached(cfg, ticker, *, force_refresh=True):
        calls.append(bool(force_refresh))
        return refreshed_payload

    monkeypatch.setattr(dashboard_loaders, "_load_attention_ticker_background_uncached", _fake_uncached)

    payload = dashboard_loaders._load_attention_ticker_background_cached(object(), "BMY", force_refresh=False)

    assert payload == refreshed_payload
    assert calls == [False]


def test_load_attention_ticker_background_cached_keeps_memoized_for_non_stale_payload(monkeypatch):
    memoized_payload = {
        "description_text": "No relevant business news found in web coverage for IRDM.",
        "recent_headlines": [],
        "source_trace": {"headline_count": 0, "relevant_news_count": 0},
    }
    monkeypatch.setattr(dashboard_loaders, "_load_attention_ticker_background_memoized", lambda cfg, ticker: memoized_payload)
    monkeypatch.setattr(
        dashboard_loaders,
        "_load_attention_ticker_background_uncached",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("uncached background loader should not run")),
    )

    payload = dashboard_loaders._load_attention_ticker_background_cached(object(), "IRDM", force_refresh=False)

    assert payload == memoized_payload
