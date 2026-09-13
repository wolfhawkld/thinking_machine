"""Offline 27-task new-evidence draft; fixed old targets, separate public/test data."""
from collections import Counter
import json
from pathlib import Path
import sys

import new_observation_feasibility_20260910 as feasibility
import new_evidence_scoring_20260910 as scoring

prior = feasibility.prior
dsl = feasibility.dsl
world_module = feasibility.spark_world
OUT = prior.ROOT / 'artifacts/new-evidence-draft-v2-20260910'
EXTRA_START = '\nAdditional observation:\n'
EXTRA_END = '\nEnd additional observation.\n'


def digest(value):
    return prior.digest_text(json.dumps(value, sort_keys=True, separators=(',', ':')))


def repeat_row(d0, public_identity):
    return min(d0, key=lambda row: digest({'namespace': 'repeat-D0-v1-20260910', 'public_identity': public_identity, 'row': row}))


def render(d0, parent, additional):
    rows = '\n'.join(f"{tuple(r['point'])} -> {r['label']}" for r in d0)
    extra = 'No additional observation is supplied.' if additional is None else f"{tuple(additional['point'])} -> {additional['label']}"
    return (
        'Propose one candidate law for an unknown binary classifier from the observations below.\n'
        'Return exactly one JSON object with the single key expression whose value is a DSL S-expression string. '
        'Do not return an option ID, prose, markdown, or additional keys.\n\n'
        'The complete input domain has x1, x2, x3 each in {-2,-1,0,1,2}. The classifier must return only 0 or 1 '
        'on every domain point. All supplied observations are true, but may not uniquely determine the law.\n\n'
        'The generator family uses one gt or eq comparison of two small arithmetic expressions, '
        'with branches (const 1) and (const 0); the true classifier is nonconstant. '
        'Each compared expression is a variable, a small constant, a negated variable, '
        'or one add/sub/mul combining a variable with a variable or small constant (either order). '
        'You may propose a hypothesis using the legal DSL below.\n\n'
        'Legal arithmetic: (var x1), (var x2), (var x3), (const c) for integer c in {-3,-2,-1,0,1,2,3}, '
        '(neg A), (add A B), (sub A B), (mul A B). Predicates: (gt A B), (eq A B). '
        'Conditional expression: (ite P A B). A and B are expressions, P is a predicate. '
        'Maximum expression depth 5 (a leaf has depth 1), maximum 31 AST nodes including predicates.\n\n'
        f'Initial observations:\n{rows}\n\n'
        f'Initial candidate (fits initial observations, not guaranteed to be the true law):\n{parent}\n'
        + EXTRA_START + extra + EXTRA_END +
        '\nReturn a candidate consistent with all supplied observations that you expect to predict unseen inputs well. '
        'You may retain or revise the initial candidate. No further tool queries are available in this task.\n'
        'Required output schema: {"expression":"<DSL S-expression>"}\n')


def public_baselines(parent, observations, reservoir):
    # Selection consults public observations only, not private bank or heldout labels.
    fits = [h for h in reservoir if all(dsl.evaluate(h, r['point']) == r['label'] for r in observations)]
    if not fits: raise ValueError('No public-consistent reservoir rule')
    rank = lambda h: (dsl.node_count(h), dsl.canonical_hash(h))
    constants = [h for h in (('const', 0), ('const', 1)) if all(dsl.evaluate(h, r['point']) == r['label'] for r in observations)]
    return {'frozen_parent': parent,
            'public_consistent_minnode': dsl.to_sexpr(min([*fits, *constants], key=rank)),
            'public_consistent_nonconstant_minnode': dsl.to_sexpr(min(fits, key=rank))}


