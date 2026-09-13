"""Authorized same-seed continuation, reusing the unchanged four-worker scheduler."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import shortcut_challenge_parallel4_20260910 as previous

base = previous.base
engine = previous.engine
LIMIT = previous.LIMIT
WORKERS = previous.WORKERS
validate_world = previous.validate_world
ordered_records = previous.ordered_records
pending_indices = previous.pending_indices
OLD = previous.OUT
OUT = base.ROOT / 'artifacts/shortcut-challenge-search-resume982-20260910'
DOC = Path(__file__).with_name('shortcut-challenge-resume982-20260910.md')
OLD_STATE_SHA = 'e0c8728d6039919ce2ad802b3b61cc98c2c8d453e8ca93142670224f7d174343'
OLD_MANIFEST = previous.manifest


def validate_boundary(state, state_sha, late_files):
    if state_sha != OLD_STATE_SHA:
        raise ValueError('Old state changed; recheck whether previous work resumed')
    if [r['index'] for r in state['completed']] != list(range(982)):
        raise ValueError('Expected the authorized 982-result boundary')
    if state.get('active_indices') != [982, 983, 984, 985] or state.get('failed_indices'):
        raise ValueError('Unexpected interruption boundary')
    if late_files:
        raise ValueError('Late results exist; review before any same-seed retry')


def manifest():
    prior = json.loads((OLD / 'manifest.json').read_text())
    state = json.loads((OLD / 'state.json').read_text())
    late = [p.name for p in OLD.glob('world-*.json') if int(p.stem.split('-')[-1]) >= 982]
    validate_boundary(state, base.sha(OLD / 'state.json'), late)
    if prior != OLD_MANIFEST():
        raise ValueError('Prior manifest or scientific dependencies changed')
    rows = []
    for row in state['completed']:
        path = OLD / row['file']
        if base.sha(path) != row['sha256']:
            raise ValueError('Retained result hash changed')
        w = validate_world(path, row['index'], prior['seeds'][row['index']])
        if any(row[k] != w[k] for k in ('task_identity', 'prompt_identity')):
            raise ValueError('Retained ledger identity changed')
        rows.append({**row, 'file': str(path)})
    if rows != ordered_records(rows, set(prior['old_prompt_identities'])):
        raise ValueError('Retained duplicate accounting changed')
    if rows[:160] != prior['retained_prefix']:
        raise ValueError('Original 160-result prefix changed')
    dispatched_lower_bound = len(rows) - len(prior['retained_prefix']) + len(state['active_indices'])
    return {
        **prior,
        'kind': 'shortcut_challenge_resume982_manifest',
        'retained_prefix': rows,
        'prior_elapsed_ledger': state['elapsed_seconds'],
        'prior_final_elapsed_unknown': True,
        'physical_attempt_count_is_lower_bound': True,
        'prior_physical_attempts': prior['prior_physical_attempts'] + dispatched_lower_bound,
        'same_seed_retry_indices': state['active_indices'],
        'same_seed_retries_authorized': True,
        'previous_session_missing': True,
        'previous_exit_code': None,
        'previous_manifest_sha256': base.sha(OLD / 'manifest.json'),
        'previous_state_sha256': OLD_STATE_SHA,
        'resume_source_sha256': base.sha(Path(__file__)),
        'resume_plan_sha256': base.sha(DOC),
    }


def audit():
    import shortcut_challenge_terminal_audit_20260910 as terminal
    return terminal.audit(run=sys.modules[__name__])


def run():
    # Scope the adapter; worker computation and scheduler source remain untouched.
    with patch.object(previous, 'OUT', OUT), patch.object(previous, 'manifest', manifest):
        previous.run()
    if json.loads((OUT / 'state.json').read_text())['status'] == 'complete_fixed_range':
        report = audit()
        base.save(OUT / 'audit.json', report, exclusive=True)
        print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--audit']:
        print(json.dumps(audit(), indent=2))
    elif sys.argv[1:]:
        raise SystemExit('Usage: shortcut_challenge_resume982_20260910.py [--audit]')
    else:
        run()
