"""Numerical, provenance and dispatch contracts; these do not grade LLM quality."""
import numpy as np
import pandas as pd

from compute.market_attention import build_market_attention_context
from services.attention_materialized import (
    bars_by_symbol_from_price_history, deserialize_attention_home_payload,
    serialize_attention_home_payload,
)
from services.aql import events, pipeline


def histories():
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2026-06-01", periods=100, tz="UTC") + pd.Timedelta(hours=4)
    common = rng.normal(0, .01, 100)
    independent = rng.normal(0, .01, 100)
    return {symbol: pd.DataFrame({"timestamp": dates,
        "close": 100 * np.cumprod(1 + values)}).rename_axis(None)
        for symbol, values in {"A": common, "B": common + rng.normal(0, .001, 100),
                               "C": independent}.items()}


def test_groups_use_adjusted_common_history_and_exclude_future_and_partial_bars():
    bars = histories()
    for frame in bars.values():
        frame.attrs["price_adjustment"] = "all"
    asof = bars["A"].timestamp.iloc[-1] + pd.Timedelta(hours=10)
    movers = pd.DataFrame({"symbol": list(bars), "change_pct": [0, 0, 0]})
    context = build_market_attention_context(movers, bars, asof_time_utc=asof)
    assert context["coverage"]["comparable_relationship_symbols"] == 3
    assert any(set(group["members"]) == {"A", "B"} for group in context["groups"])
    before = context["groups"]
    for frame in bars.values():
        frame.loc[len(frame)] = [asof + pd.Timedelta(days=1), 100000]
    after = build_market_attention_context(movers, bars, asof_time_utc=asof)
    assert before == after["groups"]
    assert pd.Timestamp(after["method"]["current_window"][-1]).date() < asof.date()
    bars["C"].attrs.clear()
    unknown = build_market_attention_context(movers, bars, asof_time_utc=asof)
    assert unknown["coverage"]["unverified_adjustment"] == ["C"]
    assert len(unknown["observations"]) == 3


def test_snapshot_roundtrip_and_history_cutoff():
    payload = {"market_context": {"columns": ["symbol"], "observations": [["A"]]}, "top_events": []}
    assert deserialize_attention_home_payload(serialize_attention_home_payload(payload))["market_context"] == payload["market_context"]
    history = pd.DataFrame({"symbol": ["A", "A"], "timestamp": ["2026-10-29T04:00Z", "2026-10-30T04:00Z"], "close": [10, 11]})
    selected = bars_by_symbol_from_price_history(history, ["A"], asof_time_utc="2026-10-29T18:00Z")
    assert len(selected["A"]) == 1


def plan(**changes):
    return {"title": "Question", "question": "Investigate the observed change", "source_ids": [],
        "related_symbols": ["A"], "followup_questions": [], "rationale": "Current observation",
        "research_action": "investigate", "prior_artifact_id": "", "group_ids": [],
        "economic_series_ids": [], **changes}


def answer():
    return {"event_title": "Question", "findings_markdown": "Retained substantive analysis",
        "cause_status": "partial", "supporting_symbols": ["A"], "claims": []}


def test_one_agenda_dispatches_only_selected_questions_with_focused_context(monkeypatch):
    monkeypatch.delenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE", raising=False)
    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        if kwargs["task"] == "source_event_nomination":
            assert "anomaly_score" in kwargs["service"].read_supplied_evidence(source_id="market-observations")["text"]
            return {"status": "completed", "payload": {"research_agenda": ["Question"],
                "findings_markdown": "Current assessment", "events": [plan()]}}
        assert '"symbol": "UNRELATED"' not in kwargs["query"]
        assert "UNRELATED" in kwargs["service"].read_supplied_evidence(source_id="market-observations")["text"]
        return {"status": "completed", "payload": answer(), "agent_result": {"quality_review": {"status": "completed"}}}

    monkeypatch.setattr(events, "run_aql_zopedia_structured_agent", run)
    movers = pd.DataFrame({"symbol": ["A", "UNRELATED"], "close": [10, 11], "prev_close": [9, 10],
                           "change_pct": [1, 2], "volume": [100, 200]})
    result = pipeline.build_bottom_up_attention_artifacts(movers, llm_client=object(),
        generated_at_utc="2026-10-30T18:50Z", run_id="contract")
    assert [call["task"] for call in calls] == ["source_event_nomination", "source_event_research"]
    assert set(result.frames["attention_candidates_1d"].symbol) == {"A", "UNRELATED"}
    assert {row["symbol"] for row in result.home_payload["event_candidates_1d"]} == {"A", "UNRELATED"}
    assert result.home_payload["market_context"]["coverage"]["observed_symbols"] == 2
    assert len(result.bundle_map) == 1


