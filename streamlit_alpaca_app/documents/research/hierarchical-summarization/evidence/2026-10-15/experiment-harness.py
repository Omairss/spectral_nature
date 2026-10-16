"""Isolated cheap experiments through the shared engine; no product changes."""
import argparse,copy,hashlib,json,os,pathlib,time,uuid,sys
sys.path.insert(0,"/tmp/sn-reliability-20261015/experiment-source-stable")
from contextlib import ExitStack
from unittest.mock import patch
from dotenv import dotenv_values
for file in ['infra/.generated/deployment.local.env','.env']:
 for key,value in dotenv_values(file).items():
  if value is not None:os.environ.setdefault(key,value)
os.environ['APP_TRACK']='hierarchy-lab-20261015';os.environ['PIPELINE_DATASET_NAMESPACE']='development'
from services import pipeline_store,zopedia_agent
from services.agent_tools import ToolRegistry, _research_tools
from services.aql_zopedia_engine import run_aql_zopedia_agent,run_aql_zopedia_structured_agent
from services.llm_flow import capture_research_configuration,research_configuration,REVIEW_CYCLES
from services.llm_execution import research_run_context
from services.llm import llm_request_deadline
root=pathlib.Path('/tmp/sn-reliability-20261015/experiments')
def load(name):return json.loads((root/name).read_text())
corpora={
 'technical':load('technical.sources.json'),
 'financial':load('financial.sources.json'),
 'database':[load(x+'.source.json') for x in ['pooling','postgres','azure']],
 'microsoft':[load('microsoft.source.json')],
 'fed':[load('fed.source.json')],
 'summarization':[load('raptor.source.json')],
}
questions={
 'technical':[
  'Our client enforces a 75-second total request deadline. DeepSeek kept sending blank lines and then our connection closed at 75 seconds. What do these observations establish about why it closed, and what should we inspect next?',
  'A complete non-streaming JSON body arrived 70 seconds after submission. Does that tell us how long inference took? Explain the useful competing explanations and observations that could distinguish them.'],
 'financial':[
  'Explain how this manufacturer changed between the supplied years and assess the analyst memo. Give a useful next investigation.',
  'Could the supplied earnings and cash-flow observations support different business interpretations? Develop useful hypotheses and explain what would change your assessment.'],
 'database':[
  'We use Azure PostgreSQL Burstable B1ms, max_connections=50 and superuser_reserved_connections=10. Several research workers open connections before lengthy remote calls. Suggest practical reliability changes using the current paid tier.',
  'On Azure PostgreSQL B1ms with max_connections=50 and superuser_reserved_connections=10, we have four worker processes and each creates a pool with max_size=10. Does that establish adequate capacity on the stated server? Explain concurrency, idle resources and the tradeoffs of waiting versus raising connection limits.'],
 'microsoft':[
  'Using the dated earnings release, explain what drove Microsoft performance and which distinctions matter between reported growth, constant currency and forward-looking commentary.',
  'As of this earnings release, what useful connections can an investor investigate between cloud demand, AI infrastructure and cash generation? Separate reported results from plausible implications.'],
 'fed':[
  'Describe the policy change in this dated FOMC statement and the balance of risks it expresses. What does it establish about future decisions?',
  'A colleague says this statement guarantees a continuing sequence of rate cuts. Assess that interpretation and describe useful implications without treating forecasts as certain.'],
 'summarization':[
  'What does this paper demonstrate about hierarchical retrieval, and what would we still need to evaluate before adopting it in a financial research agent?',
  'How does the method preserve access across different abstraction levels, and which experimental results bear on choosing a fixed summary depth for every question?'],
}
questions['database'].append('Compare NullConnectionPool with ConnectionPool(min_size=0), including what happens when several clients wait and a connection is returned. What does max_size bound, and what remains outside that bound?')
questions['summarization'].append('Reconcile the QuALITY controlled comparison and state-of-the-art comparison in this paper: what are the GPT-3 RAPTOR and DPR results, the GPT-4 RAPTOR and CoLISA test and hard-subset results, and how should we interpret the size and cause of the gains? Use the layer ablation to discuss limits on generalization.')
p=argparse.ArgumentParser();p.add_argument('--domains',default='technical,financial,microsoft,fed');p.add_argument('--question',type=int,default=0);p.add_argument('--variant',choices=['flat','hierarchy','guided_hierarchy','adaptive','summary_only'],required=True);p.add_argument('--repeat',default='a');args=p.parse_args()
snapshot=capture_research_configuration();snapshot['overrides'][REVIEW_CYCLES]='0';snapshot['overrides'][zopedia_agent._P_DEFERRED_TOOL_DISCOVERY_ENABLED]='0'
for route in snapshot['flow']['routes'].values():route.update(profile='economy',thinking='none',timeout_seconds=90,max_output_tokens=4096)
snapshot['flow']['models']['economy']['model']='deepseek-flash';snapshot['flow']['reviewers']=[]
receipts=[];original_record=pipeline_store.record_provider_attempt
attempt_file=root/'attempts.private.jsonl'
def observe(event,**kwargs):
 receipts.append(event)
 with attempt_file.open('a') as file:file.write(json.dumps(event,default=str)+'\n')
 return original_record(event,**kwargs)
