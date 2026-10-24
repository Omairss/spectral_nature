"""Paper-derived FinDeepForecast comparison; reads evaluator data, never infers.

This scorer is deliberately separate from the frozen inference runner. Published
references are native comparisons, not personally verified custom outcome labels.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table(text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rows = [line for line in lines if line.startswith('|') and line.endswith('|')]
    cells = [[cell.strip().strip('*_`') for cell in row.strip('|').split('|')] for row in rows]
    if len(cells) != 3 or any(len(row) != 2 for row in cells):
        raise ValueError('Expected exactly one two-column table with one body row')
    if not all(re.fullmatch(r':?-{3,}:?', cell) for cell in cells[1]):
        raise ValueError('Missing Markdown table separator')
    return {'header': cells[0][1], 'field': cells[2][0], 'value': cells[2][1],
            'table_only': len(lines) == 3}


def number(value: str, header: str) -> Decimal:
    # No semantic or unit inference. A percent mark denotes percentage points,
    # exactly as in the original Value (%) column, not a fraction of one.
    currency = re.fullmatch(r'Value \(in millions ([A-Z]{3})\)', header)
    if currency:
        symbols = {'USD': '$', 'SGD': '$', 'HKD': '$', 'GBP': '£', 'EUR': '€', 'JPY': '¥', 'CNY': '¥'}
        value = re.sub(r'^([+-]?)\s*' + re.escape(symbols[currency[1]]) + r'\s*', r'\1', value)
    match = re.fullmatch(r'([+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)(\s*%)?', value)
    if not match or (match[2] and header != 'Value (%)'):
        raise ValueError('Body value is not a scalar in the requested unit')
    return Decimal(match[1].replace(',', ''))


def score(manifest_path: Path, output: Path, recovery_protocol: Path | None = None) -> dict:
    manifest = json.loads(manifest_path.read_text())
    protocol_path = manifest_path.parent / manifest['protocol']
    protocol = json.loads(protocol_path.read_text())
    root = Path(protocol['root'])
    policy_path = Path(__file__).with_name('findeepforecast-scoring-policy.json')
    policy = json.loads(policy_path.read_text())
    actor_path, reference_path = root / 'actor/cases.jsonl', root / 'evaluator/references.jsonl'
    if sha(actor_path) != protocol['actor_sha256']:
        raise ValueError('Frozen actor package changed')
    actor = {row['id']: row for row in map(json.loads, actor_path.read_text().splitlines())}
    references = {row['id']: row for row in map(json.loads, reference_path.read_text().splitlines())}
    if set(actor) != set(references) or len(actor) != manifest['total']:
        raise ValueError('Actor/reference scope does not match full release')
    predictions = {}
    carried_protocols = {}
    for receipt in manifest['carried_first32k_attempts']:
        path = Path(receipt['prediction_path'])
        if sha(path) != receipt['sha256']:
            raise ValueError('A carried first attempt changed')
        predictions[receipt['id']] = path
        carried_protocols[receipt['id']] = manifest_path.parent / receipt['protocol']
    for identifier in protocol['selected_ids']:
        if identifier in predictions:
            raise ValueError('Full condition repeats a carried ID')
        path = Path(protocol['output_root']) / (hashlib.sha256(identifier.encode()).hexdigest()[:20] + '.json')
        if path.exists():
            predictions[identifier] = path
    if set(predictions) - set(actor):
        raise ValueError('Unexpected prediction ID')
    primary_predictions = dict(predictions)
    recovered_ids = set()
    recovery = None
    if recovery_protocol is not None:
        recovery = json.loads(recovery_protocol.read_text())
        recovery_protocols = {sha(recovery_protocol): recovery_protocol}
        for receipt in recovery.get('prior_recovery_protocols', []):
            prior_path = recovery_protocol.parent / receipt['path']
            if sha(prior_path) != receipt['sha256']:
                raise ValueError('Prior recovery protocol changed')
            prior = json.loads(prior_path.read_text())
            # A budget continuation must preserve inference and selection. Only
            # spending admission and declared technical retry policy may change.
            for key in ['source_git', 'engine_revision', 'actor_sha256', 'runtime_overrides',
                        'completion_tokens', 'timeout_seconds', 'runner_sha256',
                        'original_runner_sha256', 'selected_ids', 'output_root',
                        'accounting_root', 'parent_manifest', 'parent_manifest_sha256', 'condition']:
                if prior[key] != recovery[key]:
                    raise ValueError('Budget continuation changes the recovery condition: ' + key)
            recovery_protocols[receipt['sha256']] = prior_path
        for key in ['source_git', 'engine_revision', 'actor_sha256', 'runtime_overrides', 'completion_tokens', 'timeout_seconds']:
            if recovery[key] != protocol[key]:
                raise ValueError('Recovery changes the inference condition: ' + key)
        parent_path = recovery_protocol.parent / recovery['parent_manifest']
        if sha(parent_path) != recovery['parent_manifest_sha256']:
            raise ValueError('Recovery parent inventory changed')
        parent = json.loads(parent_path.read_text())
        failed_ids = {row['id'] for row in parent['attempts'] if row['status'] == 'failed'}
        if set(recovery['selected_ids']) != failed_ids:
            raise ValueError('Recovery must cover all execution-failed cases, independent of score')
        for identifier in recovery['selected_ids']:
            previous_hash = None
            case_dir = Path(recovery['output_root']) / hashlib.sha256(identifier.encode()).hexdigest()[:20]
            completed = False
            for number_, path in enumerate(sorted(case_dir.glob('attempt-*.json')), 1):
                attempt = json.loads(path.read_text())
                if (completed or attempt['id'] != identifier or attempt['attempt_number'] != number_
                        or attempt['previous_attempt_sha256'] != previous_hash
                        or attempt['primary_attempt_sha256'] != sha(primary_predictions[identifier])
                        or attempt['protocol_sha256'] not in recovery_protocols):
                    raise ValueError('Recovery history changed or retried a completed answer: ' + identifier)
                previous_hash = sha(path)
                if (attempt.get('result') or {}).get('status') == 'completed':
                    completed = True
                    predictions[identifier] = path
                    carried_protocols[identifier] = recovery_protocols[attempt['protocol_sha256']]
                    recovered_ids.add(identifier)

    results = []
    for identifier, case in actor.items():
        original = table(case['report']['original_task_prompt'])
        reference = references[identifier]
        record = reference['published_record']
        group = reference['task_group']
        non_recurrent = record['Forecast Type'] == 'Non-Recurrent'
        row = {'id': identifier, 'task_group': group, 'expected_field': original['field'],
               'expected_unit_header': original['header'], 'reference': record['Answer']}
        if not non_recurrent:
            if group == 'Corporate - Recurrent':
                tolerance = policy['corporate_tolerances'][original['header']]
            else:
                label = re.sub(r' \((?:October|November|December) \d{1,2}, 2025\)$', '', original['field'])
                tolerance = policy['macro_tolerances'][label]
            row['relative_tolerance'] = tolerance
        path = predictions.get(identifier)
        if path is None:
            row.update(status='pending', score=None)
            results.append(row)
            continue
        prediction = json.loads(path.read_text())
        expected_protocol = carried_protocols.get(identifier, protocol_path)
        expected_query = json.dumps({'information_cutoff': case['report']['forecast_deadline'],
                                     'original_task_prompt': case['report']['original_task_prompt']}, ensure_ascii=False)
        if (prediction['id'] != identifier or prediction['protocol_sha256'] != sha(expected_protocol)
                or prediction['input_sha256'] != hashlib.sha256(expected_query.encode()).hexdigest()):
            raise ValueError('Prediction identity/input/protocol mismatch: ' + identifier)
        row.update(prediction_sha256=sha(path), issued_at_utc=prediction['issued_at_utc'])
        if identifier in recovered_ids:
            row.update(prediction_origin='technical_recovery',
                       primary_prediction_sha256=sha(primary_predictions[identifier]),
                       attempt_number=prediction['attempt_number'])
        result = prediction.get('result') or {}
        if result.get('status') != 'completed':
            row.update(status='execution_failed', score=0,
                       error_type=result.get('error_type') or (prediction.get('error') or {}).get('type'),
                       error=result.get('error') or prediction.get('error'))
            results.append(row)
            continue
        row['response_completed'] = True
        answer = (result.get('payload') or {}).get('answer_markdown', '')
        try:
            parsed = table(answer)
            row.update(prediction=parsed['value'], observed_field=parsed['field'],
                       observed_unit_header=parsed['header'], table_only=parsed['table_only'],
                       field_matches=parsed['field'] == original['field'],
                       unit_header_matches=parsed['header'] == original['header'])
            if non_recurrent:
                if parsed['value'].upper() not in {'YES', 'NO'}:
                    raise ValueError('Body value is not YES or NO')
                value = parsed['value'].upper()
                row.update(status='scored', score=int(value == record['Answer'].upper()),
                           always_no_score=int(record['Answer'].upper() == 'NO'))
            else:
                value = number(parsed['value'], original['header'])
                target = Decimal(record['Answer'])
                if not target:
                    row.update(status='zero_reference_unresolved', score=None)
                elif not row['unit_header_matches']:
                    row.update(status='unit_header_review_required', score=None)
                else:
                    relative_error = abs(value - target) / abs(target)
                    row.update(status='scored', score=int(relative_error < Decimal(tolerance)),
                               relative_error=str(relative_error))
        except ValueError as exc:
            row.update(status='extraction_failed', score=None, extraction_error=str(exc))
        results.append(row)

    def summarize(rows: list[dict]) -> dict:
        issued = [r for r in rows if r['status'] != 'pending']
        completed = [r for r in rows if r.get('response_completed')]
        correct = sum(r.get('score') or 0 for r in issued)
        unresolved = sum(r['score'] is None for r in issued)
        complete_correct = sum(r.get('score') or 0 for r in completed)
        complete_unresolved = sum(r['score'] is None for r in completed)
        return {'total': len(rows), 'issued': len(issued), 'completed_responses': len(completed),
                'statuses': dict(Counter(r['status'] for r in rows)),
                'correct': correct, 'unresolved_issued': unresolved,
                'accuracy_percent_bounds_issued': [100 * correct / len(issued), 100 * (correct + unresolved) / len(issued)] if issued else None,
                'accuracy_percent_bounds_completed_responses': [100 * complete_correct / len(completed), 100 * (complete_correct + complete_unresolved) / len(completed)] if completed else None,
                'always_no_correct_on_scored_events': sum(r.get('always_no_score', 0) for r in rows),
                'scored_events': sum('always_no_score' in r for r in rows),
                'table_only_completed': sum(bool(r.get('table_only')) for r in completed),
                'field_matches_completed': sum(bool(r.get('field_matches')) for r in completed),
                'unit_header_matches_completed': sum(bool(r.get('unit_header_matches')) for r in completed)}

    report = {'generated_at_utc': datetime.now(timezone.utc).isoformat(),
              'condition': manifest['condition'], 'comparison': 'paper_derived_no_llm_extraction',
              'attempt_selection': 'Original first attempts, with explicitly carried first32k probes.' if recovery is None else 'Original completed answers plus first completed answer in each declared technical-recovery history; original failed records remain unchanged.',
              'recovery_condition': recovery['condition'] if recovery else None,
              'recovered_answers': len(recovered_ids),
              'financial_validity': 'Not adjudicated. Literal published-reference comparison; mixed corporate percentage units and a verified source discrepancy are recorded separately.',
              'timing': manifest['timing_limit'], 'inference_unchanged': True,
              'source_sha256': {'actor': sha(actor_path), 'references': sha(reference_path),
                                'manifest': sha(manifest_path), 'policy': sha(policy_path),
                                'scorer': sha(Path(__file__))},
              'summary': summarize(results),
              'groups': {group: summarize([r for r in results if r['task_group'] == group])
                         for group in sorted({r['task_group'] for r in results})},
              'cases': results}
    if recovery_protocol:
        report['source_sha256']['recovery_protocol'] = sha(recovery_protocol)
        report['source_sha256']['prior_recovery_protocols'] = sorted(
            value for value in recovery_protocols if value != sha(recovery_protocol))
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    return {k: report[k] for k in ['condition', 'summary', 'groups']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--recovery-protocol', type=Path)
    args = parser.parse_args()
    print(json.dumps(score(args.manifest, args.output, args.recovery_protocol), indent=2))
