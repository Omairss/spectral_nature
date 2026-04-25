from __future__ import annotations

import json

import numpy as np
import pandas as pd

from presentation import attention_content


def test_attention_text_helpers_normalize_blank_and_nan_values():
    assert attention_content._clean_attention_text("  Alpha   beta  ") == "Alpha beta"
    assert attention_content._raw_attention_text("  Alpha   beta  ") == "Alpha beta"
    assert attention_content._raw_attention_text(np.nan) == ""


def test_headline_items_from_news_payload_filters_blank_headlines():
    news_payload = {
        "articles": pd.DataFrame(
            [
                {
                    "headline": "First headline",
                    "summary": "First summary",
                    "source": "Newswire",
                    "url": "https://example.com/1",
                    "published_at": "2026-04-01T09:00:00Z",
                },
                {
                    "headline": "   ",
                    "summary": "Ignored",
                    "source": "Newswire",
                    "url": "https://example.com/ignored",
                    "published_at": "2026-04-01T08:00:00Z",
                },
                {
                    "headline": "Second headline",
                    "description": "Fallback description",
                    "source": "Blog",
                    "url": "https://example.com/2",
                    "published_at": "2026-04-01T07:00:00Z",
                },
            ]
        )
    }

    items = attention_content._headline_items_from_news_payload(news_payload, limit=3)

    assert items == [
        {
            "headline": "First headline",
            "summary": "First summary",
            "source": "Newswire",
            "url": "https://example.com/1",
            "published_at": "2026-04-01T09:00:00+00:00",
        },
        {
            "headline": "Second headline",
            "summary": "Fallback description",
            "source": "Blog",
            "url": "https://example.com/2",
            "published_at": "2026-04-01T07:00:00+00:00",
        },
    ]


def test_build_attention_brief_input_assembles_context_and_company_fields(monkeypatch):
    monkeypatch.setattr(
        attention_content,
        "dashboard_business_lens_from_taxonomy_row",
        lambda row: "Cloud Software",
    )
    monkeypatch.setattr(
        attention_content,
        "build_attention_news_narrative",
        lambda symbol, news_payload, peer_group_name="": {"narrative_text": f"{symbol} narrative for {peer_group_name}"},
    )
    monkeypatch.setattr(
        attention_content,
        "build_company_description",
        lambda symbol, asset, fundamentals, regime, **kwargs: f"{symbol} company description",
    )

    row = pd.Series(
        {
            "entity_id": "aapl",
            "title": "Apple alert",
            "subtitle": "Large cap tech",
            "story_text": "",
            "why_now_text": "  Demand inflecting higher.  ",
            "peer_group_name": "Megacap Tech",
            "regime_label": "Risk On",
            "linked_news_count": "2",
            "next_best_action": "Watch margins",
        }
    )
    brief_input = attention_content._build_attention_brief_input(
        row,
        news_payload={"articles": pd.DataFrame([{"headline": "Demand improves"}])},
        context_payload={
            "llm_headline": "Primary-source headline",
            "llm_summary_text": "Primary-source summary",
            "llm_narrative_text": "Narrative",
            "llm_why_now": "Why now",
            "primary_source_excerpt": "Excerpt",
        },
        asset={"name": "Apple Inc."},
    )

    assert brief_input["symbol"] == "AAPL"
    assert brief_input["company_name"] == "Apple Inc."
    assert brief_input["story_text"] == "Demand inflecting higher."
    assert brief_input["news_narrative"] == "AAPL narrative for Megacap Tech"
    assert brief_input["company_description"] == "AAPL company description"
    assert brief_input["linked_news_count"] == 2
    assert brief_input["context_headline"] == "Primary-source headline"


