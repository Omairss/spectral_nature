"""Publication identity and preservation contracts; no generated prose grading."""
from copy import deepcopy

import pytest

from pipeline.jobs.attention_home_build import (
    AttentionHomeBuildError,
    _HOME_PUBLIC_TEXT_FIELDS,
    _apply_home_surface_quality_items,
)


def publication(source_ids):
    return {"stories": [{"section": "top_events", "question": "", "source_ids": source_ids,
                         "supporting_symbols": ["EXAMPLE"], "time_basis": {}, "cause_status": "partial",
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
    assert row["story_id"] == row["event_id"] and row["research_bundle_ids"] == ["retained", "context"]
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


def test_public_reader_preserves_the_whole_selected_publication(monkeypatch):
    from types import SimpleNamespace
    from data_access.contracts import DataProvenance
    from services.attention_materialized import serialize_attention_home_payload, deserialize_attention_home_payload
    from services.home_view import build_public_home_view
    monkeypatch.setenv('ATTENTION_HOME_TOP_EVENTS_DISPLAY_LIMIT', '20')
    selected = publication(['source:original'])
    story = selected['stories'][0]
    selected['stories'] = [dict(story) for _ in range(20)]
    updated, _, _ = _apply_home_surface_quality_items({}, {}, selected,
        source_records={'source:original': {'metadata': {'kind': 'source_document'},
                        'content': {'source_id': 'original', 'raw_text': 'Complete original'}}},
        asof_time_utc='2026-10-30T18:50Z')
    saved = deserialize_attention_home_payload(serialize_attention_home_payload(updated))
    resolved = lambda value: SimpleNamespace(payload=value, provenance=DataProvenance(mode='materialized', datasets=()))
    service = SimpleNamespace(data_access=SimpleNamespace(
        resolve_attention_home_1d=lambda **kwargs: resolved(saved),
        resolve_attention_ticker_snapshots=lambda symbols: resolved({}),
        resolve_daily_market_datapoints=lambda: resolved([])))
    view = build_public_home_view(service)
    assert len(view['beats']) == 20
    assert [row['id'] for row in view['beats']] == [row['bundle_id'] for row in saved['top_events']]


def test_publication_source_contract_is_stable_and_bound_to_reader(monkeypatch):
    from pipeline.jobs import attention_home_build as home
    seen = []
    def run(**kwargs):
        schema = kwargs['schema']['properties']['stories']['items']
        identities = schema['properties']['source_ids']['items']['enum']
        assert 'source:saa_doc::first' in identities and 'source:saa_doc::second' in identities
        assert not any(identity.startswith('company-research:') for identity in identities)
        assert 'Complete first' in kwargs['service'].read_supplied_evidence(source_id='source:saa_doc::first')['text']
        assert 'Original claim' in kwargs['service'].read_supplied_evidence(source_id='bundle:research')['text']
        assert 'claims' not in kwargs['initial_evidence'][0]['completed_research_findings'][0]
        seen.append(set(identities))
        value = publication(['source:saa_doc::first'])
        value.update(market_summary={'summary_text': 'Current interpretation', 'confidence': 'medium', 'limitations': []}, issues=[])
        # The test exercises source plumbing, not generated wording.
        value['stories'][0]['time_basis'] = {'current_observation': '2026-10-30T18:50Z', 'background_events': [], 'report_dates': 'Original'}
        return {'status': 'completed', 'payload': value}
    monkeypatch.setattr(home, 'run_aql_zopedia_structured_agent', run)
    monkeypatch.setattr(home, 'complete_core_research', lambda *args: None)
    documents = [{'canonical_document_id': 'saa_doc::first', 'raw_text': 'Complete first'},
                 {'canonical_document_id': 'saa_doc::second', 'raw_text': 'Complete second'}]
    bundles = {'research': {'findings_markdown': 'Complete findings',
                           'claims': [{'claim_text': 'Original claim', 'evidence_role': 'reported_fact'}]}}
    for intake in (documents, list(reversed(documents))):
        home._review_home_public_surface({}, bundles, enrichment_rows=[{'research_origin': 'common_market_agenda'}],
            source_documents=intake, asof_time_utc='2026-10-30T18:50Z', timeout_seconds=10)
    assert seen[0] == seen[1]


def test_retained_reports_remain_readable_without_new_research_or_stale_prices(monkeypatch):
    from pipeline.jobs import attention_home_build as home
    past = {"artifact_id": "past", "created_at_utc": "2026-10-29T20:00Z", "result": {
        "agent_result": {"private_execution_trace": "Internal diagnostic"},
        "payload": {"event_title": "Existing investigation", "findings_markdown": "Complete retained report",
                    "supporting_symbols": ["EXAMPLE"]},
        "artifact_context": {"asof_time_utc": "2026-10-29T18:50Z", "source_documents": [
            {"source_id": "original", "url": "https://example.com/report", "raw_text": "Complete original"}]}}}
    future = {**past, "artifact_id": "future", "created_at_utc": "2026-10-31T20:00Z"}
    def run(**kwargs):
        visible = kwargs["initial_evidence"][0]
        assert visible["completed_research_findings"] == []
        assert "Complete retained report" not in str(visible)
        index = {row["source_id"]: row for row in visible["source_index"]}
        assert "retained-research:past" in index
        assert "retained-research:future" not in index
        read = kwargs["service"].read_supplied_evidence(source_id="retained-research:past")
        assert "Complete retained report" in read["text"]
        assert "private_execution_trace" not in read["text"]
        value = publication(["retained-research:past"])
        value.update(market_summary={"summary_text": "Current explanation", "confidence": "medium", "limitations": []}, issues=[])
        value["stories"][0]["time_basis"] = {"current_observation": "2026-10-30T18:50Z", "background_events": [], "report_dates": "Original"}
        return {"status": "completed", "payload": value}
    monkeypatch.setattr(home, "run_aql_zopedia_structured_agent", run)
    monkeypatch.setattr(home, "complete_core_research", lambda *args: None)
    payload, bundles, _ = home._review_home_public_surface(
        {"event_candidates_1d": [{"symbol": "EXAMPLE", "change_pct": 7}]}, {}, enrichment_rows=[],
        retained_research=[past, future], asof_time_utc="2026-10-30T18:50Z", timeout_seconds=10)
    story = payload["top_events"][0]
    assert story["change_pct"] == 7
    assert story["evidence"][0]["raw_text"] == "Complete original"
    assert story["research_evidence"][0]["payload"] == past["result"]["payload"]
    assert story["research_evidence"][0]["artifact_id"] == "past"
    assert story["research_source_ids"] == ["retained-research:past"]
