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
    if host.endswith('.openai.azure.com') or host in {'api.deepseek.com', 'api.openai.com', 'api.tavily.com', 'serpapi.com', 'google.serper.dev', 'api.elevenlabs.io'}:
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
assert namespace.startswith('event-adaptive-dev-'), 'Isolated DEV namespace required'
platform_execution = os.environ['CONTAINER_APP_JOB_EXECUTION_NAME']
retry_state_key = 'native_retry:' + platform_execution
prior = store.read_execution_state('release_qualification', retry_state_key, strict=True)
assert store.write_execution_state('release_qualification', 'nullable:' + platform_execution,
    {'business_lens': float('nan'), 'nested': [float('inf'), None]})
assert store.read_execution_state('release_qualification', 'nullable:' + platform_execution, strict=True) == {
    'business_lens': None, 'nested': [None, None]}

if not prior:
    from decimal import Decimal
    from io import BytesIO
    from services import page_browsing, database
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    original_env = {key: os.getenv(key) for key in ('PIPELINE_RUN_ID','RESEARCH_RUN_BUDGET_USD',
        'RESEARCH_BUDGET_MODE','RESEARCH_EVENT_MARGIN_FRACTION','CONTAINER_APP_JOB_EXECUTION_NAME')}
    event_root='event-margin-'+uuid.uuid4().hex
    os.environ.update(PIPELINE_RUN_ID=event_root, RESEARCH_RUN_BUDGET_USD='10',
                      RESEARCH_BUDGET_MODE='event',RESEARCH_EVENT_MARGIN_FRACTION='0.5')
    execution.reserve_research_cost(attempt_id='ordinary', maximum_usd=10, category='qualification')
    def expand(index):
        try:
            with execution.event_research_context({'work_id':'selected:'+str(index),
                    'evidence_ids':['retained-source:'+str(index)],'reason':'Model-selected current investigation'}):
                execution.reserve_research_cost(attempt_id='event-'+str(index),maximum_usd=1,category='qualification')
                return index
        except execution.ResearchBudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=16) as pool:
        admitted=[v for v in pool.map(expand, range(16)) if v is not None]
    assert len(admitted)==5
    state=store.read_execution_state(*execution._run_budget_key(),strict=True)
    assert state['cap_microusd']==state['event_ceiling_microusd']==15_000_000
    os.environ.update(RESEARCH_RUN_BUDGET_USD='1000',RESEARCH_EVENT_MARGIN_FRACTION='5')
    assert execution.research_spending_context()['base_budget_usd']==10
    assert execution.event_investigation_capacity(4)==6
    for index in admitted:
        execution.settle_research_cost(attempt_id='event-'+str(index),actual_usd=0)
    try:
        execution.reserve_research_cost(attempt_id='unselected',maximum_usd=1,category='qualification')
        raise AssertionError('Unselected work consumed marginal funds')
    except execution.ResearchBudgetExceeded:
        pass
    print(json.dumps({'probe':'event_margin','concurrent_workers':16,'extra_admitted':5,
        'base_usd':10,'ceiling_usd':15,'retry_cannot_reset':True,'unselected_cannot_consume_margin':True}),flush=True)

    os.environ.update(PIPELINE_RUN_ID=event_root+'-embedding',RESEARCH_RUN_BUDGET_USD='10')
    cfg=llm.LLMConfig(provider='azure_openai',api_key='simulated',model='zopedia-embedding',
        base_url='https://qualification.invalid',embedding_model='text-embedding-3-small',
        embedding_deployment='zopedia-embedding')
    response=requests.Response();response.status_code=200
    response._content=json.dumps({'model':'text-embedding-3-small','usage':{'prompt_tokens':5,'total_tokens':5},
        'data':[{'index':0,'embedding':[0.1,0.2]},{'index':1,'embedding':[0.3,0.4]}]}).encode()
    payload={'model':'zopedia-embedding','input':[[11,22,33],[44,55]]}
    assert execution._request_maximum_charge(cfg,payload)==Decimal('0.00000011')
    execution.provider_post(config=cfg,session=SimpleNamespace(post=lambda **kw:response),
        operation='embedding',json=payload,url='https://qualification.invalid/embedding',timeout=20)
    embedding_state=store.read_execution_state(*execution._run_budget_key(),strict=True)
    assert len(embedding_state['attempts'])==1 and not embedding_state.get('invalid_quote')
    assert next(iter(embedding_state['attempts'].values()))['status']=='settled'
    print(json.dumps({'probe':'azure_embedding_alias','input_tokens':5,'quoted_usd':0.00000011,
        'wire_deployment':'zopedia-embedding','pricing_model':'text-embedding-3-small','transport':'simulated','accounting':'real Azure SQL'}),flush=True)

    writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 40 700 Td (First complete source paragraph.) Tj 0 -30 Td (Second complete source paragraph.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    raw=BytesIO();writer.write(raw);pdf_bytes=raw.getvalue()
    response=requests.Response();response.status_code=200;response.url='https://qualification.invalid/source.pdf'
    response.headers['Content-Type']='application/octet-stream';response._content=pdf_bytes
    original_get=requests.get
    requests.get=lambda url,**kw: response if url=='https://qualification.invalid/source.pdf' else original_get(url,**kw)
    try:
        with execution.execution_issue_context():
            page_result=page_browsing._browse_with_http(response.url,timeout_seconds=15,max_text_chars=10000)
            assert execution.execution_issues()==[]
    finally:
        requests.get=original_get
    assert 'First complete source paragraph.' in page_result['source_text'] and 'Second complete source paragraph.' in page_result['source_text']
    assert chr(0) not in json.dumps(page_result,ensure_ascii=False)
    archive=store.ImmutableBlobArchive(namespace=namespace,root='source-responses')
    assert archive.get(page_result['source_metadata']['raw_response'])==pdf_bytes
    source_execution=platform_execution+'-source-contract'
    os.environ['CONTAINER_APP_JOB_EXECUTION_NAME']=source_execution
    ctx=jobs.JobContext(name='qualification-source',run_id=event_root+'-pdf',asof=datetime.now(timezone.utc),universe_version='qualification')
    store.write_execution_state('pipeline_execution:'+namespace,ctx.name+':'+source_execution,{'run_id':ctx.run_id})
    occupied=database.open_database_session(store._postgres_connection_string(),workload='background')
    try:
        with occupied.cursor() as cur:cur.execute('SELECT 1')
        with database_workload('background'):
            jobs._persist_dataset('qualification_pdf_source',pd.DataFrame([{'source_text':page_result['source_text'],
                'source_metadata_json':json.dumps(page_result['source_metadata'])}]),ctx,None)
        assert ctx.degraded_components=={}
    finally:
        occupied.close()
    versions=store.restore_execution_publications(execution=source_execution,run_id=ctx.run_id)
    assert versions==ctx.published_datasets
    print(json.dumps({'probe':'pdf_and_publication','original_bytes_retained':True,'paragraphs_preserved':True,
        'publication_under_background_load':True,'restored_native_versions':versions}),flush=True)
    for key,value in original_env.items():
        if value is None:os.environ.pop(key,None)
        else:os.environ[key]=value

    # Real cross-run Azure SQL transactions with an isolated, simulated wallet.
    # The official endpoint identity exercises its actual admission contract;
    # no HTTP request is sent and the credential is deliberately fictitious.
    account_root = 'future-core-' + uuid.uuid4().hex
    os.environ.update(PIPELINE_RUN_ID=account_root, RESEARCH_RUN_BUDGET_USD='10')
    account_config = llm.LLMConfig(provider='deepseek', api_key=account_root,
        model='deepseek-v4-pro', base_url='https://api.deepseek.com')
    account_key = ('provider_budget', execution._account_key(account_config))
    store.write_execution_state(*account_key, {'available':True, 'balance_usd':10,
        'observed_at':time.time(), 'reservations':{}})
    quote_function = execution._request_maximum_charge
    quote = Decimal(1)
    execution._request_maximum_charge = lambda *args, **kwargs: quote
    try:
        first_run = execution._run_budget_key()
        execution.protect_core_research(config=account_config, work={'first':'current input'}, stages=2)
        original = store.read_execution_state(*first_run, strict=True)['core_obligations']['first']
        os.environ['PIPELINE_RUN_ID'] = account_root + '-other'
        execution.protect_core_research(config=account_config, work={'other':'current input'}, stages=2)
        os.environ['PIPELINE_RUN_ID'] = account_root + '-optional'
        quote = Decimal(7)
        try:
            execution.reserve_provider_budget(account_config, {}, attempt_id='optional', timeout=20)
            raise AssertionError('Optional work consumed protected completion credit')
        except execution.ResearchBudgetExceeded:
            pass
        os.environ['PIPELINE_RUN_ID'] = account_root
        quote = Decimal(1)
        with execution.model_call_context(core_work_id='first', routing={'role':'core'}):
            execution.reserve_provider_budget(account_config, {}, attempt_id='paid', timeout=20)
        reservation = {'run':first_run, 'account':account_key}
        for _ in range(2):
            execution.settle_provider_budget(account_config, attempt_id='paid',
                charge={'status':'estimated','usd_max':.2}, definitely_unbilled=False, reservation=reservation)
        wallet = store.read_execution_state(*account_key, strict=True)
        assert execution._future_core_microusd(wallet) == 3_800_000
        assert execution._provider_unreserved_microusd(wallet) == 9_800_000
        execution.interrupt_core_research('Development qualification interruption')
        assert execution._future_core_microusd(store.read_execution_state(*account_key,strict=True)) == 2_000_000
        execution.protect_core_research(config=account_config, work={'first':'current input'}, stages=2)
        resumed = store.read_execution_state(*first_run, strict=True)['core_obligations']['first']
        assert resumed['deadline_at'] == original['deadline_at']
        assert resumed['generation'] == original['generation'] and resumed['remaining_microusd'] == 1_800_000
        with execution.model_call_context(core_work_id='first', routing={'role':'core'}):
            execution.reserve_provider_budget(account_config, {}, attempt_id='unknown', timeout=20)
        execution.interrupt_core_research('Finished with an unknown paid outcome')
        execution.settle_provider_budget(account_config, attempt_id='unknown',
            charge={'status':'usage_unavailable'}, definitely_unbilled=False, reservation=reservation)
        wallet = store.read_execution_state(*account_key, strict=True)
        assert wallet['reservations']['unknown']['status'] == 'unknown'
        assert execution._future_core_microusd(wallet) == 2_000_000
        os.environ['PIPELINE_RUN_ID'] = account_root + '-other'
        execution.complete_core_research('other')
        assert not store.read_execution_state(*account_key, strict=True)['future_core']
        print(json.dumps({'probe':'shared_account_future_core', 'independent_runs':2,
            'optional_priority_enforced':True, 'atomic_transfer_and_idempotent_refund':True,
            'retry_original_clock_and_funding':True, 'unknown_paid_outcome_retained':True,
            'paid_transport':'none', 'accounting':'real isolated Azure SQL'}), flush=True)
    finally:
        execution._request_maximum_charge = quote_function

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
    if prior:
        assert ctx.published_datasets == prior['datasets'], 'Native receipts did not restore SQL-missing publication progress'
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
        store.mutate_execution_state('pipeline_execution:'+namespace,ctx.name+':'+platform_execution,
            lambda state:{**state,'published_datasets':{}})
        print(json.dumps({'probe':'intentional_replica_failure', 'run_id':ctx.run_id}), flush=True)
        raise RuntimeError('Development-only deliberate replica failure')
    assert ctx.run_id == prior['run_id'] and ctx.asof.isoformat() == prior['asof']
    assert ctx.started_at_monotonic < time.monotonic() - 1
    print(json.dumps({'probe':'native_retry', 'original_run_id':ctx.run_id, 'same_profile':policy()['profile'],
        'budget_usd':os.environ['RESEARCH_RUN_BUDGET_USD'], 'published_versions':ctx.published_datasets,
        'elapsed_seconds':round(time.monotonic()-ctx.started_at_monotonic,2), 'status':'passed'}), flush=True)
jobs.run_news = handler
jobs.main()

# Exercise the producer's actual deadline exit after native publication. This
# uses the same controller with a thirty-second qualification clock, not a
# three-hour production execution or a fabricated successful model result.
assert prior
os.environ.update(CONTAINER_APP_JOB_EXECUTION_NAME=platform_execution + '-deadline-contract',
                  PIPELINE_JOB_MAX_DURATION_SECONDS='30', RESEARCH_RUN_BUDGET_USD='10')
def deadline_handler(ctx, conn):
    jobs._persist_dataset('qualification_deadline_report', pd.DataFrame([{'value': 2}]), ctx, conn)
    assert ctx.published_datasets['qualification_deadline_report']
    time.sleep(remaining_work_seconds(30) + .05)
    check_work()
jobs.run_news = deadline_handler
jobs.main()
receipt = store.read_execution_state('pipeline_execution:' + namespace,
    'news-ingest-and-features:' + platform_execution + '-deadline-contract', strict=True)
assert receipt['published_datasets']['qualification_deadline_report']
with database_workload('interactive'):
    conn = store.open_database_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT status FROM job_runs WHERE run_id=%s', (receipt['run_id'],))
            assert cur.fetchone()[0] == 'Warning'
    finally:
        conn.close()
print(json.dumps({'probe':'deadline_completed_subset', 'clock_seconds':30,
    'published_versions':receipt['published_datasets'], 'application_status':'Warning',
    'controller_returned':True}), flush=True)
