import importlib.util
import math
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('full_range_test',Path(__file__).with_name('shortcut_challenge_full_range_20260909.py'))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FullRangeTests(unittest.TestCase):
    def test_no_elapsed_cutoff(self):
        for completed in (0,3,51,127):
            self.assertTrue(math.isinf(m.remaining(100000,completed)))

    def test_all_51_files_retained_and_seed_bound(self):
        prior,state,rows=m.checked_prefix()
        self.assertEqual([r['index'] for r in rows],list(range(51)))
        self.assertGreater(state['elapsed_seconds'],3600)
        self.assertEqual(len(prior['seeds']),128)

    def test_manifest_keeps_range_not_time_cap(self):
        v=m.manifest()
        self.assertEqual(v['candidate_cap'],128)
        self.assertEqual(v['retry_index'],51)
        self.assertIsNone(v['total_seconds'])
        self.assertFalse(v['time_cutoff_enabled'])
        self.assertGreater(v['prior_elapsed_ledger'],3600)


if __name__=='__main__':
    unittest.main()
