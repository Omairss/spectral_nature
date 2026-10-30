"""Publication identity and preservation contracts; no generated prose grading."""
from copy import deepcopy

import pytest

from pipeline.jobs.attention_home_build import (
    AttentionHomeBuildError,
    _HOME_PUBLIC_TEXT_FIELDS,
    _apply_home_surface_quality_items,
)


def publication(source_ids):
    selected = {"story_id": "chosen", "question": "", "source_ids": source_ids,
                "supporting_symbols": ["EXAMPLE"]}
    return {"selection": {"top_events": [selected], "must_read_movers": [], "unresolved_large_moves": []},
            "stories": [{"story_id": "chosen", "time_basis": {}, "cause_status": "partial",
                         **{field: field for field in _HOME_PUBLIC_TEXT_FIELDS}}]}


def test_reader_identity_selects_previously_unpublished_research_without_losing_metadata():
    bundles = {"retained": {"bundle_id": "retained", "symbol": "EXAMPLE", "change_pct": -9,
                            "price_history": [1, 2], "evidence": [{"raw_text": "original"}]}}
    records = {"bundle:retained": {"metadata": {"kind": "shared_research", "bundle_id": "retained"},
                                  "content": bundles["retained"]},
               "bundle:context": {"metadata": {"kind": "shared_research", "bundle_id": "context"},
                                  "content": {"bundle_id": "context"}}}
    bundles["context"] = {"bundle_id": "context"}
    original = deepcopy(bundles)
    updated, saved, counts = _apply_home_surface_quality_items(
        {"top_events": [], "must_read_movers": [], "unresolved_large_moves": []}, bundles,
        publication(["bundle:retained", "bundle:context"]), source_records=records, asof_time_utc="2026-10-29T17:49:00Z")
    row = updated["top_events"][0]
    assert row["bundle_id"] != "retained"
    assert row["story_id"] == "chosen" and row["research_bundle_ids"] == ["retained", "context"]
    assert row["change_pct"] == -9 and row["price_history"] == [1, 2]
    assert row["evidence"] == original["retained"]["evidence"]
    assert row["research_source_ids"] == ["bundle:retained", "bundle:context"]
    assert all(row[field] == field for field in _HOME_PUBLIC_TEXT_FIELDS)
    assert not saved["retained"]["publish"] and saved[row["bundle_id"]]["publish"]
    assert counts["reviewed"] == 1
    assert bundles == original


def test_combined_sources_preserve_originals_and_reject_unknown_reader_identity():
    records = {
        "source:article:0": {"metadata": {"kind": "source_document"},
                             "content": {"source_id": "article", "raw_text": "complete original"}},
        "market-observations": {"metadata": {"kind": "market_observations"},
                                "content": {"observations": [{"symbol": "EXAMPLE", "change_pct": 7}]}},
    }
    updated, bundles, counts = _apply_home_surface_quality_items(
        {}, {}, publication(list(records)), source_records=records, asof_time_utc="2026-10-29T17:49:00Z")
    row = updated["top_events"][0]
    assert row["evidence"][0]["raw_text"] == "complete original"
    assert row["research_evidence"] == [records["market-observations"]["content"]]
    assert row["source_ids"] == ["article"]
    assert bundles[row["bundle_id"]]["research_source_ids"] == list(records)
    assert counts["discovered"] == 1
    with pytest.raises(AttentionHomeBuildError, match="identity is unknown"):
        _apply_home_surface_quality_items({}, {}, publication(["unknown"]), source_records=records,
                                         asof_time_utc="2026-10-29T17:49:00Z")
