import unittest
from shortcut_challenge_analysis_20260910 import score_pair


class ScoringTests(unittest.TestCase):
    def test_own_cross_and_missing(self):
        p = {'arms': {'context_a': {'task_id': 'a', 'correct_raw_action': 1},
                      'context_b': {'task_id': 'b', 'correct_raw_action': 2}},
             'option_to_raw_action': {'X': 1, 'Y': 2}}
        records = {'a': {'valid_choice': True, 'selected_option_id': 'X'},
                   'b': {'valid_choice': True, 'selected_option_id': 'Y'}}
        r = score_pair(p, records)
        self.assertEqual((r['own'], r['cross'], r['complete_switch']), (2, 0, True))
        records['a']['selected_option_id'] = 'Y'
        records['b']['selected_option_id'] = 'X'
        r = score_pair(p, records)
        self.assertEqual((r['own'], r['cross']), (0, 2))
        r = score_pair(p, {'a': {'valid_choice': True, 'selected_option_id': 'X'}})
        self.assertEqual((r['own'], r['cross'], r['both_valid']), (1, 0, False))
        records['a']['valid_choice'] = False
        records['b']['valid_choice'] = False
        self.assertEqual(score_pair(p, records)['own'], 0)


if __name__ == '__main__': unittest.main()
