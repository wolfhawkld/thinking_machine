"""Offline census of unused strict worlds; no provider or response reads."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import spark_strong_k4_utilization_primary_benchmark as benchmark
from src import spark_strong_k4_utilization_feasibility as feasibility
from src import spark_lineage


def read(path):
    return json.loads(path.read_text())


def arm_diagnostic(actions, correct):
    nc = [a for a in actions if a['public_features']['K1_supported']
          and a['public_features']['child_behavior_is_constant'] is False]
    by_raw = {a['raw_action_index']: a['public_features'] for a in nc}
    if correct not in by_raw:
        raise ValueError('strict correct action missing from public nonconstant support')
    f = by_raw[correct]
    def rank(a):
        p = a['public_features']
        return (-p['parent_behavior_novelty_count'], p['node_count'],
                p['child_canonical_hash'], a['raw_action_index'])
    selected = min(nc, key=rank)['raw_action_index']
    return {
        'policy_wrong': selected != correct,
        'strict_novelty_decoy': any(p['parent_behavior_novelty_count'] > f['parent_behavior_novelty_count']
                                   for r, p in by_raw.items() if r != correct),
        'novelty_node_tie': any((p['parent_behavior_novelty_count'], p['node_count']) ==
                               (f['parent_behavior_novelty_count'], f['node_count'])
                               for r, p in by_raw.items() if r != correct),
    }


def summarize(worlds):
    rows = [w for w in worlds if w['pair_candidates'][feasibility.STRICT_TIER]]
    strata = Counter()
    for w in rows:
        strata.update({p['stratum'] for p in w['pair_candidates'][feasibility.STRICT_TIER]})
    eligible = {w['candidate_index']: tuple(s for s in spark_lineage.MOTIF_STRATA
                if any(p['stratum'] == s for p in w['pair_candidates'][feasibility.STRICT_TIER])) for w in rows}
    q = 0
    for candidate in range(len(rows) // 4, 0, -1):
        if feasibility._matching_for_capacity(eligible, strata=spark_lineage.MOTIF_STRATA,
                                             capacity_per_stratum=candidate)['complete']:
            q = candidate
            break
    return {'unique_worlds': len(rows), 'pair_candidates': sum(len(w['pair_candidates'][feasibility.STRICT_TIER]) for w in rows),
            'worlds_eligible_per_stratum_nonadditive': dict(strata), 'maximum_balanced_worlds': 4*q}


def run():
    config = read(ROOT / 'configs/spark-strong-k4-utilization-primary-benchmark-v2.json')
    geometry = config['upstream_geometry']
    paths = {name: ROOT / geometry[key] for name, key in [
        ('manifest', 'artifact_manifest_relative_path'), ('config', 'feasibility_config_relative_path'),
        ('plan', 'feasibility_plan_relative_path')]}
    for name, key in [('manifest', 'artifact_manifest_file_sha256'), ('config', 'feasibility_config_file_sha256'),
                      ('plan', 'feasibility_plan_file_sha256')]:
        if hashlib.sha256(paths[name].read_bytes()).hexdigest() != geometry[key]:
            raise ValueError('upstream file binding mismatch')
    private_path = ROOT / 'artifacts/spark-strong-k4-utilization-primary-benchmark-v2-20260827/private.json'
    # Only identities from the old scoring key, never model responses.
    if hashlib.sha256(private_path.read_bytes()).hexdigest() != 'bbe76032ba8d120c9eb7866cabb3643e81f619fbf91bfbed4c40237e3588f06a':
        raise ValueError('old cohort binding mismatch')
    old = {p['world_binding']['candidate_index'] for p in read(private_path)['pairs']}
    pools = {name: [] for name in ('unused_strict', 'wrong_either', 'wrong_both',
                                   'strict_novelty_decoy_both', 'novelty_node_tie_both')}
    feature_contexts = 0
    def validator(shard, **kwargs):
        nonlocal feature_contexts
        start, end, worlds = feasibility._validate_shard(shard, **kwargs)
        for w in worlds:
            pairs = w['pair_candidates'][feasibility.STRICT_TIER]
            if w['candidate_index'] in old or not pairs:
                continue
            public_world, _ = benchmark._target_free_prompt_context(w['world_seed'], w['parent_canonical_hash'])
            lineages = benchmark.strong_scan._lineage_index(spark_lineage.enumerate_reachable_children(public_world))
            profiles = {p['motif_id']: p for p in w['profiles']}
            cache = {}
            groups = {name: [] for name in pools}
            for p in pairs:
                diagnostics = []
                for arm in ('context_a', 'context_b'):
                    motif = p[arm + '_motif_id']
                    if motif not in cache:
                        features = benchmark._public_action_features(public_world, motif, lineages)
                        profile = profiles[motif]
                        if [a['raw_action_index'] for a in features if a['public_features']['K1_supported']] != profile['k1_raw_action_indices']:
                            raise ValueError('K1 public replay mismatch')
                        for a in features:
                            stored = profile['actions'][a['raw_action_index']]
                            f = a['public_features']
                            if f['child_behavior_hash'] != stored['child_behavior_hash'] or f['child_behavior_is_constant'] != stored['child_behavior_is_constant']:
                                raise ValueError('public child replay mismatch')
                        cache[motif] = features
                        feature_contexts += 1
                    diagnostics.append(arm_diagnostic(cache[motif], p[arm + '_correct_raw_action_indices'][0]))
                groups['unused_strict'].append(p)
                if any(d['policy_wrong'] for d in diagnostics): groups['wrong_either'].append(p)
                if all(d['policy_wrong'] for d in diagnostics): groups['wrong_both'].append(p)
                for name in ('strict_novelty_decoy', 'novelty_node_tie'):
                    if all(d[name] for d in diagnostics): groups[name + '_both'].append(p)
            for name, selected in groups.items():
                pools[name].append({'candidate_index': w['candidate_index'],
                                    'pair_candidates': {feasibility.STRICT_TIER: selected}})
        return start, end, worlds
    compact, audit = benchmark._stream_validated_strict_worlds(
        config, read(paths['manifest']), read(paths['config']), read(paths['plan']),
        project_root=ROOT, shard_validator=validator)
    return {'kind': 'unused_development_shortcut_challenge_feasibility', 'provider_calls': 0,
            'model_outputs_read': False, 'new_worlds_generated': False, 'benchmark_minted': False,
            'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'old_worlds_excluded': len(old), 'public_feature_contexts_replayed': feature_contexts,
            'shard_validation': audit, 'all_strict': summarize(compact),
            'challenge_pools': {name: summarize(rows) for name, rows in pools.items()},
            'scope': 'Existing 1024 development candidates only; target-conditioned challenge selection, not heldout generalization.',
            'tie_definition': 'Correct action shares novelty and node count with a wrong supported nonconstant action; hash and other features need not match.',
            'categories_overlap': True, 'new_inferential_tests': False}


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
