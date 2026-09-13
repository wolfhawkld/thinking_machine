"""Independent descriptive scoring of the frozen nine-world challenge."""
from collections import Counter
import json

import shortcut_challenge_materials_20260910 as m


def score_pair(pair, records):
    selected, valid = {}, {}
    for arm, definition in pair['arms'].items():
        r = records.get(definition['task_id'])
        valid[arm] = bool(r and r['valid_choice'] and r['selected_option_id'] in pair['option_to_raw_action'])
        selected[arm] = pair['option_to_raw_action'][r['selected_option_id']] if valid[arm] else None
    own = sum(selected[a] == pair['arms'][a]['correct_raw_action'] for a in selected)
    cross = int(selected['context_a'] == pair['arms']['context_b']['correct_raw_action']) + int(selected['context_b'] == pair['arms']['context_a']['correct_raw_action'])
    return {'own': own, 'cross': cross, 'complete_switch': own == 2,
            'both_valid': all(valid.values()), 'valid_arms': sum(valid.values()),
            'selected_raw_actions': selected}


def analyze():
    p = m.legacy.base.read(m.OUT / 'plan.json')
    g = m.legacy.base.read(m.OUT / 'generation.json')
    if g['plan_file_sha256'] != m.legacy.base.sha(m.OUT / 'plan.json'):
        raise ValueError('Generation plan binding changed')
    for path, digest in p['input_file_hashes'].items():
        if m.legacy.base.sha(m.ROOT / path) != digest:
            raise ValueError('Frozen source changed: ' + path)
    if m.legacy.base.sha(m.OUT / 'private.json') != p['private_file_sha256']:
        raise ValueError('Private labels changed')
    private = m.legacy.base.read(m.OUT / 'private.json')
    expected = {t['task_id']: t for t in p['tasks']}
    records = {}
    for r in g['records']:
        if r['task_id'] in records or r['task_id'] not in expected or r['prompt_sha256'] != expected[r['task_id']]['prompt_sha256']:
            raise ValueError('Duplicate, unknown or mismatched response')
        records[r['task_id']] = r
    rows, comparisons = [], {}
    for pair in private['pairs']:
        scored = score_pair(pair, records)
        rows.append({'candidate_index': pair['candidate_index'], 'stratum': pair['stratum'], **scored})
        for policy, baseline in pair['policies'].items():
            c = comparisons.setdefault(policy, Counter(model_only=0, baseline_only=0, both=0, neither=0,
                                                       baseline_own=0, baseline_cross=0, baseline_complete_switch=0))
            a, b = scored['complete_switch'], baseline['complete_switch']
            c['both' if a and b else 'model_only' if a else 'baseline_only' if b else 'neither'] += 1
            c['baseline_own'] += baseline['own']
            c['baseline_cross'] += baseline['cross']
            c['baseline_complete_switch'] += b
    if len(rows) != 9 or len(expected) != 18:
        raise ValueError('Fixed denominators changed')
    result = {
        'kind': 'nine_world_challenge_descriptive_analysis', 'worlds': 9, 'tasks': 18,
        'plan_file_sha256': m.legacy.base.sha(m.OUT / 'plan.json'),
        'generation_file_sha256': m.legacy.base.sha(m.OUT / 'generation.json'),
        'own': sum(r['own'] for r in rows), 'cross': sum(r['cross'] for r in rows),
        'complete_switch': sum(r['complete_switch'] for r in rows),
        'valid_arms': sum(r['valid_arms'] for r in rows), 'received_parsed_records': len(records),
        'missing_or_unparsed_tasks': 18 - len(records),
        'both_valid_worlds': sum(r['both_valid'] for r in rows),
        'both_valid_subset_own': sum(r['own'] for r in rows if r['both_valid']),
        'both_valid_subset_cross': sum(r['cross'] for r in rows if r['both_valid']),
        'truncated_records': sum(r['thinking_telemetry']['output_truncated'] for r in records.values()),
        'reasoning_present_records': sum(r['thinking_telemetry']['reasoning_content_present'] for r in records.values()),
        'new_inferential_tests': False, 'world_results': rows, 'baseline_comparisons': comparisons,
        'scope': p['evidence_scope'], 'missing_invalid_scored_as_misses': True,
    }
    m.legacy.base.save(m.OUT / 'analysis.json', result)
    print(json.dumps({k: v for k, v in result.items() if k not in ('world_results', 'baseline_comparisons')}, indent=2))


if __name__ == '__main__': analyze()
