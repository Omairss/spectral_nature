"""Real local HTTP: body progress must not extend a model-call deadline."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from services import llm
from services.llm_execution import LocalResearchExecution, provider_post


@contextmanager
def wire_server(mode):
    state = {'requests':0, 'disconnected':threading.Event()}
    body = json.dumps({'model':'wire-fixture','usage':{'prompt_tokens':1,'completion_tokens':1},
        'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}).encode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            state['requests'] += 1
            try:
                if mode=='before_headers':
                    time.sleep(1.2)
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)+(40 if mode=='keepalive' else 0)))
                self.end_headers()
                if mode=='keepalive':
                    for _ in range(40):
                        self.wfile.write(b'\n')
                        self.wfile.flush()
                        time.sleep(.03)
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):
                state['disconnected'].set()

    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker = threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}',state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize('mode',['before_headers','keepalive'])
def test_absolute_deadline_interrupts_wire_and_retains_unknown_usage(tmp_path,mode):
    with wire_server(mode) as (url,state):
        config=llm.LLMConfig(provider='local',api_key='',model='wire-fixture',base_url=url,
                             timeout_seconds=2)
        client=llm.LocalChatJSONClient(config)
        execution=LocalResearchExecution(tmp_path,overrides={},budget_usd=0,
            input_usd_per_million=0,output_usd_per_million=0,cost_basis='local_resources_unpriced')
        t0=time.monotonic()
        with execution.activate():
            with pytest.raises(llm.LLMDeadlineExceeded):
                # Exercise the actual default transport through its shared
                # provider owner, rather than simulating requests.Response.
                provider_post(client,url=url,headers={},json={'model':'wire-fixture','max_tokens':7},timeout=.3)
        elapsed=time.monotonic()-t0
        assert elapsed < .8, f'{mode} extended .3-second deadline to {elapsed:.3f}s'
        rows=[json.loads(l) for l in (tmp_path/'provider_attempts.jsonl').read_text().splitlines()]
        assert [r['status'] for r in rows]==['started','failure']
        assert rows[-1]['usage'] is None
        assert rows[-1]['metadata']['error_type']=='LLMDeadlineExceeded'
        assert len(rows[0]['metadata']['input_hash'])==64
        assert rows[0]['metadata']['input_hash']==rows[-1]['metadata']['input_hash']
        assert rows[0]['metadata']['max_output_tokens']==7
        assert len(execution.summary()['unknown_attempts'])==1
        assert state['requests']==1
        if mode=='keepalive':
            assert state['disconnected'].wait(1), 'Timed-out connection remained open'
        assert not any(t.name.startswith('asyncio-portal-') for t in threading.enumerate())


def test_default_transport_preserves_complete_response_and_usage(tmp_path):
    with wire_server('fast') as (url,state):
        config=llm.LLMConfig(provider='local',api_key='',model='wire-fixture',base_url=url,
                             timeout_seconds=2)
        client=llm.LocalChatJSONClient(config)
        execution=LocalResearchExecution(tmp_path,overrides={},budget_usd=0,
            input_usd_per_million=0,output_usd_per_million=0,cost_basis='local_resources_unpriced')
        with execution.activate():
            response=provider_post(client,url=url,headers={},json={'model':'wire-fixture'},timeout=2)
        assert response.status_code==200
        assert response.json()['choices'][0]['message']['content']=='{}'
        rows=[json.loads(l) for l in (tmp_path/'provider_attempts.jsonl').read_text().splitlines()]
        assert rows[-1]['status']=='success'
        assert rows[-1]['usage']=={'prompt_tokens':1,'completion_tokens':1}
        assert not execution.summary()['unknown_attempts']
        assert state['requests']==1


@pytest.mark.parametrize('client_type',[llm.OpenAIChatJSONClient,llm.AzureOpenAIChatJSONClient,
                                      llm.DeepSeekChatJSONClient,llm.LocalChatJSONClient])
def test_default_json_clients_do_not_retry_absolute_deadline(tmp_path,client_type):
    with wire_server('keepalive') as (url,state):
        config=llm.LLMConfig(provider='local',api_key='loopback-fixture',model='wire-fixture',
            base_url=url,deployment='fixture',timeout_seconds=2)
        client=client_type(config)
        execution=LocalResearchExecution(tmp_path,overrides={},budget_usd=0,
            input_usd_per_million=0,output_usd_per_million=0,cost_basis='local_resources_unpriced')
        t0=time.monotonic()
        with execution.activate():
            with pytest.raises(llm.LLMDeadlineExceeded), llm.llm_request_deadline(time.monotonic()+.3):
                client.generate_json(system_prompt='Transport fixture',user_prompt='No model inference',
                    schema_name='fixture',schema={'type':'object'})
        assert time.monotonic()-t0 < .8
        assert state['requests']==1
        assert len(execution.summary()['unknown_attempts'])==1
        rows=[json.loads(l) for l in (tmp_path/'provider_attempts.jsonl').read_text().splitlines()]
        assert [r['status'] for r in rows]==['started','failure']
        assert rows[-1]['metadata']['error_type']=='LLMDeadlineExceeded'
        assert state['disconnected'].wait(1)
