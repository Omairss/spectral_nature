"""Resumable native forecasting evaluation through shared AQL/Zopedia.

Inference reads the frozen actor package only. This is not an on-time forecast
or a claim that later outcomes are absent from hosted model weights.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SCHEMA = {"type": "object", "properties": {"answer_markdown": {"type": "string"}},
          "required": ["answer_markdown"], "additionalProperties": False}
INSTRUCTIONS = (
    "Follow the original task prompt. Put its completed prediction table in answer_markdown. "
    "Treat the stated forecast deadline as the information cutoff. Do not use live web or serving memory."
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(protocol_path: Path, deployment_env: Path, *, preflight_only: bool = False) -> None:
    protocol = json.loads(protocol_path.read_text())
    app, root = Path(protocol['app']), Path(protocol['root'])
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=app, text=True).strip() != protocol['source_git']:
        raise ValueError('Frozen shared engine source changed')
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=app, text=True).strip():
        raise ValueError('Frozen shared engine source is modified')
    actor = root / 'actor/cases.jsonl'
    if sha(actor) != protocol['actor_sha256'] or sha(Path(__file__)) != protocol['runner_sha256']:
        raise ValueError('Frozen input or runner changed')
    if protocol.get('original_runner_sha256') != sha(Path(__file__).with_name('native_forecast.py')):
        raise ValueError('Original inference contract changed')
    # These are actor manifests and original failure receipts, never evaluator
    # records. A recovery must preserve the complete original attempt history.
    parent_manifest = protocol_path.parent / protocol['parent_manifest']
    if sha(parent_manifest) != protocol['parent_manifest_sha256']:
        raise ValueError('Parent attempt inventory changed')
    parent = json.loads(parent_manifest.read_text())
    parent_by_id = {row['id']: row for row in parent['attempts']}
    for identifier in protocol['selected_ids']:
        receipt = parent_by_id[identifier]
        if receipt['status'] != 'failed' or sha(Path(receipt['path'])) != receipt['sha256']:
            raise ValueError('Recovery does not refer to an unchanged failed first attempt')
    if preflight_only:
        print(json.dumps({'status': 'preflight_passed', 'condition': protocol['condition'],
                          'selected': len(protocol['selected_ids']), 'provider_calls': 0}))
        return
    os.environ['DEPLOYMENT_ENV_FILE'] = str(deployment_env.resolve())
    sys.path.insert(0, str(app))
    from services import llm
    from services.aql_zopedia_engine import run_aql_zopedia_structured_agent, research_engine_revision
    from services.llm_execution import LocalResearchExecution, model_call_context
    if research_engine_revision() != protocol['engine_revision']:
        raise ValueError('Loaded engine differs from protocol')
    execution = LocalResearchExecution(Path(protocol['accounting_root']),
        overrides=protocol['runtime_overrides'], budget_usd=protocol['budget_usd'],
        input_usd_per_million=protocol['input_usd_per_million'],
        output_usd_per_million=protocol['output_usd_per_million'],
        request_max_output_tokens=protocol['completion_tokens'],
        max_provider_attempts=protocol['max_provider_attempts'], retain_training_io=True,
        unknown_charge_policy='reserve_maximum')
    cases = {c['id']: c for c in map(json.loads, actor.read_text().splitlines())}
    output_dir = Path(protocol['output_root']); output_dir.mkdir(parents=True, exist_ok=True)
    with (Path(protocol['accounting_root']) / 'native-batch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for identifier in protocol['selected_ids']:
            case_dir = output_dir / hashlib.sha256(identifier.encode()).hexdigest()[:20]
            case_dir.mkdir(exist_ok=True)
            previous_paths = sorted(case_dir.glob('attempt-*.json'))
            previous = [json.loads(path.read_text()) for path in previous_paths]
            if any((item.get('result') or {}).get('status') == 'completed' for item in previous):
                print(json.dumps({'id': identifier, 'status': 'retained_completed_answer'}), flush=True)
                continue
            if previous:
                last_failure = (previous[-1].get('error') or {}).get('type') or (previous[-1].get('result') or {}).get('error_type')
                if last_failure not in protocol['retryable_execution_failures'] or len(previous) >= protocol['max_attempts_per_case']:
                    print(json.dumps({'id': identifier, 'status': 'retained_nonretryable_failure'}), flush=True)
                    continue
            output = case_dir / f'attempt-{len(previous) + 1:04d}.json'
            case = cases[identifier]
            query = json.dumps({'information_cutoff': case['report']['forecast_deadline'],
                                'original_task_prompt': case['report']['original_task_prompt']}, ensure_ascii=False)
            result, error = None, None
            started = time.monotonic()
            condition_hash = hashlib.sha256(protocol['condition'].encode()).hexdigest()[:10]
            with execution.activate(), model_call_context(run_id='agrun_findeep_' + condition_hash + '_' + output.stem):
                try:
                    result = run_aql_zopedia_structured_agent(query=query, instructions=INSTRUCTIONS,
                        schema_name='native_forecast_table', schema=SCHEMA,
                        task='external_benchmark', surface='research.quantitative.external_benchmark',
                        llm_client=llm.load_llm_client(), max_tool_calls=0, review_answer=False,
                        persist_findings=False, write_policy='none', call_type='research_grade',
                        run_timeout_seconds=protocol['timeout_seconds'],
                        llm_step_timeout_seconds=protocol['timeout_seconds'],
                        request_context={'allow_web': False, 'allowed_tool_names': [], 'allowed_source_access': ['supplied']})
                except Exception as exc:
                    error = {'type': type(exc).__name__, 'message': str(exc)}
            value = {'id': identifier, 'benchmark': 'findeepforecast', 'condition': protocol['condition'],
                     'attempt_number': len(previous) + 1,
                     'previous_attempt_sha256': sha(previous_paths[-1]) if previous_paths else None,
                     'primary_attempt_sha256': parent_by_id[identifier]['sha256'],
                     'issued_at_utc': datetime.now(timezone.utc).isoformat(),
                     'protocol_sha256': sha(protocol_path), 'input_sha256': hashlib.sha256(query.encode()).hexdigest(),
                     'result': result, 'error': error, 'elapsed_seconds': time.monotonic() - started}
            temporary = output.with_suffix('.tmp')
            temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + '\n')
            temporary.replace(output)
            summary = execution.summary()
            (output_dir / 'accounting-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
            print(json.dumps({'id': identifier, 'status': (result or {}).get('status', 'failed'),
                              'error': error, 'accounted_upper_bound_usd': summary.get('accounted_spend_upper_bound_usd')}), flush=True)
            # The shared agent returns provider failures in its result envelope.
            # Do not depend on an exception escaping the shared engine: that caused
            # the first condition to sweep all remaining tasks after HTTP 402.
            failure_type = (error or {}).get('type') or (result or {}).get('error_type')
            if failure_type in {'ResearchSpendExceeded', 'LLMProviderUnavailable', 'LLMAPIError'} or summary.get('inference_blocked_by_unknown_charge'):
                (case_dir / f'halt-{len(previous) + 1:04d}.json').write_text(json.dumps({
                    'id': identifier, 'condition': protocol['condition'], 'error_type': failure_type,
                    'result_error': (result or {}).get('error'), 'raised_error': error,
                    'remaining_not_attempted': len(protocol['selected_ids']) - protocol['selected_ids'].index(identifier) - 1,
                    'created_at_utc': datetime.now(timezone.utc).isoformat(),
                }, indent=2) + '\n')
                break


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', required=True, type=Path)
    parser.add_argument('--deployment-env', required=True, type=Path)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    run(args.protocol, args.deployment_env, preflight_only=args.preflight_only)
