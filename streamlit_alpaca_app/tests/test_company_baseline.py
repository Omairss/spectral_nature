from __future__ import annotations

import pandas as pd

from services.company_baseline import build_company_baseline_frame, deserialize_company_baseline_frame


def test_wikipedia_company_background_retries_without_legal_suffix(monkeypatch):
    import services.company as company_module

    class _Response:
        def __init__(self, status_code: int, payload: dict | None = None):
            self.status_code = status_code
            self._payload = payload or {}
            self.headers = {}

        def json(self):
            return self._payload

    requested_urls: list[str] = []

    def _get(url, headers, timeout):
        requested_urls.append(url)
        if url.endswith("Vertiv_Holdings%2C_LLC"):
            return _Response(404)
        return _Response(
            200,
            {
                "type": "standard",
                "extract": "Vertiv Holdings Co. is an American provider of infrastructure and services for data centers.",
            },
        )

    company_module._wikipedia_company_background.cache_clear()
    monkeypatch.setattr(company_module.requests, "get", _get)

    text = company_module._wikipedia_company_background("Vertiv Holdings, LLC")

    assert text.startswith("Vertiv Holdings Co.")
    assert requested_urls[-1].endswith("Vertiv_Holdings")


def test_build_company_baseline_frame_uses_company_names_and_limit(monkeypatch):
    universe = pd.DataFrame(
        {
            "symbol": ["VRT", "NVDA", "AAPL"],
            "name": ["Vertiv Holdings Co", "NVIDIA Corporation", "Apple Inc."],
        }
    )

    monkeypatch.setattr("services.company_baseline.taxonomy_lookup_by_symbol", lambda symbols: {})
    monkeypatch.setenv("COMPANY_BASELINE_REQUEST_DELAY_SECONDS", "0")
    monkeypatch.setattr(
        "services.company_baseline.build_company_description",
        lambda symbol, profile, fundamentals, news_by_symbol, news_payload, active_lens: (
            f"{profile['name']} baseline context for {symbol}. "
            "The current narrative is still thin, so the best read comes from the linked price action and recent headlines."
        ),
    )

    frame = build_company_baseline_frame(
        universe,
        symbols=["VRT", "NVDA"],
        limit=1,
        asof_time_utc="2026-04-29T12:00:00Z",
        run_id="run-1",
    )

    assert frame["symbol"].tolist() == ["VRT"]
    assert frame.iloc[0]["company_name"] == "Vertiv Holdings Co"
    assert frame.iloc[0]["company_background_text"] == "Vertiv Holdings Co baseline context for VRT."
    assert "current narrative is still thin" not in frame.iloc[0]["company_background_text"]
    assert frame.iloc[0]["baseline_source"] == "company_baseline_prefetch"
    assert frame.iloc[0]["run_id"] == "run-1"


def test_deserialize_company_baseline_frame_returns_symbol_row():
    frame = pd.DataFrame(
        [
            {"symbol": "VRT", "company_background_text": "Vertiv baseline."},
            {"symbol": "NVDA", "company_background_text": "NVIDIA baseline."},
        ]
    )

    assert deserialize_company_baseline_frame(frame, "nvda")["company_background_text"] == "NVIDIA baseline."
    assert deserialize_company_baseline_frame(frame, "missing") == {}
