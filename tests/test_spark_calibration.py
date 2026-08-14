import math
import unittest

from src.spark_calibration import (
    CALIBRATION_K_VALUES,
    RESPONSE_ENTROPY,
    RETIRED_SPARK_WORLD_SEEDS,
    SHORTEST_PARENT,
    build_calibration_context,
    partition_entropy_bits,
    replay_transcript,
    response_partition,
    response_partition_entropy_bits,
    run_calibration_trajectory,
    run_offline_calibration,
    select_response_entropy_query,
    select_shortest_parent,
)
from src.spark_compressor import SparkCompressor
from src.spark_world import SPARK_BANK_SIZE, generate_spark_world


class SparkCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = build_calibration_context(RETIRED_SPARK_WORLD_SEEDS[0])

    def test_retired_seed_and_k_scopes_are_explicit(self):
        self.assertEqual(RETIRED_SPARK_WORLD_SEEDS, tuple(range(1000, 1009)))
        self.assertEqual(CALIBRATION_K_VALUES, tuple(range(5)))
        self.assertEqual(self.context.target_count, SPARK_BANK_SIZE)
        self.assertEqual(
            sorted(self.context.raw_to_semantic), list(range(SPARK_BANK_SIZE))
        )

    def test_response_matrix_exactly_matches_spark_compressor(self):
        world = generate_spark_world(RETIRED_SPARK_WORLD_SEEDS[0], target_seed=0)
        compressor = SparkCompressor(world)
        for target_index, query_index in ((0, 0), (0, 17), (23, 41), (63, 7)):
            expected = compressor.oracle_response(
                self.context.hypotheses[target_index],
                self.context.hypotheses[query_index],
            )
            self.assertEqual(
                self.context.response_matrix[target_index][query_index], expected
            )

    def test_partition_entropy_and_target_independent_selector(self):
        self.assertEqual(partition_entropy_bits((4,)), 0.0)
        self.assertEqual(partition_entropy_bits((2, 2)), 1.0)
        self.assertEqual(partition_entropy_bits((1, 1, 1, 1)), 2.0)
        version = self.context.initial_version
        chosen = select_response_entropy_query(self.context, version)
        chosen_entropy = response_partition_entropy_bits(
            self.context, version, chosen
        )
        all_entropies = {
            index: response_partition_entropy_bits(self.context, version, index)
            for index in version
        }
        self.assertAlmostEqual(chosen_entropy, max(all_entropies.values()))
        tied = [
            index
            for index, entropy in all_entropies.items()
            if math.isclose(entropy, chosen_entropy, abs_tol=1e-12)
        ]
        self.assertEqual(
            chosen,
            min(tied, key=lambda index: self.context.hypotheses[index].rank_key),
        )
        # Neither selector accepts a target argument; identical V implies an
        # identical query for every possible realized target.
        self.assertEqual(
            {select_shortest_parent(self.context, version) for _ in range(256)},
            {select_shortest_parent(self.context, version)},
        )
        self.assertEqual(
            {select_response_entropy_query(self.context, version) for _ in range(256)},
            {chosen},
        )

    def test_response_partition_is_complete_and_disjoint(self):
        version = self.context.initial_version
        query = select_response_entropy_query(self.context, version)
        partition = response_partition(self.context, version, query)
        flattened = [index for group in partition.values() for index in group]
        self.assertEqual(sorted(flattened), list(version))
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertAlmostEqual(
            response_partition_entropy_bits(self.context, version, query),
            partition_entropy_bits(len(group) for group in partition.values()),
        )

    def test_hard_invariants_telescoping_and_replay_for_all_targets(self):
        selection_cache = {}
        for selector in (SHORTEST_PARENT, RESPONSE_ENTROPY):
            for target_index in range(SPARK_BANK_SIZE):
                trajectory = run_calibration_trajectory(
                    self.context,
                    target_index,
                    selector,
                    max_queries=4,
                    selection_cache=selection_cache,
                )
                with self.subTest(selector=selector, target_index=target_index):
                    self.assertIn(
                        trajectory.target_semantic_index, trajectory.final_version
                    )
                    for step in trajectory.steps:
                        self.assertIn(
                            trajectory.target_semantic_index,
                            step.version_indices_after,
                        )
                        self.assertLess(step.N_after, step.N_before)
                    telescoped = sum(
                        step.contraction_bits for step in trajectory.steps
                    )
                    self.assertAlmostEqual(telescoped, trajectory.contraction_bits)
                    self.assertAlmostEqual(
                        telescoped,
                        math.log2(
                            len(trajectory.initial_version)
                            / trajectory.final_version_size
                        ),
                    )
                    self.assertEqual(
                        replay_transcript(self.context, trajectory),
                        trajectory.final_version,
                    )

    def test_one_world_report_scans_k_zero_through_four(self):
        report = run_offline_calibration(
            world_seeds=(RETIRED_SPARK_WORLD_SEEDS[0],),
            k_values=CALIBRATION_K_VALUES,
        )
        self.assertEqual(report["protocol"]["model_calls"], 0)
        self.assertFalse(report["protocol"]["query_selector_receives_realized_target"])
        self.assertFalse(report["protocol"]["query_selector_uses_private_test"])
        for selector in (SHORTEST_PARENT, RESPONSE_ENTROPY):
            rows = report["selectors"][selector]["aggregate_by_K"]
            self.assertEqual([row["K"] for row in rows], list(range(5)))
            self.assertTrue(
                all(row["trajectory_count"] == SPARK_BANK_SIZE for row in rows)
            )
            self.assertEqual(rows[0]["singleton_rate"], 0.0)
            self.assertEqual(rows[0]["mean_terminal_N"], 256.0)
            self.assertEqual(rows[0]["mean_contraction_bits"], 0.0)
            self.assertEqual(rows[0]["mean_certified_fact_count"], 0.0)
            self.assertEqual(rows[0]["direct_hit_rate"], 0.0)

        baseline = report["selectors"][SHORTEST_PARENT]["aggregate_by_K"]
        benchmark = report["selectors"][RESPONSE_ENTROPY]["aggregate_by_K"]
        headroom = report["selectors"][RESPONSE_ENTROPY][
            "headroom_vs_shortest_parent_by_K"
        ]
        for base, compared, delta in zip(baseline, benchmark, headroom, strict=True):
            self.assertAlmostEqual(
                delta["singleton_rate_gain"],
                compared["singleton_rate"] - base["singleton_rate"],
            )
            self.assertAlmostEqual(
                delta["mean_terminal_N_reduction"],
                base["mean_terminal_N"] - compared["mean_terminal_N"],
            )


if __name__ == "__main__":
    unittest.main()
