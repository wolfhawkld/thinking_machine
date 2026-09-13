"""Approved 108+2 microloop transport and automatic final analysis."""
import argparse
import base64
import json
import os
from pathlib import Path
import time
import uuid

import microloop_runner_20260910 as runner
from microloop_analysis_20260910 import aggregate
from src.providers.openai_compatible import UrllibHTTPTransport, TransportError

ROOT = runner.ROOT
OUT = ROOT / 'artifacts/microloop-live-20260910'
DRAFT = ROOT / 'artifacts/microloop-draft-20260910'
SETTINGS = {'model': 'deepseek-v4-pro', 'thinking': {'type': 'enabled'},
            'reasoning_effort': 'high', 'max_tokens': 131072,
            'response_format': {'type': 'json_object'}, 'stream': False}
ENDPOINT = 'https://api.deepseek.com/chat/completions'


def sources():
    names = ['microloop_live_20260910.py', 'microloop_runner_20260910.py',
             'microloop_protocol_20260910.py', 'microloop_analysis_20260910.py',
             'new_evidence_scoring_20260910.py', 'microloop-live-protocol-draft-20260910.md']
    paths = [ROOT / 'reviews' / n for n in names]
    paths += [ROOT / 'src/dsl.py', ROOT / 'src/providers/openai_compatible.py']
    return {str(p.relative_to(ROOT)): runner.sha(p) for p in paths}


def freeze():
    runner.load_materials(DRAFT)
    OUT.mkdir(mode=0o700, exist_ok=False)
    for name in ('public.json', 'private.json', 'audit.json'):
        runner.save(OUT / name, json.loads((DRAFT / name).read_text()))
    # save() preserves the builder's serialization, but bind actual copied bytes.
    audit = json.loads((OUT / 'audit.json').read_text())
    for name in ('public', 'private'):
        if runner.sha(OUT / (name + '.json')) != audit[name + '_sha256']:
            raise ValueError('Copied material bytes differ')
    runner.save(OUT / 'plan.json', {'kind': 'microloop_live_v1', 'settings': SETTINGS,
        'endpoint': ENDPOINT, 'timeout_seconds': 3600, 'workers': 4,
        'formal_slots': 108, 'max_retries': 2, 'maximum_calls': 110,
        'maximum_completion_tokens': 14417920,
        'approval': 'User said 开始 after explicit 108+2, thinking, 131072, 4-worker budget proposal.',
        'source_hashes': sources(), 'material_hashes': {n: runner.sha(OUT / n)
            for n in ('public.json', 'private.json', 'audit.json')}})
    print('FROZEN: 108+2 approved; no API calls during freeze', flush=True)


def validate():
    plan = json.loads((OUT / 'plan.json').read_text())
    if plan['settings'] != SETTINGS or plan['source_hashes'] != sources():
        raise ValueError('Frozen source/config drift')
    for name, value in plan['material_hashes'].items():
        if runner.sha(OUT / name) != value:
            raise ValueError('Frozen material drift')
    if (plan['endpoint'], plan['timeout_seconds'], plan['workers'], plan['max_retries'],
        plan['maximum_calls'], plan['formal_slots']) != (ENDPOINT, 3600, 4, 2, 110, 108):
        raise ValueError('Budget drift')
    return plan


def sender(key, raw_dir, transport_factory=UrllibHTTPTransport):
    def send(prompt):
        started = time.monotonic()
        raw = {'prompt_sha256': runner.protocol.digest(prompt)}
        path = raw_dir / (uuid.uuid4().hex + '.json')
        try:
            response = transport_factory().post(url=ENDPOINT,
                headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
                body=json.dumps({**SETTINGS, 'messages': [{'role': 'user', 'content': prompt}]}).encode(),
                timeout=3600)
        except TransportError as exc:
            runner.save(path, {**raw, 'transport_error': exc.category, 'usage_unknown': True})
            if exc.category in ('timeout', 'dns', 'tls', 'connection_refused', 'connection_reset', 'network_io'):
                raise runner.TechnicalFailure() from None
            raise RuntimeError('Nonretryable transport configuration error') from None
        raw.update(http_status=response.status, body_base64=base64.b64encode(response.body).decode(),
                   latency_seconds=time.monotonic() - started)
        runner.save(path, raw)
        if response.status in (408, 429) or 500 <= response.status < 600:
            raise runner.TechnicalFailure()
        if response.status != 200:
            raise RuntimeError(f'Nonretryable HTTP status {response.status}')
        try:
            payload = json.loads(response.body)
            if payload.get('model') != SETTINGS['model']:
                raise RuntimeError('Unexpected response model; raw saved')
            choice = payload['choices'][0]
            content = choice['message'].get('content')
            if content is not None and not isinstance(content, str):
                raise ValueError('Nontext content')
        except (ValueError, KeyError, IndexError, TypeError):
            raise runner.TechnicalFailure() from None
        print('response saved', flush=True)
        return {'content': content, 'usage': payload.get('usage'),
                'finish_reason': choice.get('finish_reason'), 'model': payload['model'],
                'raw_file': str(path.relative_to(OUT)), 'raw_sha256': runner.sha(path)}
    return send


def finish(public, private, records):
    analysis = aggregate(public, private, records)
    results = [json.loads(p.read_text()) for p in OUT.glob('*/*.result.json')]
    known_input = known_output = 0
    unknown = 0
    for r in results:
        usage = r.get('payload', {}).get('usage')
        if not isinstance(usage, dict) or not all(type(usage.get(k)) is int for k in ('prompt_tokens', 'completion_tokens')):
            unknown += 1
        else:
            known_input += usage['prompt_tokens']
            known_output += usage['completion_tokens']
    attempts = len(list(OUT.glob('*/*.request.json')))
    if not 108 <= attempts <= 110 or len(results) != attempts:
        raise ValueError('Attempt accounting mismatch')
    for name, data in [('records.json', records), ('analysis.json', analysis),
        ('complete.json', {'trajectories': len(records), 'formal_slots': 108,
         'physical_attempts': attempts, 'known_input_tokens': known_input,
         'known_output_tokens': known_output, 'unknown_usage_attempts': unknown,
         'usage_complete': unknown == 0})]:
        path = OUT / name
        if path.exists():
            if json.loads(path.read_text()) != data: raise ValueError('Final artifact drift')
        else: runner.save(path, data)
    print('COMPLETE: 36 trajectories; analysis and accounting saved', flush=True)


def run():
    plan = validate()
    if (OUT / 'complete.json').exists():
        print('Already complete; no requests sent')
        return
    key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if not key: raise ValueError('DEEPSEEK_API_KEY is absent')
    public, private = runner.load_materials(OUT)
    raw_dir = OUT / 'raw'
    raw_dir.mkdir(mode=0o700, exist_ok=True)
    records = runner.run_collection(public, private, lambda pub: sender(key, raw_dir), OUT,
                                    max_retries=plan['max_retries'], workers=plan['workers'])
    finish(public, private, records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('freeze', 'verify', 'run'))
    args = parser.parse_args()
    {'freeze': freeze, 'verify': validate, 'run': run}[args.command]()
