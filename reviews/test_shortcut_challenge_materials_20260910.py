import unittest
import shortcut_challenge_materials_20260910 as m


class SelectionTests(unittest.TestCase):
    def test_minimum_hash_not_input_order(self):
        pairs = [{'x': 2}, {'x': 1}]
        comparisons = [{'pair_sha256': m.e.digest(p), 'policies': {
            'nonconstant-minnode': {'complete_switch': False},
            'nonconstant-novelty': {'complete_switch': False}}} for p in pairs]
        world = {'pair_candidates': {m.e.f.STRICT_TIER: pairs}, 'policy_comparisons': comparisons}
        self.assertEqual(m.choose_pair(world)[0], min(m.e.digest(p) for p in pairs))
        world['pair_candidates'][m.e.f.STRICT_TIER].reverse()
        world['policy_comparisons'].reverse()
        self.assertEqual(m.choose_pair(world)[0], min(m.e.digest(p) for p in pairs))

    def test_both_policies_must_fail_and_binding_checked(self):
        p = {'x': 1}
        c = {'pair_sha256': m.e.digest(p), 'policies': {
            'nonconstant-minnode': {'complete_switch': True},
            'nonconstant-novelty': {'complete_switch': False}}}
        w = {'pair_candidates': {m.e.f.STRICT_TIER: [p]}, 'policy_comparisons': [c]}
        self.assertIsNone(m.choose_pair(w))
        c['pair_sha256'] = 'wrong'
        with self.assertRaises(ValueError):
            m.choose_pair(w)


if __name__ == '__main__': unittest.main()
