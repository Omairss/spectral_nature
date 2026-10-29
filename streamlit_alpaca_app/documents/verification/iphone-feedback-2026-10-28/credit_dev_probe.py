"""Exact-image DEV accounting review; no real inference or provider account writes."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import os
import time
import uuid
import requests

files = {str(p.relative_to('/app')): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in Path('/app').rglob('*') if p.is_file()}
fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
assert fingerprint == os.environ['EXPECTED_RUNTIME_INVENTORY_SHA256']
print(json.dumps({'probe': 'package', 'files': len(files), 'runtime_inventory_sha256': fingerprint}), flush=True)

real_send = requests.Session.send
def prohibit_inference(session, request, **kwargs):
    from urllib.parse import urlparse
    if urlparse(request.url).hostname in {'api.deepseek.com', 'api.openai.com'}:
        raise RuntimeError('No paid transport is authorized in this verification')
    return real_send(session, request, **kwargs)
requests.Session.send = prohibit_inference

from services import llm, llm_execution as execution, pipeline_store as store
assert store.dataset_namespace().startswith('iphone-credit-dev-')
os.environ['RESEARCH_RUN_BUDGET_USD'] = '10'
config = llm.LLMConfig(provider='deepseek', api_key='local-qualification-' + uuid.uuid4().hex,
    model='deepseek-v4-pro', base_url='https://api.deepseek.com')
key = execution._account_key(config)
quote, balance = [Decimal(8)], [8]
probes = []
execution._request_maximum_charge = lambda *args: quote[0]
def observed_balance(*args, **kwargs):
    probes.append(time.time())
    response = requests.Response(); response.status_code = 200
    response._content = json.dumps({'is_available': balance[0] > 0,
        'balance_infos': [{'currency': 'USD', 'total_balance': str(balance[0])}]}).encode()
    return response
requests.get = observed_balance
assert store.write_execution_state('provider_budget', key, {
    'available': True, 'observed_at': time.time(), 'balance_usd': 8, 'reservations': {}})
original_run = os.environ['PIPELINE_RUN_ID']
execution.reserve_provider_budget(config, {}, attempt_id='lost', timeout=30)
execution.settle_provider_budget(config, attempt_id='lost', charge={}, definitely_unbilled=False)
os.environ['PIPELINE_RUN_ID'] = original_run + '-current'
quote[0], balance[0] = Decimal(2), 6
execution.reserve_provider_budget(config, {}, attempt_id='current', timeout=30)
wallet = store.read_execution_state('provider_budget', key, strict=True)
lost = wallet['reservations']['lost']
assert lost['amount_usd'] == 8 and lost['status'] == 'unknown'
assert lost['liquidity_observed_at'] > lost['finished_at']
assert execution.research_spending_context()['reserved_usd'] == 2
old = store.read_execution_state('research_run_budget', original_run, strict=True)
assert old['attempts']['lost']['maximum_microusd'] == 8_000_000
print(json.dumps({'probe': 'current_liquidity', 'old_unknown_cost_retained_usd': 8,
    'current_request_reserved_usd': 2, 'balance_probes': len(probes)}), flush=True)

receipt = {'run': ('research_run_budget', original_run), 'account': ('provider_budget', key)}
charge = {'status': 'estimated', 'usd_max': 1.5}
execution.settle_provider_budget(config, attempt_id='lost', charge=charge, definitely_unbilled=False, reservation=receipt)
finished = store.read_execution_state('provider_budget', key, strict=True)['reservations']['lost']['finished_at']
execution.settle_provider_budget(config, attempt_id='lost', charge=charge, definitely_unbilled=False, reservation=receipt)
assert store.read_execution_state('provider_budget', key, strict=True)['reservations']['lost']['finished_at'] == finished
assert store.read_execution_state('research_run_budget', original_run, strict=True)['attempts']['lost']['charged_microusd'] == 1_500_000
print(json.dumps({'probe': 'late_receipt', 'settled_usd': 1.5, 'replay_idempotent': True}), flush=True)

os.environ['PIPELINE_RUN_ID'] = original_run + '-concurrent'
os.environ['RESEARCH_RUN_BUDGET_USD'] = '100'
quote[0], balance[0] = Decimal(1), 4
store.mutate_execution_state('provider_budget', key, lambda state: {**state, 'observed_at': 0})
def admit(index):
    try:
        execution.reserve_provider_budget(config, {}, attempt_id='parallel-' + str(index), timeout=60)
        return index
    except llm.LLMProviderUnavailable:
        return None
with ThreadPoolExecutor(max_workers=16) as pool:
    admitted = [i for i in pool.map(admit, range(16)) if i is not None]
assert len(admitted) == 2  # Existing current request retains its genuine $2 hold.
assert execution.research_spending_context()['reserved_usd'] == 2
print(json.dumps({'probe': 'concurrent_liquidity', 'workers': 16, 'admitted': len(admitted),
    'active_previous_request_hold_usd': 2, 'actual_inference_requests': 0}), flush=True)
print(json.dumps({'status': 'passed', 'production_account_mutated': False}), flush=True)
