"""Focused company-research replay from retained evidence, never current-market validation."""
from __future__ import annotations
import argparse,copy,hashlib,json,os,subprocess,sys,time
from dataclasses import replace
from pathlib import Path
APP=Path(__file__).resolve().parents[5]
sys.path.insert(0,str(APP))
parser=argparse.ArgumentParser()
parser.add_argument('tickers',nargs='+')
parser.add_argument('--forecast-packet',action='store_true')
parser.add_argument('--heldout',action='store_true')
parser.add_argument('--effort',choices=['low','high','max'])
parser.add_argument('--model',choices=['deepseek-v4-pro'])
parser.add_argument('--role',default='')
args=parser.parse_args()
configuration=json.loads(Path('/tmp/sn-trading-resume-worker-20261018.private.json').read_text())
for item in configuration['properties']['template']['containers'][0]['env']:
    if 'value' in item:os.environ[item['name']]=str(item['value'])
os.environ['TRADING_AGENT_ZOPEDIA_WRITE_POLICY']='none'
os.environ['LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE']=''
import pandas as pd
from services import trading_agent as trading,aql_zopedia_engine as engine
client=engine.load_aql_zopedia_llm_client(surface='trading_agent')
if args.model or args.effort:
    client=type(client)(replace(client.config,model=args.model or client.config.model,
        reasoning_effort=args.effort or client.config.reasoning_effort))
inputs=json.loads((APP/'documents/research/trading-calls/evidence/2026-10-16/inputs.json').read_text())['heldout' if args.heldout else 'production']
horizons=[]
for row in inputs['horizons']:
    context=row['context'];start=pd.Timestamp(row['asof_time_utc'])
    context['forecast_window']={'horizon':row['horizon_label'],'start_utc':start.isoformat(),'end_utc':(start+pd.Timedelta(days=trading._TRADING_AGENT_HORIZON_DAYS[row['horizon_key']])).isoformat()}
    prices={r['symbol']:r for r in inputs['recovered_source_prices']}
    for observation in context['market_opportunity_feed']:
        if observation['symbol'] in prices:observation['close']=prices[observation['symbol']]['close']
    horizons.append(({'key':row['horizon_key'],'label':row['horizon_label'],'column':context['controls']['selected_horizon_col']},context))
pool=trading._balanced_shared_research_pool(horizons,research_pool_limit=8)
context=trading._shared_research_context(horizons,ticker_contexts=pool,research_controls={})
identity={'git_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=APP,text=True).strip(),'trading_source_sha256':hashlib.sha256((APP/'services/trading_agent.py').read_bytes()).hexdigest(),'engine':engine.research_engine_revision(),'model':client.config.model}
for ticker in args.tickers:
    seed=next(r for r in pool if r['ticker']==ticker)
    prior=next(r for r in inputs['company_packages'] if r['ticker']==ticker)
    evidence=[{'source_kind':'retained_research','ticker':ticker,'answer_markdown':prior['answer_markdown'],'asof_time_utc':prior['asof_time_utc'],'generated_at_utc':prior['generated_at_utc'],'interpretation_note':'This is prior model analysis, not independent source corroboration. Reassess its economic conclusions using its dated facts and the supplied financial observations.'}]
    started=time.monotonic()
    mode=args.role or ('packet' if args.forecast_packet else 'source')
    os.environ['PIPELINE_RUN_ID']='trading-'+mode+'-replay-20261018-'+ticker.lower()
    query=trading._aql_ticker_query(context,seed)
    if args.forecast_packet:
        candidate=copy.deepcopy(trading._TRADING_AGENT_SCHEMA['properties']['candidates']['items'])
        for field in ['ticker','setup','evidence','suggested_horizon']:
            candidate['properties'].pop(field)
            candidate['required'].remove(field)
        candidate['properties']['horizon_key']={'type':'string','enum':seed['horizon_keys']}
        candidate['required'].append('horizon_key')
        schema={'type':'object','additionalProperties':False,'properties':{
            'setup':{'type':'string'},
            'economic_basis':{'type':'string','description':'One coherent business-to-common-share argument. State what changes in assets, obligations and cash flow and what remains unknown. This owns the economics reused by every horizon call.'},
            'evidence':{'type':'array','items':{'type':'string'},'maxItems':3},
            'calls':{'type':'array','items':candidate,'minItems':len(seed['horizon_keys']),'maxItems':len(seed['horizon_keys'])}},
            'required':['setup','economic_basis','evidence','calls']}
        query+='\n\nOwn the complete company forecast in one research artifact. Develop economic_basis first and make each horizon conclusion consistent with it. Return one call for each requested horizon_key. Long predicts an absolute higher share price and short a lower price, not relative index performance. Uncertainty must remain in assumptions rather than fabricated market expectations. A later ranking step will select these calls without rewriting their economic argument.'
        result=engine.run_aql_zopedia_structured_agent(query=query,instructions=trading.get_prompt(trading._TRADING_AGENT_SYSTEM_PROMPT),schema_name='company_owned_forecasts_probe',schema=schema,task='trading_agent',surface='trading_agent',llm_client=client,max_tool_calls=0,initial_evidence=evidence,request_context={'allow_web':False,'allowed_source_access':['supplied']},persist_findings=False,write_policy='none',inference_effort=args.effort or '')
    else:
        result=engine.run_aql_zopedia_agent(query=query,task='trading_agent',surface='trading_agent',llm_client=client,max_tool_calls=0,initial_evidence=evidence,request_context={'allow_web':False,'allowed_source_access':['supplied']},persist_findings=False,write_policy='none')
    receipt={**identity,'seconds':time.monotonic()-started,'ticker':ticker,'input_sha256':hashlib.sha256(json.dumps({'seed':seed,'context':context,'evidence':evidence},sort_keys=True).encode()).hexdigest(),'result':result}
    Path(__file__).with_name(mode+'-'+ticker.lower()+'.json').write_text(json.dumps(receipt,indent=2,default=str)+'\n')
    print(json.dumps({'ticker':ticker,'seconds':receipt['seconds'],'status':result.get('status')}),flush=True)
