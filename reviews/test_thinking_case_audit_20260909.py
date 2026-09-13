import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("audit", Path(__file__).with_name("thinking-case-audit-20260909.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class AuditTests(unittest.TestCase):
    def test_nonconstant_filter_precedes_novelty(self):
        def row(i, constant, novelty):
            return {"raw_action_index": i, "public_features": {"K1_supported": True,
                    "child_behavior_is_constant": constant, "parent_behavior_novelty_count": novelty,
                    "node_count": 8, "child_canonical_hash": str(i) * 64, "full_domain_positive_count": 0}}
        actions = [row(0, True, 100), row(1, False, 25), row(2, False, 50)]
        self.assertEqual(m.select_nonconstant(actions, [0, 1, 2], "novelty"), 2)
        self.assertEqual(m.select_nonconstant(actions, [0, 1, 2], "minnode"), 1)

    def test_no_nonconstant_has_defined_fallback(self):
        row = {"raw_action_index": 0, "public_features": {"K1_supported": False}}
        self.assertEqual(m.select_nonconstant([row], [0], "novelty"), 0)

    def test_replay_exact_and_case_counts(self):
        expected = m.base.read(m.a.m.OUT / "case-audit.json")
        with patch.object(m.base, "save") as saved, contextlib.redirect_stdout(io.StringIO()):
            m.audit()
        self.assertEqual(saved.call_args.args[1], expected)
        self.assertEqual(sum(c["category"] == "model_only" for c in expected["cases"]), 6)
        self.assertEqual(sum(c["category"] == "policy_only" for c in expected["cases"]), 3)
        d = expected["posthoc_nonconstant_diagnostics"]["novelty"]
        self.assertEqual((d["own"], d["complete_switch"], d["model_only"], d["policy_only"]), (33, 11, 3, 3))


if __name__ == "__main__":
    unittest.main()