def test_zero_selected_investigations_preserve_current_numerical_signals(monkeypatch):
    monkeypatch.delenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE", raising=False)
    calls = []
    def run(**kwargs):
        calls.append(kwargs['task'])
        return {'status': 'completed', 'payload': {'research_agenda': [],
            'findings_markdown': 'Current assessment', 'events': []}}
    monkeypatch.setattr(events, 'run_aql_zopedia_structured_agent', run)
    result = pipeline.build_bottom_up_attention_artifacts(pd.DataFrame({'symbol': ['A'], 'close': [10],
        'prev_close': [9], 'change_pct': [1], 'volume': [100]}), llm_client=object(),
        generated_at_utc='2026-10-30T18:50Z', run_id='quiet-contract')
    assert calls == ['source_event_nomination']
    assert result.frames['attention_candidates_1d'].symbol.tolist() == ['A']
    assert result.bundle_map == {} and result.home_payload['top_events'] == []


def test_reuse_keeps_original_sources_dates_and_distinct_question_identity(monkeypatch):
    monkeypatch.setenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE", "contract")
    source = {"source_id": "original", "document_id": "original", "title": "Original disclosure",
        "url": "https://example.invalid/original", "published_at": "2026-10-20T12:00Z",
        "text": "Original complete text", "raw_text": "Original complete text", "source_kind": "news",
        "source_provider": "original"}
    prior = {"artifact_id": "prior", "subject": "event:original", "created_at_utc": "2026-10-20T13:00Z",
        "result": {"status": "completed", "payload": answer(), "agent_result": {
            "tool_calls": [{"status": "failed"}], "quality_review": {"status": "failed"}}, "artifact_context": {
            "asof_time_utc": "2026-10-20T13:00Z", "source_documents": [source]}}}
    monkeypatch.setattr(events.pipeline_store, "list_research_artifacts", lambda **kwargs: [prior])
    monkeypatch.setattr(events, "latest_research_artifact", lambda **kwargs: None)

    def run(**kwargs):
        assert kwargs["task"] == "source_event_nomination", "Reusing selected research must not buy another investigation"
        return {"status": "completed", "payload": {"research_agenda": [], "findings_markdown": "Current assessment",
            "events": [plan(research_action="reuse", prior_artifact_id="prior"),
                       plan(question="A separate current question", research_action="reuse", prior_artifact_id="prior")]}}

    monkeypatch.setattr(events, "run_aql_zopedia_structured_agent", run)
    result = events.research_source_events(pd.DataFrame(), market_frame=pd.DataFrame(), universe_frame=None,
        asof_time_utc="2026-10-30T18:50Z", run_id="current", llm_client=object())
    assert len({bundle["bundle_id"] for bundle in result["bundles"]}) == 2
    assert result['status'] == 'completed', 'Historical acquisition errors are not new execution failures'
    assert all(bundle["research_asof_time_utc"] == "2026-10-20T13:00Z" for bundle in result["bundles"])
    assert all(doc["published_at"] == source["published_at"] for doc in result["documents"])
    assert result["documents"][0]["raw_text"] == source["raw_text"]


