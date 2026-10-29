from pathlib import Path
import hashlib, json, os, socket, importlib

root=Path('/app')
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
fingerprint=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
assert fingerprint=='002a83afec8ac7bd44134e1bb404fc1aef92fdedfc17ee898b67dd75d220f8ac', 'Runtime inventory differs from accepted Git'
assert os.environ['GIT_SHA']=='6df6af3776883000260a29b4da0a32a9f326e095'
def no_network(*args,**kwargs):raise AssertionError('Package qualification cannot contact external services')
socket.create_connection=no_network
socket.socket.connect=no_network
for name in ['services.trading_portfolio','services.alpaca_paper','services.brokerage_mcp_store','pipeline.jobs.main','scripts.sync_robinhood_main']:
 importlib.import_module(name)
from api.main import app
paths=app.openapi()['paths']
assert '/v1/trading-agent/paper/policy' in paths
assert '/v1/trading-agent/paper/calls/{call_id}' in paths
checks=['complete Git package','API imports/OpenAPI','worker and broker imports','retained native performance API']
if ROLE=='api-next':
 from fastapi.testclient import TestClient
 client=TestClient(app)
 assert client.get('/health').status_code==200
 for path in ['/v1/trading-agent','/v1/trading-agent/paper/calls/unowned']:
  assert client.get(path).status_code in {401,403,503}
 for action in ['review','confirm','policy','refresh']:
  assert client.post('/v1/trading-agent/paper/'+action,json={}).status_code in {401,403,503}
 checks.append('unauthenticated Trading read/write rejection')
from compute.paper_portfolio import attribute_fills, period_performance
from compute.analytics import performance_summary
assert callable(attribute_fills) and callable(period_performance) and callable(performance_summary)
print(json.dumps({'status':'passed','source_revision':os.environ['GIT_SHA'],'runtime_files':len(files),'runtime_inventory_sha256':fingerprint,'all_packaged_files_checked':True,'checks':checks,'external_requests':0}),flush=True)