def test_load_attention_brief_payloads_reuses_asset_metadata_and_keys_by_event(monkeypatch):
    monkeypatch.setattr(
        attention_content,
        "dashboard_business_lens_from_taxonomy_row",
        lambda row: "Cloud Software",
    )
    monkeypatch.setattr(
        attention_content,
        "build_attention_news_narrative",
        lambda symbol, news_payload, peer_group_name="": {"narrative_text": f"{symbol} narrative"},
    )
    monkeypatch.setattr(
        attention_content,
        "build_company_description",
        lambda symbol, asset, fundamentals, regime, **kwargs: f"{symbol} company description",
    )

    asset_calls: list[str] = []

    def _fake_asset_loader(cfg, symbol, force_refresh=False):
        del cfg, force_refresh
        asset_calls.append(symbol)
        return {"name": f"{symbol} Inc."}

    def _fake_brief_loader(brief_input_json: str, *, use_llm: bool):
        payload = json.loads(brief_input_json)
        return {"title": payload["title"], "company_name": payload["company_name"], "use_llm": use_llm}

    monkeypatch.setattr(attention_content.dashboard_loaders, "_load_asset_metadata_cached", _fake_asset_loader)
    monkeypatch.setattr(attention_content.dashboard_loaders, "_load_attention_feed_brief_cached", _fake_brief_loader)

    rows = pd.DataFrame(
        [
            {
                "_homepage_v2_event_id": "event-1",
                "entity_id": "AAPL",
                "horizon": "1d",
                "title": "Event one",
                "why_now_text": "First",
            },
            {
                "_homepage_v2_event_id": "event-2",
                "entity_id": "AAPL",
                "horizon": "5d",
                "title": "Event two",
                "why_now_text": "Second",
            },
        ]
    )

    payloads = attention_content._load_attention_brief_payloads(
        object(),
        rows,
        news_payloads={"AAPL": {"articles": pd.DataFrame()}},
        context_payloads={"AAPL": {}},
        use_llm=False,
    )

    assert asset_calls == ["AAPL"]
    assert payloads == {
        "event-1": {"title": "Event one", "company_name": "AAPL Inc.", "use_llm": False},
        "event-2": {"title": "Event two", "company_name": "AAPL Inc.", "use_llm": False},
    }


def test_homepage_event_record_and_summary_prefer_brief_payload(monkeypatch):
    monkeypatch.setattr(
        attention_content,
        "build_attention_news_narrative",
        lambda symbol, news_payload, peer_group_name="": {"narrative_text": f"{symbol} cluster"},
    )

    row = pd.Series(
        {
            "_homepage_v2_event_id": "event-7",
            "entity_id": "msft",
            "title": "Microsoft alert",
            "subtitle": "Cloud momentum",
            "source_label": "Attention",
            "horizon": "1d",
            "anomaly_type": "earnings",
            "attention_score": np.float64(72.5),
            "why_now_text": "Fresh check",
            "next_best_action": "Watch Azure growth",
            "peer_group_name": "Megacap Tech",
        }
    )
    news_payload = {
        "articles": pd.DataFrame(
            [
                {"headline": "Headline 1"},
                {"headline": "Headline 2"},
                {"headline": "Headline 3"},
                {"headline": "Headline 4"},
            ]
        )
    }
    context_payload = {
        "llm_headline": "Context headline",
        "llm_summary_text": "Context summary",
        "llm_why_now": "Context why now",
        "llm_management_signal": "Management signal",
    }
    brief_payload = {
        "lead_text": "Lead sentence",
        "cluster_text": "Cluster sentence",
        "headline_text": "Headline sentence",
        "company_text": "Company sentence",
        "explainer_text": "Explainer sentence",
        "watchpoint_text": "Watch backlog",
    }

    record = attention_content._build_homepage_v2_event_record(
        row,
        news_payload=news_payload,
        context_payload=context_payload,
        brief_payload=brief_payload,
    )
    summary = attention_content._homepage_v2_item_summary(
        row,
        context_payload=context_payload,
        brief_payload=brief_payload,
    )

    assert record["event_id"] == "event-7"
    assert record["symbol"] == "MSFT"
    assert record["attention_score"] == 72.5
    assert record["story_text"] == "Lead sentence"
    assert record["cluster_text"] == "Cluster sentence"
    assert record["news_headlines"] == ["Headline 1", "Headline 2", "Headline 3"]
    assert summary == "Lead sentence Headline sentence Company sentence"


def test_build_attention_micro_chart_returns_bar_chart_with_gap_annotation():
    figure = attention_content._build_attention_micro_chart(
        pd.Series({"expected_value": 1.5, "observed_value": 4.0, "residual_value": 2.5})
    )

    assert figure is not None
    assert len(figure.data) == 1
    assert list(figure.data[0]["y"]) == ["Expected", "Observed"]
    assert figure.layout.annotations[0]["text"] == "Gap +2.50%"
