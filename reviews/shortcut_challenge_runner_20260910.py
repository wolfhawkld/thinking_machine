"""Budgeted offline supervisor. No credentials, network, or provider imports."""
import argparse
import hashlib
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'artifacts/shortcut-challenge-search-1024-20260910'
PLAN = ROOT / 'reviews/shortcut-challenge-1024-plan-20260910.md'
ENGINE = Path(__file__).with_name('shortcut_challenge_engine_20260910.py')


def load_engine():
    spec = importlib.util.spec_from_file_location('challenge_engine', ENGINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value, exclusive=False):
    payload = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        temp = path.with_suffix(path.suffix + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)


def remaining(elapsed, completed):
    return float('inf')  # Authorized fixed-count scan without time cutoff.


def deadline_status(completed):
    return 'calibration_budget_exhausted' if completed < 4 else 'scan_budget_exhausted'


def resumable(state):
    if state['status'] != 'paused_at_boundary' or state.get('active_index') is not None:
        raise ValueError('Only a saved clean boundary can resume; never redraw interrupted targets')
    if remaining(state['elapsed_seconds'], len(state['completed'])) <= 0:
        raise ValueError('Saved budget exhausted; it cannot be reset')


def worker(index, seed, path):
    # Parent alone handles user interrupts and deadline termination.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    result = load_engine().scan_world(index, seed)
    save(Path(path), result, exclusive=True)


def manifest():
    from src.provenance import source_manifest
    engine = load_engine()
    seeds = engine.seed_vector()
    checks = engine.check_seed_collisions()
    old_path = ROOT / 'artifacts/spark-strong-k4-utilization-primary-benchmark-v2-20260827/private.json'
    if sha(old_path) != 'bbe76032ba8d120c9eb7866cabb3643e81f619fbf91bfbed4c40237e3588f06a':
        raise ValueError('Old cohort identity binding changed')
    old = json.loads(old_path.read_text())
    old_prompts = sorted({engine.prompt_identity(p['prompt_context']['parent'], p['prompt_context']['D0']) for p in old['pairs']})
    return {'kind': 'shortcut_challenge_search_manifest', 'plan_sha256': sha(PLAN),
            'runner_sha256': sha(Path(__file__)), 'engine_sha256': sha(ENGINE),
            'diagnostic_sha256': sha(Path(__file__).with_name('shortcut-challenge-feasibility-20260909.py')),
            'source_manifest_sha256': source_manifest(ROOT)['source_manifest_sha256'],
            'seeds': seeds, 'seed_checks': checks, 'candidate_cap': 1024,
            'calibration_count': 4, 'calibration_seconds': None, 'total_seconds': None,
            'old_prompt_identities': old_prompts, 'old_identity_file_sha256': sha(old_path),
            'duplicate_rule': 'Exclude prior24 matching parent+D0 conservatively, and identical new full task identities; retain all in scan denominator',
            'provider_calls': 0, 'model_outputs_read': False}


def run(resume=False):
    expected = manifest()
    if resume:
        saved = json.loads((OUT / 'manifest.json').read_text())
        if saved != expected:
            raise ValueError('Frozen code/plan/seeds changed')
        state = json.loads((OUT / 'state.json').read_text())
        resumable(state)
        for row in state['completed']:
            if sha(OUT / row['file']) != row['sha256']:
                raise ValueError('Saved completed world changed')
    else:
        subprocess.run(['git', 'check-ignore', '--quiet', str(OUT / 'manifest.json')], cwd=ROOT, check=True)
        OUT.mkdir(mode=0o700, exist_ok=False)
        save(OUT / 'manifest.json', expected, exclusive=True)
        state = {'kind': 'shortcut_challenge_search_state', 'status': 'running',
                 'elapsed_seconds': 0.0, 'completed': [], 'active_index': None,
                 'provider_calls': 0, 'model_outputs_read': False}
    base_elapsed = state['elapsed_seconds']
    started = time.monotonic()
    process = None
    def persist():
        state['elapsed_seconds'] = base_elapsed + time.monotonic() - started
        save(OUT / 'state.json', state)
    try:
        state['status'] = 'running'
        persist()
        for index in range(len(state['completed']), 1024):
            persist()
            if remaining(state['elapsed_seconds'], index) <= 0:
                state['status'] = deadline_status(index)
                break
            state['active_index'] = index
            persist()
            path = OUT / f'world-{index:03d}.json'
            process = mp.get_context('spawn').Process(target=worker, args=(index, expected['seeds'][index], str(path)))
            world_started = time.monotonic()
            process.start()
            last_save = time.monotonic()
            expired = False
            while process.is_alive():
                elapsed = base_elapsed + time.monotonic() - started
                left = remaining(elapsed, index)
                if left <= 0:
                    expired = True
                    process.terminate()
                    process.join(timeout=2)
                    if process.is_alive():
                        process.kill()
                        process.join()
                    break
                process.join(timeout=min(0.25, left))
                if time.monotonic() - last_save >= 2:
                    persist()
                    last_save = time.monotonic()
            process.join()
            expired = expired or remaining(base_elapsed + time.monotonic() - started, index) <= 0
            if expired:
                state['status'] = deadline_status(index)
                state['incomplete_index'] = index
                state['late_file_present_not_scored'] = path.exists()
                break
            if process.exitcode != 0 or not path.exists():
                state['status'] = 'validation_error'
                state['error'] = f'world worker {index} exit {process.exitcode}'
                break
            value = json.loads(path.read_text())
            previous_identities = {row['task_identity'] for row in state['completed']}
            duplicate = (value['task_identity'] in previous_identities or
                         value['prompt_identity'] in expected['old_prompt_identities'])
            state['completed'].append({'index': index, 'file': path.name, 'sha256': sha(path),
                                       'task_identity': value['task_identity'], 'duplicate_excluded': duplicate,
                                       'elapsed_seconds': time.monotonic() - world_started})
            state['active_index'] = None
            persist()
            print(f'Completed {index+1}/1024; elapsed {state["elapsed_seconds"]:.1f}s', flush=True)
            if index == 3:
                state['calibration_elapsed_seconds'] = state['elapsed_seconds']
            process = None
        else:
            state['status'] = 'complete_fixed_range'
    except KeyboardInterrupt:
        state['status'] = 'paused_at_boundary' if state['active_index'] is None else 'interrupted_incomplete_world'
        if state['active_index'] is not None:
            state['incomplete_index'] = state['active_index']
    except Exception as exc:
        state['status'] = 'validation_error'
        state['error'] = str(exc)
        raise
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join()
        persist()
        print(json.dumps({'status': state['status'], 'completed': len(state['completed']),
                          'elapsed_seconds': state['elapsed_seconds']}), flush=True)
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    run(resume=args.resume)
