import copy
import unittest

import shortcut_challenge_terminal_audit_20260910 as m


class TerminalAuditTests(unittest.TestCase):
    def state(self):
        return dict(status='complete_fixed_range', completed=[{'index': i} for i in range(1024)],
                    active_indices=[], active_workers={}, failed_indices=[],
                    provider_calls=0, model_outputs_read=False)

    def test_complete_range(self):
        m.require_terminal(self.state())

    def test_reject_live_partial_duplicate_unsorted_or_error(self):
        for changes in (
            {'status': 'running'}, {'completed': self.state()['completed'][:-1]},
            {'completed': [{'index': 0}] * 1024},
            {'completed': list(reversed(self.state()['completed']))},
            {'active_indices': [1023]}, {'active_workers': {'1023': {'pid': 1}}},
            {'failed_indices': [3]}, {'error': 'worker failed'},
            {'provider_calls': 1}, {'model_outputs_read': True},
        ):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                m.require_terminal({**self.state(), **changes})

    def test_summary_replay_exact_values_and_missing_keys(self):
        replay = {'status': 'complete_fixed_range', 'pools': {'strict': 2}}
        m.require_replay_equal({**replay, 'elapsed_extra': 1}, replay)
        changed = copy.deepcopy(replay)
        changed['pools']['strict'] = 3
        for bad in (changed, {'status': 'complete_fixed_range'}):
            with self.assertRaises(ValueError):
                m.require_replay_equal(bad, replay)


if __name__ == '__main__':
    unittest.main()
