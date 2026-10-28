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
    from services import inference_policy
    # These cases own a supplied fake transport. Integrated task/model routing
    # is exercised separately at the real gateway boundary.
    monkeypatch.setattr(inference_policy, "client_for_task", lambda client, **kwargs:
        (client, {"role": "best_effort" if kwargs.get("best_effort") else "full"}))
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


def test_tokenized_embeddings_reach_transport_and_keep_usage_accounting(store):
    calls = []

    def post(**kwargs):
        calls.append(kwargs)
        result = requests.Response()
        result.status_code = 200
        result._content = json.dumps({
            "model": "text-embedding-3-small",
            "data": [
                {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                {"index": 0, "embedding": [1.0, 0.0, 0.0]},
            ],
            "usage": {"prompt_tokens": 12, "total_tokens": 12},
        }).encode()
        return result

    config = llm.LLMConfig(
        provider="azure_openai", api_key="fake", model="text-embedding-3-small",
        base_url="https://embedding.example.invalid/openai/v1",
        embedding_model="text-embedding-3-small", embedding_deployment="evidence",
        embedding_dimensions=3,
    )
    model = llm.AzureOpenAIEmbeddingClient(config, session=SimpleNamespace(post=post))
    vectors = model.generate_embeddings(["Retained source about electricity.", "An unrelated source."])

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert len(calls) == 1
    assert all(isinstance(token, int) for text in calls[0]["json"]["input"] for token in text)
    events = store[1]
    assert [event["status"] for event in events] == ["started", "success"]
    assert all(event["operation"] == "embedding" for event in events)
    assert events[-1]["usage"]["prompt_tokens"] == 12
    composition = events[-1]["metadata"]["request_composition"]
    assert composition["message_count"] == 0
    assert composition["serialized_characters"] > 0
    assert "Retained source" not in json.dumps(events, default=str)


@pytest.mark.parametrize("provider,model_type", [
    ("deepseek", llm.DeepSeekChatJSONClient),
    ("openai", llm.OpenAIChatJSONClient),
    ("azure_openai", llm.AzureOpenAIChatJSONClient),
])
def test_json_recovery_keeps_original_request(store, provider, model_type):
    transport, calls = client([response(content='{"value":'), response()])
    model = model_type(replace(transport.config, provider=provider, deployment="fake"), session=transport.session)
    schema = {"type": "object", "properties": {"value": {"type": "integer"}},
              "required": ["value"], "additionalProperties": False}
    assert model.generate_json(system_prompt="Return supplied values.", user_prompt="Evidence: value=1",
                               schema_name="transport", schema=schema)["value"] == 1
    assert len(calls) == 2
    assert json.dumps(calls[0]["json"]["messages"], ensure_ascii=False) in calls[1]["json"]["messages"][1]["content"]
    assert '{"value":' in calls[1]["json"]["messages"][1]["content"]


@pytest.mark.parametrize("provider,model_type", [
    ("deepseek", llm.DeepSeekChatJSONClient),
    ("openai", llm.OpenAIChatJSONClient),
    ("azure_openai", llm.AzureOpenAIChatJSONClient),
])
def test_valid_schema_needs_one_provider_call(store, provider, model_type):
    transport, calls = client([response()])
    model = model_type(replace(transport.config, provider=provider, deployment="fake"), session=transport.session)
    assert generate(model)["value"] == 1
    assert len(calls) == 1


def test_incomplete_json_repair_remains_a_failure(store):
    model, calls = client([response(content='{"value":'), response(content='{broken')])
    schema = {"type": "object", "required": ["value"]}
    with pytest.raises(llm.LLMJSONParseError) as error:
        model.generate_json(system_prompt="Return supplied values.", user_prompt="Evidence: value=1",
                            schema_name="transport", schema=schema)
    assert error.value.raw_content == '{broken'
    assert len(calls) == 2


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
        def download_blob(self, **kwargs): return SimpleNamespace(readall=lambda: json.dumps(pointers[self.name]).encode())
        def delete_blob(self, **kwargs): pointers.pop(self.name)
    class Archive:
        def __init__(self, *, namespace, root):
            self.root = namespace + '/' + root
            self.container = SimpleNamespace(
                walk_blobs=lambda **kwargs: [SimpleNamespace(name=name) for name in list(pointers) if name.startswith(kwargs['name_starts_with'])],
                get_blob_client=lambda name: Blob(name))
        def put(self, data, **kwargs): objects['object'] = data; return {'object': 'object'}
        def point(self, name, receipt): pointers[self.root+'/'+name] = receipt; return self.root+'/'+name
        def get(self, receipt): return objects[receipt['object']]
    monkeypatch.setattr(pipeline_store, "ImmutableBlobArchive", Archive)
    monkeypatch.setattr(pipeline_store, 'open_database_connection', lambda: None)
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
    monkeypatch.setattr(pipeline_store, 'open_database_connection', lambda: None)
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


def test_complete_paid_response_survives_child_cancellation_and_is_adopted_without_repurchase(store):
    from services.work_scope import WorkScope, WorkCancelled
    import time
    work = WorkScope(time.monotonic() + 5)
    model, calls = client([response(usage={"prompt_tokens": 101, "completion_tokens": 17})])
    post = model.session.post
    def complete_then_cancel(**kwargs):
        result = post(**kwargs)
        work.cancel()
        return result
    model.session.post = complete_then_cancel
    with pytest.raises(WorkCancelled):
        with work.activate():
            generate(model)
    model.session.post = lambda **kwargs: pytest.fail("Retained complete output was repurchased")
    assert generate(model)["value"] == 1
    assert len(calls) == 1
    assert len([event for event in store[1] if event["status"] == "success"]) == 1


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
