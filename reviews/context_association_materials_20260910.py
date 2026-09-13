"""Three-condition offline drafts; no live runner and no provider access."""
import ast
from collections import Counter
import json
import re
import sys

import context_reference_pool_20260910 as pool

m = pool.m
prior = m.prior
OUT = prior.ROOT / 'artifacts/context-association-draft-20260910'
CONDITIONS = ('aligned', 'unmatched', 'no_reference')
TABLE_START = '\nAuxiliary numerical reference:\n'
TABLE_END = '\nEnd auxiliary reference.\n'


def public_components(task):
    prompt = task['rendered_prompt']
    if prior.digest_text(prompt) != task['prompt_sha256']:
        raise ValueError('Source prompt mismatch')
    prefix, fragment_text, suffix = prior.split_context(prompt)
    parent_text = prefix.split('Frozen parent:\n')[1].strip()
    observation_text = prefix.split('Public observations:\n')[1].split('\n\nThe complete input domain')[0]
    d0 = []
    for line in observation_text.splitlines():
        point_text, label_text = line.strip().split(' -> ')
        point = ast.literal_eval(point_text)
        if not isinstance(point, tuple) or len(point) != 3 or any(type(x) is not int or x not in range(-2, 3) for x in point):
            raise ValueError('Bad public point')
        label = int(label_text)
        if label not in (0, 1):
            raise ValueError('Bad public label')
        d0.append({'point': list(point), 'label': label})
    options = re.findall(r'^  (Q[0-9A-F]{8}): (.+)$', suffix, re.MULTILINE)
    descriptions = {m.benchmark._action_description(i): i for i in range(10)}
    raw_order = [descriptions[description] for _, description in options]
    option_ids = [option for option, _ in options]
    if len(set(option_ids)) != 10 or sorted(raw_order) != list(range(10)):
        raise ValueError('Incomplete original option mapping')
    parent = m.dsl.parse_sexpr(parent_text)
    fragment = m.dsl.parse_sexpr(fragment_text)
    context = {'D0': d0, 'parent': parent_text,
               'old_subtrees': {}}
    if m.benchmark.render_fair_choice_prompt(context, fragment_text, raw_order, option_ids) != prompt:
        raise ValueError('Original public prompt reconstruction failed')
    programs = m.children(parent, fragment)
    # Retain every original slot, including redundant or public-ineligible programs.
    for program in programs:
        if m.dsl.parse_sexpr(m.dsl.to_sexpr(program)) != program:
            raise ValueError('Candidate serialization changed semantics')
    return {'parent': parent, 'parent_text': parent_text, 'fragment': fragment,
            'D0': d0, 'observation_text': observation_text, 'option_ids': option_ids,
            'raw_order': raw_order, 'programs': programs}


def reference_block(vector):
    if vector is None:
        return 'No auxiliary numerical reference is supplied.'
    if len(vector) != len(m.DOMAIN):
        raise ValueError('Reference does not cover full domain')
    return 'x1,x2,x3,value\n' + '\n'.join(
        ','.join(map(str, (*point, value))) for point, value in zip(m.DOMAIN, vector, strict=True))


def render(components, vector):
    options = '\n'.join(f'  {option}: {m.dsl.to_sexpr(components["programs"][raw])}'
                        for option, raw in zip(components['option_ids'], components['raw_order'], strict=True))
    return (
        'Choose one listed complete candidate classifier as an exploration entry for a finite symbolic binary rule.\n'
        'Return exactly one JSON object with the single key expression and a listed opaque option ID. '
        'Do not return prose, markdown, a program, or any additional key.\n\n'
        f'Public observations:\n{components["observation_text"]}\n\n'
        'The complete input domain has x1, x2, and x3 each in {-2,-1,0,1,2}; '
        'a legal result must output only 0 or 1 everywhere.\n\n'
        f'Frozen parent:\n{components["parent_text"]}\n\n'
        'Complete candidate classifiers (these are executed as written; do not substitute or edit any expression):\n'
        f'{options}\n\n'
        'An optional auxiliary numerical reference may follow. It describes an auxiliary arithmetic function, '
        'not labels of the unknown rule. Its relevance to the candidates is not guaranteed. '
        'It is not executable task material and never changes the listed candidates. '
        'All candidate definitions and public observations are supplied regardless of the reference.'
        + TABLE_START + reference_block(vector) + TABLE_END +
        '\nChoose the candidate that is binary, remains consistent with every public observation, '
        'and is most likely to distinguish the unknown rule during the fixed four-round verification process.\n\n'
        'Required output schema: {"expression":"<OPTION_ID>"}\n')


