import unittest
from dataclasses import dataclass

from src.dsl import canonical_hash, parse_sexpr
from src.spark_compressor import (
    FIRST_MISMATCH,
    CompressionError,
    EmptyVersionSpaceError,
    MAX_CALIBRATION_ROUNDS,
    MAX_COMPRESSION_ROUNDS,
    OracleResponse,
    SparkCompressor,
    run_compression,
)
from src.spark_world import generate_spark_world
from src.world_generator import Example


@dataclass(frozen=True)
class TinySparkWorld:
    hypotheses: tuple
    target_index: int
    train: tuple
    evidence: tuple
    test: tuple
    domain: tuple


def expr(text):
    return parse_sexpr(text)


class SparkCompressorTests(unittest.TestCase):
    def test_full_response_filter_uses_implicit_matching_prefix(self):
        target = expr("(var x1)")
        same_label_at_returned_point_but_wrong_prefix = expr("(const 1)")
        seed = expr("(const 0)")
        domain = ((0, 0, 0), (1, 0, 0))
        world = TinySparkWorld(
            hypotheses=(target, same_label_at_returned_point_but_wrong_prefix),
            target_index=0,
            train=(),
            evidence=(Example(domain[0], 0), Example(domain[1], 1)),
            test=(Example(domain[0], 0), Example(domain[1], 1)),
            domain=domain,
        )
        compressor = SparkCompressor(world)

        observed = compressor.oracle_response(target, seed)
        self.assertEqual(observed.kind, FIRST_MISMATCH)
        self.assertEqual((observed.index, observed.label), (1, 1))

        # Filtering only on the released point's label would retain both laws:
        # both output 1 there.  The alternative is correctly removed because
        # its first mismatch against the same seed occurred at index zero.
        updated = compressor.update_version_space(compressor.hypotheses, seed, observed)
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0].behavior, (0, 1))

        result = compressor.run(seed)
        self.assertEqual(result.steps[0].N_before, 2)
        self.assertEqual(result.steps[0].N_after, 1)
        self.assertEqual(result.steps[0].eliminated_count, 1)
        self.assertEqual(result.steps[0].exact_log_ratio, "log2(2/1)")
        self.assertEqual(result.steps[0].log2_contraction, 1.0)
        self.assertTrue(result.truth_retained)
        self.assertTrue(result.exact_identification)

    def test_empty_version_space_is_an_error_not_infinite_compression(self):
        x1 = expr("(var x1)")
        zero = expr("(const 0)")
        domain = ((0, 0, 0), (1, 0, 0))
        world = TinySparkWorld(
            hypotheses=(x1,),
            target_index=0,
            train=(),
            evidence=(domain[0], domain[1]),
            test=(),
            domain=domain,
        )
        compressor = SparkCompressor(world)
        impossible = OracleResponse.first_mismatch(0, domain[0], 99)
        with self.assertRaises(EmptyVersionSpaceError):
            compressor.update_version_space(compressor.hypotheses, zero, impossible)

    def test_singleton_needs_no_oracle_round_and_is_exactly_identified(self):
        target = expr("(add (var x1) (const 1))")
        domain = ((-1, 0, 0), (0, 0, 0), (1, 0, 0))
        world = TinySparkWorld(
            hypotheses=(target,),
            target_index=0,
            train=(),
            evidence=domain,
            test=(Example(domain[-1], 2),),
            domain=domain,
        )
        result = run_compression(world, expr("(const 0)"))

        self.assertEqual(result.rounds_completed, 0)
        self.assertEqual((result.N_0, result.N_T), (1, 1))
        self.assertEqual(result.cumulative_log_ratio.expression, "log2(1/1)")
        self.assertEqual(result.cumulative_log_ratio.bits, 0.0)
        self.assertEqual(result.final_consensus_count, len(domain))
        self.assertEqual(result.certified_fact_count, 0)
        self.assertTrue(result.full_domain_recovered)
        self.assertEqual(result.test_accuracy, 1.0)
        self.assertTrue(result.exact_identification)
        self.assertEqual(result.termination_reason, "singleton")

    def test_semantic_duplicates_are_collapsed_and_truth_is_retained(self):
        x1_larger = expr("(add (var x1) (const 0))")
        x2 = expr("(var x2)")
        x1_small = expr("(var x1)")
        domain = ((0, 0, 0), (1, 0, 0), (0, 1, 0))
        world = TinySparkWorld(
            hypotheses=(x1_larger, x2, x1_small),
            target_index=0,
            train=(Example(domain[0], 0),),
            evidence=domain,
            test=(Example(domain[1], 1), Example(domain[2], 0)),
            domain=domain,
        )
        compressor = SparkCompressor(world)

        self.assertEqual(len(compressor.hypotheses), 2)
        target_class = next(
            item for item in compressor.hypotheses if item.behavior == compressor.target_behavior
        )
        self.assertEqual(target_class.raw_indices, (0, 2))
        self.assertEqual(target_class.ast, x1_small)
        self.assertTrue(compressor.truth_retained(compressor.hypotheses))

        result = compressor.run(expr("(const 0)"))
        self.assertTrue(all(step.truth_retained for step in result.steps))
        self.assertTrue(result.truth_retained)
        self.assertEqual(result.N_0, 2)
        self.assertEqual(result.N_T, 1)
        self.assertEqual(result.full_domain_correct, len(domain))

    def test_consensus_and_new_certified_facts_are_counted_on_full_domain(self):
        x1 = expr("(var x1)")
        x2 = expr("(var x2)")
        zero = expr("(const 0)")
        domain = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0))
        world = TinySparkWorld(
            hypotheses=(x1, x2),
            target_index=0,
            train=(Example(domain[0], 0),),
            evidence=domain,
            test=(),
            domain=domain,
        )
        result = run_compression(world, zero)

        # x1 and x2 initially agree on the first and last domain points.
        self.assertEqual(result.initial_consensus_count, 2)
        self.assertEqual(result.final_consensus_count, 4)
        self.assertEqual(result.certified_fact_count, 2)
        self.assertEqual(result.steps[-1].newly_certified_fact_count, 2)

    def test_subsequent_query_selection_is_node_count_then_hash(self):
        x1 = expr("(var x1)")
        x2 = expr("(var x2)")
        x3 = expr("(var x3)")
        domain = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
        world = TinySparkWorld(
            hypotheses=(x3, x1, x2),
            target_index=0,
            train=(Example(domain[0], 0),),
            evidence=domain,
            test=(),
            domain=domain,
        )
        compressor = SparkCompressor(world)
        expected = min((x1, x2, x3), key=lambda ast: (1, canonical_hash(ast)))
        selected = compressor.select_candidate(compressor.hypotheses)
        self.assertEqual(selected.ast, expected)

        # A seed matching every hypothesis on only the first evidence point
        # causes no contraction, so round two must use the frozen selector.
        result = compressor.run(expr("(const 1)"), max_rounds=2)
        self.assertEqual(result.steps[0].N_after, 3)
        self.assertEqual(result.steps[1].query_source, "version_space")
        self.assertEqual(result.steps[1].query_ast, expected)

    def test_same_transcript_cell_has_target_independent_next_query(self):
        hypotheses = (expr("(var x1)"), expr("(var x2)"), expr("(var x3)"))
        domain = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
        worlds = tuple(
            TinySparkWorld(hypotheses, target_index, (domain[0],), domain, (), domain)
            for target_index in (0, 1)
        )
        results = tuple(
            SparkCompressor(world).run(expr("(const 1)"), max_rounds=2)
            for world in worlds
        )

        self.assertEqual(results[0].steps[0].response, results[1].steps[0].response)
        self.assertEqual(results[0].steps[0].N_after, 3)
        self.assertEqual(results[0].steps[1].query_ast, results[1].steps[1].query_ast)

    def test_real_world_evidence_injectivity_makes_match_cell_singleton(self):
        world = generate_spark_world(101, 202)
        compressor = SparkCompressor(world)
        observed = compressor.oracle_response(world.target, world.target)
        updated = compressor.update_version_space(
            compressor.hypotheses,
            world.target,
            observed,
        )

        self.assertEqual(observed, OracleResponse.match())
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0].behavior, compressor.target_behavior)

    def test_match_with_multiple_survivors_marks_evidence_equivalence(self):
        x1 = expr("(var x1)")
        x2 = expr("(var x2)")
        domain = ((0, 0, 0), (1, 0, 0), (0, 1, 0))
        world = TinySparkWorld(
            hypotheses=(x1, x2),
            target_index=0,
            train=(Example(domain[0], 0),),
            evidence=(domain[0],),
            test=(Example(domain[1], 1), Example(domain[2], 0)),
            domain=domain,
        )
        result = run_compression(world, expr("(const 0)"))

        self.assertEqual(result.rounds_completed, 1)
        self.assertEqual(result.N_t, (2, 2))
        self.assertEqual(result.steps[0].response, OracleResponse.match())
        self.assertEqual(result.termination_reason, "evidence_equivalence")
        self.assertTrue(result.evidence_equivalent)
        self.assertFalse(result.exact_identification)

    def test_live_default_is_four_but_offline_calibration_allows_six_rounds(self):
        target = expr("(var x1)")
        point = (0, 0, 0)
        world = TinySparkWorld((target,), 0, (point,), (point,), (), (point,))
        compressor = SparkCompressor(world)

        self.assertEqual(MAX_COMPRESSION_ROUNDS, 4)
        self.assertEqual(MAX_CALIBRATION_ROUNDS, 6)
        compressor.run(target, max_rounds=MAX_CALIBRATION_ROUNDS)
        with self.assertRaises(CompressionError):
            compressor.run(("unknown",))
        with self.assertRaises(ValueError):
            compressor.run(target, max_rounds=MAX_CALIBRATION_ROUNDS + 1)

    def test_every_hypothesis_must_be_consistent_with_initial_observations(self):
        x1 = expr("(var x1)")
        x2 = expr("(var x2)")
        point = (1, 0, 0)
        world = TinySparkWorld(
            hypotheses=(x1, x2),
            target_index=0,
            train=(Example(point, 1),),
            evidence=(point,),
            test=(),
            domain=(point,),
        )

        with self.assertRaisesRegex(CompressionError, "V0 must equal"):
            SparkCompressor(world)

    def test_supplied_train_label_must_match_target(self):
        x1 = expr("(var x1)")
        point = (1, 0, 0)
        world = TinySparkWorld(
            hypotheses=(x1,),
            target_index=0,
            train=(Example(point, 99),),
            evidence=(point,),
            test=(),
            domain=(point,),
        )

        with self.assertRaisesRegex(CompressionError, "train label"):
            SparkCompressor(world)


if __name__ == "__main__":
    unittest.main()
