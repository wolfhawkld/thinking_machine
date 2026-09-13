"""Read-only terminal audit. Never starts workers or rewrites search artifacts."""
import json
from pathlib import Path
from unittest.mock import patch

import shortcut_challenge_parallel4_20260910 as run
import shortcut_challenge_summary_20260910 as summary


def require_terminal(state):
    if state['status'] != 'complete_fixed_range':
        raise ValueError('Search is not complete; leave the live runner alone')
    if [r['index'] for r in state['completed']] != list(range(run.LIMIT)):
        raise ValueError('Expected exactly the sorted fixed 1024 indices')
    if state.get('active_indices') or state.get('active_workers'):
        raise ValueError('Terminal state still has active workers')
    if state.get('failed_indices') or state.get('error'):
        raise ValueError('Unresolved search error')
    if state.get('provider_calls') != 0 or state.get('model_outputs_read') is not False:
        raise ValueError('Unexpected model activity')


def require_replay_equal(saved, replay):
    for key, value in replay.items():
        if key not in saved or saved[key] != value:
            raise ValueError('Summary replay mismatch: ' + key)


def audit(run=run):
    state_path = run.OUT / 'state.json'
    before = run.base.sha(state_path)
    state = json.loads(state_path.read_text())
    require_terminal(state)
    manifest = json.loads((run.OUT / 'manifest.json').read_text())
    if manifest != run.manifest():
        raise ValueError('Manifest or scientific dependencies changed')
    records = []
    for row in state['completed']:
        path = run.OUT / row['file']
        if run.base.sha(path) != row['sha256']:
            raise ValueError('Result file hash mismatch')
        w = run.validate_world(path, row['index'], manifest['seeds'][row['index']])
        for key in ('task_identity', 'prompt_identity'):
            if row[key] != w[key]:
                raise ValueError('Ledger/world identity mismatch: ' + key)
        records.append(row)
    if records != run.ordered_records(records, set(manifest['old_prompt_identities'])):
        raise ValueError('Duplicate accounting mismatch')
    if records[:len(manifest['retained_prefix'])] != manifest['retained_prefix']:
        raise ValueError('Retained results changed')
    with patch.object(run.base, 'OUT', run.OUT), patch.object(run.base, 'manifest', run.manifest):
        replay = summary.summarize()
    saved_path = run.OUT / 'summary.json'
    saved = json.loads(saved_path.read_text())
    require_replay_equal(saved, replay)
    if before != run.base.sha(state_path):
        raise ValueError('State changed during audit')
    return {
        'kind': 'shortcut_challenge_fixed1024_terminal_audit',
        'verified': True,
        'completed_worlds': len(records),
        'retained_results_verified': len(manifest['retained_prefix']),
        'state_sha256': before,
        'manifest_sha256': run.base.sha(run.OUT / 'manifest.json'),
        'summary_sha256': run.base.sha(saved_path),
        'audit_source_sha256': run.base.sha(Path(__file__)),
        'summary_replay_equal': True,
        'oracle_reexecuted': False,
        'provider_calls': 0,
        'scope': 'Coverage, identity, file integrity, frozen dependencies and summary replay; not an independent proof of scientific scoring.',
    }


if __name__ == '__main__':
    print(json.dumps(audit(), indent=2))
