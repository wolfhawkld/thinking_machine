"""Explicitly amended one-time restart of unfinished index3; no provider calls."""
import json
import hashlib
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shortcut_challenge_runner_20260909 as base

OLD = base.ROOT / 'artifacts/shortcut-challenge-search-v1-20260909'
OUT = base.ROOT / 'artifacts/shortcut-challenge-search-resume-20260909'
DOC = Path(__file__).with_name('shortcut-challenge-resume-20260909.md')
ORIGINAL_MANIFEST = base.manifest


def remaining(elapsed, completed):
    return min(3600-elapsed, 900-elapsed) if completed < 4 else 3600-elapsed


def checked_prefix():
    manifest = json.loads((OLD/'manifest.json').read_text())
    state = json.loads((OLD/'state.json').read_text())
    if base.sha(OLD/'manifest.json') != 'c73fcda66c5f2fdf768b1ef1840ad538d4b264fa0a04b8f61515f42af1bef502':
        raise ValueError('Prior manifest differs')
    if base.sha(OLD/'state.json') != 'a191bfe10387558bf42364f4e5b26e8d056ca4a9b1bc7e2dd113270128a34f22':
        raise ValueError('Prior state differs')
    if [r['index'] for r in state['completed']] != [0,1,2] or state['active_index'] != 3:
        raise ValueError('Unexpected completed prefix')
    if (OLD/'world-003.json').exists():
        raise ValueError('Unexpected complete file for interrupted candidate')
    new = base.ENGINE.read_text()
    old = new.replace('    # The diagnostic is the same 10-position cyclic order; old helper slots cap at 24.\n', '').replace(
        'b.action_order_for_pair(index % 10,', 'b.action_order_for_pair(index,')
    if hashlib.sha256(old.encode()).hexdigest() != manifest['engine_sha256']:
        raise ValueError('Engine change extends beyond recorded equivalent sorting fix')
    rows = []
    for row in state['completed']:
        path = OLD/row['file']
        if base.sha(path) != row['sha256']:
            raise ValueError('Prior completed world changed')
        rows.append({**row, 'file': str(path)})
    return manifest, rows


def manifest():
    prior, rows = checked_prefix()
    current = ORIGINAL_MANIFEST()
    for key in ('seeds','source_manifest_sha256','runner_sha256','diagnostic_sha256','old_identity_file_sha256'):
        if current[key] != prior[key]:
            raise ValueError(f'Unexpected source or seed drift: {key}')
    return {**current, 'kind': 'shortcut_challenge_explicit_resume_manifest',
            'calibration_seconds': 900, 'previous_runtime_charged_seconds': 600,
            'previous_exact_runtime_unknown': True, 'remaining_runtime_cap_seconds': 3000,
            'retry_index': 3, 'same_seed_retry_authorized': True,
            'amendment_sha256': base.sha(DOC), 'resume_source_sha256': base.sha(Path(__file__)),
            'prior_manifest_sha256': base.sha(OLD/'manifest.json'),
            'prior_state_sha256': base.sha(OLD/'state.json'),
            'prior_termination_sha256': base.sha(OLD/'termination.json'),
            'retained_prefix': rows}


def configure():
    base.OUT = OUT
    base.manifest = manifest
    base.remaining = remaining


def run():
    frozen = manifest()
    subprocess.run(['git','check-ignore','--quiet',str(OUT/'manifest.json')],cwd=base.ROOT,check=True)
    OUT.mkdir(mode=0o700, exist_ok=False)
    base.save(OUT/'manifest.json',frozen,exclusive=True)
    base.save(OUT/'state.json',{'kind':'shortcut_challenge_explicit_resume_state',
        'status':'paused_at_boundary','active_index':None,'elapsed_seconds':600.0,
        'completed':frozen['retained_prefix'],'provider_calls':0,'model_outputs_read':False,
        'boundary_created_by_explicit_amendment_not_old_clean_pause':True},exclusive=True)
    configure()
    state = base.run(resume=True)
    import shortcut_challenge_summary_20260909 as summary
    report = summary.summarize()
    report['elapsed_seconds_is_budget_charge_not_exact_runtime'] = True
    report['prior_runtime_charged_seconds'] = 600
    report['physical_world_attempts'] = len(state['completed']) + 1 + int(state.get('active_index') is not None)
    base.save(OUT/'summary.json',report,exclusive=True)
    print(json.dumps(report,indent=2),flush=True)


if __name__ == '__main__':
    run()