def strip_reference(prompt):
    if prompt.count(TABLE_START) != 1 or prompt.count(TABLE_END) != 1:
        raise ValueError('Malformed reference boundary')
    prefix, rest = prompt.split(TABLE_START)
    _, suffix = rest.split(TABLE_END)
    return prefix + TABLE_START + '<REFERENCE>' + TABLE_END + suffix


def source_materials():
    if prior.sha(prior.SOURCE / 'plan.json') != prior.PLAN_SHA:
        raise ValueError('Source plan changed')
    plan = json.loads((prior.SOURCE / 'plan.json').read_text())
    for path, digest in plan['input_file_hashes'].items():
        if prior.sha(prior.ROOT / path) != digest:
            raise ValueError('Frozen original dependency changed')
    if prior.sha(prior.SOURCE / 'private.json') != plan['private_file_sha256']:
        raise ValueError('Original scoring binding changed')
    audit = json.loads((pool.OUT / 'audit.json').read_text())
    if audit['source_plan_sha256'] != prior.PLAN_SHA or audit['source_file_sha256'] != prior.sha(pool.__file__):
        raise ValueError('Reference selection audit changed')
    if len(plan['tasks']) != 18 or audit['tasks_with_eligible_reference'] != 18:
        raise ValueError('Incomplete fixed cohort')
    return plan, audit


