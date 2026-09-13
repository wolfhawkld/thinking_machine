import unittest
import shortcut_challenge_resume982_20260910 as r


class Resume982Tests(unittest.TestCase):
    def state(self):
        return {'completed': [{'index': i} for i in range(982)],
                'active_indices': [982, 983, 984, 985], 'failed_indices': []}

    def test_exact_boundary_and_remaining(self):
        r.validate_boundary(self.state(), r.OLD_STATE_SHA, [])
        self.assertEqual(r.pending_indices(self.state()['completed']), list(range(982, 1024)))
        self.assertEqual(r.WORKERS, 4)

    def test_changed_hash_late_result_or_wrong_boundary_rejected(self):
        for state, digest, late in (
            (self.state(), 'wrong', []), (self.state(), r.OLD_STATE_SHA, ['world-982.json']),
            ({**self.state(), 'completed': []}, r.OLD_STATE_SHA, []),
            ({**self.state(), 'active_indices': []}, r.OLD_STATE_SHA, []),
            ({**self.state(), 'failed_indices': [981]}, r.OLD_STATE_SHA, []),
        ):
            with self.assertRaises(ValueError):
                r.validate_boundary(state, digest, late)


if __name__ == '__main__':
    unittest.main()
