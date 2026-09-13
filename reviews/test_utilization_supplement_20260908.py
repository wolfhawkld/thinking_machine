"""Small synthetic checks for the independent descriptive report."""
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("supplement", Path(__file__).with_name("utilization-supplement-20260908.py"))
supplement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supplement)
budget_spec = importlib.util.spec_from_file_location("budget", Path(__file__).with_name("budget-sensitivity-20260908.py"))
budget = importlib.util.module_from_spec(budget_spec)
budget_spec.loader.exec_module(budget)


def fixture():
    pairs = []
    for ordinal in range(4):
        arms = {}
        for name, correct in (("context_a", 0), ("context_b", 1)):
            chosen = correct if ordinal < 2 else 2
            features = [{"raw_action_index": raw, "public_features": {
                "K1_supported": True, "full_domain_positive_count": 1,
                "node_count": 3, "child_canonical_hash": f"{raw:064x}",
                "parent_behavior_novelty_count": 10 if raw == chosen else 0}}
                for raw in range(3)]
            arms[name] = {"correct_raw_action_indices": [correct], "public_action_features": features}
        pairs.append({"pair_ordinal": ordinal, "construction_stratum": "synthetic",
                      "action_order": [0, 1, 2], "arms": arms})
    policy = {"policy_id": "public-k1-max-parent-novelty-node-hash",
              "complete_switch_world_count": 2, "signed_total": 4}
    return {"pairs": pairs, "baseline_report": {"policies": [policy]}}


class SupplementTests(unittest.TestCase):
    def test_fifth_round_closure_uses_diagnostic_horizon(self):
        result = SimpleNamespace(exact_identification=True, rounds_completed=5,
                                 steps=[SimpleNamespace(response=SimpleNamespace(is_match=False), N_after=1, N_before=2)])
        self.assertFalse(budget.budget_closure(result, False, 4))
        self.assertTrue(budget.budget_closure(result, False, 5))
        self.assertFalse(budget.budget_closure(result, True, 5))

    def test_pre_live_report_has_no_model_results(self):
        report = supplement.summarize(fixture())
        self.assertFalse(report["model_results_available"])
        self.assertNotIn("model", report)
        self.assertEqual(report["policies"][0]["own_context_hit_count"], 4)

    def test_four_paired_categories_and_invalid_world(self):
        rows = [{"pair_ordinal": i, "complete_two_arm_context_concordant_switch": i in (0, 2),
                 "own_context_correct_count": 2 if i in (0, 2) else 0,
                 "received_invalid_world": i == 3} for i in range(4)]
        report = supplement.summarize(fixture(), rows)
        self.assertEqual(report["policies"][0]["paired_complete_switch_comparison"],
                         {"model_only": 1, "policy_only": 1, "both": 1, "neither": 1})
        self.assertEqual(report["model"]["complete_switch_count"], 2)
        self.assertEqual(report["model"]["invalid_world_count"], 1)
        self.assertEqual(report["world_denominator"], 4)
        self.assertEqual(report["call_denominator"], 8)

    def test_partial_or_duplicate_pairs_rejected(self):
        for rows in ([], [{"pair_ordinal": 0}] * 4):
            with self.assertRaises(ValueError):
                supplement.summarize(fixture(), rows)

    def test_baseline_drift_rejected(self):
        private = fixture()
        private["baseline_report"]["policies"][0]["signed_total"] = 3
        with self.assertRaises(AssertionError):
            supplement.summarize(private)


if __name__ == "__main__":
    unittest.main()
