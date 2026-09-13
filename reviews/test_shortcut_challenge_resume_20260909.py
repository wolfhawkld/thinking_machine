import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('resume_test_module',Path(__file__).with_name('shortcut_challenge_resume_20260909.py'))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ResumeTests(unittest.TestCase):
    def test_calibration_extension_not_budget_reset(self):
        self.assertEqual(m.remaining(600,3),300)
        self.assertEqual(m.remaining(900,3),0)
        self.assertEqual(m.remaining(800,4),2800)
        self.assertEqual(m.remaining(3600,100),0)

    def test_exact_prefix_and_amendment(self):
        value=m.manifest()
        self.assertEqual([r['index'] for r in value['retained_prefix']],[0,1,2])
        self.assertEqual(value['retry_index'],3)
        self.assertEqual(value['previous_runtime_charged_seconds'],600)
        self.assertEqual(value['candidate_cap'],128)


if __name__=='__main__':
    unittest.main()