def construct():
    plan, selection_audit = source_materials()
    library = m.lineage.build_motif_library()
    selection_rows = {r['original_task_id']: r for r in selection_audit['rows']}
    public, bindings, checks = [], [], []
    components_by_id = {}
    for task in plan['tasks']:
        c = public_components(task)
        components_by_id[task['task_id']] = c
        motif = next(item for item in library if item.ast == c['fragment'])
        forbidden = pool.forbidden_vectors(c['parent'], c['programs'])
        eligible = pool.eligible_references(motif, forbidden, library)
        selected = pool.choose(eligible, task['prompt_sha256'])
        if selected is None or selected.motif_id != selection_rows[task['task_id']]['selected_reference_id']:
            raise ValueError('Structured reference unavailable or selection drift')
        aligned, unmatched = m.behavior(motif.ast), m.behavior(selected.ast)
        if Counter(aligned) != Counter(unmatched) or unmatched in forbidden or unmatched == aligned:
            raise ValueError('Association/marginal matching gate failed')
        prompts = {condition: render(c, vector) for condition, vector in
                   zip(CONDITIONS, (aligned, unmatched, None), strict=True)}
        if len({strip_reference(p) for p in prompts.values()}) != 1:
            raise ValueError('Non-reference task content changed')
        if len(prompts['aligned']) != len(prompts['unmatched']):
            raise ValueError('Matched reference character count changed')
        hashes = [m.dsl.canonical_hash(program) for program in c['programs']]
        behaviors = [m.behavior(program) for program in c['programs']]
        checks.append({'original_task_id': task['task_id'], 'eligible_references': len(eligible),
                       'candidate_hashes_by_raw': hashes,
                       'all_candidates_total': len(behaviors) == 10,
                       'binary_candidates': sum(set(v) <= {0, 1} for v in behaviors),
                       'public_consistent_candidates': sum(all(m.dsl.evaluate(program, row['point']) == row['label']
                                                                for row in c['D0']) for program in c['programs']),
                       'distinct_candidate_syntax': len(set(hashes)),
                       'distinct_candidate_behaviors': len(set(behaviors)),
                       'matched_reference_output_histogram': True,
                       'matched_reference_stratum_node_depth': True,
                       'unmatched_reference_has_no_parent_or_candidate_subtree_match': True,
                       'only_reference_block_differs': True,
                       'characters': {condition: len(p) for condition, p in prompts.items()},
                       'input_point_order_identical': True})
        for condition, prompt in prompts.items():
            draft_id = 'TASK' + prior.digest_text('association-draft-v1:' + task['task_id'] + ':' + condition)[:20]
            public.append({'task_id': draft_id, 'rendered_prompt': prompt,
                           'prompt_sha256': prior.digest_text(prompt)})
            bindings.append({'task_id': draft_id, 'original_task_id': task['task_id'], 'condition': condition,
                             'prompt_sha256': prior.digest_text(prompt),
                             'option_to_raw_action': dict(zip(c['option_ids'], c['raw_order'], strict=True)),
                             'candidate_hashes_by_raw': hashes})
    # Only now read scoring labels: they did not influence references or public prompts.
    original_private = json.loads((prior.SOURCE / 'private.json').read_text())
    lookup = {}
    for ordinal, pair in enumerate(original_private['pairs']):
        for arm, definition in pair['arms'].items():
            lookup[definition['task_id']] = (ordinal, arm, definition, pair)
    if set(lookup) != set(components_by_id) or len(lookup) != 18:
        raise ValueError('Original tasks/labels not bijective')
    for binding in bindings:
        ordinal, arm, definition, pair = lookup[binding['original_task_id']]
        if binding['option_to_raw_action'] != pair['option_to_raw_action']:
            raise ValueError('Original option mapping changed')
        if binding['option_to_raw_action'][definition['correct_option_id']] != definition['correct_raw_action']:
            raise ValueError('Original scoring key inconsistent')
        c = components_by_id[binding['original_task_id']]
        correct_raw = definition['correct_raw_action']
        correct_behavior = m.behavior(c['programs'][correct_raw])
        if sum(m.behavior(program) == correct_behavior for program in c['programs']) != 1:
            raise ValueError('Complete-program representation makes the correct candidate behaviorally ambiguous')
        if set(correct_behavior) - {0, 1} or not all(
                m.dsl.evaluate(c['programs'][correct_raw], row['point']) == row['label'] for row in c['D0']):
            raise ValueError('Original correct candidate fails public validity')
        other = 'context_b' if arm == 'context_a' else 'context_a'
        binding.update(pair_ordinal=ordinal, arm=arm, correct_option_id=definition['correct_option_id'],
                       cross_option_id=pair['arms'][other]['correct_option_id'])
    public.sort(key=lambda row: row['task_id'])
    if len(public) != 54 or len({row['task_id'] for row in public}) != 54:
        raise ValueError('Draft size mismatch')
    return {'kind': 'OFFLINE_DRAFT_NOT_LIVE_AUTHORIZATION', 'tasks': public}, {
        'source_plan_sha256': prior.PLAN_SHA, 'source_private_sha256': plan['private_file_sha256'],
        'bindings': bindings}, checks


def score(private, responses):
    """Descriptive fixed denominators; caller must separately validate response transport."""
    expected = {row['task_id']: row for row in private['bindings']}
    records = {}
    for response in responses:
        task_id = response['task_id']
        if task_id not in expected or task_id in records:
            raise ValueError('Unknown or duplicate response')
        if response.get('prompt_sha256') != expected[task_id]['prompt_sha256']:
            raise ValueError('Response prompt binding changed')
        records[task_id] = response
    result = {}
    for condition in CONDITIONS:
        rows = [row for row in expected.values() if row['condition'] == condition]
        if len(rows) != 18 or len({(r['pair_ordinal'], r['arm']) for r in rows}) != 18:
            raise ValueError('Denominator changed')
        world_rows = []
        for ordinal in range(9):
            arms = [row for row in rows if row['pair_ordinal'] == ordinal]
            if len(arms) != 2 or {row['arm'] for row in arms} != {'context_a', 'context_b'}:
                raise ValueError('Incomplete world')
            own = cross = valid_count = missing = 0
            for row in arms:
                response = records.get(row['task_id'])
                missing += response is None
                valid = bool(response and response.get('valid_choice') is True
                             and response.get('selected_option_id') in row['option_to_raw_action'])
                selected = response['selected_option_id'] if valid else None
                valid_count += valid
                own += selected == row['correct_option_id']
                cross += selected == row['cross_option_id']
            world_rows.append({'pair_ordinal': ordinal, 'own': own, 'cross': cross,
                               'valid_arms': valid_count, 'missing': missing, 'complete_switch': own == 2})
        result[condition] = {'worlds': 9, 'tasks': 18,
                             'own': sum(r['own'] for r in world_rows),
                             'cross': sum(r['cross'] for r in world_rows),
                             'complete_switch': sum(r['complete_switch'] for r in world_rows),
                             'valid_arms': sum(r['valid_arms'] for r in world_rows),
                             'missing': sum(r['missing'] for r in world_rows), 'world_rows': world_rows}
    return result


