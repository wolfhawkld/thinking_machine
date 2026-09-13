"""Four independent CPU workers, with explicit threshold migration."""
import json
import multiprocessing as mp
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
import shortcut_challenge_runner_20260910 as base
import shortcut_challenge_engine_20260910 as engine

OLD=base.ROOT/'artifacts/shortcut-challenge-search-parallel2-20260910'
OUT=base.ROOT/'artifacts/shortcut-challenge-search-parallel4-20260910'
DOC=Path(__file__).with_name('shortcut-challenge-parallel4-20260910.md')
ORIGINAL_MANIFEST=base.manifest
LIMIT=1024
WORKERS=4


def validate_world(path,index,seed):
    w=json.loads(path.read_text())
    if w['candidate_index']!=index or w['world_seed']!=seed or w['contexts_completed']!=105 or w['actions_completed']!=1050:
        raise ValueError('Wrong or incomplete world')
    if engine.digest({k:v for k,v in w.items() if k!='content_sha256'})!=w['content_sha256']:
        raise ValueError('World content hash mismatch')
    return w


def ordered_records(rows,old_prompts):
    seen=set();indices=set();result=[]
    for row in sorted(rows,key=lambda r:r['index']):
        if row['index'] in indices:raise ValueError('Duplicate completed index')
        indices.add(row['index'])
        duplicate=row['task_identity'] in seen or row['prompt_identity'] in old_prompts
        result.append({**row,'duplicate_excluded':duplicate})
        seen.add(row['task_identity'])
    return result


def available_slots(active):
    return max(0,WORKERS-active)


def pending_indices(rows):
    done={r['index'] for r in rows}
    if len(done)!=len(rows) or not done<=set(range(LIMIT)):
        raise ValueError('Invalid completed indices')
    return [i for i in range(LIMIT) if i not in done]


def manifest():
    prior=json.loads((OLD/'manifest.json').read_text())
    state=json.loads((OLD/'state.json').read_text())
    external=None
    stop_file=OLD/'threshold-stop-160.json'
    if state['status']=='running' and stop_file.exists():
        external=json.loads(stop_file.read_text())
        if external.get('session_termination_confirmed') is not True or external.get('state_sha256')!=base.sha(OLD/'state.json'):
            raise ValueError('External stop is not bound to this saved state')
    if state['status'] not in ('interrupted','validation_error','complete_fixed_range') and external is None:
        raise ValueError('Wait for the two-worker process to stop')
    if len(state['completed'])<160:raise ValueError('160-completion threshold not reached')
    current=ORIGINAL_MANIFEST()
    for key in ('seeds','source_manifest_sha256','engine_sha256','diagnostic_sha256'):
        if current[key]!=prior[key]:raise ValueError('Scientific dependency drift: '+key)
    old_script=Path(__file__).with_name('shortcut_challenge_parallel2_20260910.py')
    if base.sha(old_script)!=prior['parallel_source_sha256']:raise ValueError('Prior scheduler changed')
    rows=[]
    for row in state['completed']:
        path=OLD/row['file']
        if base.sha(path)!=row['sha256']:raise ValueError('Prior result changed')
        w=validate_world(path,row['index'],prior['seeds'][row['index']])
        rows.append({**row,'file':str(path),'prompt_identity':w['prompt_identity']})
    interrupted=state.get('interrupted_indices',[]) if external is None else external['last_saved_active_indices']
    adopted=[]
    for index in interrupted:
        path=OLD/f'world-{index:03d}.json'
        if path.exists():
            try:
                w=validate_world(path,index,prior['seeds'][index])
            except json.JSONDecodeError:
                continue  # Incomplete output remains preserved, never counted complete.
            if index not in {r['index'] for r in rows}:
                rows.append({'index':index,'file':str(path),'sha256':base.sha(path),
                    'task_identity':w['task_identity'],'prompt_identity':w['prompt_identity'],
                    'elapsed_seconds':None,'adopted_complete_orphan':True})
                adopted.append(index)
    rows=ordered_records(rows,set(prior['old_prompt_identities']))
    pending=pending_indices(rows)
    return {**prior,'kind':'shortcut_challenge_parallel4_amendment','worker_count':WORKERS,
        'retained_prefix':rows,'prior_elapsed_ledger':state['elapsed_seconds'],
        'prior_final_elapsed_unknown':external is not None or state['status']=='validation_error',
        'external_stop_sha256':base.sha(stop_file) if external else None,
        'physical_attempt_count_is_lower_bound':external is not None,
        'threshold_requested':160,'completed_at_stop':len(state['completed']),
        'adopted_complete_orphan_indices':adopted,
        'retry_index':None,
        'same_seed_retry_indices':[i for i in interrupted if i in pending],
        'same_seed_retries_authorized':True,'amendment_sha256':base.sha(DOC),
        'parallel_source_sha256':base.sha(Path(__file__)),
        'parallel2_manifest_sha256':base.sha(OLD/'manifest.json'),
        'parallel2_state_sha256':base.sha(OLD/'state.json'),
        'prior_physical_attempts':len(state['completed'])+3+len(state.get('failed_indices',[]))+len(interrupted)}


