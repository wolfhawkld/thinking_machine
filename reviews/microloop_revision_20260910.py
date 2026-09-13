"""Versioned expression-contract repair; no API and no edits to frozen modules."""
import importlib.util
import json
from pathlib import Path
import re
import sys

import microloop_protocol_20260910 as original
from src import dsl

CONDITIONS = original.CONDITIONS
validate_public = original.validate_public
initial_state = original.initial_state
schedule = original.schedule
score_trajectory = original.score_trajectory

GRAMMAR = (
    'Expression grammar: E := (var x1|x2|x3) | (const c) | (neg E) | '
    '(add E E) | (sub E E) | (mul E E) | (ite P E E). '
    'Predicate grammar: P := (gt E E) | (eq E E). Constants c are integers -3..3. '
    'The returned rule may be E or a root P. A root P is shorthand for '
    '(ite P (const 1) (const 0)): true maps to 1 and false maps to 0. '
    'Predicates are not allowed as arithmetic operands or ite value branches. '
    'For example, (gt (var x1) (const 0)) and '
    '(ite (gt (var x1) (const 0)) (const 1) (const 0)) are equivalent legal rules. '
    'Examples explain syntax only; they are not evidence about the hidden target. '
    'Maximum depth 5 and 31 nodes apply to the canonical integer expression AFTER '
    'root-predicate expansion; leaves have depth 1. '
    'Your rule must output integer 0 or 1 on all 125 inputs. '
)


def normalize_expression(expression):
    if not isinstance(expression, str):
        raise ValueError('Expression must be text')
    expanded = expression
    if re.match(r'^\s*\(\s*(gt|eq)\s', expression):
        expanded = '(ite ' + expression + ' (const 1) (const 0))'
    ast = dsl.parse_sexpr(expanded)
    dsl.validate_expr(ast)
    if set(dsl.behavior_vector(ast)) - {0, 1}:
        raise ValueError('Rule must be binary')
    return dsl.to_sexpr(ast)


def normalize_response(content, final):
    """Only normalize the expression in an otherwise valid response schema."""
    try:
        data = json.loads(content)
        expected = {'expression'} if final else {'expression', 'query'}
        if not isinstance(data, dict) or set(data) != expected:
            return content
        data['expression'] = normalize_expression(data['expression'])
        return json.dumps(data)
    except (ValueError, TypeError, RecursionError):
        return content


def parse_response(content, final):
    return original.parse_response(normalize_response(content, final), final)


def advance(public, state, content, label_at):
    revised = original.advance(public, state,
        normalize_response(content, state['round'] == 2), label_at)
    # Do not feed rewritten answers into historical context.
    revised['history'][-1]['response'] = content
    return revised


def render_prompt(public, state):
    prompt = original.render_prompt(public, state)
    start = prompt.index('Legal DSL:')
    end = prompt.index('You may retain or revise the initial candidate.')
    return prompt[:start] + GRAMMAR + prompt[end:]


def aggregate(public, private, records):
    # Isolated module instance, not a monkeypatch to the frozen imported module.
    path = Path(__file__).with_name('microloop_analysis_20260910.py')
    spec = importlib.util.spec_from_file_location('_microloop_revised_analysis', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.protocol = sys.modules[__name__]
    return module.aggregate(public, private, records)


def replay():
    import microloop_live_20260910 as live
    from microloop_result_audit_20260910 import audit
    audit()  # Verify original raw and original scoring first.
    public, private = live.runner.load_materials(live.OUT)
    records = json.loads((live.OUT / 'records.json').read_text())
    revised = aggregate(public, private, records)
    feedback_checked = 0
    for row in records:
        pub, priv = public[row['ordinal']], private[row['ordinal']]
        old = original.initial_state(pub, row['condition'])
        new = initial_state(pub, row['condition'])
        labels = {tuple(r['point']): r['label'] for r in priv['evidence']}
        for content in row['responses']:
            assert original.render_prompt(pub, old) == original.render_prompt(pub, new)
            old = original.advance(pub, old, content, lambda q: labels[tuple(q)])
            new = advance(pub, new, content, lambda q: labels[tuple(q)])
            assert old['observations'] == new['observations']
            assert old['executed_queries'] == new['executed_queries']
            feedback_checked += 1
    out = live.ROOT / 'artifacts/microloop-revision-20260910'
    out.mkdir(mode=0o700, exist_ok=False)
    live.runner.save(out / 'analysis.json', revised)
    live.runner.save(out / 'prompt-examples.json', {
        'not_sent_to_model': True,
        'initial_prompts': [render_prompt(p,initial_state(p,'active')) for p in public]})
    live.runner.save(out / 'replay-audit.json', {
        'kind':'revised_scoring_of_old_responses_not_new_prompt_experiment',
        'provider_calls':0,'response_slots':108,'returned_responses':106,
        'historical_feedback_checks':feedback_checked,
        'old_records_sha256':live.runner.sha(live.OUT / 'records.json'),
        'old_analysis_sha256':live.runner.sha(live.OUT / 'analysis.json'),
        'revised_analysis_sha256':live.runner.sha(out / 'analysis.json'),
        'source_sha256':live.runner.sha(Path(__file__)),
        'limitations':'Post-outcome contract revision; missing responses and other invalid programs remain; new prompts never sent.'})
    print('REPLAY COMPLETE: 106 saved responses; 108 history/feedback checks; zero API calls')


if __name__ == '__main__': replay()
