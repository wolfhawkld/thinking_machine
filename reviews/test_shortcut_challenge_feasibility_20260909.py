import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('challenge', Path(__file__).with_name('shortcut-challenge-feasibility-20260909.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def action(raw, novelty, nodes, constant=False):
    return {'raw_action_index': raw, 'public_features': {'K1_supported': True,
        'child_behavior_is_constant': constant, 'parent_behavior_novelty_count': novelty,
        'node_count': nodes, 'child_canonical_hash': str(raw)}}


class Diagnostics(unittest.TestCase):
    def test_constant_excluded(self):
        self.assertFalse(m.arm_diagnostic([action(0, 100, 1, True), action(1, 10, 3)], 1)['policy_wrong'])

    def test_strict_decoy(self):
        d = m.arm_diagnostic([action(0, 20, 5), action(1, 10, 3)], 1)
        self.assertTrue(d['policy_wrong'])
        self.assertTrue(d['strict_novelty_decoy'])
        self.assertFalse(d['novelty_node_tie'])

    def test_tie_not_strict_decoy(self):
        d = m.arm_diagnostic([action(0, 10, 3), action(1, 10, 3)], 1)
        self.assertTrue(d['policy_wrong'])
        self.assertTrue(d['novelty_node_tie'])
        self.assertFalse(d['strict_novelty_decoy'])

    def test_missing_correct_rejected(self):
        with self.assertRaises(ValueError): m.arm_diagnostic([action(0, 10, 3)], 1)

    def test_empty_capacity(self):
        self.assertEqual(m.summarize([])['maximum_balanced_worlds'], 0)

    def test_strata_overlap_not_four_worlds(self):
        w = {'candidate_index': 0, 'pair_candidates': {m.feasibility.STRICT_TIER:
            [{'stratum': s} for s in m.spark_lineage.MOTIF_STRATA]}}
        result = m.summarize([w])
        self.assertEqual(result['unique_worlds'], 1)
        self.assertEqual(result['maximum_balanced_worlds'], 0)

    def test_four_worlds_can_balance(self):
        worlds = [{'candidate_index': i, 'pair_candidates': {m.feasibility.STRICT_TIER:
                   [{'stratum': s} for s in m.spark_lineage.MOTIF_STRATA]}} for i in range(4)]
        self.assertEqual(m.summarize(worlds)['maximum_balanced_worlds'], 4)


if __name__ == '__main__':
    unittest.main()
