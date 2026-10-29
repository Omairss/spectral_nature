"""Exact API/broker package and entrypoint checks, with no paid or broker calls."""
from pathlib import Path
import hashlib
import importlib
import json
import os
from urllib.parse import urlparse

import requests

files = {str(p.relative_to('/app')): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in Path('/app').rglob('*') if p.is_file()}
fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
assert fingerprint == os.environ['EXPECTED_RUNTIME_INVENTORY_SHA256']
role = os.environ['QUALIFICATION_ROLE']
assert role in {'api-next', 'broker'}
assert os.environ['PIPELINE_DATASET_NAMESPACE'].startswith('event-adaptive-dev-')

send = requests.Session.send
identity_host = urlparse(os.getenv('IDENTITY_ENDPOINT', '')).hostname

def no_paid_transport(session, request, **kwargs):
    host = urlparse(request.url).hostname or ''
    if host.endswith('.vault.azure.net') or host in {identity_host, '169.254.169.254', 'login.microsoftonline.com'}:
        return send(session, request, **kwargs)
    raise RuntimeError('Package qualification permits only Azure identity and secret reads')

requests.Session.send = no_paid_transport
if role == 'api-next':
    from fastapi.testclient import TestClient
    from api.main import app
    client = TestClient(app)
    response = client.get('/health')
    assert response.status_code == 200 and response.json()['status'] == 'ok'
    denied = client.post('/v1/trading-agent/paper/refresh')
    assert denied.status_code in {401, 403}, f'Paper authentication returned {denied.status_code}'
    denied = client.get('/v1/admin/health')
    assert denied.status_code in {401, 403}, f'Admin authentication returned {denied.status_code}'
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
