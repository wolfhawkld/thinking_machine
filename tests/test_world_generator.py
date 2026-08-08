import unittest

from src.dsl import (
    DOMAIN,
    behavior_vector,
    depth,
    evaluate,
    hidden_law_constraints,
    node_count,
    variables_used,
)
from src.world_generator import (
    DEFAULT_PROBE_SIZE,
    DEFAULT_TEST_SIZE,
    DEFAULT_TRAIN_SIZE,
    SyntheticWorld,
    WorldGenerator,
    generate_world,
)


class WorldGeneratorTests(unittest.TestCase):
    def test_default_split_is_disjoint_and_labeled_by_the_law(self):
        world = generate_world(17)
        self.assertIsInstance(world, SyntheticWorld)
        self.assertEqual(len(world.train), DEFAULT_TRAIN_SIZE)
        self.assertEqual(len(world.probe), DEFAULT_PROBE_SIZE)
        self.assertEqual(len(world.test), DEFAULT_TEST_SIZE)

        train_points = {example.point for example in world.train}
        probe_points = {example.point for example in world.probe}
        test_points = {example.point for example in world.test}
        self.assertTrue(train_points.isdisjoint(probe_points))
        self.assertTrue(train_points.isdisjoint(test_points))
        self.assertTrue(probe_points.isdisjoint(test_points))
        self.assertEqual(len(train_points | probe_points | test_points), 88)
        self.assertTrue((train_points | probe_points | test_points).issubset(set(DOMAIN)))

        for example in world.train + world.probe + world.test:
            self.assertEqual(evaluate(world.law, example.point), example.label)
            self.assertLessEqual(abs(example.label), 100)

        self.assertEqual(world.x_train, tuple(example.point for example in world.train))
        self.assertEqual(world.y_test, tuple(example.label for example in world.test))
        self.assertEqual(world.X_train, world.x_train)
        self.assertEqual(world.Y_probe, world.y_probe)
        self.assertEqual(world.X_test, world.x_test)
        self.assertEqual(world.train_points, world.train)
        self.assertEqual(world.probe_points, world.probe)
        self.assertEqual(world.test_points, world.test)
        self.assertEqual(world.domain, DOMAIN)

    def test_generation_is_deterministic(self):
        first = generate_world(123)
        second = generate_world(123)
        self.assertEqual(first, second)
        self.assertEqual(first.world_hash, second.world_hash)
        self.assertNotEqual(first.world_hash, generate_world(124).world_hash)

    def test_explicit_depth_tiers_and_hidden_law_constraints(self):
        for tier in (3, 4, 5):
            with self.subTest(tier=tier):
                world = generate_world(100 + tier, depth=tier)
                self.assertEqual(world.depth_tier, tier)
                self.assertEqual(depth(world.law), tier)
                self.assertLessEqual(node_count(world.law), 31)
                self.assertGreaterEqual(len(variables_used(world.law)), 2)
                self.assertIsNone(hidden_law_constraints(world.law))
                self.assertEqual(len(behavior_vector(world.law)), len(DOMAIN))
                self.assertLessEqual(max(abs(value) for value in behavior_vector(world.law)), 100)

    def test_seed_assignment_covers_depth_tiers(self):
        tiers = [generate_world(seed).depth_tier for seed in range(9)]
        self.assertEqual(tiers, [3, 4, 5, 3, 4, 5, 3, 4, 5])

    def test_custom_split_and_invalid_split_are_checked(self):
        world = generate_world(5, train_size=1, probe_size=2, test_size=3)
        self.assertEqual((len(world.train), len(world.probe), len(world.test)), (1, 2, 3))
        with self.assertRaises(ValueError):
            generate_world(5, train_size=100, probe_size=20, test_size=10)

    def test_generator_object_matches_convenience_function(self):
        generator = WorldGenerator()
        self.assertEqual(generator.generate(9), generate_world(9))


if __name__ == "__main__":
    unittest.main()
