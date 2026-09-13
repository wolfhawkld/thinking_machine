import unittest
import microloop_baselines_20260910 as b
from src import dsl


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.rules = tuple(dsl.parse_sexpr(s) for s in (
            '(ite (gt (var x1) (const 0)) (const 1) (const 0))',
            '(ite (gt (var x2) (const 0)) (const 1) (const 0))'))
        self.obs = [{"point": [-1, -1, 0], "label": 0}]

    def test_balance_prefers_disagreement(self):
        q = b.balanced_query(self.rules, self.obs, [[0, 0, 0], [1, -1, 0]], "test")
        self.assertEqual(q, [1, -1, 0])

    def test_rule_selection_uses_observations(self):
        obs = self.obs + [{"point": [1, -1, 0], "label": 1}]
        result = dsl.parse_sexpr(b.minimum_rule(self.rules, obs))
        self.assertTrue(all(dsl.evaluate(result, r["point"]) == r["label"] for r in obs))

    def test_constant_baseline_preserved(self):
        self.assertEqual(b.minimum_rule(self.rules, self.obs, True), '(const 0)')

    def test_no_seen_query(self):
        with self.assertRaises(ValueError): b.balanced_query(self.rules, self.obs, [[-1, -1, 0]], "test")


if __name__ == "__main__": unittest.main()
