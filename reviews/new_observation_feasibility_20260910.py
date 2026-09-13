"""Target-unread query partition audit on the same nine frozen world structures.

Does not instantiate a target, generate new worlds, read model responses,
release target labels, rerun K4, or call providers.
"""
import hashlib
import json
import math
from pathlib import Path
import sys

import context_ablation_feasibility_20260910 as prior
import context_association_materials_20260910 as material

sys.path.insert(0, str(prior.ROOT))
from src import dsl, spark_world
from src.spark_strong_k4_utilization_feasibility import derive_candidate_world_seed

OUT = prior.ROOT / 'artifacts/new-observation-feasibility-20260910'
WORLD_NAMESPACE = 'spark-shortcut-challenge-search-v1-20260909:world-seed'
QUERY_NAMESPACE = 'new-observation-partition-v1-20260910'


def partition_rows(vectors, points, indices):
    if not vectors or any(len(v) != len(points) for v in vectors):
        raise ValueError('Malformed finite hypothesis bank')
    if any(value not in (0, 1) for v in vectors for value in v):
        raise ValueError('Only binary hypotheses supported')
    rows = []
    for index in indices:
        n1 = sum(v[index] for v in vectors)
        n0 = len(vectors) - n1
        rows.append({'point': list(points[index]), 'domain_index': index,
                     'branch_counts': [n0, n1],
                     'both_branches_nontrivial': min(n0, n1) >= 2,
                     'redundant': min(n0, n1) == 0,
                     'uniform_bank_expected_information_bits': sum(
                         -(n / len(vectors)) * math.log2(n / len(vectors)) for n in (n0, n1) if n)})
    return rows


def choose_query(rows, source_identity, informative):
    eligible = [r for r in rows if r['both_branches_nontrivial'] if not r['redundant']] if informative else [r for r in rows if r['redundant']]
    if not eligible:
        return None
    def rank(row):
        digest = hashlib.sha256((QUERY_NAMESPACE + ':' + source_identity + ':' + str(row['point'])).encode()).hexdigest()
        return ((abs(row['branch_counts'][0] - row['branch_counts'][1]), digest)
                if informative else (0, digest))
    return min(eligible, key=rank)


def run():
    if prior.sha(prior.SOURCE / 'plan.json') != prior.PLAN_SHA:
        raise ValueError('Original plan changed')
    plan = json.loads((prior.SOURCE / 'plan.json').read_text())
    private_path = prior.SOURCE / 'private.json'
    if prior.sha(private_path) != plan['private_file_sha256']:
        raise ValueError('Original cohort binding changed')
    # This file also contains old option labels. Only selected candidate indices
    # and arm task IDs enter this audit; no label/outcome drives query selection.
    metadata = json.loads(private_path.read_text())
    tasks = {task['task_id']: task for task in plan['tasks']}
    reports = []
    for ordinal, pair in enumerate(metadata['pairs']):
        index = pair['candidate_index']
        seed = derive_candidate_world_seed(WORLD_NAMESPACE, index)
        bank, train, evidence, test, _ = spark_world._world_structure(seed)
        if len(bank) != 256 or (len(train), len(evidence), len(test)) != (12, 49, 64):
            raise ValueError('Original structure sizes changed')
        sets = list(map(set, (train, evidence, test)))
        if any(sets[a] & sets[b] for a, b in ((0, 1), (0, 2), (1, 2))):
            raise ValueError('Original splits overlap')
        if set.union(*sets) != set(dsl.DOMAIN):
            raise ValueError('Unexpected unused/reserve domain')
        task = tasks[pair['arms']['context_a']['task_id']]
        c = material.public_components(task)
        vectors = tuple(dsl.behavior_vector(h, dsl.DOMAIN) for h in bank)
        if len(set(vectors)) != len(bank):
            raise ValueError('Bank behavior aliases changed')
        if tuple(tuple(row['point']) for row in c['D0']) != train:
            raise ValueError('Seed-derived train differs from original public prompt')
        if not all(all(dsl.evaluate(h, row['point']) == row['label'] for row in c['D0']) for h in bank):
            raise ValueError('Reconstructed bank disagrees with frozen D0')
        domain_indices = {point: i for i, point in enumerate(dsl.DOMAIN)}
        rows = partition_rows(vectors, dsl.DOMAIN, [domain_indices[p] for p in evidence])
        informative = choose_query(rows, task['prompt_sha256'], True)
        redundant = choose_query(rows, task['prompt_sha256'], False)
        reports.append({'ordinal': ordinal, 'candidate_index': index, 'bank_size': len(bank),
                        'original_task_id': task['task_id'],
                        'bank_sha256': prior.digest_text(json.dumps([dsl.canonical_hash(h) for h in bank])),
                        'evidence_points_checked': len(rows),
                        'informative_nontrivial_points': sum(r['both_branches_nontrivial'] for r in rows),
                        'redundant_evidence_points': sum(r['redundant'] for r in rows),
                        'selected_informative_query': informative, 'selected_redundant_query': redundant,
                        'all_evidence_partition_rows': rows})
    if len(reports) != 9 or len({r['candidate_index'] for r in reports}) != 9:
        raise ValueError('Fixed nine-world cohort changed')
    result = {'kind': 'NEW_OBSERVATION_QUERY_FEASIBILITY_NOT_MODEL_EXPERIMENT',
              'worlds': 9, 'hypotheses_per_world': 256, 'evidence_locations_checked': 441,
              'worlds_with_informative_nontrivial_query': sum(r['selected_informative_query'] is not None for r in reports),
              'worlds_with_redundant_evidence_query': sum(r['selected_redundant_query'] is not None for r in reports),
              'reserved_unused_points_per_world': 0,
              'source_plan_sha256': prior.PLAN_SHA, 'source_private_sha256': prior.sha(private_path),
              'source_sha256': {str(Path(p).relative_to(prior.ROOT)): prior.sha(p)
                                for p in (__file__, spark_world.__file__, dsl.__file__, material.__file__)},
              'provider_calls': 0, 'model_responses_read': False, 'target_instantiated': False,
              'additional_target_observation_labels_read_or_released': False, 'public_D0_labels_read': True,
              'new_worlds_generated': 0,
              'oracle_rerun': False, 'private_file_metadata_read': True, 'old_correct_option_labels_used': False,
              'limitations': ['hypothesis-count contraction is not model entropy',
                              'uniform-bank expected information is a reference calculation, not a proven prior for selected worlds',
                              'using an evidence observation changes the information protocol; old K4 cannot be inherited without revalidation',
                              'true added observations test new evidence use, not target-independent inspiration'],
              'world_reports': reports}
    OUT.mkdir(mode=0o700, exist_ok=False)
    prior.save(OUT / 'audit.json', result)
    print(json.dumps({k: v for k, v in result.items() if k not in ('world_reports', 'source_sha256')}, indent=2))
    print('selected branch counts:', [r['selected_informative_query']['branch_counts'] if r['selected_informative_query'] else None for r in reports])


if __name__ == '__main__': run()
