import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('audit',Path(__file__).with_name('minnode-structure-audit-20260910.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def action(raw,nodes,constant=False):
    return {'raw_action_index':raw,'public_features':{'K1_supported':True,
        'child_behavior_is_constant':constant,'node_count':nodes,'child_canonical_hash':str(raw)*64,
        'parent_behavior_novelty_count':raw,'full_domain_positive_count':1}}


class AuditTests(unittest.TestCase):
    def test_nonminimum_correct(self):
        c,_=m.diagnose([action(0,2),action(1,4)],[1],[0,1])
        self.assertEqual(c['unique_correct_above_nc_minimum'],1)
        self.assertEqual(c['nc_minnode_hits'],0)

    def test_tie_is_not_unique_minimum(self):
        c,_=m.diagnose([action(0,2),action(1,2)],[1],[0,1])
        self.assertEqual(c['unique_correct_tied_nc_minimum'],1)
        self.assertEqual(c['unique_correct_sole_nc_minimum'],0)

    def test_constant_filter_and_multiple_correct(self):
        c,_=m.diagnose([action(0,1,True),action(1,2),action(2,3)],[1,2],[0,1,2])
        self.assertEqual(c['nc_minnode_hits'],1)
        self.assertEqual(c['unique_correct_contexts'],0)


if __name__=='__main__':unittest.main()