def construct():
    audit_path = feasibility.OUT / 'audit.json'
    audit = json.loads(audit_path.read_text())
    for path, sha in audit['source_sha256'].items():
        if prior.sha(prior.ROOT / path) != sha: raise ValueError('Feasibility source changed')
    if prior.sha(prior.SOURCE / 'plan.json') != audit['source_plan_sha256'] or prior.sha(prior.SOURCE / 'private.json') != audit['source_private_sha256']:
        raise ValueError('Original source binding changed')
    old_plan = json.loads((prior.SOURCE / 'plan.json').read_text())
    old_private = json.loads((prior.SOURCE / 'private.json').read_text())
    # Load only the9 source artifacts already bound in the original model plan.
    saved_worlds = {}
    source_hashes = {}
    for relative, sha in old_private['selected_world_file_hashes'].items():
        path = prior.ROOT / relative
        if prior.sha(path) != sha: raise ValueError('Frozen original world file changed')
        world = json.loads(path.read_text())
        if world['candidate_index'] in saved_worlds:
            raise ValueError('Duplicate source world')
        saved_worlds[world['candidate_index']] = world
        source_hashes[relative] = sha
    reports = audit['world_reports']
    if (len(saved_worlds) != 9 or len(reports) != 9
            or {r['candidate_index'] for r in reports} != set(saved_worlds)
            or sorted(r['ordinal'] for r in reports) != list(range(9))):
        raise ValueError('Original nine-world cohort changed')
    tasks_by_id = {t['task_id']: t for t in old_plan['tasks']}
    reservoir = world_module.build_classifier_reservoir()
    public, bindings, baseline_records, diagnostics = [], [], {}, []
    for row in audit['world_reports']:
        index, ordinal = row['candidate_index'], row['ordinal']
        saved = saved_worlds[index]
        seed = feasibility.derive_candidate_world_seed(feasibility.WORLD_NAMESPACE, index)
        if saved['world_seed'] != seed: raise ValueError('Original seed mismatch')
        bank, train, evidence, test, _ = world_module._world_structure(seed)
        if prior.digest_text(json.dumps([dsl.canonical_hash(h) for h in bank])) != row['bank_sha256']:
            raise ValueError('Original hypothesis bank changed')
        old_task = tasks_by_id[row['original_task_id']]
        components = feasibility.material.public_components(old_task)
        d0, parent = components['D0'], components['parent_text']
        vectors = tuple(dsl.behavior_vector(h, dsl.DOMAIN) for h in bank)
        by_point = {point: i for i, point in enumerate(dsl.DOMAIN)}
        partition = feasibility.partition_rows(vectors, dsl.DOMAIN, [by_point[p] for p in evidence])
        query = feasibility.choose_query(partition, old_task['prompt_sha256'], True)
        if query != row['selected_informative_query']: raise ValueError('Pre-label query choice changed')
        q = tuple(query['point'])
        repeated = repeat_row(d0, old_task['prompt_sha256'])
        # Target selection is inherited, not redrawn. Only after query is fixed.
        target_index = saved['target_index']
        if type(target_index) is not int or not 0 <= target_index < len(bank): raise ValueError('Bad saved target index')
        target_behavior = list(vectors[target_index])
        identity = {'parent': parent, 'D0': d0, 'target_behavior': target_behavior,
                    'bank': [dsl.canonical_hash(h) for h in bank],
                    'evidence_points': [list(p) for p in evidence], 'test_points': [list(p) for p in test]}
        if digest(identity) != saved['task_identity']: raise ValueError('Restored original target identity differs')
        new_row = {'point': list(q), 'label': target_behavior[by_point[q]]}
        private_test = [{'point': list(p), 'label': target_behavior[by_point[p]]} for p in test]
        if not (q in evidence and q not in test and q not in train): raise ValueError('Observation split violation')
        if any(target_behavior[by_point[tuple(r['point'])]] != r['label'] for r in d0): raise ValueError('Target/D0 mismatch')
        remaining = query['branch_counts'][new_row['label']]
        if not 2 <= remaining < 256: raise ValueError('Actual observation not informative and non-unique')
        prompts = []
        for condition, additional in zip(scoring.CONDITIONS, (None, new_row, repeated), strict=True):
            visible = [*d0, *([] if additional is None else [additional])]
            if {tuple(r['point']) for r in visible} & set(test): raise ValueError('Test label leakage')
            # Baseline selection ends before test labels enter its scorer.
            policies = public_baselines(parent, visible, reservoir)
            prompt = render(d0, parent, additional)
            task_id = 'TASK' + digest({'namespace': 'new-evidence-v1-20260910', 'old_task': row['original_task_id'], 'condition': condition})[:20]
            task = {'task_id': task_id, 'rendered_prompt': prompt, 'prompt_sha256': prior.digest_text(prompt)}
            public.append(task)
            bindings.append({'task_id': task_id, 'prompt_sha256': task['prompt_sha256'], 'ordinal': ordinal, 'condition': condition,
                             'D0': d0, 'visible_observations': visible, 'test': private_test, 'target_behavior': target_behavior,
                             'original_task_identity': saved['task_identity']})
            for name, expression in policies.items():
                baseline_records.setdefault(name, []).append({'task_id': task_id, 'prompt_sha256': task['prompt_sha256'],
                                                             'content': json.dumps({'expression': expression})})
            prefix, rest = prompt.split(EXTRA_START)
            _, suffix = rest.split(EXTRA_END)
            prompts.append(prefix + EXTRA_START + '<EXTRA>' + EXTRA_END + suffix)
        if len(set(prompts)) != 1: raise ValueError('Non-observation content differs')
        diagnostics.append({'ordinal': ordinal, 'original_candidate_index': index,
                            'original_task_identity': saved['task_identity'], 'remaining_hypotheses_after_actual_new_label': remaining,
                            'new_observation_contradicts_parent': dsl.evaluate(components['parent'], q) != new_row['label'],
                            'repeated_observation_remaining_hypotheses': 256,
                            'test_points_unchanged_and_not_in_prompt': 64,
                            'old_evidence_points_remaining_unused': 48})
    if len(public) != 27 or len({t['task_id'] for t in public}) != 27: raise ValueError('Expected27 tasks')
    private = {'bindings': bindings, 'source_world_hashes': source_hashes}
    baselines = {name: scoring.aggregate(private, records) for name, records in baseline_records.items()}
    for value in baselines.values():
        if value['conditions']['baseline'] != value['conditions']['repeat_evidence']:
            raise ValueError('Repeat observation changed deterministic baseline')
    return {'kind': 'OFFLINE_NEW_EVIDENCE_DRAFT_NOT_LIVE_AUTHORIZATION', 'tasks': sorted(public, key=lambda t: t['task_id'])}, private, baselines, diagnostics


