"""Exact API/broker package and entrypoint checks, with no paid or broker calls."""
from pathlib import Path
import hashlib
import importlib
import json
import os

import requests

files = {str(p.relative_to('/app')): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in Path('/app').rglob('*') if p.is_file()}
fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
assert fingerprint == os.environ['EXPECTED_RUNTIME_INVENTORY_SHA256']
role = os.environ['QUALIFICATION_ROLE']
assert role in {'api-next', 'broker'}
assert os.environ['PIPELINE_DATASET_NAMESPACE'].startswith('event-adaptive-dev-')

def no_transport(*args, **kwargs):
    raise RuntimeError('Package qualification does not authorize external HTTP requests')

requests.Session.send = no_transport
if role == 'api-next':
    from fastapi.testclient import TestClient
    from api.main import app
    client = TestClient(app)
    response = client.get('/health')
    assert response.status_code == 200 and response.json()['status'] == 'ok'
    assert client.post('/v1/trading-agent/paper/refresh').status_code in {401, 403}
    assert client.get('/v1/admin/health').status_code in {401, 403}
    checks = ['API imports', 'health', 'paper route authentication', 'admin authentication']
else:
    entrypoint = importlib.import_module('scripts.sync_robinhood_main')
    assert callable(entrypoint.main)
    assert callable(entrypoint.robinhood_main_sync.run_pending)
    assert callable(entrypoint.robinhood_main_sync.enqueue_due)
    checks = ['broker entrypoint imports', 'background lifecycle imports', 'no broker execution']

print(json.dumps({'probe': 'role_package', 'role': role, 'status': 'passed',
                  'source_revision': os.environ['GIT_SHA'], 'files': len(files),
                  'runtime_inventory_sha256': fingerprint, 'checks': checks,
                  'paid_or_broker_requests': 0}), flush=True)
