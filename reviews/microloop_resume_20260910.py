"""Additive recovery of four lost responses; original frozen sources unchanged."""
import argparse
import http.client
import json
import os
from pathlib import Path

import microloop_live_20260910 as live
from src.providers.openai_compatible import TransportError, UrllibHTTPTransport

EXPECTED = ('03-active/stage-2-attempt-0.request.json',
            '03-random/stage-2-attempt-0.request.json',
            '04-random/stage-2-attempt-0.request.json',
            '04-repeat/stage-1-attempt-0.request.json')


class CompleteReadTransport:
    def post(self, **kwargs):
        try:
            return UrllibHTTPTransport().post(**kwargs)
        except http.client.HTTPException:
            # Includes IncompleteRead/RemoteDisconnected. No partial response is
            # scored; delivery and usage remain unknown, as for network_io.
            raise TransportError(category='network_io', delivery_ambiguous=True) from None


def reconcile():
    live.validate()
    out = live.OUT
    if (out / 'orchestrator.lock').exists():
        raise RuntimeError('Orchestrator lock present')
    path = out / 'recovery-plan.json'
    if path.exists():
        plan = json.loads(path.read_text())
        if plan['recovery_source_sha256'] != live.runner.sha(Path(__file__)):
            raise ValueError('Recovery source drift')
    else:
        requests = list(out.glob('*/*.request.json'))
        results = list(out.glob('*/*.result.json'))
        missing = sorted(str(p.relative_to(out)) for p in requests
                         if not p.with_name(p.name.replace('.request.', '.result.')).exists())
        if len(requests) != 41 or len(results) != 37 or missing != list(EXPECTED):
            raise ValueError('Interrupted snapshot differs')
        raw_hashes = {json.loads(p.read_text())['prompt_sha256'] for p in (out / 'raw').glob('*.json')}
        if any(json.loads((out / name).read_text())['prompt_sha256'] in raw_hashes for name in EXPECTED):
            raise ValueError('Potential recoverable raw response; inspect first')
        plan = {'kind': 'microloop_additive_transport_recovery_v1',
                'approval': 'User said 好的，继续 after explanation of four unresolved attempts and original two-retry cap.',
                'original_plan_sha256': live.runner.sha(out / 'plan.json'),
                'recovery_source_sha256': live.runner.sha(Path(__file__)),
                'missing_requests': list(EXPECTED),
                'reason': 'Process exited with IncompleteRead; four attempts have no raw/result. Individual failure causes unknown.',
                'maximum_calls_unchanged': 110, 'max_retries_unchanged': 2,
                'prior_hashes': {str(p.relative_to(out)): live.runner.sha(p)
                    for p in [*requests, *results, *out.glob('raw/*.json'), *out.glob('*/*.terminal.json')]}}
        live.runner.save(path, plan)
    if plan['original_plan_sha256'] != live.runner.sha(out / 'plan.json'):
        raise ValueError('Original plan drift')
    for name, digest in plan['prior_hashes'].items():
        if live.runner.sha(out / name) != digest:
            raise ValueError('Prior evidence changed')
    for name in EXPECTED:
        target = out / name.replace('.request.', '.result.')
        value = {'status': 'technical_failure', 'usage_unknown': True,
                 'reconciliation': 'No saved raw/result after exited process; response unavailable, delivery unknown.',
                 'recovery_plan_sha256': live.runner.sha(path)}
        if target.exists():
            if json.loads(target.read_text()) != value: raise ValueError('Recovery result drift')
        else:
            live.runner.save(target, value)
    print('Reconciled four unavailable responses; preserved 37 returned responses; retry cap remains two.', flush=True)


def run():
    reconcile()
    if (live.OUT / 'complete.json').exists():
        print('Already complete; no requests')
        return
    key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if not key: raise ValueError('DEEPSEEK_API_KEY absent')
    public, private = live.runner.load_materials(live.OUT)
    records = live.runner.run_collection(public, private,
        lambda pub: live.sender(key, live.OUT / 'raw', CompleteReadTransport),
        live.OUT, max_retries=2, workers=4)
    live.finish(public, private, records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('reconcile', 'run'))
    args = parser.parse_args()
    {'reconcile': reconcile, 'run': run}[args.command]()
