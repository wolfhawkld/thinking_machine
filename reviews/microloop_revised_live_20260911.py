"""Separately approved revised-prompt 108+2 experiment, on the same 12 worlds."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import microloop_live_20260910 as original_live
import microloop_revision_20260910 as revision
from microloop_resume_20260910 import CompleteReadTransport

ROOT = original_live.ROOT
OUT = ROOT / 'artifacts/microloop-revised-live-20260911'


def isolated(filename, name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_hashes():
    result = original_live.sources()
    for name in ('microloop_revision_20260910.py', 'microloop_resume_20260910.py',
                 'microloop_revised_live_20260911.py', 'microloop-revised-live-protocol-20260911.md'):
        path = ROOT / 'reviews' / name
        result[str(path.relative_to(ROOT))] = original_live.runner.sha(path)
    return result


def adapter(out=OUT):
    # Independent module instances; original imported modules remain unchanged.
    runner = isolated('microloop_runner_20260910.py', '_revised_runner')
    runner.protocol = SimpleNamespace(**{**vars(revision), 'digest':revision.original.digest})
    live = isolated('microloop_live_20260910.py', '_revised_live')
    live.OUT = out
    live.runner = runner
    live.aggregate = revision.aggregate
    live.sources = source_hashes
    return live


def freeze():
    live = adapter()
    public, private = live.runner.load_materials(live.DRAFT)
    OUT.mkdir(mode=0o700, exist_ok=False)
    for name in ('public.json','private.json','audit.json'):
        live.runner.save(OUT / name,json.loads((live.DRAFT / name).read_text()))
    live.runner.save(OUT / 'initial-prompts.json', [revision.render_prompt(p,revision.initial_state(p,'active')) for p in public])
    live.runner.save(OUT / 'plan.json', {
        'kind':'microloop_revised_live_v2_20260911',
        'approval':'User approved new 108+2 budget with 好的，开始 on 2026-09-11.',
        'interpretation':'Revised-prompt exploratory rerun of the same previously used 12 worlds, not unseen confirmation.',
        'settings':live.SETTINGS,'endpoint':live.ENDPOINT,'timeout_seconds':3600,
        'workers':4,'formal_slots':108,'max_retries':2,'maximum_calls':110,
        'maximum_completion_tokens':14417920,'source_hashes':source_hashes(),
        'material_hashes':{n:live.runner.sha(OUT/n) for n in
            ('public.json','private.json','audit.json','initial-prompts.json')}})
    verify()
    print('REVISED FREEZE VERIFIED; no API calls',flush=True)


def verify():
    live=adapter()
    plan=live.validate()
    if plan['kind']!='microloop_revised_live_v2_20260911': raise ValueError('Wrong revision')
    public,_=live.runner.load_materials(OUT)
    prompts=json.loads((OUT/'initial-prompts.json').read_text())
    for p,prompt in zip(public,prompts,strict=True):
        if any(revision.render_prompt(p,revision.initial_state(p,c))!=prompt for c in revision.CONDITIONS):
            raise ValueError('Initial revised prompt drift')
    return live,plan


def run():
    live,plan=verify()
    if (OUT/'complete.json').exists():
        print('Already complete; no requests')
        return
    key=os.environ.get('DEEPSEEK_API_KEY','').strip()
    if not key: raise ValueError('DEEPSEEK_API_KEY absent')
    public,private=live.runner.load_materials(OUT)
    raw=OUT/'raw'; raw.mkdir(mode=0o700,exist_ok=True)
    records=live.runner.run_collection(public,private,
        lambda pub:live.sender(key,raw,CompleteReadTransport),OUT,
        max_retries=plan['max_retries'],workers=plan['workers'])
    live.finish(public,private,records)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('freeze','verify','run'))
    args=parser.parse_args()
    {'freeze':freeze,'verify':verify,'run':run}[args.command]()
