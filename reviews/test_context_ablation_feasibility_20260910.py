import copy
import unittest
import context_ablation_feasibility_20260910 as m


class ContextAblationTests(unittest.TestCase):
    def fixture(self):
        def prompt(fragment):
            return 'Shared parent and D0' + m.START + fragment + m.END + ' OLD is fixed. ' + m.OLD_DEFINITION + '\nOptions X,Y'
        tasks = {'a': {'rendered_prompt': prompt('(add x1 1)')},
                 'b': {'rendered_prompt': prompt('(add x2 1)')}}
        pair = {'arms': {'context_a': {'task_id': 'a', 'correct_option_id': 'X'},
                         'context_b': {'task_id': 'b', 'correct_option_id': 'Y'}}}
        return pair, tasks

    def test_swap_hidden_and_no_mutation(self):
        pair, tasks = self.fixture()
        before = copy.deepcopy((pair, tasks))
        r = m.inspect_pair(pair, tasks)
        self.assertTrue(r['swapped_prompt_is_opposite_task'])
        self.assertTrue(r['hidden_prompts_identical'])
        self.assertTrue(r['visible_prompts_distinct'])
        self.assertEqual((pair, tasks), before)
        self.assertIn(m.DEFINITION, r['drafts']['hidden'][0])
        self.assertNotIn('(add x1 1)', r['drafts']['hidden'][0])

    def test_residual_arm_cue_rejected(self):
        pair, tasks = self.fixture()
        tasks['b']['rendered_prompt'] += ' extra arm cue'
        with self.assertRaises(ValueError): m.inspect_pair(pair, tasks)

    def test_label_equality_rejected(self):
        pair, tasks = self.fixture()
        pair['arms']['context_b']['correct_option_id'] = 'X'
        with self.assertRaises(ValueError): m.inspect_pair(pair, tasks)

    def test_bad_context_boundary_rejected(self):
        for prompt in ('no context', m.START + 'x' + m.START + 'y'):
            with self.assertRaises(ValueError): m.split_context(prompt)


if __name__ == '__main__': unittest.main()
