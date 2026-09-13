import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('parallel_test',Path(__file__).with_name('shortcut_challenge_parallel2_20260910.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class ParallelTests(unittest.TestCase):
    def test_cap_two(self):
        self.assertEqual([m.available_slots(n) for n in (0,1,2,3)],[2,1,0,0])

    def test_out_of_order_duplicate_winner_is_lowest_index(self):
        a={'index':130,'task_identity':'same','prompt_identity':'p'}
        b={'index':129,'task_identity':'same','prompt_identity':'q'}
        result=m.ordered_records([a,b],set())
        self.assertEqual([r['index'] for r in result],[129,130])
        self.assertEqual([r['duplicate_excluded'] for r in result],[False,True])
        self.assertEqual(result,m.ordered_records([b,a],set()))

    def test_previous_prompt_excluded(self):
        r=m.ordered_records([{'index':2,'task_identity':'x','prompt_identity':'seen'}],{'seen'})
        self.assertTrue(r[0]['duplicate_excluded'])

    def test_duplicate_index_rejected(self):
        row={'index':1,'task_identity':'x','prompt_identity':'p'}
        with self.assertRaises(ValueError):m.ordered_records([row,row],set())

    def test_prior129_exactly_retained_and_same_engine(self):
        v=m.manifest()
        self.assertEqual([r['index'] for r in v['retained_prefix']],list(range(129)))
        self.assertEqual(v['retry_index'],129)
        self.assertEqual(v['worker_count'],2)
        self.assertFalse(v['time_cutoff_enabled'])


if __name__=='__main__':unittest.main()
