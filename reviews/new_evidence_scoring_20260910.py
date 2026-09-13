"""Candidate-law scoring, not the historical K4/action-choice endpoint."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import dsl

CONDITIONS = ('baseline', 'new_evidence', 'repeat_evidence')


def score_expression(content, binding):
    # Validate the scoring key even when the model response is missing/invalid.
    if len(binding['test']) != 64 or len({tuple(r['point']) for r in binding['test']}) != 64:
        raise ValueError('Test denominator changed')
    if {tuple(r['point']) for r in binding['visible_observations']} & {tuple(r['point']) for r in binding['test']}:
        raise ValueError('Visible/test overlap')
    if len(binding['target_behavior']) != len(dsl.DOMAIN):
        raise ValueError('Target domain binding changed')
    result = {'valid_program': False, 'missing': content is None, 'failure': None,
              'D0_consistent': False, 'visible_consistent': False,
              'test_correct': 0, 'test_total': 64, 'test_accuracy': 0.0,
              'test_perfect': False, 'full_domain_exact': False,
              'consistent_test_accuracy': 0.0}
    try:
        if content is None:
            result['failure'] = 'missing'
            return result
        payload = json.loads(content)
        if not isinstance(payload, dict) or set(payload) != {'expression'} or not isinstance(payload['expression'], str):
            raise ValueError('Expected one expression string')
        expression = dsl.parse_sexpr(payload['expression'])
        dsl.validate_expr(expression)
        behavior = dsl.behavior_vector(expression, dsl.DOMAIN)
        if set(behavior) - {0, 1}:
            result['failure'] = 'nonbinary'
            return result
    except (ValueError, TypeError, RecursionError):
        result['failure'] = 'format_or_dsl_invalid'
        return result
    d0_fit = all(dsl.evaluate(expression, r['point']) == r['label'] for r in binding['D0'])
    visible_fit = all(dsl.evaluate(expression, r['point']) == r['label'] for r in binding['visible_observations'])
    correct = sum(dsl.evaluate(expression, r['point']) == r['label'] for r in binding['test'])
    result.update(valid_program=True, D0_consistent=d0_fit, visible_consistent=visible_fit,
                  test_correct=correct, test_accuracy=correct / 64, test_perfect=correct == 64,
                  full_domain_exact=list(behavior) == binding['target_behavior'],
                  consistent_test_accuracy=correct / 64 if visible_fit else 0.0)
    return result


def aggregate(private, records):
    bindings = private['bindings']
    expected = {b['task_id']: b for b in bindings}
    if len(bindings) != 27 or len(expected) != 27:
        raise ValueError('Expected27 tasks')
    responses = {}
    for r in records:
        if r['task_id'] not in expected or r['task_id'] in responses:
            raise ValueError('Unknown/duplicate response')
        if r['prompt_sha256'] != expected[r['task_id']]['prompt_sha256']:
            raise ValueError('Prompt binding changed')
        responses[r['task_id']] = r
    result = {}
    for condition in CONDITIONS:
        rows = sorted((b for b in bindings if b['condition'] == condition), key=lambda b: b['ordinal'])
        if [b['ordinal'] for b in rows] != list(range(9)):
            raise ValueError('World denominator changed')
        scored = [{'ordinal': b['ordinal'], **score_expression(responses.get(b['task_id'], {}).get('content'), b)} for b in rows]
        result[condition] = {'worlds': 9, 'test_points_per_world': 64,
                             'mean_test_accuracy': sum(r['test_accuracy'] for r in scored) / 9,
                             'valid_programs': sum(r['valid_program'] for r in scored),
                             'missing': sum(r['missing'] for r in scored),
                             'visible_consistent': sum(r['visible_consistent'] for r in scored),
                             'test_perfect': sum(r['test_perfect'] for r in scored),
                             'full_domain_exact': sum(r['full_domain_exact'] for r in scored),
                             'mean_consistent_test_accuracy': sum(r['consistent_test_accuracy'] for r in scored) / 9,
                             'world_rows': scored}
    contrasts = {}
    for left, right in (('new_evidence', 'baseline'), ('new_evidence', 'repeat_evidence'), ('repeat_evidence', 'baseline')):
        deltas = [a['test_correct'] - b['test_correct'] for a, b in zip(result[left]['world_rows'], result[right]['world_rows'], strict=True)]
        contrasts[left + '_vs_' + right] = {'mean_test_accuracy_delta': sum(deltas) / (9 * 64),
                                          'wins': sum(d > 0 for d in deltas), 'losses': sum(d < 0 for d in deltas),
                                          'ties': sum(d == 0 for d in deltas), 'world_test_correct_deltas': deltas}
    return {'conditions': result, 'contrasts': contrasts, 'new_inferential_tests': False}