def test_completed_work_survives_an_independent_cancelled_investigation(monkeypatch):
    from services.work_scope import WorkCancelled
    monkeypatch.delenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE", raising=False)

    def run(**kwargs):
        if kwargs["task"] == "source_event_nomination":
            return {"status": "completed", "payload": {"research_agenda": [], "findings_markdown": "Current assessment",
                "events": [plan(question="keep"), plan(question="cancel")]}}
        if kwargs["artifact_context"]["question"] == "cancel":
            raise WorkCancelled("Original deadline")
        return {"status": "completed", "payload": answer()}

    monkeypatch.setattr(events, "run_aql_zopedia_structured_agent", run)
    result = events.research_source_events(pd.DataFrame(), market_frame=pd.DataFrame(), universe_frame=None,
        asof_time_utc="2026-10-30T18:50Z", run_id="current", llm_client=object())
    assert result["status"] == "partial" and len(result["bundles"]) == 1
    assert sorted(row["status"] for row in result["results"]) == ["cancelled", "completed"]


def test_stale_history_does_not_become_a_current_relative_return():
    bars = histories()
    for frame in bars.values():
        frame.attrs["price_adjustment"] = "all"
    asof = bars["A"].timestamp.iloc[-1] + pd.Timedelta(hours=10)
    bars["C"] = bars["C"].iloc[:-4].copy()
    context = build_market_attention_context(pd.DataFrame({"symbol": list(bars)}), bars, asof_time_utc=asof, benchmark_symbol="A")
    rows = [dict(zip(context["columns"], values)) for values in context["observations"]]
    stale = next(row for row in rows if row["symbol"] == "C")
    assert stale.get("return_5d_pct") is None or pd.isna(stale["return_5d_pct"])
    assert stale.get("return_5d_excess_pp") is None or pd.isna(stale["return_5d_excess_pp"])


def test_prior_publication_reaches_focused_research_without_copying_unrelated_bodies(monkeypatch):
    monkeypatch.delenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE", raising=False)
    capture = {"title": "Original", "source_text": "Dated original body", "url": "https://example.invalid/a",
               "published_at": "2026-10-29T12:00Z", "canonical_document_id": "saa_doc::original"}
    previous = {"asof_time_utc": "2026-10-29T14:00Z", "stories": [
        {"supporting_symbols": ["A"], "what_happened_text": "Relevant prior finding", "evidence": [capture]},
        {"supporting_symbols": ["OTHER"], "what_happened_text": "Unrelated full body"}]}

    def run(**kwargs):
        if kwargs["task"] == "source_event_nomination":
            return {"status": "completed", "payload": {"research_agenda": [], "findings_markdown": "Assessment", "events": [plan()]}}
        assert "Relevant prior finding" in kwargs["query"]
        assert "Unrelated full body" not in kwargs["query"]
        original = kwargs["service"].read_supplied_evidence(source_id="saa_doc::original")
        assert "Dated original body" in original["text"]
        return {"status": "completed", "payload": answer()}

    monkeypatch.setattr(events, "run_aql_zopedia_structured_agent", run)
    result = events.research_source_events(pd.DataFrame(), market_frame=pd.DataFrame(), universe_frame=None,
        asof_time_utc="2026-10-30T18:50Z", run_id="current", llm_client=object(), previous_publication=previous)
    assert result["status"] == "completed"


def test_partial_snapshot_does_not_compare_yesterdays_return_with_todays_benchmark():
    bars = histories()
    for frame in bars.values():
        frame.attrs["price_adjustment"] = "all"
    asof = bars["A"].timestamp.iloc[-1] + pd.Timedelta(hours=10)
    bars["C"] = bars["C"].iloc[:-1].copy()
    context = build_market_attention_context(pd.DataFrame({"symbol": list(bars)}), bars,
        asof_time_utc=asof, benchmark_symbol="A")
    rows = [dict(zip(context["columns"], values)) for values in context["observations"]]
    row = next(row for row in rows if row["symbol"] == "C")
    assert pd.isna(row.get("return_5d_excess_pp"))


