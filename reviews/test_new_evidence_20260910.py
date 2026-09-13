import copy
import json
import unittest

import new_evidence_materials_20260910 as m
import new_evidence_scoring_20260910 as s


class NewEvidenceTests(unittest.TestCase):
    def binding(self):
        behavior = [int(p[0] > 0) for p in m.dsl.DOMAIN]
        d0 = [{'point': list(m.dsl.DOMAIN[0]), 'label': behavior[0]}]
        test = [{'point': list(p), 'label': int(p[0] > 0)} for p in m.dsl.DOMAIN[-64:]]
        return {'D0': d0, 'visible_observations': d0, 'test': test, 'target_behavior': behavior}

    def test_exact_rule_and_invalid_outputs(self):
        b = self.binding()
        content = json.dumps({'expression': '(ite (gt (var x1) (const 0)) (const 1) (const 0))'})
        score = s.score_expression(content, b)
        self.assertTrue(score['full_domain_exact'])
        self.assertEqual(score['test_correct'], 64)
        self.assertTrue(score['visible_consistent'])
        for bad in ('not-json', '{"expression": "Q12345678"}', '{"expression":"(var x1)","extra":1}'):
            self.assertFalse(s.score_expression(bad, b)['valid_program'])
        self.assertEqual(s.score_expression('{"expression":"(var x1)"}', b)['failure'], 'nonbinary')
        self.assertTrue(s.score_expression(None, b)['missing'])

    def test_test_accuracy_separate_from_visible_consistency(self):
        b = self.binding()
        b['visible_observations'] = b['D0'] + [{'point': [2, 2, 2], 'label': 1}]
        # Move this example out of private test for a valid synthetic split.
        b['test'] = [{'point': list(p), 'label': int(p[0] > 0)} for p in m.dsl.DOMAIN[1:65]]
        score = s.score_expression('{"expression":"(const 0)"}', b)
        self.assertTrue(score['valid_program'])
        self.assertFalse(score['visible_consistent'])
        self.assertGreater(score['test_accuracy'], 0)
        self.assertEqual(score['consistent_test_accuracy'], 0)

    def test_test_overlap_and_denominator_fail_loudly(self):
        b = self.binding()
        content = '{"expression":"(const 0)"}'
        b['test'].pop()
        with self.assertRaises(ValueError): s.score_expression(content, b)

    def test_bad_scoring_keys_rejected_even_without_valid_response(self):
        for content in (None, 'not-json', '{"expression":"(var x1)"}'):
            for defect in ('count', 'overlap', 'domain'):
                b = self.binding()
                if defect == 'count': b['test'].pop()
                elif defect == 'overlap': b['visible_observations'].append(b['test'][0])
                else: b['target_behavior'].pop()
                with self.subTest(content=content, defect=defect):
                    with self.assertRaises(ValueError): s.score_expression(content, b)
        b = self.binding()
        b['visible_observations'].append(b['test'][0])
        with self.assertRaises(ValueError): s.score_expression(content, b)

    def test_repeat_selection_and_only_extra_changes(self):
        d0 = [{'point': [0, 0, 0], 'label': 0}, {'point': [1, 0, 0], 'label': 1}]
        chosen = m.repeat_row(d0, 'public')
        self.assertEqual(chosen, m.repeat_row(list(reversed(d0)), 'public'))
        prompts = [m.render(d0, '(const 0)', x) for x in (None, chosen, {'point': [2, 0, 0], 'label': 1})]
        outside = []
        for p in prompts:
            before, rest = p.split(m.EXTRA_START)
            _, after = rest.split(m.EXTRA_END)
            outside.append((before, after))
        self.assertEqual(outside[0], outside[1])
        self.assertEqual(outside[0], outside[2])

    def test_baseline_selection_ignores_hidden_labels_and_repeat(self):
        reservoir = [m.dsl.parse_sexpr('(ite (gt (var x1) (const 0)) (const 1) (const 0))')]
        visible = [{'point': [1, 0, 0], 'label': 1}]
        a = m.public_baselines('(const 1)', visible, reservoir)
        b = m.public_baselines('(const 1)', visible * 2, reservoir)
        self.assertEqual(a, b)
        self.assertEqual(a['public_consistent_minnode'], '(const 1)')
        self.assertNotEqual(a['public_consistent_nonconstant_minnode'], '(const 1)')

    def test_27_task_denominators_and_prompt_hash(self):
        rows = []
        for condition in s.CONDITIONS:
            for ordinal in range(9):
                rows.append({**copy.deepcopy(self.binding()), 'condition': condition, 'ordinal': ordinal,
                             'task_id': f'{condition}-{ordinal}', 'prompt_sha256': 'hash'})
        private = {'bindings': rows}
        result = s.aggregate(private, [])
        self.assertTrue(all(v['worlds'] == 9 and v['missing'] == 9 for v in result['conditions'].values()))
        bad = [{'task_id': rows[0]['task_id'], 'prompt_sha256': 'wrong', 'content': None}]
        with self.assertRaises(ValueError): s.aggregate(private, bad)
        private['bindings'].pop()
        with self.assertRaises(ValueError): s.aggregate(private, [])


if __name__ == '__main__': unittest.main()
