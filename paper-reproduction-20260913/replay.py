"""Standalone stdlib-only scoring replay for the staged revised microloop.

Run from any directory with Python 3.10+. No provider, network or project import.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import re

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('dsl', ROOT / 'dsl.py')
dsl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dsl)


def main():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    prompts = json.loads((ROOT / 'prompts.json').read_text())
    for row in prompts:
        assert hashlib.sha256(row['prompt'].encode()).hexdigest() == row['prompt_sha256']
    rows = json.loads((ROOT / 'microloop-evaluation.json').read_text())
    scores = {c: [0, 0, 0] for c in ('active', 'random', 'repeat')}
    counts = dict.fromkeys(scores, 0)
    stage_checks = 0
    for row in rows:
        assert len(row['responses']) == len(row['expected_correct']) == 3
        assert len(row['test']) == 64
        observations = list(row['D0'])
        for stage, response in enumerate(row['responses']):
            obj = json.loads(response)
            assert set(obj) == ({'expression'} if stage == 2 else {'expression', 'query'})
            expression = obj['expression']
            if re.match(r'^\s*\(\s*(gt|eq)\s', expression):
                expression = '(ite ' + expression + ' (const 1) (const 0))'
            ast = dsl.parse_sexpr(expression)
            dsl.validate_expr(ast)
            assert set(dsl.behavior_vector(ast)) <= {0, 1}
            assert all(dsl.evaluate(ast, x['point']) == x['label'] for x in observations)
            correct = sum(dsl.evaluate(ast, x['point']) == x['label'] for x in row['test'])
            assert correct == row['expected_correct'][stage]
            scores[row['condition']][stage] += correct
            if stage < 2:
                observations.append(row['feedback'][stage])
            stage_checks += 1
        counts[row['condition']] += 1
    assert stage_checks == 108 and set(counts.values()) == {12}
    print(json.dumps({'manifest_files_verified': len(manifest['files']),
        'exact_prompts_verified': len(prompts), 'stage_scores_and_consistency_verified': stage_checks,
        'mean_accuracy_percent': {c: [100 * n / 768 for n in v] for c, v in scores.items()},
        'scope': 'revised microloop scoring; not all-paper construction or raw-provider replay',
        'model_calls': 0}, indent=2))


if __name__ == '__main__':
    main()
