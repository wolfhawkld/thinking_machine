import unittest

from src.dsl import (
    DSLValidationError,
    behavior_hash,
    behavior_vector,
    canonical_hash,
    depth,
    evaluate,
    hidden_law_constraints,
    is_behaviorally_equivalent,
    node_count,
    operator_types,
    parse_sexpr,
    to_sexpr,
    validate_expr,
    variables_used,
)


class DSLTests(unittest.TestCase):
    def test_round_trip_and_canonical_order(self):
        text = "(add (var x2) (mul (var x1) (const 2)))"
        expression = parse_sexpr(text)
        canonical_text = "(add (mul (const 2) (var x1)) (var x2))"
        self.assertEqual(to_sexpr(expression), canonical_text)
        self.assertEqual(parse_sexpr(to_sexpr(expression)), expression)

        reversed_expression = parse_sexpr(
            "(add (mul (const 2) (var x1)) (var x2))"
        )
        self.assertEqual(expression, reversed_expression)
        self.assertEqual(canonical_hash(expression), canonical_hash(reversed_expression))

    def test_evaluation_supports_mapping_and_sequence_environments(self):
        expression = parse_sexpr("(sub (mul (var x1) (var x2)) (neg (var x3)))")
        self.assertEqual(evaluate(expression, {"x1": 2, "x2": 3, "x3": -1}), 5)
        self.assertEqual(evaluate(expression, (2, 3, -1)), 5)

        conditional = parse_sexpr(
            "(ite (gt (var x1) (const 0)) (var x1) (neg (var x1)))"
        )
        self.assertEqual(evaluate(conditional, (2, 0, 0)), 2)
        self.assertEqual(evaluate(conditional, (-2, 0, 0)), 2)

    def test_shape_statistics(self):
        expression = parse_sexpr(
            "(add (mul (var x1) (var x2)) (neg (var x3)))"
        )
        self.assertEqual(depth(expression), 3)
        self.assertEqual(node_count(expression), 6)
        self.assertEqual(variables_used(expression), frozenset({"x1", "x2", "x3"}))
        self.assertEqual(operator_types(expression), frozenset({"add", "mul", "neg"}))

    def test_behavior_vector_and_hashes(self):
        expression = parse_sexpr("(add (var x1) (var x2))")
        reversed_expression = parse_sexpr("(add (var x2) (var x1))")
        vector = behavior_vector(expression)
        self.assertEqual(len(vector), 125)
        self.assertEqual(vector[0], -4)  # (-2, -2, -2) in DOMAIN order
        self.assertEqual(vector, behavior_vector(reversed_expression))
        self.assertTrue(is_behaviorally_equivalent(expression, reversed_expression))
        self.assertEqual(behavior_hash(expression), behavior_hash(reversed_expression))

    def test_validation_bounds_and_size(self):
        valid = parse_sexpr("(add (var x1) (var x2))")
        self.assertIsNone(validate_expr(valid))

        too_large = (
            "mul",
            ("mul", ("mul", ("mul", ("const", 3), ("const", 3)), ("const", 3)), ("const", 3)),
            ("const", 3),
        )
        with self.assertRaises(DSLValidationError):
            validate_expr(too_large)

        too_deep = ("neg", ("neg", ("neg", ("neg", ("neg", ("var", "x1"))))))
        with self.assertRaises(DSLValidationError):
            validate_expr(too_deep)

    def test_parser_rejects_malformed_or_out_of_grammar_nodes(self):
        malformed = (
            "",
            "(var x4)",
            "(const 8)",
            "(add (var x1))",
            "(unknown (var x1) (var x2))",
            "(ite (add (var x1) (var x2)) (var x1) (var x2))",
            "(var x1) trailing",
        )
        for text in malformed:
            with self.subTest(text=text):
                with self.assertRaises(DSLValidationError):
                    parse_sexpr(text)

    def test_hidden_law_constraints(self):
        valid = parse_sexpr("(add (mul (var x1) (var x2)) (sub (var x3) (const 1)))")
        self.assertIsNone(hidden_law_constraints(valid))

        with self.assertRaises(DSLValidationError):
            hidden_law_constraints(parse_sexpr("(add (var x1) (const 1))"))
        with self.assertRaises(DSLValidationError):
            hidden_law_constraints(parse_sexpr("(add (var x1) (var x1))"))


if __name__ == "__main__":
    unittest.main()
