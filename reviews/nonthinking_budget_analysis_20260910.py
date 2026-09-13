"""Fixed eight-world action scoring; descriptive budget and historical comparison."""
from collections import Counter
import json
import statistics
import sys
import nonthinking_budget_live_20260910 as live

OUT = live.OUT
OLD = live.draft.OLD / 'generation.json'
THINK = live.THINK
read = live.transport._read_json
sha = live.transport.sha


def score_pair(pair, records):
    own = cross = valid = missing = invalid = 0
    choices = {}
    for arm, opposite in (('context_a','context_b'),('context_b','context_a')):
        r = records.get(pair['arms'][arm]['task_id'])
        choice = None
        if r is None: missing += 1
        elif not r['valid_choice']: invalid += 1
        else:
            choice = r['selected_option_id']
            valid += 1
            own += choice in pair['arms'][arm]['correct_option_ids']
            cross += choice in pair['arms'][opposite]['correct_option_ids']
        choices[arm] = choice
    signed = own-cross
    return {'pair_id':pair['pair_id'],'pair_ordinal':pair.get('pair_ordinal'),
            'own':own,'cross':cross,'full':int(own==2),'signed':signed,
            'favorable':int(signed>0),'adverse':int(signed<0),'tie':int(signed==0),
            'valid':valid,'missing':missing,'invalid':invalid,'selected_option_ids':choices}


def summarize(pairs, records):
    rows = [score_pair(p,records) for p in pairs]
    if len(rows)!=8: raise ValueError('Eight-world denominator changed')
    result = {k:sum(r[k] for r in rows) for k in ('own','cross','full','signed','favorable','adverse','tie','valid','missing','invalid')}
    rs = [r for r in records.values() if r is not None]
    outputs = [r['response']['output_tokens'] for r in rs]
    result.update(worlds=8,tasks=16,world_rows=rows,
                  output_tokens={'min':min(outputs) if outputs else None,
                                 'median':statistics.median(outputs) if outputs else None,
                                 'max':max(outputs) if outputs else None,'sum':sum(outputs),'known':len(outputs)},
                  input_tokens=sum(r['response']['input_tokens'] for r in rs),
                  finish_reasons=dict(Counter(r['response']['finish_reason'] for r in rs)),
                  candidate_formats=dict(Counter(r['response'].get('candidate_format','unknown') for r in rs)),
                  provider_models=dict(Counter(r['response']['provider_model'] for r in rs)),
                  fingerprints=dict(Counter(str(r['response'].get('provider_fingerprint_sha256')) for r in rs)),
                  reasoning_presence=dict(Counter('present' if r.get('thinking_telemetry',{}).get('reasoning_content_present') is True
                                                 else 'absent' if r.get('thinking_telemetry',{}).get('reasoning_content_present') is False
                                                 else 'unknown' for r in rs)))
    return result


def contrast(left,right):
    if [r['pair_id'] for r in left['world_rows']] != [r['pair_id'] for r in right['world_rows']]:
        raise ValueError('Paired world identities changed')
    result = {}
    for k in ('own','full','signed'):
        ds = [a[k]-b[k] for a,b in zip(left['world_rows'],right['world_rows'],strict=True)]
        result[k] = {'delta':sum(ds),'differences':ds,'wins':sum(d>0 for d in ds),
                     'losses':sum(d<0 for d in ds),'ties':sum(d==0 for d in ds)}
    return result


