import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('parallel4_test',Path(__file__).with_name('shortcut_challenge_parallel4_20260910.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class Parallel4Tests(unittest.TestCase):
    def test_real160_prefix_verified_before_migration(self):
        value=m.manifest()
        self.assertEqual(len(value['retained_prefix']),160)
        self.assertEqual(value['same_seed_retry_indices'],[160])
        self.assertEqual(value['worker_count'],4)

    def test_stale_state_requires_bound_external_stop(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'OLD',Path(tmp)):
            m.base.save(Path(tmp)/'manifest.json',{})
            m.base.save(Path(tmp)/'state.json',{'status':'running'})
            m.base.save(Path(tmp)/'threshold-stop-160.json',{'session_termination_confirmed':True,'state_sha256':'wrong'})
            with self.assertRaises(ValueError):m.manifest()

    def test_four_slot_cap(self):
        self.assertEqual([m.available_slots(i) for i in range(6)],[4,3,2,1,0,0])

    def test_pending_uses_set_difference_not_count(self):
        pending=m.pending_indices([{'index':0},{'index':2},{'index':4}])
        self.assertEqual(pending[:3],[1,3,5])
        self.assertEqual(len(pending),1021)
        self.assertEqual(pending[-1],1023)

    def test_no_duplicate_or_out_of_range(self):
        for rows in ([{'index':1},{'index':1}],[{'index':1024}]):
            with self.assertRaises(ValueError):m.pending_indices(rows)

    def test_completion_order_does_not_choose_duplicate(self):
        rows=[{'index':6,'task_identity':'x','prompt_identity':'p'},
              {'index':5,'task_identity':'x','prompt_identity':'q'}]
        a=m.ordered_records(rows,set());b=m.ordered_records(list(reversed(rows)),set())
        self.assertEqual(a,b)
        self.assertEqual([r['duplicate_excluded'] for r in a],[False,True])

    def test_cannot_migrate_running_or_under160(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'OLD',Path(tmp)):
            m.base.save(Path(tmp)/'manifest.json',{})
            for state in ({'status':'running'}, {'status':'interrupted','completed':[{}]*159}):
                m.base.save(Path(tmp)/'state.json',state)
                with self.assertRaises(ValueError):m.manifest()


if __name__=='__main__':unittest.main()
