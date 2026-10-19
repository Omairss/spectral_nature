import sys,os,json,uuid,threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import psycopg
from psycopg import sql
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
settings=json.loads(Path('/tmp/sn-trading-resume-worker-20261018.private.json').read_text())
for item in settings['properties']['template']['containers'][0]['env']:
 if 'value' in item:os.environ[item['name']]=str(item['value'])
from services import pipeline_store as store, trading_call_tracking as tracking
schema='qa_call_tracking_'+uuid.uuid4().hex[:12]
dsn=store._postgres_connection_string()
admin=psycopg.connect(dsn,connect_timeout=15)
with admin.cursor() as cur:cur.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
admin.commit()
def connect():
 c=psycopg.connect(dsn,connect_timeout=15)
 with c.cursor() as cur:cur.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
 c.commit();return c
store.open_database_connection=connect
store._db_connect=connect
store.dataset_namespace=lambda:'qa-trading-calls'
candidate={'candidate_id':str(uuid.uuid4()),'ticker':'QA','horizon_key':'1w','horizon_label':'1 Week','stance':'long','hypothesis':'Persistence verification fixture','forecast':{'reference_price':100}}
try:
 assert tracking.initialize_tracking()==0
 c=connect();store._bootstrap_trading_agent_actions(c);c.close()
 barrier=threading.Barrier(3)
 def approve(_):
  barrier.wait();return store.record_trading_agent_action(candidate=candidate,action='approve',requested_by='qa-owner-a')
 with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(approve,range(3)))
 assert all(r[0] for r in results),results
 a=tracking.approved_calls(owner_id='qa-owner-a');assert len(a)==1
 original=a[0];assert original['candidate_payload']==candidate
 assert store.record_trading_agent_action(candidate={**candidate,'hypothesis':'Changed after approval'},action='approve',requested_by='qa-owner-a')[0]
 assert tracking.approved_calls(owner_id='qa-owner-a')[0]['candidate_payload']==candidate
 assert not store.record_trading_agent_action(candidate=candidate,action='reject',requested_by='qa-owner-a')[0]
 assert store.record_trading_agent_action(candidate=candidate,action='approve',requested_by='qa-owner-b')[0]
 assert len(tracking.approved_calls(owner_id='qa-owner-b'))==1
 assert tracking.approved_calls(owner_id='qa-owner-b',tracking_id=original['tracking_id'])==[]
 rejected={**candidate,'candidate_id':str(uuid.uuid4())}
 assert store.record_trading_agent_action(candidate=rejected,action='reject',requested_by='qa-owner-a')[0]
 assert len(tracking.approved_calls(owner_id='qa-owner-a'))==1
 assert not store.record_trading_agent_action(candidate={**candidate,'stance':'watch'},action='approve',requested_by='qa-owner-a')[0]
 assert not store.record_trading_agent_action(candidate=candidate,action='approve',requested_by='')[0]
 entry={'timestamp':'2026-10-19T04:00:00+00:00','session_date':'2026-10-19','close':100,'price_dataset_version':'qa'}
 assert tracking.pin_entries({original['tracking_id']:entry})[original['tracking_id']]==entry
 assert tracking.pin_entries({original['tracking_id']:{**entry,'close':200}})[original['tracking_id']]==entry
 c=connect()
 with c.cursor() as cur:
  cur.execute('SELECT count(*) FROM trading_agent_actions WHERE requested_by=%s AND candidate_id=%s',('qa-owner-a',candidate['candidate_id']));assert cur.fetchone()[0]==1
  # Genuine old log-only approval is retained even when the new ledger already exists.
  legacy={**candidate,'candidate_id':str(uuid.uuid4())};legacy.pop('horizon_key');legacy.pop('ticker')
  cur.execute('INSERT INTO trading_agent_actions(action_id,candidate_id,action,status,execution_mode,created_at_utc,requested_by,candidate_payload,dataset_namespace,horizon_key,ticker) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)',(str(uuid.uuid4()),legacy['candidate_id'],'place_requested','logged','log_only','2026-10-15T10:00:00Z','qa-owner-a',json.dumps(legacy),'qa-trading-calls','1w','QA'))
 c.commit();c.close()
 assert tracking.initialize_tracking()==1
 assert tracking.initialize_tracking()==0
 assert len(tracking.approved_calls(owner_id='qa-owner-a'))==2
 store.dataset_namespace=lambda:'different-qa-environment'
 assert tracking.approved_calls()==[]
 print('Passed real PostgreSQL atomic approval, concurrent retry/idempotence, immutable thesis/entry, rejection, owner/environment isolation, legacy approval migration and migration retry checks.')
finally:
 with admin.cursor() as cur:cur.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
 admin.commit();admin.close()
 print('Removed isolated QA schema',schema)