def worker(index,seed,path):
    signal.signal(signal.SIGINT,signal.SIG_IGN)
    base.save(Path(path),engine.scan_world(index,seed),exclusive=True)


def rss_kib(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):return int(line.split()[1])
    except (OSError,ValueError):pass
    return None


def run():
    frozen=manifest()
    subprocess.run(['git','check-ignore','--quiet',str(OUT/'manifest.json')],cwd=base.ROOT,check=True)
    OUT.mkdir(mode=0o700,exist_ok=False);base.save(OUT/'manifest.json',frozen,exclusive=True)
    state={'kind':'shortcut_challenge_parallel4_state','status':'running','completed':frozen['retained_prefix'],
           'active_indices':[],'active_workers':{},'elapsed_seconds':frozen['prior_elapsed_ledger'],
           'failed_indices':[],'interrupted_indices':[],'time_cutoff_enabled':False,
           'provider_calls':0,'model_outputs_read':False,'worker_count':WORKERS}
    started=time.monotonic();active={};pending=pending_indices(state['completed']);cursor=0;failed=False
    def persist():
        state['completed']=ordered_records(state['completed'],set(frozen['old_prompt_identities']))
        state['active_indices']=sorted(active)
        state['active_workers']={str(i):{'pid':p.pid,'rss_kib':rss_kib(p.pid)} for i,(p,_,_) in active.items()}
        state['elapsed_seconds']=frozen['prior_elapsed_ledger']+time.monotonic()-started
        base.save(OUT/'state.json',state)
    try:
        persist()
        while active or (cursor<len(pending) and not failed):
            while cursor<len(pending) and available_slots(len(active)) and not failed:
                index=pending[cursor];cursor+=1
                path=OUT/f'world-{index:03d}.json'
                p=mp.get_context('spawn').Process(target=worker,args=(index,frozen['seeds'][index],str(path)))
                p.start();active[index]=(p,path,time.monotonic())
                persist()
            for index,(p,path,begin) in list(active.items()):
                if p.is_alive():continue
                p.join();del active[index]
                try:
                    if p.exitcode!=0:raise ValueError(f'Worker exit {p.exitcode}')
                    w=validate_world(path,index,frozen['seeds'][index])
                    state['completed'].append({'index':index,'file':str(path),'sha256':base.sha(path),
                        'task_identity':w['task_identity'],'prompt_identity':w['prompt_identity'],
                        'elapsed_seconds':time.monotonic()-begin})
                    print(f'Completed index {index}; total {len(state["completed"])}/1024',flush=True)
                except Exception as exc:
                    failed=True;state['failed_indices'].append(index);state['error']=str(exc)
            persist()
            if active:time.sleep(0.5)
        state['status']='validation_error' if failed else 'complete_fixed_range'
    except KeyboardInterrupt:
        state['status']='interrupted';state['interrupted_indices']=sorted(active)
    except Exception as exc:
        state['status']='validation_error';state['error']=str(exc);state['interrupted_indices']=sorted(active)
    finally:
        for p,_,_ in active.values():
            if p.is_alive():p.terminate()
            p.join(timeout=2)
            if p.is_alive():p.kill();p.join()
        active.clear();persist()
    if state['status']=='complete_fixed_range' and [r['index'] for r in state['completed']]!=list(range(LIMIT)):
        raise ValueError('Incomplete or duplicated final index range')
    base.OUT=OUT;base.manifest=manifest
    import shortcut_challenge_summary_20260910 as summary
    report=summary.summarize()
    report.update({'worker_count':WORKERS,'time_cutoff_enabled':False,
        'parallel_elapsed_seconds':time.monotonic()-started,
        'physical_world_attempts':frozen['prior_physical_attempts']+cursor,
        'physical_attempt_count_is_lower_bound':frozen['physical_attempt_count_is_lower_bound'],
        'elapsed_seconds_is_budget_charge_not_exact_runtime':True})
    base.save(OUT/'summary.json',report,exclusive=True)
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':run()
