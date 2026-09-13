"""Approved32+2 cap-only nonthinking comparison; immutable shared transport."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import nonthinking_budget_audit_20260910 as draft
import shortcut_challenge_live_20260910 as transport

ROOT = transport.ROOT
HERE = Path(__file__).resolve()
OUT = ROOT / 'artifacts/nonthinking-budget-live-20260910'
PLAN_PATH = OUT / 'plan.json'
ANALYSIS = HERE.with_name('nonthinking_budget_analysis_20260910.py')
AUDIT = HERE.with_name('nonthinking_budget_response_audit_20260910.py')
PROTOCOL = HERE.with_name('nonthinking-budget-live-protocol-20260910.md')
THINK = ROOT / 'artifacts/deepseek-thinking-128k-staggered-20260909/generation.json'
SETTINGS = {'model': 'deepseek-v4-pro', 'thinking': {'type': 'disabled'},
            'temperature': 0.2, 'max_tokens': 256, 'response_format': {'type': 'json_object'}, 'stream': False}


def public_schedule(rows):
    tasks, bindings, caps = [], [], {}
    for row in rows:
        tid = 'BUDGET' + hashlib.sha256(f"{row['original_task_id']}:{row['max_tokens']}".encode()).hexdigest()[:20]
        tasks.append({k: row[k] for k in ('rendered_prompt', 'prompt_sha256')} | {'task_id': tid})
        caps[tid] = row['max_tokens']
        bindings.append({k: row[k] for k in ('original_task_id', 'max_tokens', 'prompt_sha256')} | {'task_id': tid})
    if len(tasks) != 32 or len(caps) != 32 or sorted(caps.values()) != [256] * 16 + [8192] * 16:
        raise ValueError('Expected32 distinct slots with16 of each cap')
    return tasks, bindings, caps


def request_body(plan, task):
    return {**plan['settings'], 'max_tokens': plan['cap_by_task'][task['task_id']],
            'messages': [{'role': 'user', 'content': task['rendered_prompt']}]}


@contextmanager
def adapter():
    changes = {'OUT': OUT, 'PLAN_PATH': PLAN_PATH, 'HERE': HERE, 'TASK_COUNT': 32,
               'MAX_CALLS': 34, 'TIMEOUT_SECONDS': 120, 'SETTINGS': SETTINGS, 'request_body': request_body}
    old = {k: getattr(transport, k) for k in changes}
    try:
        for k, v in changes.items(): setattr(transport, k, v)
        yield transport
    finally:
        for k, v in old.items(): setattr(transport, k, v)


def validate():
    with adapter(): plan, tasks = transport._validate_plan()
    if plan.get('kind') != 'nonthinking_budget_live_v1' or plan.get('max_workers') != 32:
        raise ValueError('Wrong budget experiment')
    expected, _, caps = public_schedule(transport._read_json(draft.OUT / 'public-draft.json')['tasks'])
    if tasks != expected or plan['cap_by_task'] != caps:
        raise ValueError('Frozen draft schedule/caps differ')
    if transport.sha(OUT / 'private.json') != plan['private_file_sha256']:
        raise ValueError('Private binding mismatch')
    if plan['maximum_completion_tokens'] != 151552:
        raise ValueError('Budget changed')
    return plan, tasks


def freeze():
    subprocess.run([sys.executable, str(Path(draft.__file__)), '--verify'], check=True)
    audit = transport._read_json(draft.OUT / 'audit.json')
    tasks, bindings, caps = public_schedule(transport._read_json(draft.OUT / 'public-draft.json')['tasks'])
    original = transport._read_json(draft.OLD / 'plan.json')['construction_binding']
    private_path = ROOT / original['private_key_relative_path']
    old_pairs = transport._read_json(private_path)['pairs']
    by_id = {p['pair_id']: p for p in old_pairs}
    private = {'pairs': [by_id[r['pair_id']] for r in audit['selected_pairs']], 'bindings': bindings}
    files = [HERE, ANALYSIS, AUDIT, PROTOCOL, Path(transport.__file__), Path(transport.serial.__file__),
             Path(transport.base.__file__), ROOT / 'src/providers/openai_compatible.py', ROOT / 'src/runner.py',
             ROOT / 'src/spark_strong_k4_utilization_primary_live.py',
             draft.OUT / 'audit.json', draft.OUT / 'public-draft.json', THINK]
    if transport.sha(THINK) != 'b474bf96248f2d6fd6c494322c731aaaf1a8b2c014242e59bf76cc5866dfc36c':
        raise ValueError('Historical thinking generation changed')
    hashes = dict(audit['source_sha256'])
    hashes.update({str(p.relative_to(ROOT)): transport.sha(p) for p in files})
    OUT.mkdir(mode=0o700, exist_ok=False)
    transport._write_exclusive_json(OUT / 'private.json', private)
    plan = {'kind': 'nonthinking_budget_live_v1', 'tasks': tasks, 'cap_by_task': caps,
            'settings': SETTINGS, 'endpoint': transport.ENDPOINT, 'timeout_seconds': 120,
            'formal_calls': 32, 'maximum_calls': 34, 'max_technical_retries': 2,
            'max_workers': 32, 'interval_seconds': 3, 'maximum_completion_tokens': 151552,
            'input_file_hashes': hashes, 'private_file_sha256': transport.sha(OUT / 'private.json'),
            'authorization': 'User confirmed32+2calls and256/8192caps;no expansion',
            'retry_policy': 'first wave drains;2lowest-index eligible technical failures once each;no content retry',
            'scoring': '8worlds/16arms percap;own/cross/full/net;missinginvalid0;no new pvalues',
            'historical_transport_kind': 'generation kind names old transport; this plan defines cap-only experiment',
            'provider_calls_during_freeze': 0}
    transport._write_exclusive_json(PLAN_PATH, plan)
    validate()
    print(json.dumps({'frozen': True, 'plan_sha256': transport.sha(PLAN_PATH), 'provider_calls': 0}))


def main():
    if sys.argv[1:] == ['freeze']: freeze()
    elif sys.argv[1:] == ['validate']:
        _, tasks = validate()
        print(json.dumps({'validated_tasks': len(tasks), 'provider_calls': 0}))
    elif sys.argv[1:] == ['run', '--execute']:
        validate()
        with adapter(): transport.run(True)
        subprocess.run([sys.executable, str(AUDIT), '--save'], check=True)
        subprocess.run([sys.executable, str(ANALYSIS)], check=True)
    else: raise SystemExit('Usage: nonthinking_budget_live_20260910.py freeze | validate | run --execute')


if __name__ == '__main__': main()
