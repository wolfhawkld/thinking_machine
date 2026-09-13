"""Aggregate only completed files from the offline search ledger."""
import json
from pathlib import Path
import sys
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shortcut_challenge_runner_20260909 as runner
import shortcut_challenge_engine_20260909 as engine


def summarize():
    out = runner.OUT
    state = json.loads((out / 'state.json').read_text())
    manifest = json.loads((out / 'manifest.json').read_text())
    if manifest != runner.manifest():
        raise ValueError('Manifest changed after execution')
    pools = {n: [] for n in ('unused_strict', 'wrong_either', 'wrong_both',
                             'strict_novelty_decoy_both', 'novelty_node_tie_both')}
    policies = {}
    duplicates = 0
    for entry in state['completed']:
        path = out / entry['file']
        if runner.sha(path) != entry['sha256']:
            raise ValueError('Completed file changed')
        world = json.loads(path.read_text())
        unsigned = {k:v for k,v in world.items() if k != 'content_sha256'}
        if world['content_sha256'] != engine.digest(unsigned):
            raise ValueError('World digest changed')
        if world['contexts_completed'] != 105 or world['actions_completed'] != 1050:
            raise ValueError('Partial world in completed denominator')
        if entry['duplicate_excluded']:
            duplicates += 1
            continue
        for name in pools:
            pools[name].append({'candidate_index': world['candidate_index'],
                               'pair_candidates': {engine.f.STRICT_TIER: world['challenge_groups'][name]}})
        for pair in world['policy_comparisons']:
            for policy, scores in pair['policies'].items():
                row = policies.setdefault(policy, Counter(pair_witnesses=0, own=0, cross=0, complete_switch=0))
                row['pair_witnesses'] += 1
                row.update(scores)
    return {'kind': 'bounded_new_development_search_summary', 'status': state['status'],
            'candidate_cap': 128, 'completed_worlds': len(state['completed']),
            'incomplete_or_unscanned_worlds': 128-len(state['completed']),
            'duplicate_excluded_worlds': duplicates, 'elapsed_seconds': state['elapsed_seconds'],
            'calibration_elapsed_seconds': state.get('calibration_elapsed_seconds'),
            'provider_calls': 0, 'model_outputs_read': False, 'benchmark_minted': False,
            'challenge_pools': {n: engine.helpers().summarize(rows) for n,rows in pools.items()},
            'policies_over_all_strict_pair_witnesses_not_independent_worlds': policies,
            'scope': 'Descriptive development construction; incomplete prefixes not full-range infeasibility.',
            'manifest_sha256': runner.sha(out/'manifest.json'), 'state_sha256': runner.sha(out/'state.json'),
            'summary_source_sha256': runner.sha(Path(__file__))}


if __name__ == '__main__':
    report = summarize()
    runner.save(runner.OUT/'summary.json', report, exclusive=True)
    print(json.dumps(report, indent=2))