def write(name,value):
 path=root/name;path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str));path.chmod(0o600)
def summary(sources,level):
 contract={'sources':sources,'level':level,'model':'deepseek-flash','version':1,'engine_source_snapshot':'3018e160f1e017c92690e2dd653173441d842753f32f637612760bbf85b385d9'}
 key=hashlib.sha256(json.dumps(contract,sort_keys=True).encode()).hexdigest();path=root/('summary-'+key+'.private.json')
 if path.exists():return load(path.name)['result']['payload']['summary']
 start=time.monotonic();result=run_aql_zopedia_structured_agent(
  query=json.dumps({'sources':sources},ensure_ascii=False),instructions='Prepare a useful navigational summary of the supplied evidence. Preserve dates, numerical comparisons, conditions, disagreements, task-premise origins and potentially useful indirect relationships. Distinguish source reports from inference and uncertainty. Refer to source identities so an analyst can return to originals. A summary is a derived view, not independent corroboration. Organize by meaning without forcing a fixed domain template. Do not answer an unstated question.',
  schema_name='hierarchy_experiment_summary',schema={'type':'object','properties':{'summary':{'type':'string'}},'required':['summary'],'additionalProperties':False},
  stage='summary',task='hierarchy.experiment.summary',surface='hierarchy.experiment',max_tool_calls=0,persist_findings=False,write_policy='none',run_timeout_seconds=90)
 write(path.name,{'contract':contract,'result':result,'seconds':time.monotonic()-start})
 if result.get('status')!='completed':raise RuntimeError('Summary did not complete: '+str(result.get('error')))
 return result['payload']['summary']