def main():
    public, private, baselines, diagnostics = construct()
    artifacts = {'public-draft.json': public, 'private-draft.json': private, 'code-baselines.json': baselines}
    if sys.argv[1:] == ['--verify']:
        audit = json.loads((OUT / 'audit.json').read_text())
        for path, expected in audit['source_sha256'].items():
            if prior.sha(prior.ROOT / path) != expected: raise ValueError('New-evidence source changed')
        for name, value in artifacts.items():
            if json.loads((OUT / name).read_text()) != value or prior.sha(OUT / name) != audit['artifact_sha256'][name]:
                raise ValueError('New-evidence draft reconstruction mismatch')
        if audit['diagnostics'] != diagnostics: raise ValueError('Diagnostics changed')
        print('Verified27 drafts,9 original targets,disjoint test data,code baselines;0 model calls.')
        return
    if sys.argv[1:]: raise SystemExit('Usage: new_evidence_materials_20260910.py [--verify]')
    OUT.mkdir(mode=0o700, exist_ok=False)
    for name, value in artifacts.items(): prior.save(OUT / name, value)
    sources = [__file__, scoring.__file__, feasibility.__file__, prior.__file__, feasibility.material.__file__, world_module.__file__, dsl.__file__,
               feasibility.OUT / 'audit.json', prior.SOURCE / 'plan.json', prior.SOURCE / 'private.json']
    audit = {'kind': 'NEW_EVIDENCE_OFFLINE_DRAFT_AUDIT', 'worlds': 9, 'draft_tasks': 27,
             'provider_calls': 0, 'model_response_files_read': False, 'new_worlds_generated': 0,
             'original_targets_restored': 9, 'new_targets_drawn': 0, 'oracle_rerun': False,
             'additional_true_labels_materialized_for_draft': 9, 'private_test_labels_not_in_prompts': True,
             'live_authorized': False, 'source_sha256': {str(Path(p).relative_to(prior.ROOT)): prior.sha(p) for p in sources},
             'artifact_sha256': {name: prior.sha(OUT / name) for name in artifacts}, 'diagnostics': diagnostics}
    prior.save(OUT / 'audit.json', audit)
    print(json.dumps({k: v for k, v in audit.items() if k not in ('source_sha256', 'artifact_sha256', 'diagnostics')}, indent=2))
    print('remaining hypotheses:', [d['remaining_hypotheses_after_actual_new_label'] for d in diagnostics])
    print('parent contradicted worlds:', sum(d['new_observation_contradicts_parent'] for d in diagnostics))
    print('CODE BASELINES NOT MODEL RESULTS:', json.dumps({name: {c: {k: v for k, v in row.items() if k != 'world_rows'}
                                                                for c, row in result['conditions'].items()} for name, result in baselines.items()}))


if __name__ == '__main__': main()
