import math
import unittest

from src import dsl
from src.spark_calibration import (
    SHORTEST_PARENT,
    build_calibration_context,
    run_calibration_trajectory,
    select_shortest_parent,
)
from src.spark_lineage import MOTIF_STRATA, enumerate_reachable_children
from src.spark_reachability import (
    MIN_CHILDREN_PER_STRATUM,
    MIN_ELIGIBLE_CHILDREN,
    MIN_INDUCED_CELL_CHANGE_RATE,
    MIN_OPERATIONAL_PARTITIONS,
    build_query_profile,
    run_reachable_calibration,
)
from src.spark_world import SPARK_BANK_SIZE, generate_spark_world


class SparkReachabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_reachable_calibration(world_seeds=(1000,))
        cls.world_result = cls.report["per_world"][0]
        cls.context = build_calibration_context(1000)
        cls.world = generate_spark_world(1000, 0)

    def test_parent_profile_exactly_matches_bank_response_column(self):
        parent_index = select_shortest_parent(
            self.context, self.context.initial_version
        )
        profile = build_query_profile(
            self.context, self.context.hypotheses[parent_index].ast
        )
        self.assertEqual(
            profile.responses,
            tuple(
                self.context.response_matrix[target][parent_index]
                for target in self.context.initial_version
            ),
        )
        flattened = [index for cell in profile.partition_cells for index in cell]
        self.assertEqual(sorted(flattened), list(range(SPARK_BANK_SIZE)))
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(profile.raw_response_change_rate, 0.0)
        self.assertEqual(profile.induced_cell_change_rate, 0.0)

    def test_parent_outcome_matches_frozen_shortest_baseline(self):
        trajectories = [
            run_calibration_trajectory(
                self.context, target, SHORTEST_PARENT, max_queries=4
            )
            for target in range(SPARK_BANK_SIZE)
        ]
        expected_singletons = sum(row.singleton for row in trajectories)
        expected_terminal_n = sum(row.final_version_size for row in trajectories)
        observed = self.world_result["parent_outcome"]
        self.assertEqual(observed["singleton_count"], expected_singletons)
        self.assertEqual(observed["terminal_N_sum"], expected_terminal_n)
        self.assertEqual(observed["direct_hit_count"], 1)

    def test_seed_1000_passes_every_structural_gate(self):
        result = self.world_result
        self.assertTrue(result["structure_passed"])
        self.assertGreaterEqual(
            result["eligible_unique_behavior_count"], MIN_ELIGIBLE_CHILDREN
        )
        self.assertGreaterEqual(
            result["eligible_unique_partition_count"], MIN_OPERATIONAL_PARTITIONS
        )
        self.assertEqual(
            set(result["eligible_unique_behavior_count_by_stratum"]),
            set(MOTIF_STRATA),
        )
        for count in result["eligible_unique_behavior_count_by_stratum"].values():
            self.assertGreaterEqual(count, MIN_CHILDREN_PER_STRATUM)
        for child in result["eligible_children"]:
            self.assertGreaterEqual(
                child["induced_cell_change_rate"],
                MIN_INDUCED_CELL_CHANGE_RATE,
            )
            self.assertEqual(len(child["matched_replacements"]), 2)
            self.assertEqual(
                len(
                    {
                        replacement["partition_sha256"]
                        for replacement in child["matched_replacements"]
                    }
                ),
                2,
            )

    def test_benchmark_is_selected_only_by_first_partition_entropy(self):
        benchmark = self.world_result["benchmark"]
        children = self.world_result["eligible_children"]
        self.assertIsNotNone(benchmark)
        self.assertAlmostEqual(
            benchmark["partition_entropy_bits"],
            max(child["partition_entropy_bits"] for child in children),
        )
        self.assertEqual(
            benchmark["selection_rule"],
            "maximum_first_query_partition_entropy_then_frozen_structural_key",
        )

    def test_off_bank_direct_hit_uses_complete_domain_behavior(self):
        benchmark = self.world_result["benchmark"]
        selected = next(
            record
            for record in enumerate_reachable_children(self.world)
            if record.lineage_hash == benchmark["lineage_hash"]
        )
        child_behavior = dsl.behavior_vector(selected.child_ast, self.world.domain)
        expected = sum(
            child_behavior == hypothesis.behavior
            for hypothesis in self.context.hypotheses
        )
        self.assertEqual(benchmark["outcome"]["direct_hit_count"], expected)
        self.assertTrue(math.isfinite(benchmark["partition_entropy_bits"]))

    def test_one_world_report_is_diagnostic_even_when_thresholds_pass(self):
        self.assertTrue(self.report["structure_passed_all_worlds"])
        self.assertTrue(all(self.report["performance_checks"].values()))
        self.assertTrue(self.report["thresholds_satisfied"])
        self.assertFalse(self.report["decisive_scope"])
        self.assertIsNone(self.report["calibration_passed"])
        self.assertEqual(
            self.report["decision"], "diagnostic_only_nondecisive_world_scope"
        )
        self.assertEqual(self.report["protocol"]["model_calls"], 0)
        self.assertFalse(
            self.report["protocol"][
                "private_target_labels_or_outcomes_used_for_benchmark_selection"
            ]
        )


if __name__ == "__main__":
    unittest.main()
