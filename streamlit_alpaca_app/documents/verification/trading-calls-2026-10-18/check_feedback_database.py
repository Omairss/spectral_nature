import sys,os,json,uuid
from pathlib import Path
import psycopg
from psycopg import sql
sys.path.insert(0,str(Path.cwd()))
settings=json.loads(Path('/tmp/sn-trading-resume-worker-20261018.private.json').read_text())
for item in settings['properties']['template']['containers'][0]['env']:
 if 'value' in item:os.environ[item['name']]=str(item['value'])
from services import pipeline_store as store,zopedia_learning as learning
namespace=''
store.dataset_namespace=lambda:namespace
schema='qa_feedback_'+uuid.uuid4().hex[:12]
c=psycopg.connect(store._postgres_connection_string(),connect_timeout=15)
try:
 with c.cursor() as cur:
  cur.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
  cur.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
  cur.execute("CREATE TABLE saa_zopedia_chat_threads(thread_id text,user_key text,status text,updated_at timestamptz)")
 c.commit();learning.bootstrap_zopedia_learning_storage(c)
 records={'decision':{'title':'Decision','content':{'original':'unchanged'}}}
 def put(owner='owner',source='call'):
  return learning.enqueue_learning_episode(c,user_key=owner,namespace=namespace,source_id=source,question='Review original thesis',evidence_records=records)
 eid=put();c.commit();inputs=learning._recent_threads(c,limit=1);assert len(inputs)==1 and inputs[0]['episode']['event_id']==eid
 learning._update_learning_event_fields(c,eid,status='reviewed_no_change');c.commit()
 records['decision']['content']['original']='later changes cannot rewrite original'
 assert put()==eid;c.commit()
 with c.cursor() as cur:
  cur.execute('SELECT status,metadata_json FROM saa_zopedia_learning_events WHERE event_id=%s',(eid,));status,metadata=cur.fetchone()
 assert status=='reviewed_no_change' and metadata['evidence_records']['decision']['content']['original']=='unchanged'
 assert not learning._recent_threads(c,limit=1)
 namespace='dev';dev=put();other=put(owner='other');c.commit();assert len({eid,dev,other})==3
 selected=learning._recent_threads(c,limit=1);assert len(selected)==1 and selected[0]['episode']['metadata']['namespace']=='dev'
 namespace='';assert not learning._recent_threads(c,limit=25)
 print('Passed real PostgreSQL immutable feedback enqueue, completed-review retry, production empty namespace, owner/environment identity and shared worker limit checks.')
finally:
 c.rollback()
 with c.cursor() as cur:cur.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
 c.commit();c.close();print('Removed isolated schema',schema)
