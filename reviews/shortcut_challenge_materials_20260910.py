"""Freeze nine-world, target-free provider materials before any model response."""
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys

import shortcut_challenge_resume982_20260910 as search

ROOT = search.base.ROOT
OUT = ROOT / 'artifacts/shortcut-challenge-live-20260910'
HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location('legacy_thinking', HERE.with_name('deepseek-thinking-128k-live-20260909.py'))
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
e = search.engine
b = e.b


def choose_pair(world):
    eligible = []
    for pair, comparison in zip(world['pair_candidates'][e.f.STRICT_TIER], world['policy_comparisons'], strict=True):
        if e.digest(pair) != comparison['pair_sha256']:
            raise ValueError('Pair/comparison binding mismatch')
        if all(not comparison['policies'][p]['complete_switch'] for p in ('nonconstant-minnode', 'nonconstant-novelty')):
            eligible.append((e.digest(pair), pair, comparison))
    return min(eligible, key=lambda row: row[0]) if eligible else None


def build():
    audit = json.loads((search.OUT / 'audit.json').read_text())
    if not audit['verified'] or not audit['summary_replay_equal'] or audit['completed_worlds'] != 1024:
        raise ValueError('Requires verified full search')
    for name in ('state', 'manifest', 'summary'):
        if search.base.sha(search.OUT / (name + '.json')) != audit[name + '_sha256']:
            raise ValueError('Search changed after audit')
    manifest = json.loads((search.OUT / 'manifest.json').read_text())
    if manifest != search.manifest():
        raise ValueError('Search dependencies changed')
    state = json.loads((search.OUT / 'state.json').read_text())
    pairs, tasks, sources = [], [], {}
    catalog = b._motif_catalog()
    for row in state['completed']:
        path = search.OUT / row['file']
        if search.base.sha(path) != row['sha256']:
            raise ValueError('World file changed')
        w = search.validate_world(path, row['index'], manifest['seeds'][row['index']])
        if row['duplicate_excluded']:
            continue
        selected = choose_pair(w)
        if selected is None:
            continue
        pair_hash, pair, comparison = selected
        b._validate_selected_pair(pair, pair['stratum'])
        world, context = b._target_free_prompt_context(w['world_seed'], w['parent_canonical_hash'])
        if e.prompt_identity(context['parent'], context['D0']) != w['prompt_identity']:
            raise ValueError('Public context identity mismatch')
        lineages = e.f._lineage_index(e.spark_lineage.enumerate_reachable_children(world))
        order = w['diagnostic_action_order']
        anchor = e.digest({'namespace': 'shortcut-challenge-live-20260910', 'world': row['index'], 'pair': pair_hash})
        options = b._option_ids(anchor)
        mapping = dict(zip(options, order, strict=True))
        arms, selections = {}, {}
        for arm in ('context_a', 'context_b'):
            motif = pair[arm + '_motif_id']
            features = b._public_action_features(world, motif, lineages)
            raw = pair[arm + '_correct_raw_action_indices'][0]
            selections[arm] = {
                p: e.nonconstant_select(features, order, p.split('-')[1]) if p.startswith('nonconstant-')
                else b.prompt_support._select_baseline_raw_action(p, features, order)
                for p in comparison['policies']
            }
            prompt = b.prompt_support.render_fair_choice_prompt(context, catalog[motif]['motif_sexpr'], order, options)
            task_id = 'TASK' + e.digest({'anchor': anchor, 'arm': arm})[:20]
            import hashlib
            task = {'task_id': task_id, 'rendered_prompt': prompt,
                    'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
            tasks.append(task)
            arms[arm] = {'task_id': task_id, 'correct_raw_action': raw,
                         'correct_option_id': next(o for o, r in mapping.items() if r == raw)}
        replay = {}
        for policy in comparison['policies']:
            own = sum(selections[a][policy] == arms[a]['correct_raw_action'] for a in arms)
            cross = int(selections['context_a'][policy] == arms['context_b']['correct_raw_action']) + int(selections['context_b'][policy] == arms['context_a']['correct_raw_action'])
            replay[policy] = {'own': own, 'cross': cross, 'complete_switch': own == 2}
        if replay != comparison['policies']:
            raise ValueError('All-policy public replay mismatch')
        pairs.append({'candidate_index': row['index'], 'task_identity': w['task_identity'],
                      'pair_sha256': pair_hash, 'stratum': pair['stratum'], 'arms': arms,
                      'option_to_raw_action': mapping, 'policies': replay})
        sources[str(path.relative_to(ROOT))] = row['sha256']
    if len(pairs) != 9 or len(tasks) != 18 or len({p['task_identity'] for p in pairs}) != 9:
        raise ValueError('Expected nine distinct worlds and eighteen tasks')
    # Same frozen, outcome-independent order on every reconstruction.
    tasks.sort(key=lambda t: e.digest({'schedule': 'shortcut-20260910', 'task_id': t['task_id']}))
    if len({t['prompt_sha256'] for t in tasks}) != 18:
        raise ValueError('Duplicate rendered task')
    OUT.mkdir(mode=0o700, exist_ok=False)
    legacy.base.save(OUT / 'private.json', {'pairs': pairs, 'selected_world_file_hashes': sources})
    files = [HERE, HERE.with_name('shortcut_challenge_live_20260910.py'),
             HERE.with_name('shortcut_challenge_analysis_20260910.py'),
             HERE.with_name('shortcut-challenge-live-plan-20260910.md'), legacy.HERE,
             Path(legacy.base.__file__), ROOT / 'src/providers/openai_compatible.py',
             ROOT / 'src/spark_strong_k4_utilization_primary_live.py', ROOT / 'src/runner.py']
    public = {
        'kind': 'nine_world_shortcut_challenge', 'created_utc': legacy.base.now(),
        'settings': legacy.SETTINGS, 'endpoint': legacy.base.ENDPOINT, 'timeout_seconds': legacy.TIMEOUT,
        'tasks': tasks, 'formal_calls': 18, 'maximum_calls': 20, 'max_technical_retries': 2,
        'interval_seconds': 3, 'max_workers': 18, 'maximum_completion_tokens': 20 * 131072,
        'selection': 'all nine joint-failure worlds; one minimum pair SHA256 per world; original diagnostic action order frozen for live',
        'retry_policy': 'after first wave, ascending task index; max one retry per failed slot; global max two; transport or HTTP429/5xx only; no invalid-content or envelope retry',
        'scoring': 'all nine worlds retained; missing/invalid arms are misses; report valid-pair subset separately, no inferential p-values',
        'evidence_scope': 'descriptive outcome-conditioned challenge; no generalization or entropy-mechanism claim',
        'private_file_sha256': legacy.base.sha(OUT / 'private.json'),
        'search_summary_sha256': audit['summary_sha256'],
        'strata': dict(Counter(p['stratum'] for p in pairs)),
        'input_file_hashes': {str(p.relative_to(ROOT)): legacy.base.sha(p) for p in files},
        'provider_calls_during_construction': 0, 'public_policy_replay_count': 26,
        'new_canary_calls': 0, 'canary_note': 'reuse historical working request/response contract; no extra canary budget; no claim of current availability verification',
    }
    legacy.base.save(OUT / 'plan.json', public)
    print(json.dumps({'worlds': len(pairs), 'tasks': len(tasks), 'strata': public['strata'],
                      'plan_sha256': legacy.base.sha(OUT / 'plan.json'), 'calls': 0}))


if __name__ == '__main__':
    build()
