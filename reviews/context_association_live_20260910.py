"""Approved 54+2 live adapter; immutable proven transport, new frozen plan."""
from contextlib import contextmanager
import itertools
import json
from pathlib import Path
import subprocess
import sys

import context_association_materials_20260910 as materials
import shortcut_challenge_live_20260910 as transport

ROOT = materials.prior.ROOT
HERE = Path(__file__).resolve()
OUT = ROOT / 'artifacts/context-association-live-20260910'
PLAN_PATH = OUT / 'plan.json'
ANALYSIS = HERE.with_name('context_association_live_analysis_20260910.py')
AUDIT = HERE.with_name('context_association_response_audit_20260910.py')
PROTOCOL = HERE.with_name('context-association-live-protocol-20260910.md')
FORMAL_CALLS = 54
MAX_CALLS = 56
SETTINGS = dict(transport.SETTINGS)


def scheduled_tasks(public, private):
    tasks = {t['task_id']: t for t in public['tasks']}
    grouped = {}
    for b in private['bindings']:
        group = grouped.setdefault(b['original_task_id'], {})
        if b['condition'] in group:
            raise ValueError('Duplicate condition')
        group[b['condition']] = b['task_id']
    if len(grouped) != 18:
        raise ValueError('Expected18 original tasks')
    originals = sorted(grouped, key=lambda t: materials.prior.digest_text('association-schedule-v1:' + t))
    orders = list(itertools.permutations(materials.CONDITIONS))
    result = []
    for index, original in enumerate(originals):
        group = grouped[original]
        if set(group) != set(materials.CONDITIONS):
            raise ValueError('Incomplete condition block')
        result.extend(tasks[group[c]] for c in orders[index % len(orders)])
    if len(result) != 54 or len({t['task_id'] for t in result}) != 54 or set(tasks) != {t['task_id'] for t in result}:
        raise ValueError('Public schedule not bijective')
    return result


@contextmanager
def adapter():
    # Only process-local configuration changes; no edits to the frozen transport.
    overrides = {'OUT': OUT, 'PLAN_PATH': PLAN_PATH, 'HERE': HERE,
                 'TASK_COUNT': FORMAL_CALLS, 'MAX_CALLS': MAX_CALLS}
    old = {key: getattr(transport, key) for key in overrides}
    try:
        for key, value in overrides.items(): setattr(transport, key, value)
        yield transport
    finally:
        for key, value in old.items(): setattr(transport, key, value)


def validate():
    with adapter():
        plan, tasks = transport._validate_plan()
    if plan.get('kind') != 'context_structured_reference_live_v1' or plan.get('max_workers') != 54:
        raise ValueError('Wrong live protocol')
    if plan.get('source_private_file_sha256') != plan.get('private_file_sha256'):
        raise ValueError('Draft/live private hash mismatch')
    # Runtime sender verifies the public draft content but never loads private labels.
    draft = transport._read_json(materials.OUT / 'public-draft.json')
    if {t['task_id']: t for t in tasks} != {t['task_id']: t for t in draft['tasks']}:
        raise ValueError('Live prompts differ from reviewed draft')
    return plan, tasks


def freeze():
    # --verify is read-only and refuses drift in existing draft/source bindings.
    subprocess.run([sys.executable, str(Path(materials.__file__)), '--verify'], check=True)
    public = transport._read_json(materials.OUT / 'public-draft.json')
    private = transport._read_json(materials.OUT / 'private-draft.json')
    tasks = scheduled_tasks(public, private)
    files = [HERE, ANALYSIS, AUDIT, PROTOCOL, Path(transport.__file__), Path(transport.serial.__file__),
             Path(transport.base.__file__), Path(materials.__file__),
             ROOT / 'src/providers/openai_compatible.py', ROOT / 'src/runner.py',
             ROOT / 'src/spark_strong_k4_utilization_primary_live.py',
             materials.OUT / 'public-draft.json', materials.OUT / 'private-draft.json',
             materials.OUT / 'audit.json', materials.pool.OUT / 'audit.json']
    draft_audit = transport._read_json(materials.OUT / 'audit.json')
    hashes = dict(draft_audit['source_sha256'])
    hashes.update({str(path.relative_to(ROOT)): transport.sha(path) for path in files})
    plan = {'kind': 'context_structured_reference_live_v1', 'tasks': tasks,
            'settings': SETTINGS, 'endpoint': transport.ENDPOINT,
            'formal_calls': FORMAL_CALLS, 'maximum_calls': MAX_CALLS, 'max_technical_retries': 2,
            'timeout_seconds': 3600, 'interval_seconds': 3, 'max_workers': 54,
            'maximum_completion_tokens': MAX_CALLS * SETTINGS['max_tokens'],
            'private_file_sha256': transport.sha(materials.OUT / 'private-draft.json'),
            'source_private_file_sha256': transport.sha(materials.OUT / 'private-draft.json'),
            'source_plan_sha256': materials.prior.PLAN_SHA,
            'input_file_hashes': hashes, 'conditions': list(materials.CONDITIONS),
            'schedule': '18 public-hash-ordered task blocks; each six permutations of three conditions used3times; 3s stagger',
            'retry_policy': 'first wave drains; up to2 lowest scheduled-index eligible technical failures; once per slot; no correctness/content retry',
            'scoring': 'all9worlds/18tasks per condition; missing/invalid misses; descriptive paired contrasts; no new pvalues',
            'evidence_scope': 'redundant structured auxiliary correspondence; no equal-information/internal-entropy/general-discovery claim',
            'historical_transport_kind': 'generation.kind retains shortcut_challenge_deepseek_thinking_live as transport provenance; plan defines experiment',
            'provider_calls_during_freeze': 0}
    OUT.mkdir(mode=0o700, exist_ok=False)
    # Copy bytes so reviewed private hash is exactly preserved.
    transport._write_private_bytes(OUT / 'private.json', (materials.OUT / 'private-draft.json').read_bytes())
    transport._write_exclusive_json(PLAN_PATH, plan)
    validate()
    print(json.dumps({'frozen': True, 'formal_calls': 54, 'maximum_calls': 56,
                      'plan_sha256': transport.sha(PLAN_PATH), 'provider_calls': 0}))


def main():
    if sys.argv[1:] == ['freeze']:
        freeze()
    elif sys.argv[1:] == ['validate']:
        _, tasks = validate()
        print(json.dumps({'validated_tasks': len(tasks), 'provider_calls': 0}))
    elif sys.argv[1:] == ['run', '--execute']:
        validate()
        with adapter():
            transport.run(True)
        # Automatic next step even if some slots are invalid/missing.
        subprocess.run([sys.executable, str(ANALYSIS)], check=True)
        subprocess.run([sys.executable, str(AUDIT), '--save'], check=True)
    else:
        raise SystemExit('Usage: context_association_live_20260910.py freeze | validate | run --execute')


if __name__ == '__main__': main()
