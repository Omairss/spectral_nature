from __future__ import annotations

import pandas as pd

from services.common.news_freshness import coerce_article_published_at


def test_coerce_article_published_at_handles_relative_provider_dates():
    asof = pd.Timestamp("2026-06-23T12:00:00Z")

    published_at = coerce_article_published_at("12 hours ago", asof_time_utc=asof)

    assert published_at == pd.Timestamp("2026-06-23T00:00:00Z")


def test_coerce_article_published_at_handles_yesterday():
    asof = pd.Timestamp("2026-06-23T12:00:00Z")

    published_at = coerce_article_published_at("yesterday", asof_time_utc=asof)

    assert published_at == pd.Timestamp("2026-06-22T12:00:00Z")
