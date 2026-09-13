import unittest
import context_association_audit_20260910 as m


class AssociationTests(unittest.TestCase):
    def test_ten_explicit_slots_and_subtraction_direction(self):
        parent = m.dsl.parse_sexpr('(ite (gt (var x1) (var x2)) (const 1) (const 0))')
        fragment = m.dsl.parse_sexpr('(add (var x3) (const 1))')
        programs = m.children(parent, fragment)
        self.assertEqual(len(programs), 10)
        expected = m.dsl.parse_sexpr('(ite (gt (sub (add (var x3) (const 1)) (var x1)) (var x2)) (const 1) (const 0))')
        self.assertEqual(programs[3], m.dsl.canonicalize(expected))
        self.assertNotEqual(m.behavior(programs[2]), m.behavior(programs[3]))

    def test_semantic_alias_not_only_substring(self):
        a = m.dsl.parse_sexpr('(add (var x1) (const 1))')
        alias = m.dsl.parse_sexpr('(sub (var x1) (const -1))')
        self.assertEqual(m.matched_slots([a], alias), [0])
        self.assertEqual(m.matched_slots([a], m.dsl.parse_sexpr('(add (var x2) (const 1))')), [])

    def test_inspect_preserves_all_slots(self):
        r = m.inspect('(ite (gt (var x1) (var x2)) (const 1) (const 0))',
                      '(add (var x3) (const 1))', '(add (var x3) (const 2))')
        self.assertEqual(r['candidate_count'], 10)
        self.assertTrue(r['reference_behaviors_different'])
        self.assertEqual(r['actual_semantic_subtree_slots'], list(range(10)))


if __name__ == '__main__': unittest.main()
