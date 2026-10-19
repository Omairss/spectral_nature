import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import json
import pandas as pd
from services.trading_call_tracking import build_approved_outcomes,completed_daily_prices
call={'tracking_id':'qa','owner_id':'qa','candidate_id':'qa','ticker':'CHECK','stance':'long','horizon_key':'1w','horizon_label':'1 Week','horizon_days':7,'approved_at_utc':'2026-10-16T14:00:00Z','candidate_payload':{'hypothesis':'calculation fixture'},'entry_payload':None,'completed_at_utc':None}
calendar=['2026-10-16','2026-10-19','2026-10-20','2026-10-21','2026-10-22','2026-10-23','2026-10-26']
prices=pd.DataFrame([{'symbol':s,'timestamp':d,'close':p} for s,pts in {'CHECK':[('2026-10-16T04:00:00Z',80),('2026-10-19T04:00:00Z',100),('2026-10-20T04:00:00Z',105),('2026-10-21T04:00:00Z',120),('2026-10-26T04:00:00Z',110)],'SPY':[('2026-10-19T04:00:00Z',100),('2026-10-20T04:00:00Z',101),('2026-10-21T04:00:00Z',110),('2026-10-26T04:00:00Z',102)]}.items() for d,p in pts])
f,entries=build_approved_outcomes(calls=[call],prices=prices,previous=pd.DataFrame(),trading_days=calendar,as_of='2026-10-21T12:00:00Z',price_version='fixture-v1')
r=f.iloc[0];assert abs(r.return_pct-5)<1e-8;assert abs(r.benchmark_return_pct-1)<1e-8;assert r.status=='open';assert entries['qa']['session_date']=='2026-10-19';assert len(json.loads(r.path_json))==2
call['entry_payload']=entries['qa'];split=prices.copy();split['close']=split['close'].astype(float);split.loc[split.symbol.eq('CHECK'),'close']*=0.5
split.loc[split.symbol.eq('CHECK') & split.timestamp.eq('2026-10-20T04:00:00Z'),'close']=55
f,_=build_approved_outcomes(calls=[call],prices=split,previous=pd.DataFrame(),trading_days=calendar,as_of='2026-10-21T12:00:00Z',price_version='fixture-v2');assert abs(f.iloc[0].return_pct-10)<1e-8;assert f.iloc[0].entry_close==100
call['stance']='short';f,_=build_approved_outcomes(calls=[call],prices=prices,previous=pd.DataFrame(),trading_days=calendar,as_of='2026-10-27T12:00:00Z',price_version='fixture-v3');assert f.iloc[0].status=='matured';assert abs(f.iloc[0].return_pct+10)<1e-8;assert abs(f.iloc[0].excess_return_pct+8)<1e-8
missing=prices[~(prices.symbol.eq('SPY') & prices.timestamp.eq('2026-10-19T04:00:00Z'))];f,_=build_approved_outcomes(calls=[call],prices=missing,previous=pd.DataFrame(),trading_days=calendar,as_of='2026-10-21T12:00:00Z',price_version='fixture-v4');assert pd.isna(f.iloc[0].benchmark_return_pct)
dst=pd.DataFrame([{'symbol':'CHECK','timestamp':'2026-11-01T04:00:00Z','close':100}]);assert len(completed_daily_prices(dst,'2026-11-02T04:30:00Z'))==0;assert len(completed_daily_prices(dst,'2026-11-02T05:00:00Z'))==1
print('Passed approval timing, future-data exclusion, weekend entry, consistent split adjustment, exact benchmark alignment, short returns, maturity and DST checks.')

# Missing an expected market session must never shift the entry or exit to a later price.
no_entry=prices[~(prices.symbol.eq('CHECK') & prices.timestamp.eq('2026-10-19T04:00:00Z'))]
f,entries=build_approved_outcomes(calls=[{**call,'entry_payload':None}],prices=no_entry,previous=pd.DataFrame(),as_of='2026-10-21T12:00:00Z',price_version='qa-missing-entry',trading_days=calendar)
assert f.iloc[0].status=='price_unavailable' and not entries
no_exit=prices[~(prices.symbol.eq('CHECK') & prices.timestamp.eq('2026-10-26T04:00:00Z'))]
f,_=build_approved_outcomes(calls=[call],prices=no_exit,previous=pd.DataFrame(),as_of='2026-10-27T12:00:00Z',price_version='qa-missing-exit',trading_days=calendar)
assert f.iloc[0].status=='price_unavailable' and f.iloc[0].scheduled_exit_date=='2026-10-26'
assert f.iloc[0].last_price_date=='2026-10-21'
print('Passed fixed calendar-session entry/exit and missing-price handling.')

# Research-generation diagnostics use the publication clock rather than the older
# market snapshot. They remain distinct from the approval-time paper ledger.
from services.trading_agent import build_trading_agent_outcomes
candidate=pd.DataFrame([{'candidate_id':'new','ticker':'CHECK','stance':'short','direction':'short','research_status':'forecast','horizon_key':'1w','asof_time_utc':'2026-10-15T12:00:00Z','generated_at_utc':'2026-10-16T19:00:00Z','candidate_json':'{"narrative_contract_version":"directional_price_calls_v3"}'}])
f=build_trading_agent_outcomes(candidates=candidate,price_history=prices,evaluation_time_utc='2026-10-21T12:00:00Z')
assert f.iloc[0].entry_close==100 and f.iloc[0].exit_close==105
assert abs(f.iloc[0].signed_return_pct+5)<1e-8 and f.iloc[0].research_status=='forecast'
print('Passed actual publication-clock entry, completed-bar cutoff and directional-contract preservation for research diagnostics.')
