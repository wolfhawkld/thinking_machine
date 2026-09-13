"""Extend the existing development prefix to 1024, without a time cutoff."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import shortcut_challenge_runner_20260910 as base

OLD=base.ROOT/'artifacts/shortcut-challenge-search-full-range-20260909'
OUT=base.OUT
ORIGINAL_MANIFEST=base.manifest


def checked_prefix():
    for name,expected in (
        ('manifest.json','e72f8335aa4997e8b4be75e2cef75a36f475cd0c426eda299b8d2d50dd9d19c3'),
        ('state.json','e8249f059fab85862d7f55e3fc536a7b1dd59fcbf47590988d791f3c8d4c8728'),
        ('summary.json','ef1dc3447bcddce3b5eeb9bdf43c66dcfb8a35057e84244256ffc664a39f2692')):
        if base.sha(OLD/name)!=expected:raise ValueError('Prior binding changed: '+name)
    prior=json.loads((OLD/'manifest.json').read_text())
    state=json.loads((OLD/'state.json').read_text())
    if state['status']!='complete_fixed_range' or [r['index'] for r in state['completed']]!=list(range(128)):
        raise ValueError('Requires exactly the completed128 prefix')
    original=Path(__file__).with_name('shortcut_challenge_engine_20260909.py')
    if base.sha(original)!=prior['engine_sha256']:raise ValueError('Original engine drift')
    transformed=original.read_text().replace('128','1024').replace(
        'e57785d0b2580a2918e965bc13f35010a32603dba1c8690d61111b76bb9f3655',
        '677d2d0021f4b45b852f5b51c6aec747d3a5d375c8a08432a93de70be6a73030')
    if transformed!=base.ENGINE.read_text():raise ValueError('Scientific implementation changed beyond range extension')
    rows=[]
    for row in state['completed']:
        path=OLD/row['file']
        if base.sha(path)!=row['sha256']:raise ValueError('Prior world changed')
        rows.append({**row,'file':str(path)})
    return prior,state,rows


def manifest():
    prior,state,rows=checked_prefix()
    current=ORIGINAL_MANIFEST()
    if current['seeds'][:128]!=prior['seeds'] or len(current['seeds'])!=1024:
        raise ValueError('Seed-prefix drift')
    for key in ('source_manifest_sha256','diagnostic_sha256','old_identity_file_sha256'):
        if current[key]!=prior[key]:raise ValueError('Source dependency drift: '+key)
    return {**current,'kind':'shortcut_challenge_1024_development_expansion',
            'retained_prefix':rows,'prior_elapsed_ledger':state['elapsed_seconds'],
            'time_cutoff_enabled':False,'candidate_cap':1024,'new_candidate_count':896,
            'expansion_source_sha256':base.sha(Path(__file__)),
            'summary_source_sha256':base.sha(Path(__file__).with_name('shortcut_challenge_summary_20260910.py')),
            'prior_manifest_sha256':base.sha(OLD/'manifest.json'),
            'prior_state_sha256':base.sha(OLD/'state.json'),'prior_summary_sha256':base.sha(OLD/'summary.json'),
            'joint_policy_diagnostic_added_after_original128_results':True}


def configure():
    base.manifest=manifest


def run():
    frozen=manifest()
    subprocess.run(['git','check-ignore','--quiet',str(OUT/'manifest.json')],cwd=base.ROOT,check=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    base.save(OUT/'manifest.json',frozen,exclusive=True)
    base.save(OUT/'state.json',{'kind':'shortcut_challenge_1024_expansion_state',
        'status':'paused_at_boundary','active_index':None,'elapsed_seconds':frozen['prior_elapsed_ledger'],
        'completed':frozen['retained_prefix'],'provider_calls':0,'model_outputs_read':False,
        'time_cutoff_enabled':False,'retained_world_count':128},exclusive=True)
    configure()
    state=base.run(resume=True)
    import shortcut_challenge_summary_20260910 as summary
    report=summary.summarize()
    report.update({'elapsed_seconds_is_budget_charge_not_exact_runtime':True,
        'expansion_elapsed_seconds':state['elapsed_seconds']-frozen['prior_elapsed_ledger'],
        'time_cutoff_enabled':False,'physical_world_attempts':len(state['completed'])+2+int(state.get('active_index') is not None)})
    base.save(OUT/'summary.json',report,exclusive=True)
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':run()
