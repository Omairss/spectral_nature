import os,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
cfg=json.load(open('/tmp/sn-trading-resume-worker-20261018.private.json'))
for row in cfg['properties']['template']['containers'][0]['env']:
 if 'value' in row:os.environ[row['name']]=str(row['value'])
os.environ['PIPELINE_RUN_ID']='trading-feedback-review-20261019'
from services.zopedia_learning import critique_learning_event
from services.aql_zopedia_engine import load_aql_zopedia_llm_client
inputs=json.load(open('documents/research/trading-calls/evidence/2026-10-16/inputs.json'))['heldout']
package=inputs['company_packages'][0]
event={'event_id':'qa_feedback_review_20261019','user_key':'qa-review','trigger_type':'outcome_episode',
 'original_question':'Review the original investment reasoning alongside the illustrative paper outcome. Identify only reusable lessons supported by this record.',
 'metadata':{'evidence_records':{
  'decision':{'title':'Retained original company research','metadata':{'phase':'decision'},'content':package},
  'outcome':{'title':'Illustrative outcome for reviewer development','metadata':{'phase':'outcome','synthetic':True},'content':{'direction':'long','return_pct':10,'benchmark_return_pct':10,'excess_return_pct':0,'note':'Synthetic return, not a historical observation. No realized business metrics or evidence of which assumption materialized. Do not infer model accuracy from this fixture.'}}
 }}}
start=time.monotonic();result=critique_learning_event(event,llm_client=load_aql_zopedia_llm_client(surface='zopedia.learning_job',fallback_to_default=True))
Path('documents/verification/trading-calls-2026-10-18/feedback-critic.json').write_text(json.dumps({'elapsed_seconds':time.monotonic()-start,'fixture':event,'result':result},indent=2,default=str)+'\n')
print(json.dumps(result))
