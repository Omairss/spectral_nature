"""Evidence access contracts, not generated prose or qualitative grading."""
from types import SimpleNamespace

from data_access.query_service import QueryService
from services.zopedia_agent import _tool_history_prompt
from services import zopedia_research


def test_compact_history_keeps_original_readable():
    body = 'original document content ' * 1000
    call = {'tool_call_id':'read-1','tool_name':'research.read_supplied_evidence','status':'completed',
            'result_summary':{'evidence_context_text':body,'llm_context_text':'source metadata',
                              'evidence_refs':[{'url':'https://example.com/original'}]}}
    service = QueryService(data_access=SimpleNamespace()).with_supplied_evidence({
        'history:read-1':{'title':'Original','metadata':{},'content':call}})
    compact = _tool_history_prompt([call])
    assert body not in compact
    assert 'history:read-1' in compact
    original = service.read_supplied_evidence(source_id='history:read-1',pointer='/result_summary/evidence_context_text',max_characters=60000)
    assert original['text'] == body
    assert body in _tool_history_prompt([call],full_evidence=True)


def test_wiki_search_returns_page_identity_without_expanding_ancestry(monkeypatch):
    import pandas as pd
    urls = ['https://example.com/source/' + str(i) for i in range(3000)]
    monkeypatch.setattr(zopedia_research,'search_zopedia_pages',lambda **kwargs:pd.DataFrame([{
        'page_id':'page-1','page_type':'company','title':'Company','summary':'Dated finding','source_urls':urls,
        'version':4,'entity_refs':[],'outgoing_links':[]}]))
    result = zopedia_research.zopedia_search_pages(query='company')
    row = result['summary'][0]
    assert row['page_id'] == 'page-1' and row['source_version'] == 4
    assert row['source_reference_count'] == 3000 and row['source_refs'] == []
    assert row['source_reference_resource'] == {'tool':'zopedia.sources_for_page','page_id':'page-1'}
    assert len(str(result)) < 3000


def test_model_reads_selected_original_without_loading_other_documents(monkeypatch):
    import json,time
    from services import zopedia_agent as agent
    body = 'complete selected passage ' * 300
    service = QueryService(data_access=SimpleNamespace()).with_supplied_evidence({
        'chosen':{'title':'Selected original','metadata':{'date':'2026-11-03'},'content':body},
        'other':{'title':'Other original','metadata':{},'content':'unselected body ' * 1000}})
    prompts = []
    def generate(**kwargs):
        prompts.append(kwargs['user_prompt'])
        if len(prompts) == 1:
            assert body not in prompts[-1]
            return {'decision':{'action':'read','tool_name':'research.read_supplied_evidence',
                'tool_arguments':json.dumps({'source_id':'chosen','max_characters':16000})}}
        assert body in prompts[-1]
        assert 'unselected body' not in prompts[-1]
        return {'decision':{'value':1}}
    monkeypatch.setattr(agent,'generate_json_via_aql_zopedia_gateway',generate)
    result = agent._run_evidence_model_step(llm_client=object(),service=service,tool_calls=[],context_state={},
        prompt=lambda calls:agent._tool_history_prompt(calls,full_evidence=True),system_prompt='contract',
        schema_name='read_contract',schema={'type':'object','properties':{'value':{'type':'integer'}},'required':['value']},
        surface='isolated',purpose='contract',deadline_monotonic=time.monotonic()+5,metadata={},progress_callback=None)
    assert result['value'] == 1 and len(prompts) == 2


def test_economical_completion_can_read_retained_originals(monkeypatch):
    import json,time
    from services import zopedia_agent as agent
    service=QueryService(data_access=SimpleNamespace()).with_supplied_evidence({
        'saved':{'title':'Saved evidence','metadata':{},'content':'original observation'}})
    calls=[]
    monkeypatch.setattr(agent,'best_effort_required',lambda:True)
    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls)==1:
            return {'decision':{'action':'read','tool_name':'research.read_supplied_evidence',
                'tool_arguments':json.dumps({'source_id':'saved'})}}
        assert 'original observation' in kwargs['user_prompt']
        return {'decision':{'value':1}}
    monkeypatch.setattr(agent,'generate_json_via_aql_zopedia_gateway',generate)
    result=agent._run_evidence_model_step(llm_client=object(),service=service,tool_calls=[],context_state={},
        prompt=lambda items:agent._tool_history_prompt(items,full_evidence=True),system_prompt='contract',
        schema_name='retained_read',schema={'type':'object','properties':{'value':{'type':'integer'}},'required':['value']},
        surface='isolated',purpose='contract',deadline_monotonic=time.monotonic()+5,metadata={},progress_callback=None)
    assert result['value']==1 and len(calls)==2
