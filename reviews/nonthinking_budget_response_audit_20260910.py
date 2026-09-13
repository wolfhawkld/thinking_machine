"""Read-only raw/attempt/slot replay for the32+2 budget comparison."""
import json
import sys
import nonthinking_budget_live_20260910 as live


def audit():
    plan, tasks = live.validate()
    t, out = live.transport, live.OUT
    gen = t._read_json(out / 'generation.json')
    events = [json.loads(s) for s in (out / 'attempts.jsonl').read_text().splitlines()]
    plan_sha = t.sha(live.PLAN_PATH)
    if gen['plan_file_sha256'] != plan_sha or events[0] != {'event': 'start', 'plan_file_sha256': plan_sha} or events[-1]['event'] != 'complete':
        raise ValueError('Not terminal for frozen plan')
    starts = [e for e in events if e['event'] == 'request_started']
    finished = [e for e in events if e['event'] == 'attempt_finished']
    keys = [(e['index'], e['attempt']) for e in starts]
    terminal = {(e['index'], e['attempt']): e for e in finished}
    if len(set(keys)) != len(keys) or len(terminal) != len(finished) or set(keys) != set(terminal):
        raise ValueError('Attempt bijection mismatch')
    if len(keys) != gen['provider_calls'] or len(keys) > 34 or len(gen['slots']) != 32:
        raise ValueError('Denominator or budget changed')
    if any(not 0 <= i < 32 or a not in (1, 2) for i, a in keys) or sum(a == 2 for i, a in keys) > 2:
        raise ValueError('Bad attempt index')
    retry_indices = [e['index'] for e in events if e['event'] == 'retry_scheduled']
    first = {i: e for (i, a), e in terminal.items() if a == 1}
    stopped = any((e.get('failure') or {}).get('stop_new_dispatch') for e in first.values())
    eligible = [] if stopped else sorted(i for i,e in first.items() if (e.get('failure') or {}).get('retryable'))[:2]
    if retry_indices != eligible[:len(retry_indices)] or sorted(retry_indices) != sorted(i for i,a in keys if a == 2):
        raise ValueError('Retry selection changed')
    positions = {(e['index'], e['attempt']): n for n,e in enumerate(events) if e['event'] == 'attempt_finished'}
    for n,e in enumerate(events):
        if e['event'] == 'retry_scheduled' and n <= max(positions[i,1] for i in first):
            raise ValueError('Retry before first wave drained')
    replays = 0
    for (i,a), e in terminal.items():
        task = tasks[i]
        if e['task_id'] != task['task_id']: raise ValueError('Task mismatch')
        saved = t._read_json(out / f'attempt-{i:02d}-{a:02d}.json')
        if any(saved[k] != e[k] for k in ('index','attempt','task_id','status','record','failure','raw_response_file')):
            raise ValueError('Attempt/ledger mismatch')
        name = e['raw_response_file']
        if name is not None and name != f'raw-response-{i:02d}-attempt-{a:02d}.bin':
            raise ValueError('Raw filename mismatch')
        if e['status'] == 'response':
            replay = t.base.record_response(task, t._read_json(out / name), e['record']['response']['latency_ms'])
            if replay != e['record']: raise ValueError('Raw replay mismatch')
            replays += 1
    records = []
    for i, slot in enumerate(gen['slots']):
        task = tasks[i]
        if (slot['index'],slot['task_id'],slot['prompt_sha256']) != (i,task['task_id'],task['prompt_sha256']):
            raise ValueError('Slot binding changed')
        attempts = sorted(a for index,a in keys if index == i)
        if len(attempts) != slot['attempt_count']: raise ValueError('Slot attempts mismatch')
        if attempts:
            e = terminal[i,attempts[-1]]
            if slot['record'] != e['record'] or slot['status'] != e['status']: raise ValueError('Slot is not final attempt')
        elif slot['status'] != 'not_dispatched' or slot['record'] is not None: raise ValueError('Missing slot mismatch')
        if slot['record'] is not None: records.append(slot['record'])
    if records != gen['records']: raise ValueError('Generation records mismatch')
    for metric in ('input_tokens','output_tokens'):
        if sum(r['response'][metric] for r in records) != gen['known_response_usage'][metric]:
            raise ValueError('Usage mismatch')
    return {'verified': True, 'physical_attempts': len(keys), 'raw_replays': replays,
            'technical_retries': sum(a == 2 for i,a in keys), 'task_slots':32,
            'plan_sha256':plan_sha, 'generation_sha256':t.sha(out/'generation.json'),
            'ledger_sha256':t.sha(out/'attempts.jsonl'), 'new_provider_calls':0}


if __name__ == '__main__':
    if sys.argv[1:] not in ([], ['--save']): raise SystemExit('Usage: nonthinking_budget_response_audit_20260910.py [--save]')
    result = audit()
    if sys.argv[1:]: live.transport._write_exclusive_json(live.OUT/'response-audit.json', result)
    print(json.dumps(result, indent=2))
