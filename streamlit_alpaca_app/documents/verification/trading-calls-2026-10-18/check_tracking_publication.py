import sys,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from pipeline.jobs import main as jobs
from services import trading_call_tracking as tracking
call={'tracking_id':'qa','owner_id':'qa','candidate_id':'qa','ticker':'QA','stance':'long','horizon_key':'1w','horizon_label':'1 Week','horizon_days':7,'approved_at_utc':'2026-10-16T14:00:00Z','candidate_payload':{'hypothesis':'publication fixture'},'entry_payload':None,'completed_at_utc':None,'entry_policy':tracking.ENTRY_POLICY}
prices=pd.DataFrame([{'symbol':symbol,'timestamp':ts,'close':price} for symbol in ['QA','SPY'] for ts,price in [('2026-10-19T04:00:00Z',100.),('2026-10-26T04:00:00Z',110.)]])
class API:
 def get_market_calendar(self,**kwargs):return [{'date':'2026-10-16'},{'date':'2026-10-19'},{'date':'2026-10-26'},{'date':'2026-10-27'}]
 def get_stock_bars(self,*args,**kwargs):return {}
ctx=SimpleNamespace(asof=pd.Timestamp('2026-10-27T12:00:00Z'),published_datasets={})
events=[];published={}
def persist(name,frame,ctx,conn):
 events.append(name);published[name]=frame;ctx.published_datasets[name]='qa-'+name
 if name==tracking.DATASET and fail_publication:raise RuntimeError('simulated publication failure')
def pin(entries):events.append('pin');return entries
def complete(frame):
 assert tracking.DATASET in published
 events.append('complete')
def enqueue(frame,calls):
 assert tracking.DATASET in published
 events.append('enqueue')
with patch.object(tracking,'initialize_tracking'),patch.object(tracking,'approved_calls',return_value=[call]),patch.object(jobs,'load_latest_dataset_frame',return_value=(pd.DataFrame(),None)),patch.object(jobs,'_bars_to_price_history_frame',return_value=prices),patch.object(jobs,'_persist_dataset',side_effect=persist),patch.object(tracking,'pin_entries',side_effect=pin),patch.object(tracking,'complete_calls',side_effect=complete),patch.object(tracking,'enqueue_outcome_reviews',side_effect=enqueue),patch.object(jobs,'_job_progress'):
 fail_publication=True
 try:jobs.run_trading_call_tracking(ctx,api=API())
 except RuntimeError as exc:assert str(exc)=='simulated publication failure'
 else:raise AssertionError('Expected failed publication')
 assert 'complete' not in events
 fail_publication=False;events.clear();jobs.run_trading_call_tracking(ctx,api=API())
 assert events==[tracking.PRICE_DATASET,'pin',tracking.DATASET,'complete','enqueue']
 outcome=published[tracking.DATASET].iloc[0]
 assert abs(outcome.return_pct-10)<1e-8 and outcome.price_dataset_version=='qa-'+tracking.PRICE_DATASET
 assert outcome.status=='matured'
 frame=published[tracking.DATASET]
 call['completed_at_utc']=ctx.asof
 events.clear()
 with patch.object(jobs,'load_latest_dataset_frame',return_value=(frame,None)):
  jobs.run_trading_call_tracking(ctx,api=API())
 assert events==['enqueue'],events
 metadata=SimpleNamespace(asof_time_utc='2026-10-27T12:00:00Z')
 def projected(_metadata,columns,symbols=None):return frame[columns]
 with patch.object(tracking,'approved_calls',return_value=[call]):
  from services import pipeline_store as store
  with patch.object(store,'latest_dataset_metadata',return_value=metadata),patch.object(store,'load_dataset_frame',side_effect=projected):
   summary=tracking.tracking_view(owner_id='qa')['calls'][0]
   assert 'path' not in summary and 'candidate' not in summary
   detail=tracking.tracking_view(owner_id='qa',tracking_id='qa')['calls'][0]
   assert len(detail['path'])==2 and detail['candidate']==call['candidate_payload']
   try:tracking.tracking_view(owner_id='other',tracking_id='qa')
   except tracking.TrackingUnavailable:pass
   else:raise AssertionError('Expected materialized ownership mismatch to fail')
print('Passed driver publication ordering/retry, exact price-version binding, lightweight summaries, lazy detail serialization and materialized ownership checks.')
