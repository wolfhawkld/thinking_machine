"""Read-only terminal transport replay; --save writes one exclusive audit."""
import json
import sys

import context_association_live_20260910 as live


def audit():
    plan, tasks = live.validate()
    out, t = live.OUT, live.transport
    generation = t._read_json(out / 'generation.json')
    if generation['plan_file_sha256'] != t.sha(live.PLAN_PATH):
        raise ValueError('Generation plan mismatch')
    events = [json.loads(line) for line in (out / 'attempts.jsonl').read_text().splitlines()]
    if events[0]['event'] != 'start' or events[0]['plan_file_sha256'] != t.sha(live.PLAN_PATH) or events[-1]['event'] != 'complete':
        raise ValueError('Ledger is not terminal for this plan')
    starts = [e for e in events if e['event'] == 'request_started']
    finishes = [e for e in events if e['event'] == 'attempt_finished']
    keys = [(e['index'], e['attempt']) for e in starts]
    if len(set(keys)) != len(keys) or set(keys) != {(e['index'], e['attempt']) for e in finishes} or len(finishes) != len(starts):
        raise ValueError('Attempt event bijection failed')
    if len(starts) != generation['provider_calls'] or not 0 <= len(starts) <= 56:
        raise ValueError('Physical budget mismatch')
    if sum(attempt == 2 for _, attempt in keys) > 2 or any(not 0 <= i < 54 or attempt not in (1, 2) for i, attempt in keys):
        raise ValueError('Invalid attempt indices or retry budget')
    if len(generation['slots']) != 54:
        raise ValueError('Lost task slots')
    replays = 0
    finish_map = {(e['index'], e['attempt']): e for e in finishes}
    for event in finishes:
        i, attempt = event['index'], event['attempt']
        task = tasks[i]
        if event['task_id'] != task['task_id']:
            raise ValueError('Event/task mismatch')
        saved = t._read_json(out / f'attempt-{i:02d}-{attempt:02d}.json')
        if any(saved[k] != event[k] for k in ('index', 'attempt', 'task_id', 'status', 'record', 'failure', 'raw_response_file')):
            raise ValueError('Attempt artifact differs from ledger')
        if attempt == 2:
            first = finish_map.get((i, 1))
            if not first or first['status'] != 'technical_failure' or not first['failure']['retryable']:
                raise ValueError('Unjustified technical retry')
        if event['status'] == 'response':
            record = event['record']
            raw = t._read_json(out / event['raw_response_file'])
            replay = t.base.record_response(task, raw, record['response']['latency_ms'])
            if replay != record:
                raise ValueError('Raw response replay mismatch')
            replays += 1
    records = []
    for i, slot in enumerate(generation['slots']):
        task = tasks[i]
        attempts = [a for index, a in keys if index == i]
        if slot['index'] != i or slot['task_id'] != task['task_id'] or slot['prompt_sha256'] != task['prompt_sha256']:
            raise ValueError('Slot mapping mismatch')
        if slot['attempt_count'] != len(attempts):
            raise ValueError('Slot attempt count mismatch')
        if attempts:
            final = finish_map[i, max(attempts)]
            if slot['record'] != final['record'] or slot['status'] != final['status']:
                raise ValueError('Slot does not retain final attempt')
        elif slot['status'] != 'not_dispatched' or slot['record'] is not None:
            raise ValueError('Undispatched slot mismatch')
        if slot['record'] is not None: records.append(slot['record'])
    if records != generation['records']:
        raise ValueError('Generation records differ from terminal slots')
    for metric in ('input_tokens', 'output_tokens'):
        if sum(r['response'][metric] for r in records) != generation['known_response_usage'][metric]:
            raise ValueError('Usage sum mismatch')
    result = {'kind': 'context_association_response_replay', 'verified': True,
              'physical_attempts': len(starts), 'task_slots': 54,
              'raw_response_replays': replays, 'technical_retries': sum(a == 2 for _, a in keys),
              'plan_sha256': t.sha(live.PLAN_PATH), 'generation_sha256': t.sha(out / 'generation.json'),
              'ledger_sha256': t.sha(out / 'attempts.jsonl'), 'new_provider_calls': 0}
    if (out / 'analysis.json').exists(): result['analysis_sha256'] = t.sha(out / 'analysis.json')
    return result


if __name__ == '__main__':
    if sys.argv[1:] not in ([], ['--save']): raise SystemExit('Usage: context_association_response_audit_20260910.py [--save]')
    result = audit()
    if sys.argv[1:]: live.transport._write_exclusive_json(live.OUT / 'response-audit.json', result)
    print(json.dumps(result, indent=2))
