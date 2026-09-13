"""Post-run read-only replay, with an optional exclusive audit artifact."""
import json
import sys
import shortcut_challenge_live_20260910 as live
import shortcut_challenge_analysis_20260910 as analysis


def audit():
    p, tasks = live._validate_plan()
    out = live.OUT
    g = live._read_json(out / 'generation.json')
    a = live._read_json(out / 'analysis.json')
    private = live._read_json(out / 'private.json')
    assert live.sha(out / 'private.json') == p['private_file_sha256']
    assert g['plan_file_sha256'] == a['plan_file_sha256'] == live.sha(out / 'plan.json')
    assert a['generation_file_sha256'] == live.sha(out / 'generation.json')
    events = [json.loads(line) for line in (out / 'attempts.jsonl').read_text().splitlines()]
    starts = [x for x in events if x['event'] == 'request_started']
    finishes = [x for x in events if x['event'] == 'attempt_finished']
    assert events[0]['plan_file_sha256'] == live.sha(out / 'plan.json')
    assert events[-1]['event'] == 'complete'
    assert len(starts) == len(finishes) == g['provider_calls'] == 18
    assert {(x['index'], x['attempt']) for x in starts} == {(i, 1) for i in range(18)}
    assert len(g['records']) == len(g['slots']) == 18
    by_id = {r['task_id']: r for r in g['records']}
    assert len(by_id) == 18
    for i, task in enumerate(tasks):
        saved = live._read_json(out / f'attempt-{i:02d}-01.json')
        record = by_id[task['task_id']]
        slot = g['slots'][i]
        assert saved['status'] == slot['status'] == 'response'
        assert saved['record'] == slot['record'] == record
        assert saved['task_id'] == slot['task_id'] == task['task_id']
        assert record['prompt_sha256'] == task['prompt_sha256']
        assert slot['attempt_count'] == 1
        event = next(x for x in finishes if x['index'] == i)
        assert event['record'] == record and event['attempt'] == 1
        raw = json.loads((out / saved['raw_response_file']).read_bytes())
        replay = live.base.record_response(task, raw, record['response']['latency_ms'])
        assert replay == record
    rows = [{'candidate_index': pair['candidate_index'], 'stratum': pair['stratum'],
             **analysis.score_pair(pair, by_id)} for pair in private['pairs']]
    assert rows == a['world_results']
    assert sum(x['own'] for x in rows) == a['own']
    assert sum(x['cross'] for x in rows) == a['cross']
    assert sum(x['complete_switch'] for x in rows) == a['complete_switch']
    assert g['usage_complete'] is True and not g['technical_failures']
    for k in ('input_tokens', 'output_tokens'):
        assert sum(r['response'][k] for r in g['records']) == g['known_response_usage'][k]
    return {'kind': 'nine_world_live_response_replay_audit', 'verified': True,
            'requests_verified': 18, 'raw_response_replays': 18, 'world_scores_replayed': 9,
            'technical_retries': 0, 'source_bindings_valid': True,
            'plan_sha256': live.sha(out / 'plan.json'),
            'generation_sha256': live.sha(out / 'generation.json'),
            'analysis_sha256': live.sha(out / 'analysis.json'),
            'ledger_sha256': live.sha(out / 'attempts.jsonl'),
            'new_provider_calls_during_audit': 0}


if __name__ == '__main__':
    result = audit()
    if sys.argv[1:] == ['--save']:
        live._write_exclusive_json(live.OUT / 'response-audit.json', result)
    elif sys.argv[1:]:
        raise SystemExit('Usage: shortcut_challenge_live_audit_20260910.py [--save]')
    print(json.dumps(result, indent=2))
