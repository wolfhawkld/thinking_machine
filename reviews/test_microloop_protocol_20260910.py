import copy
import json
import unittest

import microloop_protocol_20260910 as m


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        points = list(map(list, m.dsl.DOMAIN))
        self.public = {"ordinal": 0, "task_id": "synthetic-only", "D0": [{"point": p, "label": 0} for p in points[:12]],
                       "parent": "(const 0)", "query_points": points[12:61]}
        self.private = {"test": [{"point": p, "label": 0} for p in points[61:]], "target_behavior": [0] * 125}
        self.called = []

    def label(self, point):
        self.assertIn(point, self.public["query_points"])
        self.called.append(point)
        return 0

    def response(self, point):
        return json.dumps({"expression": "(const 0)", "query": point})

    def test_three_calls_two_queries(self):
        s = m.initial_state(self.public, "active")
        for p in self.public["query_points"][:2]:
            s = m.advance(self.public, s, self.response(p), self.label)
        s = m.advance(self.public, s, '{"expression":"(const 0)"}', self.label)
        self.assertEqual(len(self.called), 2)
        self.assertEqual(m.score_trajectory(self.public, self.private, s)["final_test_accuracy"], 1)
        with self.assertRaises(ValueError): m.advance(self.public, s, "{}", self.label)

    def test_repeat_no_oracle(self):
        s = m.initial_state(self.public, "repeat")
        for _ in range(2): s = m.advance(self.public, s, "{}", self.label)
        self.assertEqual(self.called, [])
        self.assertEqual(len(s["observations"]), 14)

    def test_random_ignores_proposal_and_no_replacement(self):
        s = m.initial_state(self.public, "random")
        for _ in range(2): s = m.advance(self.public, s, "{}", self.label)
        self.assertEqual(self.called, m.schedule(self.public))
        self.assertNotEqual(self.called[0], self.called[1])

    def test_invalid_active_no_feedback(self):
        for p in [self.private["test"][0]["point"], [True, 0, 0], [9, 9, 9], self.public["D0"][0]["point"]]:
            s = m.advance(self.public, m.initial_state(self.public, "active"), self.response(p), self.label)
            self.assertIsNone(s["history"][0]["feedback"])
        self.assertEqual(self.called, [])

    def test_repeated_active_query_consumes_round(self):
        p = self.public["query_points"][0]
        s = m.advance(self.public, m.initial_state(self.public, "active"), self.response(p), self.label)
        s = m.advance(self.public, s, self.response(p), self.label)
        self.assertEqual(len(self.called), 1)
        self.assertIsNone(s["history"][1]["feedback"])

    def test_no_private_prompt_and_equal_initial_prompts(self):
        prompts = [m.render_prompt(self.public, m.initial_state(self.public, c)) for c in m.CONDITIONS]
        self.assertEqual(len(set(prompts)), 1)
        self.assertNotIn(self.public["task_id"], prompts[0])
        bad = {**self.public, "target_behavior": [0] * 125}
        with self.assertRaises(ValueError): m.initial_state(bad, "active")

    def test_valid_query_survives_invalid_program(self):
        content = json.dumps({"expression": "not-a-program", "query": self.public["query_points"][0]})
        s = m.advance(self.public, m.initial_state(self.public, "active"), content, self.label)
        self.assertFalse(s["history"][0]["valid_program"])
        self.assertEqual(len(self.called), 1)

    def test_invalid_final_fixed_zero(self):
        s = m.initial_state(self.public, "repeat")
        for _ in range(3): s = m.advance(self.public, s, "{}", self.label)
        self.assertEqual(m.score_trajectory(self.public, self.private, s)["final_test_accuracy"], 0)
        bad = copy.deepcopy(self.private)
        bad["test"][0]["label"] = 1
        with self.assertRaises(ValueError): m.score_trajectory(self.public, bad, s)

    def test_state_not_mutated(self):
        s = m.initial_state(self.public, "active")
        m.advance(self.public, s, self.response(self.public["query_points"][0]), self.label)
        self.assertEqual(s["round"], 0)
        self.assertEqual(s["history"], [])

    def test_actual_feedback_changes_next_prompt(self):
        q = self.public["query_points"][0]
        s = m.initial_state(self.public, "active")
        a = m.advance(self.public, s, self.response(q), lambda _: 0)
        b = m.advance(self.public, s, self.response(q), lambda _: 1)
        self.assertNotEqual(m.render_prompt(self.public, a), m.render_prompt(self.public, b))
        self.assertEqual(s["observations"], self.public["D0"])

    def test_score_uses_only_prior_feedback_for_each_stage(self):
        q1, q2 = self.public["query_points"][:2]
        private = copy.deepcopy(self.private)
        private["target_behavior"][list(m.dsl.DOMAIN).index(tuple(q1))] = 1
        s = m.initial_state(self.public, "active")
        s = m.advance(self.public, s, self.response(q1), lambda _: 1)
        s = m.advance(self.public, s, self.response(q2), lambda _: 0)
        s = m.advance(self.public, s, '{"expression":"(const 0)"}', self.label)
        result = m.score_trajectory(self.public, private, s)
        self.assertTrue(result["stages"][0]["visible_consistent"])
        self.assertFalse(result["stages"][1]["visible_consistent"])
        self.assertEqual(result["final_test_accuracy"], 1)


if __name__ == "__main__": unittest.main()
