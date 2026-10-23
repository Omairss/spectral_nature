"""Retrospective native forecasting-format checks through shared AQL/Zopedia.

Inference reads the frozen actor package only. This is not an on-time forecast
or a claim that later outcomes are absent from hosted model weights.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
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


def run(protocol_path: Path, deployment_env: Path) -> None:
    protocol = json.loads(protocol_path.read_text())
    app, root = Path(protocol['app']), Path(protocol['root'])
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=app, text=True).strip() != protocol['source_git']:
        raise ValueError('Frozen shared engine source changed')
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=app, text=True).strip():
        raise ValueError('Frozen shared engine source is modified')
    actor = root / 'actor/cases.jsonl'
    if sha(actor) != protocol['actor_sha256'] or sha(Path(__file__)) != protocol['runner_sha256']:
        raise ValueError('Frozen input or runner changed')
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
    for identifier in protocol['selected_ids']:
        output = output_dir / (hashlib.sha256(identifier.encode()).hexdigest()[:20] + '.json')
        if output.exists():
            print(json.dumps({'id': identifier, 'status': 'retained_existing_attempt'}), flush=True)
            continue
        case = cases[identifier]
        query = json.dumps({'information_cutoff': case['report']['forecast_deadline'],
                            'original_task_prompt': case['report']['original_task_prompt']}, ensure_ascii=False)
        result, error = None, None
        started = time.monotonic()
        with execution.activate(), model_call_context(run_id='agrun_findeep_' + output.stem):
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
                 'issued_at_utc': datetime.now(timezone.utc).isoformat(),
                 'protocol_sha256': sha(protocol_path), 'input_sha256': hashlib.sha256(query.encode()).hexdigest(),
                 'result': result, 'error': error, 'elapsed_seconds': time.monotonic() - started}
        output.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + '\n')
        summary = execution.summary()
        (output_dir / 'accounting-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps({'id': identifier, 'status': (result or {}).get('status', 'failed'),
                          'error': error, 'accounted_upper_bound_usd': summary.get('accounted_spend_upper_bound_usd')}), flush=True)
        if error and error['type'] in {'ResearchSpendExceeded', 'LLMProviderUnavailable'}:
            break
        if summary.get('inference_blocked_by_unknown_charge'):
            break


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', required=True, type=Path)
    parser.add_argument('--deployment-env', required=True, type=Path)
    args = parser.parse_args()
    run(args.protocol, args.deployment_env)
