"""Saved-label pair-condition census, without new targets or oracle work."""
import hashlib
import itertools
import json
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import shortcut_challenge_runner_20260909 as runner


def run():
    folder=runner.ROOT/'artifacts/shortcut-challenge-search-full-range-20260909'
    state=json.loads((folder/'state.json').read_text())
    assert state['status']=='complete_fixed_range' and len(state['completed'])==128
    counts=Counter();worlds=Counter()
    for entry in state['completed']:
        path=folder/entry['file']
        assert runner.sha(path)==entry['sha256']
        world=json.loads(path.read_text())
        valid=[p for p in world['profiles'] if len(p['nonconstant_k4_raw_action_indices'])==1 and not p['constant_k4_raw_action_indices']]
        counts['unique_nonconstant_no_constant_contexts']+=len(valid)
        worlds['has_eligible_single_context']+=bool(valid)
        found=set()
        strict_count=0
        for a,b in itertools.combinations(valid,2):
            tests={'same_stratum_complexity_distinct_behavior':a['stratum']==b['stratum'] and a['complexity_bucket']==b['complexity_bucket'] and a['motif_behavior_hash']!=b['motif_behavior_hash'],
                   'equal_K2':len(a['k2_raw_action_indices'])==len(b['k2_raw_action_indices']),
                   'different_correct_action':a['nonconstant_k4_raw_action_indices']!=b['nonconstant_k4_raw_action_indices']}
            counts['single_eligible_context_combinations']+=1
            for name,ok in tests.items():
                if ok:counts[name+'_alone_pairs']+=1
            for omit in tests:
                if all(v for k,v in tests.items() if k!=omit):
                    name='all_except_'+omit;counts[name]+=1;found.add(name)
            if all(tests.values()):counts['strict_pairs']+=1;found.add('strict_pairs');strict_count+=1
        assert strict_count==len(world['pair_candidates']['strict_unique_nonconstant_switch'])
        for name in found:worlds[name]+=1
    return {'kind':'pair_condition_census_existing_labels','provider_calls':0,'new_targets_drawn':0,'oracle_runs':0,
            'context_and_pair_counts':dict(counts),'world_counts':dict(worlds),
            'original_strict_pair_counts_reproduced_for_all_128_worlds':True,
            'conditions_removed_only_for_diagnostic_not_new_benchmark':True,
            'source_sha256':runner.sha(Path(__file__)),'state_sha256':runner.sha(folder/'state.json')}


if __name__=='__main__':
    result=run();runner.save(Path(__file__).with_suffix('.json'),result,exclusive=True)
    print(json.dumps(result,indent=2))
