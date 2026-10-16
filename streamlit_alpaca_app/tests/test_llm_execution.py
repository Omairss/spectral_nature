"""Execution contracts only; research quality is reviewed from live artifacts."""
from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import requests

from services import llm, llm_execution as execution, pipeline_store


@pytest.fixture
def store(monkeypatch):
    values = {}
    events = []
    monkeypatch.setenv("APP_TRACK", "development")
    monkeypatch.setenv("PIPELINE_RUN_ID", "root-job")
    monkeypatch.setattr(pipeline_store, "read_execution_state", lambda ns, key, **kwargs: values.get((ns, key)))
    def claim(ns, key, *, owner, seconds):
        if (ns, key) in values:
            return False
        values[(ns, key)] = {"owner": owner}
        return True
    def release(ns, key, *, owner):
        if values.get((ns, key), {}).get("owner") == owner:
            values.pop((ns, key), None)
    monkeypatch.setattr(pipeline_store, "claim_execution_state", claim)
    monkeypatch.setattr(pipeline_store, "release_execution_state", release)
    def write(ns, key, value, *, immutable=False):
        if not immutable or (ns, key) not in values:
            values[ns, key] = json.loads(json.dumps(value, default=str))
        return True
    monkeypatch.setattr(pipeline_store, "write_execution_state", write)
    def checkpoint(*, namespace, input_key, artifact, subject, task):
        write(namespace, artifact["artifact_id"], artifact, immutable=True)
        write(namespace+":inputs", input_key, {"artifact_id": artifact["artifact_id"]})
        if subject:
            write(namespace+":latest", subject+":"+task, {"artifact_id": artifact["artifact_id"]})
        return values[(namespace, artifact["artifact_id"])]
    monkeypatch.setattr(pipeline_store, "retain_research_checkpoint", checkpoint)
    monkeypatch.setattr(pipeline_store, "record_provider_attempt", lambda event: events.append(event) or True)
    execution._ACCOUNTS.clear()
    return values, events


def response(*, status=200, content='{"value":1}', usage=None):
    value = requests.Response()
    value.status_code = status
    value._content = json.dumps({"model": "deepseek-flash", "usage": usage,
        "choices": [{"message": {"content": content}}]}).encode()
    return value


def client(responses):
    calls = []
    def post(**kwargs):
        calls.append(kwargs)
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
    config = llm.LLMConfig(provider="deepseek", api_key="fake", model="deepseek-reasoner",
                           base_url="https://example.invalid", thinking_mode="enabled")
    return llm.DeepSeekChatJSONClient(config, session=SimpleNamespace(post=post)), calls


def generate(model):
    return model.generate_json(system_prompt="Return supplied values.", user_prompt="Evidence: value=1",
        schema_name="transport", schema={"type": "object", "properties": {"value": {"type": "integer"}}})


def test_transport_records_retry_and_repair_with_causal_root(store):
    model, calls = client([requests.ConnectionError("disconnected"), response(content="{broken", usage={"prompt_tokens": 10}),
                           response(usage={"prompt_tokens": 11})])
    with execution.model_call_context(model_call_id="logical-call", run_id="child-agent", surface="research", purpose="resolve"):
        assert generate(model)["value"] == 1
    events = [event for event in store[1] if event["status"] != "started"]
    assert len(calls) == len(events) == 3
    assert [event["operation"] for event in events] == ["chat", "chat", "json_repair"]
    assert events[0]["usage"] is None
    assert events[1]["usage"]["prompt_tokens"] == 10
    assert {event["root_run_id"] for event in events} == {"root-job"}
    assert {event["parent_run_id"] for event in events} == {"child-agent"}
    assert len({event["attempt_id"] for event in events}) == 3
    assert calls[-1]["json"]["max_tokens"] == 65536
    assert "max_tokens" not in calls[0]["json"]


def test_billing_circuit_is_shared_and_recovers_without_paid_probe(store, monkeypatch):
    model, calls = client([response(status=402), response()])
    with pytest.raises(llm.LLMAPIError):
        generate(model)
    with pytest.raises(llm.LLMProviderUnavailable):
        generate(model)
    assert len(calls) == 1
    execution._ACCOUNTS.clear()  # A second worker reads the durable account state.
    balance = requests.Response(); balance.status_code = 200; balance._content = b'{"is_available":true}'
    probes = []
    monkeypatch.setattr(execution.requests, "get", lambda *a, **k: probes.append(a) or balance)
    assert generate(model)["value"] == 1
    assert len(calls) == 2 and len(probes) == 1
    assert "fake" not in json.dumps(list(store[0]))


def test_effort_is_scoped_and_schema_does_not_change_system_prefix(store):
    model, calls = client([response(), response()])
    with execution.model_call_context(inference_effort="none"):
        generate(model)
    generate(model)
    assert calls[0]["json"]["thinking"] == {"type": "disabled"}
    assert calls[1]["json"]["thinking"] == {"type": "enabled"}
    assert model.config.thinking_mode == "enabled"
    assert calls[0]["json"]["messages"][0]["content"] == calls[1]["json"]["messages"][0]["content"]
    assert calls[0]["json"]["messages"][1]["content"].startswith("Evidence: value=1")


