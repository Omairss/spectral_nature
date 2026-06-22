from __future__ import annotations

import numpy as np
import pandas as pd

from presentation import attention_content


def test_attention_text_helpers_normalize_blank_and_nan_values():
    assert attention_content._clean_attention_text("  Alpha   beta  ") == "Alpha beta"
    assert attention_content._raw_attention_text("  Alpha   beta  ") == "Alpha beta"
    assert attention_content._raw_attention_text(np.nan) == ""


def test_display_markdown_sections_split_one_line_zopedia_answer_and_escape_dollars():
    sections = attention_content.display_markdown_sections(
        "### What Changed Snowflake reported EPS of $0.39 on $1.39B revenue. "
        "### Most Likely Driver Company-specific earnings beat. "
        "### What To Watch Watch whether SNOW holds above $240."
    )

    assert sections == [
        ("What Changed", "Snowflake reported EPS of \\$0.39 on \\$1.39B revenue."),
        ("Most Likely Driver", "Company-specific earnings beat."),
        ("What To Watch", "Watch whether SNOW holds above \\$240."),
    ]


def test_streamlit_safe_markdown_text_removes_inline_code_and_heading_markers():
    assert (
        attention_content.streamlit_safe_markdown_text("### Background `0.39` and $1.39B")
        == "Background 0.39 and \\$1.39B"
    )


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




def test_build_attention_micro_chart_returns_bar_chart_with_gap_annotation():
    figure = attention_content._build_attention_micro_chart(
        pd.Series({"expected_value": 1.5, "observed_value": 4.0, "residual_value": 2.5})
    )

    assert figure is not None
    assert len(figure.data) == 1
    assert list(figure.data[0]["y"]) == ["Expected", "Observed"]
    assert figure.layout.annotations[0]["text"] == "Gap +2.50%"
