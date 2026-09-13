"""Durable, offline-tested microloop orchestration; no network adapter enabled.

send(prompt) is an injected one-shot transport returning {content, usage}.
Only TechnicalFailure is retryable. Unknown in-flight attempts require manual
reconciliation, never automatic resubmission. Up to four parallel trajectories.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import microloop_protocol_20260910 as protocol


class TechnicalFailure(Exception):
    pass


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x') as stream:
        path.chmod(0o600)
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        import os
        os.fsync(stream.fileno())


def load_materials(directory):
    audit = json.loads((directory / 'audit.json').read_text())
    for name in ('public', 'private'):
        if sha(directory / (name + '.json')) != audit[name + '_sha256']:
            raise ValueError('Material hash mismatch')
    public = json.loads((directory / 'public.json').read_text())['worlds']
    private = json.loads((directory / 'private.json').read_text())['worlds']
    if [w['ordinal'] for w in public] != list(range(12)) or [w['ordinal'] for w in private] != list(range(12)):
        raise ValueError('Expected fixed 12 worlds')
    for w in public:
        protocol.validate_public(w)
    return public, private


def run_trajectory(public, condition, label_at, send, directory, retry_budget):
    """Resume completed responses without re-sending; mutable global retry count.

    Use a single orchestrator. Retries consume the experiment-wide extra-attempt
    budget supplied by the caller. No retry for malformed or inaccurate content.
    """
    directory.mkdir(parents=True, exist_ok=True)
    state = protocol.initial_state(public, condition)
    responses = []
    for stage in range(3):
        if retry_budget.get('stop') is not None and retry_budget['stop'].is_set():
            raise RuntimeError('Collection stopped after a fatal error')
        prompt = protocol.render_prompt(public, state)
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        terminal_path = directory / f'stage-{stage}.terminal.json'
        if terminal_path.exists():
            terminal = json.loads(terminal_path.read_text())
            if terminal['prompt_sha256'] != prompt_hash:
                raise ValueError('Terminal prompt drift')
            content = terminal['content']
            responses.append(content)
            state = protocol.advance(public, state, content, label_at)
            continue
        attempt = 0
        while True:
            stem = directory / f'stage-{stage}-attempt-{attempt}'
            request_path = stem.with_suffix('.request.json')
            result_path = stem.with_suffix('.result.json')
            request = {'stage': stage, 'attempt': attempt, 'prompt': prompt,
                       'prompt_sha256': prompt_hash}
            if request_path.exists():
                if json.loads(request_path.read_text()) != request:
                    raise ValueError('Request drift')
                if not result_path.exists():
                    raise RuntimeError('Unresolved in-flight attempt; reconcile before resuming')
                result = json.loads(result_path.read_text())
            else:
                if retry_budget.get('stop') is not None and retry_budget['stop'].is_set():
                    raise RuntimeError('Collection stopped before dispatch')
                if result_path.exists():
                    raise ValueError('Orphan result')
                save(request_path, request)
                try:
                    payload = send(prompt)
                    if not isinstance(payload, dict) or not isinstance(payload.get('content'), (str, type(None))):
                        raise ValueError('Transport envelope invalid; reconcile saved request')
                    result = {'status': 'returned', 'payload': payload}
                except TechnicalFailure:
                    # Do not log arbitrary exception strings (URLs/credentials).
                    result = {'status': 'technical_failure', 'usage_unknown': True}
                save(result_path, result)
            if result['status'] == 'returned':
                content = result['payload']['content']
                break
            if result['status'] != 'technical_failure':
                raise ValueError('Unknown attempt status')
            # Existing retries must be replayed even after current budget is used.
            next_path = directory / f'stage-{stage}-attempt-{attempt + 1}.request.json'
            if next_path.exists():
                attempt += 1
                continue
            with retry_budget.get('lock', nullcontext()):
                granted = retry_budget['remaining'] > 0
                if granted:
                    retry_budget['remaining'] -= 1
            if granted:
                attempt += 1
                continue
            content = None
            break
        save(terminal_path, {'prompt_sha256': prompt_hash, 'content': content})
        responses.append(content)
        state = protocol.advance(public, state, content, label_at)
    return {'ordinal': public['ordinal'], 'condition': condition, 'responses': responses}


def run_collection(public, private, send_factory, output, max_retries=2, workers=4):
    """Bounded parallel trajectories, sequential stages, exclusive orchestrator.

    send_factory receives public material only, never private test/target data.
    A crashed lock is reconciled manually. Existing physical retries count toward
    the same global cap on resume. No model transport is supplied by this module.
    """
    if len(public) != 12 or len(private) != 12 or workers not in range(1, 5):
        raise ValueError('Collection dimensions')
    output.mkdir(parents=True, exist_ok=True)
    lock = output / 'orchestrator.lock'
    save(lock, {'kind': 'single_orchestrator'})
    try:
        existing_retries = sum(json.loads(p.read_text())['attempt'] > 0
                               for p in output.glob('*/*.request.json'))
        if existing_retries > max_retries:
            raise ValueError('Existing retries exceed budget')
        budget = {'remaining': max_retries - existing_retries, 'lock': threading.Lock(),
                  'stop': threading.Event()}
        def execute(*args):
            try:
                return run_trajectory(*args)
            except Exception:
                budget['stop'].set()
                raise
        jobs = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for pub, priv in zip(public, private, strict=True):
                if pub['ordinal'] != priv['ordinal']:
                    raise ValueError('World binding mismatch')
                labels = {tuple(r['point']): r['label'] for r in priv['evidence']}
                # Rotate condition submission order across worlds, without labels.
                offset = pub['ordinal'] % 3
                order = protocol.CONDITIONS[offset:] + protocol.CONDITIONS[:offset]
                for condition in order:
                    jobs.append(pool.submit(execute, pub, condition,
                        lambda q, labels=labels: labels[tuple(q)], send_factory(pub),
                        output / f"{pub['ordinal']:02d}-{condition}", budget))
            rows = [job.result() for job in jobs]
        return sorted(rows, key=lambda r: (r['ordinal'], r['condition']))
    finally:
        lock.unlink()


def mock_run(materials, output):
    public, private = load_materials(materials)
    output.mkdir(mode=0o700, exist_ok=False)
    save(output / 'plan.json', {'kind': 'offline_mock_only', 'provider_calls': 0,
         'public_sha256': sha(materials / 'public.json'),
         'private_sha256': sha(materials / 'private.json'), 'formal_slots': 108})
    def factory(pub):
        def send(prompt):
            final = 'Final response; no further queries.' in prompt
            response = {'expression': pub['parent']}
            if not final:
                stage = 0 if 'Measurement stage 1.' in prompt else 1
                response['query'] = pub['query_points'][stage]
            return {'content': json.dumps(response), 'usage': None, 'mock': True}
        return send
    records = run_collection(public, private, factory, output, max_retries=0, workers=4)
    save(output / 'records.json', records)
    from microloop_analysis_20260910 import aggregate
    save(output / 'analysis.json', aggregate(public, private, records))
    save(output / 'complete.json', {'mock_trajectories': len(records), 'provider_calls': 0,
         'records_sha256': sha(output / 'records.json'), 'analysis_sha256': sha(output / 'analysis.json')})
    print('OFFLINE MOCK COMPLETE: 36 trajectories, 108 synthetic responses, zero API calls')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mock', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    mock_run(ROOT / 'artifacts/microloop-draft-20260910', args.output)
