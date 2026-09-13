"""Offline cap diagnostic and outcome-blind subset draft; no provider calls."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys

import shortcut_challenge_live_20260910 as io

ROOT = io.ROOT
OLD = ROOT / 'artifacts/spark-strong-k4-utilization-primary-live-v1-20260908'
OUT = ROOT / 'artifacts/nonthinking-budget-draft-20260910'
NAMESPACE = 'nonthinking-budget-subset-v1-20260910'
STRATA = ('affine_commutative', 'affine_directional', 'affine_multiplicative', 'pairwise_variable')


def select_pairs(pairs):
    # Only structural stratum and already-frozen anchor enter selection.
    groups = defaultdict(list)
    for p in pairs:
        groups[p['construction_stratum']].append(p)
    if set(groups) != set(STRATA) or any(len(v) != 6 for v in groups.values()):
        raise ValueError('Original24-world stratum denominator changed')
    return [p for stratum in STRATA for p in sorted(groups[stratum], key=lambda p:
            hashlib.sha256((NAMESPACE + ':' + p['pair_anchor_sha256']).encode()).hexdigest())[:2]]


def summarize(records):
    if len(records) != 48 or len({r['task_id'] for r in records}) != 48:
        raise ValueError('Original48 responses required')
    outputs = [r['response']['output_tokens'] for r in records]
    return {'tasks': 48, 'valid_choices': sum(r['valid_choice'] for r in records),
            'finish_reasons': dict(Counter(r['response']['finish_reason'] for r in records)),
            'provider_models': dict(Counter(r['response']['provider_model'] for r in records)),
            'output_tokens': {'min': min(outputs), 'median': statistics.median(outputs),
                              'max': max(outputs), 'sum': sum(outputs),
                              'histogram': {str(k): v for k, v in sorted(Counter(outputs).items())}},
            'input_tokens': sum(r['response']['input_tokens'] for r in records),
            'outputs_at_or_above_256': sum(x >= 256 for x in outputs),
            'reasoning_token_field_missing': sum(r['response'].get('reasoning_tokens') is None for r in records)}


def construct():
    plan_path = OLD / 'plan.json'
    if io.sha(plan_path) != 'f8d99fba3c50510beb6180c2f14fc34d91fed30ee0ef61caff1c923ee07641de':
        raise ValueError('Original plan changed')
    plan = io._read_json(plan_path)
    binding = plan['construction_binding']
    public_path = ROOT / binding['public_manifest_relative_path']
    private_path = ROOT / binding['private_key_relative_path']
    for path, key in ((public_path, 'public_manifest_file_sha256'), (private_path, 'private_key_file_sha256')):
        if io.sha(path) != binding[key]: raise ValueError('Original cohort changed')
    public, private = io._read_json(public_path), io._read_json(private_path)
    selected = select_pairs(private['pairs'])
    tasks = {t['task_id']: t for t in public['tasks']}
    selected_rows = [{'pair_id': p['pair_id'], 'pair_anchor_sha256': p['pair_anchor_sha256'],
                     'stratum': p['construction_stratum'],
                     'task_ids': [p['arms'][a]['task_id'] for a in ('context_a', 'context_b')]} for p in selected]
    draft = []
    for pair_index, row in enumerate(selected_rows):
        for arm_index, task_id in enumerate(row['task_ids']):
            caps = (256, 8192) if (pair_index + arm_index) % 2 == 0 else (8192, 256)
            for cap in caps:
                draft.append({'original_task_id': task_id, 'max_tokens': cap,
                              'rendered_prompt': tasks[task_id]['rendered_prompt'],
                              'prompt_sha256': tasks[task_id]['prompt_sha256']})
    # Historical outputs are read only after selecting the draft subset.
    gen_path = OLD / 'generation.json'
    if io.sha(gen_path) != 'bf40ab09660fcf561cdac434017ff2f7c07c9d1582fdd571592ac55ad11b8eba':
        raise ValueError('Original generation changed')
    gen = io._read_json(gen_path)
    for r in gen['records']:
        if r['prompt_sha256'] != tasks[r['task_id']]['prompt_sha256']:
            raise ValueError('Historical response prompt mismatch')
    files = [Path(__file__).resolve(), plan_path, gen_path, public_path, private_path]
    audit = {'kind': 'NONTHINKING_BUDGET_OFFLINE_DRAFT', 'new_provider_calls': 0,
             'live_authorized': False, 'historical': summarize(gen['records']),
             'original_request_contract': plan['request_contract'],
             'selection_uses_historical_model_outputs': False,
             'selection_occurs_after_historical_results_known_to_researchers': True,
             'selection_namespace': NAMESPACE, 'selected_pairs': selected_rows,
             'proposed_formal_calls': 32, 'proposed_max_technical_retries': 2,
             'proposed_maximum_calls': 34, 'proposed_maximum_completion_tokens': 16 * 256 + 18 * 8192,
             'source_sha256': {str(p.relative_to(ROOT)): io.sha(p) for p in files}}
    return audit, {'kind': 'DRAFT_NOT_LIVE_AUTHORIZATION', 'tasks': draft}


def main():
    audit, draft = construct()
    if sys.argv[1:] == ['--verify']:
        if io._read_json(OUT / 'audit.json') != audit or io._read_json(OUT / 'public-draft.json') != draft:
            raise ValueError('Offline draft reconstruction changed')
        print('Verified48 old responses and32 proposed tasks;0 provider calls.')
        return
    if sys.argv[1:]: raise SystemExit('Usage: nonthinking_budget_audit_20260910.py [--verify]')
    OUT.mkdir(mode=0o700, exist_ok=False)
    io._write_exclusive_json(OUT / 'audit.json', audit)
    io._write_exclusive_json(OUT / 'public-draft.json', draft)
    print(json.dumps({'historical': audit['historical'], 'selected_worlds': len(audit['selected_pairs']),
                      'draft_tasks': len(draft['tasks']), 'new_provider_calls': 0}, indent=2))


if __name__ == '__main__': main()