def main():
    public, private, checks = construct()
    all_correct = [{'task_id': b['task_id'], 'valid_choice': True,
                    'prompt_sha256': b['prompt_sha256'],
                    'selected_option_id': b['correct_option_id']} for b in private['bindings']]
    if any(r['complete_switch'] != 9 or r['cross'] != 0 for r in score(private, all_correct).values()):
        raise ValueError('Synthetic scoring failed')
    if any(r['own'] != 0 or r['missing'] != 18 for r in score(private, []).values()):
        raise ValueError('Synthetic missing scoring failed')
    if sys.argv[1:] == ['--verify']:
        if json.loads((OUT / 'public-draft.json').read_text()) != public or json.loads((OUT / 'private-draft.json').read_text()) != private:
            raise ValueError('Draft reconstruction mismatch')
        audit = json.loads((OUT / 'audit.json').read_text())
        if audit['checks'] != checks:
            raise ValueError('Audit reconstruction mismatch')
        if audit['reference_pool_audit_sha256'] != prior.sha(pool.OUT / 'audit.json'):
            raise ValueError('Reference pool audit changed')
        for name in ('public-draft.json', 'private-draft.json'):
            if audit['artifact_sha256'][name] != prior.sha(OUT / name):
                raise ValueError('Draft hash changed')
        for path, digest in audit['source_sha256'].items():
            if prior.sha(prior.ROOT / path) != digest:
                raise ValueError('Draft source changed')
        print('Verified: 54 drafts, original option/label bindings, 18 reference matches, synthetic scoring; no model calls.')
        return
    if sys.argv[1:]:
        raise ValueError('Only --verify or initial offline construction is supported')
    OUT.mkdir(mode=0o700, exist_ok=False)
    prior.save(OUT / 'public-draft.json', public)
    prior.save(OUT / 'private-draft.json', private)
    paths = [__file__, pool.__file__, m.__file__, prior.__file__, m.dsl.__file__, m.lineage.__file__, m.benchmark.__file__]
    audit = {'kind': 'OFFLINE_ASSOCIATION_DRAFT_AUDIT', 'worlds': 9, 'original_tasks': 18, 'draft_tasks': 54,
             'source_plan_sha256': prior.PLAN_SHA, 'conditions': list(CONDITIONS),
             'artifact_sha256': {name: prior.sha(OUT / name) for name in ('public-draft.json', 'private-draft.json')},
             'source_sha256': {str(prior.Path(path).relative_to(prior.ROOT)): prior.sha(path) for path in paths},
             'reference_pool_audit_sha256': prior.sha(pool.OUT / 'audit.json'),
             'public_schema_keys': ['task_id', 'rendered_prompt', 'prompt_sha256'],
             'provider_calls': 0, 'model_responses_read': False, 'oracle_rerun': False,
             'synthetic_scoring_only': True, 'equal_tokens_verified': False, 'equal_information_claimed': False,
             'correct_candidate_behavior_unique_and_public_valid': True,
             'live_authorized': False, 'checks': checks}
    prior.save(OUT / 'audit.json', audit)
    print(json.dumps({k: v for k, v in audit.items() if k not in ('checks', 'source_sha256')}, indent=2))


if __name__ == '__main__': main()
