"""Qualify the production API image inside DEV; all production data access is read-only."""
from pathlib import Path
import hashlib
import json
import os
import requests

files = {str(p.relative_to('/app')): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in Path('/app').rglob('*') if p.is_file()}
fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
assert fingerprint == os.environ['EXPECTED_RUNTIME_INVENTORY_SHA256']
print(json.dumps({'probe': 'package', 'files': len(files), 'runtime_inventory_sha256': fingerprint}), flush=True)

send = requests.Session.send
def read_only_transport(session, request, **kwargs):
    from urllib.parse import urlparse
    host = urlparse(request.url).hostname or ''
    if host in {'api.deepseek.com', 'api.openai.com', 'api.tavily.com', 'serpapi.com', 'google.serper.dev'}:
        raise RuntimeError('No inference or acquisition is authorized by API verification')
    return send(session, request, **kwargs)
requests.Session.send = read_only_transport

from api import main as api
from fastapi.testclient import TestClient
from services import api_auth, auth_service, trading_call_tracking as tracking, pipeline_store as store

baseline_url = os.environ['BASELINE_API_URL'].rstrip('/')
response = requests.get(baseline_url + '/openapi.json', timeout=30)
response.raise_for_status()
baseline = response.json()
candidate = api.app.openapi()
assert baseline['paths'] == candidate['paths'] and baseline['components'] == candidate['components']
operations = sum(method in {'get', 'post', 'put', 'patch', 'delete'} for path in candidate['paths'].values() for method in path)
assert operations == 53
print(json.dumps({'probe': 'api_contract', 'operations': operations, 'all_paths_and_schemas_preserved': True}), flush=True)

client = TestClient(api.app)
assert client.get('/v1/trading-agent').status_code == 401
assert client.get('/v1/research-conversations').status_code == 401
assert client.get('/v1/feature-requests').status_code == 401
print(json.dumps({'probe': 'anonymous_authorization', 'protected_paths': 3, 'status': 401}), flush=True)

owner = os.environ['QUALIFICATION_OWNER_ID']
context = auth_service.UserContext(user_id=owner, email='isolated@example.invalid', first_name='Isolated',
    last_name='review', display_name='Isolated review', role='admin', portfolio_id='', portfolio_slug='',
    portfolio_name='', membership_role='admin', share_fraction=1, can_view_full_portfolio=True)
principal = api_auth.AuthPrincipal(principal_type='user', scopes=tuple(api_auth.DEFAULT_USER_SCOPES | api_auth.ADMIN_EXTRA_SCOPES),
    auth_source='isolated_in_process_verification', user_context=context)
api.app.dependency_overrides[api._require_principal] = lambda: principal
calls = tracking.approved_calls(owner_id=owner)
metadata = store.latest_dataset_metadata(tracking.DATASET)
published = store.load_dataset_frame(metadata)
owned = published[published['owner_id'].eq(owner)].to_dict('records')
entered = next(row for row in owned if len(json.loads(row.get('path_json') or '[]')) >= 5)
identity = entered['tracking_id']
response = client.get('/v1/trading-agent/calls/' + identity)
assert response.status_code == 200, response.text
detail = response.json()
assert detail['tracking_id'] == identity and 'owner_id' not in detail
assert detail['path'] == json.loads(entered['path_json'])
assert detail['return_pct'] == entered['return_pct']
assert detail['performance']['daily_observations'] >= 4
assert detail['performance']['sharpe_ratio'] is not None
assert response.headers['cache-control'] == 'private, no-store'
print(json.dumps({'probe': 'owned_call_detail', 'ticker': detail['ticker'], 'path_points': len(detail['path']),
    'roi_pct': detail['return_pct'], 'performance': detail['performance'], 'original_path_and_roi_preserved': True}), flush=True)

principal = api_auth.AuthPrincipal(principal_type='user', scopes=tuple(api_auth.DEFAULT_USER_SCOPES),
    auth_source='isolated_in_process_verification', user_context=auth_service.UserContext(
        user_id='non-owner-qualification', email='isolated@example.invalid', first_name='', last_name='',
        display_name='', role='admin', portfolio_id='', portfolio_slug='', portfolio_name='', membership_role='admin', share_fraction=1))
assert client.get('/v1/trading-agent/calls/' + identity).status_code == 404
api.app.dependency_overrides.clear()
assert client.get('/v1/trading-agent/calls/' + identity).status_code == 401
print(json.dumps({'probe': 'owner_isolation', 'nonowner_status': 404, 'anonymous_status': 401}), flush=True)
print(json.dumps({'status': 'passed', 'actual_inference_requests': 0, 'product_data_writes': 0,
    'auth_note': 'Principal substitution is confined to this in-process DEV verification; application source and real server auth remain unchanged.'}), flush=True)
