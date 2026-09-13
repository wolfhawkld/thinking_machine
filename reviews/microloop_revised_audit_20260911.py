"""Offline raw/trajectory replay of the revised experiment; no model requests."""
import base64
import json
from collections import Counter
import microloop_revised_live_20260911 as experiment
from src import dsl


def audit():
    live, plan = experiment.verify()
    public, private = live.runner.load_materials(live.OUT)
    records = json.loads((live.OUT / 'records.json').read_text())
    analysis = live.aggregate(public, private, records)
    assert analysis == json.loads((live.OUT / 'analysis.json').read_text())
    returned = 0
    input_tokens = output_tokens = 0
    finishes = Counter()
    for path in live.OUT.glob('*/*.result.json'):
        result = json.loads(path.read_text())
        assert result['status'] == 'returned'
        payload = result['payload']
        raw_path = live.OUT / payload['raw_file']
        assert live.runner.sha(raw_path) == payload['raw_sha256']
        raw = json.loads(raw_path.read_text())
        request = json.loads(path.with_name(path.name.replace('.result.', '.request.')).read_text())
        assert raw['prompt_sha256'] == request['prompt_sha256']
        envelope = json.loads(base64.b64decode(raw['body_base64']))
        assert envelope['choices'][0]['message'].get('content') == payload['content']
        assert envelope['usage'] == payload['usage']
        assert envelope['model'] == plan['settings']['model']
        finishes[envelope['choices'][0]['finish_reason']] += 1
        input_tokens += envelope['usage']['prompt_tokens']
        output_tokens += envelope['usage']['completion_tokens']
        returned += 1
    assert returned == 108
    checks = 0
    changed = Counter()
    contradicted = Counter()
    for row in records:
        ordinal, condition = row['ordinal'], row['condition']
        pub, priv = public[ordinal], private[ordinal]
        state = experiment.revision.initial_state(pub, condition)
        labels = {tuple(r['point']): r['label'] for r in priv['evidence']}
        vectors = []
        for stage, content in enumerate(row['responses']):
            directory = live.OUT / f'{ordinal:02d}-{condition}'
            terminal = json.loads((directory / f'stage-{stage}.terminal.json').read_text())
            result = json.loads((directory / f'stage-{stage}-attempt-0.result.json').read_text())
            assert content == terminal['content'] == result['payload']['content']
            assert terminal['prompt_sha256'] == experiment.revision.original.digest(
                experiment.revision.render_prompt(pub, state))
            expression = experiment.revision.parse_response(content, stage == 2)['expression']
            ast = dsl.parse_sexpr(expression)
            # Independent point-by-point score, not the aggregate scoring helper.
            correct = sum(dsl.evaluate(ast, r['point']) == r['label'] for r in priv['test'])
            scored = analysis['conditions'][condition]['world_rows'][ordinal]['stages'][stage]
            assert correct == scored['test_correct']
            vectors.append(dsl.behavior_vector(ast))
            state = experiment.revision.advance(pub, state, content, lambda q: labels[tuple(q)])
            feedback = state['history'][-1]['feedback']
            if feedback and dsl.evaluate(ast, feedback['point']) != feedback['label']:
                contradicted[condition] += 1
            checks += 1
        changed[condition] += vectors[0] != vectors[-1]
    complete = json.loads((live.OUT / 'complete.json').read_text())
    assert complete['known_input_tokens'] == input_tokens
    assert complete['known_output_tokens'] == output_tokens
    assert complete['physical_attempts'] == 108 and complete['usage_complete']
    return {'raw_verified': returned, 'stage_prompt_and_direct_score_checks': checks,
        'aggregate_replay_identical': True, 'finish_reasons': dict(finishes),
        'known_input_tokens': input_tokens, 'known_output_tokens': output_tokens,
        'initial_final_behavior_changed_worlds': dict(changed),
        'feedback_contradicting_immediately_prior_candidate': dict(contradicted),
        'provider_calls_in_audit': 0,
        'records_sha256': live.runner.sha(live.OUT / 'records.json'),
        'analysis_sha256': live.runner.sha(live.OUT / 'analysis.json')}


if __name__ == '__main__':
    result = audit()
    path = experiment.ROOT / 'artifacts/microloop-revised-audit-20260911.json'
    if path.exists(): assert json.loads(path.read_text()) == result
    else: experiment.original_live.runner.save(path, result)
    print(json.dumps(result, indent=2))
