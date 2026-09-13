"""Read-only source replay plus separately labelled post-hoc predicate diagnosis."""
import base64
from collections import Counter
import json
import re
from pathlib import Path
import microloop_live_20260910 as live
from microloop_analysis_20260910 import aggregate
from new_evidence_scoring_20260910 import score_expression
from src import dsl


def audit():
    live.validate()
    p = live.OUT
    public, private = live.runner.load_materials(p)
    records = json.loads((p / 'records.json').read_text())
    original = aggregate(public, private, records)
    assert original == json.loads((p / 'analysis.json').read_text())
    returned = 0
    for path in p.glob('*/*.result.json'):
        result = json.loads(path.read_text())
        if result['status'] != 'returned': continue
        payload = result['payload']
        rawpath = p / payload['raw_file']
        assert live.runner.sha(rawpath) == payload['raw_sha256']
        raw = json.loads(rawpath.read_text())
        request = json.loads(path.with_name(path.name.replace('.result.', '.request.')).read_text())
        assert raw['prompt_sha256'] == request['prompt_sha256']
        envelope = json.loads(base64.b64decode(raw['body_base64']))
        assert envelope['choices'][0]['message'].get('content') == payload['content']
        assert envelope.get('usage') == payload['usage']
        returned += 1
    failures = {c: [Counter() for _ in range(3)] for c in live.runner.protocol.CONDITIONS}
    rows = []
    missing = []
    for record in records:
        ordinal, condition = record['ordinal'], record['condition']
        pub, priv = public[ordinal], private[ordinal]
        state = live.runner.protocol.initial_state(pub, condition)
        labels = {tuple(r['point']):r['label'] for r in priv['evidence']}
        diag = []
        for stage, content in enumerate(record['responses']):
            terminal = json.loads((p / f'{ordinal:02d}-{condition}' / f'stage-{stage}.terminal.json').read_text())
            assert terminal['content'] == content
            prompt = live.runner.protocol.render_prompt(pub, state)
            assert terminal['prompt_sha256'] == live.runner.protocol.digest(prompt)
            parsed = live.runner.protocol.parse_response(content, stage == 2)
            expression = parsed['expression']
            category = 'valid' if parsed['valid_program'] else 'invalid_other'
            if content is None:
                category = 'missing'; missing.append({'ordinal':ordinal,'condition':condition,'stage':stage})
            if expression is not None and not parsed['valid_program']:
                try:
                    if re.match(r'^\s*\(\s*(gt|eq)\s',expression):
                        wrapped = dsl.parse_sexpr('(ite ' + expression + ' (const 1) (const 0))')
                        dsl.validate_expr(wrapped)
                        expression = dsl.to_sexpr(wrapped)
                        category = 'root_predicate_wrappable'
                except (ValueError,TypeError,RecursionError): pass
            failures[condition][stage][category] += 1
            binding = {'D0':pub['D0'],'visible_observations':state['observations'],
                       'test':priv['test'],'target_behavior':priv['target_behavior']}
            diag.append(score_expression(json.dumps({'expression':expression}) if expression is not None else None,binding))
            # Critically replay ORIGINAL response and ORIGINAL feedback, not normalized history.
            state = live.runner.protocol.advance(pub,state,content,lambda q:labels[tuple(q)])
        rows.append({'ordinal':ordinal,'condition':condition,'diagnostic_stages':diag})
    summary = {}
    for c in failures:
        selected = [r for r in rows if r['condition']==c]
        summary[c] = [{'accuracy':sum(r['diagnostic_stages'][i]['test_accuracy'] for r in selected)/12,
                       'valid':sum(r['diagnostic_stages'][i]['valid_program'] for r in selected),
                       'consistent':sum(r['diagnostic_stages'][i]['visible_consistent'] for r in selected),
                       'exact':sum(r['diagnostic_stages'][i]['full_domain_exact'] for r in selected)} for i in range(3)]
    return {'returned_raw_verified':returned,'original_aggregate_replay_identical':True,
            'missing':missing,'failure_categories':failures,
            'diagnostic_notice':'Post-hoc root-predicate wrapping for scoring only; original prompts, trajectories and primary scores unchanged; not a new confirmatory result.',
            'diagnostic_summary':summary,'diagnostic_rows':rows}


if __name__ == '__main__':
    result = audit()
    target = live.ROOT / 'artifacts/microloop-live-diagnostic-v2-20260910.json'
    if target.exists():
        assert json.loads(target.read_text()) == result
    else: live.runner.save(target,result)
    print(json.dumps({k:v for k,v in result.items() if k!='diagnostic_rows'},ensure_ascii=False,indent=2))