def test_literal_conversion_preserves_style_and_global_thinking_configuration(store):
    from dataclasses import replace
    model, calls = client([response(), response()])
    model.config = replace(model.config, thinking_mode="disabled", reasoning_effort="high")
    with execution.model_call_context(literal_output=True):
        generate(model)
    generate(model)
    assert calls[0]["json"]["messages"][0]["content"] == "Return supplied values."
    assert "Global language policy" in calls[1]["json"]["messages"][0]["content"]
    assert all(call["json"]["thinking"] == {"type": "disabled"} for call in calls)
    assert calls[0]["json"]["max_tokens"] == 65536
    assert "max_tokens" not in calls[1]["json"]


def test_evidence_stream_prefix_and_complete_passages():
    from services.llm_context import serialize_llm_context, organize_source_captures
    passage = "source observation " * 200 + "decisive final observation"
    first = {"url": "https://example.invalid/source", "text": passage, "published_at": "2026-10-11"}
    second = {**first, "published_at": "2026-10-12"}
    before = serialize_llm_context([first], append_only=True)
    after = serialize_llm_context([first, second], append_only=True)
    assert after.startswith(before) and after.count(passage) == 1
    grouped = organize_source_captures([first, second])
    assert len(grouped) == 1 and grouped[0]["captures"] == [first, second]


def test_completed_research_tool_reaches_agent_with_complete_findings(monkeypatch):
    from services.agent_tools import _invoke_research_tool
    from services.zopedia_agent import _summarize_tool_result
    findings = {"slot_findings": {"arbitrary_topic": [{"finding": "complete retained observation " * 500}]}}
    artifact = {"artifact_id": "artifact", "subject": "company:EXAMPLE", "task": "research",
                "created_at_utc": "2026-10-13", "result": {"payload": findings,
                    "agent_result": {"query": "private execution detail"}}}
    monkeypatch.setattr(pipeline_store, "list_research_artifacts", lambda **kwargs: [artifact])
    result = _invoke_research_tool(service=None, tool_name="research.completed_investigations",
                                  arguments={"subjects": ["company:EXAMPLE"]})
    context = json.loads(_summarize_tool_result(result)["evidence_context_text"])
    assert len(context) == 1 and context[0]["findings"] == findings
    assert "private execution detail" not in json.dumps(context)


def test_failed_structured_stage_resumes_schema_repair_without_rebuying_research(store, monkeypatch):
    from services.aql_zopedia_engine import run_aql_zopedia_structured_agent
    monkeypatch.setattr(pipeline_store, "record_model_call", lambda **kwargs: True)
    model, calls = client([response(content='{"value":"1"}'),
                           response(content='{"value":"still invalid"}'), response(content='{"value":1}')])
    kwargs = dict(query="Retained observation: value=1", schema_name="retained_number",
                  schema={"type":"object","properties":{"value":{"type":"integer"}},"required":["value"]},
                  task="contract_repair", surface="ops.contract", max_tool_calls=0,
                  llm_client=model, reuse_scope="public_company_research", research_subject="contract:example")
    first = run_aql_zopedia_structured_agent(**kwargs)
    assert first["status"] == "failed" and first["partial_payload"] == {"value":"1"}
    second = run_aql_zopedia_structured_agent(**kwargs)
    assert second["status"] == "completed" and second["payload"] == {"value":1}
    assert len(calls) == 3
    assert 'Schema violations to correct:' in calls[-1]["json"]["messages"][1]["content"]
    assert run_aql_zopedia_structured_agent(**kwargs)["payload"] == second["payload"]
    assert len(calls) == 3


def test_dossier_normalization_preserves_text_and_arbitrary_research_topics():
    from services.aql.business_model_stack import _normalize_research_dossier_payload, _normalize_research_query_plan_payload
    text = "retained finding " * 200 + "last observation"
    payload = {"new_relationship": {"description": "retained annotation"}, "source_inventory": [], "slot_findings": {"new_research_topic": [
        {"finding": text, "status": "model-defined status", "confidence": "medium",
         "evidence_refs": ["source"], "missing_evidence": []}]},
        "source_gaps": [], "next_research_actions": [], "follow_up_questions": []}
    assert _normalize_research_dossier_payload(payload)["slot_findings"]["new_research_topic"][0]["finding"] == text
    assert _normalize_research_dossier_payload(payload)["new_relationship"] == payload["new_relationship"]
    plan = _normalize_research_query_plan_payload({"queries": [
        {"slot": "new_research_topic", "query": "A new research question", "question": "question"}]}, max_queries=24)
    assert plan[0]["slot"] == "new_research_topic"


def test_application_evidence_is_checkpointed_with_analysis_and_invalidates_reuse(store):
    model, _ = client([])
    calls = []
    @execution.checkpoint_structured_research
    def run(**kwargs):
        calls.append(kwargs)
        return {"status": "completed", "payload": {"findings": []}}
    kwargs = dict(query="Complete passages", schema={"type": "object"}, surface="research", task="analysis",
                  llm_client=model, max_tool_calls=0, reuse_scope="public_company_research",
                  research_subject="company:EXAMPLE", artifact_context={"source_inventory": [{"evidence_ref": "original"}]})
    first = run(**kwargs)
    replay = run(**kwargs)
    assert len(calls) == 1 and replay["artifact_context"] == first["artifact_context"]
    prior = execution.latest_research_artifact(access_scope="public_company_research", subject="company:EXAMPLE", task="analysis")
    assert prior["artifact_context"] == kwargs["artifact_context"]
    run(**{**kwargs, "artifact_context": {"source_inventory": [{"evidence_ref": "changed"}]}})
    assert len(calls) == 2