def test_relationship_change_compares_the_same_prior_peers():
    bars = histories()
    a = bars['A'].close.pct_change().fillna(0).to_numpy()
    c = bars['C'].close.pct_change().fillna(0).to_numpy()
    bars['B']['close'] = 100 * np.cumprod(1 + np.concatenate([a[:60], c[60:]]))
    for frame in bars.values():
        frame.attrs['price_adjustment'] = 'all'
    context = build_market_attention_context(pd.DataFrame({'symbol': list(bars)}), bars,
        asof_time_utc=bars['A'].timestamp.iloc[-1] + pd.Timedelta(days=1), comparison_shift=40)
    assert ['A', 'B'] in context['previous_groups'].values()
    assert any(group['members'] == ['B', 'C'] for group in context['groups'])
    b = next(dict(zip(context['columns'], row)) for row in context['observations']
             if row[context['columns'].index('symbol')] == 'B')
    assert b['prior_group_peer_correlation_previous_window'] > .99
    assert b['prior_group_peer_correlation_current_window'] < .4


def test_article_url_preserves_both_captures_and_claim_uses_its_named_capture(monkeypatch):
    monkeypatch.delenv('LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE', raising=False)
    url = 'https://example.invalid/article'
    previous = {'stories': [{'supporting_symbols': ['A'], 'evidence': [{
        'source_text': 'Earlier captured version', 'url': url, 'canonical_document_id': 'saa_doc::earlier'}]}]}
    def run(**kwargs):
        if kwargs['task'] == 'source_event_nomination':
            return {'status': 'completed', 'payload': {'research_agenda': [], 'findings_markdown': 'Assessment',
                'events': [plan(source_ids=[url])]}}
        sources = kwargs['initial_evidence']
        assert {source['text'] for source in sources} == {'Earlier captured version', 'Current captured version'}
        current = next(source for source in sources if source['text'] == 'Current captured version')
        return {'status': 'completed', 'payload': {**answer(), 'claims': [{
            'claim_text': 'Current finding', 'source_id': current['document_id'], 'source_url': url,
            'evidence_role': 'reported_fact'}]}}
    monkeypatch.setattr(events, 'run_aql_zopedia_structured_agent', run)
    result = events.research_source_events(pd.DataFrame([{'headline': 'Updated report', 'content': 'Current captured version',
        'url': url, 'published_at': '2026-10-30T12:00Z'}]), market_frame=pd.DataFrame(), universe_frame=None,
        asof_time_utc='2026-10-30T18:50Z', run_id='versions', llm_client=object(), previous_publication=previous)
    claim = result['bundles'][0]['claims'][0]
    assert claim['citation_status'] == 'resolved' and claim['document_id'] != 'saa_doc::earlier'


def test_failed_acquisition_or_review_is_visible_without_discarding_valid_research(monkeypatch):
    monkeypatch.delenv('LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE', raising=False)
    def run(**kwargs):
        if kwargs['task'] == 'source_event_nomination':
            return {'status': 'completed', 'payload': {'research_agenda': [], 'findings_markdown': 'Assessment', 'events': [plan()]}}
        return {'status': 'completed', 'payload': answer(), 'agent_result': {
            'tool_calls': [{'status': 'failed', 'error': 'Acquisition deadline'}],
            'quality_review': {'status': 'failed'}}}
    monkeypatch.setattr(events, 'run_aql_zopedia_structured_agent', run)
    result = events.research_source_events(pd.DataFrame(), market_frame=pd.DataFrame(), universe_frame=None,
        asof_time_utc='2026-10-30T18:50Z', run_id='partial', llm_client=object())
    assert result['status'] == 'partial' and len(result['bundles']) == 1
    assert result['results'][0]['tool_failure_count'] == 1
    assert result['results'][0]['review_status'] == 'failed'
