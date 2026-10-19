"""Frozen Trading comparisons through the current shared engine; never a release source."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

APP = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(APP))
parser = argparse.ArgumentParser()
parser.add_argument('role')
parser.add_argument('horizons', nargs='+')
parser.add_argument('--review', action='store_true')
parser.add_argument('--heldout', action='store_true')
args = parser.parse_args()
configuration = json.loads(Path('/tmp/sn-trading-resume-worker-20261018.private.json').read_text())
for item in configuration['properties']['template']['containers'][0]['env']:
    if 'value' in item:
        os.environ[item['name']] = str(item['value'])
os.environ['TRADING_AGENT_ZOPEDIA_WRITE_POLICY'] = 'none'
os.environ['LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE'] = ''
from services import trading_agent as trading, aql_zopedia_engine as engine
client = engine.load_aql_zopedia_llm_client(surface='trading_agent.synthesis')
if client is None or 'deepseek' not in client.config.model.lower():
    raise RuntimeError('The configured cheap open-source inference client is unavailable.')
inputs = json.loads((APP/'documents/research/trading-calls/evidence/2026-10-16/inputs.json').read_text())['heldout' if args.heldout else 'production']
packages = [{**row, 'limitations': json.loads(row['limitations_json']), 'tool_calls': json.loads(row['tool_calls_json'])} for row in inputs['company_packages']]
prices = {row['symbol']: row for row in inputs['recovered_source_prices']}
original_operation = engine.run_aql_zopedia_structured_agent
operations = []
def recorded_operation(**kwargs):
    kwargs['request_context'] = {'allow_web': False, 'allowed_source_access': ['supplied']}
    kwargs['review_answer'] = args.review
    result = original_operation(**kwargs)
    operations.append(result)
    return result
engine.run_aql_zopedia_structured_agent = recorded_operation
identity = {'git_revision': subprocess.check_output(['git','rev-parse','HEAD'],cwd=APP,text=True).strip(),
            'trading_source_sha256': hashlib.sha256((APP/'services/trading_agent.py').read_bytes()).hexdigest(),
            'engine': engine.research_engine_revision(), 'model': client.config.model, 'provider': client.config.provider}
print(json.dumps(identity), flush=True)
for horizon in args.horizons:
    row = next(row for row in inputs['horizons'] if row['horizon_key'] == horizon)
    context = json.loads(json.dumps(row['context']))
    for observed in context['market_opportunity_feed']:
        source = prices.get(observed['symbol'])
        if source:
            if source['asof_time_utc'] != observed['asof_time_utc']:
                raise RuntimeError('Frozen quote is from another source observation.')
            observed['close'] = source['close']
    # Explicit dates are source data for both arms, not new current-web evidence.
    import pandas as pd
    start = pd.Timestamp(row['asof_time_utc'])
    context['forecast_window'] = {'horizon': row['horizon_label'], 'start_utc': start.isoformat(),
        'end_utc': (start+pd.Timedelta(days=trading._TRADING_AGENT_HORIZON_DAYS[horizon])).isoformat()}
    contexts, selected = trading._research_packages_for_horizon(context, packages)
    os.environ['PIPELINE_RUN_ID'] = f'trading-resume-20261018-{args.role}-{horizon}'
    operations.clear()
    started = time.monotonic()
    try:
        result = trading._synthesize_trading_agent_suggestions(context=context,ticker_contexts=contexts,ticker_packages=selected,llm_client=client)
    except Exception as exc:
        result = {'status':'failed','error':f'{type(exc).__name__}: {exc}'}
    receipt = {**identity, 'role':args.role, 'horizon':horizon, 'heldout':args.heldout, 'review':args.review,
        'input_sha256':hashlib.sha256(json.dumps({'context':context,'packages':selected},sort_keys=True).encode()).hexdigest(),
        'seconds':time.monotonic()-started, 'result':result, 'operations':operations}
    output = Path(__file__).parent/f'{args.role}-{horizon}.json'
    output.write_text(json.dumps(receipt,indent=2,default=str)+'\n')
    print(json.dumps({'horizon':horizon,'status':result.get('status'),'seconds':receipt['seconds'],
        'calls':[(c['ticker'],c['stance']) for c in result.get('candidates',[])],'error':result.get('error')}),flush=True)