def test_source_annotations_preserve_owned_catalog_and_new_leads():
    from services.aql.business_model_stack import _normalize_research_dossier_payload
    catalog = [{"evidence_ref": "original", "url": "https://example.org/source", "captures": [{"published_at": "2026-10-13"}]}]
    result = _normalize_research_dossier_payload({"source_annotations": [
        {"evidence_ref": "original", "url": "model changed URL", "business_use": "useful connection"},
        {"evidence_ref": "new_lead", "business_use": "indirect relationship"}]}, source_catalog=catalog)
    sources = {source["evidence_ref"]: source for source in result["source_inventory"]}
    assert sources["original"]["url"] == catalog[0]["url"]
    assert sources["original"]["captures"] == catalog[0]["captures"]
    assert sources["original"]["business_use"] == "useful connection"
    assert sources["new_lead"]["business_use"] == "indirect relationship"


def test_provider_receipts_survive_database_outage_and_reconcile_once(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setenv("APP_TRACK", "development")
    pointers = {}; objects = {}; inserted = {}
    class Blob:
        def __init__(self, name): self.name = name
        def download_blob(self): return SimpleNamespace(readall=lambda: json.dumps(pointers[self.name]).encode())
        def delete_blob(self): pointers.pop(self.name)
    class Archive:
        def __init__(self, *, namespace, root):
            self.root = namespace + '/' + root
            self.container = SimpleNamespace(
                list_blobs=lambda **kwargs: [SimpleNamespace(name=name) for name in list(pointers) if name.startswith(kwargs['name_starts_with'])],
                get_blob_client=lambda name: Blob(name))
        def put(self, data, **kwargs): objects['object'] = data; return {'object': 'object'}
        def point(self, name, receipt): pointers[self.root+'/'+name] = receipt; return self.root+'/'+name
        def get(self, receipt): return objects[receipt['object']]
    monkeypatch.setattr(pipeline_store, "ImmutableBlobArchive", Archive)
    monkeypatch.setattr(pipeline_store, "_db_connect", lambda: None)
    event = {'attempt_id': 'attempt', 'root_run_id': 'research', 'settings_scope': 'development',
             'started_at_utc': datetime.now(timezone.utc), 'usage': {'prompt_tokens': 101, 'completion_tokens': 17}}
    assert pipeline_store.record_provider_attempt(event)
    assert len(pointers) == 1 and not inserted
    def restored(event, *, archive_on_failure=True):
        inserted.setdefault(event['attempt_id'], event)
        return True
    monkeypatch.setattr(pipeline_store, "record_provider_attempt", restored)
    assert pipeline_store.reconcile_provider_attempt_receipts(days=1)['reconciled'] == 1
    assert inserted['attempt']['usage'] == event['usage'] and not pointers
    assert pipeline_store.reconcile_provider_attempt_receipts(days=1)['reconciled'] == 0
    assert len(inserted) == 1


def test_completed_research_unavailability_is_not_an_empty_discovery_result(monkeypatch):
    monkeypatch.setattr(pipeline_store, "_db_connect", lambda: None)
    with pytest.raises(RuntimeError, match="unavailable"):
        pipeline_store.list_research_artifacts(namespace="research_artifact:development:public_company_research")


def test_metering_receipt_survives_both_store_failures_in_execution_log(store, monkeypatch, caplog):
    model, calls = client([response(usage={"prompt_tokens": 101, "completion_tokens": 17})])
    def unavailable(event): raise RuntimeError("stores unavailable")
    monkeypatch.setattr(pipeline_store, "record_provider_attempt", unavailable)
    assert generate(model)["value"] == 1 and len(calls) == 1
    receipt = json.loads(next(record.message.split('receipt=', 1)[1]
                              for record in caplog.records if 'receipt=' in record.message))
    assert receipt["usage"] == {"prompt_tokens": 101, "completion_tokens": 17}
    assert receipt["root_run_id"] == "root-job" and 'fake' not in json.dumps(receipt)


def test_tool_research_repairs_retained_draft_without_repeating_acquisition(store, monkeypatch):
    from services import aql_zopedia_engine as engine
    monkeypatch.setattr(pipeline_store, "record_model_call", lambda **kwargs: True)
    monkeypatch.delenv("CONTAINER_APP_JOB_EXECUTION_NAME", raising=False)
    acquisitions = []
    def investigate(**kwargs):
        acquisitions.append(kwargs)
        return {"status": "failed", "answer_markdown": '{"value":"1"}',
                "error": "Structured contract failure", "continuation_state": {"tool_calls": [{"tool_name":"source.read"}]}}
    monkeypatch.setattr(engine, "run_aql_zopedia_agent", investigate)
    model, calls = client([response(content='{"value":"invalid"}'),response(),response()])
    kwargs = dict(query="Supplied event", schema_name="event",schema={"type":"object","properties":{"value":{"type":"integer"}},"required":["value"]},
                  task="event", surface="research.event", max_tool_calls=2, llm_client=model,
                  reuse_scope="public_company_research", research_subject="event:example")
    assert engine.run_aql_zopedia_structured_agent(**kwargs)["status"] == "failed"
    assert engine.run_aql_zopedia_structured_agent(**kwargs)["payload"] == {"value":1}
    assert len(acquisitions) == 1 and len(calls) == 2
    assert engine.run_aql_zopedia_structured_agent(**kwargs)["payload"] == {"value":1}
    assert len(calls) == 2
    monkeypatch.setenv("PIPELINE_RUN_ID", "next-scheduled-investigation")
    assert engine.run_aql_zopedia_structured_agent(**kwargs)["payload"] == {"value":1}
    assert len(acquisitions) == 2 and len(calls) == 3


def test_interrupted_acquisition_resumes_its_tool_state_within_execution(store, monkeypatch):
    from services import aql_zopedia_engine as engine
    monkeypatch.delenv("CONTAINER_APP_JOB_EXECUTION_NAME", raising=False)
    state = {"tool_calls": [{"tool_name":"source.read","result":{"complete_passage":"retained observation"}}]}
    calls = []
    def investigate(**kwargs):
        calls.append(kwargs)
        if len(calls)==1:return {"status":"failed","answer_markdown":"","continuation_state":state,"error":"transport timeout"}
        assert kwargs["resume_state"] == state
        return {"status":"completed","answer_markdown":'{"value":1}'}
    monkeypatch.setattr(engine, "run_aql_zopedia_agent", investigate)
    model, _ = client([])
    kwargs = dict(query="Event", schema_name="event",schema={"type":"object"}, task="event",surface="research.event",max_tool_calls=2,
                  llm_client=model,reuse_scope="public_company_research",research_subject="event:example")
    assert engine.run_aql_zopedia_structured_agent(**kwargs)["status"] == "failed"
    assert engine.run_aql_zopedia_structured_agent(**kwargs)["payload"] == {"value":1}
    assert len(calls)==2


def test_aql_writer_receives_complete_prior_analysis(monkeypatch):
    from services.aql import writer
    from services import aql_zopedia_engine
    prior = {'artifact_id':'existing', 'created_at_utc':'2026-10-13',
             'payload':{'arbitrary_relationship':'complete prior finding ' * 1000},
             'artifact_context':{'source_inventory':[{'evidence_ref':'source'}]}}
    captured = []
    monkeypatch.setattr(writer, 'latest_research_artifact', lambda **kwargs: prior)
    def generate(**kwargs):
        captured.append(kwargs)
        return {'status':'completed', 'payload': {'queries':[], 'hypotheses':[{'text':'retained hypothesis'}]}}
    monkeypatch.setattr(aql_zopedia_engine, 'run_aql_zopedia_structured_agent', generate)
    candidate={'symbol':'EXAMPLE','company_name':'Example'}
    writer._write_symbol_bundle(candidate, [], [], llm_client=object(), cause_status='hypothesis')
    assert len(captured) == 1
    for call in captured:
        evidence=json.loads(call['query'].split('Evidence JSON:\n',1)[1].split('\n\nRequest JSON:',1)[0])
        assert evidence['completed_research']==prior


def test_event_replay_identity_excludes_its_own_new_result(monkeypatch):
    import hashlib
    import pandas as pd
    from services.aql import events
    from services.json_utils import to_jsonable
    url='https://example.invalid/event'
    source='doc::event-news::'+hashlib.sha256(url.encode()).hexdigest()[:20]
    plan={'title':'Event','question':'Question','source_ids':[source], 'related_symbols':[], 'followup_questions':[], 'rationale':'Research'}
    version=[1]; captured=[]
    monkeypatch.setattr(events, 'latest_research_artifact', lambda **kwargs: {'artifact_id':str(version[0]),'payload':{'observation':version[0]}})
    def run(**kwargs):
        if kwargs['schema_name']=='source_event_nominations':return {'status':'completed','payload':{'events':[plan]}}
        captured.append(kwargs)
        raise RuntimeError('Stop at model boundary')
    monkeypatch.setattr(events,'run_aql_zopedia_structured_agent',run)
    kwargs=dict(market_frame=pd.DataFrame(),universe_frame=None,asof_time_utc='2026-10-13T12:00:00Z',run_id='execution',llm_client=object())
    news=pd.DataFrame([{'headline':'Event','url':url,'summary':'Complete report','published_at':'2026-10-13T11:00:00Z'}])
    events.research_source_events(news,**kwargs);version[0]=2;events.research_source_events(news,**kwargs)
    assert len(captured)==2 and captured[0]['query']!=captured[1]['query']
    assert captured[0]['reuse_identity']==captured[1]['reuse_identity']


def test_cost_estimate_counts_reasoning_inside_completion_once():
    from datetime import datetime, timezone
    usage={"prompt_tokens": 1000000, "prompt_cache_hit_tokens": 500000,
           "completion_tokens": 1000000, "completion_tokens_details": {"reasoning_tokens": 800000}}
    cost=execution.estimated_charge(provider="deepseek",model="deepseek-flash",usage=usage,
                                   started_at=datetime(2026,10,12,14,tzinfo=timezone.utc))
    assert cost["usd_min"] == pytest.approx(.6765)
    assert cost["usd_min"] == cost["usd_max"]
    assert execution.estimated_charge(provider="deepseek",model="unknown",usage=usage,
        started_at=datetime.now(timezone.utc))["status"] == "price_unavailable"


def test_research_replay_is_opt_in_versioned_and_environment_owned(store, monkeypatch):
    calls = []
    model, _ = client([])
    @execution.checkpoint_structured_research
    def run(**kwargs):
        calls.append(kwargs)
        return {"status": "completed", "payload": {"value": len(calls)}}
    kwargs = dict(query="Evidence", schema={"type": "object", "required": ["value"]},
                  surface="research", task="dossier", llm_client=model, max_tool_calls=0,
                  reuse_scope="public_company_research", research_subject="company:EXAMPLE")
    first = run(**kwargs); second = run(**kwargs)
    assert len(calls) == 1 and first["payload"] == second["payload"]
    assert second["artifact_reuse"]["artifact_id"]
    run(**{**kwargs, "query": "Changed evidence"})
    run(**{**kwargs, "force_refresh": True})
    assert run(**kwargs)["payload"]["value"] == 3
    monkeypatch.setenv("APP_TRACK", "production")
    run(**kwargs)
    run(**{**kwargs, "reuse_scope": ""})
    run(**{**kwargs, "max_tool_calls": 2})
    assert len(calls) == 6


def test_latest_findings_do_not_recursively_embed_agent_traces(store):
    model, _ = client([])
    namespace, key = execution.research_artifact_key(access_scope="public_company_research",
        subject="company:EXAMPLE", query="Evidence", schema={}, config=model.config)
    execution.retain_research_artifact(namespace, key, subject="company:EXAMPLE", task="dossier",
        result={"payload": {"finding": "observation"}, "agent_result": {"query": "previous large prompt"}})
    prior = execution.latest_research_artifact(access_scope="public_company_research", subject="company:EXAMPLE", task="dossier")
    assert prior["payload"]["finding"] == "observation"
    assert "previous large prompt" not in json.dumps(prior)


def test_deferred_review_cannot_authorize_retention():
    from services.zopedia_agent import _execute_zopedia_agent_loop
    with pytest.raises(ValueError, match="cannot authorize"):
        _execute_zopedia_agent_loop(run_id="run", query="question", persist_findings=True,
                                    answer_review_owner="downstream")


def test_graph_vectors_follow_content_and_embedding_space(store, monkeypatch):
    from services import knowledge_graph as graph
    graph._NODE_VECTOR_CACHE.clear()
    vectors = {}; calls = []
    config = llm.LLMConfig(provider="openai", api_key="fake", model="chat", base_url="https://example.invalid")
    def embed(texts):
        calls.append(texts)
        return [[1.0, 0.0] for _ in texts]
    monkeypatch.setattr(graph, "load_embedding_client", lambda: SimpleNamespace(config=config, generate_embeddings=embed))
    monkeypatch.setattr(pipeline_store, "read_execution_states", lambda ns, keys: {key: vectors[(ns,key)] for key in keys if (ns,key) in vectors})
    monkeypatch.setattr(pipeline_store, "write_execution_states", lambda ns, data: vectors.update({(ns,k):v for k,v in data.items()}) or True)
    nodes={"a": {"canonical_label": "A"}, "b": {"canonical_label": "B"}}
    assert len(graph._get_node_vectors(nodes)) == 2
    graph._NODE_VECTOR_CACHE.clear()
    graph._get_node_vectors(nodes)
    assert len(calls) == 1
    nodes["a"]["description"] = "Updated description"
    graph._get_node_vectors(nodes)
    assert len(calls[-1]) == 1
    config = replace(config, embedding_model="another-embedding-model")
    graph._get_node_vectors(nodes)
    assert len(calls[-1]) == 2


def test_audio_reuses_text_voice_model_and_format(store, monkeypatch):
    from services.elevenlabs_tts import ElevenLabsTTSClient, ElevenLabsTTSConfig
    objects = {}; calls = []
    class Archive:
        def __init__(self, **kwargs): pass
        def put(self, payload, **kwargs):
            objects[str(len(objects))] = payload
            return {"id": str(len(objects)-1)}
        def get(self, receipt): return objects[receipt["id"]]
    monkeypatch.setattr(pipeline_store, "ImmutableBlobArchive", Archive)
    def post(**kwargs):
        calls.append(kwargs)
        r = requests.Response();r.status_code=200;r._content=b"audio-bytes"
        return r
    c=ElevenLabsTTSClient(ElevenLabsTTSConfig(api_key="fake",voice_id="voice",model_id="model",output_format="mp3"),
                         session=SimpleNamespace(post=post))
    assert c.synthesize("Same text") == c.synthesize("Same text") == b"audio-bytes"
    assert len(calls) == 1 and store[1][0]["operation"] == "audio"


@pytest.mark.parametrize("workload", ["interactive", "news"])
def test_leaf_reuse_tracks_its_producer_not_unrelated_flow_settings(store, monkeypatch, workload):
    from copy import deepcopy
    from services import llm_flow
    model, _ = client([])
    snapshot = llm_flow.capture_research_configuration(workload=workload)
    calls = []
    @execution.checkpoint_structured_research
    def run(**kwargs):
        calls.append(kwargs)
        return {"status": "completed", "payload": {"value": 1}}
    kwargs = dict(query="Original observation", schema={"type": "object"},
                  surface="research", task="supplied", llm_client=model, max_tool_calls=0,
                  reuse_scope="public_company_research", research_subject="company:EXAMPLE")
    with llm_flow.research_configuration(snapshot):
        run(**kwargs)
    unrelated = deepcopy(snapshot)
    unrelated["flow"]["routes"]["monitor"]["thinking"] = "max"
    unrelated["flow"]["reviewers"].append({**unrelated["flow"]["reviewers"][0], "id": "another"})
    with llm_flow.research_configuration(unrelated):
        assert run(**kwargs)["artifact_reuse"]
    assert len(calls) == 1
    changed = deepcopy(snapshot)
    routes = changed["flow"]["workloads"]["news"] if workload == "news" else changed["flow"]["routes"]
    routes["synthesis"]["thinking"] = "low"
    with llm_flow.research_configuration(changed):
        run(**kwargs)
    with llm_flow.research_configuration(snapshot):
        run(**{**kwargs, "instructions": "A changed producer instruction"})
    assert len(calls) == 3
    if workload == "news":
        policy = deepcopy(snapshot)
        policy["overrides"][llm_flow.DEEP_REASONING_PROMPT] = "Changed shared reasoning policy."
        with llm_flow.research_configuration(policy):
            run(**kwargs)
        assert len(calls) == 4


def test_resolved_retrieval_executes_once_without_a_second_planner(store, monkeypatch):
    import pandas as pd
    from services import zopedia_research, saa
    calls = []
    def unexpected_planner(**kwargs):
        raise AssertionError('Resolved queries must not load another planner')
    def search(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame()
    monkeypatch.setattr(zopedia_research, 'load_aql_zopedia_llm_client', unexpected_planner)
    monkeypatch.setattr(saa, 'search_retained_evidence_chunks', search)
    result = zopedia_research.search_evidence(
        query='Research objective', start_date='2026-10-01',
        resolved_plan={'queries': ['first resolved query', 'second resolved query'],
                       'reporting_start': None, 'reporting_end': None})
    assert {row['query'] for row in calls} == {'first resolved query', 'second resolved query'}
    assert len(calls) == 2 and all(row['use_semantic'] for row in calls)
    assert result['reporting_window'] == {'start': None, 'end': None}


def test_concurrent_identical_supplied_work_has_one_producer(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from services import llm_flow
    snapshot = llm_flow.capture_research_configuration(workload='interactive')
    model, _ = client([])
    entered, blocked, finish = Event(), Event(), Event()
    calls = []
    claim = pipeline_store.claim_execution_state
    def observed_claim(*args, **kwargs):
        result = claim(*args, **kwargs)
        if not result: blocked.set()
        return result
    monkeypatch.setattr(pipeline_store, 'claim_execution_state', observed_claim)
    @execution.checkpoint_structured_research
    def run(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert finish.wait(5)
        return {'status': 'completed', 'payload': {'value': 1}}
    def invoke():
        with llm_flow.research_configuration(snapshot):
            return run(query='Identical original inputs', schema={'type': 'object'},
                       surface='research', task='supplied', llm_client=model, max_tool_calls=0,
                       reuse_scope='public_company_research', research_subject='company:EXAMPLE')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke)
        assert entered.wait(5)
        second = pool.submit(invoke)
        try:
            assert blocked.wait(5)
        finally:
            finish.set()
        assert first.result()['payload'] == second.result()['payload']
    assert len(calls) == 1


def test_final_handoff_uses_reserved_time_without_repeating_a_failed_analysis(store, monkeypatch):
    from services import zopedia_agent, llm_flow
    from services.agents.research_session import ResearchBudget
    calls = []
    budget = ResearchBudget.start(30, 90)
    def generate(**kwargs):
        calls.append(kwargs)
        if kwargs['purpose'] == 'agent.research_step':
            return {'action': 'final', 'answer_markdown': 'Retained draft', 'research_state': {}}
        if kwargs['purpose'] == 'agent.analysis_step':
            raise llm.LLMDeadlineExceeded('Deliberate model deadline failure')
        raise AssertionError(kwargs['purpose'])
    monkeypatch.setattr(zopedia_agent, 'generate_json_via_aql_zopedia_gateway', generate)
    model, _ = client([])
    with llm_flow.research_configuration(llm_flow.capture_research_configuration(workload='interactive')):
        result = zopedia_agent._execute_zopedia_agent_loop(
            run_id='handoff', query='Supplied task', llm_client=model, service=SimpleNamespace(list_capabilities=lambda: {}),
            max_tool_calls=1, persist_findings=False, write_policy='none',
            answer_review_owner='downstream', run_budget=budget)
    assert [row['purpose'] for row in calls] == ['agent.research_step', 'agent.analysis_step']
    assert result['answer_markdown'] == 'Retained draft'
    assert result['limitations']


@pytest.mark.parametrize('structured', [False, True])
@pytest.mark.parametrize('evidence', [{'sources': [{'title': 'Original'}]}, ['invalid record'], 'source text'])
def test_shared_engine_rejects_malformed_evidence_before_inference(store, monkeypatch, structured, evidence):
    from services import aql_zopedia_engine as engine
    def unexpected_inference(**kwargs):
        raise AssertionError('Malformed evidence must fail before loading inference')
    monkeypatch.setattr(engine, 'load_aql_zopedia_llm_client', unexpected_inference)
    kwargs = dict(query='Supplied task', initial_evidence=evidence, max_tool_calls=0,
                  persist_findings=False, write_policy='none', surface='review', task='contract')
    if structured:
        kwargs.update(schema={'type': 'object'}, schema_name='contract')
    with pytest.raises(ValueError, match='initial_evidence must be a list'):
        (engine.run_aql_zopedia_structured_agent if structured else engine.run_aql_zopedia_agent)(**kwargs)


@pytest.mark.parametrize('handoff,stage', [('final', 'synthesis'), ('analyze', 'reasoning')])
def test_analysis_model_keeps_ownership_across_computation(store, monkeypatch, handoff, stage):
    from services import zopedia_agent, llm_flow
    calls, tool_calls = [], []
    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {'action': handoff, 'answer_markdown': 'Routine proposed draft', 'research_state': {}}
        if len(calls) == 2:
            assert 'analysis.run_python' in kwargs['user_prompt']
            return {'action': 'tool_call', 'tool_name': 'analysis.run_python',
                    'tool_arguments': json.dumps({'objective': 'Compute supplied values', 'code': 'result = {"value": 2}'}),
                    'answer_markdown': '', 'research_state': {}}
        assert len(calls) == 3
        assert kwargs['schema']['properties']['action']['enum'] == ['final']
        return {'action': 'final', 'answer_markdown': 'Completed analytical answer', 'research_state': {}}
    def compute(**kwargs):
        tool_calls.append(kwargs)
        return {'result_type': 'analysis_result', 'payload': {'status': 'succeeded', 'result': {'value': 2}}}
    monkeypatch.setattr(zopedia_agent, 'generate_json_via_aql_zopedia_gateway', generate)
    monkeypatch.setattr(zopedia_agent, '_invoke_tool_with_heartbeat', compute)
    model, _ = client([])
    with llm_flow.research_configuration(llm_flow.capture_research_configuration(workload='interactive')):
        result = zopedia_agent._execute_zopedia_agent_loop(
            run_id='ownership', query='Supplied task', llm_client=model,
            service=SimpleNamespace(list_capabilities=lambda: {}), max_tool_calls=1,
            persist_findings=False, write_policy='none', answer_review_owner='downstream')
    assert [c['stage'] for c in calls] == ['researcher', stage, stage]
    assert len(tool_calls) == 1
    assert result['answer_markdown'] == 'Completed analytical answer'
    assert result['continuation_state']['active_analysis_stage'] == ''


def test_accepted_review_does_not_reopen_research_for_optional_followup(store, monkeypatch):
    from services import zopedia_agent, llm_flow
    configuration = llm_flow.default_flow_configuration()
    template = configuration['reviewers'][0]
    configuration['reviewers'] = [{**template, 'id': verdict, 'focus': verdict} for verdict in ('accept', 'revise')]
    monkeypatch.setattr(zopedia_agent, 'flow_configuration', lambda: configuration)
    monkeypatch.setattr(zopedia_agent, '_run_answer_judge', lambda **kwargs: {
        'status': 'completed', 'verdict': kwargs['reviewer_focus'], 'research_requests': [kwargs['reviewer_focus']]})
    result = zopedia_agent._run_review_cycle(draft_answer='Draft', tool_calls=[])
    assert result['research_requests'] == ['revise']
    assert result['reviews'][0]['research_requests'] == ['accept']


def test_review_derives_correction_from_checked_claim(store, monkeypatch):
    from services import zopedia_agent
    check = {'claim': 'Draft claim', 'basis': 'Original premise',
             'verification': 'Independent check', 'correction': 'Necessary edit'}
    monkeypatch.setattr(zopedia_agent, 'generate_json_via_aql_zopedia_gateway', lambda **kwargs: {
        'checks': [check], 'critique_summary': 'Summary', 'confidence': 'high', 'research_requests': []})
    model, _ = client([])
    result = zopedia_agent._run_answer_judge(
        llm=model, run_id='atomic-review', query='Question', tool_calls=[], draft_answer='Draft',
        draft_confidence='high', limitations=[], conversation_history=[], prefetched_context='',
        timeout_seconds=10, progress_callback=None)
    assert result['verdict'] == 'revise'
    assert result['corrections'] == [{'claim': check['claim'], 'basis': check['basis'],
                                      'counterexample': check['verification'], 'correction': check['correction']}]


def test_pending_correction_resumes_without_rebuying_research_or_review(store, monkeypatch):
    from services import zopedia_agent, llm_flow
    calls = []
    review = {'status': 'completed', 'reviews': [{'status': 'completed', 'verdict': 'revise',
              'corrections': [{'claim': 'Draft', 'correction': 'Edit', 'basis': 'Premise'}]}],
              'research_requests': [], 'corrections_applied': False, 'correction_status': 'pending'}
    state = {'run_id': 'resume-correction', 'draft_answer': 'Retained draft',
             'review_history': [review], 'synthesis_evidence_count': 0}
    def generate(**kwargs):
        calls.append(kwargs)
        assert kwargs['purpose'] == 'agent.review_corrections'
        return {'answer_markdown': 'Completed correction', 'analysis_basis': []}
    def unexpected_review(**kwargs):
        raise AssertionError('Completed review must not run again')
    monkeypatch.setattr(zopedia_agent, 'generate_json_via_aql_zopedia_gateway', generate)
    monkeypatch.setattr(zopedia_agent, '_run_review_cycle', unexpected_review)
    model, _ = client([])
    with llm_flow.research_configuration(llm_flow.capture_research_configuration(workload='interactive')):
        result = zopedia_agent._execute_zopedia_agent_loop(
            run_id=state['run_id'], query='Question', llm_client=model,
            service=SimpleNamespace(list_capabilities=lambda: {}), max_tool_calls=1,
            persist_findings=False, write_policy='none', resume_state=state)
    assert len(calls) == 1
    assert result['answer_markdown'] == 'Completed correction'
    assert result['quality_review']['correction_status'] == 'completed'
    assert result['completed_review_cycles'] == 1
    assert result['continuation_state']['active_analysis_stage'] == ''


def test_invalid_structured_analysis_does_not_replace_retained_valid_draft(store, monkeypatch):
    from services import zopedia_agent, llm_flow
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return {'action': 'final', 'answer_markdown': '{"value":1}' if len(calls) == 1 else '{"value":"invalid"}',
                'research_state': {}}
    monkeypatch.setattr(zopedia_agent, 'generate_json_via_aql_zopedia_gateway', generate)
    model, _ = client([])
    with llm_flow.research_configuration(llm_flow.capture_research_configuration(workload='interactive')):
        result = zopedia_agent._execute_zopedia_agent_loop(
            run_id='invalid-analysis', query='Question', llm_client=model,
            service=SimpleNamespace(list_capabilities=lambda: {}), max_tool_calls=1,
            persist_findings=False, write_policy='none', answer_review_owner='downstream',
            response_schema={'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']})
    assert result['answer_markdown'] == '{"value":1}'
    assert result['continuation_state']['partial_answer'] == '{"value":"invalid"}'
    assert result['continuation_state']['active_analysis_stage'] == 'synthesis'


def test_computation_originals_retain_values_behind_summary_view(store):
    from services import zopedia_agent
    summary = zopedia_agent._summarize_tool_result({
        'result_type': 'analysis_result', 'payload': {
            'analysis_run_id': 'computation', 'status': 'succeeded',
            'llm_context_text': 'Stdout: 1 line.', 'stdout': 'computed_value=21',
            'result': {'nested': {'value': 21}}, 'metrics': [],
        }})
    assert 'computed_value=21' not in summary['llm_context_text']
    assert json.loads(summary['evidence_context_text'])['result'] == {'nested': {'value': 21}}
    call = {'tool_call_id': 'compute', 'tool_name': 'analysis.run_python', 'status': 'completed',
            'arguments': {}, 'result_summary': summary}
    assert 'computed_value=21' in zopedia_agent._tool_history_prompt([call], full_evidence=True)


def test_review_is_checkpointed_before_starting_paid_correction(store, monkeypatch):
    from services import zopedia_agent, llm_flow
    checkpoints = []
    def progress(event):
        if event.get('_checkpoint'):
            checkpoints.append(json.loads(json.dumps(event['_checkpoint'])))
    def generate(**kwargs):
        if kwargs['purpose'] == 'agent.review_corrections':
            assert checkpoints[-1]['review_history'][-1]['correction_status'] == 'pending'
            assert checkpoints[-1]['review_cycles_used'] == 1
            return {'answer_markdown': 'Corrected draft', 'analysis_basis': []}
        return {'action': 'final', 'answer_markdown': 'Draft', 'analysis_basis': []}
    monkeypatch.setattr(zopedia_agent, 'generate_json_via_aql_zopedia_gateway', generate)
    monkeypatch.setattr(zopedia_agent, '_run_review_cycle', lambda **kwargs: {
        'status': 'completed', 'reviews': [{'status': 'completed', 'verdict': 'revise', 'corrections': []}],
        'research_requests': [], 'corrections_applied': False})
    model, _ = client([])
    with llm_flow.research_configuration(llm_flow.capture_research_configuration(workload='interactive')):
        result = zopedia_agent._execute_zopedia_agent_loop(
            run_id='checkpoint-review', query='Question', llm_client=model,
            service=SimpleNamespace(list_capabilities=lambda: {}), max_tool_calls=1,
            persist_findings=False, write_policy='none', progress_callback=progress)
    assert result['quality_review']['correction_status'] == 'completed'


def test_analysis_finishes_review_followup_without_an_extra_review_cycle(store, monkeypatch):
    from services import zopedia_agent, llm_flow
    calls, reviews = [], []
    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 3:
            return {'action': 'analyze', 'research_state': {'next_actions': ['Reassess original source']}}
        return {'action': 'final', 'answer_markdown': 'Updated analysis' if len(calls) == 4 else 'Draft',
                'analysis_basis': [], 'review_requests_addressed': ['Reassess original source'] if len(calls) == 4 else []}
    def review(**kwargs):
        reviews.append(kwargs)
        return {'status': 'completed', 'reviews': [{'status': 'completed', 'verdict': 'revise', 'corrections': []}],
                'research_requests': ['Reassess original source'], 'corrections_applied': False}
    monkeypatch.setattr(zopedia_agent, 'generate_json_via_aql_zopedia_gateway', generate)
    monkeypatch.setattr(zopedia_agent, '_run_review_cycle', review)
    model, _ = client([])
    with llm_flow.research_configuration(llm_flow.capture_research_configuration(workload='interactive')):
        result = zopedia_agent._execute_zopedia_agent_loop(
            run_id='analysis-followup', query='Question', llm_client=model,
            service=SimpleNamespace(list_capabilities=lambda: {}), max_tool_calls=2,
            persist_findings=False, write_policy='none')
    assert len(calls) == 4 and len(reviews) == 1
    assert result['completed_review_cycles'] == 1
    assert result['quality_review']['pending_research_requests'] == []
