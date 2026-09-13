"""27+2 approved candidate-law experiment, using the frozen one-shot transport."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import new_evidence_materials_20260910 as materials
import new_evidence_scoring_20260910 as scoring
import shortcut_challenge_live_20260910 as transport
from src.providers.openai_compatible import _extract_response

ROOT = materials.prior.ROOT
HERE = Path(__file__).resolve()
OUT = ROOT / 'artifacts/new-evidence-live-20260910'
PLAN_PATH = OUT / 'plan.json'
ANALYSIS = HERE.with_name('new_evidence_live_analysis_20260910.py')
AUDIT = HERE.with_name('new_evidence_response_audit_20260910.py')
PROTOCOL = HERE.with_name('new-evidence-live-protocol-20260910.md')
SETTINGS = dict(transport.SETTINGS)
FORMAL_CALLS, MAX_CALLS = 27, 29


def record_response(task, payload, elapsed):
    # Extract envelope/accounting only; do NOT use the old option-ID parser.
    expression, inp, out, model, finish, hit, miss, reasoning, fmt, fingerprint = _extract_response(payload)
    if model != SETTINGS['model']:
        raise ValueError('Unexpected response model')
    message = payload['choices'][0]['message']
    content = message.get('content')
    valid = False
    try:
        candidate = json.loads(content)
        if not isinstance(candidate, dict) or set(candidate) != {'expression'} or not isinstance(candidate['expression'], str):
            raise ValueError('Invalid candidate schema')
        expr = materials.dsl.parse_sexpr(candidate['expression'])
        materials.dsl.validate_expr(expr)
        valid = not (set(materials.dsl.behavior_vector(expr, materials.dsl.DOMAIN)) - {0, 1})
    except (ValueError, TypeError, RecursionError):
        pass
    text = message.get('reasoning_content')
    return {'task_id': task['task_id'], 'prompt_sha256': task['prompt_sha256'],
            'content': content, 'valid_choice': valid,
            'valid_choice_semantics': 'legacy transport field: valid binary DSL, not correctness',
            'response': {'expression': expression, 'input_tokens': inp, 'output_tokens': out,
                         'latency_ms': elapsed, 'provider_model': model, 'finish_reason': finish,
                         'prompt_cache_hit_tokens': hit, 'prompt_cache_miss_tokens': miss,
                         'reasoning_tokens': reasoning, 'candidate_format': fmt,
                         'provider_fingerprint': fingerprint, 'seed_supported': False},
            'thinking_telemetry': {'reasoning_content_present': isinstance(text, str) and bool(text.strip()),
                                   'reasoning_character_count': len(text) if isinstance(text, str) else None,
                                   'reasoning_content_sha256': hashlib.sha256(text.encode()).hexdigest() if isinstance(text, str) else None,
                                   'output_truncated': finish == 'length'}}


def scheduled_tasks(public, private):
    tasks = {t['task_id']: t for t in public['tasks']}
    groups = {}
    for b in private['bindings']:
        group = groups.setdefault(b['ordinal'], {})
        if b['condition'] in group:
            raise ValueError('Duplicate condition')
        group[b['condition']] = b['task_id']
    if set(groups) != set(range(9)) or len(tasks) != 27 or len(public['tasks']) != 27:
        raise ValueError('Expected nine worlds and27 tasks')
    # Public prompt hashes determine world order; no target label/outcome consulted.
    ordered = sorted(groups, key=lambda ordinal: materials.digest({
        'namespace': 'new-evidence-schedule-v1-20260910',
        'prompts': sorted(tasks[tid]['prompt_sha256'] for tid in groups[ordinal].values())}))
    result = []
    for position, ordinal in enumerate(ordered):
        group = groups[ordinal]
        if set(group) != set(scoring.CONDITIONS):
            raise ValueError('Incomplete world block')
        offset = position % 3
        order = scoring.CONDITIONS[offset:] + scoring.CONDITIONS[:offset]
        result.extend(tasks[group[c]] for c in order)
    if len(result) != 27 or len({t['task_id'] for t in result}) != 27:
        raise ValueError('Schedule not bijective')
    return result


@contextmanager
def adapter():
    overrides = {'OUT': OUT, 'PLAN_PATH': PLAN_PATH, 'HERE': HERE,
                 'TASK_COUNT': FORMAL_CALLS, 'MAX_CALLS': MAX_CALLS}
    old = {key: getattr(transport, key) for key in overrides}
    old_parser = transport.base.record_response
    try:
        for key, value in overrides.items(): setattr(transport, key, value)
        transport.base.record_response = record_response
        yield transport
    finally:
        transport.base.record_response = old_parser
        for key, value in old.items(): setattr(transport, key, value)


def validate():
    with adapter():
        plan, tasks = transport._validate_plan()
    if (plan.get('kind') != 'new_evidence_candidate_law_live_v1'
            or plan.get('max_workers') != 27
            or plan.get('conditions') != list(scoring.CONDITIONS)):
        raise ValueError('Wrong live protocol')
    private_sha = transport.sha(OUT / 'private.json')
    if private_sha != plan.get('private_file_sha256') or private_sha != plan.get('source_private_file_sha256'):
        raise ValueError('Private binding drift')
    draft = transport._read_json(materials.OUT / 'public-draft.json')
    if {t['task_id']: t for t in tasks} != {t['task_id']: t for t in draft['tasks']}:
        raise ValueError('Prompts differ from v2 draft')
    if materials.digest(tasks) != plan.get('schedule_sha256'):
        raise ValueError('Schedule changed')
    return plan, tasks


def freeze():
    subprocess.run([sys.executable, str(Path(materials.__file__)), '--verify'], check=True)
    public = transport._read_json(materials.OUT / 'public-draft.json')
    private = transport._read_json(materials.OUT / 'private-draft.json')
    tasks = scheduled_tasks(public, private)
    files = [HERE, ANALYSIS, AUDIT, PROTOCOL, Path(transport.__file__), Path(transport.serial.__file__),
             Path(transport.base.__file__), Path(scoring.__file__),
             ROOT / 'src/providers/openai_compatible.py', ROOT / 'src/runner.py',
             ROOT / 'src/spark_strong_k4_utilization_primary_live.py',
             materials.OUT / 'public-draft.json', materials.OUT / 'private-draft.json',
             materials.OUT / 'audit.json', materials.OUT / 'code-baselines.json']
    hashes = dict(transport._read_json(materials.OUT / 'audit.json')['source_sha256'])
    hashes.update(private['source_world_hashes'])
    hashes.update({str(p.relative_to(ROOT)): transport.sha(p) for p in files})
    plan = {'kind': 'new_evidence_candidate_law_live_v1', 'tasks': tasks,
            'settings': SETTINGS, 'endpoint': transport.ENDPOINT,
            'formal_calls': 27, 'maximum_calls': 29, 'max_technical_retries': 2,
            'timeout_seconds': 3600, 'interval_seconds': 3, 'max_workers': 27,
            'maximum_completion_tokens': 29 * SETTINGS['max_tokens'],
            'private_file_sha256': transport.sha(materials.OUT / 'private-draft.json'),
            'source_private_file_sha256': transport.sha(materials.OUT / 'private-draft.json'),
            'input_file_hashes': hashes, 'conditions': list(scoring.CONDITIONS),
            'schedule_sha256': materials.digest(tasks),
            'schedule': '9 public-prompt-hash ordered world blocks; three cyclic condition orders each3times;3s stagger',
            'retry_policy': 'first wave drains; up to2 lowest-index technical failures, once per slot; no content/correctness retry',
            'scoring': 'frozen v2 scoring:9 equal-weight world test accuracies per condition;64 private test points;invalid/missing0;descriptive paired contrasts',
            'historical_transport_fields': 'generation.kind retains old transport tag; valid_choice means valid binary DSL only; plan defines new endpoint',
            'authorization': 'User active goal approves27formal+2technical,DeepSeek thinking high,max_tokens131072;no expansion',
            'provider_calls_during_freeze': 0}
    OUT.mkdir(mode=0o700, exist_ok=False)
    transport._write_private_bytes(OUT / 'private.json', (materials.OUT / 'private-draft.json').read_bytes())
    transport._write_exclusive_json(PLAN_PATH, plan)
    validate()
    print(json.dumps({'frozen': True, 'formal_calls': 27, 'maximum_calls': 29,
                      'plan_sha256': transport.sha(PLAN_PATH), 'provider_calls': 0}))


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
    else:
        raise SystemExit('Usage: new_evidence_live_20260910.py freeze | validate | run --execute')


if __name__ == '__main__': main()
