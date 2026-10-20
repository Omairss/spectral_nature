from types import SimpleNamespace

import pandas as pd

from data_access.layer import DataAccessLayer
from services.aql import business_model_stack as business
from services.stock_view import build_stock_investigator_view


def test_stock_detail_preserves_shared_research_and_its_date(monkeypatch):
    story = "# Company analysis\n\n" + ("Retained source passage.\n\n" * 200) + "[Source](https://example.com/report)"
    frames = {
        "zopedia_ticker_business_model_stacks": pd.DataFrame([{
            "symbol": "ABC", "company_name": "ABC Company", "status": "partial",
            "business_story_markdown": story, "asof_time_utc": "2026-10-19T12:00:00Z",
        }]),
        "attention_context_bundle": pd.DataFrame([{
            "symbol": "ABC", "llm_summary_text": "Separate feed narrative",
            "llm_management_signal": "Separate interpretation",
        }]),
    }
    monkeypatch.setattr(DataAccessLayer, "_try_pipeline_frame", lambda self, name, **kw:
                        (frames[name], {}) if name in frames else None)
    monkeypatch.setattr(DataAccessLayer, "_try_pipeline_symbol_frame", lambda *args, **kw: None)
    service = SimpleNamespace(data_access=DataAccessLayer(materialized_only=True))
    view = build_stock_investigator_view("ABC", service=service)

    assert view["company"]["summary"] == story
    assert pd.Timestamp(view["company"]["as_of"]) == pd.Timestamp("2026-10-19T12:00:00Z")
    assert view["company"]["management_signal"] == ""
    assert view["company"]["background"] == ""
    assert "zopedia_ticker_business_model_stacks" in view["provenance"]["datasets"]
    assert "attention_context_bundle" not in view["provenance"]["datasets"]


def test_company_research_retains_unselected_and_failed_refreshes(monkeypatch):
    prior = pd.DataFrame([
        {"symbol": symbol, "business_story_markdown": "Retained " + symbol,
         "asof_time_utc": "2026-10-19T12:00:00Z", "run_id": "previous"}
        for symbol in ("ABC", "DEF", "GHI")
    ])
    results = [business.build_ticker_business_model_stack(symbol=symbol, asof_time_utc=date)
               for symbol, date in (("ABC", "2026-10-20T12:00:00Z"),
                                    ("GHI", "2026-10-18T12:00:00Z"),
                                    ("JKL", "2026-10-20T12:00:00Z"))]
    results[1].business_story_markdown = "Earlier historical analysis"
    results[2].business_story_markdown = "New analysis"
    results[2].status = "partial"
    monkeypatch.setattr(business, "build_ticker_business_model_stack_results", lambda **kw: results)
    frames = business.build_ticker_business_model_stack_frames(symbols=["ABC", "GHI", "JKL"], existing_stacks_frame=prior)
    retained = frames["zopedia_ticker_business_model_stacks"].set_index("symbol")

    assert set(retained.index) == {"ABC", "DEF", "GHI", "JKL"}
    for symbol in ("ABC", "DEF", "GHI"):
        assert retained.loc[symbol, "business_story_markdown"] == "Retained " + symbol
        assert retained.loc[symbol, "run_id"] == "previous"
    assert retained.loc["JKL", "business_story_markdown"] == "New analysis"
    assert retained.loc["JKL", "status"] == "partial"


def test_company_research_runs_without_news(monkeypatch):
    from pipeline.jobs import main

    prior = pd.DataFrame([{"symbol": "ABC", "business_story_markdown": "Retained company analysis"}])
    received = {}

    def research(**kwargs):
        received.update(kwargs)
        return {"zopedia_ticker_business_model_stacks": prior}

    monkeypatch.setenv("ZOPEDIA_TICKER_BUSINESS_RESEARCH_ENABLED", "false")
    monkeypatch.setattr(main, "_configure_news_business_research_budget_env", lambda: None)
    monkeypatch.setattr(main, "_load_latest_materialized_frame", lambda name:
                        prior if name == "zopedia_ticker_business_model_stacks" else pd.DataFrame())
    monkeypatch.setattr(main, "_zopedia_pages_for_news_resolution", lambda conn: pd.DataFrame())
    monkeypatch.setattr(main, "build_ticker_business_model_stack_frames", research)
    phases = main._iter_news_business_research_output_phases(
        news=pd.DataFrame(), edgar_evidence=pd.DataFrame(), symbols=["ABC"],
        llm_client=None, ctx=SimpleNamespace(run_id="refresh", asof=pd.Timestamp("2026-10-20T12:00:00Z")), conn=None,
    )
    output = next(phases)
    assert received["news_frame"].empty
    pd.testing.assert_frame_equal(received["existing_stacks_frame"], prior)
    pd.testing.assert_frame_equal(output["zopedia_ticker_business_model_stacks"], prior)
