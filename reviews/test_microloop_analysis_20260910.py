import copy
import json
import unittest

import microloop_analysis_20260910 as analysis
import microloop_protocol_20260910 as protocol


def _synthetic_worlds():
    """Small in-memory 12-world fixture; no artifact or credential reads."""
    parent = "(ite (gt (var x1) (const 0)) (const 1) (const 0))"
    points = [list(point) for point in protocol.dsl.DOMAIN]
    publics = []
    privates = []
    for ordinal in range(12):
        target = [protocol.dsl.evaluate(protocol.dsl.parse_sexpr(parent), point) for point in protocol.dsl.DOMAIN]
        d0 = [{"point": point, "label": target[i]} for i, point in enumerate(points[:12])]
        query = points[12:61]
        test = [{"point": point, "label": target[i]} for i, point in enumerate(points[61:], 61)]
        publics.append({"ordinal": ordinal, "task_id": f"synthetic-{ordinal}", "D0": d0,
                        "parent": parent, "query_points": query})
        privates.append({"ordinal": ordinal,
                         "evidence": [{"point": point, "label": target[i]} for i, point in enumerate(query, 12)],
                         "test": test, "target_behavior": target})
    return publics, privates


def _responses(public):
    q0, q1 = public["query_points"][:2]
    return [
        json.dumps({"expression": public["parent"], "query": q0}),
        json.dumps({"expression": public["parent"], "query": q1}),
        json.dumps({"expression": public["parent"]}),
    ]


class MicroloopAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.public, self.private = _synthetic_worlds()
        self.records = [
            {"ordinal": ordinal, "condition": condition,
             "responses": _responses(self.public[ordinal])}
            for ordinal in range(12)
            for condition in analysis.CONDITIONS
        ]

    def test_12_worlds_three_conditions_and_per_world_scores(self):
        result = analysis.aggregate(self.public, self.private, self.records)
        self.assertEqual(set(result["conditions"]), set(analysis.CONDITIONS))
        self.assertEqual(result["design"]["trajectories"], 36)
        self.assertEqual(result["design"]["response_slots"], 108)
        self.assertTrue(result["design"]["responses_are_not_independent_samples"])
        for condition in analysis.CONDITIONS:
            summary = result["conditions"][condition]
            self.assertEqual(summary["denominator"], 12)
            self.assertEqual(len(summary["stage_metrics"]), 3)
            self.assertEqual(len(summary["world_scores"]), 12)
            self.assertEqual(summary["missing_responses"], 0)
            for stage in summary["stage_metrics"]:
                self.assertEqual(stage["valid_program"], 12)
                self.assertEqual(stage["visible_consistent"], 12)
                self.assertEqual(stage["full_domain_exact"], 12)
                self.assertEqual(stage["mean_test_accuracy"], 1.0)
        self.assertEqual(result["conditions"]["active"]["new_labels"], 24)
        self.assertEqual(result["conditions"]["random"]["new_labels"], 24)
        self.assertEqual(result["conditions"]["repeat"]["new_labels"], 0)
        self.assertTrue(all(v["ties"] == 12 for v in result["contrasts"].values()))

    def test_missing_record_is_fixed_three_missing_responses(self):
        records = [r for r in self.records if not (r["ordinal"] == 3 and r["condition"] == "random")]
        result = analysis.aggregate(self.public, self.private, records)
        summary = result["conditions"]["random"]
        self.assertEqual(summary["denominator"], 12)
        self.assertEqual(summary["missing_responses"], 3)
        self.assertEqual(summary["stage_metrics"][0]["valid_program"], 11)
        self.assertEqual(summary["world_scores"][3]["missing_responses"], 3)
        self.assertEqual(result["contrasts"]["active_vs_random"]["wins"], 1)

    def test_none_keeps_control_feedback_but_not_program_validity(self):
        records = copy.deepcopy(self.records)
        records[3]["responses"] = [None, None, None]  # ordinal 1, active
        result = analysis.aggregate(self.public, self.private, records)
        row = result["conditions"]["active"]["world_scores"][1]
        self.assertEqual(row["missing_responses"], 3)
        self.assertEqual(row["query_executed"], 0)
        self.assertEqual(row["new_labels"], 0)
        for stage in result["conditions"]["active"]["stage_metrics"]:
            self.assertEqual(stage["valid_program"], 11)

        records[4]["responses"] = [None, None, None]  # ordinal 1, random
        result = analysis.aggregate(self.public, self.private, records)
        row = result["conditions"]["random"]["world_scores"][1]
        self.assertEqual(row["query_executed"], 2)
        self.assertEqual(row["new_labels"], 2)
        self.assertEqual(row["query_valid"], 0)

    def test_rejects_duplicate_unknown_and_wrong_length_records(self):
        duplicate = self.records + [self.records[0]]
        with self.assertRaises(ValueError):
            analysis.aggregate(self.public, self.private, duplicate)
        unknown = copy.deepcopy(self.records)
        unknown[0]["ordinal"] = 12
        with self.assertRaises(ValueError):
            analysis.aggregate(self.public, self.private, unknown)
        wrong_length = copy.deepcopy(self.records)
        wrong_length[0]["responses"] = []
        with self.assertRaises(ValueError):
            analysis.aggregate(self.public, self.private, wrong_length)

    def test_does_not_mutate_inputs(self):
        public = copy.deepcopy(self.public)
        private = copy.deepcopy(self.private)
        records = copy.deepcopy(self.records)
        analysis.aggregate(public, private, records)
        self.assertEqual(public, self.public)
        self.assertEqual(private, self.private)
        self.assertEqual(records, self.records)


if __name__ == "__main__":
    unittest.main()
