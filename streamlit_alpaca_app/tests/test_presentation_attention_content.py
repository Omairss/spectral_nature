from __future__ import annotations

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
