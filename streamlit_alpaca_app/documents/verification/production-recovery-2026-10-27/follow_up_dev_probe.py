"""Exact-image DEV integration: real Azure SQL/Blob, simulated paid transport.

Complete narrative/model comparisons are separately owner-approved and retained.
This check never sends a paid inference or acquisition request.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import os
import threading
import time
import uuid

import pandas as pd
import requests

files = {str(p.relative_to('/app')): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in Path('/app').rglob('*') if p.is_file()}
fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
assert fingerprint == os.environ['EXPECTED_RUNTIME_INVENTORY_SHA256'], 'Packaged Git inventory differs'
print(json.dumps({'probe': 'package', 'files': len(files), 'runtime_inventory_sha256': fingerprint,
                  'source_revision': os.getenv('GIT_SHA')}), flush=True)

real_send = requests.Session.send
def prohibit_paid_transport(session, request, **kwargs):
    from urllib.parse import urlparse
    host = urlparse(request.url).hostname or ''
    if host in {'api.deepseek.com', 'api.openai.com', 'api.tavily.com', 'serpapi.com', 'google.serper.dev'}:
        raise RuntimeError('This DEV integration does not authorize paid transport')
    return real_send(session, request, **kwargs)
requests.Session.send = prohibit_paid_transport

from pipeline.jobs import main as jobs
from services import llm, llm_execution as execution, pipeline_store as store, zopedia_learning
from services.aql import business_model_stack as company
from services.aql_zopedia_gateway import generate_json_via_aql_zopedia_gateway as generate
from services.database import database_workload
from services.inference_policy import policy
from services.work_scope import WorkScope, check_work, remaining_work_seconds

assert policy()['profile'] == 'balanced'
namespace = store.dataset_namespace()
assert namespace.startswith('production-followup-dev-'), 'Isolated DEV namespace required'
platform_execution = os.environ['CONTAINER_APP_JOB_EXECUTION_NAME']
retry_state_key = 'native_retry:' + platform_execution
prior = store.read_execution_state('release_qualification', retry_state_key, strict=True)

if not prior:
    root = 'financial-integration-' + uuid.uuid4().hex
    os.environ.update(PIPELINE_RUN_ID=root, RESEARCH_RUN_BUDGET_USD='10')
    calls = []
    lock = threading.Lock()
    factory = llm.client_from_config
    def simulated_client(config):
        client = factory(config)
        def post(**kwargs):
            with lock:
                calls.append(kwargs['json']['model'])
            time.sleep(.05)
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps({'model': config.model,
                'usage': {'prompt_tokens': 100, 'completion_tokens': 100},
                'choices': [{'message': {'content': '{"value":1}'}}]}).encode()
            return response
        client.session = SimpleNamespace(post=post)
        return client
    llm.client_from_config = simulated_client
    config = llm.LLMConfig(provider='deepseek', api_key='simulated-dev-transport',
        model='deepseek-v4-pro', base_url='https://qualification.invalid',
        thinking_mode='enabled', reasoning_effort='high')
    execution.protect_core_research(config=config, work={str(i): 'Current input' for i in range(8)}, stages=3)
    spending = execution.research_spending_context()
    execution.reserve_research_cost(attempt_id='optional-headroom', maximum_usd=spending['available_usd'], category='qualification')
    def write(index):
        with execution.model_call_context(core_work_id=str(index)):
            for purpose in ('agent.final_synthesis', 'agent.answer_judge'):
                assert generate(llm_client=simulated_client(config), surface='qualification.current_report',
                    purpose=purpose, call_type='research_grade', system_prompt='Return JSON from supplied input.',
                    user_prompt='Current input', schema_name='qualification', schema={'type':'object'},
                    deadline_monotonic=time.monotonic()+60)['value'] == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: copy_context().run(write, i), range(8)))
    assert calls == ['deepseek-v4-pro'] * 16
    execution.settle_research_cost(attempt_id='optional-headroom', actual_usd=0)
    print(json.dumps({'probe':'protected_core', 'concurrent_workers':8, 'full_model_calls':16,
                      'paid_transport':'simulated', 'real_sql_accounting':True}), flush=True)

    # A completed response arriving after cancellation survives a fresh caller.
    child = WorkScope(time.monotonic()+10)
    client = simulated_client(config)
    post = client.session.post
    def late(**kwargs):
        response = post(**kwargs)
        child.cancel()
        return response
    client.session.post = late
    try:
        with child.activate():
            client.generate_json(system_prompt='Retain response', user_prompt='Distinct late input',
                                 schema_name='late', schema={'type':'object'})
    except TimeoutError:
        pass
    client.session.post = lambda **kwargs: (_ for _ in ()).throw(RuntimeError('Paid response was repurchased'))
    assert client.generate_json(system_prompt='Retain response', user_prompt='Distinct late input',
                               schema_name='late', schema={'type':'object'})['value'] == 1
    print(json.dumps({'probe':'late_paid_response', 'adopted_without_repurchase':True}), flush=True)

    os.environ['PIPELINE_RUN_ID'] = root + '-zero'
    os.environ['RESEARCH_RUN_BUDGET_USD'] = '0'
    before = len(calls)
    generate(llm_client=simulated_client(config), surface='qualification.current_report',
        purpose='agent.final_synthesis', call_type='research_grade', system_prompt='Return JSON.',
        user_prompt='Current zero-budget input', schema_name='zero', schema={'type':'object'},
        deadline_monotonic=time.monotonic()+60)
    assert calls[before:] == ['deepseek-flash']
    print(json.dumps({'probe':'zero_budget_current_report', 'model':'deepseek-flash'}), flush=True)
    llm.client_from_config = factory

    # Hold background capacity while actual ownership renewal uses control capacity.
    held = threading.Event()
    release = threading.Event()
    def occupy_background():
        with database_workload('background'):
            conn = store.open_database_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute('SELECT 1')
                held.set()
                release.wait(timeout=35)
            finally:
                conn.close()
    holder = threading.Thread(target=occupy_background)
    holder.start()
    assert held.wait(timeout=15)
    try:
        with execution.execution_ownership('qualification_ownership', root, lease_seconds=12) as owned:
            assert owned
            time.sleep(15)
            check_work()
    finally:
        release.set()
        holder.join(timeout=10)
    assert not holder.is_alive()
    print(json.dumps({'probe':'ownership_under_background_load', 'held_seconds':15, 'lease_seconds':12,
                      'renewal_survived':True}), flush=True)

    event_id = 'zlearn_qualification_' + uuid.uuid4().hex
    case = {'learning_event_id':event_id, 'name':'durability-qualification',
            'question':'Retained qualification input', 'evidence_records':{}, 'user_key':namespace}
    conn = store.open_database_connection()
    try:
        zopedia_learning._persist_learning_event(conn, {'event_id':event_id, 'user_key':namespace,
            'status':'qualification', 'metadata':{'namespace':namespace}})
        reference = zopedia_learning.persist_regression_eval_case(case, conn=conn)
        conn.commit()
    finally:
        conn.close()
    conn = store.open_database_connection()
    try:
        assert zopedia_learning.replay_learning_eval(reference, conn=conn)['eval_case'] == case
        with conn.cursor() as cur:
            cur.execute('DELETE FROM saa_zopedia_learning_events WHERE event_id=%s', (event_id,))
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({'probe':'learning_input', 'fresh_connection_exact_read':True}), flush=True)

os.environ.update(PIPELINE_JOB_NAME='news-ingest-and-features',
    RESEARCH_RUN_BUDGET_USD='1000' if prior else '10', PIPELINE_JOB_MAX_DURATION_SECONDS='600')
def handler(ctx, conn):
    state = store.read_execution_state('pipeline_execution:' + namespace,
        ctx.name + ':' + platform_execution, strict=True)
    assert state['run_id'] == ctx.run_id and os.environ['RESEARCH_RUN_BUDGET_USD'] == '10'
    with execution.execution_ownership('pipeline_job:' + namespace, 'publication-qualification', lease_seconds=60) as owned:
        assert owned
        jobs._persist_dataset('qualification_current_report', pd.DataFrame([{'value':1}]), ctx, conn)
        dataset = store._stable_latest_manifest_metadata('qualification_current_report')
        assert dataset.dataset_version_id == ctx.published_datasets['qualification_current_report']
        latest = store._read_blob_json(store.dataset_blob_path('manifests/qualification_current_report/latest.json'))
        obsolete = {**latest, 'dataset_version_id': 'obsolete-' + uuid.uuid4().hex,
            'execution_fence': latest['execution_fence'] - 1,
            'ingested_at_utc': (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}
        assert not store.publish_dataset_manifest(obsolete)
        assert store._stable_latest_manifest_metadata('qualification_current_report').dataset_version_id == dataset.dataset_version_id
    if not prior:
        assert store.write_execution_state('release_qualification', retry_state_key,
            {'run_id':ctx.run_id, 'asof':ctx.asof.isoformat(), 'datasets':ctx.published_datasets,
             'started_at_utc':datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'probe':'intentional_replica_failure', 'run_id':ctx.run_id}), flush=True)
        raise RuntimeError('Development-only deliberate replica failure')
    assert ctx.run_id == prior['run_id'] and ctx.asof.isoformat() == prior['asof']
    assert ctx.started_at_monotonic < time.monotonic() - 1
    print(json.dumps({'probe':'native_retry', 'original_run_id':ctx.run_id, 'same_profile':policy()['profile'],
        'budget_usd':os.environ['RESEARCH_RUN_BUDGET_USD'], 'published_versions':ctx.published_datasets,
        'elapsed_seconds':round(time.monotonic()-ctx.started_at_monotonic,2), 'status':'passed'}), flush=True)
jobs.run_news = handler
jobs.main()
