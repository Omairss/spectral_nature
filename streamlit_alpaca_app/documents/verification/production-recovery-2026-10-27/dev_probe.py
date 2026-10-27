"""Native exact-image check: eight workers, durable inputs and actual retry."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import os
import threading
import time
import uuid

files = {str(path.relative_to('/app')): hashlib.sha256(path.read_bytes()).hexdigest()
         for path in Path('/app').rglob('*') if path.is_file()}
fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
if fingerprint != os.environ['EXPECTED_RUNTIME_INVENTORY_SHA256']:
    raise RuntimeError('Packaged application differs from accepted Git runtime')
print(json.dumps({'probe': 'package', 'files': len(files), 'runtime_inventory_sha256': fingerprint,
                  'source_revision': os.getenv('GIT_SHA')}), flush=True)

import pipeline.jobs.main
from services import llm, pipeline_store, zopedia_learning
from services.aql import business_model_stack as company
from services.aql_zopedia_gateway import generate_json_via_aql_zopedia_gateway
from services.inference_policy import execute_with_fallback, policy
from services.work_scope import WorkScope, remaining_work_seconds

print(json.dumps({'probe': 'activation', 'effective_profile': policy()['profile'],
                  'not_before': os.environ.get('LLM_POLICY_NOT_BEFORE')}), flush=True)
if policy()['profile'] != 'balanced':
    raise RuntimeError('Same-day balanced profile did not activate')

event_id = 'zlearn_qualification_' + uuid.uuid4().hex
case = {'learning_event_id': event_id, 'name': 'durability-qualification',
        'question': 'Retained qualification input', 'evidence_records': {},
        'user_key': 'production-recovery-qualification'}
conn = pipeline_store.open_database_connection()
if conn is None:
    raise RuntimeError('Managed-identity database credential unavailable')
try:
    zopedia_learning._persist_learning_event(conn, {'event_id': event_id, 'user_key': case['user_key'],
        'status': 'qualification', 'metadata': {'namespace': pipeline_store.dataset_namespace()}})
    reference = zopedia_learning.persist_regression_eval_case(case, conn=conn)
    conn.commit()
    conn.close()
    conn = pipeline_store.open_database_connection()
    retained = zopedia_learning.replay_learning_eval(reference, conn=conn)['eval_case']
    if retained != case:
        raise RuntimeError('A fresh connection did not read the exact frozen review input')
    print(json.dumps({'probe': 'learning_durability', 'fresh_connection': True,
                      'local_file_required': False, 'status': 'passed'}), flush=True)
finally:
    if conn is None or conn.closed:
        conn = pipeline_store.open_database_connection()
    with conn.cursor() as cur:
        cur.execute('DELETE FROM saa_zopedia_learning_events WHERE event_id=%s', (event_id,))
    conn.commit()
    conn.close()

original = company.build_ticker_business_model_stack
barrier = threading.Barrier(8)
seen = []
def admission_probe(*, symbol, **kwargs):
    seen.append(remaining_work_seconds(1000))
    barrier.wait(timeout=5)
    return symbol
company.build_ticker_business_model_stack = admission_probe
try:
    with WorkScope(time.monotonic() + 10).activate():
        results = company.build_ticker_business_model_stack_results(symbols=[str(index) for index in range(8)])
    if len(results) != 8 or len(seen) != 8 or not all(0 < value <= 10 for value in seen):
        raise RuntimeError('Eight company workers did not inherit their execution deadline')
    print(json.dumps({'probe': 'company_workers', 'workers': 8, 'parent_deadline_inherited': True}), flush=True)
finally:
    company.build_ticker_business_model_stack = original

corpus = pipeline_store._read_blob_parquet(os.environ['QUALIFICATION_CORPUS_BLOB_PATH'])
symbols = list(dict.fromkeys(corpus['symbol'].dropna().astype(str)))[:8]
inputs = [corpus[corpus['symbol'].astype(str) == symbol].to_json(orient='records') for symbol in symbols]
if len(inputs) != 8:
    raise RuntimeError('Eight actual source-capture contexts are required')
print(json.dumps({'probe': 'source_contexts', 'subjects': len(inputs),
                  'context_bytes': sum(len(value.encode()) for value in inputs)}), flush=True)

base = llm.load_llm_client()
if not base:
    raise RuntimeError('Managed-identity provider credential unavailable')
config = replace(base.config, model='deepseek-v4-pro', thinking_mode='enabled', reasoning_effort='high')
schema = {'type': 'object', 'additionalProperties': False,
          'properties': {'summary': {'type': 'string'}}, 'required': ['summary']}
context = SimpleNamespace(name='news-ingest-and-features', run_id=uuid.uuid4().hex,
                          started_at_monotonic=time.monotonic(), degraded_components={})

def handler(context, connection):
    active = policy()['profile']
    selected = inputs if active == 'balanced' else inputs[:1]
    gate = threading.Barrier(len(selected))
    started = time.monotonic()
    def request(index):
        client = llm.client_from_config(config)
        gate.wait(timeout=10)
        result = generate_json_via_aql_zopedia_gateway(llm_client=client,
            surface='pipeline.ticker_business_model_stack.research_plan' if active == 'balanced' else 'pipeline.ticker_business_model_stack',
            purpose='agent.research_step' if active == 'balanced' else 'ticker_business_model_stack.direct',
            call_type='admin_probe', system_prompt='Summarize the supplied company source captures in one sentence. Return JSON.',
            user_prompt=selected[index], schema_name='recovery_native_capacity_probe', schema=schema,
            deadline_monotonic=time.monotonic() + 300, metadata={'qualification': True, 'worker': index})
        response = getattr(result, 'response_metadata', {})
        return {'worker': index, 'model': response.get('model'), 'completed': bool(result.get('summary'))}
    with ThreadPoolExecutor(max_workers=len(selected)) as pool:
        results = list(pool.map(request, range(len(selected))))
    peak = Path('/sys/fs/cgroup/memory.peak')
    print(json.dumps({'probe': 'provider_concurrency', 'profile': active, 'workers': len(results),
                      'results': results, 'seconds': time.monotonic()-started,
                      'memory_peak_bytes': int(peak.read_text()) if peak.exists() else None}), flush=True)
    if not all(row['completed'] for row in results):
        raise RuntimeError('A concurrent provider request failed')
    if active == 'balanced':
        print(json.dumps({'probe': 'intentional_candidate_failure'}), flush=True)
        raise RuntimeError('Development-only failure for actual Azure baseline retry')
    print(json.dumps({'probe': 'fallback_completed', 'profile': active}), flush=True)
    return {'profile': active}

execute_with_fallback(handler, context, None)
