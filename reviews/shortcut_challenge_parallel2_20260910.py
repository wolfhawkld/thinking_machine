"""Two independent CPU workers, deterministic index-ordered merging."""
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

OLD=base.ROOT/'artifacts/shortcut-challenge-search-1024-20260910'
OUT=base.ROOT/'artifacts/shortcut-challenge-search-parallel2-20260910'
DOC=Path(__file__).with_name('shortcut-challenge-parallel2-20260910.md')
ORIGINAL_MANIFEST=base.manifest
LIMIT=1024
WORKERS=2


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


def manifest():
    for name,want in (('manifest.json','250af6d00a5b0278cc97ea921e40b9196d811586a64b6476bf964982a4af7ab9'),
                      ('state.json','058ad6a356936e5625f9910ef9394f493a8784f94a205a45a2b0acc5032e9f80')):
        if base.sha(OLD/name)!=want:raise ValueError('Prior serial state changed')
    prior=json.loads((OLD/'manifest.json').read_text());state=json.loads((OLD/'state.json').read_text())
    if [r['index'] for r in state['completed']]!=list(range(129)) or state['active_index']!=129 or (OLD/'world-129.json').exists():
        raise ValueError('Unexpected interrupted prefix')
    current=ORIGINAL_MANIFEST()
    for key in ('seeds','source_manifest_sha256','engine_sha256','diagnostic_sha256','summary_source_sha256'):
        if key=='summary_source_sha256':
            if prior[key]!=base.sha(Path(__file__).with_name('shortcut_challenge_summary_20260910.py')):raise ValueError('Summary changed')
        elif current[key]!=prior[key]:raise ValueError('Scientific dependency drift: '+key)
    rows=[]
    for row in state['completed']:
        path=OLD/row['file']
        if base.sha(path)!=row['sha256']:raise ValueError('Prior result changed')
        w=validate_world(path,row['index'],prior['seeds'][row['index']])
        rows.append({**row,'file':str(path),'prompt_identity':w['prompt_identity']})
    ordered=ordered_records(rows,set(prior['old_prompt_identities']))
    if any(a['duplicate_excluded']!=b['duplicate_excluded'] for a,b in zip(rows,ordered,strict=True)):
        raise ValueError('Prior duplicate decisions changed')
    return {**prior,'kind':'shortcut_challenge_parallel2_amendment','worker_count':WORKERS,
            'retained_prefix':ordered,'prior_elapsed_ledger':state['elapsed_seconds'],
            'prior_final_elapsed_unknown':True,'serial_external_interrupt_exit_code':130,
            'serial_running_state_is_stale':True,'retry_index':129,'same_seed_retry_authorized':True,
            'amendment_sha256':base.sha(DOC),'parallel_source_sha256':base.sha(Path(__file__)),
            'serial_manifest_sha256':base.sha(OLD/'manifest.json'),'serial_state_sha256':base.sha(OLD/'state.json')}


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
    state={'kind':'shortcut_challenge_parallel2_state','status':'running','completed':frozen['retained_prefix'],
           'active_indices':[],'active_workers':{},'elapsed_seconds':frozen['prior_elapsed_ledger'],
           'failed_indices':[],'interrupted_indices':[],'time_cutoff_enabled':False,
           'provider_calls':0,'model_outputs_read':False,'worker_count':WORKERS}
    started=time.monotonic();active={};next_index=129;failed=False
    def persist():
        state['completed']=ordered_records(state['completed'],set(frozen['old_prompt_identities']))
        state['active_indices']=sorted(active)
        state['active_workers']={str(i):{'pid':p.pid,'rss_kib':rss_kib(p.pid)} for i,(p,_,_) in active.items()}
        state['elapsed_seconds']=frozen['prior_elapsed_ledger']+time.monotonic()-started
        base.save(OUT/'state.json',state)
    try:
        persist()
        while active or (next_index<LIMIT and not failed):
            while next_index<LIMIT and available_slots(len(active)) and not failed:
                index=next_index;next_index+=1
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
    report.update({'worker_count':2,'time_cutoff_enabled':False,
        'parallel_elapsed_seconds':time.monotonic()-started,
        'physical_world_attempts':len(state['completed'])+3+len(state['failed_indices'])+len(state['interrupted_indices']),
        'elapsed_seconds_is_budget_charge_not_exact_runtime':True})
    base.save(OUT/'summary.json',report,exclusive=True)
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':run()
