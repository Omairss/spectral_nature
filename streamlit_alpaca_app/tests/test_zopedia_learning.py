from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from data_access.query_service import QueryService
from services import aql_zopedia_engine, llm, zopedia_learning


def _thread():
    return {"thread_id": "thread", "user_key": "owner", "messages": [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Answer"},
    ]}


def test_episode_identity_tracks_owner_messages_and_original_execution():
    thread = _thread()
    original = deepcopy(thread)
    episode = zopedia_learning.detect_learning_events_from_payload(thread)[0]
    assert thread == original
    assert episode == zopedia_learning.detect_learning_events_from_payload(thread)[0]
    assert episode["event_id"] != zopedia_learning.detect_learning_events_from_payload(thread, user_key="another-owner")[0]["event_id"]
    thread["messages"].append({"role": "user", "content": "Correction"})
    assert episode["event_id"] != zopedia_learning.detect_learning_events_from_payload(thread)[0]["event_id"]
    assert episode["event_id"] != zopedia_learning.detect_learning_events_from_payload(original, run_payloads=[{"run_id": "attempt", "status": "failed"}])[0]["event_id"]
    assert zopedia_learning.detect_learning_events_from_payload({"messages": [{"role": "user", "content": "Question"}]}) == []


def _critic_dependencies(monkeypatch, operation):
    monkeypatch.setattr(QueryService, "from_environment", classmethod(lambda cls: cls(data_access=None)))
    monkeypatch.setattr(aql_zopedia_engine, "research_engine_revision", lambda: "execution-contract")
    monkeypatch.setattr(aql_zopedia_engine, "run_aql_zopedia_structured_agent", operation)
    monkeypatch.setattr(llm, "get_active_narrative_style_rule", lambda: "frozen-style")
    return SimpleNamespace(config=SimpleNamespace(provider="transport", model="model"))


def test_completed_critique_reuses_exact_inputs_and_invalidates_changed_evidence(monkeypatch):
    calls = []
    payload = {"proposed_change_type": "no_change", "root_cause": ""}
    def operation(**kwargs):
        calls.append(kwargs)
        return {"status": "completed", "payload": payload}
    client = _critic_dependencies(monkeypatch, operation)
    thread = _thread()
    event = zopedia_learning.detect_learning_events_from_payload(thread)[0]
    pending = []
    result = zopedia_learning.critique_learning_event(event, thread, [], client, on_pending=lambda: pending.append(True))
    assert result["_execution"]["status"] == "completed"
    assert result["root_cause"] == payload["root_cause"]
    repeated = zopedia_learning.critique_learning_event(event, thread, [], client, previous=result, on_pending=lambda: pending.append(True))
    assert repeated["_reused"] is True
    assert len(calls) == len(pending) == 1
    thread["messages"].append({"role": "user", "content": "Additional evidence"})
    changed_event = zopedia_learning.detect_learning_events_from_payload(thread)[0]
    zopedia_learning.critique_learning_event(changed_event, thread, [], client, previous=result)
    assert len(calls) == 2
    assert calls[-1]["write_policy"] == "none"
    assert calls[-1]["request_context"]["allowed_source_access"] == ["supplied"]


