"""Separate public workflow instructions/inputs from publisher evaluation files."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root):
    store = root / 'public-actor'
    rows = []
    def admit(identifier, benchmark, instruction, constraints, files):
        directory = store / identifier / 'actor'
        directory.mkdir(parents=True, exist_ok=True)
        records = []
        for source in files:
            if source.is_symlink() or not source.is_file():
                raise ValueError('Only original regular actor files may be admitted')
            destination = directory / source.name
            digest = sha(source)
            if destination.exists():
                if sha(destination) != digest:
                    raise ValueError('Retained actor file changed')
            else:
                shutil.copyfile(source, destination)
            records.append({'name': source.name, 'bytes': source.stat().st_size, 'sha256': digest})
        rows.append({'id': identifier, 'benchmark': benchmark, 'instruction': instruction,
                     'constraints': constraints, 'files': records})
    # FINCH task.json was already extracted using publisher-declared source files.
    finch = json.loads((root / 'finch-actor-manifest.json').read_text())
    for case in finch:
        files = [root / 'finch' / case['id'] / 'actor' / f['name'] for f in case['files']]
        for source, receipt in zip(files, case['files']):
            if sha(source) != receipt['sha256']:
                raise ValueError('FINCH source changed')
        admit(case['id'], 'finch', case['instruction'], case['constraints'], files)
    # The interrogation JSON mixes questions with expected answers: whitelist
    # question text and IDs only, never send that original file to the actor.
    bluefin = root / 'bluefin'
    for path in sorted((bluefin / 'interrogation').glob('*/evaluator/questions.json')):
        for question in json.loads(path.read_text()):
            admit('bluefin_' + question['id'], 'bluefin', question['question'], '',
                  [path.parent.parent / 'actor/input_workbook.xlsx'])
    for category in ['manipulation', 'synthesis']:
        for path in sorted((bluefin / category).glob('*/actor/instruction.md')):
            admit('bluefin_' + category + '_' + path.parent.parent.name, 'bluefin',
                  path.read_text(), '', [path.with_name('input_workbook.xlsx')])
    # Scratch/template share a case. Evaluate the template once; retain scratch
    # inputs and all reference/rubric/state checks outside this actor package.
    emb = root / 'emb/M003-template/actor'
    admit('emb_M003_template', 'emb', (emb / 'instructions.md').read_text(), '',
          [emb / 'Customer Data Raw.xlsx', emb / 'template.xlsx'])
    manifest = root / 'public-actor-manifest.json'
    with manifest.open('x') as stream:
        json.dump(rows, stream, indent=2, ensure_ascii=False)
    receipt = {'prepared_at_utc': datetime.now(timezone.utc).isoformat(),
               'counts': {name: sum(r['benchmark'] == name for r in rows) for name in ['finch', 'bluefin', 'emb']},
               'actor_cases': len(rows), 'actor_files': sum(len(r['files']) for r in rows),
               'actor_sha256': sha(manifest), 'actor_store': str(store),
               'evaluator_files_admitted': 0,
               'selection': 'Complete retained public native workflows; scratch/template count once. All source files verified; no reference workbooks, rubric, expected answer or scenario checks admitted.'}
    (root / 'public-actor-preparation.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    prepare(parser.parse_args().root)
