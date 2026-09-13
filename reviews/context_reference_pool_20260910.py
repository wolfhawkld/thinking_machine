"""Check existing motif-library references; never generate new task worlds."""
from collections import Counter
import hashlib
import json

import context_association_audit_20260910 as m

NAMESPACE = 'auxiliary-structured-reference-v1-20260910'
OUT = m.prior.ROOT / 'artifacts/context-reference-pool-20260910'


def forbidden_vectors(parent, programs):
    return {m.behavior(node) for program in [parent, *programs]
            for node in m.arithmetic_subtrees(program)}


def eligible_references(actual, forbidden, library):
    actual_vector = m.behavior(actual.ast)
    values = Counter(actual_vector)
    return [motif for motif in library
            if motif.stratum == actual.stratum
            and motif.complexity_bucket == actual.complexity_bucket
            and Counter(m.behavior(motif.ast)) == values
            and m.behavior(motif.ast) not in forbidden
            and m.behavior(motif.ast) != actual_vector]


def choose(eligible, prompt_hash):
    if not eligible:
        return None
    return min(eligible, key=lambda motif: hashlib.sha256(
        (NAMESPACE + ':' + prompt_hash + ':' + motif.canonical_hash).encode()).hexdigest())


def run():
    prior = m.prior
    if prior.sha(prior.SOURCE / 'plan.json') != prior.PLAN_SHA:
        raise ValueError('Source plan changed')
    plan = json.loads((prior.SOURCE / 'plan.json').read_text())
    if prior.sha(prior.SOURCE / 'private.json') != plan['private_file_sha256']:
        raise ValueError('Source binding changed')
    # Public-only reference selection. Private labels/world results are not read.
    library = m.lineage.build_motif_library()
    rows = []
    for task in plan['tasks']:
        prompt = task['rendered_prompt']
        if prior.digest_text(prompt) != task['prompt_sha256']:
            raise ValueError('Prompt changed')
        prefix, fragment, _ = prior.split_context(prompt)
        parent = m.dsl.parse_sexpr(prefix.split('Frozen parent:\n')[1].strip())
        actual_ast = m.dsl.parse_sexpr(fragment)
        actual = next(motif for motif in library if motif.ast == actual_ast)
        programs = m.children(parent, actual_ast)
        candidates = eligible_references(actual, forbidden_vectors(parent, programs), library)
        selected = choose(candidates, task['prompt_sha256'])
        rows.append({'original_task_id': task['task_id'], 'eligible_count': len(candidates),
                     'eligible_ids': sorted(c.motif_id for c in candidates),
                     'actual_reference_sexpr': fragment,
                     'selected_reference_sexpr': m.dsl.to_sexpr(selected.ast) if selected else None,
                     'selected_reference_id': selected.motif_id if selected else None})
    result = {'kind': 'existing_library_reference_capacity_not_model_experiment',
              'source_plan_sha256': prior.PLAN_SHA, 'source_file_sha256': prior.sha(__file__),
              'library_size': len(library), 'tasks_checked': len(rows),
              'tasks_with_eligible_reference': sum(r['eligible_count'] > 0 for r in rows),
              'selection_namespace': NAMESPACE, 'selection_inputs': 'public prompt and fixed original motif library only',
              'private_labels_read': False, 'model_responses_read': False, 'provider_calls': 0,
              'new_worlds_generated': 0, 'oracle_rerun': False, 'rows': rows}
    OUT.mkdir(mode=0o700, exist_ok=False)
    prior.save(OUT / 'audit.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, indent=2))
    print('eligible counts:', [r['eligible_count'] for r in rows])


if __name__ == '__main__': run()