def test_transport_failure_remains_pending_and_is_retried(monkeypatch):
    calls = []
    def operation(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError("transport timeout")
        return {"status": "completed", "payload": {"proposed_change_type": "no_change"}}
    client = _critic_dependencies(monkeypatch, operation)
    thread = _thread()
    event = zopedia_learning.detect_learning_events_from_payload(thread)[0]
    pending = []
    failed = zopedia_learning.critique_learning_event(event, thread, [], client, on_pending=lambda: pending.append(True))
    assert failed["_execution"]["status"] == "pending"
    assert "error" in failed["_execution"]
    assert "proposed_change_type" not in failed
    recovered = zopedia_learning.critique_learning_event(event, thread, [], client, previous=failed, on_pending=lambda: pending.append(True))
    assert recovered["_execution"]["status"] == "completed"
    assert len(calls) == len(pending) == 2


def test_learning_job_keeps_other_episodes_after_one_critic_failure(monkeypatch):
    from contextlib import contextmanager

    class Connection:
        info = SimpleNamespace(transaction_status=0)

        @contextmanager
        def cursor(self):
            yield SimpleNamespace(execute=lambda *args: None, fetchone=lambda: None)

        def commit(self):
            pass

    monkeypatch.setattr(zopedia_learning, "bootstrap_zopedia_learning_storage", lambda conn: None)
    monkeypatch.setattr(zopedia_learning, "load_aql_zopedia_llm_client", lambda **kwargs: object())
    monkeypatch.setattr(zopedia_learning, "_recent_threads", lambda conn, limit: [
        {"thread_id": "first", "user_key": "owner"}, {"thread_id": "second", "user_key": "owner"},
    ])
    monkeypatch.setattr(zopedia_learning, "load_chat_thread", lambda **kwargs: {**_thread(), "thread_id": kwargs["thread_id"]})
    monkeypatch.setattr(zopedia_learning, "_load_run_payloads_for_thread", lambda thread: [])
    stored = []
    monkeypatch.setattr(zopedia_learning, "_persist_learning_event", lambda conn, event, **kwargs: stored.append(deepcopy(event)) or dict(event))
    updates = []
    monkeypatch.setattr(zopedia_learning, "_merge_learning_event_metadata", lambda *args: None)
    monkeypatch.setattr(zopedia_learning, "_update_learning_event_fields", lambda conn, event_id, **fields: updates.append(fields))
    monkeypatch.setenv("ZOPEDIA_LEARNING_VERIFY_EVALS", "0")

    def critic(event, *args, on_pending, **kwargs):
        on_pending()
        if event["thread_id"] == "first":
            return {"_execution": {"status": "pending", "error": "transport timeout"}}
        return {"_execution": {"status": "completed"}, "proposed_change_type": "no_change"}

    monkeypatch.setattr(zopedia_learning, "critique_learning_event", critic)
    result = zopedia_learning.run_zopedia_learning_job(limit=2, conn=Connection())
    assert result["threads_scanned"] == 2
    assert result["critiques_pending"] == 1
    assert result["events_triaged"] == 1
    assert result["status"] == "partial"
    assert any(event["thread_id"] == "first" and event["status"] == "critique_pending" for event in stored)
    assert any(update.get("status") == "reviewed_no_change" for update in updates)


def test_saved_review_runs_before_new_criticism_and_does_not_rebuy_the_critic(monkeypatch):
    from contextlib import contextmanager
    phases=[];fields=[]
    prior={'event_id':'old','status':'review_pending','eval_case_path':'/ephemeral/old.json',
        'metadata_json':{'critic':{'_execution':{'status':'completed','input_fingerprint':'older-engine'}},
                         'eval_case':{'event_id':'old','frozen_input':True}}}
    class Connection:
        info=SimpleNamespace(transaction_status=0)
        @contextmanager
        def cursor(self):
            class Cursor:
                def execute(self,sql,args):self.identity=args[0]
                def fetchone(self):return (prior,) if self.identity=='old' else None
            yield Cursor()
        def commit(self):pass
    monkeypatch.setattr(zopedia_learning,'bootstrap_zopedia_learning_storage',lambda conn:None)
    monkeypatch.setattr(zopedia_learning,'load_aql_zopedia_llm_client',lambda **kw:object())
    monkeypatch.setattr(zopedia_learning,'_recent_threads',lambda *a,**kw:[
        {'episode':{'event_id':'old','metadata':{}}},{'episode':{'event_id':'new','metadata':{}}}])
    monkeypatch.setattr(zopedia_learning,'persist_regression_eval_case',lambda case,**kw:'zopedia_learning_event:'+case['event_id'])
    monkeypatch.setattr(zopedia_learning,'_update_learning_event_fields',lambda conn,event_id,**kw:fields.append((event_id,kw)))
    monkeypatch.setattr(zopedia_learning,'_merge_learning_event_metadata',lambda *a:None)
    monkeypatch.setattr(zopedia_learning,'_persist_learning_event',lambda conn,event,**kw:event)
    monkeypatch.setattr(zopedia_learning,'replay_learning_eval',lambda ref,**kw:phases.append(('review',ref)) or {'status':'reviewed'})
    def critique(event,*args,on_pending,**kwargs):
        on_pending();phases.append(('critique',event['event_id']))
        return {'_execution':{'status':'completed'},'proposed_change_type':'no_change'}
    monkeypatch.setattr(zopedia_learning,'critique_learning_event',critique)
    monkeypatch.setenv('ZOPEDIA_LEARNING_VERIFY_EVALS','1')
    result=zopedia_learning.run_zopedia_learning_job(limit=2,conn=Connection())
    assert phases==[('review','zopedia_learning_event:old'),('critique','new')]
    assert result['critiques_reused']==1 and result['reviews_pending']==0
    assert any(identity=='old' and value.get('status')=='reviewed' for identity,value in fields)


def test_learning_reports_actual_per_case_admission_deferrals(monkeypatch):
    import time
    from contextlib import contextmanager
    class Connection:
        info=SimpleNamespace(transaction_status=0)
        @contextmanager
        def cursor(self):yield SimpleNamespace(execute=lambda *a:None,fetchone=lambda:None)
        def commit(self):pass
    monkeypatch.setattr(zopedia_learning,'bootstrap_zopedia_learning_storage',lambda conn:None)
    monkeypatch.setattr(zopedia_learning,'load_aql_zopedia_llm_client',lambda **kw:object())
    monkeypatch.setattr(zopedia_learning,'_recent_threads',lambda *a,**kw:[{'episode':{'event_id':str(i),'metadata':{}}} for i in range(2)])
    monkeypatch.setattr(zopedia_learning,'_persist_learning_event',lambda conn,event,**kw:event)
    monkeypatch.setattr(zopedia_learning,'_merge_learning_event_metadata',lambda *a:None)
    def critique(event,*args,on_pending,**kw):
        on_pending();raise AssertionError('An unfundable full case started inference')
    monkeypatch.setattr(zopedia_learning,'critique_learning_event',critique)
    monkeypatch.setenv('ZOPEDIA_LEARNING_CASE_TIMEOUT_SECONDS','900')
    result=zopedia_learning.run_zopedia_learning_job(limit=2,conn=Connection(),deadline_monotonic=time.monotonic()+500)
    assert result['cases_deferred']==result['critiques_pending']==2
    assert result['job_status']=='Warning'
