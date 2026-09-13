import unittest
import new_observation_feasibility_20260910 as m


class PartitionTests(unittest.TestCase):
    def test_informative_redundant_and_singleton(self):
        vectors = [(0, 0, 0), (0, 0, 0), (0, 1, 0), (0, 1, 1)]
        points = ((0, 0, 0), (0, 0, 1), (0, 0, 2))
        rows = m.partition_rows(vectors, points, range(3))
        self.assertTrue(rows[0]['redundant'])
        self.assertEqual(rows[1]['branch_counts'], [2, 2])
        self.assertEqual(rows[1]['uniform_bank_expected_information_bits'], 1)
        self.assertFalse(rows[2]['both_branches_nontrivial'])
        self.assertEqual(m.choose_query(rows, 'fixed', True), rows[1])
        self.assertEqual(m.choose_query(rows, 'fixed', False), rows[0])

    def test_no_qualifying_query_does_not_relax(self):
        rows = m.partition_rows([(0,), (1,)], ((0, 0, 0),), [0])
        self.assertIsNone(m.choose_query(rows, 'fixed', True))
        self.assertIsNone(m.choose_query(rows, 'fixed', False))

    def test_selection_order_independence_and_invalid_bank(self):
        rows = m.partition_rows([(0, 1), (0, 1), (1, 0), (1, 0)], ((0, 0, 0), (1, 0, 0)), [0, 1])
        self.assertEqual(m.choose_query(rows, 'fixed', True), m.choose_query(list(reversed(rows)), 'fixed', True))
        with self.assertRaises(ValueError): m.partition_rows([], (), [])
        with self.assertRaises(ValueError): m.partition_rows([(2,)], ((0, 0, 0),), [0])


if __name__ == '__main__': unittest.main()