def analyze(plan_path=live.PLAN_PATH,private_path=OUT/'private.json',generation_path=OUT/'generation.json',
            analysis_path=OUT/'analysis.json',*,report_path=OUT/'results.md',write=True):
    plan,tasks = live.validate()
    if read(plan_path)!=plan or sha(private_path)!=plan['private_file_sha256']:
        raise ValueError('Plan/private binding changed')
    private,gen = read(private_path),read(generation_path)
    if gen['plan_file_sha256']!=sha(plan_path) or len(gen['slots'])!=32 or gen['formal_calls']!=32 or not 0<=gen['provider_calls']<=34:
        raise ValueError('Generation binding/denominator changed')
    if gen['records'] != [s['record'] for s in gen['slots'] if s['record'] is not None]:
        raise ValueError('Records do not match slots')
    for i,(task,slot) in enumerate(zip(tasks,gen['slots'],strict=True)):
        if slot['index']!=i or any(slot[k]!=task[k] for k in ('task_id','prompt_sha256')):
            raise ValueError('Slot order changed')
        if slot['record'] is not None and any(slot['record'][k]!=task[k] for k in ('task_id','prompt_sha256')):
            raise ValueError('Response binding changed')
    records = {r['task_id']:r for r in gen['records']}
    if len(records)!=len(gen['records']): raise ValueError('Duplicate responses')
    pairs,bindings = private['pairs'],private['bindings']
    originals = {p['arms'][a]['task_id'] for p in pairs for a in ('context_a','context_b')}
    if len(pairs)!=8 or len(originals)!=16 or len(bindings)!=32:
        raise ValueError('Private denominator changed')
    if {b['task_id'] for b in bindings}!={t['task_id'] for t in tasks}:
        raise ValueError('Private/public task set mismatch')
    conditions, original_hashes = {},{}
    for cap in (256,8192):
        bs = [b for b in bindings if b['max_tokens']==cap]
        if len(bs)!=16 or {b['original_task_id'] for b in bs}!=originals:
            raise ValueError('Cap cohort changed')
        for b in bs:
            if plan['cap_by_task'][b['task_id']]!=cap: raise ValueError('Cap mismatch')
            tid=b['original_task_id']
            if tid in original_hashes and original_hashes[tid]!=b['prompt_sha256']: raise ValueError('Prompt differs across caps')
            original_hashes[tid]=b['prompt_sha256']
        conditions[str(cap)] = summarize(pairs,{b['original_task_id']:records.get(b['task_id']) for b in bs})
    historical = {}
    for label,path in (('old_nonthinking',OLD),('old_thinking',THINK)):
        if plan['input_file_hashes'].get(str(path.relative_to(live.ROOT)))!=sha(path): raise ValueError('Historical binding changed')
        old = read(path)['records']
        by_id = {r['task_id']:r for r in old}
        if len(by_id)!=48 or len(old)!=48: raise ValueError('Historical denominator changed')
        selected = {tid:by_id[tid] for tid in originals}
        if any(r['prompt_sha256']!=original_hashes[tid] for tid,r in selected.items()): raise ValueError('Historical prompt differs')
        historical[label] = summarize(pairs,selected)
    result = {'kind':'nonthinking_budget_descriptive_v1','plan_sha256':sha(plan_path),'generation_sha256':sha(generation_path),
              'private_sha256':sha(private_path),'conditions':conditions,'historical_same_subset':historical,
              'pairwise_high_minus_low':contrast(conditions['8192'],conditions['256']),
              'new_low_minus_old_low':contrast(conditions['256'],historical['old_nonthinking']),
              'provider_calls':gen['provider_calls'],'usage_complete':gen['usage_complete'],
              'known_response_usage':gen['known_response_usage'],'new_inferential_tests':False}
    if write:
        live.transport._write_exclusive_json(analysis_path,result)
        live.transport._write_private_bytes(report_path,render_report(result).encode())
    return result


def render_report(result):
    lines=['# Non-thinking output-budget comparison','',
           'Fixed8worlds/16arms per configuration; descriptive only, no new p-values.','',
           '| condition | own/16 | cross/16 | full/8 | net | favorable/adverse/tie | valid/missing/invalid |',
           '|---|---:|---:|---:|---:|---|---|']
    rows={**result['conditions'],**result['historical_same_subset']}
    for name,r in rows.items():
        lines.append(f"| {name} | {r['own']} | {r['cross']} | {r['full']} | {r['signed']} | {r['favorable']}/{r['adverse']}/{r['tie']} | {r['valid']}/{r['missing']}/{r['invalid']} |")
    lines+=['','## Contemporaneous high-minus-low paired differences','']
    for k,r in result['pairwise_high_minus_low'].items():
        lines.append(f"- {k}: delta {r['delta']:+d}; wins/losses/ties {r['wins']}/{r['losses']}/{r['ties']}; by world {r['differences']}")
    lines+=['','## Response diagnostics','']
    for cap,r in result['conditions'].items():
        lines.append(f"- {cap}: input {r['input_tokens']}; output {r['output_tokens']}; finish {r['finish_reasons']}; reasoning {r['reasoning_presence']}.")
    lines+=['','Historical thinking is a different configuration and time, not a cap-only causal comparison. Larger requested caps do not establish larger actual compute. Missing/invalid results retain denominators; no general-discovery, entropy-mechanism, or RSI claim.','']
    return '\n'.join(lines)


if __name__ == '__main__':
    if sys.argv[1:] not in ([],['--verify']): raise SystemExit('Usage: nonthinking_budget_analysis_20260910.py [--verify]')
    result=analyze(write=False)
    if sys.argv[1:]:
        if read(OUT/'analysis.json')!=result or (OUT/'results.md').read_text()!=render_report(result): raise ValueError('Analysis reconstruction changed')
        print('Analysis and report verified;0 provider calls.')
    else:
        live.transport._write_exclusive_json(OUT/'analysis.json',result)
        live.transport._write_private_bytes(OUT/'results.md',render_report(result).encode())
        print(json.dumps({'analysis_saved':True,'provider_calls':0}))
