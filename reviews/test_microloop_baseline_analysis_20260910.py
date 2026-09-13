import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import microloop_baseline_analysis_20260910 as analysis
from src import dsl


def _synthetic_worlds():
    domain = list(dsl.DOMAIN)
    d0_points = domain[:12]
    query_points = domain[12:61]
    test_points = domain[61:125]
    expression = "(ite (gt (var x1) (const 0)) (const 1) (const 0))"
    ast = dsl.parse_sexpr(expression)
    labels = dict(zip(domain, dsl.behavior_vector(ast), strict=True))
    public = {
        "ordinal": 0,
        "task_id": "microloop-0",
        "D0": [{"point": list(p), "label": labels[p]} for p in d0_points],
        "parent": expression,
        "query_points": [list(p) for p in query_points],
    }
    private = {
        "world_hash": "b" * 64,
        "test": [{"point": list(p), "label": labels[p]} for p in test_points],
        "target_behavior": list(dsl.behavior_vector(ast)),
    }
    return public, private


class BaselineAnalysisTests(unittest.TestCase):
    def test_score_expression_keeps_visible_observations_separate(self):
        public, private = _synthetic_worlds()
        result = analysis._score_expression(
            public["parent"], public, private, public["D0"]
        )
        self.assertTrue(result["valid_program"])
        self.assertTrue(result["D0_consistent"])
        self.assertTrue(result["visible_consistent"])
        self.assertEqual(result["test_correct"], 64)
        self.assertTrue(result["full_domain_exact"])

    def test_summarize_uses_equal_world_denominator_and_paired_deltas(self):
        score = {
            "test_accuracy": 0.5,
            "test_correct": 32,
            "test_perfect": False,
            "full_domain_exact": False,
            "valid_program": True,
            "D0_consistent": True,
            "visible_consistent": True,
            "consistent_test_accuracy": 0.5,
        }
        rows = []
        for ordinal in range(12):
            policies = {}
            for policy in analysis.POLICIES:
                stages = []
                for stage in range(3):
                    candidate_scores = {}
                    for candidate in analysis.CANDIDATES:
                        value = dict(score)
                        if policy == "balanced" and stage == 2 and candidate == "parent":
                            value.update(test_correct=40, test_accuracy=0.625, consistent_test_accuracy=0.625)
                        candidate_scores[candidate] = value
                    stages.append({"stage": stage, "observed_count": 12, "new_observations": 0, "scores": candidate_scores})
                policies[policy] = {"queries": [], "stages": stages}
            rows.append({"ordinal": ordinal, "task_id": f"microloop-{ordinal}", "world_hash": "a" * 64, "policies": policies})
        result = analysis.summarize(rows)
        balanced_parent = result["conditions"]["balanced"]["stages"]["2"]["parent"]
        self.assertEqual(balanced_parent["worlds"], 12)
        self.assertAlmostEqual(balanced_parent["mean_test_accuracy"], 0.625)
        delta = result["paired_world_deltas"]["balanced_vs_random"]["2"]["parent"]
        self.assertEqual(delta["worlds"], 12)
        self.assertEqual(delta["mean_test_correct_delta"], 8)
        self.assertEqual((delta["wins"], delta["losses"], delta["ties"]), (12, 0, 0))

    def test_exclusive_json_writer_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            analysis._write_exclusive_json(path, {"ok": True})
            self.assertEqual(json.loads(path.read_text()), {"ok": True})
            with self.assertRaises(FileExistsError):
                analysis._write_exclusive_json(path, {"ok": False})

    def test_synthetic_world_runs_three_public_policies(self):
        public, private = _synthetic_worlds()
        reservoir = (dsl.parse_sexpr(public["parent"]),)
        result = analysis.analyze_world(public, private, reservoir)
        self.assertEqual(set(result["policies"]), set(analysis.POLICIES))
        for policy in analysis.POLICIES:
            self.assertEqual(len(result["policies"][policy]["stages"]), 3)
            self.assertEqual(len(result["policies"][policy]["queries"]), 2)
            self.assertEqual(
                result["policies"][policy]["stages"][-1]["scores"]["parent"]["test_correct"],
                64,
            )


if __name__ == "__main__":
    unittest.main()
