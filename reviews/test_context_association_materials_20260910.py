import copy
import unittest

import context_association_materials_20260910 as m


class MaterialsTests(unittest.TestCase):
    def fixture(self):
        rows = []
        for condition in m.CONDITIONS:
            for pair in range(9):
                for arm, own, cross in [('context_a', 'X', 'Y'), ('context_b', 'Y', 'X')]:
                    rows.append({'task_id': f'{condition}-{pair}-{arm}', 'condition': condition,
                                 'pair_ordinal': pair, 'arm': arm, 'option_to_raw_action': {'X': 0, 'Y': 1},
                                 'correct_option_id': own, 'cross_option_id': cross, 'prompt_sha256': 'hash'})
        return {'bindings': rows}

    def responses(self, private, field='correct_option_id'):
        return [{'task_id': row['task_id'], 'prompt_sha256': 'hash', 'valid_choice': True,
                 'selected_option_id': row[field]} for row in private['bindings']]

    def test_full_own_and_cross(self):
        private = self.fixture()
        for result in m.score(private, self.responses(private)).values():
            self.assertEqual((result['own'], result['cross'], result['complete_switch']), (18, 0, 9))
        for result in m.score(private, self.responses(private, 'cross_option_id')).values():
            self.assertEqual((result['own'], result['cross'], result['complete_switch']), (0, 18, 0))

    def test_missing_invalid_and_no_mutation(self):
        private = self.fixture()
        before = copy.deepcopy(private)
        for result in m.score(private, []).values():
            self.assertEqual((result['tasks'], result['missing'], result['own']), (18, 18, 0))
        responses = self.responses(private)
        responses[0]['valid_choice'] = False
        responses[1]['selected_option_id'] = 'unknown'
        r = m.score(private, responses)['aligned']
        self.assertEqual((r['own'], r['valid_arms'], r['complete_switch'], r['missing']), (16, 16, 8, 0))
        self.assertEqual(before, private)

    def test_wrong_hash_unknown_duplicate_denominator_rejected(self):
        private = self.fixture()
        responses = self.responses(private)
        bad = copy.deepcopy(responses)
        bad[0]['prompt_sha256'] = 'other'
        with self.assertRaises(ValueError): m.score(private, bad)
        with self.assertRaises(ValueError): m.score(private, responses + responses[:1])
        bad = copy.deepcopy(responses)
        bad[0]['task_id'] = 'unknown'
        with self.assertRaises(ValueError): m.score(private, bad)
        private['bindings'].pop()
        with self.assertRaises(ValueError): m.score(private, [])

    def test_reference_table_marginals_and_boundaries(self):
        a = tuple(range(125))
        b = tuple(reversed(a))
        self.assertEqual(len(m.reference_block(a)), len(m.reference_block(b)))
        with self.assertRaises(ValueError): m.reference_block((1, 2))
        left = 'header' + m.TABLE_START + m.reference_block(a) + m.TABLE_END + 'footer'
        right = 'header' + m.TABLE_START + m.reference_block(b) + m.TABLE_END + 'footer'
        self.assertEqual(m.strip_reference(left), m.strip_reference(right))
        with self.assertRaises(ValueError): m.strip_reference('no table')

    def test_pool_filter_and_deterministic_selection(self):
        library = m.m.lineage.build_motif_library()
        actual = next(item for item in library if item.stratum == 'affine_commutative')
        forbidden = {m.m.behavior(actual.ast)}
        eligible = m.pool.eligible_references(actual, forbidden, library)
        self.assertTrue(eligible)
        self.assertTrue(all(item.stratum == actual.stratum and item.complexity_bucket == actual.complexity_bucket
                            and m.Counter(m.m.behavior(item.ast)) == m.Counter(m.m.behavior(actual.ast))
                            and m.m.behavior(item.ast) not in forbidden for item in eligible))
        self.assertEqual(m.pool.choose(eligible, 'fixed'), m.pool.choose(list(reversed(eligible)), 'fixed'))
        self.assertIsNone(m.pool.choose([], 'fixed'))
        all_forbidden = {m.m.behavior(item.ast) for item in library}
        self.assertEqual(m.pool.eligible_references(actual, all_forbidden, library), [])


if __name__ == '__main__': unittest.main()
