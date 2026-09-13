"""Public-feature replay on existing data only; no new targets or oracle runs."""
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'reviews'))
import shortcut_challenge_engine_20260909 as engine
import shortcut_challenge_runner_20260909 as runner


def diagnose(actions, correct, order):
    supported=[a for a in actions if a['public_features']['K1_supported']]
    nc=[a for a in supported if a['public_features']['child_behavior_is_constant'] is False]
    if not correct or not set(correct)<=set(a['raw_action_index'] for a in nc):
        raise ValueError('Invalid nonconstant K4 correct set')
    selected={
        'minnode':engine.b.prompt_support._select_baseline_raw_action('public-k1-min-node-hash',actions,order),
        'nc_minnode':engine.nonconstant_select(actions,order,'minnode'),
        'nc_novelty':engine.nonconstant_select(actions,order,'novelty')}
    min_size=min(a['public_features']['node_count'] for a in nc)
    minima=[a['raw_action_index'] for a in nc if a['public_features']['node_count']==min_size]
    counts=Counter(contexts=1,correct_actions=len(correct),unique_correct_contexts=len(correct)==1,
                   only_one_nonconstant_candidate=len(nc)==1)
    for name,raw in selected.items(): counts[name+'_hits']=int(raw in correct)
    if len(correct)==1:
        counts['unique_correct_above_nc_minimum']=int(correct[0] not in minima)
        counts['unique_correct_tied_nc_minimum']=int(correct[0] in minima and len(minima)>1)
        counts['unique_correct_sole_nc_minimum']=int(minima==correct)
    return counts,selected


def old_cohort():
    path=ROOT/'artifacts/spark-strong-k4-utilization-primary-benchmark-v2-20260827/private.json'
    if runner.sha(path)!='bbe76032ba8d120c9eb7866cabb3643e81f619fbf91bfbed4c40237e3588f06a':
        raise ValueError('Old cohort drift')
    private=json.loads(path.read_text())
    totals=Counter();pairs=Counter(worlds=24)
    for pair in private['pairs']:
        hits={k:[] for k in ('minnode','nc_minnode','nc_novelty')}
        for arm in pair['arms'].values():
            correct=arm['correct_raw_action_indices']
            counts,selected=diagnose(arm['public_action_features'],correct,pair['action_order'])
            totals.update(counts)
            for name in hits: hits[name].append(selected[name] in correct)
        for name,values in hits.items(): pairs[name+'_complete']+=all(values)
        pairs['both_nc_policies_fail_on_same_arm_worlds']+=any(not hits['nc_minnode'][i] and not hits['nc_novelty'][i] for i in range(2))
    return {'contexts':dict(totals),'paired_worlds':dict(pairs),'private_source_sha256':runner.sha(path)}


def run():
    folder=ROOT/'artifacts/shortcut-challenge-search-full-range-20260909'
    state=json.loads((folder/'state.json').read_text())
    if state['status']!='complete_fixed_range' or len(state['completed'])!=128:
        raise ValueError('Requires complete fixed scan')
    totals=Counter();strict_totals=Counter();world_counts=Counter();cache_stats=Counter()
    for saved in state['completed']:
        path=folder/saved['file']
        if runner.sha(path)!=saved['sha256']:raise ValueError('Saved world drift')
        w=json.loads(path.read_text())
        if engine.digest({k:v for k,v in w.items() if k!='content_sha256'})!=w['content_sha256']:
            raise ValueError('World digest mismatch')
        profiles=[p for p in w['profiles'] if p['nonconstant_k4_raw_action_indices']]
        if not profiles: continue
        # Recreate target-free structure only; never draw or inspect a new target.
        public,_=engine.b._target_free_prompt_context(w['world_seed'],w['parent_canonical_hash'])
        lineages=engine.f._lineage_index(engine.spark_lineage.enumerate_reachable_children(public))
        strict_motifs={p[a+'_motif_id'] for p in w['pair_candidates'][engine.f.STRICT_TIER] for a in ('context_a','context_b')}
        correct_by_motif={};selected_by_motif={};failure={k:False for k in ('minnode','nc_minnode','nc_novelty')}
        for p in profiles:
            actions=engine.b._public_action_features(public,p['motif_id'],lineages)
            for row in actions:
                raw=row['raw_action_index'];features=row['public_features'];stored=p['actions'][raw]
                if features['K1_supported']!=stored['endpoint_flags']['K1']:
                    raise ValueError('K1 replay mismatch')
                if features['K1_supported'] and (features['child_behavior_hash']!=stored['child_behavior_hash'] or
                    features['child_behavior_is_constant']!=stored['child_behavior_is_constant']):
                    raise ValueError('Public child replay mismatch')
            correct=p['nonconstant_k4_raw_action_indices']
            counts,selected=diagnose(actions,correct,w['diagnostic_action_order'])
            totals.update(counts)
            if p['motif_id'] in strict_motifs: strict_totals.update(counts)
            for name,raw in selected.items():failure[name]|=raw not in correct
            correct_by_motif[p['motif_id']]=correct;selected_by_motif[p['motif_id']]=selected
            cache_stats['contexts_public_features_replayed']+=1
        for pair,row in zip(w['pair_candidates'][engine.f.STRICT_TIER],w['policy_comparisons'],strict=True):
            if engine.digest(pair)!=row['pair_sha256']:raise ValueError('Pair comparison binding mismatch')
            for name,policy in [('minnode','public-k1-min-node-hash'),('nc_minnode','nonconstant-minnode'),('nc_novelty','nonconstant-novelty')]:
                own=sum(selected_by_motif[pair[a+'_motif_id']][name] in correct_by_motif[pair[a+'_motif_id']] for a in ('context_a','context_b'))
                if own!=row['policies'][policy]['own']:raise ValueError('Existing policy arithmetic differs')
        world_counts['worlds_with_any_nonconstant_K4']+=1
        for name,bad in failure.items():world_counts[name+'_fails_on_some_K4_context_worlds']+=bad
    report={'kind':'minnode_structural_diagnostic_existing_data_only','provider_calls':0,'new_targets_drawn':0,
            'oracle_runs':0,'model_outputs_read':False,'new_worlds_scanned':0,
            'old_selected24':old_cohort(),'new128_pre_pair_nonconstant_K4_contexts':dict(totals),
            'new128_unique_contexts_participating_in_strict_pairs':dict(strict_totals),
            'new128_world_counts':dict(world_counts),'public_replay':dict(cache_stats),
            'context_counts_are_not_independent_world_counts':True,
            'new_scan_summary_sha256':runner.sha(folder/'summary.json'),
            'source_sha256':runner.sha(Path(__file__)),
            'interpretation':'Conditional descriptive comparison, not causal identification of node-count bias.'}
    return report


if __name__=='__main__':
    result=run()
    runner.save(Path(__file__).with_suffix('.json'),result,exclusive=True)
    print(json.dumps(result,indent=2))
