import math
import unittest

from src.dsl import DOMAIN, behavior_vector, depth, evaluate, node_count, validate_expr
from src.spark_world import (
    SPARK_BANK_SIZE,
    SPARK_EVIDENCE_SIZE,
    SPARK_INITIAL_HARTLEY_BITS,
    SPARK_TEST_SIZE,
    SPARK_TRAIN_SIZE,
    build_classifier_reservoir,
    build_hypothesis_bank,
    build_spark_world,
    generate_spark_world,
)


class SparkWorldTests(unittest.TestCase):
    def test_global_reservoir_is_large_binary_and_semantically_unique(self):
        reservoir = build_classifier_reservoir()
        self.assertGreater(len(reservoir), SPARK_BANK_SIZE)
        behaviors = {behavior_vector(item) for item in reservoir}
        self.assertEqual(len(behaviors), len(reservoir))
        for hypothesis in reservoir:
            values = behavior_vector(hypothesis)
            self.assertEqual(set(values), {0, 1})
            self.assertIsNone(validate_expr(hypothesis))
            self.assertLessEqual(depth(hypothesis), 4)
            self.assertLessEqual(node_count(hypothesis), 10)

    def test_bank_has_256_unique_bounded_classifier_behaviors(self):
        bank = build_hypothesis_bank(1000)
        self.assertEqual(len(bank), SPARK_BANK_SIZE)
        self.assertEqual(math.log2(len(bank)), SPARK_INITIAL_HARTLEY_BITS)
        self.assertEqual(len({behavior_vector(item) for item in bank}), len(bank))

    def test_splits_are_strict_and_cover_complete_domain(self):
        world = generate_spark_world(1000, 202)
        self.assertEqual(len(world.train), SPARK_TRAIN_SIZE)
        self.assertEqual(len(world.evidence), SPARK_EVIDENCE_SIZE)
        self.assertEqual(len(world.test), SPARK_TEST_SIZE)
        train, evidence, test = map(
            set, (world.x_train, world.x_evidence, world.x_test)
        )
        self.assertTrue(train.isdisjoint(evidence))
        self.assertTrue(train.isdisjoint(test))
        self.assertTrue(evidence.isdisjoint(test))
        self.assertEqual(train | evidence | test, set(DOMAIN))
        for example in world.train + world.evidence + world.test:
            self.assertEqual(evaluate(world.target, example.point), example.label)

    def test_train_conditioning_and_both_private_projections(self):
        for seed in range(1000, 1009):
            with self.subTest(world_seed=seed):
                world = generate_spark_world(seed, 5000 + seed)
                projections = lambda points: {
                    tuple(evaluate(hypothesis, point) for point in points)
                    for hypothesis in world.hypotheses
                }
                self.assertEqual(len(projections(world.x_train)), 1)
                self.assertEqual(len(projections(world.x_evidence)), SPARK_BANK_SIZE)
                self.assertEqual(len(projections(world.x_test)), SPARK_BANK_SIZE)
                self.assertGreaterEqual(world.conditioning_group_size, SPARK_BANK_SIZE)
                self.assertGreater(world.reservoir_size, SPARK_BANK_SIZE)

    def test_world_seed_changes_bank_without_target_dependence(self):
        first = generate_spark_world(1000, 0)
        other_world = generate_spark_world(1001, 0)
        other_target = generate_spark_world(1000, 1)
        self.assertNotEqual(first.hypotheses, other_world.hypotheses)
        self.assertEqual(first.hypotheses, other_target.hypotheses)
        self.assertEqual(first.x_train, other_target.x_train)
        self.assertEqual(first.x_evidence, other_target.x_evidence)
        self.assertEqual(first.x_test, other_target.x_test)
        self.assertEqual(first.target_index, 197)
        self.assertEqual(other_target.target_index, 68)

    def test_generation_and_alias_are_deterministic(self):
        first = generate_spark_world(1000, 88)
        self.assertEqual(first, generate_spark_world(1000, 88))
        self.assertEqual(first, build_spark_world(1000, 88))
        self.assertEqual(first.hypotheses, build_hypothesis_bank(1000))

    def test_input_types_are_explicit(self):
        with self.assertRaises(TypeError):
            generate_spark_world(True, 1)
        with self.assertRaises(TypeError):
            generate_spark_world(1, "2")


if __name__ == "__main__":
    unittest.main()
