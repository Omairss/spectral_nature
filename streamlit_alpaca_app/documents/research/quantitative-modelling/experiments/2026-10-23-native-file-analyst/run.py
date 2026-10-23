"""Native-file tasks through the shared Zopedia agent, without evaluator access."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

INSTRUCTIONS = (
    "Complete the original task using the supplied files and available tools. "
    "Inspect the inputs, execute the work and save requested native deliverables under /workspace/output. "
    "Preserve unrelated original workbook contents and check the saved outputs. "
    "If required information is unavailable, explain what is missing rather than inventing data."
)
SCHEMA = {"type": "object", "properties": {
    "answer_markdown": {"type": "string"},
    "output_files": {"type": "array", "items": {"type": "string"}}},
    "required": ["answer_markdown", "output_files"], "additionalProperties": False}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(store, output):
    rows = []
    for path in sorted(store.glob('*/task.json'), key=lambda p: int(p.parent.name)):
        task = json.loads(path.read_text())
        files = []
        for name in task['source_files']:
            source = path.parent / 'actor' / name
            if source.is_symlink() or source.resolve().parent != (path.parent / 'actor').resolve():
                raise ValueError('Declared source escapes actor directory')
            files.append({'name': name, 'bytes': source.stat().st_size, 'sha256': sha(source)})
        rows.append({'id': task['id'], 'instruction': task['instruction_en'],
                     'constraints': task['task_constraints'], 'files': files})
    with output.open('x') as stream:
        json.dump(rows, stream, indent=2, ensure_ascii=False)
    print(json.dumps({'cases': len(rows), 'files': sum(len(r['files']) for r in rows),
                      'actor_sha256': sha(output)}))


def run(protocol_path, env):
    protocol = json.loads(protocol_path.read_text())
    app, root = Path(protocol['app']), Path(protocol['root'])
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=app, text=True).strip() != protocol['source_git']:
        raise ValueError('Frozen shared source changed')
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=app, text=True).strip():
        raise ValueError('Frozen shared source is modified')
    if sha(__file__) != protocol['runner_sha256'] or sha(protocol['actor_manifest']) != protocol['actor_sha256']:
        raise ValueError('Runner or actor package changed')
    os.environ['DEPLOYMENT_ENV_FILE'] = str(env.resolve())
    sys.path.insert(0, str(app))
    from data_access import DataAccessLayer, QueryService
    from services import llm
    from services.analysis_files import FileAnalysisWorkspace
    from services.aql_zopedia_engine import research_engine_revision, run_aql_zopedia_structured_agent
    from services.llm_execution import LocalResearchExecution, model_call_context
    if research_engine_revision() != protocol['engine_revision']:
        raise ValueError('Loaded shared engine differs from protocol')
    root.mkdir(parents=True, exist_ok=True)
    execution = LocalResearchExecution(root / 'accounting', overrides=protocol['runtime_overrides'],
        budget_usd=protocol['budget_usd'], input_usd_per_million=protocol['input_usd_per_million'],
        output_usd_per_million=protocol['output_usd_per_million'],
        request_max_output_tokens=protocol['completion_tokens'],
        max_provider_attempts=protocol['max_provider_attempts'], retain_training_io=True,
        unknown_charge_policy='reserve_maximum')
    @dataclass(frozen=True)
    class FileService(QueryService):
        def list_capabilities(self):
            return {'datasets': {}, 'charts': {}}
        def fetch_dataset(self, *args, **kwargs):
            raise ValueError('This native-file condition has no external dataset connection')
    cases = {r['id']: r for r in json.loads(Path(protocol['actor_manifest']).read_text())}
    stop = threading.Event()
    def investigate(identifier):
        folder = root / 'predictions' / identifier
        output = folder / 'first.json'
        if output.exists():
            return {'id': identifier, 'status': 'retained_first_attempt'}
        if stop.is_set():
            return {'id': identifier, 'status': 'not_started'}
        folder.mkdir(parents=True, exist_ok=True)
        case = cases[identifier]
        files = {}
        for record in case['files']:
            file = Path(protocol['actor_store']) / identifier / 'actor' / record['name']
            if sha(file) != record['sha256']:
                raise ValueError('Frozen native input changed')
            files[record['name']] = file
        result = error = None
        started = time.monotonic()
        run_id = 'agrun_native_files_' + protocol['condition'] + '_' + identifier
        with execution.activate(), model_call_context(run_id=run_id):
            try:
                with FileAnalysisWorkspace(files=files, artifact_root=folder / 'workspace',
                    image=protocol['image_id'], docker_context=protocol['docker_context'], memory_mb=1024) as workspace:
                    with workspace.activate():
                        result = run_aql_zopedia_structured_agent(
                            query=json.dumps(case, ensure_ascii=False), instructions=INSTRUCTIONS,
                            schema_name='native_file_deliverables', schema=SCHEMA,
                            task='external_benchmark', surface='research.quantitative.native_files',
                            service=FileService(DataAccessLayer()), llm_client=llm.load_llm_client(),
                            max_tool_calls=24, review_answer=False, persist_findings=False, write_policy='none',
                            call_type='research_grade', force_refresh=True,
                            run_timeout_seconds=1800, llm_step_timeout_seconds=480, tool_call_timeout_seconds=150,
                            request_context={'allow_web': False, 'allowed_tool_names': [
                                'tools.search', 'system.capabilities', 'analysis.run_files'],
                                'allowed_source_access': ['supplied']},
                            progress_callback=lambda event: execution.record('progress', {'id': identifier, 'event': event}))
            except Exception as exc:
                error = {'type': type(exc).__name__, 'message': str(exc)}
        receipt = {'id': identifier, 'condition': protocol['condition'], 'run_id': run_id,
                   'issued_at_utc': datetime.now(timezone.utc).isoformat(),
                   'protocol_sha256': sha(protocol_path), 'input_sha256': hashlib.sha256(
                       json.dumps(case, ensure_ascii=False).encode()).hexdigest(),
                   'result': result, 'error': error, 'elapsed_seconds': time.monotonic() - started,
                   'assessment': 'pending_primary_agent_review'}
        with output.open('x') as stream:
            json.dump(receipt, stream, indent=2, ensure_ascii=False, default=str)
        summary = execution.summary()
        failure = (error or {}).get('type') or (result or {}).get('error_type')
        if failure in {'LLMAPIError', 'LLMProviderUnavailable', 'ResearchSpendExceeded'}:
            stop.set()
        return {'id': identifier, 'status': (result or {}).get('status', 'failed'), 'error': error,
                'accounted_upper_bound_usd': summary['accounted_spend_upper_bound_usd']}
    with (root / 'batch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with ThreadPoolExecutor(max_workers=protocol['concurrency']) as pool:
            for future in as_completed([pool.submit(investigate, i) for i in protocol['selected_ids']]):
                print(json.dumps(future.result()), flush=True)
        (root / 'accounting-summary.json').write_text(json.dumps(execution.summary(), indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'run'])
    parser.add_argument('--store', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--protocol', type=Path)
    parser.add_argument('--deployment-env', type=Path)
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare(args.store, args.output)
    else:
        run(args.protocol, args.deployment_env)
