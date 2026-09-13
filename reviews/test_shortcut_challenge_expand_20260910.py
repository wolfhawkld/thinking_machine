import importlib.util
import math
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('expansion',Path(__file__).with_name('shortcut_challenge_expand_20260910.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class ExpansionTests(unittest.TestCase):
    def test_frozen_prefix_and_unchanged_science(self):
        v=m.manifest()
        self.assertEqual([r['index'] for r in v['retained_prefix']],list(range(128)))
        self.assertEqual(v['candidate_cap'],1024)
        self.assertEqual(v['new_candidate_count'],896)
        self.assertEqual(v['seed_checks']['collisions'],0)
        self.assertIsNone(v['total_seconds'])

    def test_no_timeout_after_old_budget(self):
        self.assertTrue(math.isinf(m.base.remaining(100000,128)))
        self.assertTrue(math.isinf(m.base.remaining(100000,1023)))

    def test_1024_boundary_before_target_draw(self):
        e=m.base.load_engine()
        with patch.object(e,'generate_spark_world') as draw:
            with self.assertRaises(ValueError):e.scan_world(1024,0)
        draw.assert_not_called()
        self.assertEqual(len(set(e.seed_vector())),1024)

    def test_supervisor_starts_at128_stops_at1024(self):
        import ast
        tree=ast.parse(Path(m.base.__file__).read_text())
        ranges=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='range']
        self.assertTrue(any(len(n.args)==2 and isinstance(n.args[1],ast.Constant) and n.args[1].value==1024 for n in ranges))


if __name__=='__main__':unittest.main()
