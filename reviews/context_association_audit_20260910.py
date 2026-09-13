"""Offline candidate/auxiliary-reference separation; no oracle or provider calls."""
import itertools
import json
import sys
from pathlib import Path

import context_ablation_feasibility_20260910 as prior

sys.path.insert(0, str(prior.ROOT))
from src import dsl, spark_lineage as lineage
from src import spark_strong_k4_benchmark as benchmark

OUT = prior.ROOT / 'artifacts/context-association-audit-20260910'
DOMAIN = tuple(itertools.product(range(-2, 3), repeat=3))


def children(parent, fragment):
    """Keep all ten raw slots, including candidates failing public eligibility."""
    result = []
    for raw in range(10):
        action = benchmark._semantic_action(raw)
        old = lineage.get_subtree(parent, action.path)
        if action.operation == 'replace':
            inserted = fragment
        elif action.motif_side == 'left':
            inserted = (action.binary_operator, fragment, old)
        else:
            inserted = (action.binary_operator, old, fragment)
        result.append(dsl.canonicalize(lineage._replace_subtree(parent, action.path, inserted)))
    return result


def behavior(expr):
    return tuple(dsl.evaluate(expr, point) for point in DOMAIN)


def arithmetic_subtrees(expr):
    if expr[0] in ('var', 'const', 'neg', 'add', 'sub', 'mul'):
        yield expr
    for child in expr[1:]:
        if isinstance(child, tuple):
            yield from arithmetic_subtrees(child)


def matched_slots(programs, reference):
    fingerprint = behavior(reference)
    return [i for i, program in enumerate(programs)
            if any(behavior(node) == fingerprint for node in arithmetic_subtrees(program))]


def inspect(parent_text, actual_text, alternative_text):
    parent, actual, alternative = map(dsl.parse_sexpr, (parent_text, actual_text, alternative_text))
    programs = children(parent, actual)
    # Independent action enumeration from the original lineage grammar.
    motif = next(m for m in lineage.build_motif_library()
                 if dsl.canonicalize(m.ast) == dsl.canonicalize(actual))
    variants = tuple(lineage._candidate_action_variants(parent, motif))
    if len(variants) != 10:
        raise ValueError('Unexpected original action grammar')
    for raw, variant in enumerate(variants):
        action = benchmark._semantic_action(raw)
        if (variant.operation, variant.path, variant.binary_operator, variant.motif_side) != (
                action.operation, action.path, action.binary_operator, action.motif_side):
            raise ValueError('Action order drift')
        try:
            replay = lineage.apply_edit(parent, motif, variant)
        except lineage.LineageError:
            # Original grammar still displays this slot; do not delete invalid edits.
            continue
        if replay != programs[raw]:
            raise ValueError('Executable child mismatch')
    return {
        'candidate_count': len(programs),
        'candidate_hashes': [dsl.canonical_hash(p) for p in programs],
        'actual_semantic_subtree_slots': matched_slots(programs, actual),
        'alternative_semantic_subtree_slots': matched_slots(programs, alternative),
        'reference_behaviors_different': behavior(actual) != behavior(alternative),
        'same_reference_node_count': dsl.node_count(actual) == dsl.node_count(alternative),
        'same_reference_depth': dsl.depth(actual) == dsl.depth(alternative),
        'actual_table_characters': len(json.dumps(behavior(actual))),
        'alternative_table_characters': len(json.dumps(behavior(alternative))),
    }


def run():
    if prior.sha(prior.SOURCE / 'plan.json') != prior.PLAN_SHA:
        raise ValueError('Frozen source changed')
    plan = json.loads((prior.SOURCE / 'plan.json').read_text())
    private_path = prior.SOURCE / 'private.json'
    if prior.sha(private_path) != plan['private_file_sha256']:
        raise ValueError('Private source changed')
    private = json.loads(private_path.read_text())
    tasks = {t['task_id']: t for t in plan['tasks']}
    if len(tasks) != 18 or len(private['pairs']) != 9:
        raise ValueError('Source cohort changed')
    rows = []
    for ordinal, pair in enumerate(private['pairs']):
        prompts = [tasks[pair['arms'][arm]['task_id']]['rendered_prompt']
                   for arm in ('context_a', 'context_b')]
        for arm, prompt in zip(('context_a', 'context_b'), prompts):
            if prior.digest_text(prompt) != tasks[pair['arms'][arm]['task_id']]['prompt_sha256']:
                raise ValueError('Prompt hash mismatch')
        prior.inspect_pair(pair, tasks)
        prefix, a, _ = prior.split_context(prompts[0])
        _, b, _ = prior.split_context(prompts[1])
        parent = prefix.split('Frozen parent:\n')[1].strip()
        for arm, actual, alternative in (('context_a', a, b), ('context_b', b, a)):
            result = inspect(parent, actual, alternative)
            rows.append({'pair_ordinal': ordinal, 'arm': arm, **result})
    report = {
        'kind': 'auxiliary_behavioral_association_feasibility_not_live_plan',
        'worlds': 9, 'arms': len(rows), 'candidate_programs_checked': sum(r['candidate_count'] for r in rows),
        'arms_alternative_matches_no_candidate_subtree': sum(not r['alternative_semantic_subtree_slots'] for r in rows),
        'arms_actual_matches_all_candidates': sum(len(r['actual_semantic_subtree_slots']) == 10 for r in rows),
        'arms_reference_behaviors_different': sum(r['reference_behaviors_different'] for r in rows),
        'arms_same_node_count_and_depth': sum(r['same_reference_node_count'] and r['same_reference_depth'] for r in rows),
        'arms_same_unpadded_table_character_count': sum(r['actual_table_characters'] == r['alternative_table_characters'] for r in rows),
        'domain_points': len(DOMAIN), 'provider_calls': 0, 'oracle_rerun': False,
        'model_responses_read': False, 'new_worlds_generated': 0,
        'source_plan_sha256': prior.PLAN_SHA, 'source_private_sha256': prior.sha(private_path),
        'source_file_sha256': prior.sha(__file__),
        'scientific_gate': 'structural/finite-domain association only; not proof of relevance to the correct action',
        'rows': rows,
    }
    OUT.mkdir(mode=0o700, exist_ok=False)
    prior.save(OUT / 'audit.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, indent=2))


if __name__ == '__main__':
    run()
