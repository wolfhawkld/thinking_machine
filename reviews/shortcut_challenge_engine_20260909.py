"""New bounded development scan, reusing the original scientific primitives."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import dsl, spark_lineage, spark_closure
from src import spark_strong_k4_utilization_feasibility as f
from src import spark_strong_k4_utilization_primary_benchmark as b
from src.spark_world import generate_spark_world
from src.spark_compressor import SparkCompressor

NAMESPACE = 'spark-shortcut-challenge-search-v1-20260909'
SEED_SHA = 'e57785d0b2580a2918e965bc13f35010a32603dba1c8690d61111b76bb9f3655'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def seed_vector():
    return [f.derive_candidate_world_seed(NAMESPACE + ':world-seed', i) for i in range(128)]


def check_seed_collisions():
    seeds = seed_vector()
    old = set()
    sources = {}
    # Covers every configured reservation; additionally exclude entire retired v1 range.
    for path in sorted((ROOT / 'configs').glob('*.json')):
        value = json.loads(path.read_text())
        found = set()
        def visit(x, key=''):
            if isinstance(x, dict):
                for k, v in x.items(): visit(v, k)
            elif isinstance(x, list):
                if key in ('seeds', 'world_seeds'):
                    found.update(v for v in x if type(v) is int)
                for v in x: visit(v)
            elif key == 'world_seed' and type(x) is int:
                found.add(x)
        visit(value)
        if found:
            old.update(found)
            sources[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    old.update(f.derive_candidate_world_seed('spark-strong-k4-utilization-feasibility-v1:world-seed', i) for i in range(1024))
    if len(set(seeds)) != 128 or digest(seeds) != SEED_SHA or set(seeds) & old:
        raise ValueError('Seed collision or frozen vector mismatch')
    return {'unique': 128, 'collisions': 0, 'historical_union_count': len(old),
            'seed_vector_sha256': digest(seeds), 'source_hashes': sources}


def helpers():
    path = Path(__file__).with_name('shortcut-challenge-feasibility-20260909.py')
    spec = importlib.util.spec_from_file_location('challenge_helpers', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prompt_identity(parent, train):
    return digest({'parent': parent, 'D0': train})


def nonconstant_select(actions, order, mode):
    nc = [a for a in actions if a['public_features']['K1_supported']
          and a['public_features']['child_behavior_is_constant'] is False]
    if not nc:
        return b.prompt_support._select_baseline_raw_action('public-k1-max-parent-novelty-node-hash', actions, order)
    def rank(a):
        v = a['public_features']
        return ((-v['parent_behavior_novelty_count'],) if mode == 'novelty' else ()) + (
            v['node_count'], v['child_canonical_hash'], a['raw_action_index'])
    return min(nc, key=rank)['raw_action_index']


def scan_world(index, seed):
    if not 0 <= index < 128 or seed_vector()[index] != seed:
        raise ValueError('Candidate outside fixed plan')
    if spark_closure.CLOSURE_MAX_ROUNDS != 4:
        raise ValueError('Four-round contract changed')
    target_seed = f.derive_private_target_seed({'development_target_materialization':
                                               {'target_seed_namespace': NAMESPACE}}, seed)
    # Exactly one draw; not the old authorization-gated wrapper or a forged old plan.
    world = generate_spark_world(seed, target_seed=target_seed)
    parent = spark_lineage.select_parent(world)
    compressor = f._BehaviorCachedCompressor(SparkCompressor(world))
    parent_result = compressor.run(parent, max_rounds=4)
    lineages = f._lineage_index(spark_lineage.enumerate_reachable_children(world))
    profiles = [f._context_profile(world, motif, f._raw_actions(world, motif['motif_id']),
                                   lineages, compressor, parent_result)
                for motif in f.enumerate_full_motif_library()]
    if len(profiles) != 105 or any(p['raw_action_count'] != 10 for p in profiles):
        raise ValueError('Incomplete action universe')
    f._validate_profiles(profiles)
    pairs = f.pair_candidates_for_world(profiles)
    helper = helpers()
    # The diagnostic is the same 10-position cyclic order; old helper slots cap at 24.
    order = list(b.action_order_for_pair(index % 10, NAMESPACE + ':diagnostic-display'))
    cache = {}
    groups = {name: [] for name in ('unused_strict', 'wrong_either', 'wrong_both',
                                   'strict_novelty_decoy_both', 'novelty_node_tie_both')}
    comparisons = []
    by_motif = {p['motif_id']: p for p in profiles}
    for pair in pairs[f.STRICT_TIER]:
        b._validate_selected_pair(pair, pair['stratum'])
        diagnostics, arms = [], {}
        for arm in ('context_a', 'context_b'):
            motif = pair[arm + '_motif_id']
            if motif not in cache:
                cache[motif] = b._public_action_features(world, motif, lineages)
                if [a['raw_action_index'] for a in cache[motif] if a['public_features']['K1_supported']] != by_motif[motif]['k1_raw_action_indices']:
                    raise ValueError('Public support and scored profile disagree')
            correct = pair[arm + '_correct_raw_action_indices'][0]
            arms[arm] = (cache[motif], correct)
            diagnostics.append(helper.arm_diagnostic(cache[motif], correct))
        groups['unused_strict'].append(pair)
        if any(d['policy_wrong'] for d in diagnostics): groups['wrong_either'].append(pair)
        if all(d['policy_wrong'] for d in diagnostics): groups['wrong_both'].append(pair)
        for name in ('strict_novelty_decoy', 'novelty_node_tie'):
            if all(d[name] for d in diagnostics): groups[name + '_both'].append(pair)
        policies = {}
        for policy in (*b.prompt_support.BASELINE_POLICY_IDS, 'nonconstant-novelty', 'nonconstant-minnode'):
            selected = {arm: nonconstant_select(actions, order, policy.split('-')[1]) if policy.startswith('nonconstant-')
                        else b.prompt_support._select_baseline_raw_action(policy, actions, order)
                        for arm, (actions, _) in arms.items()}
            own = sum(selected[arm] == correct for arm, (_, correct) in arms.items())
            cross = int(selected['context_a'] == arms['context_b'][1]) + int(selected['context_b'] == arms['context_a'][1])
            policies[policy] = {'own': own, 'cross': cross, 'complete_switch': own == 2}
        comparisons.append({'pair_sha256': digest(pair), 'policies': policies})
    train = [{'point': list(e.point), 'label': e.label} for e in world.train]
    identity = {'parent': dsl.to_sexpr(parent), 'D0': train,
                'target_behavior': list(dsl.behavior_vector(world.target)),
                'bank': [dsl.canonical_hash(h) for h in world.hypotheses],
                'evidence_points': [list(e.point) for e in world.evidence],
                'test_points': [list(e.point) for e in world.test]}
    result = {'kind': 'shortcut_challenge_new_development_world', 'candidate_index': index,
              'world_seed': seed, 'world_hash': world.world_hash, 'target_index': world.target_index,
              'parent_canonical_hash': dsl.canonical_hash(parent), 'prompt_identity': prompt_identity(identity['parent'], train),
              'task_identity': digest(identity), 'profiles': profiles, 'pair_candidates': pairs,
              'challenge_groups': groups, 'policy_comparisons': comparisons,
              'diagnostic_action_order': order, 'display_order_not_a_frozen_live_schedule': True,
              'target_draws': 1, 'provider_calls': 0, 'model_outputs_read': False,
              'development_only': True, 'contexts_completed': 105, 'actions_completed': 1050,
              'four_rounds': True, 'benchmark_minted': False}
    return {**result, 'content_sha256': digest(result)}
