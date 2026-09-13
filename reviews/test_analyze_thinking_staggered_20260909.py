import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("analysis", Path(__file__).with_name("analyze-thinking-staggered-20260909.py"))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.p, self.old, self.preserved = a.m.checked_plan()
        self.g = a.base.read(a.m.OUT / "generation.json")
        self.events = [json.loads(s) for s in (a.m.OUT / "attempts.jsonl").read_text().splitlines()]

    def validate(self, g, events=None):
        a.validate_generation(self.p, self.old, self.preserved, g, self.events if events is None else events,
                              lambda i: a.base.read(a.m.OUT / f"response-{i:02d}.json"))

    def test_complete_validates(self):
        self.validate(self.g)

    def test_missing_or_reordered_response_rejected(self):
        g = copy.deepcopy(self.g)
        g["records"].pop()
        with self.assertRaises(ValueError): self.validate(g)
        g = copy.deepcopy(self.g)
        g["records"][0], g["records"][1] = g["records"][1], g["records"][0]
        with self.assertRaises(ValueError): self.validate(g)

    def test_duplicate_ledger_response_rejected(self):
        events = copy.deepcopy(self.events)
        events.insert(-1, next(e for e in events if e["event"] == "response"))
        with self.assertRaises(ValueError): self.validate(self.g, events)

    def test_unknown_usage_cannot_be_claimed_complete(self):
        g = copy.deepcopy(self.g)
        g["total_usage_complete"] = True
        with self.assertRaises(ValueError): self.validate(g)

    def test_offline_replay_exact(self):
        expected = a.base.read(a.m.OUT / "analysis.json")
        with patch.object(a.base, "save") as save, contextlib.redirect_stdout(io.StringIO()):
            a.analyze()
        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.args[1], expected)

    def test_direct_choice_arithmetic_matches_scorer(self):
        # Independent arithmetic on selected options, without calling the scorer.
        private = a.base.read(a.base.ROOT / self.old["benchmark_binding"]["private_key_relative_path"])
        by_task = {r["task_id"]: r for r in self.g["records"]}
        own = cross = complete = favorable = adverse = tie = 0
        for pair in private["pairs"]:
            aa, bb = (pair["arms"][arm] for arm in ("context_a", "context_b"))
            x, y = by_task[aa["task_id"]]["selected_option_id"], by_task[bb["task_id"]]["selected_option_id"]
            ca, cb = aa["correct_option_ids"][0], bb["correct_option_ids"][0]
            o, c = int(x == ca) + int(y == cb), int(x == cb) + int(y == ca)
            own += o; cross += c; complete += o == 2
            favorable += o > c; adverse += o < c; tie += o == c
        self.assertEqual((own, cross, complete, favorable, adverse, tie), (34, 1, 11, 22, 0, 2))


if __name__ == "__main__":
    unittest.main()