with patch.object(pipeline_store,'record_provider_attempt',side_effect=observe),research_configuration(snapshot):
 for domain in args.domains.split(','):
  name=f'{domain}-{args.question}-{args.variant}-{args.repeat}.private.json'
  if (root/name).exists():print('retained',name,flush=True);continue
  originals=copy.deepcopy(corpora[domain]);question=questions[domain][args.question]
  nodes={str(row['source_id']):row for row in originals}
  summary_start=len(receipts)
  if args.variant!='flat':
   leaves=[]
   for source in originals:
    content=summary([source],'source');leaf={'source_id':'summary:'+str(source['source_id']),'title':source['title'],'chunk_text':content,'source_kind':'derived_summary','derived_from_source_ids':[source['source_id']]};nodes[leaf['source_id']]=leaf;leaves.append(leaf)
   overview=summary(leaves,'overview') if len(leaves)>1 else leaves[0]['chunk_text']
   initial=[{'source_id':'overview:'+domain,'title':'Navigational overview with originals available','chunk_text':overview,'source_kind':'derived_summary','derived_from_source_ids':[s['source_id'] for s in originals]}, {'source_id':'catalog:'+domain,'source_kind':'source_catalog','entries':[{'source_id':key,'title':value['title'],'source_kind':value['source_kind']} for key,value in nodes.items()]}]
  else:initial=originals
  if args.variant=='guided_hierarchy':
   initial.append({'source_id':'navigation-policy','source_kind':'navigation_instructions','chunk_text':'The overview and source summaries are optional navigation aids and may omit details or contain mistakes. Choose whichever abstraction levels help answer this question. Consult retained original documents when the answer would depend on information missing from a summary, or when a consequential calculation, causal claim, qualification or apparent disagreement needs resolution. You may finish directly when the available evidence is sufficient. A summary does not independently corroborate its originals.'})
  navigation={}
  if args.variant=='adaptive':
   initial=[initial[1],initial[0]]
   navigation={'evidence_navigation':'The catalog names retained originals and derived summaries. Summaries are optional navigation aids; they may omit detail or contain mistakes. Choose levels useful for this question. Read retained originals when an important calculation, qualification, disagreement or explanation needs resolution. You can finish without more reads when existing evidence is sufficient. Summaries are not independent corroboration.'}
  construction_receipts=receipts[summary_start:];tool_reads=[]
  catalog=[{'name':'research.search_evidence','description':'List all retained original documents and summary nodes for this supplied corpus. All nodes remain discoverable.','inputSchema':{'type':'object','properties':{'query':{'type':'string'}}},'knowledge_effect':'read','source_access':'retained'},
   {'name':'research.read_source','description':'Read a complete original or summary by its catalog source_id. Originals remain available even if omitted from an overview.','inputSchema':{'type':'object','properties':{'source_id':{'type':'string'}},'required':['source_id']},'knowledge_effect':'read','source_access':'retained'}]
  catalog.append(next(tool for tool in _research_tools() if tool['name']=='research.read_context'))
  def invoke(*,tool_name,arguments=None,**kwargs):
   arguments=arguments or {};key=arguments.get('source_id');selected=nodes.get(key)
   if tool_name=='research.search_evidence':payload={'catalog':[{'source_id':k,'title':v['title'],'source_kind':v['source_kind']} for k,v in nodes.items()]}
   else:
    tool_reads.append(key);payload={'source':selected,'error':None if selected else 'Unknown source identity'}
   return {'request':{'name':tool_name,'params':arguments},'result_type':'research','payload':payload,'llm_context_text':json.dumps(payload,ensure_ascii=False),'summary':{'text':str(key or 'Complete source catalog')}}
  start=time.monotonic();root_run='hierarchy_'+uuid.uuid4().hex[:15];before=len(receipts)
  with patch.object(zopedia_agent,'build_tool_registry',return_value=ToolRegistry(catalog)),patch.object(zopedia_agent,'invoke_tool',side_effect=invoke),research_run_context(root_run),llm_request_deadline(start+240):
   result=run_aql_zopedia_agent(query=question,initial_evidence=initial,service=object(),task='hierarchy.experiment.answer',surface='hierarchy.experiment',max_tool_calls=0 if args.variant=='summary_only' else 4,persist_findings=False,write_policy='none',run_timeout_seconds=240,llm_step_timeout_seconds=90,request_context={'allow_web':False,**navigation})
  record={'catalog_version':2,'engine_source_snapshot':'3018e160f1e017c92690e2dd653173441d842753f32f637612760bbf85b385d9','domain':domain,'question':question,'variant':args.variant,'repeat':args.repeat,'original_characters':sum(len(s['chunk_text']) for s in originals),'initial_characters':len(json.dumps(initial)),'tool_reads':tool_reads,'construction_attempts':construction_receipts,'answer_attempts':receipts[before:],'seconds':time.monotonic()-start,'result':result}
  write(name,record);print(json.dumps({'case':name,'status':result.get('status'),'reads':tool_reads,'attempts':len(receipts)-before,'seconds':round(record['seconds'],1)}),flush=True)
