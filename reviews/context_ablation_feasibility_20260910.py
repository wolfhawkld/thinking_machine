"""Offline semantic feasibility; never reads model responses or calls providers."""
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'artifacts/shortcut-challenge-live-20260910'
OUT = ROOT / 'artifacts/context-ablation-feasibility-20260910'
PLAN_SHA = '66b98756b83016fe4c702ea914d1be7ff984eb8f37873e79ad480a0e842496c2'
START = '\nContext fragment:\n'
END = '\n\nIn each choice,'
OLD_DEFINITION = 'CONTEXT means the context fragment above.'
DEFINITION = ('CONTEXT means the fixed executable fragment held by the evaluator. '
              'The context display reports information about that fragment; '
              'visibility of the display does not change the fragment used for execution.')
HIDDEN = 'The executable fragment is fixed, but its expression is not disclosed in this condition.'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def split_context(prompt):
    if prompt.count(START) != 1 or prompt.count(OLD_DEFINITION) != 1:
        raise ValueError('Unexpected prompt schema')
    before, rest = prompt.split(START)
    if rest.count(END) != 1:
        raise ValueError('Unexpected context boundary')
    context, after = rest.split(END)
    if not context.strip():
        raise ValueError('Empty context')
    return before, context, after


def observation_prompt(prompt, visible):
    before, fragment, after = split_context(prompt)
    display = fragment if visible else HIDDEN
    return (before + '\nContext display:\n' + display + END + after).replace(OLD_DEFINITION, DEFINITION)


def inspect_pair(pair, tasks):
    a, b = (tasks[pair['arms'][arm]['task_id']]['rendered_prompt'] for arm in ('context_a', 'context_b'))
    prefix_a, fragment_a, suffix_a = split_context(a)
    prefix_b, fragment_b, suffix_b = split_context(b)
    if prefix_a != prefix_b or suffix_a != suffix_b or fragment_a == fragment_b:
        raise ValueError('Expected prompts differing only in executable context')
    if pair['arms']['context_a']['correct_option_id'] == pair['arms']['context_b']['correct_option_id']:
        raise ValueError('Expected distinct arm labels')
    if prefix_a + START + fragment_b + END + suffix_a != b:
        raise ValueError('Swap did not reconstruct the opposite task')
    hidden = [observation_prompt(prompt, False) for prompt in (a, b)]
    if hidden[0] != hidden[1]:
        raise ValueError('Unexpected arm cue remains after observation redaction')
    visible = [observation_prompt(prompt, True) for prompt in (a, b)]
    return {
        'swapped_prompt_is_opposite_task': True,
        'opposite_task_has_different_correct_option': True,
        'hidden_prompts_identical': True,
        'visible_prompts_distinct': visible[0] != visible[1],
        'visible_characters': [len(p) for p in visible],
        'hidden_characters': [len(p) for p in hidden],
        'drafts': {'visible': visible, 'hidden': hidden},
    }


def save(path, value):
    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def run():
    if sha(SOURCE / 'plan.json') != PLAN_SHA:
        raise ValueError('Source cohort plan changed')
    plan = json.loads((SOURCE / 'plan.json').read_text())
    private_sha = sha(SOURCE / 'private.json')
    if private_sha != plan['private_file_sha256']:
        raise ValueError('Original execution-label binding changed')
    private = json.loads((SOURCE / 'private.json').read_text())
    tasks = {t['task_id']: t for t in plan['tasks']}
    if len(tasks) != 18 or len(private['pairs']) != 9:
        raise ValueError('Fixed source cohort changed')
    for task in tasks.values():
        if digest_text(task['rendered_prompt']) != task['prompt_sha256']:
            raise ValueError('Source prompt hash mismatch')
    reports, drafts = [], []
    for ordinal, pair in enumerate(private['pairs']):
        result = inspect_pair(pair, tasks)
        reports.append({'ordinal': ordinal, 'stratum': pair['stratum'],
                        **{k: v for k, v in result.items() if k != 'drafts'}})
        for condition, prompts in result['drafts'].items():
            for arm, prompt in zip(('context_a', 'context_b'), prompts, strict=True):
                drafts.append({
                    'draft_id': f'visibility-{ordinal:02d}-{arm}-{condition}',
                    'condition': condition, 'arm': arm, 'pair_ordinal': ordinal,
                    'rendered_prompt': prompt, 'prompt_sha256': digest_text(prompt),
                    'execution_binding_original_task_id': pair['arms'][arm]['task_id'],
                    'execution_binding_private_sha256': private_sha,
                    'correct_option_id': pair['arms'][arm]['correct_option_id'],
                })
    assert sha(SOURCE / 'private.json') == private_sha and sha(SOURCE / 'plan.json') == PLAN_SHA
    report = {
        'kind': 'context_observation_feasibility_not_model_experiment',
        'worlds_checked': 9, 'original_tasks_checked': 18,
        'swap_reconstructs_opposite_task_count': sum(r['swapped_prompt_is_opposite_task'] for r in reports),
        'swap_changes_correct_option_count': sum(r['opposite_task_has_different_correct_option'] for r in reports),
        'hidden_identical_pairs': sum(r['hidden_prompts_identical'] for r in reports),
        'visible_distinct_pairs': sum(r['visible_prompts_distinct'] for r in reports),
        'draft_tasks': len(drafts), 'draft_is_live_authorization': False,
        'strata': dict(Counter(r['stratum'] for r in reports)),
        'source_plan_sha256': PLAN_SHA, 'source_private_sha256': private_sha,
        'source_file_sha256': sha(__file__), 'provider_calls': 0,
        'model_response_files_read': False, 'new_worlds_generated': 0, 'oracle_rerun': False,
        'length_matched': False, 'equal_information': False,
        'execution_binding': 'unchanged original task, action map and private label; only observation is transformed',
        'limitations': ['necessary task information is removed', 'hidden arms are observationally indistinguishable',
                        'not a pure test of relevance with equal information', 'no internal entropy inference'],
        'pair_checks': reports,
    }
    OUT.mkdir(mode=0o700, exist_ok=False)
    save(OUT / 'draft-private.json', {'warning': 'NOT provider-ready: contains private labels; do not send whole records', 'tasks': drafts})
    save(OUT / 'audit.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'pair_checks'}, indent=2))


if __name__ == '__main__': run()
