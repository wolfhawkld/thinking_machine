"""Build a local allowlisted prompt appendix and limited offline scoring bundle.

No recursive artifact copying, credentials, provider calls, or publishing.
Generated outputs only; frozen input artifacts remain read-only.
"""
from pathlib import Path
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import microloop_revision_20260910 as revision

OUT = ROOT / 'paper-reproduction-20260913'
SOURCES = {
    'original-entry': 'spark-strong-k4-utilization-primary-benchmark-v2-20260827/public.json',
    'challenge-entry': 'shortcut-challenge-live-20260910/plan.json',
    'reference-control': 'context-association-live-20260910/plan.json',
    'one-observation': 'new-evidence-live-20260910/plan.json',
    'output-cap': 'nonthinking-budget-live-20260910/plan.json',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    prompts, source_hashes, examples = [], {}, []
    def read(path):
        data = path.read_bytes()
        source_hashes[str(path.relative_to(ROOT))] = digest(data)
        return json.loads(data)
    for group, name in SOURCES.items():
        tasks = read(ROOT / 'artifacts' / name)['tasks']
        for task in tasks:
            prompt = task['rendered_prompt']
            assert digest(prompt.encode()) == task['prompt_sha256']
            prompts.append({'experiment': group, 'task_id': task['task_id'],
                            'prompt': prompt, 'prompt_sha256': task['prompt_sha256']})
        examples.append((group, tasks[0]['rendered_prompt']))
    live = ROOT / 'artifacts/microloop-revised-live-20260911'
    public = read(live / 'public.json')['worlds']
    private = read(live / 'private.json')['worlds']
    records = read(live / 'records.json')
    analysis = read(live / 'analysis.json')
    evaluation = []
    for row in records:
        ordinal, condition = row['ordinal'], row['condition']
        pub, priv = public[ordinal], private[ordinal]
        labels = {tuple(r['point']): r['label'] for r in priv['evidence']}
        state = revision.initial_state(pub, condition)
        feedback = []
        for stage, content in enumerate(row['responses']):
            request = read(live / f'{ordinal:02d}-{condition}/stage-{stage}-attempt-0.request.json')
            prompt = revision.render_prompt(pub, state)
            assert request['prompt'] == prompt
            assert request['prompt_sha256'] == digest(prompt.encode())
            prompts.append({'experiment': 'revised-microloop', 'ordinal': ordinal,
                            'condition': condition, 'stage': stage, 'prompt': prompt,
                            'prompt_sha256': request['prompt_sha256']})
            if ordinal == 0 and condition == 'active':
                examples.append((f'revised-microloop-stage-{stage}', prompt))
            state = revision.advance(pub, state, content, lambda q: labels[tuple(q)])
            if stage < 2:
                feedback.append(state['history'][-1]['feedback'])
        expected = analysis['conditions'][condition]['world_rows'][ordinal]['stages']
        evaluation.append({'ordinal': ordinal, 'condition': condition, 'D0': pub['D0'],
                           'responses': row['responses'], 'feedback': feedback,
                           'test': priv['test'],
                           'expected_correct': [s['test_correct'] for s in expected]})
    files = {
        'prompts.json': json.dumps(prompts, ensure_ascii=False, indent=2).encode(),
        'microloop-evaluation.json': json.dumps(evaluation, ensure_ascii=False, indent=2).encode(),
        'dsl.py': (ROOT / 'src/dsl.py').read_bytes(),
        'replay.py': (ROOT / 'reviews/replay_paper_bundle_20260913.py').read_bytes(),
        'README.md': (
            '# Local reproduction candidate — 13 September 2026\n\n'
            'Not published and not a complete-paper reproduction package. Python 3.10+; standard library only. '
            'Run `python3 -I replay.py` from this directory. '
            'No API keys, network, model requests or dependencies are required.\n\n'
            '`prompts.json` contains exact user prompt strings for the original entry cohort and four follow-up task sets, '
            'plus all 108 revised microloop stage prompts. Original cohort prompts are shared across configurations; '
            'this is not a physical request count. Legacy microloop prompts/rescoring are not exported. '
            'Request settings and any other message roles are not reconstructed for the earlier experiments. '
            'Revised microloop transport sent a single user message containing each saved prompt, with no system message.\n\n'
            '`microloop-evaluation.json` contains saved answer content, actual feedback and held-out evaluation labels. '
            'The test labels are evaluator-only: never add this file to a model prompt. '
            'The package verifies 108 revised stage scores and evidence consistency using the original DSL, '
            'but does not independently regenerate worlds, recompute every paper baseline or verify provider raw envelopes. '
            'Full local raw replay remains in the source repository.\n\n'
            'Allowlisted fields only: no key files, headers, environment values, endpoints, raw provider envelopes, '
            'reasoning traces, or request IDs. Source hashes are provenance, not proof of independent scientific validity. '
            'The author has approved sharing this candidate in the public project repository for independent review. '
            'Complete-paper packaging, licensing review and a separate-machine check remain pending.\n'
        ).encode(),
    }
    for name, data in files.items():
        assert not re.search(rb'sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._-]{16,}|-----BEGIN .*PRIVATE KEY', data), name
    manifest = {'scope': 'limited scoring candidate; approved for public repository sharing',
                'files': {n: digest(d) for n, d in files.items()},
                'source_files': source_hashes,
                'prompt_count': len(prompts), 'evaluation_trajectories': len(evaluation)}
    OUT.mkdir(exist_ok=True)
    allowed = set(files) | {'manifest.json', '__pycache__'}
    assert not {p.name for p in OUT.iterdir()} - allowed, 'Unexpected output file; inspect manually'
    for name, data in files.items():
        (OUT / name).write_bytes(data)
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    appendix = '# Exact Prompt Appendix (2026-09-13)\n\n'
    appendix += ('These are verbatim saved user strings, not rewritten templates. '
                 'Examples are the first frozen task in each earlier task set and all three stages of world 0 active '
                 'in the revised microloop, selected by position, not success. '
                 'The complete machine-readable [prompt collection](paper-reproduction-20260913/prompts.json) '
                 'contains ' + str(len(prompts)) + ' strings with SHA256 hashes. '
                 'World data and prior answers are deliberately retained inside each prompt. '
                 'The revised transport sent one user message; see the [scope and reproduction instructions]'
                 '(paper-reproduction-20260913/README.md) for coverage and exclusions.\n\n')
    for name, prompt in examples:
        assert '```' not in prompt
        appendix += '## ' + name + '\n\nSHA256: `' + digest(prompt.encode()) + '`\n\n```text\n' + prompt + '\n```\n\n'
    (ROOT / 'paper-prompts-en-20260913.md').write_text(appendix.rstrip() + '\n')
    print(json.dumps({'prompts': len(prompts), 'trajectories': len(evaluation),
                      'allowlisted_files': len(files) + 1, 'model_calls': 0}, indent=2))


if __name__ == '__main__':
    main()
