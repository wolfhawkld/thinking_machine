import unittest

from src.dsl import parse_sexpr
from src.verifier import (
    CandidateResult,
    Counterexample,
    FAILURE_DEPTH,
    FAILURE_NODE_COUNT,
    FAILURE_OUTPUT_BOUND,
    FAILURE_PARSE_OR_GRAMMAR,
    FAILURE_RUNTIME,
    Verifier,
    canonical_hash,
    parse_candidate,
    select_rule,
)
from src.world_generator import Example


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.expression = parse_sexpr("(add (mul (var x1) (var x2)) (var x3))")
        self.probe = tuple(
            Example(point, point[0] * point[1] + point[2])
            for point in ((-2, 1, 0), (1, 2, -1), (2, -1, 2), (0, 2, -2))
        )
        self.test = tuple(
            Example(point, point[0] * point[1] + point[2])
            for point in ((-2, -2, 1), (1, -1, -2), (2, 2, 0))
        )

    def test_probe_verification_and_test_accuracy(self):
        verifier = Verifier()
        result = verifier.verify_probe(self.expression, self.probe)
        self.assertIsInstance(result, CandidateResult)
        self.assertTrue(result.valid)
        self.assertEqual(result.probe_accuracy, 1.0)
        self.assertEqual(verifier.test_accuracy(self.expression, self.test), 1.0)
        self.assertEqual(result.predictions, tuple(item.label for item in self.probe))
        self.assertEqual(result.failure_types, ())
        self.assertEqual(result.failure_codes, ())

    def test_failure_code_parse_or_grammar_for_malformed_and_bad_ast(self):
        verifier = Verifier()
        malformed = verifier.verify_probe("(wat (var x1))", self.probe)
        bad_ast = verifier.verify_probe(("wat", ("var", "x1")), self.probe)
        for result in (malformed, bad_ast):
            self.assertEqual(result.probe_accuracy, 0.0)
            self.assertIn(FAILURE_PARSE_OR_GRAMMAR, result.failure_types)
            self.assertEqual(result.failure_types, result.failure_codes)
            self.assertFalse(result.valid)

    def test_failure_code_depth(self):
        deep = ("neg", ("neg", ("neg", ("neg", ("neg", ("neg", ("var", "x1")))))))
        result = Verifier().verify_probe(deep, self.probe)
        self.assertIn(FAILURE_DEPTH, result.failure_codes)
        self.assertNotIn(FAILURE_RUNTIME, result.failure_codes)
        self.assertEqual(result.probe_accuracy, 0.0)

    def test_failure_code_node_count(self):
        def zero_tree(depth):
            if depth <= 1:
                return ("const", 0)
            return ("sub", zero_tree(depth - 1), zero_tree(depth - 1))

        # Depth is exactly five, while the conditional predicate and both
        # branches together exceed the 31-node budget.
        oversized = ("ite", ("gt", zero_tree(3), zero_tree(3)), zero_tree(4), zero_tree(4))
        result = Verifier().verify_probe(oversized, self.probe)
        self.assertIn(FAILURE_NODE_COUNT, result.failure_codes)
        self.assertNotIn(FAILURE_DEPTH, result.failure_codes)

    def test_failure_code_output_bound(self):
        too_large = ("mul", ("mul", ("mul", ("mul", ("var", "x1"), ("const", 3)), ("const", 3)), ("const", 3)), ("const", 3))
        result = Verifier().verify_probe(too_large, self.probe)
        self.assertIn(FAILURE_OUTPUT_BOUND, result.failure_codes)
        self.assertNotIn(FAILURE_RUNTIME, result.failure_codes)
        self.assertFalse(result.valid)

    def test_failure_code_runtime_is_distinct_from_validation(self):
        # The AST is valid, but this malformed point cannot be evaluated by the
        # three-variable DSL environment.
        result = Verifier().verify_probe(("var", "x1"), [((1, 2), 1)])
        self.assertTrue(result.syntax_valid)
        self.assertFalse(result.runtime_valid)
        self.assertIn(FAILURE_RUNTIME, result.failure_codes)
        self.assertNotIn(FAILURE_PARSE_OR_GRAMMAR, result.failure_codes)
        self.assertEqual(result.probe_accuracy, 0.0)

    def test_counterexamples_are_bounded_and_releasable(self):
        wrong = parse_sexpr("(var x1)")
        verifier = Verifier(counterexample_limit=2)
        result = verifier.verify_probe(wrong, self.probe)
        self.assertEqual(len(result.counterexamples), 2)
        self.assertTrue(all(isinstance(item, Counterexample) for item in result.counterexamples))
        released = verifier.counterexamples(wrong, self.probe, released=result.counterexamples[:1])
        self.assertEqual(len(released), 1)
        self.assertNotEqual(released[0].inputs, result.counterexamples[0].inputs)

    def test_selection_and_archive_drop_invalid_results(self):
        verifier = Verifier()
        invalid = verifier.verify_probe("(wat (var x1))", self.probe)
        self.assertIsNone(verifier.select_best((invalid,), results=True))
        self.assertEqual(verifier.archive((invalid,), results=True), [])

    def test_archive_deduplicates_full_domain_behavior(self):
        equivalent_large = parse_sexpr("(add (var x1) (const 0))")
        equivalent_small = parse_sexpr("(var x1)")
        archived = Verifier().archive(
            (equivalent_large, equivalent_small),
            self.probe,
            results=True,
        )

        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].candidate, equivalent_small)

    def test_invalid_candidates_are_reported_not_raised(self):
        result = Verifier().verify_probe("(wat (var x1))", self.probe)
        self.assertFalse(result.syntax_valid)
        self.assertFalse(result.valid)
        self.assertEqual(result.probe_accuracy, 0.0)
        self.assertTrue(result.failures)

    def test_json_and_list_candidates_are_accepted(self):
        text = '(add (mul (var x1) (var x2)) (var x3))'
        self.assertEqual(parse_candidate('{"expression": ' + repr(text).replace("'", '"') + '}'), self.expression)
        list_ast = ["add", ["mul", ["var", "x1"], ["var", "x2"]], ["var", "x3"]]
        self.assertEqual(parse_candidate(list_ast), self.expression)

    def test_selection_uses_accuracy_then_size_then_hash(self):
        wrong = parse_sexpr("(var x1)")
        verifier = Verifier()
        self.assertEqual(verifier.select_rule((wrong, self.expression), self.probe), self.expression)
        self.assertEqual(select_rule((wrong, self.expression), self.probe), self.expression)
        self.assertEqual(canonical_hash(self.expression), verifier.verify_probe(self.expression, self.probe).canonical_hash)


if __name__ == "__main__":
    unittest.main()
