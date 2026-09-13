"""Authorized fixed-range continuation without elapsed-time cutoff."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shortcut_challenge_runner_20260909 as base

OLD = base.ROOT/'artifacts/shortcut-challenge-search-resume-20260909'
OUT = base.ROOT/'artifacts/shortcut-challenge-search-full-range-20260909'
DOC = Path(__file__).with_name('shortcut-challenge-full-range-20260909.md')
ORIGINAL_MANIFEST = base.manifest


def remaining(elapsed, completed):
    # Internal sentinel only; no non-finite numbers in manifests or saved JSON.
    return float('inf')


def checked_prefix():
    for name, expected in (
        ('manifest.json','0eb126e5765e0b3a54216c4238b96684955a3383b10005d878011879c8a0db32'),
        ('state.json','c4cb1f25a16cf8928126522c823771ece5049084d0bb9fa304d5cb660635cd70'),
        ('summary.json','03270500be18069eaaa20f870c647e6ce642c2a23fb3d9e77cbee1ab0f38247e')):
        if base.sha(OLD/name) != expected:
            raise ValueError('Prior continuation binding differs: '+name)
    prior=json.loads((OLD/'manifest.json').read_text())
    state=json.loads((OLD/'state.json').read_text())
    if state['status']!='scan_budget_exhausted' or state['active_index']!=51:
        raise ValueError('Unexpected prior stop')
    if [r['index'] for r in state['completed']]!=list(range(51)) or (OLD/'world-051.json').exists():
        raise ValueError('Unexpected complete/incomplete prefix')
    rows=[]
    for row in state['completed']:
        path=OLD/row['file']
        if base.sha(path)!=row['sha256']:
            raise ValueError('Prior complete result changed')
        rows.append({**row,'file':str(path)})
    return prior,state,rows


def manifest():
    prior,state,rows=checked_prefix()
    current=ORIGINAL_MANIFEST()
    for key in ('seeds','source_manifest_sha256','runner_sha256','engine_sha256',
                'diagnostic_sha256','old_identity_file_sha256'):
        if prior[key]!=current[key]:
            raise ValueError('Source/seed drift: '+key)
    return {**current,'kind':'shortcut_challenge_full_range_amendment',
            'calibration_seconds':None,'total_seconds':None,'time_cutoff_enabled':False,
            'candidate_cap':128,'retry_index':51,'same_seed_retry_authorized':True,
            'retained_prefix':rows,'prior_elapsed_ledger':state['elapsed_seconds'],
            'elapsed_ledger_includes_historical_600_second_charge':True,
            'amendment_sha256':base.sha(DOC),'full_range_source_sha256':base.sha(Path(__file__)),
            'prior_manifest_sha256':base.sha(OLD/'manifest.json'),
            'prior_state_sha256':base.sha(OLD/'state.json'),
            'prior_summary_sha256':base.sha(OLD/'summary.json')}


def configure():
    base.OUT=OUT
    base.manifest=manifest
    base.remaining=remaining


def run():
    frozen=manifest()
    subprocess.run(['git','check-ignore','--quiet',str(OUT/'manifest.json')],cwd=base.ROOT,check=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    base.save(OUT/'manifest.json',frozen,exclusive=True)
    base.save(OUT/'state.json',{'kind':'shortcut_challenge_full_range_state',
        'status':'paused_at_boundary','active_index':None,'elapsed_seconds':frozen['prior_elapsed_ledger'],
        'completed':frozen['retained_prefix'],'provider_calls':0,'model_outputs_read':False,
        'time_cutoff_enabled':False,'boundary_created_by_explicit_amendment':True},exclusive=True)
    configure()
    state=base.run(resume=True)
    import shortcut_challenge_summary_20260909 as summary
    report=summary.summarize()
    report.update({'elapsed_seconds_is_budget_charge_not_exact_runtime':True,
                   'historical_runtime_charge_seconds':600,'time_cutoff_enabled':False,
                   'physical_world_attempts':len(state['completed'])+2+int(state.get('active_index') is not None)})
    base.save(OUT/'summary.json',report,exclusive=True)
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':
    run()
